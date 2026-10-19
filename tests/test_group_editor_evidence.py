"""Grouping-only read-only reasons, filters and unchanged manual edit semantics."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from ai_cull_assistant.group_editor import (
    GroupEditor, grouped_assets, grouping_reason_items, grouping_evidence_text,
)
from ai_cull_assistant.models import PhotoAsset


def asset(name='A', group=1, status='review'):
    path = Path('/tmp') / (name + '.jpg')
    return PhotoAsset(name, path, path, None, path, datetime(2026, 1, 1), '.jpg',
                      group_id=group, analysis_mode='landscape', clarity_evidence={'recommendation': status, 'reliability': 'adequate'})


def editor_model(assets):
    editor = object.__new__(GroupEditor)
    editor.assets = assets
    editor._groups = grouped_assets(assets)
    editor._members_by_group = dict(editor._groups)
    editor.selected_group_id = assets[0].group_id
    editor.selected_stem = None
    editor.filter_var = SimpleNamespace(get=lambda: '全部')
    editor.detail_canvas = SimpleNamespace(yview_moveto=Mock())
    editor._redraw_rows = Mock()
    editor._redraw_detail = Mock()
    editor._refresh_inspector = Mock()
    editor.on_change = Mock()
    return editor


def test_scoped_reasons_do_not_mix_legacy_face_and_body():
    photo = asset()
    photo.clarity_evidence.update(reasons=['insufficient_native_face_pixels'], primary_quality={
        'reasons_by_part': {'body': ['body_region_ownership_unresolved'], 'face': ['face_visibility_unverified']}})
    before = deepcopy(asdict(photo))
    result = grouping_reason_items(photo)
    assert len(result) == 2
    assert result[0].startswith('身体：')
    assert result[1].startswith('面部：')
    assert '不代表已确认遮挡' in result[1]
    assert '人脸像素不足' not in ' '.join(result)
    assert asdict(photo) == before


def test_unknown_codes_visible_without_invented_diagnosis():
    photo = asset()
    photo.clarity_evidence['reasons'] = ['future_reason', 'future_reason', 17]
    assert grouping_reason_items(photo) == ['原始原因：future_reason']
    text = grouping_evidence_text(photo, 'review')
    assert '本地技术建议：待复核' in text
    assert '本地技术建议：建议弃置' not in text
    assert '人工决定：本页未读取' in text
    assert '不绘制推测区域' in text
    assert '眨眼' not in text


def test_missing_reason_is_explicit_not_invented_blur():
    photo = asset(status='keep')
    assert grouping_reason_items(photo) == []
    assert '未记录具体原因。' in grouping_evidence_text(photo, 'keep')


def test_filter_uses_existing_reliability_gate_without_mutation():
    photos = [asset('A', status='keep'), asset('B', status='reject'), asset('C')]
    photos[1].clarity_evidence['reliability'] = 'insufficient'
    editor = editor_model(photos)
    before = deepcopy([asdict(a) for a in photos])
    for label, expected in [('全部', ['A', 'B', 'C']), ('待复核', ['B', 'C']),
                            ('建议保留', ['A']), ('建议弃置', [])]:
        editor.filter_var = SimpleNamespace(get=lambda label=label: label)
        assert [a.stem for a in editor._filtered_members()] == expected
    assert [asdict(a) for a in photos] == before


def test_filter_and_group_change_clear_hidden_split_anchor():
    editor = editor_model([asset('A', status='keep'), asset('B'), asset('C', group=2)])
    editor.selected_stem = 'A'
    editor.filter_var = SimpleNamespace(get=lambda: '待复核')
    editor._filter_changed()
    assert editor.selected_stem is None
    editor.detail_canvas.yview_moveto.assert_called_with(0)
    editor.selected_stem = 'B'
    editor._select_group(2)
    assert editor.selected_stem is None
    assert editor.selected_group_id == 2


def test_split_merge_preserve_immediate_save_callback():
    editor = editor_model([asset('A'), asset('B'), asset('C', group=2)])
    editor.selected_stem = 'B'
    editor._split_selected()
    assert editor.on_change.call_count == 1
    assert editor.assets[0].group_id != editor.assets[1].group_id
    assert editor.selected_group_id == editor.assets[1].group_id
    editor._merge_neighbor(-1)
    assert editor.on_change.call_count == 2
    assert editor.assets[0].group_id == editor.assets[1].group_id
    assert editor.selected_stem is None


def test_responsive_inspector_preserves_explicit_user_choice():
    editor = editor_model([asset()])
    editor.content = object()
    editor._inspector_override = False
    editor._show_inspector = Mock()
    editor._content_configured(SimpleNamespace(widget=editor.content, width=880))
    editor._show_inspector.assert_called_with(False)
    editor._content_configured(SimpleNamespace(widget=editor.content, width=1420))
    editor._show_inspector.assert_called_with(True)
    editor.inspector_frame = SimpleNamespace(winfo_manager=lambda: 'grid')
    editor._toggle_inspector()
    editor._show_inspector.assert_called_with(False)
    editor._show_inspector.reset_mock()
    editor._content_configured(SimpleNamespace(widget=editor.content, width=1420))
    editor._show_inspector.assert_not_called()


def test_thumbnail_redraw_does_not_reset_inspector_scroll():
    editor = editor_model([asset()])
    del editor._refresh_inspector  # Exercise real method, not model fixture stub.
    editor.evidence_text = Mock()
    editor.selected_stem = 'A'
    editor._refresh_inspector()
    assert editor.evidence_text.insert.call_count == 1
    editor._refresh_inspector()
    assert editor.evidence_text.insert.call_count == 1


def test_expired_portrait_policy_cannot_revive_cached_reject():
    from ai_cull_assistant.group_editor import grouping_recommendations, local_recommendation_summary
    photo = asset(status='reject')
    photo.analysis_mode = 'portrait'
    photo.clarity_evidence['primary_quality'] = {'version': 'confirmed-primary-quality-v1',
                                               'recommendation': 'reject'}
    editor = editor_model([photo])
    assert grouping_recommendations([photo])[str(photo.primary_path)]['recommendation'] == 'review'
    assert '当前本地：待复核' in local_recommendation_summary([photo], photo)
    editor.filter_var = SimpleNamespace(get=lambda: '建议弃置')
    assert editor._filtered_members() == []
    assert '缓存建议未获当前校验' in grouping_evidence_text(photo, 'review')


def test_activate_clears_anchor_hidden_by_recomputed_filter():
    photo = asset(status='keep')
    editor = editor_model([photo])
    editor._closed = False
    editor.winfo_exists = lambda: True
    editor.selected_stem = photo.stem
    editor.filter_var = SimpleNamespace(get=lambda: '建议保留')
    photo.clarity_evidence['recommendation'] = 'review'
    editor.on_activate()
    assert editor.selected_stem is None
    assert editor._filtered_members() == []
    editor.on_change.assert_not_called()


def test_subject_route_exact_visible_object_without_mutating_evidence():
    first, second = asset('same'), asset('same')
    second.primary_path = Path('/different/same.jpg')
    editor = editor_model([first, second])
    editor.on_select_subject = Mock()
    before = deepcopy([asdict(a) for a in editor.assets])
    editor._select_asset(second.stem, second)
    editor._choose_subject_for_photo()
    editor.on_select_subject.assert_called_once_with(second)
    assert editor.on_select_subject.call_args.args[0] is second
    assert [asdict(a) for a in editor.assets] == before


def test_subject_route_no_selection_hidden_or_ambiguous_does_not_call():
    editor = editor_model([asset('same'), asset('same')])
    editor.on_select_subject = Mock()
    editor._choose_subject_for_photo()
    editor.selected_stem = 'same'
    editor._choose_subject_for_photo()
    editor._selected_subject_asset = editor.assets[0]
    editor.filter_var = SimpleNamespace(get=lambda: '建议保留')
    editor._choose_subject_for_photo()
    editor.on_select_subject.assert_not_called()


def test_subject_button_tracks_selection_without_confirmation():
    photo=asset('A');editor=editor_model([photo]);editor.on_select_subject=Mock()
    editor.subject_button=Mock();editor.evidence_text=Mock()
    # Replace model stub with the actual read-only refresh.
    GroupEditor._refresh_inspector(editor)
    editor.subject_button.configure.assert_called_with(state='disabled')
    editor.selected_stem='A'
    GroupEditor._refresh_inspector(editor)
    editor.subject_button.configure.assert_called_with(state='normal')
    assert 'primary_subject_confirmation' not in photo.clarity_evidence
    editor.on_select_subject.assert_not_called()


def test_scope_measurement_and_stale_reasons_are_distinct():
    from ai_cull_assistant.group_editor import grouping_review_stage
    photo=asset()
    photo.clarity_evidence['primary_quality']={'issues':[{'part':'selection','reason':'primary_selection_unconfirmed'}]}
    assert grouping_review_stage(photo).startswith('主体范围待指定')
    photo.clarity_evidence['primary_quality']={'recommendation':'review','issues':[{'part':'face','reason':'insufficient_native_face_pixels'}]}
    assert grouping_review_stage(photo).startswith('清晰度或局部证据待复核')
    photo.ai_focus_dirty=True
    assert grouping_review_stage(photo).startswith('需重新扫描')
    photo.ai_focus_dirty=False
    photo.clarity_evidence={'recommendation':'keep','reasons':['strong_landmark_aligned_detail']}
    assert '待复核' not in grouping_review_stage(photo)


def test_same_stem_filter_never_substitutes_a_different_photo():
    first, second=asset('same', status='keep'), asset('same', status='review')
    second.primary_path=Path('/different/same.jpg')
    editor=editor_model([first,second]);editor.on_select_subject=Mock()
    editor._select_asset(first.stem,first)
    editor.filter_var=SimpleNamespace(get=lambda:'待复核')
    # Guard also works before a filter UI refresh has run.
    editor._choose_subject_for_photo()
    editor.on_select_subject.assert_not_called()
    editor._filter_changed()
    assert editor.selected_stem is None
    assert editor._subject_target() is None


def test_current_scoped_quality_reason_beats_legacy_selection_reason():
    from ai_cull_assistant.group_editor import grouping_review_stage
    photo=asset()
    photo.clarity_evidence.update(reasons=['primary_selection_unconfirmed'], primary_quality={
        'recommendation':'review','issues':[{'reason':'insufficient_native_face_pixels'}]})
    assert grouping_review_stage(photo).startswith('清晰度或局部证据待复核')


def test_duplicate_name_split_is_refused_without_mutating_groups(monkeypatch):
    editor=editor_model([asset('same'),asset('same')]);editor.selected_stem='same'
    before=deepcopy([asdict(a) for a in editor.assets])
    monkeypatch.setattr('ai_cull_assistant.group_editor.messagebox.showinfo',Mock())
    editor._split_selected()
    assert [asdict(a) for a in editor.assets]==before
    editor.on_change.assert_not_called()


def test_exact_selected_status_does_not_borrow_same_named_advice():
    first,second=asset('same',status='reject'),asset('same',status='keep')
    second.primary_path=Path('/different/same.jpg')
    editor=editor_model([first,second]);editor.detail_status_var=Mock()
    editor._select_asset(second.stem,second);editor._refresh_detail_status()
    assert '当前本地：推荐保留' in editor.detail_status_var.set.call_args.args[0]


def test_only_old_ai_review_stale_does_not_demand_another_local_rescan():
    from ai_cull_assistant.group_editor import grouping_review_stage
    photo=asset(status='keep');photo.ai_focus_stale=True;photo.ai_focus_dirty=False
    assert not grouping_review_stage(photo).startswith('需重新扫描')
    text=grouping_evidence_text(photo,'keep')
    assert '旧 AI 复核已失效' in text
    assert '不表示本地测量也已过期' in text
