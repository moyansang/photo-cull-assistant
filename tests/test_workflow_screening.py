from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image

from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.screening import ScreeningResult
from ai_cull_assistant.workflow import apply_auto_rejects


def make_asset(tmp_path: Path, stem: str) -> PhotoAsset:
    p = tmp_path / f"{stem}.jpg"
    Image.new("RGB", (100, 100), "white").save(p)
    return PhotoAsset(
        stem=stem,
        display_path=p,
        primary_path=p,
        raw_path=None,
        jpg_path=p,
        captured_at=datetime(2026, 1, 1),
        ext=".jpg",
    )


def test_apply_auto_rejects_records_suggestions_without_sidecars(tmp_path):
    rejected = make_asset(tmp_path, "REJECTED")
    kept = make_asset(tmp_path, "KEPT")
    results = {
        "REJECTED": ScreeningResult(True, "obvious_subject_blur", True, 1.0, 10.0, (1, 1, 10, 10)),
        "KEPT": ScreeningResult(False, "subject_not_obviously_blurred", True, 20.0, 50.0, (1, 1, 10, 10)),
    }

    count = apply_auto_rejects([rejected, kept], results)

    assert count == 1
    assert rejected.auto_rejected
    assert not kept.auto_rejected
    assert not list(tmp_path.glob('*.xmp'))
