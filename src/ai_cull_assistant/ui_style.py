"""Scoped neutral desktop styles; no workflow or persisted settings changes."""
from tkinter import ttk, font
import tkinter as tk

COLORS = dict(bg='#F3F4F5', surface='#FFFFFF', text='#202428', muted='#5B636B',
              border='#B7BEC5', accent='#245DB5', selection='#E5EDFA',
              hover='#E9EDF2', disabled='#737B83', photo='#303030',
              photo_text='#ECEEEF', danger='#A82A30')


def _root(widget):
    while widget.master is not None:
        widget = widget.master
    return widget


def initialize(widget):
    root = _root(widget)
    if hasattr(root, '_cull_fonts'):
        return
    family = 'Microsoft YaHei UI' if 'Microsoft YaHei UI' in font.families(root) else font.nametofont('TkDefaultFont', root=root).actual('family')
    root._cull_fonts = {name: font.Font(root=root, family=family, size=size, weight=weight)
                        for name, size, weight in [('body', 10, 'normal'), ('small', 9, 'normal'),
                                                 ('shortcut', 8, 'normal'), ('heading', 11, 'bold')]}
    style = ttk.Style(root)
    # Copy only the required Clam elements; keep Vista and unrelated pages intact.
    old_theme = style.theme_use()
    kinds = ['TButton', 'TEntry', 'TCombobox', 'TSpinbox', 'TCheckbutton',
             'TNotebook', 'TNotebook.Tab', 'Treeview', 'Treeview.Heading',
             'Horizontal.TProgressbar']
    try:
        style.theme_use('clam')
        layouts = {kind: style.layout(kind) for kind in kinds}
    finally:
        style.theme_use(old_theme)
    copied = set()
    def clone(layout):
        result = []
        for element, options in layout:
            name = 'Cull.' + element
            if name not in copied:
                style.element_create(name, 'from', 'clam', element)
                copied.add(name)
            options = dict(options)
            if 'children' in options:
                options['children'] = clone(options['children'])
            result.append((name, options))
        return result
    for kind, layout in layouts.items():
        style.layout('Cull.' + kind, clone(layout))
    c = COLORS
    for kind in kinds + ['TFrame', 'TLabel', 'TLabelframe', 'TLabelframe.Label']:
        style.configure('Cull.' + kind, background=c['bg'], foreground=c['text'],
                        font=root._cull_fonts['body'], bordercolor=c['border'],
                        lightcolor=c['bg'], darkcolor=c['bg'])
    style.configure('Cull.TButton', padding=(10, 5), borderwidth=1, relief='raised', width=0, lightcolor=c['border'], darkcolor=c['border'])
    style.map('Cull.TButton', background=[('disabled', c['bg']), ('pressed', c['selection']), ('active', c['hover'])],
              foreground=[('disabled', c['disabled'])], bordercolor=[('focus', c['accent'])])
    style.configure('Primary.Cull.TButton', background=c['accent'], foreground='white', bordercolor=c['accent'])
    style.map('Primary.Cull.TButton', background=[('disabled', '#E1E4E7'), ('pressed', '#194788'), ('active', '#1C50A0')],
              foreground=[('disabled', c['disabled']), ('!disabled', 'white')])
    style.configure('Danger.Cull.TButton', foreground=c['danger'])
    style.configure('Nav.Cull.TButton', padding=(8, 3), borderwidth=0, relief='flat')
    for kind in ['TEntry', 'TCombobox', 'TSpinbox']:
        name = 'Cull.' + kind
        style.configure(name, fieldbackground=c['surface'], background=c['surface'], padding=5, arrowsize=12)
        style.map(name, fieldbackground=[('disabled', '#E8EAED'), ('readonly', c['surface'])],
                  foreground=[('disabled', c['disabled'])], bordercolor=[('focus', c['accent'])],
                  selectbackground=[('!disabled', c['accent'])], selectforeground=[('!disabled', 'white')])
    style.configure('Cull.TCheckbutton', padding=(0, 4), indicatorsize=14, indicatormargin=6)
    style.map('Cull.TCheckbutton', foreground=[('disabled', c['disabled'])],
              indicatorbackground=[('selected', c['accent']), ('!selected', c['surface'])],
              indicatorforeground=[('selected', 'white')])
    style.configure('Cull.TNotebook', borderwidth=0, tabmargins=(0, 0, 0, 8))
    style.configure('Cull.TNotebook.Tab', padding=(16, 7))
    style.map('Cull.TNotebook.Tab', background=[('selected', c['surface']), ('!selected', c['bg'])],
              foreground=[('selected', c['accent'])])
    style.configure('Cull.Treeview', background=c['surface'], fieldbackground=c['surface'],
                    rowheight=round(root.winfo_fpixels('26p')), borderwidth=1)
    style.map('Cull.Treeview', background=[('selected', c['selection'])], foreground=[('selected', c['text'])])
    style.configure('Cull.Treeview.Heading', padding=6, font=root._cull_fonts['small'], relief='flat')
    style.configure('Assist.Cull.Treeview', rowheight=max(54, round(root.winfo_fpixels('34p'))))
    style.configure('Cull.Horizontal.TProgressbar', background=c['accent'], troughcolor='#E1E4E7', borderwidth=0)
    style.configure('Muted.Cull.TLabel', foreground=c['muted'], font=root._cull_fonts['small'])
    style.configure('Heading.Cull.TLabel', font=root._cull_fonts['heading'])


def apply_page(window):
    """Style existing controls without replacing their callbacks or variables."""
    initialize(window)
    fonts = _root(window)._cull_fonts
    window.configure(background=COLORS['bg'])
    def visit(parent):
        for child in parent.winfo_children():
            if isinstance(child, tk.Toplevel):
                continue
            kind = child.winfo_class()
            if isinstance(child, ttk.Widget):
                if kind in ('TFrame', 'TLabel', 'TLabelframe', 'TButton', 'TEntry', 'TCombobox',
                            'TSpinbox', 'TCheckbutton', 'TNotebook', 'Treeview'):
                    child.configure(style=('Assist.Cull.Treeview' if kind == 'Treeview' else 'Cull.' + kind))
                if kind in ('TEntry', 'TCombobox', 'TSpinbox'):
                    child.configure(font=fonts['body'])
                if kind == 'TProgressbar':
                    child.configure(style='Cull.Horizontal.TProgressbar')
            elif isinstance(child, tk.Text):
                child.configure(background=COLORS['surface'], foreground=COLORS['text'], font=fonts['body'],
                                selectbackground=COLORS['selection'], selectforeground=COLORS['text'],
                                relief='flat', borderwidth=0, highlightthickness=1,
                                highlightbackground=COLORS['border'], highlightcolor=COLORS['accent'], padx=10, pady=8)
            visit(child)
    visit(window)
    bar = getattr(window, '_page_chrome', None)
    if bar:
        bar.configure(padding=(8, 6))
        for group in bar.winfo_children():
            for child in group.winfo_children():
                if isinstance(child, ttk.Button):
                    child.configure(style='Nav.Cull.TButton')
                elif isinstance(child, ttk.Label):
                    child.configure(style='Muted.Cull.TLabel', font=fonts['shortcut'])
