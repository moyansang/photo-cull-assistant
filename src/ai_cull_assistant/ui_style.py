"""Photography workbench theme, implemented with ttkbootstrap on Tkinter."""
from tkinter import ttk, font
import tkinter as tk
import ttkbootstrap as bootstrap

COLORS = dict(bg='#F4F6F8', surface='#FFFFFF', text='#303640', muted='#626B77',
              border='#CFD7E1', accent='#466B97', selection='#E5EFFB',
              hover='#EDF2F8', disabled='#758293', photo='#303030',
              photo_text='#ECEEEF', danger='#A82A30')


def _root(widget):
    while widget.master is not None:
        widget = widget.master
    return widget


def initialize(widget):
    root = _root(widget)
    if hasattr(root, '_cull_fonts'):
        return root._cull_style
    style = bootstrap.Style(theme='bootstrap-light')
    theme = bootstrap.Theme(name='photo-cull', primary=COLORS['accent'],
                            secondary='#617084', neutral='#8794A5',
                            success='#267A58', info='#347DAD', warning='#B27B24', danger=COLORS['danger'],
                            light=dict(background=COLORS['bg'], foreground=COLORS['text']))
    for definition in theme.to_definitions():
        style.register_theme(definition)
    style.theme_use('photo-cull-light')
    root._cull_style = style
    # Plain Tk roots are also used by isolated dialogs/tests. Release the theme
    # singleton only when its own interpreter closes, never on child destruction.
    def release(event):
        if event.widget is root and bootstrap.Style.instance is style:
            bootstrap.Style.instance = None
            from ttkbootstrap.utils.fonts import Fonts
            Fonts.reset()
    root.bind('<Destroy>', release, add='+')
    family = 'Microsoft YaHei UI' if 'Microsoft YaHei UI' in font.families(root) else font.nametofont('TkDefaultFont', root=root).actual('family')
    root._cull_fonts = {name: font.Font(root=root, family=family, size=size, weight=weight)
                       for name, size, weight in [('body', 10, 'normal'), ('small', 9, 'normal'),
                                                ('shortcut', 8, 'normal'), ('heading', 11, 'bold')]}
    body = root._cull_fonts['body']
    style.configure('.', font=body, foreground=COLORS['text'])
    style.configure('TLabel', foreground=COLORS['text'])
    style.configure('TEntry', fieldbackground=COLORS['surface'])
    style.configure('TNotebook.Tab', foreground=COLORS['text'])
    style.configure('Workbench.Treeview', rowheight=round(root.winfo_fpixels('25p')), font=body)
    style.map('Workbench.Treeview', background=[('selected', COLORS['selection'])], foreground=[('selected', COLORS['text'])])
    style.configure('TButton', font=body, padding=(12, 7))
    style.configure('TEntry', font=body, padding=7)
    style.configure('TCombobox', font=body, padding=6)
    style.configure('TSpinbox', font=body, padding=6)
    style.configure('TCheckbutton', font=body, padding=(0, 4))
    style.configure('TNotebook.Tab', font=body, padding=(14, 8))
    style.configure('Muted.TLabel', foreground=COLORS['muted'], font=root._cull_fonts['small'])
    style.configure('Heading.TLabel', font=root._cull_fonts['heading'])
    style.configure('Assist.Treeview', rowheight=max(54, round(root.winfo_fpixels('34p'))), font=body)
    style.map('Assist.Treeview', background=[('selected', COLORS['selection'])], foreground=[('selected', COLORS['text'])])
    return style


def apply_page(window):
    style = initialize(window)
    fonts = _root(window)._cull_fonts
    window.configure(background=COLORS['bg'])
    def visit(parent):
        for child in parent.winfo_children():
            if isinstance(child, tk.Toplevel):
                continue
            if isinstance(child, ttk.Button):
                bootstrap.apply_bootstyle(child, 'secondary-outline')
            elif isinstance(child, ttk.Entry) or isinstance(child, (ttk.Combobox, ttk.Spinbox)):
                child.configure(font=fonts['body'])
            elif isinstance(child, ttk.Treeview):
                child.configure(style='Assist.Treeview' if getattr(window, '_page_id', '') == 'assist' else 'Workbench.Treeview')
            elif isinstance(child, tk.Text):
                child.configure(background=COLORS['surface'], foreground=COLORS['text'], font=fonts['body'],
                                selectbackground=COLORS['selection'], selectforeground=COLORS['text'],
                                relief='flat', borderwidth=0, highlightthickness=1,
                                highlightbackground=COLORS['border'], highlightcolor=COLORS['accent'], padx=12, pady=10)
            visit(child)
    visit(window)
    style.configure('secondary.Outline.TButton', foreground=COLORS['text'], bordercolor=COLORS['border'])
    style_page_chrome(window)
    style.configure('secondary.Link.TButton', font=fonts['small'], padding=(5, 4))




def set_button_style(button, role):
    bootstrap.apply_bootstyle(button, role)
    if role == 'danger-link':
        initialize(button).configure('danger.Link.TButton', foreground=COLORS['danger'])


def style_page_chrome(window):
    bar = getattr(window, '_page_chrome', None)
    if bar is None:
        return
    style = initialize(window)
    fonts = _root(window)._cull_fonts
    bar.configure(padding=(16, 8), style='Toolbar.TFrame')
    style.configure('Toolbar.TFrame', background=COLORS['surface'])
    style.configure('Nav.TButton', font=fonts['body'], padding=(14, 7),
                    foreground=COLORS['muted'], background=COLORS['surface'],
                    borderwidth=0, relief='flat', bordercolor=COLORS['surface'],
                    focuscolor=COLORS['accent'])
    style.map('Nav.TButton', background=[('active', COLORS['hover'])],
              foreground=[('disabled', COLORS['disabled']), ('active', COLORS['text'])])
    style.configure('Active.Nav.TButton', foreground=COLORS['accent'], background=COLORS['selection'])
    style.map('Active.Nav.TButton', background=[('active', COLORS['selection'])])
    for action, button in bar.buttons.items():
        button.configure(style='Active.Nav.TButton' if action == window._page_id else 'Nav.TButton')
    bar.help_button.configure(style='Nav.TButton')
