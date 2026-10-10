"""Current-workspace evidence cleanup UI; never restore or rescan to inspect it."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import messagebox

from . import evidence_cleanup as cleanup
from .settings import application_dir


def confirmation_text(plan, destination):
    return (
        f'当前工作区：{plan.workspace}\n'
        f'可移出 {len(plan.files)} 个纯图片证据包，共 {plan.bytes / 1024**2:.2f} MiB。\n'
        f'混合内容、异常或非图片项目将保留：{plan.preserved} 项。\n\n'
        f'原片缺失 {plan.missing_sources} 个、已变化 {plan.changed_sources} 个。'
        '缺失原片的证据可能无法再生成；已变化原片重生成的图片不保证与历史一致。\n\n'
        '仅移出 focus-evidence/blobs 中的图片包。保留扫描记录、评分与选择、人工分组、'
        '裁切设置、AI 请求和回答、任务、联系表及导出；不会重新扫描或改写照片记录。\n'
        '移出后将无法在工作区查看这些历史 AI 输入图片。后续重新评分仍需满足原片及任务校验。\n\n'
        f'保存到：{destination}\n'
        '本次是移动，尚未释放磁盘空间。检查后可手动删除；手动永久删除后无法从本功能恢复。\n\n'
        '确认移出这些证据图片吗？'
    )


class EvidenceCleanupMixin:
    def _refresh_evidence_button(self):
        if hasattr(self, 'evidence_cleanup_button'):
            busy = self._processing_busy or self._review_busy() or self.updates.busy
            self.evidence_cleanup_button.configure(state='disabled' if busy else 'normal')

    def _refresh_evidence_status(self):
        if hasattr(self, 'evidence_status_var'):
            value = self.workspace_var.get().strip()
            self.evidence_status_var.set(cleanup.evidence_status(value) if value else '')

    def _clean_evidence_images(self):
        if (getattr(self, '_cleaning_evidence', False) or self._processing_busy
                or self._review_busy() or self.updates.busy
                or getattr(self, '_clearing_workspace', False)
                or getattr(self, '_relocating_source', False)):
            messagebox.showinfo('暂不能清理', '任务或工作区操作进行中，请等待结束后再清理证据图片。', parent=self)
            return
        if any(isinstance(child, tk.Toplevel) and child.winfo_exists() for child in self.winfo_children()):
            messagebox.showinfo('暂不能清理', '请先关闭编辑或任务子窗口，保留并保存当前修改。', parent=self)
            return
        workspace, source = self.workspace_var.get().strip(), self.input_var.get().strip()
        if (not workspace or not source or workspace != self._active_workspace
                or source != self._active_input):
            messagebox.showinfo('暂不能清理', '请选择当前工作区及其原照片目录，等待路径切换完成。', parent=self)
            return
        # Do not call _sync_selected_workspace/_restore_session: either can drop
        # missing-photo records. Archived checkpoints are inspected in place.
        self._cleaning_evidence = self._clearing_workspace = True
        self._set_processing_busy(True)
        self.stop_button.configure(state='disabled')
        self.next_step_var.set('正在只读统计证据图片并核对原片，请稍候…')
        self._evidence_cleanup_worker(
            lambda: cleanup.inspect_evidence(Path(workspace), Path(source)),
            self._confirm_evidence_cleanup,
        )

    def _evidence_cleanup_worker(self, operation, callback):
        completed = queue.Queue()
        def run():
            try:
                completed.put((operation(), None))
            except Exception as exc:
                completed.put((None, exc))
        def poll():
            try:
                result, error = completed.get_nowait()
            except queue.Empty:
                self.after(50, poll)
                return
            if error is not None:
                self._finish_evidence_cleanup('证据清理未完成；请保留原文件和待手动删除目录。')
                messagebox.showerror('无法清理证据图片', str(error), parent=self)
            else:
                callback(result)
        threading.Thread(target=run, name='evidence-cleanup', daemon=True).start()
        self.after(50, poll)

    def _confirm_evidence_cleanup(self, plan):
        if self._close_after_stop:
            self._finish_evidence_cleanup('已取消证据清理。')
            return
        if not plan.files:
            self._finish_evidence_cleanup('没有可清理的纯图片证据包。')
            messagebox.showinfo('清理证据图片', f'没有可清理的纯图片证据包；保留异常或混合内容 {plan.preserved} 项。', parent=self)
            return
        destination = cleanup.default_quarantine_root(application_dir())
        if not messagebox.askyesno('确认清理证据图片', confirmation_text(plan, destination), parent=self, default='no'):
            self._finish_evidence_cleanup('已取消证据清理，文件未移动。')
            return
        self.next_step_var.set('正在复核并移出证据图片；请勿移动工作区或原照片…')
        self._evidence_cleanup_worker(lambda: cleanup.quarantine_evidence(plan, destination), self._evidence_cleanup_done)

    def _evidence_cleanup_done(self, result):
        text = (f"已移出 {result['moved']} 个证据包，共 {result['bytes'] / 1024**2:.2f} MiB。\n"
                f"未完成 {len(result['failed'])} 项；被占用或变化的文件会保留。\n"
                f"位置：{result['destination']}\n"
                '评分、分组和请求回答保持不变；尚未释放磁盘空间，检查后可手动删除移出的文件。')
        if result['failed']:
            text += '\n\n' + '\n'.join(result['failed'][:3])
        self._finish_evidence_cleanup('证据图片处理完成；检查待手动删除目录后再决定删除。')
        (messagebox.showwarning if result['failed'] else messagebox.showinfo)('清理证据图片', text, parent=self)

    def _finish_evidence_cleanup(self, text):
        self._cleaning_evidence = self._clearing_workspace = False
        self._set_processing_busy(False)
        self._refresh_evidence_status()
        self.next_step_var.set(text)
        if self._close_after_stop:
            self._close_after_stop = False
            self.after(0, self._close)
