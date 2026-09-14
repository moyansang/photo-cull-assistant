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

    def choose_workers(self):
        return 4


def setup_job(tmp_path, monkeypatch, count=9):
    folder = tmp_path / 'photos'
    folder.mkdir()
    for i in range(count):
        Image.new('RGB', (80, 100), 'gray').save(folder / f'P{i:04}.jpg')
    monkeypatch.setattr(module, 'ResourceBudget', FixedBudget)
    options = dict(technical_screening=True)
    job = module.start_job(folder, tmp_path/'work', options, CropSettings(), mode='scan')
    return folder, job, options


def test_parallel_batch_is_bounded_and_stop_saves_started_photos(tmp_path, monkeypatch):
    folder, job, options = setup_job(tmp_path, monkeypatch)
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
    assert job._data['completed_photos'] == 6
    base = json.loads((job.root/'job.json').read_text('utf-8'))
    assert base['completed_photos'] == 0
    assert len(list((job.root/'journal').glob('*.json'))) == 6
    restored = module.load_job(job.workspace, folder)
    assert restored._data['completed_photos'] == 6
    assert all(restored.assets[i].preview_path.is_file() for i in range(6))
    # Finished photos retain old settings, only the remaining three use new ones.
    monkeypatch.setattr(module, 'assign_groups', lambda *args: None)
    logs = []
    result = restored.run(dict(technical_screening=False), CropSettings(), Event(), None, on_log=logs.append)
    assert result is not None
    assert Counter(calls) == Counter(f'P{i:04}' for i in range(6))
    assert all(restored._data['photo_options'][str(i)]['technical_screening'] == (i < 6) for i in range(9))
    assert any('耗时统计' in line for line in logs)


def test_low_memory_reduces_next_batch_without_extra_dispatch(tmp_path, monkeypatch):
    _folder, job, options = setup_job(tmp_path, monkeypatch, 7)
    choices = iter([4, 1])
    class ShrinkingBudget(FixedBudget):
        def choose_workers(self):
            return next(choices)
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
