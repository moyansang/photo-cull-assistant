from pathlib import Path
from types import SimpleNamespace
import ctypes as c
import sys
import tkinter as tk
import pytest
from ai_cull_assistant import directory_picker as picker
from ai_cull_assistant.app import App


def test_photo_previous_path_and_cancel(monkeypatch, tmp_path):
    calls=[]
    monkeypatch.setattr(picker.filedialog,'askdirectory',lambda **kw: calls.append(kw) or '')
    assert picker.choose_photo_directory(None,str(tmp_path)) == ''
    assert calls[0]['initialdir']==str(tmp_path)
    assert picker.existing_directory(tmp_path/'removed')==str(tmp_path)


def test_photo_empty_opens_this_pc(monkeypatch):
    monkeypatch.setattr(picker.sys,'platform','win32')
    calls=[]
    monkeypatch.setattr(picker,'_choose_this_pc',lambda *args: calls.append(args) or '')
    assert picker.choose_photo_directory(None,'')==''
    assert len(calls)==1


def test_workspace_always_uses_current_path(monkeypatch,tmp_path):
    from ai_cull_assistant import app as module
    calls=[]
    monkeypatch.setattr(module.filedialog,'askdirectory',lambda **kw: calls.append(kw) or '')
    app=SimpleNamespace(settings_dir=tmp_path/'settings',workspace_var=SimpleNamespace(get=lambda:str(tmp_path)))
    App._choose_workspace(app)
    App._choose_workspace(app)
    assert [call['initialdir'] for call in calls]==[str(tmp_path)]*2


@pytest.mark.skipif(sys.platform!='win32',reason='Windows Shell')
def test_native_dialog_initial_shell_folder_is_this_pc(monkeypatch):
    # Exercise real COM creation and SetFolder; inspect folder instead of
    # displaying a modal chooser or choosing anything on the user's behalf.
    def fake_show(dialog, owner):
        def invoke(obj,index,*types):
            address=c.cast(obj,c.POINTER(c.POINTER(c.c_void_p))).contents[index]
            return c.WINFUNCTYPE(c.c_long,c.c_void_p,*types)(address)
        folder=c.c_void_p();name=c.c_void_p()
        assert invoke(dialog,13,c.POINTER(c.c_void_p))(dialog,c.byref(folder))>=0
        try:
            assert invoke(folder,5,c.c_ulong,c.POINTER(c.c_void_p))(folder,0x80028000,c.byref(name))>=0
            assert '20d04fe0-3aea-1069-a2d8-08002b30309d' in c.wstring_at(name).lower()
        finally:
            ole=c.WinDLL('ole32');ole.CoTaskMemFree.argtypes=[c.c_void_p]
            if name.value:ole.CoTaskMemFree(name)
            invoke(folder,2)(folder)
        return -2147023673  # cancelled
    monkeypatch.setattr(picker,'_show_native_dialog',fake_show)
    root=tk.Tk();root.withdraw()
    try:
        assert picker._choose_this_pc(root,'选择照片文件夹')==''
    finally:root.destroy()
