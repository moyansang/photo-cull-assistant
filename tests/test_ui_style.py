import tkinter as tk
from tkinter import ttk, font

from ai_cull_assistant.ui_style import apply_page


def test_bootstrap_theme_preserves_widget_behaviour():
    root = tk.Tk()
    root.withdraw()
    try:
        style = ttk.Style(root)
        default_font = font.nametofont('TkDefaultFont', root=root).actual()
        other = tk.Toplevel(root)
        other.withdraw()
        untouched = ttk.Button(other, text='其他页面')
        variable = tk.StringVar(root, '原值')
        entry = ttk.Entry(root, textvariable=variable)
        called = []
        button = ttk.Button(root, text='操作', command=lambda: called.append(variable.get()))
        entry.pack()
        button.pack()
        apply_page(root)
        apply_page(root)  # re-application must not duplicate Tcl elements/fonts
        variable.set('修改后')
        button.invoke()
        assert called == ['修改后']
        assert entry.get() == '修改后'
        assert untouched.cget('style') == ''
        assert style.theme_use() == 'photo-cull-light'
        assert font.nametofont('TkDefaultFont', root=root).actual() == default_font
        button.state(['disabled'])
        button.invoke()
        assert called == ['修改后']
    finally:
        root.destroy()
