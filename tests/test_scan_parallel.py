from collections import Counter
from threading import Event, Lock
from time import sleep
import json

from PIL import Image
import pytest

from ai_cull_assistant import processing_job as module
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.screening import ScreeningResult
from ai_cull_assistant.scan_resources import ResourceSnapshot


class FixedBudget:
    def snapshot(self):
        return ResourceSnapshot(8, 16*1024**3, 10*1024**3, 200*1024**2)

    def observe(self, *args, **kwargs):
        pass

    def choose_workers(self, **kwargs):
        return 4


def setup_job(tmp_path, monkeypatch, count=9):
    # These tests isolate dispatch/checkpoint mechanics from the measured policy.
    class FixedPolicy:
        def __init__(self, **kwargs): pass
        def choose(self, cap): return cap
        def observe(self, *args): return False
    monkeypatch.setattr(module, 'AdaptiveConcurrencyPolicy', FixedPolicy)
    folder = tmp_path / 'photos'
    folder.mkdir()
    for i in range(count):
        Image.new('RGB', (80, 100), 'gray').save(folder / f'P{i:04}.jpg')
    monkeypatch.setattr(module, 'ResourceBudget', FixedBudget)
    options = dict(technical_screening=True)
    job = module.start_job(folder, tmp_path/'work', options, CropSettings(), mode='scan')
    return folder, job, options


def test_parallel_batch_is_bounded_and_stop_saves_started_photos(tmp_path, monkeypatch):
    folder, job, options = setup_job(tmp_path, monkeypatch, 12)
    active = peak = 0
    calls = []
    lock = Lock()
    parallel_started = Event()
    def screen(assets, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append(assets[0].stem)
            if active == 4:
                parallel_started.set()
        if assets[0].stem >= 'P0002':
            assert parallel_started.wait(3), 'four workers should actually overlap'
        sleep(.01)
        with lock:
            active -= 1
        return {assets[0].stem: ScreeningResult(False, 'no_reliable_face', False)}
    monkeypatch.setattr(module, 'screen_assets', screen)
    stopped = Event()
    def progress(_value):
        if job._data['completed_photos'] >= 3:
            stopped.set()
    # Call local stage directly to simulate no final full-snapshot flush.
    assert not job._run_local_photos(module._normalise_options(options), CropSettings(), stopped, progress, None)
    assert peak == 4
    completed = job._data['completed_photos']
    assert 6 <= completed <= 10
    base = json.loads((job.root/'job.json').read_text('utf-8'))
    assert base['completed_photos'] == 0
    assert len(list((job.root/'journal').glob('*.json'))) == completed
    restored = module.load_job(job.workspace, folder)
    assert restored._data['completed_photos'] == completed
    assert all(restored.assets[i].preview_path.is_file() for i in range(completed))
    # Finished photos retain old settings, only the remaining photos use new ones.
    monkeypatch.setattr(module, 'assign_groups', lambda *args: None)
    logs = []
    result = restored.run(dict(technical_screening=False), CropSettings(), Event(), None, on_log=logs.append)
    assert result is not None
    assert Counter(calls) == Counter(f'P{i:04}' for i in range(completed))
    assert all(restored._data['photo_options'][str(i)]['technical_screening'] == (i < completed) for i in range(12))
    assert any('耗时统计' in line for line in logs)


def test_low_memory_reduces_next_batch_without_extra_dispatch(tmp_path, monkeypatch):
    _folder, job, options = setup_job(tmp_path, monkeypatch, 7)
    choices = iter([4, 1])
    class ShrinkingBudget(FixedBudget):
        def choose_workers(self, **kwargs):
            return next(choices, 1)
    monkeypatch.setattr(module, 'ResourceBudget', ShrinkingBudget)
    monkeypatch.setattr(module, 'screen_assets', lambda assets, **kw: {
        assets[0].stem: ScreeningResult(False, 'no_reliable_face', False)})
    monkeypatch.setattr(module, 'assign_groups', lambda *a: None)
    logs = []
    assert job.run(options, CropSettings(), Event(), None, on_log=logs.append)
    assert any('同时处理 4 张' in line for line in logs)
    assert any('同时处理 1 张（按 CPU' in line for line in logs)


def test_completed_results_survive_worker_exception(tmp_path, monkeypatch):
    folder, job, options = setup_job(tmp_path, monkeypatch, 3)
    def screen(assets, **kw):
        if assets[0].stem == 'P0001':
            raise OSError('decode failed')
        return {assets[0].stem: ScreeningResult(False, 'no_reliable_face', False)}
    monkeypatch.setattr(module, 'screen_assets', screen)
    with pytest.raises(OSError, match='decode failed'):
        job.run(options, CropSettings(), Event(), None)
    restored = module.load_job(job.workspace, folder)
    assert restored._data['completed_photos'] == 1
    assert restored.assets[0].preview_path.is_file()


def test_checkpoint_serializes_one_photo_even_for_large_inventory(tmp_path, monkeypatch):
    from copy import deepcopy
    _folder, job, _options = setup_job(tmp_path, monkeypatch, 1)
    job.assets = [deepcopy(job.assets[0]) for _ in range(1000)]
    job._data['work_indices'] = list(range(1000))
    key = module._asset_key(job.assets[0])
    job._data['screening_results'][key] = dict(rejected=False, reason='no_reliable_face', face_found=False)
    original = module._asset_to_dict
    seen = []
    monkeypatch.setattr(module, '_asset_to_dict', lambda asset: seen.append(asset) or original(asset))
    job._data['completed_photos'] = 1
    job._checkpoint(None, index=0)
    assert len(seen) == 1


def test_old_snapshot_can_resume_with_new_journal(tmp_path, monkeypatch):
    folder, job, options = setup_job(tmp_path, monkeypatch, 1)
    data = json.loads((job.root/'job.json').read_text('utf-8'))
    data['version'] = 1
    (job.root/'job.json').write_text(json.dumps(data), encoding='utf-8')
    restored = module.load_job(job.workspace, folder)
    assert restored._data['version'] == module.JOB_VERSION
    monkeypatch.setattr(module, 'assign_groups', lambda *a: None)
    assert restored.run(dict(technical_screening=False), CropSettings(), Event(), None)


def test_fast_worker_refills_before_slow_photo_finishes(tmp_path, monkeypatch):
    _folder, job, options = setup_job(tmp_path, monkeypatch, 10)
    slow_started, replacement_started = Event(), Event()
    def screen(assets, **kwargs):
        stem = assets[0].stem
        if stem == 'P0002':
            slow_started.set()
            assert replacement_started.wait(3), 'idle slot was not refilled'
        if stem == 'P0006':
            assert slow_started.is_set()
            replacement_started.set()
        return {stem: ScreeningResult(False, 'no_reliable_face', False)}
    monkeypatch.setattr(module, 'screen_assets', screen)
    monkeypatch.setattr(module, 'assign_groups', lambda *args: None)
    assert job.run(options, CropSettings(), Event(), None)
    assert job._data['completed_photos'] == 10
    assert replacement_started.is_set()
    lines = (job.workspace / 'logs' / 'session.log').read_text('utf-8').splitlines()
    records = [json.loads(line.split(' ', 1)[1]) for line in lines if line.startswith('[扫描诊断] ')]
    events = [row for row in records if row.get('type') == 'scheduler']
    assert events and 'available_memory_bytes' in events[-1]


def test_worker_level_change_does_not_mix_old_completions_into_new_window(tmp_path, monkeypatch):
    _folder, job, options = setup_job(tmp_path, monkeypatch, 10)
    release_old_worker = Event()
    old_worker_finished = Event()
    state_lock = Lock()
    new_level_finished = 0

    class SwitchingPolicy:
        instance = None

        def __init__(self, **kwargs):
            self.choose_calls = 0
            self.observations = []
            SwitchingPolicy.instance = self

        @property
        def best_workers(self):
            return 4

        def choose(self, cap):
            self.choose_calls += 1
            if self.choose_calls == 1:
                return 2
            release_old_worker.set()
            return 4

        def observe(self, workers, count, elapsed):
            with state_lock:
                completed_at_new_level = new_level_finished
            self.observations.append((workers, count, completed_at_new_level))
            assert old_worker_finished.is_set()
            assert completed_at_new_level >= 4
            return False

    def screen(assets, **kwargs):
        nonlocal new_level_finished
        stem = assets[0].stem
        if stem == 'P0002':
            assert release_old_worker.wait(3), 'scheduler never requested the new worker level'
            old_worker_finished.set()
        elif stem >= 'P0004':
            with state_lock:
                new_level_finished += 1
        return {stem: ScreeningResult(False, 'no_reliable_face', False)}

    monkeypatch.setattr(module, 'AdaptiveConcurrencyPolicy', SwitchingPolicy)
    monkeypatch.setattr(module, 'screen_assets', screen)
    monkeypatch.setattr(module, 'assign_groups', lambda *args: None)

    assert job.run(options, CropSettings(), Event(), None)
    assert SwitchingPolicy.instance.observations
    assert all(row[:2] == (4, 4) for row in SwitchingPolicy.instance.observations)
