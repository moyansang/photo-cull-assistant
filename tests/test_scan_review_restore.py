from datetime import datetime
import json

import pytest

from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.session_store import load_session, save_session
from ai_cull_assistant.workflow import ScanResult
from ai_cull_assistant.scan_review_ui import result_state


def fixture(tmp_path):
    source = tmp_path / '原图 空格'
    source.mkdir()
    jpg = source / 'same.jpg'
    raw = source / 'same.dng'
    other = source / 'other.jpg'
    for path in (jpg, raw, other):
        path.write_bytes(b'not decoded')
    assets = [PhotoAsset('same', jpg, raw, raw, jpg, datetime(2026, 1, 1), '.dng', group_id=7,
                         clarity_evidence={'recommendation': 'keep'},
                         ai_focus_result={'status': 'clear', 'reason': 'saved'},
                         local_recommendation='keep'),
              PhotoAsset('other', other, other, None, other, datetime(2026, 1, 1), '.jpg', group_id=7)]
    workspace = tmp_path / '工作区'
    result = ScanResult(assets, workspace / 'previews', workspace / 'contact_sheets', workspace,
                        workspace / 'groups.json', input_dir=source)
    save_session(result)
    (workspace / 'groups.json').write_text('{"original":true}', encoding='utf-8')
    return result


def bytes_in(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_missing_members_retained_no_write(tmp_path):
    result = fixture(tmp_path)
    result.assets[0].raw_path.unlink()
    result.assets[1].primary_path.unlink()
    before = bytes_in(result.workspace_dir)
    restored = load_session(result.workspace_dir, result.input_dir, preserve_unavailable=True)
    assert len(restored.assets) == 2
    assert restored.assets[0].raw_path == result.assets[0].raw_path
    assert restored.assets[0].jpg_path == result.assets[0].jpg_path
    assert restored.assets[0].group_id == 7
    assert restored.assets[0].clarity_evidence == result.assets[0].clarity_evidence
    assert restored.assets[0].ai_focus_dirty and restored.assets[0].ai_focus_stale
    assert any('缺失' in n for n in restored.assets[0].notes)
    assert result_state(restored.assets[0]) == 'failed'
    assert result_state(restored.assets[1]) == 'failed'
    assert bytes_in(result.workspace_dir) == before


def test_modified_source_and_old_fields_are_readonly(tmp_path):
    result = fixture(tmp_path)
    path = result.workspace_dir / 'scan-session.json'
    data = json.loads(path.read_text('utf-8'))
    for row in data['assets']:
        for key in ('clarity_version', 'focus_scope'):
            row.pop(key)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    result.assets[0].jpg_path.write_bytes(b'modified source')
    before = bytes_in(result.workspace_dir)
    with pytest.raises(ValueError, match='已修改'):
        load_session(result.workspace_dir, result.input_dir)
    restored = load_session(result.workspace_dir, result.input_dir, preserve_unavailable=True)
    assert len(restored.assets) == 2
    assert restored.assets[0].focus_scope is None
    assert restored.assets[0].clarity_version is None
    assert restored.assets[0].local_recommendation == 'keep'
    assert restored.assets[0].ai_focus_dirty
    assert any('已修改' in n for n in restored.assets[0].notes)
    assert result_state(restored.assets[0]) == 'review'
    assert restored.assets[0].clarity_evidence == result.assets[0].clarity_evidence
    assert bytes_in(result.workspace_dir) == before


def test_offline_directory_still_shows_inventory(tmp_path):
    result = fixture(tmp_path)
    result.input_dir.rename(tmp_path / 'temporarily offline')
    before = bytes_in(result.workspace_dir)
    restored = load_session(result.workspace_dir, result.input_dir, preserve_unavailable=True)
    assert len(restored.assets) == 2
    assert all(asset.ai_focus_dirty for asset in restored.assets)
    assert bytes_in(result.workspace_dir) == before


def test_focus_evidence_directory_does_not_create_lock(tmp_path):
    result = fixture(tmp_path)
    (result.workspace_dir / 'focus-evidence').mkdir()
    before = bytes_in(result.workspace_dir)
    load_session(result.workspace_dir, result.input_dir, preserve_unavailable=True)
    assert bytes_in(result.workspace_dir) == before
    assert not (result.workspace_dir / '.evidence-operation.lock').exists()


def test_explicit_partial_recheck_preserves_unchecked_inventory(tmp_path):
    result = fixture(tmp_path)
    path = result.workspace_dir / 'scan-session.json'
    old = json.loads(path.read_text('utf-8'))['source_stats']
    target = result.assets[0]
    untouched = result.assets[1]
    target.primary_path.write_bytes(b'new explicit check source')
    untouched.primary_path.unlink()
    retained = {str(untouched.primary_path.resolve()): old[str(untouched.primary_path.resolve())]}
    save_session(result, fresh=True, retained_source_stats=retained)
    saved = json.loads(path.read_text('utf-8'))['source_stats']
    assert saved[str(untouched.primary_path.resolve())] == retained[str(untouched.primary_path.resolve())]
    st = target.primary_path.stat()
    assert saved[str(target.primary_path.resolve())] == [st.st_size, st.st_mtime_ns]
    assert saved[str(target.primary_path.resolve())] != old[str(target.primary_path.resolve())]
    with pytest.raises(FileNotFoundError):
        save_session(result, fresh=True)


def test_partial_recheck_does_not_bless_unchecked_changed_source(tmp_path):
    result = fixture(tmp_path)
    path = result.workspace_dir / 'scan-session.json'
    old = json.loads(path.read_text('utf-8'))['source_stats']
    untouched = result.assets[1].primary_path
    untouched.write_bytes(b'changed but never rechecked')
    key = str(untouched.resolve())
    save_session(result, fresh=True, retained_source_stats={key: old[key]})
    saved = json.loads(path.read_text('utf-8'))['source_stats']
    assert saved[key] == old[key]
    assert saved[key] != [untouched.stat().st_size, untouched.stat().st_mtime_ns]
    restored = load_session(result.workspace_dir, result.input_dir, preserve_unavailable=True)
    assert 'source_changed' in restored.assets[1].notes
