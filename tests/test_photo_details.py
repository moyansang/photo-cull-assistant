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
    app=App(settings_dir=tmp_path); app.withdraw()
    app.preset_var.set('宽松'); app.per_page_var.set(24); app.columns_var.set(3)
    app.screening_var.set(False); app.input_var.set('E:/photos')
    app._close()
    app=App(settings_dir=tmp_path); app.withdraw()
    try:
        assert app.preset_var.get()=='宽松'
        assert app.per_page_var.get()==24 and app.columns_var.get()==3
        assert not app.screening_var.get() and app.input_var.get()=='E:/photos'
    finally:
        app._close()
