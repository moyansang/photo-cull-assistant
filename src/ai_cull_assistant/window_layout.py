"""Screen-aware sizing and scrolling helpers for the Tk user interface."""
from __future__ import annotations

from dataclasses import dataclass
import sys
import tkinter as tk
from tkinter import ttk


@dataclass(frozen=True)
class WorkArea:
    """A monitor's usable rectangle, in Tk screen coordinates."""

    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class WindowGeometry:
    width: int
    height: int
    x: int
    y: int

    def tk_value(self) -> str:
        x = f"+{self.x}" if self.x >= 0 else str(self.x)
        y = f"+{self.y}" if self.y >= 0 else str(self.y)
        return f"{self.width}x{self.height}{x}{y}"


def calculate_window_geometry(
    preferred_size: tuple[int, int],
    work_area: WorkArea,
    *,
    minimum_size: tuple[int, int] = (320, 240),
    margin: int = 16,
) -> WindowGeometry:
    """Fit a preferred window size inside a work area and center it."""
    if work_area.width <= 0 or work_area.height <= 0:
        raise ValueError("work area dimensions must be positive")
    margin = max(0, int(margin))
    available_width = max(1, work_area.width - margin * 2)
    available_height = max(1, work_area.height - margin * 2)
    wanted_width = max(int(preferred_size[0]), int(minimum_size[0]), 1)
    wanted_height = max(int(preferred_size[1]), int(minimum_size[1]), 1)
    width = min(wanted_width, available_width)
    height = min(wanted_height, available_height)
    x = work_area.left + (work_area.width - width) // 2
    y = work_area.top + (work_area.height - height) // 2
    return WindowGeometry(width, height, x, y)


def _windows_work_area(widget: tk.Misc) -> WorkArea | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        monitor = user32.MonitorFromWindow(widget.winfo_id(), 2)  # MONITOR_DEFAULTTONEAREST
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.rcWork
            return WorkArea(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except (AttributeError, OSError, tk.TclError, ValueError):
        pass
    return None


def work_area_for(widget: tk.Misc) -> WorkArea:
    """Return the work area of the monitor nearest *widget*."""
    area = _windows_work_area(widget)
    if area is not None:
        return area
    try:
        width = int(widget.winfo_vrootwidth())
        height = int(widget.winfo_vrootheight())
        left = int(widget.winfo_vrootx())
        top = int(widget.winfo_vrooty())
        if width > 0 and height > 0:
            return WorkArea(left, top, width, height)
    except (AttributeError, tk.TclError, ValueError):
        pass
    return WorkArea(0, 0, int(widget.winfo_screenwidth()), int(widget.winfo_screenheight()))


def fit_window(
    window: tk.Toplevel | tk.Tk,
    preferred_size: tuple[int, int],
    *,
    minimum_size: tuple[int, int] = (320, 240),
    parent: tk.Misc | None = None,
    margin: int = 32,
    resizable: tuple[bool, bool] = (True, True),
) -> WindowGeometry:
    """Size and center a window within the nearest monitor's usable area."""
    window.update_idletasks()
    anchor = parent if parent is not None and parent.winfo_exists() else window
    area = work_area_for(anchor)
    geometry = calculate_window_geometry(
        preferred_size,
        area,
        minimum_size=minimum_size,
        margin=margin,
    )
    window.resizable(*resizable)
    if resizable[0] or resizable[1]:
        window.minsize(
            min(max(1, minimum_size[0]), geometry.width),
            min(max(1, minimum_size[1]), geometry.height),
        )
    window.geometry(geometry.tk_value())
    return geometry


class ScrollableFrame(ttk.Frame):
    """A frame whose content remains reachable when either dimension is tight."""

    def __init__(self, parent: tk.Misc, *, padding: int | tuple[int, ...] = 0) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.v_scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.h_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.content = ttk.Frame(self.canvas, padding=padding)
        self._content_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.v_scrollbar.set, xscrollcommand=self.h_scrollbar.set)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self._content_size = (1, 1)
        self.content.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_content)
        self._toplevel = self.winfo_toplevel()
        self._wheel_binding = self._toplevel.bind("<MouseWheel>", self._route_mousewheel, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _update_scroll_region(self, _event: tk.Event | None = None) -> None:
        self._content_size = (self.content.winfo_reqwidth(), self.content.winfo_reqheight())
        self._sync_content_size()

    def _resize_content(self, _event: tk.Event) -> None:
        self._sync_content_size()

    def _sync_content_size(self) -> None:
        canvas_width = max(1, self.canvas.winfo_width())
        canvas_height = max(1, self.canvas.winfo_height())
        content_width, content_height = self._content_size
        self.canvas.itemconfigure(
            self._content_id,
            width=max(canvas_width, content_width),
            height=max(canvas_height, content_height),
        )
        self.canvas.configure(scrollregion=(0, 0, max(canvas_width, content_width), max(canvas_height, content_height)))
        if content_width > canvas_width:
            self.h_scrollbar.grid(row=1, column=0, sticky="ew")
        else:
            self.h_scrollbar.grid_remove()
            self.canvas.xview_moveto(0)
        if content_height > canvas_height:
            self.v_scrollbar.grid(row=0, column=1, sticky="ns")
        else:
            self.v_scrollbar.grid_remove()
            self.canvas.yview_moveto(0)

    def _route_mousewheel(self, event: tk.Event) -> str | None:
        try:
            target = self.winfo_containing(event.x_root, event.y_root)
            origin = target
            while target is not None:
                if target == self:
                    if isinstance(origin, (tk.Text, tk.Listbox, ttk.Treeview)):
                        return None
                    if isinstance(origin, tk.Canvas) and origin != self.canvas:
                        return None
                    return self._on_mousewheel(event)
                target = target.master
        except tk.TclError:
            pass
        return None

    def _on_mousewheel(self, event: tk.Event) -> str:
        if event.state & 0x0001 and self.h_scrollbar.winfo_manager():
            self.canvas.xview_scroll(int(-event.delta / 120), "units")
        else:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget == self and self._wheel_binding:
            self._toplevel.unbind("<MouseWheel>", self._wheel_binding)
            self._wheel_binding = None


def scrollable_body(parent: tk.Misc, *, padding: int | tuple[int, ...] = 0) -> ttk.Frame:
    """Pack a responsive scrolling surface and return its content frame."""
    container = ScrollableFrame(parent, padding=padding)
    container.pack(fill="both", expand=True)
    container.content._scroll_container = container  # type: ignore[attr-defined]
    return container.content


# Kept as a descriptive alias for callers that only expect vertical overflow.
VerticalScrolledFrame = ScrollableFrame
