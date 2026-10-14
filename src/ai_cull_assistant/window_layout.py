"""Screen-aware sizing and positioning helpers for the Tk user interface."""
from __future__ import annotations

from dataclasses import dataclass
import sys
import tkinter as tk


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
    if getattr(window, '_embedded', False):
        return WindowGeometry(window.winfo_width(), window.winfo_height(), 0, 0)
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
    # Constructors withdraw before creating controls so theme initialization and
    # geometry negotiation cannot expose a default-sized window at the origin.
    if window.state() == 'withdrawn':
        window.update_idletasks()
        window.deiconify()
    return geometry
