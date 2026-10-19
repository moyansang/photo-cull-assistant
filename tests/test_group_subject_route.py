"""Headless route checks: choosing a photo opens a draft, never confirms it."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_cull_assistant.app import App
from ai_cull_assistant.crop_dialog import CropDialog
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset


def asset(tmp_path, directory, name='same'):
    path = tmp_path / directory / (name + '.jpg')
    return PhotoAsset(name, path, path, None, path, datetime(2026, 1, 1), '.jpg',
                      preview_path=path)


def app_model(assets, page=None):
    pages = {'faces': page} if page is not None else {}
    app = SimpleNamespace(
        scan_result=SimpleNamespace(assets=assets), crop_settings=CropSettings(),
        _workspace_pages=pages, _page_host=object(),
        _processing_busy=False, _processing_job=None, _cleaning_evidence=False,
        updates=SimpleNamespace(busy=False), _review_busy=Mock(return_value=False),
        _sync_selected_workspace=Mock(return_value=True),
        _show_workspace_page=Mock(return_value=True), _save_crop_settings=Mock(),
        _start_processing=Mock(), _commit_detection_confidence=Mock(),
    )
    app._mount_workspace_page = Mock(side_effect=lambda name, value: pages.__setitem__(name, value))
    return app


@pytest.fixture
def route_ui(monkeypatch):
    notices = Mock()
    monkeypatch.setattr('ai_cull_assistant.app.messagebox.showinfo', notices)
    return notices


def test_path_lookup_distinguishes_duplicate_stems_and_rejects_ambiguity(tmp_path):
    first, second = asset(tmp_path, 'left'), asset(tmp_path, 'right')
    assert first.stem == second.stem
    assert CropDialog.asset_index([first, second], second) == 1
    assert CropDialog.asset_index([first, second], deepcopy(second)) == 1
    assert CropDialog.asset_index([first, second], asset(tmp_path, 'missing')) is None
    assert CropDialog.asset_index([first, second, deepcopy(second)], second) is None
    assert CropDialog.asset_index([first], None) is None
    assert CropDialog.asset_index([first], SimpleNamespace(primary_path=None)) is None


def test_initial_unknown_photo_rejected_before_creating_tk_window(tmp_path):
    first, missing = asset(tmp_path, 'first'), asset(tmp_path, 'missing')
    with pytest.raises(ValueError, match='无法唯一定位'):
        CropDialog(None, [first], CropSettings(), Mock(), initial_asset=missing)


def editor_model(assets):
    editor = object.__new__(CropDialog)
    editor.assets = assets
    editor.index = 0
    editor._closed = False
    editor.winfo_exists = lambda: True
    editor._crop_selected = True
    editor._active_face_key = 'current-person'
    editor._drag = ('crop',)
    editor.edits = {'draft': {'selected_faces': [[.1, .2, .3, .4]]}}
    editor.store_current = Mock()
    editor.load_current = Mock()
    editor.render = Mock()
    editor.on_save = Mock()
    return editor


def test_existing_editor_stores_current_before_jump_and_keeps_drafts(tmp_path):
    first, second = asset(tmp_path, 'first'), asset(tmp_path, 'second')
    editor = editor_model([first, second])
    original = deepcopy(editor.edits)
    events = []
    def store():
        events.append(('store', editor.index))
        editor.edits['current'] = {'scale_factor': 1.4}
    editor.store_current.side_effect = store
    editor.load_current.side_effect = lambda: events.append(('load', editor.index))
    editor.render.side_effect = lambda: events.append(('render', editor.index))
    assert editor.show_asset(second)
    assert events == [('store', 0), ('load', 1), ('render', 1)]
    assert editor.edits['draft'] == original['draft']
    assert editor.edits['current'] == {'scale_factor': 1.4}
    assert editor._active_face_key is None and editor._drag is None
    assert not editor._crop_selected
    editor.on_save.assert_not_called()
    assert not any('primary_subject_confirmation' in entry for entry in editor.edits.values())


@pytest.mark.parametrize('kind', ['missing', 'ambiguous', 'closed', 'destroyed'])
def test_failed_editor_jump_does_not_store_or_change_current_photo(tmp_path, kind):
    first, second = asset(tmp_path, 'first'), asset(tmp_path, 'second')
    editor = editor_model([first, second])
    target = second
    if kind == 'missing':
        target = asset(tmp_path, 'missing')
    elif kind == 'ambiguous':
        editor.assets.append(deepcopy(second))
    elif kind == 'closed':
        editor._closed = True
    else:
        editor.winfo_exists = lambda: False
    original = deepcopy(editor.edits)
    assert not editor.show_asset(target)
    assert editor.index == 0 and editor.edits == original
    editor.store_current.assert_not_called()
    editor.load_current.assert_not_called()
    editor.render.assert_not_called()
    editor.on_save.assert_not_called()


def test_repeat_same_photo_keeps_active_person_and_never_saves(tmp_path):
    photo = asset(tmp_path, 'first')
    editor = editor_model([photo])
    assert editor.show_asset(photo)
    assert editor.show_asset(photo)
    assert editor._active_face_key == 'current-person'
    assert editor.index == 0
    assert editor.store_current.call_count == 2
    editor.on_save.assert_not_called()


def test_route_reuses_existing_draft_before_showing_page(tmp_path, route_ui, monkeypatch):
    first, second = asset(tmp_path, 'left'), asset(tmp_path, 'right')
    page = editor_model([first, second])
    app = app_model([first, second], page)
    app._show_workspace_page.side_effect = lambda name: page.index == 1
    factory = Mock()
    factory.asset_index = CropDialog.asset_index
    monkeypatch.setattr('ai_cull_assistant.app.CropDialog', factory)
    assert App._open_group_subject(app, second)
    assert page.index == 1
    factory.assert_not_called()
    app._mount_workspace_page.assert_not_called()
    app._save_crop_settings.assert_not_called()
    app._start_processing.assert_not_called()
    app._commit_detection_confidence.assert_not_called()
    page.on_save.assert_not_called()
    route_ui.assert_not_called()


def test_route_creates_at_target_once_without_save_or_rescan(tmp_path, route_ui, monkeypatch):
    first, second = asset(tmp_path, 'left'), asset(tmp_path, 'right')
    page = SimpleNamespace(winfo_exists=lambda: True, show_asset=Mock(return_value=True))
    factory = Mock(return_value=page)
    factory.asset_index = CropDialog.asset_index
    monkeypatch.setattr('ai_cull_assistant.app.CropDialog', factory)
    app = app_model([first, second])
    original = deepcopy(app.crop_settings)
    assert App._open_group_subject(app, second)
    factory.assert_called_once_with(app._page_host, [first, second], app.crop_settings,
                                    app._save_crop_settings, embedded=True, initial_asset=second)
    app._mount_workspace_page.assert_called_once_with('faces', page)
    assert App._open_group_subject(app, second)
    assert factory.call_count == 1
    page.show_asset.assert_called_once_with(second)
    assert app.crop_settings == original
    app._save_crop_settings.assert_not_called()
    app._start_processing.assert_not_called()
    route_ui.assert_not_called()


@pytest.mark.parametrize('busy', ['processing', 'paused', 'review', 'updates', 'cleanup'])
def test_route_blocks_active_or_paused_work_without_sync_or_navigation(tmp_path, route_ui, busy):
    photo = asset(tmp_path, 'first')
    app = app_model([photo])
    if busy == 'processing':
        app._processing_busy = True
    elif busy == 'paused':
        app._processing_job = object()
    elif busy == 'review':
        app._review_busy.return_value = True
    elif busy == 'updates':
        app.updates.busy = True
    else:
        app._cleaning_evidence = True
    assert not App._open_group_subject(app, photo)
    app._sync_selected_workspace.assert_not_called()
    app._show_workspace_page.assert_not_called()
    app._mount_workspace_page.assert_not_called()
    app._save_crop_settings.assert_not_called()
    assert route_ui.call_count == 1


@pytest.mark.parametrize('kind', ['stale', 'ambiguous', 'landscape', 'sync_failure', 'workspace_change'])
def test_route_rejects_unreliable_or_inapplicable_target(tmp_path, route_ui, kind):
    photo = asset(tmp_path, 'first')
    app = app_model([photo])
    target = photo
    if kind == 'stale':
        target = deepcopy(photo)
    elif kind == 'ambiguous':
        app.scan_result.assets.append(deepcopy(photo))
    elif kind == 'landscape':
        photo.analysis_mode = 'landscape'
    elif kind == 'sync_failure':
        app._sync_selected_workspace.return_value = False
    else:
        def switch_workspace():
            app.scan_result = SimpleNamespace(assets=[asset(tmp_path, 'other')])
            return True
        app._sync_selected_workspace.side_effect = switch_workspace
    assert not App._open_group_subject(app, target)
    app._show_workspace_page.assert_not_called()
    app._mount_workspace_page.assert_not_called()
    app._save_crop_settings.assert_not_called()
    app._start_processing.assert_not_called()


def test_existing_draft_without_target_is_retained_without_empty_jump(tmp_path, route_ui):
    first, target = asset(tmp_path, 'first'), asset(tmp_path, 'target')
    page = editor_model([first])
    app = app_model([first, target], page)
    original = deepcopy(page.edits)
    assert not App._open_group_subject(app, target)
    assert app._workspace_pages['faces'] is page and page.edits == original
    assert page.index == 0
    page.store_current.assert_not_called()
    app._show_workspace_page.assert_not_called()
    app._mount_workspace_page.assert_not_called()
    app._save_crop_settings.assert_not_called()
    assert '草稿已保留' in route_ui.call_args.args[1]
