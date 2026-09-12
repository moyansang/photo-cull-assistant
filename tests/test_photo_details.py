from dataclasses import asdict
from datetime import datetime
from types import SimpleNamespace
import tkinter as tk
from PIL import Image
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.crop_dialog import CropDialog
from ai_cull_assistant.subject import detail_features, selection_score
from ai_cull_assistant.yunet import FaceDetection


def test_photo_edit_navigation_manual_hide_save_cancel(tmp_path):
    assets=[]
    for name in ('a','b'):
        path=tmp_path/f'{name}.jpg'
        Image.new('RGB',(400,600),'gray').save(path)
        assets.append(PhotoAsset(name,path,path,None,path,datetime.now(),'.jpg',preview_path=path))
    root=tk.Tk(); root.withdraw()
    saved=[]
    try:
        initial=CropSettings()
        d=CropDialog(root,assets,initial,saved.append)
        d.scale.set(1.4); d.shift.set(.1); d.ratio.set('1:1')
        d.render()
        x,y,w,h,_,_=d._image_rect
        d.pointer_down(SimpleNamespace(x=x+w*.3,y=y+h*.2))
        d.pointer_up(SimpleNamespace(x=x+w*.6,y=y+h*.4))
        assert detail_features(assets[0],d.global_settings()).face is not None
        d.navigate(1)
        assert d.scale.get()==1 and d.shift.get()==0 and d.ratio.get()=='124:150'
        d.hide_face(); d.confidence.set(.95)
        d.navigate(-1)
        assert d.scale.get()==1.4 and d.shift.get()==.1
        assert detail_features(assets[0],d.global_settings()).face is not None
        d.save()
        settings=CropSettings.from_dict(asdict(saved[0]))
        assert settings.for_asset(assets[0]).aspect_ratio=='1:1'
        assert settings.for_asset(assets[1]).scale_factor==1
        assert settings.for_asset(assets[0]).detection_confidence==.95
        assert detail_features(assets[1],settings).face is None
        assert initial.photos=={}
        d=CropDialog(root,assets,settings,saved.append)
        d.auto_face(); d.scale.set(2); d.destroy()
        assert settings.for_asset(assets[0]).scale_factor==1.4
        assert settings.photos[settings.key(assets[0])]['manual_face']
    finally:
        root.destroy()


def test_confident_subject_beats_large_backdrop():
    real=FaceDetection((400,400,140,180),(),.93)
    backdrop=FaceDetection((50,0,300,300),(),.84)
    assert selection_score(real,1067,1600)>selection_score(backdrop,1067,1600)+.025


def test_all_main_options_survive_restart(tmp_path):
    # Isolate Tcl interpreter lifetime from other image/GUI tests.
    import subprocess, sys
    result = subprocess.run([sys.executable, '-c', "import runpy,sys; from pathlib import Path; runpy.run_path(sys.argv[1])['_check_restart'](Path(sys.argv[2]))", __file__, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _check_restart(tmp_path):
    from ai_cull_assistant.app import App
    photos=tmp_path/'photos';photos.mkdir()
    app=App(settings_dir=tmp_path); app.withdraw()
    app.input_var.set(str(photos));app._sync_selected_workspace()
    app.preset_var.set('宽松'); app.per_page_var.set(24); app.columns_var.set(3)
    app.screening_var.set(False); app.no_updates_var.set(True)
    app._close()
    app=App(settings_dir=tmp_path); app.withdraw()
    try:
        assert app.preset_var.get()=='宽松'
        assert app.per_page_var.get()==24 and app.columns_var.get()==3
        assert not app.screening_var.get() and app.input_var.get()==str(photos)
        assert app.no_updates_var.get()
    finally:
        app._close()


def test_next_unmarked_wraps_skips_hidden_and_preserves_edits(monkeypatch, tmp_path):
    import ai_cull_assistant.crop_dialog as module
    assets=[SimpleNamespace(primary_path=tmp_path/str(i), stem=str(i)) for i in range(4)]
    settings=CropSettings()
    settings.photos[settings.key(assets[2])]={'hidden':True}
    marked={0,1}
    monkeypatch.setattr(module, 'detail_features', lambda asset, settings: SimpleNamespace(face=(.2,.2,.2,.2) if int(asset.stem) in marked else None))
    calls=[]
    dialog=SimpleNamespace(assets=assets,index=1,store_current=lambda:calls.append('stored'),global_settings=lambda:settings,load_current=lambda:calls.append('loaded'),render=lambda:calls.append('rendered'),caption=SimpleNamespace(configure=lambda **kw:calls.append(kw['text'])))
    CropDialog.next_unmarked(dialog)
    assert dialog.index==3 and calls[:3]==['stored','loaded','rendered']
    marked.remove(0)
    CropDialog.next_unmarked(dialog)
    assert dialog.index==0  # wraps past the last photo
    marked.update({0,3})
    CropDialog.next_unmarked(dialog)
    assert '没有待补选' in calls[-1]
    dialog.assets=[]
    CropDialog.next_unmarked(dialog)
    assert '请先扫描' in calls[-1]


def test_crop_title_and_footer_visible_on_small_screen(tmp_path, monkeypatch):
    from ai_cull_assistant import window_layout
    monkeypatch.setattr(window_layout,'work_area_for',lambda w:window_layout.WorkArea(0,0,800,600))
    root=tk.Tk()
    p=tmp_path/'sample.jpg';Image.new('RGB',(400,600),'gray').save(p)
    a=PhotoAsset('sample',p,p,None,p,datetime.now(),'.jpg',preview_path=p)
    try:
        d=CropDialog(root,[a],CropSettings(),lambda s:None)
        root.update()
        assert d.title()=='检测/调整人脸框'
        buttons=[]
        def walk(w):
            for c in w.winfo_children():
                if isinstance(c,tk.ttk.Button) and c.cget('text') in ('取消','重新扫描修改过的图片'):buttons.append(c)
                walk(c)
        walk(d)
        assert len(buttons)==2
        for b in buttons:
            assert b.winfo_viewable()
            assert b.winfo_rooty()+b.winfo_height()<=d.winfo_rooty()+d.winfo_height()
    finally:
        root.destroy()
