"""Tool page switches must not grow a window outside a small monitor."""
import tkinter as tk

import pytest

from ai_cull_assistant.app import App
from ai_cull_assistant.settings import save_values
from ai_cull_assistant import window_layout
from ai_cull_assistant import tool_window


@pytest.mark.parametrize('dpi', [96, 144, 192])
def test_tool_tabs_fit_small_work_area_and_forms_scroll(tmp_path, monkeypatch, dpi):
    original_init = tk.Tk.__init__
    def scaled_init(root, *args, **kwargs):
        original_init(root, *args, **kwargs)
        root._old_scaling = root.tk.call('tk', 'scaling')
        root.tk.call('tk', 'scaling', dpi / 72)
    monkeypatch.setattr(tk.Tk, '__init__', scaled_init)
    area = window_layout.WorkArea(0, 0, 900, 600)
    monkeypatch.setattr(window_layout, 'work_area_for', lambda _: area)
    monkeypatch.setattr(tool_window, 'work_area_for', lambda _: area)
    save_values(tmp_path, {'input': '', 'workspace': '', 'options': {'no_auto_updates': True}})
    app = App(settings_dir=tmp_path)
    try:
        app.update()
        window = app._open_tool_page('api')
        editor = app._api_config_window
        editor.key_var.set('local-unsaved-test-value')
        for page_id in ('api', 'help', 'lr', 'update', 'api'):
            window.show_page(page_id)
            app.update()
            assert window.winfo_width() <= area.width - 64
            assert window.winfo_height() <= area.height - 64
            if page_id == 'api':
                for tab in (editor.basic_tab, editor.advanced_tab):
                    editor.settings_notebook.select(tab)
                    app.update()
                    assert tab.canvas.winfo_height() >= 80
                    for button in (editor.save_button, editor.test_button, editor.close_button):
                        assert button.winfo_viewable()
                        assert button.winfo_width() >= button.winfo_reqwidth()
                        assert button.winfo_rooty() + button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
                    # The complete oversized form remains reachable, including
                    # the last advanced field, without resizing the dialog.
                    tab.canvas.yview_moveto(1)
                    tab.canvas.xview_moveto(1)
                    app.update()
                    assert tab.canvas.yview()[1] == 1.0
                    assert tab.canvas.xview()[1] == 1.0
        assert editor.key_var.get() == 'local-unsaved-test-value'
        assert not (tmp_path / 'ai-api-profiles.json').exists()
    finally:
        app.tk.call('tk', 'scaling', app._old_scaling)
        app.destroy()
