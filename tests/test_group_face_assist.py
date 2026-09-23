import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import tkinter as tk
from PIL import Image

from ai_cull_assistant import crop_dialog as crop_dialog_module
from ai_cull_assistant import group_face_assist
from ai_cull_assistant import group_face_assist_dialog
from ai_cull_assistant.crop_dialog import CropDialog
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.group_face_assist import (
    AssistImage,
    FaceProposal,
    clamp_box,
    collect_targets,
    normalized_box_ok,
    propose_group_faces,
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


# -------------------------------------------------------------- proposal math

BOX = (.40, .30, .12, .15)  # normalized reference face box
IMAGE_W, IMAGE_H = 1600, 1200


def _block(seed, w, h):
    return np.random.default_rng(seed).integers(30, 225, size=(h, w, 3), dtype=np.uint8)


def _save_scene(tmp_path, name, paste_blocks, size=(IMAGE_H, IMAGE_W)):
    image = np.full((*size, 3), 128, np.uint8)
    for (x, y, block) in paste_blocks:
        image[y:y + block.shape[0], x:x + block.shape[1]] = block
    path = tmp_path / f'{name}.jpg'
    Image.fromarray(image).save(path, quality=95)
    return path


def _ref_block():
    return _block(1, int(BOX[2] * IMAGE_W), int(BOX[3] * IMAGE_H))


def _reference(tmp_path):
    x, y = int(BOX[0] * IMAGE_W), int(BOX[1] * IMAGE_H)
    path = _save_scene(tmp_path, 'ref', [(x, y, _ref_block())])
    return AssistImage(key='ref', stem='ref', preview_path=str(path))


def _target(tmp_path, name, offset=(20, 10)):
    x, y = int(BOX[0] * IMAGE_W) + offset[0], int(BOX[1] * IMAGE_H) + offset[1]
    path = _save_scene(tmp_path, name, [(x, y, _ref_block())])
    return group_face_assist.AssistTarget(key=name, stem=name, preview_path=str(path))


def _fake_detector(record):
    def fake(image, score_threshold=.9, rotate_rescue=True):
        h, w = image.shape[:2]
        record.append({'threshold': score_threshold, 'rotate_rescue': rotate_rescue})
        return [FaceDetection((w * .15, h * .15, w * .7, h * .7), (), .93)]
    return fake


def test_propose_matches_shifted_face_and_returns_reliable(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(group_face_assist, 'detect', _fake_detector(calls))
    reference = _reference(tmp_path)
    target = _target(tmp_path, 't1')
    proposals = propose_group_faces(reference, BOX, [target], threading.Event())
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.level == 'reliable', proposal.reason
    assert proposal.box is not None
    x, y, _, _ = proposal.box
    assert abs(x - (BOX[0] + 20 / IMAGE_W)) < .05
    assert abs(y - (BOX[1] + 10 / IMAGE_H)) < .05
    assert normalized_box_ok(list(proposal.box))
    # Local verification skips rotation and defaults to the global default.
    assert calls and all(call['rotate_rescue'] is False for call in calls)
    assert all(call['threshold'] == .8 for call in calls)


def test_propose_matches_same_normalized_position_across_preview_sizes(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(group_face_assist, 'detect', _fake_detector(calls))
    reference = _reference(tmp_path)

    target_width, target_height = IMAGE_W // 2, IMAGE_H // 2
    target_block = np.asarray(Image.fromarray(_ref_block()).resize(
        (int(BOX[2] * target_width), int(BOX[3] * target_height)),
        Image.Resampling.LANCZOS,
    ))
    x = int(BOX[0] * target_width)
    y = int(BOX[1] * target_height)
    target_path = _save_scene(
        tmp_path, 'scaled_target', [(x, y, target_block)],
        size=(target_height, target_width),
    )
    target = group_face_assist.AssistTarget(
        key='scaled_target', stem='scaled_target', preview_path=str(target_path))

    proposals = propose_group_faces(reference, BOX, [target], threading.Event())

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.level == 'reliable', proposal.reason
    assert proposal.box is not None
    px, py, pw, ph = proposal.box
    assert abs(px - BOX[0]) < .05
    assert abs(py - BOX[1]) < .05
    assert abs(pw - BOX[2]) < .05
    assert abs(ph - BOX[3]) < .05
    assert calls


def test_detect_failure_for_one_target_does_not_block_later_targets(monkeypatch, tmp_path):
    calls = 0

    def flaky_detector(image, score_threshold=.9, rotate_rescue=True):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('detector failed for this target')
        height, width = image.shape[:2]
        return [FaceDetection((width * .15, height * .15, width * .7, height * .7), (), .93)]

    monkeypatch.setattr(group_face_assist, 'detect', flaky_detector)
    reference = _reference(tmp_path)
    targets = [_target(tmp_path, 'broken'), _target(tmp_path, 'later')]
    progress = []

    proposals = propose_group_faces(
        reference, BOX, targets, threading.Event(),
        on_progress=lambda done, total, proposal: progress.append((done, total, proposal.target_key)),
    )

    assert [proposal.target_key for proposal in proposals] == ['broken', 'later']
    assert proposals[0].level in ('missing', 'review')
    assert proposals[1].level == 'reliable', proposals[1].reason
    assert calls == 2
    assert progress == [(1, 2, 'broken'), (2, 2, 'later')]


def test_grading_rules(monkeypatch, tmp_path):
    reference = _reference(tmp_path)
    target = _target(tmp_path, 'g1')
    box_px = (int(BOX[0] * IMAGE_W), int(BOX[1] * IMAGE_H),
              int(BOX[2] * IMAGE_W), int(BOX[3] * IMAGE_H))

    def fake_confirmed(image, *args, **kwargs):
        h, w = image.shape[:2]
        return [FaceDetection((w * .05, h * .05, w * .9, h * .9), (), .91)]

    def run(peaks, confirmed):
        monkeypatch.setattr(group_face_assist, '_match_peaks', lambda *a, **k: list(peaks))
        monkeypatch.setattr(group_face_assist, 'detect',
                            fake_confirmed if confirmed else (lambda *a, **k: []))
        proposals = propose_group_faces(reference, BOX, [
            group_face_assist.AssistTarget(key='g1', stem='g1',
                                           preview_path=target.preview_path)], threading.Event())
        return proposals[0]

    strong = [(0.85,) + tuple(float(v) for v in box_px)]
    rival = [(0.80, float(box_px[0] + 15), float(box_px[1] + 20), float(box_px[2]), float(box_px[3]))]
    # High, unique, detector-confirmed -> reliable.
    assert run(strong, True).level == 'reliable'
    # Two close-score candidates -> never reliable, even with a detection.
    proposal = run(strong + rival, True)
    assert proposal.level == 'review'
    assert '多个' in proposal.reason
    # Detector finds nothing at the match location -> review, keep match box.
    proposal = run(strong, False)
    assert proposal.level == 'review'
    assert '未通过人脸检测' in proposal.reason
    assert proposal.box is not None
    # Mid-strength unique match with a detection -> review (low match score).
    weak = [(0.60,) + tuple(float(v) for v in box_px)]
    assert run(weak, True).level == 'review'
    # No peaks at all -> missing with no box.
    proposal = run([], True)
    assert proposal.level == 'missing'
    assert proposal.box is None


def test_propose_handles_unreadable_and_weak_targets(monkeypatch, tmp_path):
    monkeypatch.setattr(group_face_assist, 'detect', lambda *a, **k: [])
    reference = _reference(tmp_path)
    weak = _save_scene(tmp_path, 'weak', [(100, 100, _block(99, 150, 150))])
    targets = [
        group_face_assist.AssistTarget(key='nofile', stem='nofile',
                                       preview_path=str(tmp_path / 'gone.jpg')),
        group_face_assist.AssistTarget(key='weak', stem='weak', preview_path=str(weak)),
    ]
    progress = []
    proposals = propose_group_faces(
        reference, BOX, targets, threading.Event(),
        on_progress=lambda done, total, proposal: progress.append((done, total)))
    assert proposals[0].level == 'missing'
    assert '预览缺失' in proposals[0].reason
    assert proposals[0].box is None
    assert proposals[1].level in ('missing', 'review')
    assert progress == [(1, 2), (2, 2)]


def test_propose_stops_early_on_stop_event(monkeypatch, tmp_path):
    monkeypatch.setattr(group_face_assist, 'detect', lambda *a, **k: [])
    reference = _reference(tmp_path)
    targets = [_target(tmp_path, f's{i}') for i in range(4)]
    stop = threading.Event()
    stop.set()
    assert propose_group_faces(reference, BOX, targets, stop) == []


def test_propose_rejects_unreadable_reference(tmp_path):
    reference = AssistImage(key='r', stem='r', preview_path=None)
    with pytest.raises(ValueError):
        propose_group_faces(reference, BOX, [], threading.Event())


def test_propose_rejects_low_texture_reference(tmp_path):
    path = tmp_path / 'flat-reference.jpg'
    Image.new('RGB', (IMAGE_W, IMAGE_H), (128, 128, 128)).save(path)
    reference = AssistImage(key='flat', stem='flat', preview_path=str(path))
    with pytest.raises(ValueError):
        propose_group_faces(reference, BOX, [], threading.Event())


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


def test_assist_dialog_adopts_into_parent_edits(monkeypatch, tmp_path):
    root, dialog, assets, saved, _ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key
    proposals = {key(assets[1]): (.25, .30, .12, .15), key(assets[2]): (.25, .30, .12, .15)}
    monkeypatch.setattr(group_face_assist, 'propose_group_faces', fake_propose(proposals))
    dialog_ui = Recorder()
    dialog_ui.install(monkeypatch, group_face_assist_dialog)
    try:
        dialog.assist_group_faces()
        assist = dialog._assist_dialog
        assert assist is not None
        pump(assist, lambda: all(row['proposal'] is not None for row in assist.rows.values()))
        target_key = key(assets[1])
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


def test_assist_dialog_cancel_writes_nothing(monkeypatch, tmp_path):
    root, dialog, assets, saved, _ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key
    monkeypatch.setattr(group_face_assist, 'propose_group_faces',
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
    monkeypatch.setattr(group_face_assist, 'propose_group_faces', fake_propose({
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
    monkeypatch.setattr(group_face_assist, 'propose_group_faces',
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
    monkeypatch.setattr(group_face_assist, 'propose_group_faces',
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
    monkeypatch.setattr(group_face_assist, 'propose_group_faces', blocked)
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
    monkeypatch.setattr(group_face_assist, 'propose_group_faces',
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


def test_cross_group_candidate_never_auto_accepts_identity(monkeypatch, tmp_path):
    import numpy as np
    from dataclasses import replace
    path = tmp_path / 'face.jpg'
    Image.new('RGB', (100, 100), 'white').save(path)
    target = group_face_assist.AssistTarget(key='other', stem='other', preview_path=str(path), cross_group=True)
    monkeypatch.setattr(group_face_assist, '_match_peaks', lambda *a: [(0.99, 20, 20, 20, 20)])
    monkeypatch.setattr(group_face_assist, '_verify_with_detector', lambda *a: ((20, 20, 20, 20), .99))
    template = np.ones((20, 20), dtype=np.uint8)
    result = group_face_assist._propose_for_target(template, template, target, (.2,.2,.2,.2),
                                                  'ref', threading.Event(), .8)
    assert result.level == 'review'
    assert '同一人物' in result.reason


def test_cross_group_confirm_merges_into_draft(monkeypatch, tmp_path):
    root, dialog, assets, saved, ui = open_dialog_with_manual_face(monkeypatch, tmp_path)
    key = dialog.global_settings().key
    assets[1].group_id = 2
    target_key = key(assets[1])
    monkeypatch.setattr(group_face_assist, 'propose_group_faces', fake_propose({target_key: (.25,.3,.12,.15)}))
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
