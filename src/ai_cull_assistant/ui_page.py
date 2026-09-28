from __future__ import annotations

import tkinter as tk


class WorkspacePage(tk.Frame):
    """A workspace surface that can live in the main window or in a dialog.

    Standalone pages are packed into a private ``Toplevel`` and expose the
    small window-manager API used by the existing dialogs.  Embedded pages are
    ordinary frames; their owner decides when to grid, pack, or hide them.
    """

    def __init__(self, parent: tk.Misc, *, embedded: bool = False) -> None:
        self._embedded = bool(embedded)
        self._page_parent = parent
        self._host_window: tk.Toplevel | None = None
        master: tk.Misc = parent
        if not self._embedded:
            self._host_window = tk.Toplevel(parent)
            self._host_window.withdraw()
            master = self._host_window
        super().__init__(master, borderwidth=0, highlightthickness=0)
        if self._host_window is not None:
            self.pack(fill="both", expand=True)
            self._host_window.protocol("WM_DELETE_WINDOW", self.close_page)

    @property
    def window(self) -> tk.Misc:
        """Return the real window hosting this page."""
        return self._host_window or self.winfo_toplevel()

    def return_home(self) -> None:
        """Hide an embedded editor without discarding its draft."""
        owner = self.winfo_toplevel()
        if self._embedded and hasattr(owner, '_show_workspace_page'):
            owner._show_workspace_page('home')
        else:
            self.close_page()

    def close_page(self) -> None:
        """Close a standalone page; hide an embedded page without discarding it."""
        if self._embedded:
            manager = self.winfo_manager()
            if manager == "grid":
                self.grid_remove()
            elif manager == "pack":
                self.pack_forget()
            elif manager == "place":
                self.place_forget()
            return
        host = self._host_window
        if host is not None and host.winfo_exists():
            host.destroy()

    # ``fit_window`` and older tests address a page like a Toplevel.  Keep that
    # contract while the content itself remains a reusable Frame.
    def title(self, value: str | None = None):
        return self._wm("title", value)

    wm_title = title

    def transient(self, master: tk.Misc | None = None):
        return self._wm("transient", master.winfo_toplevel() if master is not None else None)

    def attributes(self, *args):
        return self._wm('attributes', *args)

    def grab_set(self):
        if not self._embedded:
            return super().grab_set()

    def grab_release(self):
        if not self._embedded:
            return super().grab_release()

    wm_transient = transient

    def geometry(self, value: str | None = None):
        return self._wm("geometry", value)

    wm_geometry = geometry

    def minsize(self, width: int | None = None, height: int | None = None):
        if width is None or height is None:
            return self._wm("minsize")
        return self._wm("minsize", width, height)

    wm_minsize = minsize

    def resizable(self, width: bool | None = None, height: bool | None = None):
        if width is None or height is None:
            return self._wm("resizable")
        return self._wm("resizable", width, height)

    wm_resizable = resizable

    def protocol(self, name: str | None = None, func=None):
        if name is None:
            return self._wm("protocol")
        return self._wm("protocol", name, func)

    wm_protocol = protocol

    def state(self, newstate: str | None = None):
        return self._wm("state", newstate)

    wm_state = state

    def withdraw(self) -> None:
        self._wm("withdraw")

    wm_withdraw = withdraw

    def deiconify(self) -> None:
        self._wm("deiconify")

    wm_deiconify = deiconify

    def _wm(self, method: str, *args):
        host = self._host_window
        if host is None:
            if method == "state":
                return "normal"
            if method in {"geometry", "title"} and not args:
                return ""
            return None
        return getattr(host, method)(*args)

    def destroy(self) -> None:
        host = self._host_window
        try:
            super().destroy()
        finally:
            # When callers destroy the page directly, remove the empty wrapper
            # on the next idle turn.  When the wrapper/root is already closing,
            # Tk cancels this callback with the window and avoids recursion.
            if host is not None:
                try:
                    if host.winfo_exists():
                        host.after_idle(lambda: host.destroy() if host.winfo_exists() else None)
                except tk.TclError:
                    pass
