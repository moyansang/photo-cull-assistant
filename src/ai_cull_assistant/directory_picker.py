"""Explicit initial locations for folder dialogs, including Windows This PC."""
from pathlib import Path
import sys
from tkinter import filedialog


def existing_directory(value):
    if not str(value).strip():
        return None
    path = Path(value).expanduser().absolute()
    for candidate in (path, *path.parents):
        if candidate.is_dir():
            return str(candidate)
    return None


def choose_photo_directory(parent, previous):
    initial = existing_directory(previous)
    if initial:
        return filedialog.askdirectory(parent=parent, title='选择照片文件夹', initialdir=initial)
    if sys.platform == 'win32':
        return _choose_this_pc(parent, '选择照片文件夹')
    return filedialog.askdirectory(parent=parent, title='选择照片文件夹', initialdir=str(Path.home()))


def _choose_this_pc(parent, title):
    # This PC is a Shell namespace item, not a filesystem path. Passing it as
    # Tk initialdir can normalize it into a nonexistent directory.
    import ctypes as c
    from ctypes import wintypes as w
    import uuid

    class GUID(c.Structure):
        _fields_ = [('bytes', c.c_ubyte * 16)]
        @classmethod
        def parse(cls, value):
            return cls.from_buffer_copy(uuid.UUID(value).bytes_le)

    ole = c.WinDLL('ole32')
    shell = c.WinDLL('shell32')
    ole.CoInitializeEx.argtypes = [c.c_void_p, w.DWORD]
    ole.CoInitializeEx.restype = c.c_long
    ole.CoCreateInstance.argtypes = [c.POINTER(GUID), c.c_void_p, w.DWORD,
                                    c.POINTER(GUID), c.POINTER(c.c_void_p)]
    ole.CoCreateInstance.restype = c.c_long
    ole.CoTaskMemFree.argtypes = [c.c_void_p]
    shell.SHCreateItemFromParsingName.argtypes = [w.LPCWSTR, c.c_void_p,
                                                c.POINTER(GUID), c.POINTER(c.c_void_p)]
    shell.SHCreateItemFromParsingName.restype = c.c_long

    def call(obj, index, *types):
        address = c.cast(obj, c.POINTER(c.POINTER(c.c_void_p))).contents[index]
        return c.WINFUNCTYPE(c.c_long, c.c_void_p, *types)(address)

    def checked(hr):
        if hr < 0:
            raise OSError(f'打开文件夹选择器失败：0x{hr & 0xffffffff:08X}')

    init = ole.CoInitializeEx(None, 2)
    if init < 0 and (init & 0xffffffff) != 0x80010106:
        checked(init)
    dialog, folder, result = c.c_void_p(), c.c_void_p(), c.c_void_p()
    name = c.c_void_p()
    try:
        clsid = GUID.parse('DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7')
        iid = GUID.parse('D57C7288-D4AD-4768-BE02-9D969532D960')
        item_iid = GUID.parse('43826D1E-E718-42EE-BC55-A1E261C37BFE')
        checked(ole.CoCreateInstance(c.byref(clsid), None, 1, c.byref(iid), c.byref(dialog)))
        options = w.DWORD()
        checked(call(dialog, 10, c.POINTER(w.DWORD))(dialog, c.byref(options)))
        checked(call(dialog, 9, w.DWORD)(dialog, options.value | 0x20 | 0x40 | 0x800 | 0x2000000))
        checked(call(dialog, 17, w.LPCWSTR)(dialog, title))
        checked(shell.SHCreateItemFromParsingName(
            '::{20D04FE0-3AEA-1069-A2D8-08002B30309D}', None, c.byref(item_iid), c.byref(folder)))
        checked(call(dialog, 12, c.c_void_p)(dialog, folder))
        user = c.WinDLL('user32')
        user.GetAncestor.argtypes = [w.HWND, w.UINT]
        user.GetAncestor.restype = w.HWND
        owner = user.GetAncestor(parent.winfo_id(), 2)
        hr = _show_native_dialog(dialog, owner)
        if (hr & 0xffffffff) == 0x800704C7:
            return ''
        checked(hr)
        checked(call(dialog, 20, c.POINTER(c.c_void_p))(dialog, c.byref(result)))
        checked(call(result, 5, w.DWORD, c.POINTER(c.c_void_p))(result, 0x80058000, c.byref(name)))
        return c.wstring_at(name)
    finally:
        if name.value:
            ole.CoTaskMemFree(name)
        for obj in (result, folder, dialog):
            if obj.value:
                call(obj, 2)(obj)
        if init >= 0:
            ole.CoUninitialize()


def _show_native_dialog(dialog, owner):
    import ctypes as c
    from ctypes import wintypes as w
    address = c.cast(dialog, c.POINTER(c.POINTER(c.c_void_p))).contents[3]
    return c.WINFUNCTYPE(c.c_long, c.c_void_p, w.HWND)(address)(dialog, owner)
