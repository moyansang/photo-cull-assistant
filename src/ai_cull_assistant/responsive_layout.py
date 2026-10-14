"""Small Tk containers that keep controls reachable on compact displays."""
import tkinter as tk
from tkinter import ttk


class WrappingToolbar(ttk.Frame):
    """Lay out whole controls in rows without shrinking their text."""

    def __init__(self, master, *, gap=6, **kwargs):
        super().__init__(master, **kwargs)
        self._items = []
        self._gap = gap
        self._row_gap = gap
        self._pending = None
        self.bind('<Configure>', self._schedule, add='+')
        self.bind('<Destroy>', self._destroy, add='+')

    def add(self, widget):
        self._items.append(widget)
        widget.place(x=0, y=0)
        widget.bind('<Configure>', self._schedule, add='+')
        self._schedule()
        return widget

    def _schedule(self, _event=None):
        if self._pending is None:
            self._pending = self.after_idle(self._layout)

    def set_row_gap(self, gap):
        if self._row_gap != gap:
            self._row_gap = gap
            self._schedule()

    def _layout(self):
        self._pending = None
        width = self.winfo_width()
        if width <= 1:
            return
        top = used = row_height = 0
        for widget in self._items:
            requested = widget.winfo_reqwidth() + self._gap
            if used and used + requested > width:
                top += row_height + self._row_gap
                used = row_height = 0
            # Let place track the natural size so text/font changes produce
            # Configure and reflow the next row, rather than clipping a label.
            widget.place(x=used, y=top)
            row_height = max(row_height, widget.winfo_reqheight())
            used += requested
        height = top + row_height + self._row_gap
        minimum_width = max((item.winfo_reqwidth() for item in self._items), default=1)
        if (self.winfo_pixels(self.cget('height')) != height
                or self.winfo_pixels(self.cget('width')) != minimum_width):
            self.configure(height=height, width=minimum_width)
            # A clipped frame need not receive Configure when only its
            # requested size changes. Update its scroll extent after Tk has
            # propagated the toolbar's new natural height.
            parent = self.master
            while parent is not None:
                if isinstance(parent, ScrollableFrame):
                    parent.request_layout()
                    break
                parent = getattr(parent, 'master', None)

    def _destroy(self, event):
        if event.widget is self and self._pending is not None:
            self.after_cancel(self._pending)
            self._pending = None


class ScrollableFrame(ttk.Frame):
    """A form viewport; oversized controls remain accessible by scrolling."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._pending = None
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        from .ui_style import COLORS
        self.canvas = tk.Canvas(self, width=1, height=1, highlightthickness=0,
                                background=COLORS['bg'])
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.content = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.vertical = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.vertical.grid(row=0, column=1, sticky='ns')
        self.horizontal = ttk.Scrollbar(self, orient='horizontal', command=self.canvas.xview)
        self.horizontal.grid(row=1, column=0, sticky='ew')
        self.canvas.configure(yscrollcommand=self.vertical.set, xscrollcommand=self.horizontal.set)
        self.canvas.bind('<Configure>', self._resize)
        self.content.bind('<Configure>', self._resize)
        self._top = self.winfo_toplevel()
        self._wheel_id = self._top.bind('<MouseWheel>', self._wheel, add='+')
        self.bind('<Destroy>', self._destroy, add='+')

    def request_layout(self):
        if self._pending is None:
            self._pending = self.after_idle(self._requested_layout)

    def _requested_layout(self):
        self._pending = None
        self._resize()

    def _resize(self, _event=None):
        if self.winfo_width() <= 1 or self.winfo_height() <= 1:
            return
        requested_width = self.content.winfo_reqwidth()
        requested_height = self.content.winfo_reqheight()
        vertical = horizontal = False
        for _ in range(2):
            available_width = max(1, self.winfo_width() - (self.vertical.winfo_reqwidth() if vertical else 0))
            horizontal = requested_width > available_width
            available_height = max(1, self.winfo_height() - (self.horizontal.winfo_reqheight() if horizontal else 0))
            vertical = requested_height > available_height
        self.vertical.grid() if vertical else self.vertical.grid_remove()
        self.horizontal.grid() if horizontal else self.horizontal.grid_remove()
        width = max(available_width, requested_width)
        height = max(available_height, requested_height)
        self.canvas.itemconfigure(self._window, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def _wheel(self, event):
        widget = event.widget
        if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox, ttk.Spinbox)):
            return
        while widget is not None and widget is not self:
            widget = getattr(widget, 'master', None)
        if widget is self:
            axis = self.canvas.xview if event.state & 1 else self.canvas.yview
            if axis() != (0.0, 1.0):
                axis('scroll', -int(event.delta / 120), 'units')
                return 'break'

    def _destroy(self, event):
        if event.widget is self and self._pending is not None:
            self.after_cancel(self._pending)
            self._pending = None
        if event.widget is self and self._wheel_id:
            self._top.unbind('<MouseWheel>', self._wheel_id)
            self._wheel_id = None
