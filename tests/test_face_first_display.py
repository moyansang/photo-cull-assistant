"""Exercise real render/edit methods on synthetic pixels, without a Tk server."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from ai_cull_assistant.body_subject_dialog import (
    BodySubjectDialog, effective_face_boxes, selected_body_boxes, visible_body_boxes,
)
from ai_cull_assistant.crop_dialog import CropDialog
from ai_cull_assistant.crop_settings import CropSettings

FACE = (.2, .1, .2, .2)
BODY = (.1, .1, .5, .8)


def asset():
    return NS(subject_features=NS(face=FACE), clarity_evidence={'body_candidates': [{'box': BODY}]})


def test_hidden_bodies_are_retained_and_manual_faces_override_cached_faces():
    a = asset()
    entry = {'selected_bodies': [list(BODY)]}
    before = deepcopy(entry)
    assert visible_body_boxes(a, entry) == []
    assert selected_body_boxes(a, entry) == [BODY]
    assert entry == before
    a.subject_features.face = None
    assert visible_body_boxes(a, dict(entry, manual_face=list(FACE))) == []
    assert visible_body_boxes(a, entry) == [BODY]


@pytest.mark.parametrize('entry, expected', [
    ({'selected_faces': []}, []),
    ({'selected_faces': [], 'selected_bodies': [BODY]}, [BODY]),
    ({'selected_faces': [], 'roi_selection_schema': 2}, [BODY]),
    ({'selected_faces': [], 'selected_bodies': []}, []),
    ({'hidden': True, 'selected_bodies': [BODY]}, []),
    ({'subjects_disabled': True, 'selected_bodies': [BODY]}, []),
])
def test_disabled_faces_and_legacy_empty_policy(entry, expected):
    assert visible_body_boxes(asset(), entry) == expected


def test_prepared_faces_override_stale_asset_but_not_manual_selection():
    a = asset()
    assert visible_body_boxes(a, {}, face_boxes=[]) == [BODY]
    assert effective_face_boxes(a, {'selected_faces': []}, face_boxes=[FACE]) == []
    assert effective_face_boxes(a, {'manual_face': FACE}, face_boxes=[]) == [FACE]


def test_restore_faces_preserves_hidden_manual_body_selection():
    entry = {'selected_faces': [], 'selected_bodies': [list(BODY)]}
    fake = NS(assets=[asset()], current_entry=lambda: entry,
              load_current=lambda: None, render=lambda: None)
    CropDialog.auto_face(fake)
    assert entry == {'selected_bodies': [list(BODY)]}


@pytest.mark.parametrize('method', ['apply', 'restore_auto', 'select_all', 'clear', 'pointer_down', 'pointer_up', 'pointer_move'])
def test_hidden_body_editor_actions_never_change_or_commit_selection(method):
    calls = []
    fake = NS(_face_first=True, selected=[BODY], on_apply=calls.append)
    args = [NS(x=5, y=5)] if method.startswith('pointer') else []
    getattr(BodySubjectDialog, method)(fake, *args)
    assert fake.selected == [BODY] and calls == []


class Widget:
    def __init__(self):
        self.text = ''
    def configure(self, **kw):
        self.text = kw.get('text', self.text)
    def delete(self, *a):
        pass
    def create_image(self, *a, **kw):
        pass
    def create_text(self, *a, **kw):
        pass
    def create_rectangle(self, *a, **kw):
        pass
    def winfo_width(self):
        return 400
    def winfo_height(self):
        return 400
    def set(self, value):
        pass
    def get(self):
        return 1.


@pytest.mark.parametrize('with_face', [True, False])
def test_real_crop_render_draws_only_one_roi_kind(monkeypatch, with_face):
    from ai_cull_assistant import crop_dialog
    original = Image.new('RGB', (200, 200), '#888888')
    boxes = {'selected_faces': [list(FACE)] if with_face else [], 'selected_bodies': [list(BODY)]}
    before = deepcopy(boxes)
    settings = NS(photos={'a': boxes}, key=lambda a: 'a')
    a = NS(stem='synthetic', group_id=1, subject_features=NS(face=FACE, candidate_count=1),
           clarity_evidence={}, analysis_mode='portrait')
    subjects = [NS(face=FACE, head=FACE)] if with_face else []
    fake = NS(_pending=None, assets=[a], index=0, _crop_selected=False, _active_face_key=None,
              store_current=lambda: None, photos=[], canvas=Widget(), detail_canvas=Widget(),
              participant_status=Widget(), caption=Widget(), scale=Widget(), person=Widget(),
              person_picker=Widget(), global_settings=lambda: settings, _original_photos={},
              _prepared_preview=lambda *_: (original, subjects, [NS(box=(40,20,40,40), score=.9)]),
              settings=lambda: CropSettings(), _same_face=CropDialog._same_face)
    drawn = []
    actual_draw = crop_dialog.ImageDraw.Draw
    class DrawSpy:
        def __init__(self, im):
            self.real = actual_draw(im)
        def rectangle(self, bounds, **kw):
            drawn.append(kw['outline'])
            return self.real.rectangle(bounds, **kw)
        def text(self, *a, **kw):
            return self.real.text(*a, **kw)
    monkeypatch.setattr(crop_dialog.ImageDraw, 'Draw', DrawSpy)
    monkeypatch.setattr(crop_dialog.ImageTk, 'PhotoImage', lambda image, **kw: image.copy())
    CropDialog.render(fake)
    assert boxes == before
    assert ('#cc77ff' in drawn) is not with_face
    assert any(color in drawn for color in ('#00aa66', '#ff9900')) is with_face
    if not with_face:
        assert '#3399ff' not in drawn
        assert fake._crop_rect is None
    original.close()


def test_prepare_does_not_silently_reselect_legacy_disabled_bodies():
    from concurrent.futures import Future
    future = Future()
    original = Image.new('RGB', (100, 100))
    future.set_result((original, [BODY]))
    fake = NS(_closed=False, asset=asset(), _future=future, entry={'selected_faces': []}, selected=[],
              render=lambda: None)
    BodySubjectDialog._finish_prepare(fake)
    assert fake.selected == []
    original.close()


def test_real_body_dialog_render_hides_bodies_without_clearing_them(monkeypatch):
    from ai_cull_assistant import body_subject_dialog
    original = Image.new('RGB', (200, 200), '#888888')
    fake = NS(_closed=False, original=original, _face_first=True, face_boxes=[FACE],
              selected=[BODY], candidates=[BODY], canvas=Widget(), status=Widget())
    drawn = []
    actual_draw = body_subject_dialog.ImageDraw.Draw
    class DrawSpy:
        def __init__(self, im):
            self.real = actual_draw(im)
        def rectangle(self, bounds, **kw):
            drawn.append(bounds)
            return self.real.rectangle(bounds, **kw)
        def text(self, *a, **kw):
            return self.real.text(*a, **kw)
    monkeypatch.setattr(body_subject_dialog.ImageDraw, 'Draw', DrawSpy)
    monkeypatch.setattr(body_subject_dialog.ImageTk, 'PhotoImage', lambda image, **kw: image.copy())
    BodySubjectDialog.render(fake)
    assert drawn == [(40., 20., 80., 60.00000000000001)]
    assert fake.selected == [BODY]
    assert '身体选择保留' in fake.status.text
    original.close()
