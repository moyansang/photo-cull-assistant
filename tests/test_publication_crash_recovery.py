"""Actual child-process exits at publication/rollback boundaries, no real photos."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
from threading import Event

import pytest
from PIL import Image

from ai_cull_assistant import processing_job as pj, publication_transaction as tx
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.session_store import load_session

OPTIONS = {'technical_screening': False, 'analysis_mode': 'landscape', 'photos_per_page': 2, 'columns': 2}
ROOTS = {'contact_sheets', 'previews', 'screening_results.json', 'groups.json',
         'scan-session.json', 'processing-settings.json', '.source-location.json',
         'ai_project.json', 'crop-settings.json', 'export', 'focus-evidence'}
ANCILLARY = ['ai_project.json', 'crop-settings.json', 'export/prior.json', 'focus-evidence/prior.json']


def files(root):
    # Windows byte-range locks also deny reads from another handle. The root
    # lock is synchronization state, not a published artifact or user record;
    # reading it here prevents the crash injection from reaching publication.
    lock_path = root / '.publication-operation.lock'
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and p != lock_path}


def published(root):
    return {k: v for k, v in files(root).items() if k.split('/')[0] in ROOTS}


def save(path, value):
    path.write_text(json.dumps(value, indent=2), 'utf-8')


def child(case, mode, point):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'),
               OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    result = subprocess.run([sys.executable, __file__, str(case), mode, point],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 86, result.stdout + result.stderr


def setup_worker(case, mode, point):
    source = case / 'photos'; work = case / 'workspace'; source.mkdir()
    Image.new('RGB', (96, 64), 'orange').save(source / 'A.png')
    crops = CropSettings()
    first = pj.start_job(source, work, OPTIONS, crops, mode='scan').run(OPTIONS, crops, Event(), None)
    for name in ['contact_sheets/prior.jpg', *ANCILLARY]:
        path = work / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"synthetic": "old artifact"}')
    if mode != 'sheets':
        Image.new('RGB', (96, 64), 'blue').save(source / 'B.png')
    save(case / 'old.json', published(work)); save(case / 'source.json', files(source))
    job = pj.start_job(source, work, OPTIONS, crops, result=None if mode == 'full' else first,
                       mode='sheets' if mode == 'sheets' else 'scan')
    save(case / 'job-root.json', str(job.root))
    real_publish = pj.ProcessingJob._publish; real_replace = Path.replace
    real_atomic = pj.atomic_json; real_tx_atomic = tx.atomic_json
    active = False

    def leave():
        os._exit(86)

    def replace(path, target):
        result = real_replace(path, target)
        if active:
            target = Path(target)
            if point == 'first_backup' and target.parent == job.root / 'backup':
                leave()
            if point == 'staged' and path == job.output / ('contact_sheets' if mode == 'sheets' else 'previews'):
                leave()
        return result

    def atomic(path, value):
        real_atomic(path, value)
        if active and point == 'settings' and Path(path) == work / 'processing-settings.json':
            leave()

    def tx_atomic(path, value):
        real_tx_atomic(path, value)
        if active and point == 'committed' and value.get('phase') == 'committed':
            leave()

    def publish(self, progress):
        nonlocal active
        save(case / 'old.json', published(work))
        active = True
        return real_publish(self, progress)

    Path.replace = replace; pj.atomic_json = atomic; tx.atomic_json = tx_atomic
    pj.ProcessingJob._publish = publish
    job.run(OPTIONS, crops, Event(), None)
    raise AssertionError('Exit injection did not run')


def recovery_worker(case, point):
    root = Path(json.loads((case / 'job-root.json').read_text()))
    real_replace = Path.replace; real_remove = tx._remove; real_rmtree = tx.shutil.rmtree

    def replace(path, target):
        result = real_replace(path, target)
        if point == 'return_new' and Path(target).is_relative_to(root / 'output'):
            os._exit(86)
        if point == 'remove_old' and Path(target).name.startswith('recovery-discard-'):
            os._exit(86)
        if point == 'restore_old' and path.name.startswith('recovery-copy-'):
            os._exit(86)
        return result

    def remove(path):
        real_remove(path)
        if point == 'remove_old' and path.name.startswith('recovery-discard-'):
            os._exit(86)
        if point == 'cleanup_backup' and path == root / 'backup':
            os._exit(86)

    def rmtree(path, *args, **kwargs):
        if point == 'partial_backup_cleanup' and Path(path) == root / 'backup':
            next(p for p in Path(path).rglob('*') if p.is_file()).unlink()
            os._exit(86)
        return real_rmtree(path, *args, **kwargs)

    Path.replace = replace; tx._remove = remove; tx.shutil.rmtree = rmtree
    pj.load_job(case / 'workspace', case / 'photos')
    raise AssertionError('Recovery exit injection did not run')


def assert_current(case, expected, sheets):
    work = case / 'workspace'; source = case / 'photos'
    result = load_session(work, source)
    assert result and len(result.assets) == expected
    assert all(a.preview_path and a.preview_path.is_file() for a in result.assets)
    for asset in result.assets:
        with Image.open(asset.preview_path) as image:
            image.load()
    if sheets:
        assert result.main_pages and all(p.is_file() for p in result.main_pages)
    old = json.loads((case / 'old.json').read_text())
    now = published(work)
    assert all(now[key] == old[key] for key in ANCILLARY)
    assert files(source) == json.loads((case / 'source.json').read_text())


def resume_and_check(case, mode):
    work = case / 'workspace'; source = case / 'photos'
    job = pj.load_job(work, source)
    assert job is not None
    # A pre-commit crash restores *all* prior published bytes before resuming.
    assert published(work) == json.loads((case / 'old.json').read_text())
    assert all(a.preview_path.is_file() for a in job.assets)
    result = job.run(OPTIONS, CropSettings(), Event(), None)
    assert result is not None
    assert pj.load_job(work, source) is None
    assert_current(case, 1 if mode == 'sheets' else 2, mode == 'sheets')


@pytest.mark.parametrize('mode', ['full', 'incremental', 'sheets'])
@pytest.mark.parametrize('point', ['first_backup', 'staged', 'settings'])
def test_process_exit_rolls_back_then_resumes(tmp_path, mode, point):
    child(tmp_path, mode, point)
    resume_and_check(tmp_path, mode)


@pytest.mark.parametrize('mode', ['full', 'incremental', 'sheets'])
def test_committed_process_exit_validates_and_finishes_cleanup(tmp_path, mode):
    child(tmp_path, mode, 'committed')
    before = published(tmp_path / 'workspace')
    assert pj.load_job(tmp_path / 'workspace', tmp_path / 'photos') is None
    assert published(tmp_path / 'workspace') == before
    assert_current(tmp_path, 1 if mode == 'sheets' else 2, mode == 'sheets')


@pytest.mark.parametrize('point', ['return_new', 'remove_old', 'restore_old', 'cleanup_backup', 'partial_backup_cleanup'])
def test_rollback_itself_can_be_interrupted(tmp_path, point):
    child(tmp_path, 'full', 'settings')
    child(tmp_path, 'recover', point)
    resume_and_check(tmp_path, 'full')


@pytest.mark.parametrize('damage', ['committed_file', 'backup_file', 'missing_journal', 'broken_journal'])
def test_unverifiable_transaction_preserves_every_byte(tmp_path, damage):
    child(tmp_path, 'full', 'committed' if damage == 'committed_file' else 'settings')
    root = Path(json.loads((tmp_path / 'job-root.json').read_text()))
    if damage == 'committed_file':
        (tmp_path / 'workspace' / 'groups.json').write_text('unexpected external change')
    elif damage == 'backup_file':
        next(p for p in (root / 'backup').rglob('*') if p.is_file()).write_text('bad backup')
    elif damage == 'missing_journal':
        (root / tx.JOURNAL).unlink()
    else:
        (root / tx.JOURNAL).write_text('{broken')
    before = files(tmp_path / 'workspace')
    with pytest.raises(tx.PublicationRecoveryError):
        pj.load_job(tmp_path / 'workspace', tmp_path / 'photos')
    # Damaged backup can be discovered after safe return of staged files; it
    # must never be consumed/cleared, and no original evidence may disappear.
    after = files(tmp_path / 'workspace')
    assert root.exists() and (root / 'backup').exists()
    if damage != 'backup_file':
        assert after == before
    else:
        backup_rel = (root / 'backup').relative_to(tmp_path / 'workspace').as_posix() + '/'
        assert {k:v for k,v in before.items() if k.startswith(backup_rel)} == {k:v for k,v in after.items() if k.startswith(backup_rel)}
    assert files(tmp_path / 'photos') == json.loads((tmp_path / 'source.json').read_text())


@pytest.mark.parametrize('target', ['groups.json', 'scan-session.json'])
def test_external_modification_is_not_overwritten(tmp_path, target):
    child(tmp_path, 'full', 'settings')
    path = tmp_path / 'workspace' / target
    path.write_bytes(b'external user edit after process exit')
    before = files(tmp_path / 'workspace')
    with pytest.raises(tx.PublicationRecoveryError, match='其他操作修改'):
        pj.load_job(tmp_path / 'workspace', tmp_path / 'photos')
    assert files(tmp_path / 'workspace') == before


@pytest.mark.parametrize('damage', ['overlap', 'schema', 'missing_passive'])
def test_malformed_journal_cannot_mutate_workspace(tmp_path, damage):
    child(tmp_path, 'full', 'settings')
    root = Path(json.loads((tmp_path / 'job-root.json').read_text()))
    path = root / tx.JOURNAL
    state = json.loads(path.read_text())
    if damage == 'overlap':
        entry = dict(next(e for e in state['entries'] if e['destination'] == 'previews'))
        entry.update(index=len(state['entries']), destination='previews/' + state['job_id'])
        state['entries'].append(entry)
    elif damage == 'schema':
        state['entries'][0]['old'] = {'directory': {'../escape': {'file': '0'*64}}}
    else:
        state['entries'] = state['entries'][:-1]
    save(path, state)
    before = files(tmp_path / 'workspace')
    with pytest.raises(tx.PublicationRecoveryError):
        pj.load_job(tmp_path / 'workspace', tmp_path / 'photos')
    assert files(tmp_path / 'workspace') == before


def test_run_failure_during_rollback_keeps_checkpoint_and_retries_safely(tmp_path, monkeypatch):
    source = tmp_path / 'photos'; source.mkdir()
    Image.new('RGB', (96,64), 'orange').save(source / 'A.png')
    work = tmp_path / 'workspace'; crops = CropSettings()
    first = pj.start_job(source, work, OPTIONS, crops, mode='scan').run(OPTIONS, crops, Event(), None)
    Image.new('RGB', (96,64), 'blue').save(source / 'B.png')
    job = pj.start_job(source, work, OPTIONS, crops, mode='scan')
    old = published(work); observed = {}
    real_begin = tx.begin; real_atomic = pj.atomic_json
    def begin(*args):
        observed['checkpoint'] = (job.root / 'job.json').read_bytes()
        return real_begin(*args)
    def fail_settings(path, value):
        real_atomic(path, value)
        if Path(path) == work / 'processing-settings.json':
            raise OSError('injected write failure')
    def fail_copy(*args, **kwargs):
        raise OSError('injected transient recovery failure')
    with monkeypatch.context() as patch:
        patch.setattr(tx, 'begin', begin)
        patch.setattr(pj, 'atomic_json', fail_settings)
        patch.setattr(tx.shutil, 'copytree', fail_copy)
        with pytest.raises(tx.PublicationRecoveryError):
            job.run(OPTIONS, crops, Event(), None)
    assert (job.root / 'job.json').read_bytes() == observed['checkpoint']
    assert all(str(a.preview_path).startswith(str(job.output)) for a in job.assets)
    before_retry = files(work)
    with pytest.raises(tx.PublicationRecoveryError):
        job.run(OPTIONS, crops, Event(), None)
    with pytest.raises(tx.PublicationRecoveryError):
        job._invalidate()
    assert files(work) == before_retry
    resumed = pj.load_job(work, source)
    assert resumed and published(work) == old
    assert resumed.run(OPTIONS, crops, Event(), None)
    session = load_session(work, source)
    assert len(session.assets) == 2 and all(a.preview_path.is_file() for a in session.assets)


def test_workspace_lock_prevents_second_process_recovery(tmp_path):
    child(tmp_path, 'full', 'settings')
    work = tmp_path / 'workspace'
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    with tx.operation(work):
        before = files(work)
        with pytest.raises(tx.PublicationRecoveryError):
            pj.load_job(work, tmp_path / 'photos')
        script = "from ai_cull_assistant.processing_job import load_job; import sys; load_job(sys.argv[1],sys.argv[2])"
        result = subprocess.run([sys.executable, '-c', script, str(work), str(tmp_path/'photos')],
                                capture_output=True, text=True, env=env, timeout=15)
        assert result.returncode != 0 and 'PublicationRecoveryError' in result.stderr
        assert files(work) == before
    resume_and_check(tmp_path, 'full')


def test_completed_stale_job_object_cannot_recreate_processing_state(tmp_path):
    source=tmp_path/'photos'; source.mkdir()
    Image.new('RGB',(96,64),'orange').save(source/'A.png')
    work=tmp_path/'workspace'; job=pj.start_job(source,work,OPTIONS,CropSettings(),mode='scan')
    assert job.run(OPTIONS,CropSettings(),Event(),None)
    before=files(work)
    with pytest.raises(pj.ProcessingJobError, match='已结束'):
        job.run(OPTIONS,CropSettings(),Event(),None)
    assert files(work)==before


@pytest.mark.parametrize('method', ['retry_failed', 'skip_failed'])
def test_focus_decisions_hold_lock_and_reload_checkpoint(tmp_path, method):
    source=tmp_path/'photos'; source.mkdir()
    Image.new('RGB',(96,64),'orange').save(source/'A.png')
    work=tmp_path/'workspace'; job=pj.start_job(source,work,OPTIONS,CropSettings(),mode='scan')
    job._data.update(mode='focus', awaiting_focus_error_decision=True,
                     focus_review_errors=[{'index': 0, 'error': 'synthetic'}])
    job._persist()
    with tx.operation(work):
        before=files(work)
        with pytest.raises(tx.PublicationRecoveryError):
            getattr(job,method)()
        assert files(work)==before
    latest=pj.load_job(work,source)
    latest._data['awaiting_focus_error_decision']=False
    latest._persist()
    before=files(work)
    with pytest.raises(pj.ProcessingJobError):
        getattr(job,method)()
    assert files(work)==before


def test_stale_runner_refreshes_latest_checkpoint_under_lock(tmp_path, monkeypatch):
    source=tmp_path/'photos'; source.mkdir()
    Image.new('RGB',(96,64),'orange').save(source/'A.png')
    work=tmp_path/'workspace'; stale=pj.start_job(source,work,OPTIONS,CropSettings(),mode='scan')
    newer=pj.load_job(work,source)
    newer._data['scan_owner_sentinel']='newer checkpoint'
    newer._persist()
    seen=[]
    def observe(*args, **kwargs):
        seen.append(stale._data.get('scan_owner_sentinel'))
        return None
    monkeypatch.setattr(stale,'_run_guarded',observe)
    stale.run(OPTIONS,CropSettings(),Event(),None)
    assert seen==['newer checkpoint']


if __name__ == '__main__':
    case, mode, point = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    if mode == 'recover':
        recovery_worker(case, point)
    else:
        setup_worker(case, mode, point)
