import queue
import subprocess
import sys
import threading
from tkinter import messagebox, Toplevel

from .settings import application_dir
from . import updater


class UpdateController:
    def __init__(self, app):
        self.app=app
        self.busy=False
        self.events=queue.Queue()
        self.app.after(200,self.poll)

    def _set_busy(self, busy):
        self.busy=busy
        self.app._set_update_busy(busy)

    def check(self, manual=False):
        if self.busy: return
        if self.app._processing_busy:
            if manual: messagebox.showinfo('检查更新','请先完成当前照片处理。',parent=self.app)
            return
        self._set_busy(True)
        def work():
            try: self.events.put(('release',updater.latest_release(self.app.settings_dir / "update-cache.json", force=manual),manual))
            except Exception as exc: self.events.put(('error',str(exc),manual))
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        while True:
            try:
                kind,value,manual=self.events.get_nowait()
            except queue.Empty:
                break
            if kind!='progress': self._set_busy(False)
            if kind=='error':
                self.app._restore_scan_progress()
                if manual: messagebox.showerror('检查/下载更新失败',value,parent=self.app)
                else: self.app._log('自动检查更新失败，可稍后手动检查：'+value)
            elif kind=='release':
                if value:
                    if not manual and self.app.no_updates_var.get():
                        continue
                    if messagebox.askyesno('发现新版本',f"发现 {value['version']}，是否下载并更新？\n下载校验后将关闭程序并重新启动。\n设置、照片、联系表等用户内容会保留。",parent=self.app):
                        self.download(value)
                    else: self.app._restore_scan_progress()
                elif manual: messagebox.showinfo('检查更新','当前已是最新正式版。',parent=self.app)
            elif kind=='progress':
                self.app._show_update_progress(value)
            elif kind=='ready':
                self.app._restore_scan_progress()
                self.app._save_preferences()
                helper,plan=value
                try:
                    subprocess.Popen([str(helper),'--install-update',str(plan)],cwd=str(helper.parent))
                except OSError as exc:
                    messagebox.showerror('更新失败',str(exc),parent=self.app)
                else:
                    self.app._close(); return
        self.app.after(200,self.poll)

    def download(self,release):
        if not getattr(sys,'frozen',False):
            self.app._restore_scan_progress()
            messagebox.showinfo('更新','源码运行模式不自动替换，请使用 Windows 便携版。',parent=self.app); return
        worker=getattr(self.app,'_scan_thread',None)
        if (worker and worker.is_alive()) or any(isinstance(c,Toplevel) for c in self.app.winfo_children()):
            self.app._restore_scan_progress()
            messagebox.showinfo('更新','请先完成扫描并关闭编辑窗口，再更新。',parent=self.app); return
        self._set_busy(True)
        self.app._show_update_progress(0)
        self.app._log('正在下载并校验新版，请等待；准备完成后将自动重启。')
        def work():
            last=[None]
            def progress(percent, _phase):
                if percent != last[0]:
                    last[0]=percent
                    self.events.put(('progress',percent,True))
            try: self.events.put(('ready',updater.prepare_update(release,application_dir(),progress),True))
            except Exception as exc: self.events.put(('error',str(exc),True))
        threading.Thread(target=work,daemon=True).start()
