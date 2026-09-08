"""Opt-in portable-build smoke test using temporary generated photos only."""
import json
from pathlib import Path
import tempfile
import traceback


def run(report_path: str) -> None:
    report = {}
    try:
        import cv2
        import rawpy
        import exifread
        from PIL import Image
        from .app import App
        from .screening import _face_cascade
        from .workflow import run_scan, apply_selection_text

        assert not _face_cascade().empty(), "Missing face detector data"
        app = App()
        app.withdraw()
        app.update()
        report["window_title"] = app.title()
        app.destroy()
        with tempfile.TemporaryDirectory(prefix="aicull-smoke-") as folder:
            root = Path(folder)
            photos = root / "photos"
            photos.mkdir()
            Image.new("RGB", (640, 480), "silver").save(photos / "TEST001.jpg")
            result = run_scan(photos, root / "workspace")
            assert len(result.assets) == 1
            assert result.main_pages and all(p.exists() for p in result.main_pages)
            apply_selection_text(result.assets, "TEST001,5", root / "selected")
            assert (photos / "TEST001.xmp").exists()
        report.update(ok=True, opencv=cv2.__version__, rawpy=rawpy.__version__)
    except Exception:
        report.update(ok=False, error=traceback.format_exc())
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["ok"]:
        raise SystemExit(1)
