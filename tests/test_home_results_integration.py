"""Exercise real pending workspace changes through the homepage button."""
import os
from unittest.mock import Mock

from ai_cull_assistant.app import App
from ai_cull_assistant.project_storage import workspace_for
from ai_cull_assistant.settings import save_values


def test_result_button_switches_workspace_and_repeated_clicks_are_read_only(tmp_path, monkeypatch):
    settings = tmp_path / 'settings'
    first, second = tmp_path / 'first-photos', tmp_path / 'second-photos'
    first.mkdir()
    second.mkdir()
    old = workspace_for(settings, first, preferred=tmp_path / 'old-workspace')
    new = workspace_for(settings, second, preferred=tmp_path / 'new-workspace')
    (old / 'exports').mkdir()
    (old / 'exports/lightroom_results.json').write_text('{}', encoding='utf-8')
    (new / 'lightroom_results.json').write_text('{}', encoding='utf-8')
    save_values(settings, {'input': str(first), 'workspace': str(old), 'options': {'no_auto_updates': True}})
    opened, notices = Mock(), Mock()
    monkeypatch.setattr(os, 'startfile', opened, raising=False)
    monkeypatch.setattr('ai_cull_assistant.app.messagebox.showinfo', notices)
    app = App(settings_dir=settings)
    try:
        app.update()
        app.results_button.invoke()
        opened.assert_called_once_with(old / 'exports')
        # Do not pump the delayed settings-save timer: the click must resolve
        # the newly typed source itself rather than open old cached output.
        app.input_var.set(str(second))
        app.results_button.invoke()
        assert opened.call_args.args == (new,)
        assert app.workspace_var.get() == str(new)
        before = {p.relative_to(new): p.read_bytes() for p in new.rglob('*') if p.is_file()}
        for _ in range(3):
            app.results_button.invoke()
        assert opened.call_count == 5
        assert all(call.args == (new,) for call in opened.call_args_list[1:])
        assert before == {p.relative_to(new): p.read_bytes() for p in new.rglob('*') if p.is_file()}
        assert not app._processing_busy and app._processing_job is None
        assert app._visible_page == 'home'
        notices.assert_not_called()
    finally:
        app._close()
