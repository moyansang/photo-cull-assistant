from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image

from ai_cull_assistant.contact_sheet import generate_contact_sheet_sets, page_filename, _detail_rail_labels
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.crop_settings import CropSettings


def make_asset(tmp_path: Path, stem: str, group_id: int, rejected: bool = False) -> PhotoAsset:
    preview = tmp_path / f"{stem}_preview.jpg"
    Image.new("RGB", (600, 900), "white").save(preview)
    original = tmp_path / f"{stem}.jpg"
    Image.new("RGB", (600, 900), "white").save(original)
    return PhotoAsset(
        stem=stem,
        display_path=original,
        primary_path=original,
        raw_path=None,
        jpg_path=original,
        captured_at=datetime(2026, 1, 1),
        ext=".jpg",
        group_id=group_id,
        preview_path=preview,
        auto_rejected=rejected,
        screening_reason="obvious_subject_blur" if rejected else None,
    )


def test_page_filename_contains_page_group_and_photo_range(tmp_path):
    assets = [make_asset(tmp_path, "P1111001", 2), make_asset(tmp_path, "P1111008", 4)]
    name = page_filename(assets, 3)
    assert name == "sheet_003_G002-G004_P1111001-P1111008.jpg"


def test_generate_contact_sheet_sets_separates_main_and_rejected(tmp_path):
    assets = [
        make_asset(tmp_path, "P1111001", 1),
        make_asset(tmp_path, "P1111002", 1, rejected=True),
        make_asset(tmp_path, "P1111003", 2),
    ]
    out = tmp_path / "contact_sheets"

    result = generate_contact_sheet_sets(assets, out, photos_per_page=16, columns=4)

    assert result.main_pages
    assert result.rejected_pages
    assert all(p.parent.name == "main" for p in result.main_pages)
    assert all(p.parent.name == "rejected_review" for p in result.rejected_pages)
    assert all("P1111002" not in p.name for p in result.main_pages)
    assert any("P1111002" in p.name for p in result.rejected_pages)


def test_main_contact_sheet_width_is_chat_friendly(tmp_path):
    assets = [make_asset(tmp_path, f"P11110{i:02d}", 1) for i in range(4)]
    out = tmp_path / "contact_sheets"

    result = generate_contact_sheet_sets(assets, out, photos_per_page=16, columns=4)

    with Image.open(result.main_pages[0]) as image:
        assert 1950 <= image.width <= 2100


def test_head_only_inset_can_render_without_claiming_face(tmp_path, monkeypatch):
    from ai_cull_assistant import contact_sheet
    from ai_cull_assistant.subject import SubjectFeatures
    asset = make_asset(tmp_path, 'HEAD_ONLY', 1)
    head = SubjectFeatures('a','b',None,None,(.3,.2,.2,.3), head_source='head_detector')
    monkeypatch.setattr(contact_sheet, 'detail_features', lambda *a: head)
    crops = []
    def crop(image, face, bounds, settings):
        crops.append((face, bounds))
        return Image.new('RGB',(124,150),'red')
    monkeypatch.setattr(contact_sheet, 'face_crop', crop)
    result = generate_contact_sheet_sets([asset], tmp_path/'sheets')
    assert result.main_pages and crops == [(None, head.head)]


def test_selected_people_each_get_a_detail_crop_in_persisted_order(tmp_path, monkeypatch):
    from ai_cull_assistant import contact_sheet
    from ai_cull_assistant.subject import SubjectFeatures
    asset = make_asset(tmp_path, 'MULTI', 1)
    key = str(asset.primary_path.resolve()).casefold()
    boxes = [(.1,.1,.2,.2), (.6,.2,.2,.2)]
    from ai_cull_assistant.crop_settings import face_box_key
    settings = CropSettings(photos={key: {
        'selected_faces': [list(box) for box in boxes],
        'face_crops': {
            face_box_key(boxes[0]): {'scale_factor': 1.2},
            face_box_key(boxes[1]): {'scale_factor': 1.8},
        },
    }})
    subjects = [SubjectFeatures('a','b',None,box,box) for box in boxes]
    monkeypatch.setattr(contact_sheet, 'detail_features_list', lambda *a: subjects)
    crops = []
    def crop(_image, face, _head, person_settings):
        crops.append((face, person_settings.scale_factor))
        return Image.new('RGB',(124,150),'red')
    monkeypatch.setattr(contact_sheet, 'face_crop', crop)

    result = generate_contact_sheet_sets([asset], tmp_path/'sheets', crop_settings=settings)

    assert result.main_pages and crops == [(boxes[0], 1.2), (boxes[1], 1.8)]


def test_more_than_four_details_label_total_and_remaining():
    assert _detail_rail_labels(6) == ('SELECTED TOTAL: 6', 'REMAINING DETAILS: 2')
    assert _detail_rail_labels(4) == ('SELECTED TOTAL: 4', None)
