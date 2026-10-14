"""Directory routing tests also run without a graphical display."""
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_cull_assistant.app import App


def opener(monkeypatch, workspace):
    app = SimpleNamespace(
        workspace_var=SimpleNamespace(get=lambda: str(workspace)),
        _sync_selected_workspace=Mock(return_value=True),
        # Opening an existing directory must not consult a cached project,
        # start processing, or enter the AI review page.
        _open_ai_review=Mock(side_effect=AssertionError('must not start review')),
        _start_processing=Mock(side_effect=AssertionError('must not start processing')),
        review_project=object(), scan_result=object(),
    )
    opened, notices = Mock(), Mock()
    monkeypatch.setattr(os, 'startfile', opened, raising=False)
    monkeypatch.setattr('ai_cull_assistant.app.messagebox.showinfo', notices)
    return app, opened, notices


@pytest.mark.parametrize('layout', ['structured', 'legacy', 'both'])
def test_open_results_uses_export_location_without_changing_files(tmp_path, monkeypatch, layout):
    workspace = tmp_path / '当前工作区'
    workspace.mkdir()
    if layout in ('structured', 'both'):
        (workspace / 'exports').mkdir()
        (workspace / 'exports/lightroom_results.json').write_text('{}', encoding='utf-8')
    if layout in ('legacy', 'both'):
        (workspace / 'lightroom_results.json').write_text('{}', encoding='utf-8')
    before = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    app, opened, notices = opener(monkeypatch, workspace)
    App._open_results_dir(app)
    opened.assert_called_once_with(workspace / 'exports' if layout == 'structured' else workspace)
    app._sync_selected_workspace.assert_called_once_with()
    notices.assert_not_called()
    app._open_ai_review.assert_not_called()
    app._start_processing.assert_not_called()
    assert before == {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}


def test_open_results_without_workspace_never_opens_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'exports').mkdir()
    (tmp_path / 'exports/lightroom_results.json').write_text('{}', encoding='utf-8')
    app, opened, notices = opener(monkeypatch, '  ')
    App._open_results_dir(app)
    opened.assert_not_called()
    assert '请先选择工作区' in notices.call_args.args[1]


@pytest.mark.parametrize('state', ['missing_workspace', 'missing_exports', 'file_instead_of_directory'])
def test_missing_result_directory_is_explained_without_creating_it(tmp_path, monkeypatch, state):
    workspace = tmp_path / 'workspace'
    if state != 'missing_workspace':
        workspace.mkdir()
    if state == 'file_instead_of_directory':
        (workspace / 'exports').write_text('keep', encoding='utf-8')
    before = list(tmp_path.rglob('*'))
    app, opened, notices = opener(monkeypatch, workspace)
    App._open_results_dir(app)
    opened.assert_not_called()
    assert '尚无选片结果文件' in notices.call_args.args[1]
    assert before == list(tmp_path.rglob('*'))


@pytest.mark.parametrize('state', ['empty_exports', 'structured_json_directory', 'legacy_json_directory'])
def test_result_must_be_a_file_before_opening_its_directory(tmp_path, monkeypatch, state):
    workspace = tmp_path / 'workspace'
    (workspace / 'exports').mkdir(parents=True)
    if state == 'structured_json_directory':
        (workspace / 'exports/lightroom_results.json').mkdir()
    elif state == 'legacy_json_directory':
        (workspace / 'lightroom_results.json').mkdir()
        # Keep the export resolver's legacy precedence: do not silently fall
        # through to another location when the selected legacy path is invalid.
        (workspace / 'exports/lightroom_results.json').write_text('{}', encoding='utf-8')
    before = sorted(p.relative_to(workspace) for p in workspace.rglob('*'))
    app, opened, notices = opener(monkeypatch, workspace)
    App._open_results_dir(app)
    opened.assert_not_called()
    notices.assert_called_once()
    assert '尚无选片结果文件' in notices.call_args.args[1]
    assert '请先导出到 LR' in notices.call_args.args[1]
    expected = workspace / ('lightroom_results.json' if state == 'legacy_json_directory'
                            else 'exports/lightroom_results.json')
    assert str(expected) in notices.call_args.args[1]
    app._open_ai_review.assert_not_called()
    app._start_processing.assert_not_called()
    assert sorted(p.relative_to(workspace) for p in workspace.rglob('*')) == before


def test_failed_workspace_sync_does_not_open_stale_results(tmp_path, monkeypatch):
    (tmp_path / 'exports').mkdir()
    (tmp_path / 'exports/lightroom_results.json').write_text('{}', encoding='utf-8')
    app, opened, notices = opener(monkeypatch, tmp_path)
    app._sync_selected_workspace.return_value = False
    App._open_results_dir(app)
    opened.assert_not_called()
    notices.assert_not_called()


def test_workspace_is_resolved_after_pending_selection_sync(tmp_path, monkeypatch):
    old, new = tmp_path / 'old', tmp_path / 'new'
    for workspace in (old, new):
        (workspace / 'exports').mkdir(parents=True)
        (workspace / 'exports/lightroom_results.json').write_text('{}', encoding='utf-8')
    app, opened, notices = opener(monkeypatch, old)
    def sync():
        app.workspace_var = SimpleNamespace(get=lambda: str(new))
        return True
    app._sync_selected_workspace.side_effect = sync
    App._open_results_dir(app)
    opened.assert_called_once_with(new / 'exports')
    notices.assert_not_called()


def test_os_open_failure_shows_current_result_directory(tmp_path, monkeypatch):
    (tmp_path / 'exports').mkdir()
    (tmp_path / 'exports/lightroom_results.json').write_text('{}', encoding='utf-8')
    app, opened, notices = opener(monkeypatch, tmp_path)
    opened.side_effect = OSError('no desktop shell')
    App._open_results_dir(app)
    assert notices.call_args.args == ('选片结果目录', str(tmp_path / 'exports'))
