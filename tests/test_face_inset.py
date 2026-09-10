from datetime import datetime

from PIL import Image

from ai_cull_assistant import contact_sheet
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.subject import SubjectFeatures, face_crop


def test_inset_renders_in_separate_area_and_does_not_crop_photo(tmp_path, monkeypatch):
    image = Image.new('RGB', (400, 800), 'blue')
    image.paste('red', (140, 80, 240, 180))
    image.paste('green', (0, 760, 400, 800))
    preview = tmp_path / 'sample.png'
    image.save(preview)
    monkeypatch.setattr('ai_cull_assistant.subject.features', lambda p, *args: SubjectFeatures('0', '0', '0', (.35, .1, .25, .125)))
    asset = PhotoAsset('TEST', preview, preview, None, preview, datetime(2026, 1, 1), '.png', group_id=1, preview_path=preview)
    page = contact_sheet.generate_contact_sheets([asset], tmp_path / 'out')[0]
    with Image.open(page) as rendered:
        # Red face exists in both the whole-photo column and the inset column.
        y0 = contact_sheet.MARGIN + contact_sheet.PAGE_HEADER_H + contact_sheet.GROUP_HEADER_H
        for x in (contact_sheet.MARGIN + 170, contact_sheet.MARGIN + 410):
            r, g, b = rendered.getpixel((x, y0 + 70))
            assert r > 170 and b < 70
        r, g, b = rendered.getpixel((contact_sheet.MARGIN + 170, y0 + 380))
        assert g > 70 and b < 70  # Full photo's bottom remains visible.


def test_face_crop_clips_at_image_edges():
    crop = face_crop(Image.new('RGB', (100, 100)), (0, 0, .4, .4))
    assert crop.size == (50, 52)
