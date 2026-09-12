from datetime import datetime
import json

from PIL import Image

from ai_cull_assistant.ai_project import ReviewProject, photo_id
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.lightroom_results import focus_review_status
from ai_cull_assistant.models import PhotoAsset


def asset(root, stem="中文照片"):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{stem}.jpg"
    Image.new("RGB", (80, 120), "gray").save(path)
    return PhotoAsset(stem, path, path, None, path, datetime.now(), ".jpg")


def project_for(tmp_path, assets):
    project = ReviewProject(tmp_path / "workspace")
    project.refresh(assets, CropSettings())
    return project


def test_export_preserves_photo_folder_and_existing_xmp(tmp_path):
    photo = asset(tmp_path / "photos", "TEST01")
    xmp = photo.primary_path.with_suffix(".xmp")
    xmp.write_bytes(b"existing edits")
    before = {path.name: path.read_bytes() for path in photo.primary_path.parent.iterdir()}
    project = project_for(tmp_path, [photo])
    project.confirm(photo_id(photo), 5, 0)

    output = json.loads(project.export_final().read_text("utf-8"))

    assert output["photos"][0]["rating"] == 5
    assert output["photos"][0]["pick_status"] == 0
    assert before == {path.name: path.read_bytes() for path in photo.primary_path.parent.iterdir()}


def test_raw_jpeg_exact_paths_and_duplicate_stems_are_safe(tmp_path):
    first = asset(tmp_path / "a")
    first.raw_path = first.primary_path.with_suffix(".RW2")
    first.raw_path.write_bytes(b"raw")
    second = asset(tmp_path / "b")
    project = project_for(tmp_path, [first, second])
    project.confirm(photo_id(first), 4, None)
    project.confirm(photo_id(second), 5, None)

    rows = json.loads(project.export_final().read_text("utf-8"))["photos"]

    assert len(rows) == 3
    assert {row["path"] for row in rows} == {
        str(path.resolve()) for photo in (first, second) for path in photo.rating_target_paths
    }


def test_api_focus_status_uses_existing_lightroom_keyword(tmp_path):
    photo = asset(tmp_path / "photos")
    photo.screening_reason = "ai_focus_uncertain"
    assert focus_review_status(photo) is True
    photo.screening_reason = "ai_focus_clear"
    assert focus_review_status(photo) is False
    photo.screening_reason = "ai_focus_blur"
    photo.auto_rejected = True
    photo.ai_focus_result = {
        "status": "blur",
        "reason": "双眼存在运动拖影",
        "source": "api",
    }
    project = project_for(tmp_path, [photo])

    row = json.loads(project.export_final(ai_ratings=True).read_text("utf-8"))["photos"][0]

    assert row["pick_status"] == -1
    assert row["focus_review"] is False
    assert row["ai_metadata"] == {
        "selection_reason": "",
        "review_items": "",
        "clarity_status": "模糊",
        "clarity_reason": "双眼存在运动拖影",
    }
    assert not list(photo.primary_path.parent.glob("*.xmp"))
