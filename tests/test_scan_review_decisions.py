"""Synthetic production ReviewProject/LR round trips; no API or image writes."""
import copy
from datetime import datetime
import json
from pathlib import Path

from PIL import Image
import pytest

from ai_cull_assistant.ai_project import ReviewProject, photo_id
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.lightroom_results import effective_decision
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.scan_review_decisions import DecisionConflict, ReviewDecisions


def make_asset(root, name='同名 照片'):
    root.mkdir(parents=True, exist_ok=True)
    path = root / (name + '.jpg')
    Image.new('RGB', (80, 100), 'gray').save(path)
    return PhotoAsset(name, path, path, None, path, datetime(2026, 1, 1), '.jpg',
                      analysis_mode='landscape', clarity_version='clarity-v2',
                      clarity_evidence={'state': 'uncertain', 'recommendation': 'review',
                                        'reasons': ['insufficient_native_eye_pixels']})


def test_view_is_read_only_for_absent_and_old_project(tmp_path):
    asset = make_asset(tmp_path / '照片')
    workspace = tmp_path / '独立 工作区'
    service = ReviewDecisions(workspace, [asset], CropSettings())
    original = copy.deepcopy(asset)
    row = service.rows()[photo_id(asset)]
    assert row['final']['confirmed'] is False
    assert asset == original and not workspace.exists()
    service.set(asset, pick_status=1)
    before = service.path.read_bytes()
    service.rows()
    assert service.path.read_bytes() == before and asset == original


def test_roundtrip_manual_pending_stars_pair_and_duplicate_stem_export(tmp_path):
    first = make_asset(tmp_path / 'A')
    first.raw_path = first.primary_path.with_suffix('.RW2')
    first.raw_path.write_bytes(b'synthetic RAW member, not decoded')
    second = make_asset(tmp_path / 'B')
    photos = [first, second]
    originals = {p: p.read_bytes() for a in photos for p in a.rating_target_paths}
    workspace = tmp_path / '工作区 space'
    service = ReviewDecisions(workspace, photos, CropSettings())
    service.rows()
    service.set(first, pick_status=1)
    service.set(first, rating=5)
    service.set(second, pick_status=-1, rating=2)
    reopened = ReviewDecisions(workspace, photos, CropSettings())
    rows = reopened.rows()
    assert len(rows) == 2
    assert effective_decision(rows[photo_id(first)])['focus_review'] is True
    assert rows[photo_id(first)]['clarity_evidence'] == first.clarity_evidence
    output = json.loads(reopened.export().read_text('utf-8'))['photos']
    assert len(output) == 3
    by_path = {r['path']: r for r in output}
    for p in first.rating_target_paths:
        assert by_path[str(p.resolve())]['pick_status'] == 1
        assert by_path[str(p.resolve())]['rating'] == 5
        assert by_path[str(p.resolve())]['focus_review'] is True
        assert by_path[str(p.resolve())]['ai_metadata']['clarity_status'] == '清晰度待确认'
    assert by_path[str(second.primary_path.resolve())]['pick_status'] == -1
    reopened.set(first, rating=0)
    output = json.loads(reopened.export().read_text('utf-8'))['photos']
    assert all(r['rating'] == 0 for r in output if r['path'] in {str(p.resolve()) for p in first.rating_target_paths})
    reopened.withdraw(first)
    row = reopened.rows()[photo_id(first)]
    assert not row['final']['confirmed']
    assert effective_decision(row)['recommendation'] == 'review'
    assert [h['action'] for h in row['final_history']] == ['confirm', 'confirm', 'confirm', 'withdraw']
    assert {p: p.read_bytes() for p in originals} == originals
    assert not list(tmp_path.rglob('*.xmp'))


def test_stars_alone_do_not_approve_keep_and_pick_alone_keeps_current_ai_star(tmp_path):
    asset = make_asset(tmp_path / 'photos')
    service = ReviewDecisions(tmp_path / 'work', [asset], CropSettings())
    service.rows()
    row = service.set(asset, rating=4)
    decision = effective_decision(row)
    assert decision['rating'] == 4 and decision['pick_status'] is None
    assert decision['recommendation'] == 'review' and decision['focus_review']
    service.withdraw(asset)
    project = ReviewProject(service.workspace)
    row = project.data['photos'][photo_id(asset)]
    row['ai'] = {'fingerprint': row['fingerprint'], 'rating': 3, 'suggest_reject': False}
    project.save()
    service.rows()
    row = service.set(asset, pick_status=1)
    assert effective_decision(row)['rating'] == 3
    assert row['final']['rating_confirmed'] is False


def test_stale_project_or_changed_input_refused_and_repeated_action_idempotent(tmp_path):
    asset = make_asset(tmp_path / 'photos')
    first = ReviewDecisions(tmp_path / 'work', [asset], CropSettings())
    second = ReviewDecisions(tmp_path / 'work', [asset], CropSettings())
    first.rows(); second.rows()
    first.set(asset, pick_status=1)
    before = first.path.read_bytes()
    with pytest.raises(DecisionConflict):
        second.set(asset, pick_status=-1)
    assert first.path.read_bytes() == before
    first.set(asset, pick_status=1)
    assert first.path.read_bytes() == before
    asset.primary_path.write_bytes(b'replaced')
    with pytest.raises(DecisionConflict):
        first.set(asset, rating=5)
    assert first.path.read_bytes() == before


def test_failure_keeps_persisted_and_in_memory_manual_state(tmp_path, monkeypatch):
    from ai_cull_assistant import ai_project
    asset = make_asset(tmp_path / 'photos')
    service = ReviewDecisions(tmp_path / 'work', [asset], CropSettings())
    service.rows(); service.set(asset, pick_status=1)
    before = service.path.read_bytes()
    project = ReviewProject(service.workspace)
    state = copy.deepcopy(project.data)
    def fail(*args, **kwargs):
        raise OSError('simulated disk full')
    monkeypatch.setattr(ai_project, 'atomic_json', fail)
    with pytest.raises(OSError, match='disk full'):
        service.set(asset, rating=5)
    with pytest.raises(OSError, match='disk full'):
        project.confirm(photo_id(asset), 1, -1)
    assert project.data == state and service.path.read_bytes() == before
    with pytest.raises(OSError):
        project.withdraw_confirmation(photo_id(asset))
    assert project.data == state and service.path.read_bytes() == before


def test_manual_decision_does_not_revive_stale_photography_ai(tmp_path):
    asset = make_asset(tmp_path / 'photos')
    project = ReviewProject(tmp_path / 'work')
    project.refresh([asset], CropSettings())
    row = project.data['photos'][photo_id(asset)]
    row['ai'] = {'fingerprint': row['fingerprint'], 'rating': 5, 'suggest_reject': True}
    row['stale'] = True
    project.save()
    project.confirm(photo_id(asset), 4, 1)
    assert project.data['photos'][photo_id(asset)]['stale'] is True
    project.withdraw_confirmation(photo_id(asset))
    assert project.effective_decision(photo_id(asset))['recommendation'] == 'review'


def test_shipped_lua_explicit_zero_clears_only_rating():
    from lupa.lua51 import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    plugin = Path(__file__).resolve().parents[1] / 'lightroom/PhotoCullAssistant.lrplugin'
    core = lua.execute((plugin / 'Core.lua').read_text('utf-8'))
    decoder = lua.execute((plugin / 'json.lua').read_text('utf-8'))
    lua.execute('''photo={rating=5,pickStatus=1}; writes={}
    function photo:getRawMetadata(k) return self[k] end
    function photo:setRawMetadata(k,v) self[k]=v; table.insert(writes,k) end
    catalog={findPhotoByPath=function(self,path) return photo end}''')
    payload = {'format': 'photo-cull-assistant', 'version': 1,
               'photos': [{'path': '/synthetic/中文.jpg', 'rating': 0}]}
    rows = core.validate(decoder.decode(json.dumps(payload, ensure_ascii=False)))
    matched, _ = core.plan(rows, lua.globals().catalog)
    core.apply(matched)
    assert lua.globals().photo['rating'] == 0
    assert lua.globals().photo['pickStatus'] == 1
    assert list(lua.globals().writes.values()) == ['rating']


def test_old_ai_project_instance_cannot_overwrite_new_manual_decision(tmp_path):
    asset = make_asset(tmp_path / 'photos')
    initial = ReviewProject(tmp_path / 'work')
    initial.refresh([asset], CropSettings())
    old = ReviewProject(initial.workspace)
    current = ReviewDecisions(initial.workspace, [asset], CropSettings())
    current.rows(); current.set(asset, pick_status=1, rating=5)
    before = current.path.read_bytes()
    old.data['photos'][photo_id(asset)]['ai'] = {'rating': 1, 'suggest_reject': True}
    with pytest.raises(ValueError, match='另一个操作更新'):
        old.save()
    assert current.path.read_bytes() == before


def test_export_requires_current_displayed_source(tmp_path):
    asset = make_asset(tmp_path / 'photos')
    service = ReviewDecisions(tmp_path / 'work', [asset], CropSettings())
    service.rows()
    asset.primary_path.write_bytes(b'changed input')
    with pytest.raises(DecisionConflict, match='未导出'):
        service.export()
    assert not list(service.workspace.rglob('lightroom_results.json'))


def test_readonly_archive_is_not_silently_treated_as_empty_project(tmp_path):
    asset = make_asset(tmp_path / 'photos')
    workspace = tmp_path / 'work'; workspace.mkdir()
    archive = workspace / '.workspace-archive.zip'
    archive.write_bytes(b'archive identity, not parsed on read-only route')
    before = archive.read_bytes()
    service = ReviewDecisions(workspace, [asset], CropSettings())
    with pytest.raises(ValueError, match='旧归档'):
        service.rows()
    assert archive.read_bytes() == before and not service.path.exists()


def test_concurrent_project_saves_publish_one_complete_version(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    asset = make_asset(tmp_path / 'photos')
    original = ReviewProject(tmp_path / 'work')
    original.refresh([asset], CropSettings())
    projects = [ReviewProject(original.workspace), ReviewProject(original.workspace)]
    barrier = threading.Barrier(2)
    def save(index):
        project = projects[index]
        project.data['photos'][photo_id(asset)]['final'] = {
            'confirmed': True, 'pick_status': 1 if index == 0 else -1, 'rating': index + 3}
        barrier.wait(timeout=5)
        try:
            project.save()
            return 'saved'
        except ValueError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, [0, 1]))
    assert sorted(outcomes) == ['conflict', 'saved']
    row = json.loads(original.path.read_text('utf-8'))['photos'][photo_id(asset)]
    assert (row['final']['pick_status'], row['final']['rating']) in {(1, 3), (-1, 4)}
