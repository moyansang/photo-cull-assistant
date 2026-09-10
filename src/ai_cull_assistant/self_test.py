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
        from .yunet import detect
        import numpy as np
        from .workflow import run_scan, apply_selection_text

        assert not _face_cascade().empty(), "Missing face detector data"
        assert detect(np.zeros((320, 320, 3), np.uint8)) == []
        report["yunet_model_inference"] = True
        with tempfile.TemporaryDirectory(prefix="aicull-smoke-") as folder:
            root = Path(folder)
            app = App(settings_dir=root)
            app.withdraw()
            app.update()
            report["window_title"] = app.title()
            from .app import GROUPING_LABELS
            assert app.preset_var.get() == "标准"
            assert list(GROUPING_LABELS.values()) == ["strict", "standard", "loose"]
            assert app.workspace_var.get() == str(root)
            assert app.export_var.get() == str(root)
            app.input_var.set(str(root / "测试照片"))
            app.workspace_var.set(str(root / "自选工作区"))
            app.export_var.set(str(root / "自选精选"))
            app._close()
            reopened = App(settings_dir=root)
            reopened.withdraw()
            assert reopened.input_var.get() == str(root / "测试照片")
            assert reopened.workspace_var.get() == str(root / "自选工作区")
            assert reopened.export_var.get() == str(root / "自选精选")
            reopened._close()
            report["directory_persistence"] = True
            photos = root / "photos"
            photos.mkdir()
            Image.new("RGB", (640, 480), "silver").save(photos / "TEST001.jpg")
            result = run_scan(photos, root / "workspace")
            assert len(result.assets) == 1
            assert result.main_pages and all(p.exists() for p in result.main_pages)
            from .crop_dialog import CropDialog
            from .crop_settings import CropSettings
            from .subject import SubjectFeatures
            test_app = App(settings_dir=root)
            test_app.withdraw()
            test_app.scan_result = result
            result.assets[0].subject_checked = True
            result.assets[0].subject_features = SubjectFeatures('0', '0', '0', (.3,.2,.2,.2), (.2,.1,.4,.4))
            dialog = CropDialog(test_app, result.assets, test_app.crop_settings, test_app._save_crop_settings)
            dialog.scale.set(1.25)
            dialog.shift.set(-.15)
            dialog.ratio.set('1:1')
            dialog.render()
            assert len(dialog.photos) == 2
            dialog.confidence.set(.75)
            dialog.render()
            assert result.assets[0].subject_confidence == .75
            assert len(dialog.photos) == 1  # Blank synthetic photo is still rejected.
            dialog.navigate(1)
            dialog.save()
            test_app._close()
            check_app = App(settings_dir=root)
            check_app.withdraw()
            assert check_app.crop_settings.detection_confidence == .75
            assert check_app.crop_settings.for_asset(result.assets[0]) == CropSettings(1.25, -.15, '1:1', .75)
            check_app._close()
            report['crop_settings_dialog_and_persistence'] = True
            apply_selection_text(result.assets, "TEST001,5", root / "selected")
            assert (photos / "TEST001.xmp").exists()
        report.update(ok=True, opencv=cv2.__version__, rawpy=rawpy.__version__)
    except Exception:
        report.update(ok=False, error=traceback.format_exc())
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["ok"]:
        raise SystemExit(1)
