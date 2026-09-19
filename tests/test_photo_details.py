from dataclasses import asdict
from datetime import datetime
from types import SimpleNamespace
import tkinter as tk
from PIL import Image
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.crop_dialog import CropDialog
from ai_cull_assistant.subject import SubjectFeatures, detail_features, selection_score
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
        d.scale.set(1.4); d.shift.set(.1); d.offset_x.set(.2); d.ratio.set('1:1')
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
        assert d.offset_x.get()==.2
        assert detail_features(assets[0],d.global_settings()).face is not None
        d.save()
        settings=CropSettings.from_dict(asdict(saved[0]))
        assert settings.for_asset(assets[0]).aspect_ratio=='1:1'
        assert settings.for_asset(assets[0]).offset_x_factor==.2
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


def test_crop_frame_drag_and_wheel_are_separate_from_manual_face(tmp_path, monkeypatch):
    import ai_cull_assistant.crop_dialog as module
    preview_dir=tmp_path/'v05';preview_dir.mkdir()
    path=preview_dir/'face.jpg'
    Image.new('RGB',(400,600),'gray').save(path)
    asset=PhotoAsset('face',path,path,None,path,datetime.now(),'.jpg',preview_path=path)
    asset.subject_checked=True
    asset.subject_confidence=.8
    asset.subject_features=SubjectFeatures('','','',(.35,.2,.2,.2),(.3,.12,.3,.36))
    detections=[]
    opened=[]
    real_open=module.Image.open
    monkeypatch.setattr(module, 'ensure_preview', lambda *_: (_ for _ in ()).throw(AssertionError('existing preview must be reused')))
    monkeypatch.setattr(module, 'detect', lambda *_: detections.append(True) or [])
    monkeypatch.setattr(module.Image, 'open', lambda *args,**kwargs: opened.append(True) or real_open(*args,**kwargs))
    root=tk.Tk();root.withdraw()
    try:
        dialog=CropDialog(root,[asset],CropSettings(),lambda settings:None)
        dialog.render()
        left,top,right,bottom=dialog._crop_rect
        start_x=(left+right)/2;start_y=(top+bottom)/2
        dialog.pointer_down(SimpleNamespace(x=start_x,y=start_y))
        assert dialog._crop_selected
        assert dialog.canvas.itemcget('crop-outline','outline')=='#ffb000'
        dialog.pointer_move(SimpleNamespace(x=start_x+12,y=start_y+8))
        assert len(dialog.canvas.find_withtag('crop-outline'))==1
        assert not dialog.canvas.find_withtag('drag')
        dialog.pointer_up(SimpleNamespace(x=start_x+12,y=start_y+8))
        assert dialog.offset_x.get()>0
        assert dialog.shift.get()>0
        assert dialog.canvas.itemcget('crop-outline','outline')=='#ffb000'
        assert dialog.current_entry()['offset_x_factor']==round(dialog.offset_x.get(),3)
        assert dialog.current_entry()['shift_factor']==round(dialog.shift.get(),3)
        assert 'manual_face' not in dialog.current_entry()
        assert 'preview_version' not in dialog.current_entry()
        old_scale=dialog.scale.get()
        x,y,w,h,_,_=dialog._image_rect
        dialog.mouse_wheel(SimpleNamespace(x=x+w/2,y=y+h/2,delta=120,num=0))
        assert dialog.scale.get()==old_scale-.02
        dialog.render()
        assert len(detections)==1
        assert len(opened)==1
        start_x=x+w*.05;start_y=y+h*.65
        dialog.pointer_down(SimpleNamespace(x=start_x,y=start_y))
        assert not dialog._crop_selected
        assert dialog._drag[0]=='manual'
        dialog.pointer_move(SimpleNamespace(x=start_x+30,y=start_y+30))
        dialog.pointer_up(SimpleNamespace(x=start_x+30,y=start_y+30))
        assert dialog.current_entry()['preview_version']=='v05'
        assert dialog.canvas.itemcget('crop-outline','outline')=='#00aa66'
        left,top,right,bottom=dialog._crop_rect
        center=SimpleNamespace(x=(left+right)/2,y=(top+bottom)/2)
        dialog.pointer_down(center)
        dialog.pointer_up(center)
        assert dialog.canvas.itemcget('crop-outline','outline')=='#ffb000'
        dialog.auto_face()
        assert 'preview_version' not in dialog.current_entry()
        dialog.destroy()
        asset.subject_features=SimpleNamespace(face=None,head=(.3,.12,.3,.36))
        head_dialog=CropDialog(root,[asset],CropSettings(),lambda settings:None)
        assert head_dialog._crop_rect is not None
        labels=[head_dialog.canvas.itemcget(item,'text') for item in head_dialog.canvas.find_all()
                if head_dialog.canvas.type(item)=='text']
        assert '头部定位，清晰度待确认' in labels
        head_dialog.destroy()
    finally:
        root.destroy()


def test_multi_person_selector_saves_independent_crops_only_after_edit(tmp_path, monkeypatch):
    import ai_cull_assistant.crop_dialog as module
    path = tmp_path / 'people.jpg'
    Image.new('RGB', (600, 800), 'gray').save(path)
    asset = PhotoAsset('people', path, path, None, path, datetime.now(), '.jpg', preview_path=path)
    boxes = [(.1, .15, .2, .25), (.62, .2, .18, .22)]
    subjects = [SubjectFeatures('', '', None, box, box) for box in boxes]
    key = CropSettings().key(asset)
    settings = CropSettings(photos={key: {'selected_faces': [list(box) for box in boxes]}})
    monkeypatch.setattr(module, 'detail_features_list', lambda *_: subjects)
    monkeypatch.setattr(module, 'detect', lambda *_: [])
    root = tk.Tk(); root.withdraw()
    try:
        dialog = CropDialog(root, [asset], settings, lambda _settings: None)
        entry = dialog.current_entry()
        assert 'face_crops' not in entry
        assert tuple(dialog.person_picker.cget('values')) == ('人物 1', '人物 2')
        assert dialog.person.get() == '人物 1'

        dialog.scale.set(1.4)
        dialog.offset_x.set(.2)
        dialog.store_current()
        first_key = module.face_box_key(boxes[0])
        assert entry['face_crops'][first_key]['scale_factor'] == 1.4

        dialog.person_picker.current(1)
        dialog.select_person()
        assert dialog.person.get() == '人物 2'
        assert dialog.scale.get() == 1
        left, top, right, bottom = dialog._crop_rect
        center = SimpleNamespace(x=(left + right) / 2, y=(top + bottom) / 2)
        dialog.pointer_down(center)
        dialog.pointer_up(SimpleNamespace(x=center.x + 10, y=center.y + 6))
        second_offset = dialog.offset_x.get()
        assert second_offset > 0 and second_offset != .2
        dialog.ratio.set('1:1')
        dialog.shift.set(-.3)
        dialog.store_current()
        second_key = module.face_box_key(boxes[1])
        assert entry['face_crops'][second_key]['aspect_ratio'] == '1:1'
        assert entry['face_crops'][second_key]['shift_factor'] == -.3

        dialog.person_picker.current(0)
        dialog.select_person()
        assert dialog.scale.get() == 1.4
        assert dialog.offset_x.get() == .2
        dialog.person_picker.current(1)
        dialog.select_person()
        assert dialog.offset_x.get() == round(second_offset, 3)
        dialog.destroy()
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
    assets=[SimpleNamespace(
        primary_path=tmp_path/str(i), stem=str(i),
        subject_features=SimpleNamespace(face=(.2,.2,.2,.2), head=(.2,.15,.2,.3)),
    ) for i in range(4)]
    settings=CropSettings()
    settings.photos[settings.key(assets[2])]={'hidden':True}
    assets[3].subject_features=None
    monkeypatch.setattr(module, 'detail_features', lambda *_: (_ for _ in ()).throw(AssertionError('must use cached scan results')))
    calls=[]
    dialog=SimpleNamespace(assets=assets,index=1,store_current=lambda:calls.append('stored'),global_settings=lambda:settings,load_current=lambda:calls.append('loaded'),render=lambda:calls.append('rendered'),caption=SimpleNamespace(configure=lambda **kw:calls.append(kw['text'])))
    CropDialog.next_unmarked(dialog)
    assert dialog.index==3 and calls[:3]==['stored','loaded','rendered']
    assets[0].subject_features=None
    CropDialog.next_unmarked(dialog)
    assert dialog.index==0  # wraps past the last photo
    assets[0].subject_features=SimpleNamespace(face=(.2,.2,.2,.2),head=None)
    assets[3].subject_features=SimpleNamespace(face=None,head=(.2,.1,.3,.4))
    CropDialog.next_unmarked(dialog)
    assert '没有待补选' in calls[-1]
    settings.photos[settings.key(assets[0])]={'selected_faces': []}
    dialog.index=3
    CropDialog.next_unmarked(dialog)
    assert dialog.index==0  # explicit empty selection remains missing
    dialog.assets=[]
    CropDialog.next_unmarked(dialog)
    assert '请先扫描' in calls[-1]


def test_existing_manual_box_records_legacy_preview_version_on_store(tmp_path):
    preview_dir=tmp_path/'v04';preview_dir.mkdir()
    asset=SimpleNamespace(primary_path=tmp_path/'source.raw',preview_path=preview_dir/'source.jpg')
    settings=CropSettings()
    key=settings.key(asset)
    edits={key:{'manual_face':[.2,.2,.3,.3]}}
    dialog=SimpleNamespace(
        assets=[asset],index=0,_loading=False,edits=edits,
        global_settings=lambda:CropSettings(photos=edits),
        settings=lambda:CropSettings(scale_factor=1.2,shift_factor=.1,offset_x_factor=.2),
        _preview_version=CropDialog._preview_version,
    )
    CropDialog.store_current(dialog)
    assert edits[key]['preview_version']=='v04'


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
