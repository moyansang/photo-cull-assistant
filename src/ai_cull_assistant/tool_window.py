"""One owned window for settings, updates, Lightroom and contextual help."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .ui_help import PAGE_HELP, install_control_help, install_page_chrome
from .ui_style import apply_page, style_page_chrome
from .version import VERSION, BUILD
from .window_layout import fit_window


class ToolWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.transient(app)
        self.app = app
        self.pages = {}
        self._tearing_down = False
        self._returning_home = False
        self._help_context = 'home'
        self._previous_grab = self.grab_current()
        if self._previous_grab is not None:
            self._previous_grab.grab_release()
        install_page_chrome(self, 'api')
        self.content = ttk.Frame(self)
        self.content.pack(fill='both', expand=True)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _e: self.close())
        self.bind('<Destroy>', self._on_destroy, add='+')

    def show_page(self, page_id, *, help_context=None):
        if page_id == 'help':
            if help_context and help_context != 'help':
                self._help_context = help_context
        if page_id not in self.pages:
            self.pages[page_id] = self._create_page(page_id)
        for page in self.pages.values():
            page.pack_forget()
        page = self.pages[page_id]
        page.pack(fill='both', expand=True)
        self._page_id = page_id
        if page_id == 'help':
            title, text = PAGE_HELP.get(self._help_context, PAGE_HELP['home'])
            self.help_heading.configure(text=title)
            self.help_text.configure(state='normal')
            self.help_text.delete('1.0', 'end')
            self.help_text.insert('1.0', text)
            self.help_text.configure(state='disabled')
            self.help_text.yview_moveto(0)
        self.title({'api': 'AI API 配置', 'update': '更新与版本',
                    'lr': 'Lightroom 插件', 'help': '使用帮助'}[page_id])
        style_page_chrome(self)
        self.update_idletasks()
        # Negotiate against the visible page only. Keep the same native window
        # and user position when switching, growing only when content needs it.
        minimum = (max(640, self.winfo_reqwidth()), max(310, self.winfo_reqheight()))
        if self.state() == 'withdrawn':
            fit_window(self, (max(740, minimum[0]), max(460, minimum[1])),
                       minimum_size=minimum, parent=self.app)
        else:
            self.minsize(*minimum)
            width, height = max(self.winfo_width(), minimum[0]), max(self.winfo_height(), minimum[1])
            if (width, height) != (self.winfo_width(), self.winfo_height()):
                self.geometry(f'{width}x{height}')
        self.lift()
        self.focus_set()

    def _create_page(self, page_id):
        if page_id == 'api':
            from .ai_api_dialog import ApiConfigDialog
            page = ApiConfigDialog(self.content, self.app.settings_dir,
                                   on_saved=self.app._api_profiles_saved,
                                   embedded=True, on_close=self.close)
            self.app._api_config_window = page
            return page
        page = tk.Frame(self.content)
        footer = ttk.Frame(page, padding=(18, 8, 18, 12))
        footer.pack(side='bottom', fill='x')
        ttk.Button(footer, text='关闭', command=self.close).pack(side='right')
        body = ttk.Frame(page, padding=18)
        body.pack(fill='both', expand=True)
        if page_id == 'update':
            ttk.Label(body, text=f'当前版本：v{VERSION} · 构建 {BUILD}').pack(anchor='w', pady=8)
            ttk.Checkbutton(body, text='不再自动检查更新', variable=self.app.no_updates_var,
                            command=self.app._save_preferences).pack(anchor='w', pady=12)
            ttk.Button(body, text='立即检查', command=self._check_update).pack(anchor='w', pady=12)
            ttk.Label(body, text='下载进度显示在主页面共用进度条。').pack(anchor='w')
        elif page_id == 'lr':
            ttk.Label(body, text='1. 在 Lightroom 增效工具管理器中添加插件。\n\n2. 从 AI 选片与导出页导出结果。\n\n3. 在 Lightroom 插件中导入结果文件。').pack(anchor='w', pady=12)
            ttk.Button(body, text='打开插件目录', command=self.app._open_lr_plugin).pack(anchor='w', pady=8)
        elif page_id == 'help':
            self.help_heading = ttk.Label(body, style='Heading.TLabel')
            self.help_heading.pack(anchor='w', pady=(0, 12))
            text_frame = ttk.Frame(body)
            text_frame.pack(fill='both', expand=True)
            self.help_text = tk.Text(text_frame, wrap='word', height=12, width=55)
            scroll = ttk.Scrollbar(text_frame, command=self.help_text.yview)
            self.help_text.configure(yscrollcommand=scroll.set)
            scroll.pack(side='right', fill='y')
            self.help_text.pack(side='left', fill='both', expand=True)
        apply_page(page)
        install_control_help(page, page_id)
        return page

    def _check_update(self):
        api = self.pages.get('api')
        if api is not None and api._is_busy():
            from tkinter import messagebox
            messagebox.showinfo('检查更新', '请等待 API 保存或连接测试结束后再检查更新。', parent=self)
            return
        self.close()
        self.app.updates.check(True)

    def return_home(self):
        self._returning_home = True
        self.close()

    def close(self):
        api = self.pages.get('api')
        if api is not None and api.winfo_exists() and api._is_busy():
            api.destroy()  # Existing deferred-close contract waits for the worker.
            self.show_page('api')
            return
        returning_home = self._returning_home
        self.destroy()
        if returning_home:
            self.app._show_workspace_page('home')
        self.app.lift()
        self.app.focus_set()

    def destroy(self):
        # Parent teardown also reaches here; do not route child destruction back
        # through close(), which would recurse into the same window.
        self._tearing_down = True
        super().destroy()

    def _on_destroy(self, event):
        if event.widget is not self:
            return
        previous = self._previous_grab
        self._previous_grab = None
        try:
            if previous is not None and previous.winfo_exists() and previous.winfo_viewable() and self.app.grab_current() is None:
                previous.grab_set()
        except tk.TclError:
            pass
