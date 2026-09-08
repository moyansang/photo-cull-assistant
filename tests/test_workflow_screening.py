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


def test_apply_auto_rejects_writes_lightroom_reject_flag_only_for_rejected(tmp_path):
    rejected = make_asset(tmp_path, "REJECTED")
    kept = make_asset(tmp_path, "KEPT")
    results = {
        "REJECTED": ScreeningResult(True, "obvious_subject_blur", True, 1.0, 10.0, (1, 1, 10, 10)),
        "KEPT": ScreeningResult(False, "subject_not_obviously_blurred", True, 20.0, 50.0, (1, 1, 10, 10)),
    }

    count = apply_auto_rejects([rejected, kept], results)

    assert count == 1
    xmp = tmp_path / "REJECTED.xmp"
    assert xmp.exists()
    content = xmp.read_text(encoding="utf-8")
    assert 'xmpDM:pick="-1"' in content
    assert 'xmpDM:good="false"' in content
    assert 'xmp:Rating="-1"' not in content
    assert not (tmp_path / "KEPT.xmp").exists()
