from dataclasses import asdict
import json
from threading import Event

from PIL import Image
import pytest

from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.processing_job import start_job, load_job
from ai_cull_assistant.session_store import load_session, source_changes
from test_processing_job import _photos, _options


def initial(tmp_path, count=3):
    photos = _photos(tmp_path, count)
    workspace = tmp_path / 'workspace'
    result = start_job(photos, workspace, _options(), CropSettings(), mode='scan').run(
        _options(), CropSettings(), Event(), None)
    return photos, workspace, result


def test_delete_restores_remaining_and_invalidates_sheets(tmp_path):
    photos, workspace, result = initial(tmp_path)
    survivor = asdict(result.assets[1])
    result.assets[0].primary_path.unlink()
    restored = load_session(workspace, photos)
    assert len(restored.assets) == 2
    assert asdict(restored.assets[0]) == survivor
    assert len(load_session(workspace, photos).assets) == 2
    assert json.loads((workspace/'workflow-stage.json').read_text())['contact_sheets_ready'] is False
    assert 'P0000' not in json.loads((workspace/'groups.json').read_text())['photo_stems']
    assert 'P0000' not in json.loads((workspace/'screening_results.json').read_text())['results']


def test_unavailable_source_keeps_session(tmp_path):
    photos, workspace, result = initial(tmp_path)
    before = (workspace/'scan-session.json').read_bytes()
    photos.rename(tmp_path/'offline')
    with pytest.raises(ValueError, match='不可访问'):
        load_session(workspace, photos)
    assert (workspace/'scan-session.json').read_bytes() == before


def test_added_only_scanned_and_resume_preserves_old_analysis(tmp_path, monkeypatch):
    photos, workspace, result = initial(tmp_path)
    old = [asdict(a) for a in result.assets]
    Image.new('RGB', (80, 100), 'red').save(photos/'NEW.jpg')
    assert len(source_changes(workspace, photos)[0]) == 1
    restored = load_session(workspace, photos)
    job = start_job(photos, workspace, _options(), CropSettings(), result=restored, mode='scan')
    assert len(job._data['work_indices']) == 1
    stop = Event(); stop.set()
    assert job.run(_options(), CropSettings(), stop, None) is None
    job = load_job(workspace, photos)
    calls = []
    original = job._scan_one
    def scan(asset, *args):
        calls.append(asset.stem)
        return original(asset, *args)
    monkeypatch.setattr(job, '_scan_one', scan)
    completed = job.run(_options(), CropSettings(), Event(), None)
    assert calls == ['NEW']
    by_stem = {a.stem: a for a in completed.assets}
    for row in old:
        assert asdict(by_stem[row['stem']]) == row
    assert by_stem['NEW'].preview_path.is_file()
    assert len(load_session(workspace, photos).assets) == 4
    assert source_changes(workspace, photos) == ([], [])


def test_all_deleted_keeps_empty_workspace(tmp_path):
    photos, workspace, result = initial(tmp_path, 1)
    result.assets[0].primary_path.unlink()
    assert load_session(workspace, photos).assets == []
    assert load_session(workspace, photos).assets == []


def test_raw_removed_remaining_jpeg_is_pending_scan(tmp_path):
    from ai_cull_assistant.session_store import save_session
    photos, workspace, result = initial(tmp_path, 1)
    asset = result.assets[0]
    raw = photos/'P0000.RW2'
    raw.write_bytes(b'raw-placeholder')
    asset.raw_path = asset.primary_path = raw
    save_session(result, fresh=True)
    raw.unlink()
    assert load_session(workspace, photos).assets == []
    assert source_changes(workspace, photos)[0] == [str((photos/'P0000.jpg').resolve())]


def test_copied_workspace_with_deleted_photo_can_restore(tmp_path):
    import shutil
    from ai_cull_assistant.project_storage import relocate_copied_workspace
    photos, workspace, result = initial(tmp_path)
    copied = tmp_path/'copied'
    shutil.copytree(workspace, copied)
    result.assets[0].primary_path.unlink()
    relocate_copied_workspace(copied, json.loads((copied/'scan-session.json').read_text()))
    restored = load_session(copied, photos)
    assert len(restored.assets) == 2
    assert restored.workspace_dir == copied
