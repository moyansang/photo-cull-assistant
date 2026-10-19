"""Headless UI-state/Canvas contracts only; no real camera or Tk visual validation."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_cull_assistant import af_overlay, af_jpeg_preview
from PIL import Image
from ai_cull_assistant.group_editor import (
    GroupEditor, _af_source_stamp, _af_jpeg_source, draw_af_projection, draw_af_diagnostic, grouped_assets,
)
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.ui_style import COLORS


class Value:
    def __init__(self, value=''):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeLoader:
    """Deliberately returns stale results too: UI must not trust queue identity."""
    def __init__(self):
        self.counter = count(1)
        self.requests = []
        self.results = []
        self.invalidations = 0
        self.closed = False

    def request(self, path):
        token = next(self.counter)
        self.requests.append((token, path))
        return token

    def pop_completed(self):
        results, self.results = self.results, []
        return [(token, value if 'metadata' in value else {
            'metadata': value, 'clean_image': None, 'status_text': af_overlay.af_status_text(value)})
            for token, value in results]

    def invalidate(self):
        self.invalidations += 1

    def close(self):
        self.closed = True


def photo(path, *, name=None, group=1):
    path = Path(path)
    path.write_bytes(b'original camera bytes')
    return PhotoAsset(name or path.stem, path, path, None, path,
                      datetime(2026, 1, 1), '.jpg', group_id=group,
                      analysis_mode='landscape')


def editor_for(assets, monkeypatch):
    editor = object.__new__(GroupEditor)
    editor.assets = assets
    editor._groups = grouped_assets(assets)
    editor._members_by_group = dict(editor._groups)
    editor.selected_group_id = assets[0].group_id
    editor.selected_stem = None
    editor._selected_subject_asset = None
    editor.filter_var = Value('全部')
    editor.af_overlay_var = Value(False)
    editor.af_status_var = Value('AF已关闭')
    editor._closed = False
    editor._af_suspended = False
    editor._af_loader = None
    editor._af_poll_token = None
    editor._af_request_token = None
    editor._af_request_target = None
    editor._af_request_path = None
    editor._af_request_stamp = None
    editor._af_record = None
    editor._redraw_token = editor._detail_redraw_token = editor._thumb_poll_token = None
    editor._thumbnail_loader = Mock()
    editor._redraw_rows = Mock()
    editor._redraw_detail = Mock(side_effect=lambda: editor._refresh_inspector())
    editor._schedule_detail_redraw = Mock()
    editor.detail_canvas = Mock()
    editor.evidence_text = Mock()
    editor.subject_button = Mock()
    editor.on_change = Mock()
    editor.on_select_subject = Mock()
    editor.winfo_exists = lambda: True
    editor.callbacks = {}
    callback_ids = count(1)

    def after(delay, callback):
        token = f'af-after-{next(callback_ids)}'
        editor.callbacks[token] = callback
        return token

    editor.after = Mock(side_effect=after)
    editor.after_cancel = Mock(side_effect=lambda token: editor.callbacks.pop(token, None))
    loader = FakeLoader()
    factory = Mock(return_value=loader)
    monkeypatch.setattr(af_jpeg_preview, 'AfJpegPreviewLoader', factory)
    return editor, loader, factory


def enable(editor, asset):
    editor._select_asset(asset.stem, asset)
    editor.af_overlay_var.set(True)
    editor._toggle_af_overlay()
    return editor._af_request_token


def poll(editor):
    token = editor._af_poll_token
    assert token in editor.callbacks
    editor.callbacks.pop(token)()


def parsed_record():
    return {'status': 'not_validated', 'reason': 'real_camera_coordinate_mapping_not_validated',
            'diagnostics': {'coordinates': {'oriented_center_xy_hypothesis': [.2, .8],
                                           'oriented_bbox_xywh_hypothesis': [.1, .7, .2, .2]}}}


def test_actual_build_controls_default_off_and_are_in_separate_row(monkeypatch):
    from ai_cull_assistant import group_editor as module

    class Widget:
        def __init__(self, master=None, **kwargs):
            self.master, self.options = master, kwargs

        def __getattr__(self, name):
            return Mock()

    for name in ('Frame', 'Label', 'Button', 'Combobox', 'Checkbutton', 'LabelFrame', 'Scrollbar'):
        monkeypatch.setattr(module.ttk, name, Widget)
    for name in ('Canvas', 'Text'):
        monkeypatch.setattr(module.tk, name, Widget)
    monkeypatch.setattr(module.tk, 'StringVar', Value)
    monkeypatch.setattr(module.tk, 'BooleanVar', Value)
    editor = object.__new__(GroupEditor)
    editor._embedded = False
    editor._refresh_inspector = Mock()
    editor._build_ui()
    assert editor.af_overlay_var.get() is False
    assert editor.af_status_var.get() == 'AF已关闭'
    assert editor.af_toggle.options['text'] == 'AF区域（所选照片）'
    assert editor.af_toggle.master is not editor.subject_button.master
    assert editor.af_toggle.options['command'] == editor._toggle_af_overlay


def test_disabled_or_unselected_never_constructs_loader(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, factory = editor_for([asset], monkeypatch)
    editor._select_asset(asset.stem, asset)
    editor._refresh_inspector()
    factory.assert_not_called()
    editor._selected_subject_asset = None
    editor.selected_stem = None
    editor.af_overlay_var.set(True)
    editor._toggle_af_overlay()
    factory.assert_not_called()
    assert not loader.requests
    assert '请选择' in editor.af_status_var.get()
    assert editor.callbacks == {}


def test_only_exact_selected_paired_jpeg_source_is_requested(tmp_path, monkeypatch):
    first = photo(tmp_path / 'A.jpg')
    second = photo(tmp_path / 'B.jpg')
    paired = tmp_path / 'B.RW2'
    paired.write_bytes(b'RAW bytes')
    second.raw_path = second.primary_path = paired
    second.preview_path = tmp_path / 'cached.jpg'
    editor, loader, factory = editor_for([first, second], monkeypatch)
    token = enable(editor, second)
    assert loader.requests == [(token, str(second.jpg_path))]
    assert editor._af_request_target is second
    assert editor._af_record is None
    assert len(editor.callbacks) == 1
    factory.assert_called_once_with()
    editor._refresh_inspector()
    assert len(loader.requests) == 1


def test_record_shows_status_but_never_draws_hypothesis(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    token = enable(editor, asset)
    record = parsed_record()
    loader.results = [(token, record)]
    poll(editor)
    assert editor._af_record == record
    assert '未验证' in editor.af_status_var.get()
    assert editor.callbacks == {}
    editor._draw_af_overlay(asset, object(), (40, 80, 440, 280), 'detail:A')
    editor.detail_canvas.create_rectangle.assert_not_called()
    editor.detail_canvas.create_line.assert_not_called()


@pytest.mark.parametrize('record, expected', [
    ({'status': 'error', 'reason': 'exiftool_unavailable'}, 'ExifTool'),
    ({'status': 'missing', 'reason': 'af_position_and_area_missing'}, '未发现'),
    ({'status': 'unsupported'}, '不支持'),
])
def test_explicit_unknown_and_dependency_statuses(tmp_path, monkeypatch, record, expected):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    token = enable(editor, asset)
    loader.results = [(token, record)]
    poll(editor)
    assert expected in editor.af_status_var.get()
    editor.on_change.assert_not_called()


def test_switch_same_named_assets_rejects_late_previous_photo(tmp_path, monkeypatch):
    first = photo(tmp_path / 'first.jpg', name='same')
    second = photo(tmp_path / 'second.jpg', name='same')
    editor, loader, _ = editor_for([first, second], monkeypatch)
    old = enable(editor, first)
    editor._select_asset(second.stem, second)
    current = editor._af_request_token
    assert current != old
    loader.results = [(old, {'status': 'missing'})]
    poll(editor)
    assert editor._af_record is None
    assert editor._af_request_target is second
    assert editor.af_status_var.get() == '正在读取同源预览与AF区域信息…'
    assert len(editor.callbacks) == 1
    loader.results = [(current, parsed_record())]
    poll(editor)
    assert editor._af_record == parsed_record()


def test_turn_off_then_on_rejects_old_generation_and_hides_old_record(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    old = enable(editor, asset)
    old_callback = editor.callbacks[editor._af_poll_token]
    editor.af_overlay_var.set(False)
    editor._toggle_af_overlay()
    assert editor.callbacks == {}
    assert editor._af_record is None
    assert editor._af_request_target is None
    assert editor.af_status_var.get() == 'AF已关闭'
    old_callback()  # Even an already dequeued Tk callback cannot restart a read.
    assert len(loader.requests) == 1
    editor.af_overlay_var.set(True)
    editor._toggle_af_overlay()
    current = editor._af_request_token
    assert current != old
    loader.results = [(old, {'status': 'missing'}), (current, parsed_record())]
    poll(editor)
    assert editor._af_record == parsed_record()


def test_group_navigation_clears_selected_af_without_scanning_new_group(tmp_path, monkeypatch):
    first = photo(tmp_path / 'A.jpg')
    second = photo(tmp_path / 'B.jpg', group=2)
    editor, loader, _ = editor_for([first, second], monkeypatch)
    old = enable(editor, first)
    editor._select_group(2)
    assert editor._af_request_token is None
    assert len(loader.requests) == 1
    assert editor.callbacks == {}
    loader.results = [(old, parsed_record())]
    editor._drain_af_results()
    assert editor._af_record is None
    assert '请选择' in editor.af_status_var.get()


def test_filter_hiding_selected_asset_invalidates_without_substitution(tmp_path, monkeypatch):
    first = photo(tmp_path / 'A.jpg', name='same')
    second = photo(tmp_path / 'B.jpg', name='same')
    first.clarity_evidence = {'recommendation': 'keep', 'reliability': 'adequate'}
    second.clarity_evidence = {'recommendation': 'review', 'reliability': 'insufficient'}
    editor, loader, _ = editor_for([first, second], monkeypatch)
    enable(editor, first)
    editor.filter_var.set('待复核')
    editor._filter_changed()
    assert editor.selected_stem is None
    assert editor._af_request_target is None
    assert len(loader.requests) == 1
    assert editor.callbacks == {}


def test_unmap_invalidates_and_map_rereads_without_reusing_late_result(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    old = enable(editor, asset)
    late_callback = editor.callbacks[editor._af_poll_token]
    editor._af_page_unmapped(SimpleNamespace(widget=editor))
    assert editor._af_suspended
    assert editor.callbacks == {}
    loader.results = [(old, parsed_record())]
    late_callback()
    assert editor._af_record is None
    assert len(loader.requests) == 1
    editor._af_page_mapped(SimpleNamespace(widget=editor))
    assert not editor._af_suspended
    assert len(loader.requests) == 2
    assert editor._af_request_token != old
    poll(editor)
    assert editor._af_record is None


def test_child_map_events_do_not_restart_parent_af(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    token = enable(editor, asset)
    editor._af_page_unmapped(SimpleNamespace(widget=object()))
    editor._af_page_mapped(SimpleNamespace(widget=object()))
    assert not editor._af_suspended
    assert editor._af_request_token == token
    assert len(loader.requests) == 1


def test_activate_invalidates_even_same_photo_and_source(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    old = enable(editor, asset)
    editor.on_activate()
    assert editor._af_request_token != old
    assert len(loader.requests) == 2
    assert editor._af_request_target is asset


def test_source_rewrite_invalidates_on_refresh_and_poll(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    old = enable(editor, asset)
    asset.primary_path.write_bytes(b'new bytes with different size')
    loader.results = [(old, parsed_record())]
    poll(editor)
    current = editor._af_request_token
    assert current != old
    assert editor._af_record is None
    assert len(loader.requests) == 2
    loader.results = [(current, parsed_record())]
    poll(editor)
    assert editor._af_record is not None
    asset.primary_path.write_bytes(b'rewritten again')
    editor._refresh_inspector()
    assert editor._af_record is None
    assert len(loader.requests) == 3


def test_source_changes_between_selection_guard_and_queue_read(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    old = enable(editor, asset)

    def completion_with_change():
        asset.primary_path.write_bytes(b'changed at completion')
        return [(old, parsed_record())]

    loader.pop_completed = completion_with_change
    poll(editor)
    assert editor._af_record is None
    assert editor._af_request_token != old
    assert len(loader.requests) == 2


def test_paired_jpeg_reassignment_cannot_keep_previous_af_record(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    replacement = photo(tmp_path / 'B.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    old = enable(editor, asset)
    asset.jpg_path = replacement.primary_path
    loader.results = [(old, parsed_record())]
    poll(editor)
    assert editor._af_record is None
    assert editor._af_request_path == str(replacement.primary_path)
    assert loader.requests[-1][1] == str(replacement.primary_path)


def test_stat_errors_are_stable_unknown_then_reappearing_source_rereads(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    asset.primary_path.unlink()
    editor, loader, _ = editor_for([asset], monkeypatch)
    old = enable(editor, asset)
    assert _af_source_stamp(str(asset.primary_path))[0] is None
    editor._refresh_inspector()
    assert len(loader.requests) == 1
    asset.primary_path.write_bytes(b'reappeared')
    editor._refresh_inspector()
    assert editor._af_request_token != old
    assert len(loader.requests) == 2


def test_destroy_cancels_callbacks_and_late_result_never_restarts(tmp_path, monkeypatch):
    from ai_cull_assistant.ui_page import WorkspacePage
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    token = enable(editor, asset)
    late_callback = editor.callbacks[editor._af_poll_token]
    parent_destroy = Mock()
    monkeypatch.setattr(WorkspacePage, 'destroy', parent_destroy)
    editor.destroy()
    assert loader.closed
    assert editor.callbacks == {}
    loader.results = [(token, parsed_record())]
    late_callback()
    editor._af_page_mapped(SimpleNamespace(widget=editor))
    editor.destroy()
    assert editor._af_record is None
    assert len(loader.requests) == 1
    parent_destroy.assert_called_once_with()


def test_af_actions_never_mutate_asset_or_call_subject_save(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    before = deepcopy(asdict(asset))
    source_before = asset.primary_path.read_bytes()
    token = enable(editor, asset)
    loader.results = [(token, parsed_record())]
    poll(editor)
    editor._draw_af_overlay(asset, object(), (0, 0, 400, 200), 'detail:A')
    editor.af_overlay_var.set(False)
    editor._toggle_af_overlay()
    assert asdict(asset) == before
    assert asset.primary_path.read_bytes() == source_before
    editor.on_change.assert_not_called()
    editor.on_select_subject.assert_not_called()


def test_existing_model_surface_without_af_controls_remains_usable():
    editor = object.__new__(GroupEditor)
    editor._draw_af_overlay(object(), object(), (0, 0, 10, 10), 'detail:A')
    editor._sync_af_overlay(None)
    editor._invalidate_af_request()


def geometry(*, status='validated', center=(100., 150.), box=(80., 120., 130., 180.),
             clip_rect=(50., 100., 450., 300.)):
    return af_overlay.OverlayGeometry(status, 'SYNTHETIC-RENDERER-TEST-ONLY', center, box, clip_rect)


def test_synthetic_projection_draws_canvas_xyxy_without_changing_photo_bindings():
    canvas = Mock()
    canvas.create_rectangle.return_value = 10
    canvas.create_line.side_effect = [11, 12]
    result = draw_af_projection(canvas, geometry(), tags=('detail:exact-photo',))
    assert result == (10, 11, 12)
    canvas.create_rectangle.assert_called_once_with(80., 120., 130., 180.,
        outline=COLORS['accent'], width=2, dash=(4, 2), tags=('detail:exact-photo', 'af-overlay'))
    assert canvas.create_line.call_args_list[0].args == (95., 150., 105., 150.)
    assert canvas.create_line.call_args_list[1].args == (100., 145., 100., 155.)
    canvas.tag_bind.assert_not_called()


@pytest.mark.parametrize('center, expected', [
    ((50., 100.), [(50., 100., 55., 100.), (50., 100., 50., 105.)]),
    ((450., 300.), [(445., 300., 450., 300.), (450., 295., 450., 300.)]),
    ((450., 100.), [(445., 100., 450., 100.), (450., 100., 450., 105.)]),
    ((50., 300.), [(50., 300., 55., 300.), (50., 295., 50., 300.)]),
])
def test_synthetic_center_cross_is_clipped_to_actual_image_edges(center, expected):
    canvas = Mock()
    draw_af_projection(canvas, geometry(center=center, box=None))
    assert [call.args for call in canvas.create_line.call_args_list] == expected
    canvas.create_rectangle.assert_not_called()


def test_synthetic_tiny_image_cannot_put_cross_into_letterbox():
    canvas = Mock()
    draw_af_projection(canvas, geometry(center=(11., 21.), box=None, clip_rect=(10., 20., 12., 22.)))
    assert [call.args for call in canvas.create_line.call_args_list] == [
        (10., 21., 12., 21.), (11., 20., 11., 22.)]


@pytest.mark.parametrize('bad', [
    None,
    geometry(status='not_validated'),
    geometry(status='error'),
    geometry(center=(float('nan'), 150.)),
    geometry(center=(float('inf'), 150.)),
    geometry(center=(10**1000, 150.)),
    geometry(center=(True, 150.)),
    geometry(center=(49., 150.)),
    geometry(center=(100., 150.), box=(80., 120., 90., 180.)),
    geometry(box=(80., 120., 80., 180.)),
    geometry(box=(40., 120., 130., 180.)),
    geometry(box=(80., 120., 130., float('inf'))),
    geometry(clip_rect=None),
    geometry(clip_rect=(50., 100., 50., 300.)),
    geometry(clip_rect=(50., 100., 10**1000, 300.)),
])
def test_malformed_unvalidated_or_unbound_projection_draws_nothing(bad):
    canvas = Mock()
    assert draw_af_projection(canvas, bad) == ()
    canvas.create_rectangle.assert_not_called()
    canvas.create_line.assert_not_called()


def test_preview_dimensions_and_even_claimed_mapping_cannot_invent_frame_binding(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    token = enable(editor, asset)
    record = parsed_record()
    record['validated_display_mapping'] = {'status': 'validated'}
    loader.results = [(token, record)]
    poll(editor)
    image = SimpleNamespace(width=lambda: 400, height=lambda: 200)
    assert editor._af_projection_for_thumbnail(asset, image, (50, 100, 450, 300)) is None
    editor._draw_af_overlay(asset, image, (50, 100, 450, 300), 'detail:A')
    editor.detail_canvas.create_rectangle.assert_not_called()
    editor.detail_canvas.create_line.assert_not_called()


def test_draw_gate_rechecks_active_source_and_selection_before_provider(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    token = enable(editor, asset)
    loader.results = [(token, parsed_record())]
    poll(editor)
    provider = Mock(return_value=geometry())
    editor._af_projection_for_thumbnail = provider
    asset.primary_path.write_bytes(b'changed source must never be overlaid')
    editor._draw_af_overlay(asset, object(), (50, 100, 450, 300), 'detail:A')
    provider.assert_not_called()


def test_synthetic_contract_projection_connects_to_canvas_renderer():
    identity = {'size': 42, 'mtime_ns': 100, 'ctime_ns': 101, 'device': 1, 'inode': 2}
    source = {'path': '/synthetic/original.RW2', 'sha256': 'a' * 64,
              'stat_identity': identity, 'unchanged_during_read': True}
    parser = {'name': 'ExifTool', 'path': '/synthetic/exiftool', 'version': '13.59',
              'adapter_version': '1', 'stat_identity': identity}
    record = {'status': 'validated', 'source': source, 'parser': parser,
              'validated_display_mapping': {
                  'status': 'validated', 'validation_id': 'SYNTHETIC-TEST-ONLY',
                  'coordinate_frame': af_overlay.DISPLAY_FRAME, 'exif_transposed': True,
                  'source': source, 'parser': parser, 'preview_id': 'synthetic-crop-and-frame',
                  'frame_size': [1000, 500], 'center_xy': [.25, .6], 'area_size_wh': [.2, .4]}}
    preview = af_overlay.PreviewFrame(source, 'synthetic-crop-and-frame', (1000, 500), True)
    projected = af_overlay.build_overlay_geometry(record, preview, (20, 40, 420, 240))
    assert projected.status == 'validated'
    canvas = Mock()
    draw_af_projection(canvas, projected)
    assert canvas.create_rectangle.call_args.args == pytest.approx((80, 120, 160, 200))
    assert canvas.create_line.call_args_list[0].args == pytest.approx((115, 160, 125, 160))
    assert canvas.create_line.call_args_list[1].args == pytest.approx((120, 155, 120, 165))


def jpeg_bundle(asset, image, *, metadata=None):
    """UI-state fixture only; unsealed mappings never pass real projection."""
    path = _af_jpeg_source(asset)
    stamp = _af_source_stamp(path)
    return {
        'request_path': path,
        'metadata': metadata or parsed_record(),
        'clean_image': image,
        'provenance': {
            'source': {'path': str(Path(path).resolve()), 'sha256': 'f' * 64,
                       'stat_identity': dict(zip(('size', 'mtime_ns', 'ctime_ns', 'device', 'inode'), stamp))},
            'binding': {'source_unchanged': True},
        },
        'geometry': {'status': 'not_validated'},
        'status_text': 'AF区域诊断：不是确认焦点，也不代表人物或清晰度',
    }


def fake_photoimage(monkeypatch):
    from ai_cull_assistant import group_editor
    from threading import get_ident
    creations = []

    def create(image, master):
        size = image.size
        result = SimpleNamespace(width=lambda: size[0], height=lambda: size[1],
                                 pixel=image.getpixel((0, 0)), thread=get_ident())
        creations.append(result)
        return result

    monkeypatch.setattr(group_editor.ImageTk, 'PhotoImage', create)
    return creations


def diagnostic_geometry(*, center=(100., 150.), box=(80., 120., 130., 180.),
                        clip_rect=(50., 100., 450., 300.), status='diagnostic'):
    return af_jpeg_preview.JpegDiagnosticGeometry(status, 'SYNTHETIC-UI-ONLY', center, box, clip_rect)


def test_raw_only_requests_own_source_without_sibling_jpeg(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.RW2')
    asset.raw_path, asset.jpg_path = asset.primary_path, None
    editor, loader, factory = editor_for([asset], monkeypatch)
    enable(editor, asset)
    assert _af_jpeg_source(asset) == str(asset.primary_path)
    assert '读取同源预览' in editor.af_status_var.get()
    assert loader.requests
    factory.assert_called_once()
    assert editor._af_request_path == str(asset.primary_path)


def test_unpaired_primary_jpeg_is_selected_without_searching_siblings(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.JPEG')
    asset.jpg_path = None
    editor, loader, _ = editor_for([asset], monkeypatch)
    token = enable(editor, asset)
    assert loader.requests == [(token, str(asset.primary_path))]


def test_missing_paired_jpeg_never_falls_back_to_raw_or_primary_jpeg(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    asset.jpg_path = tmp_path / 'missing-pair.jpg'
    editor, loader, _ = editor_for([asset], monkeypatch)
    token = enable(editor, asset)
    assert loader.requests == [(token, str(asset.jpg_path))]
    assert editor._af_request_path != str(asset.primary_path)


def test_changing_primary_raw_does_not_change_explicit_paired_jpeg_request(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'pair.jpg')
    asset.primary_path = tmp_path / 'first.RW2'
    editor, loader, _ = editor_for([asset], monkeypatch)
    token = enable(editor, asset)
    asset.primary_path = tmp_path / 'second.RW2'
    editor._refresh_inspector()
    assert editor._af_request_token == token
    assert len(loader.requests) == 1


def test_dedicated_pixels_resize_on_main_thread_and_cache_only_current_size(tmp_path, monkeypatch):
    from threading import get_ident
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    made = fake_photoimage(monkeypatch)
    token = enable(editor, asset)
    clean = Image.new('RGB', (400, 200), 'red')
    bundle = jpeg_bundle(asset, clean)
    loader.results = [(token, bundle)]
    poll(editor)
    first = editor._af_thumbnail_photo(asset, (200, 100))
    assert first.pixel == (255, 0, 0)
    assert first.thread == get_ident()
    assert (first.width(), first.height()) == (200, 100)
    assert editor._af_thumbnail_photo(asset, (200, 100)) is first
    second = editor._af_thumbnail_photo(asset, (120, 120))
    assert second is not first
    assert (second.width(), second.height()) == (120, 60)
    assert len(made) == 2
    assert len(loader.requests) == 1
    assert clean.size == (400, 200)
    editor.af_overlay_var.set(False)
    editor._toggle_af_overlay()
    assert editor._af_display_photo is None
    assert editor._af_bundle is None
    with pytest.raises(ValueError):
        clean.getpixel((0, 0))


def test_diagnostic_can_draw_only_on_exact_bundle_photo_not_same_sized_old_thumb(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    fake_photoimage(monkeypatch)
    token = enable(editor, asset)
    clean = Image.new('RGB', (400, 200), 'red')
    loader.results = [(token, jpeg_bundle(asset, clean))]
    poll(editor)
    dedicated = editor._af_thumbnail_photo(asset, (400, 200))
    project = Mock(return_value=diagnostic_geometry())
    monkeypatch.setattr(af_jpeg_preview, 'project_jpeg_diagnostic', project)
    old = SimpleNamespace(width=lambda: 400, height=lambda: 200)
    assert editor._draw_af_overlay(asset, old, (50, 100, 450, 300), 'detail:A') is False
    project.assert_not_called()
    assert editor._draw_af_overlay(asset, dedicated, (50, 100, 450, 300), 'detail:A') is True
    project.assert_called_once_with(editor._af_bundle, (50, 100, 450, 300))
    assert editor.detail_canvas.create_rectangle.call_args.kwargs['tags'] == ('detail:A', 'af-diagnostic')
    editor._invalidate_af_request()


def test_plain_unsealed_diagnostic_bundle_can_never_draw(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    fake_photoimage(monkeypatch)
    token = enable(editor, asset)
    clean = Image.new('RGB', (400, 200), 'red')
    bundle = jpeg_bundle(asset, clean)
    bundle['geometry'] = {'status': 'diagnostic', 'center': (.5, .5)}
    loader.results = [(token, bundle)]
    poll(editor)
    dedicated = editor._af_thumbnail_photo(asset, (400, 200))
    assert dedicated is not None
    assert editor._draw_af_overlay(asset, dedicated, (50, 100, 450, 300), 'detail:A') is False
    editor.detail_canvas.create_line.assert_not_called()
    editor._invalidate_af_request()


@pytest.mark.parametrize('vendor,label', [(None, 'AF区域（非确认焦点）'), ('sony', 'AF回放位置（非确认焦点）'), ('canon', 'AF记录点（非清晰度证明）'), ('nikon', 'AF记录区域（非清晰度证明）')])
def test_selected_card_uses_dedicated_pixels_other_cards_and_off_use_originals(tmp_path, monkeypatch, vendor, label):
    first = photo(tmp_path / 'A.jpg')
    second = photo(tmp_path / 'B.jpg')
    editor, loader, _ = editor_for([first, second], monkeypatch)
    fake_photoimage(monkeypatch)
    token = enable(editor, first)
    clean = Image.new('RGB', (400, 200), 'red')
    bundle = jpeg_bundle(first, clean)
    bundle['metadata'].setdefault('diagnostics', {})['camera'] = {'vendor': vendor}
    loader.results = [(token, bundle)]
    poll(editor)
    original = SimpleNamespace(width=lambda: 200, height=lambda: 100, pixel=(0, 0, 255))
    editor._cached_photo = Mock(return_value=original)
    editor._detail_images = []
    editor._detail_requested_keys = editor._row_requested_keys = set()
    editor._cancel_stale_thumbnail_requests = Mock()
    editor.detail_status_var = Value()
    editor.filter_count_var = Value()
    editor.detail_canvas.winfo_width.return_value = 700
    editor.detail_canvas.winfo_height.return_value = 400
    editor.detail_canvas.canvasy.return_value = 0
    editor._fonts = {'small': SimpleNamespace(metrics=lambda _: 12, measure=lambda value: len(value) * 7)}
    projection = Mock(side_effect=lambda bundle, rect: diagnostic_geometry(
        center=(rect[0] + 20, rect[1] + 20), box=None, clip_rect=rect))
    monkeypatch.setattr(af_jpeg_preview, 'project_jpeg_diagnostic', projection)
    before = deepcopy([asdict(first), asdict(second)])
    GroupEditor._redraw_detail(editor)
    image_calls = editor.detail_canvas.create_image.call_args_list
    assert image_calls[0].kwargs['image'].pixel == (255, 0, 0)
    assert image_calls[1].kwargs['image'] is original
    assert editor._cached_photo.call_count == 1
    assert editor._cached_photo.call_args.args[0] is second
    assert any(label in call.kwargs.get('text', '')
               for call in editor.detail_canvas.create_text.call_args_list)
    assert projection.call_count == 1
    editor.af_overlay_var.set(False)
    editor._toggle_af_overlay()
    editor.detail_canvas.reset_mock()
    editor._cached_photo.reset_mock()
    GroupEditor._redraw_detail(editor)
    assert all(call.kwargs['image'] is original for call in editor.detail_canvas.create_image.call_args_list)
    assert editor._cached_photo.call_count == 2
    assert projection.call_count == 1
    assert [asdict(first), asdict(second)] == before
    editor.on_change.assert_not_called()
    editor.on_select_subject.assert_not_called()


@pytest.mark.parametrize('mutation', ['request_path', 'stat', 'source_unchanged'])
def test_unbound_clean_image_is_closed_and_never_replaces_old_thumbnail(tmp_path, monkeypatch, mutation):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    token = enable(editor, asset)
    image = Image.new('RGB', (50, 30), 'red')
    bundle = jpeg_bundle(asset, image)
    if mutation == 'request_path':
        bundle['request_path'] = str(tmp_path / 'other.jpg')
    elif mutation == 'stat':
        bundle['provenance']['source']['stat_identity']['size'] += 1
    else:
        bundle['provenance']['binding']['source_unchanged'] = False
    loader.results = [(token, bundle)]
    poll(editor)
    assert '绑定失效' in editor.af_status_var.get()
    assert editor._af_thumbnail_photo(asset, (50, 30)) is None
    with pytest.raises(ValueError):
        image.getpixel((0, 0))


def test_stale_popped_bundle_pixels_are_closed_while_current_pixels_remain(tmp_path, monkeypatch):
    first = photo(tmp_path / 'A.jpg')
    second = photo(tmp_path / 'B.jpg')
    editor, loader, _ = editor_for([first, second], monkeypatch)
    old = enable(editor, first)
    stale = Image.new('RGB', (50, 30), 'red')
    stale_bundle = jpeg_bundle(first, stale)
    editor._select_asset(second.stem, second)
    current = editor._af_request_token
    live = Image.new('RGB', (50, 30), 'blue')
    loader.results = [(old, stale_bundle), (current, jpeg_bundle(second, live))]
    poll(editor)
    with pytest.raises(ValueError):
        stale.getpixel((0, 0))
    assert live.getpixel((0, 0)) == (0, 0, 255)
    editor._invalidate_af_request()


@pytest.mark.parametrize('event', ['selection', 'unmap', 'destroy', 'toggle_off'])
def test_displayed_clean_pixels_closed_at_every_lifecycle_boundary(tmp_path, monkeypatch, event):
    from ai_cull_assistant.ui_page import WorkspacePage
    first = photo(tmp_path / 'A.jpg')
    second = photo(tmp_path / 'B.jpg')
    editor, loader, _ = editor_for([first, second], monkeypatch)
    token = enable(editor, first)
    clean = Image.new('RGB', (40, 20), 'red')
    loader.results = [(token, jpeg_bundle(first, clean))]
    poll(editor)
    if event == 'selection':
        editor._select_asset(second.stem, second)
    elif event == 'unmap':
        editor._af_page_unmapped(SimpleNamespace(widget=editor))
    elif event == 'destroy':
        monkeypatch.setattr(WorkspacePage, 'destroy', Mock())
        editor.destroy()
    else:
        editor.af_overlay_var.set(False)
        editor._toggle_af_overlay()
    assert editor._af_bundle is None
    with pytest.raises(ValueError):
        clean.getpixel((0, 0))


def test_image_creation_failure_retains_regular_photo_and_never_draws_on_it(tmp_path, monkeypatch):
    from ai_cull_assistant import group_editor
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    token = enable(editor, asset)
    image = Image.new('RGB', (50, 30), 'red')
    loader.results = [(token, jpeg_bundle(asset, image))]
    poll(editor)
    monkeypatch.setattr(group_editor.ImageTk, 'PhotoImage', Mock(side_effect=RuntimeError('synthetic Tk failure')))
    assert editor._af_thumbnail_photo(asset, (50, 30)) is None
    assert '显示失败' in editor.af_status_var.get()
    assert editor._draw_af_overlay(asset, object(), (0, 0, 50, 30), 'detail:A') is False
    editor._invalidate_af_request()


def test_diagnostic_renderer_never_accepts_validated_or_unbound_geometry():
    canvas = Mock()
    for candidate in (None, diagnostic_geometry(status='validated'), diagnostic_geometry(status='hidden'),
                      diagnostic_geometry(center=(10**1000, 150.)),
                      diagnostic_geometry(clip_rect=None), diagnostic_geometry(box=(40, 120, 130, 180))):
        assert draw_af_diagnostic(canvas, candidate) == ()
    canvas.create_rectangle.assert_not_called()
    canvas.create_line.assert_not_called()


def test_diagnostic_renderer_clips_cross_and_preserves_separate_gate():
    canvas = Mock()
    marker = diagnostic_geometry(center=(10, 20), box=None, clip_rect=(10, 20, 12, 22))
    assert len(draw_af_diagnostic(canvas, marker, tags=('detail:exact',))) == 2
    assert [call.args for call in canvas.create_line.call_args_list] == [(10, 20, 12, 20), (10, 20, 10, 22)]
    assert all(call.kwargs['tags'] == ('detail:exact', 'af-diagnostic')
               for call in canvas.create_line.call_args_list)
    stronger = Mock()
    assert draw_af_projection(stronger, marker) == ()
    stronger.create_line.assert_not_called()


def test_drawn_canvas_marks_cannot_revive_when_hidden_page_maps_with_changed_source(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.jpg')
    editor, loader, _ = editor_for([asset], monkeypatch)
    fake_photoimage(monkeypatch)
    old = enable(editor, asset)
    clean = Image.new('RGB', (400, 200), 'red')
    loader.results = [(old, jpeg_bundle(asset, clean))]
    poll(editor)
    dedicated = editor._af_thumbnail_photo(asset, (400, 200))
    monkeypatch.setattr(af_jpeg_preview, 'project_jpeg_diagnostic',
                        Mock(return_value=diagnostic_geometry()))
    assert editor._draw_af_overlay(asset, dedicated, (50, 100, 450, 300), 'detail:A')
    editor.detail_canvas.delete.reset_mock()
    editor._schedule_detail_redraw.reset_mock()
    editor._af_page_unmapped(SimpleNamespace(widget=editor))
    editor.detail_canvas.delete.assert_called_with('af-diagnostic')
    with pytest.raises(ValueError):
        clean.getpixel((0, 0))
    asset.primary_path.write_bytes(b'changed while page was hidden')
    editor._af_page_mapped(SimpleNamespace(widget=editor))
    assert editor._af_request_token != old
    editor._schedule_detail_redraw.assert_called()
    late = Image.new('RGB', (50, 30), 'green')
    loader.results = [(old, jpeg_bundle(asset, late))]
    poll(editor)
    assert editor._af_bundle is None
    with pytest.raises(ValueError):
        late.getpixel((0, 0))


def test_real_jpeg_decode_and_sealed_synthetic_metadata_reach_diagnostic_canvas(tmp_path, monkeypatch):
    """Actual JPEG pixels + mocked camera tags; this is not a real-camera test."""
    from ai_cull_assistant import af_diagnostic, af_metadata
    asset = photo(tmp_path / 'orientation8.jpg')
    raw_image = Image.new('RGB', (6000, 4000), (180, 40, 20))
    try:
        exif = Image.Exif()
        exif[274] = 8
        raw_image.save(asset.primary_path, format='JPEG', exif=exif)
    finally:
        raw_image.close()
    parser = tmp_path / 'synthetic-exiftool'
    parser.write_bytes(b'not executable; test identity only')
    raw = {
        'IFD0:Make': 'Panasonic', 'IFD0:Model': 'DC-S5M2', 'IFD0:Orientation': 8,
        'Panasonic:FocusMode': 6, 'Panasonic:AFAreaMode': '16 0',
        'Panasonic:AFPointPosition': [.25, .6], 'Panasonic:AFAreaSize': [.1, .2],
        'Panasonic:PanasonicImageWidth': 6000, 'Panasonic:PanasonicImageHeight': 4000,
        'ExifIFD:ExifImageWidth': 6000, 'ExifIFD:ExifImageHeight': 4000,
    }

    def metadata_reader(path, **kwargs):
        return {
            'status': 'not_validated', 'reason': 'real_camera_coordinate_mapping_not_validated',
            'source': {**af_metadata._source_identity(Path(path)), 'unchanged_during_read': True},
            'parser': {'name': 'ExifTool', 'path': str(parser.resolve()), 'version': '13.59',
                       'adapter_version': af_metadata.ADAPTER_VERSION,
                       'stat_identity': af_metadata._stat_identity(parser.stat())},
            'raw_tags': raw, 'diagnostics': af_metadata._diagnostics(raw),
            'usable_for_subject_selection': False, 'usable_for_keep_reject': False,
        }

    monkeypatch.setattr(af_metadata, 'read_af_metadata', metadata_reader)
    image, report = af_diagnostic.prepare_af_diagnostic(asset.primary_path, max_edge=192)
    bundle = af_jpeg_preview._bind_prepared(str(asset.primary_path), image, report)
    assert bundle['clean_image'].size == (128, 192)
    assert bundle['metadata']['status'] == 'not_validated'
    assert report['preview']['exif_transpose_count'] == 1
    editor, loader, _ = editor_for([asset], monkeypatch)
    fake_photoimage(monkeypatch)
    token = enable(editor, asset)
    loader.results = [(token, bundle)]
    poll(editor)
    dedicated = editor._af_thumbnail_photo(asset, (100, 150))
    assert (dedicated.width(), dedicated.height()) == (100, 150)
    assert editor._draw_af_overlay(asset, dedicated, (20, 40, 120, 190), 'detail:exact') is True
    box = editor.detail_canvas.create_rectangle.call_args
    assert box.args == pytest.approx((70, 145, 90, 160))
    assert box.kwargs['tags'] == ('detail:exact', 'af-diagnostic')
    assert editor._af_record['usable_for_subject_selection'] is False
    assert editor._af_record['usable_for_keep_reject'] is False
    editor._invalidate_af_request()
    with pytest.raises(ValueError):
        image.getpixel((0, 0))


def test_raw_preview_without_af_mapping_can_display_without_quality_mutation(tmp_path, monkeypatch):
    asset = photo(tmp_path / 'A.ARW')
    asset.raw_path, asset.jpg_path = asset.primary_path, None
    before = deepcopy(asdict(asset))
    editor, loader, _ = editor_for([asset], monkeypatch)
    fake_photoimage(monkeypatch)
    token = enable(editor, asset)
    clean = Image.new('RGB', (320, 214), 'blue')
    bundle = jpeg_bundle(asset, clean)
    bundle['status_text'] = 'RAW内嵌预览可查看；AF坐标映射未确认，暂不绘制'
    bundle['status'] = 'viewable'
    loader.results = [(token, bundle)]
    poll(editor)
    dedicated = editor._af_thumbnail_photo(asset, (320, 214))
    assert dedicated is not None and dedicated.pixel == (0, 0, 255)
    assert 'AF坐标映射未确认' in editor.af_status_var.get()
    assert editor._draw_af_overlay(asset, dedicated, (10, 20, 330, 234), 'detail:A') is False
    assert asdict(asset) == before
    editor._invalidate_af_request()
    with pytest.raises(ValueError):
        clean.getpixel((0, 0))


def test_all_record_roles_draw_without_picking_one():
    from ai_cull_assistant.af_jpeg_preview import JpegDiagnosticGeometry as G
    clip = (0., 0., 300., 200.)
    roles = ('selected', 'camera_reported_in_focus', 'selected_and_camera_reported_in_focus')
    markers = tuple(G('diagnostic', 'synthetic', (50.+i*80, 100.), (40.+i*80,90.,60.+i*80,110.),
                      clip, role=role, index=i) for i, role in enumerate(roles))
    geometry = G('diagnostic', 'synthetic', clip_rect=clip, markers=markers)
    canvas = Mock()
    assert len(draw_af_diagnostic(canvas, geometry)) == 9
    assert [call.kwargs['dash'] for call in canvas.create_rectangle.call_args_list] == [(6,3),(2,2),(6,2,2,2)]
    assert canvas.create_line.call_count == 6


def test_collection_is_all_or_none_before_canvas_writes():
    from ai_cull_assistant.af_jpeg_preview import JpegDiagnosticGeometry as G
    clip = (0., 0., 300., 200.)
    good = G('diagnostic', 'synthetic', (50.,100.), None, clip, role='selected', index=0)
    bad = G('diagnostic', 'synthetic', (500.,100.), None, clip, role='camera_reported_in_focus', index=1)
    canvas = Mock()
    assert draw_af_diagnostic(canvas, G('diagnostic','synthetic',clip_rect=clip,markers=(good,bad))) == ()
    canvas.create_line.assert_not_called()
