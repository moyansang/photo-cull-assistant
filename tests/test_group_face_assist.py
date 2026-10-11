import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import tkinter as tk
from PIL import Image

from ai_cull_assistant import crop_dialog as crop_dialog_module
from ai_cull_assistant import group_face_assist
from ai_cull_assistant import group_face_assist_dialog
from ai_cull_assistant.crop_dialog import CropDialog
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.group_face_assist import (
    FaceProposal,
    clamp_box,
    collect_targets,
    normalized_box_ok,
    reference_box_from_entry,
)
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.yunet import FaceDetection


def wait_preview(dialog):
    deadline = time.monotonic() + 10
    while (dialog._future is not None or dialog._wanted is not None or dialog._pending) and time.monotonic() < deadline:
        dialog.update()
        time.sleep(.01)
    assert dialog._future is None and dialog._wanted is None


def pump(widget, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            widget.update()
        except tk.TclError:
            break
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), 'timed out waiting for dialog state'


def asset(stem, group_id=1, subject=None):
    path = Path(f'/fake/{stem}.RW2')
    return PhotoAsset(stem, path, path, path, None, datetime(2026, 1, 1), '.rw2',
                      group_id=group_id, subject_features=subject)


def key_for(item):
    return item.stem


# --------------------------------------------------------------- selection

def test_collect_targets_skips_marked_hidden_and_explicit_empty():
    reference = asset('ref')
    marked = asset('marked', subject=SimpleNamespace(head=(0, 0, .1, .1), face=None))
    members = [
        asset('before'),
        reference,
        asset('t_manual'),
        marked,
        asset('t_hidden'),
        asset('t_empty'),
        asset('t_selected'),
        asset('t_missing'),
    ]
    edits = {
        't_manual': {'manual_face': [.3, .3, .1, .1]},
        't_hidden': {'hidden': True},
        't_empty': {'selected_faces': []},
        't_selected': {'selected_faces': [[.2, .2, .1, .1]]},
        'before': {},
    }
    targets = collect_targets(reference, members, edits, key_for)
    assert [target.key for target in targets] == ['before', 't_missing']
    assert targets[0].snapshot == '{}'


def test_collect_targets_orders_capture_neighbours_first():
    reference = asset('ref')
    members = [asset('a'), asset('b'), reference, asset('c'), asset('d')]
    targets = collect_targets(reference, members, {}, key_for)
    assert [target.key for target in targets] == ['b', 'c', 'a', 'd']


def test_collect_targets_rejects_assets_from_other_groups():
    reference = asset('ref', group_id=1)
    members = [
        asset('other_before', group_id=2),
        reference,
        asset('same_group', group_id=1),
        asset('other_after', group_id=2),
    ]
    targets = collect_targets(reference, members, {}, key_for)
    assert [target.key for target in targets] == ['same_group']


def test_reference_box_states():
    assert reference_box_from_entry({'manual_face': [.1, .1, .2, .2]}) == ([.1, .1, .2, .2], 'ok')
    assert reference_box_from_entry({'selected_faces': [[.1, .1, .2, .2]]}) == ([.1, .1, .2, .2], 'ok')
    assert reference_box_from_entry({'selected_faces': [[.1, .1, .2, .2], [.4, .4, .2, .2]]})[1] == 'multiple'
    assert reference_box_from_entry({'selected_faces': [[]], 'manual_face': [2, 0, 0, 0]})[1] == 'none'
    assert reference_box_from_entry({})[1] == 'none'


def test_box_validity_helpers():
    assert normalized_box_ok([.1, .1, .2, .2])
    assert not normalized_box_ok([.9, .5, .2, .2])
    assert not normalized_box_ok([.1, .1, 0, .2])
    assert not normalized_box_ok('bad')
    assert clamp_box(-.2, .5, .9, .9) == (0.0, 0.5, 0.9, 0.5)
    assert clamp_box(.2, .2, 5, 5) == (.2, .2, .8, .8)


BOX = (.40, .30, .12, .15)


# ------------------------------------------------------------- dialog flow

class Recorder:
    def __init__(self):
        self.calls = []
        self.yes = True

    def _show(self, kind):
        def show(title, message, parent=None):
            self.calls.append((kind, title, message))
            return self.yes if kind == 'askyesno' else None
        return show

    def install(self, monkeypatch, module):
        for kind in ('showinfo', 'showwarning', 'showerror', 'askyesno'):
            monkeypatch.setattr(module.messagebox, kind, self._show(kind), raising=False)


def make_assets(tmp_path, count=3, group_id=1):
    preview_dir = tmp_path / 'v05'
    preview_dir.mkdir(exist_ok=True)
    assets = []
    for index in range(count):
        path = preview_dir / f'p{index}.jpg'
        Image.new('RGB', (400, 600), 'gray').save(path)
        source = tmp_path / f'p{index}.RW2'
        source.write_bytes(b'raw-placeholder')
        assets.append(PhotoAsset(f'p{index}', path, source, None, path, datetime(2026, 1, 1),
                                 '.rw2', group_id=group_id, preview_path=path))
    return assets


def open_dialog_with_manual_face(monkeypatch, tmp_path):
    ui = Recorder()
    ui.install(monkeypatch, crop_dialog_module)
    assets = make_assets(tmp_path)
    root = tk.Tk()
    root.withdraw()
    saved = []
    dialog = CropDialog(root, assets, CropSettings(), saved.append)
    wait_preview(dialog)
    dialog.render()
    wait_preview(dialog)
    x, y, w, h, _, _ = dialog._image_rect
    dialog.pointer_down(SimpleNamespace(x=x + w * .3, y=y + h * .2))
    dialog.pointer_up(SimpleNamespace(x=x + w * .6, y=y + h * .4))
    return root, dialog, assets, saved, ui


def fake_propose(proposals_by_key):
    def propose(reference, reference_box, targets, stop_event, on_progress=None, **kwargs):
        produced = []
        for index, target in enumerate(targets, 1):
            box = proposals_by_key.get(target.key)
            if box:
                proposal = FaceProposal(target.key, reference.key, box, 'reliable',
                                        .9, .93, '匹配唯一且通过人脸检测')
            else:
                proposal = FaceProposal(target.key, reference.key, None, 'missing',
                                        None, None, '未找到足够强的匹配')
            produced.append(proposal)
            if on_progress:
                on_progress(index, len(targets), proposal)
        return produced
    return propose


def test_workspace_assist_uses_active_person_without_group_chooser(monkeypatch, tmp_path):
    assets = make_assets(tmp_path)
    assets[1].group_id = 2
    assets[2].group_id = 3
    root = tk.Tk()
    root.withdraw()
    captured = {}

    class DummyAssist:
        def __init__(self, parent, reference, reference_box, targets, **kwargs):
            captured.update(reference=reference, reference_box=reference_box,
                            targets=list(targets), kwargs=kwargs)

        def winfo_exists(self):
            return True

    monkeypatch.setattr(crop_dialog_module, 'GroupFaceAssistDialog', DummyAssist)
    dialog = CropDialog(root, assets, CropSettings(), lambda _settings: None)
    try:
        wait_preview(dialog)
        settings = dialog.global_settings()
        reference_key = settings.key(assets[0])
        first = [.10, .12, .16, .18]
        active = [.58, .15, .14, .17]
        dialog.edits[reference_key] = {'selected_faces': [first, active]}
        dialog._active_face_key = crop_dialog_module.face_box_key(active)
        dialog.load_current()
        dialog.assist_group_faces()
        assert captured['reference_box'] == tuple(active)
        assert {target.key for target in captured['targets']} == {
            settings.key(assets[1]), settings.key(assets[2])}
        assert all(target.cross_group for target in captured['targets'])
        assert dialog._assist_reference_face_key == crop_dialog_module.face_box_key(active)
    finally:
        dialog._assist_dialog = None
        dialog.destroy()
        root.destroy()


def test_multi_person_reference_remains_valid_after_other_person_changes(tmp_path, monkeypatch):
    assets = make_assets(tmp_path, count=2)
    warnings = []
    monkeypatch.setattr(crop_dialog_module.messagebox, 'showwarning',
                        lambda *args, **kwargs: warnings.append(args))
    dialog = object.__new__(CropDialog)
    settings = CropSettings()
    reference_key = settings.key(assets[0])
    target_key = settings.key(assets[1])
    active = [.58, .15, .14, .17]
    dialog.edits = {reference_key: {
        'selected_faces': [[.10, .12, .16, .18], active]}}
    dialog._assist_reference_face_key = crop_dialog_module.face_box_key(active)
    dialog._assist_assets = {reference_key: assets[0], target_key: assets[1]}
    dialog._assist_signatures = {
        reference_key: group_face_assist.asset_signature(assets[0]),
        target_key: group_face_assist.asset_signature(assets[1]),
    }
    dialog.load_current = lambda: None
    dialog.render = lambda: None
    # Editing or reordering another participant does not invalidate the
    # reference person while the selected face itself still exists.
    dialog.edits[reference_key]['selected_faces'] = [[.2, .2, .1, .1], active]
    applied, skipped = dialog.apply_assist_proposals(reference_key, active, [{
        'target_key': target_key,
        'box': [.25, .30, .12, .15],
        'snapshot': '{}',
        'stem': assets[1].stem,
    }])
    assert applied == 1 and skipped == [] and warnings == []
    assert dialog.edits[target_key]['manual_face'] == [.25, .30, .12, .15]


def test_worker_built_preview_refreshes_guarded_target_signature(tmp_path):
    assets = make_assets(tmp_path, count=1)
    asset = assets[0]
    missing = tmp_path / 'v05' / 'missing.jpg'
    asset.preview_path = missing
    key = CropSettings().key(asset)
    dialog = object.__new__(CropDialog)
    dialog._assist_assets = {key: asset}
    before = group_face_assist.asset_signature(asset)
    dialog._assist_signatures = {key: before}
    dialog._assist_preview_cache_dir = tmp_path
    Image.new('RGB', (100, 140), 'gray').save(missing)

    dialog._assist_target_prepared(key, str(missing))

    assert asset.preview_path == missing.resolve()
    assert dialog._assist_signatures[key] == group_face_assist.asset_signature(asset)
    assert dialog._assist_signatures[key] != before


def test_assist_dialog_adopts_into_parent_edits(monkeypatch, tmp_path):
    root, dialog, assets, saved, _ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key
    proposals = {key(assets[1]): (.25, .30, .12, .15), key(assets[2]): (.25, .30, .12, .15)}
    monkeypatch.setattr(group_face_assist, 'propose_person_faces', fake_propose(proposals))
    dialog_ui = Recorder()
    dialog_ui.install(monkeypatch, group_face_assist_dialog)
    try:
        dialog.assist_group_faces()
        assist = dialog._assist_dialog
        assert assist is not None
        assert assist.master is dialog
        assert str(assist.transient()) == str(dialog.winfo_toplevel())
        pump(assist, lambda: all(row['proposal'] is not None for row in assist.rows.values()))
        target_key = key(assets[1])
        pump(assist, lambda: target_key in assist._thumbnail_cache)
        assert assist._reference_photos
        assert assist.tree.item(target_key, 'image')
        assist.tree.selection_set(target_key)
        assist.confirm_current()
        assist.accept_selected()
        pump(root, lambda: 'manual_face' in dialog.edits.get(key(assets[1]), {}))
        entry = dialog.edits[key(assets[1])]
        assert [round(v, 3) for v in entry['manual_face']] == [.25, .3, .12, .15]
        assert entry['preview_version'] == 'v05'
        assert saved == []  # adoption touches only the parent draft, never saves
        assert not assist.winfo_exists()
    finally:
        dialog.destroy()
        root.destroy()


def test_assist_fixed_actions_visible_on_short_desktop(monkeypatch, tmp_path):
    from ai_cull_assistant import window_layout
    monkeypatch.setattr(window_layout, 'work_area_for',
                        lambda _widget: window_layout.WorkArea(0, 0, 900, 600))
    root, dialog, assets, _saved, _ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    root.deiconify()
    dialog.deiconify()
    root.update()
    key = dialog.global_settings().key
    monkeypatch.setattr(group_face_assist, 'propose_person_faces', fake_propose({
        key(assets[1]): (.25, .3, .12, .15),
    }))
    try:
        dialog.assist_group_faces()
        assist = dialog._assist_dialog
        pump(assist, lambda: assist._finished)
        wanted = {'停止查找', '确认此框 (Y)', '不是这个人 (X)', '暂时跳过 (P)', '关闭', '采用选中候选'}
        buttons = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.ttk.Button) and child.cget('text') in wanted:
                    buttons.append(child)
                walk(child)

        walk(assist)
        assert {button.cget('text') for button in buttons} == wanted
        for button in buttons:
            assert button.winfo_viewable()
            assert button.winfo_rooty() + button.winfo_height() <= assist.winfo_rooty() + assist.winfo_height()
        assert assist.reference_preview.winfo_viewable()
        assert assist.preview.winfo_height() >= 80
    finally:
        dialog.destroy()
        root.destroy()


def test_assist_dialog_cancel_writes_nothing(monkeypatch, tmp_path):
    root, dialog, assets, saved, _ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key
    monkeypatch.setattr(group_face_assist, 'propose_person_faces',
                        fake_propose({key(assets[1]): (.25, .3, .12, .15)}))
    try:
        dialog.assist_group_faces()
        assist = dialog._assist_dialog
        pump(assist, lambda: all(row['proposal'] is not None for row in assist.rows.values()))
        assist.rows[key(assets[1])]['accepted'] = True
        assist.destroy()
        pump(root, lambda: dialog._assist_dialog is None)
        assert 'manual_face' not in dialog.edits.get(key(assets[1]), {})
        assert saved == []
    finally:
        dialog.destroy()
        root.destroy()


def test_accept_skips_targets_changed_after_snapshot(monkeypatch, tmp_path):
    root, dialog, assets, _saved, _ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key
    monkeypatch.setattr(group_face_assist, 'propose_person_faces', fake_propose({
        key(assets[1]): (.25, .3, .12, .15), key(assets[2]): (.25, .3, .12, .15)}))
    dialog_ui = Recorder()
    dialog_ui.install(monkeypatch, group_face_assist_dialog)
    try:
        dialog.assist_group_faces()
        assist = dialog._assist_dialog
        pump(assist, lambda: all(row['proposal'] is not None for row in assist.rows.values()))
        user_box = [.4, .4, .1, .1]
        dialog.edits.setdefault(key(assets[2]), {})['manual_face'] = user_box
        for target_key in (key(assets[1]), key(assets[2])):
            assist.rows[target_key]['accepted'] = True
        assist.accept_selected()
        pump(root, lambda: not assist.winfo_exists())
        assert 'manual_face' in dialog.edits[key(assets[1])]
        assert dialog.edits[key(assets[2])]['manual_face'] == user_box
        assert any(kind == 'showwarning' for kind, *_rest in dialog_ui.calls)
    finally:
        dialog.destroy()
        root.destroy()


def test_accept_voids_round_when_reference_box_changed(monkeypatch, tmp_path):
    root, dialog, assets, _saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key
    reference_key = key(assets[0])
    monkeypatch.setattr(group_face_assist, 'propose_person_faces',
                        fake_propose({key(assets[1]): (.25, .3, .12, .15)}))
    try:
        dialog.assist_group_faces()
        assist = dialog._assist_dialog
        pump(assist, lambda: all(row['proposal'] is not None for row in assist.rows.values()))
        dialog.edits[reference_key]['manual_face'] = [.1, .1, .5, .5]
        assist.rows[key(assets[1])]['accepted'] = True
        assist.accept_selected()
        pump(root, lambda: not assist.winfo_exists())
        assert 'manual_face' not in dialog.edits.get(key(assets[1]), {})
        assert any(kind == 'showwarning' for kind, *_rest in ui.calls)
    finally:
        dialog.destroy()
        root.destroy()


# ------------------------------------------------------------- save prompt

def test_save_prompts_only_about_photos_with_ai_conclusions(monkeypatch, tmp_path):
    root, dialog, assets, saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    assets[0].ai_focus_result = {'verdict': 'clear'}
    try:
        dialog.save()
        prompts = [call for call in ui.calls if call[0] == 'askyesno']
        assert prompts and '1 张' in prompts[-1][2]
        assert '不会自动调用 API' in prompts[-1][2]
        assert len(saved) == 1
    finally:
        dialog.destroy()
        root.destroy()


def test_save_prompt_decline_keeps_dialog_working(monkeypatch, tmp_path):
    root, dialog, assets, saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    assets[0].ai_focus_result = {'verdict': 'clear'}
    ui.yes = False
    try:
        dialog.save()
        assert saved == []
        assert dialog.winfo_exists()
    finally:
        dialog.destroy()
        root.destroy()


def test_candidate_drag_wheel_and_close_do_not_apply(monkeypatch, tmp_path):
    root, dialog, assets, saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key
    target_key = key(assets[1])
    monkeypatch.setattr(group_face_assist, 'propose_person_faces',
                        fake_propose({target_key: (.25, .3, .12, .15)}))
    try:
        dialog.assist_group_faces()
        assist = dialog._assist_dialog
        pump(assist, lambda: assist._finished)
        assist.tree.selection_set(target_key)
        assist.show_current()
        ix, iy, dw, dh, _, _ = assist._image_rect
        start = SimpleNamespace(x=ix+.31*dw, y=iy+.375*dh)
        end = SimpleNamespace(x=start.x+.05*dw, y=start.y+.04*dh)
        assist.pointer_down(start)
        assist.pointer_move(end)
        assist.pointer_up(end)
        box = assist.rows[target_key]['box']
        assert box == pytest.approx((.30, .34, .12, .15))
        assert assist._selected_box_key == target_key
        assist.pointer_wheel(SimpleNamespace(delta=120))
        assert assist.rows[target_key]['box'][2] == pytest.approx(.12*1.02)
        assert not assist.rows[target_key]['accepted']
        assist.confirm_current()
        assert assist.rows[target_key]['accepted']
        assist.destroy()
        assert 'manual_face' not in dialog.edits.get(target_key, {})
        assert not saved
    finally:
        dialog.destroy()
        root.destroy()


def test_stopping_waits_for_worker_before_accept(monkeypatch, tmp_path):
    root, dialog, assets, saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key
    target_key = key(assets[1])
    release = threading.Event()
    def blocked(reference, reference_box, targets, stop_event, on_progress, **kwargs):
        on_progress(1, len(targets), FaceProposal(target_key, reference.key,
                    (.25,.3,.12,.15), 'review', .8, None, '待确认'))
        release.wait(5)
        return []
    monkeypatch.setattr(group_face_assist, 'propose_person_faces', blocked)
    try:
        dialog.assist_group_faces()
        assist = dialog._assist_dialog
        pump(assist, lambda: assist.rows[target_key]['box'] is not None)
        assist.tree.selection_set(target_key)
        assist.confirm_current()
        assist.stop_search()
        assist.accept_selected()
        assert assist._awaiting_done
        assert 'manual_face' not in dialog.edits.get(target_key, {})
        release.set()
        pump(root, lambda: dialog._assist_dialog is None)
        assert 'manual_face' in dialog.edits[target_key]
        assert not saved
    finally:
        release.set()
        dialog.destroy()
        root.destroy()


def test_changed_preview_is_not_overwritten(monkeypatch, tmp_path):
    root, dialog, assets, saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key
    target_key = key(assets[1])
    monkeypatch.setattr(group_face_assist, 'propose_person_faces',
                        fake_propose({target_key: (.25,.3,.12,.15)}))
    try:
        dialog.assist_group_faces()
        assist = dialog._assist_dialog
        pump(assist, lambda: assist._finished)
        assist.tree.selection_set(target_key)
        assist.confirm_current()
        Image.new('RGB', (200,300), 'red').save(assets[1].preview_path)
        assist.accept_selected()
        assert 'manual_face' not in dialog.edits.get(target_key, {})
        assert any('照片、预览或分组已改变' in message for _,_,message in ui.calls)
    finally:
        dialog.destroy()
        root.destroy()


def test_explicit_cross_group_scope_includes_other_group():
    reference = asset('ref', group_id=1)
    targets = collect_targets(reference, [reference, asset('other', group_id=2)], {},
                              lambda a: a.stem, allow_cross_group=True)
    assert len(targets) == 1 and targets[0].cross_group


def test_cross_group_confirm_merges_into_draft(monkeypatch, tmp_path):
    root, dialog, assets, saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key
    assets[1].group_id = 2
    target_key = key(assets[1])
    monkeypatch.setattr(group_face_assist, 'propose_person_faces', fake_propose({target_key: (.25,.3,.12,.15)}))
    try:
        dialog.assist_group_faces({1, 2})
        assist = dialog._assist_dialog
        pump(root, lambda: assist._finished)
        assist.tree.selection_set(target_key)
        assist.confirm_current()
        assist.accept_selected()
        pump(root, lambda: dialog._assist_dialog is None)
        assert 'manual_face' in dialog.edits[target_key]
        assert not saved
    finally:
        dialog.destroy()
        root.destroy()


def test_confirmation_label_and_direct_drawing(monkeypatch, tmp_path):
    root, dialog, assets, saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key(assets[1])
    monkeypatch.setattr(group_face_assist, 'propose_person_faces', fake_propose({key: (.25,.3,.12,.15)}))
    try:
        dialog.assist_group_faces()
        assist = dialog._assist_dialog
        pump(root, lambda: assist._finished)
        assist.tree.selection_set(key)
        assist.show_current()
        assist.confirm_current()
        assert assist.tree.item(key, 'values')[2] == '已确认'
        assist.tree.selection_set(key)
        assist.show_current()
        x,y,w,h,_,_ = assist._image_rect
        assist.pointer_down(SimpleNamespace(x=x+w*.6,y=y+h*.6))
        assist.pointer_up(SimpleNamespace(x=x+w*.8,y=y+h*.8))
        assert not assist.rows[key]['accepted']
        assert assist.tree.item(key, 'values')[2] == '手动调整'
        assert assist._selected_box_key is None
        assist.pointer_down(SimpleNamespace(x=x+w*.7,y=y+h*.7))
        assert assist._selected_box_key == key
    finally:
        dialog.destroy()
        root.destroy()


def test_clear_selected_faces_and_previous_unmarked(monkeypatch, tmp_path):
    root, dialog, assets, saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    try:
        key = dialog.global_settings().key(assets[0])
        dialog.clear_face_selection()
        assert dialog.edits[key]['selected_faces'] == []
        assert 'manual_face' not in dialog.edits[key]
        dialog.index = 1
        dialog.previous_unmarked()
        assert dialog.index == 0
    finally:
        dialog.destroy()
        root.destroy()


def test_review_shortcuts_advance_and_last_stays(monkeypatch, tmp_path):
    root, dialog, assets, saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key=dialog.global_settings().key
    target_key=key(assets[1]);second=key(assets[2])
    monkeypatch.setattr(group_face_assist,'propose_person_faces',fake_propose({target_key:(.25,.3,.12,.15),second:(.25,.3,.12,.15)}))
    try:
        dialog.assist_group_faces()
        assist=dialog._assist_dialog
        pump(root,lambda:assist._finished)
        assist.tree.selection_set(target_key)
        event=lambda key:SimpleNamespace(keysym=key,state=0,widget=assist.tree)
        assert assist._review_shortcut(event('Y'))=='break'
        assert assist.rows[target_key]['accepted']
        assert assist.tree.selection()[0]==second
        assist._review_shortcut(event('x'))
        assert assist.rows[second]['status']=='不是这个人'
        assert assist.tree.selection()[0]==second
        assist.tree.selection_set(target_key)
        assist._review_shortcut(event('p'))
        assert assist.rows[target_key]['status']=='跳过'
        assert not assist.rows[target_key]['accepted']
        assert assist.tree.selection()[0]==second
        assert not saved
        assist._awaiting_done=True
        assist._review_shortcut(event('y'))
        assert not assist.rows[second]['accepted']
    finally:
        dialog.destroy();root.destroy()


def test_multiple_automatic_faces_require_review_but_manual_choices_do_not():
    from ai_cull_assistant.subject import SubjectFeatures
    from ai_cull_assistant.crop_dialog import needs_person_review
    item=asset('multiple',subject=SubjectFeatures('','',None,(.1,.1,.1,.1),candidate_count=2,candidate_confidence=.8))
    assert needs_person_review(item,{})
    assert not group_face_assist._entry_located({},item)
    for entry in ({'selected_faces':[[.1,.1,.1,.1]]},{'selected_faces':[]},
                  {'hidden':True},{'manual_face':[.1,.1,.1,.1]}):
        assert not needs_person_review(item,entry)
        assert group_face_assist._entry_located(entry,item)
    assert collect_targets(asset('ref'),[asset('ref'),item],{},key_for)[0].key=='multiple'


def test_legacy_multiple_faces_index_then_choose_replaces_auto(monkeypatch,tmp_path):
    from ai_cull_assistant.subject import SubjectFeatures
    from ai_cull_assistant.crop_dialog import needs_person_review
    root,dialog,assets,saved,ui=open_dialog_with_manual_face(monkeypatch,tmp_path)
    try:
        wait_preview(dialog)
        item=assets[1]
        item.subject_features=SubjectFeatures('','',None,(.1,.1,.1,.1),(.08,.08,.15,.2))
        item.subject_checked=True;item.subject_confidence=.8
        def detect_with_numeric_threshold(image, threshold):
            assert isinstance(threshold, float)
            assert threshold == .8
            return [FaceDetection((40,60,40,60),(),.99), FaceDetection((200,120,40,60),(),.9)]
        monkeypatch.setattr(crop_dialog_module, 'detect', detect_with_numeric_threshold)
        dialog.next_unmarked()
        assert dialog._review_future is not None  # worker, no synchronous preview inference
        pump(root,lambda:dialog._review_future is None)
        assert dialog.index==1
        wait_preview(dialog)
        assert item.subject_features.candidate_count==2
        assert needs_person_review(item,dialog.current_entry())
        dialog._selected_boxes=[(.1,.1,.1,.1)]
        wanted=(.5,.2,.1,.1)
        dialog._toggle_selected(wanted)
        assert dialog.current_entry()['selected_faces']==[list(wanted)]
        assert not needs_person_review(item,dialog.current_entry())
        assert not saved
    finally:
        dialog.destroy();root.destroy()


def test_unmarked_index_failure_is_not_reported_as_empty_and_can_retry(monkeypatch, tmp_path):
    from ai_cull_assistant.subject import SubjectFeatures
    root, dialog, assets, saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    try:
        wait_preview(dialog)
        item = assets[1]
        item.subject_features = SubjectFeatures('', '', None, (.1,.1,.1,.1), (.08,.08,.15,.2))
        item.subject_checked = True
        item.subject_confidence = .8
        dialog.edits[dialog.global_settings().key(assets[2])] = {'hidden': True}
        def broken(*args):
            raise TypeError("Argument 'score_threshold' cannot be treated as a float")
        monkeypatch.setattr(crop_dialog_module, 'detect', broken)
        dialog.next_unmarked()
        pump(root, lambda: dialog._review_future is None)
        assert '检查未完成' in dialog.caption.cget('text')
        assert 'score_threshold' not in dialog.caption.cget('text')
        assert item.subject_features.candidate_count is None
        monkeypatch.setattr(crop_dialog_module, 'detect', lambda image, threshold: [FaceDetection((40,60,40,60),(),.99)])
        dialog.next_unmarked()
        pump(root, lambda: dialog._review_future is None)
        assert item.subject_features.candidate_count == 1
        assert '没有未标记' in dialog.caption.cget('text')
    finally:
        dialog.destroy(); root.destroy()
