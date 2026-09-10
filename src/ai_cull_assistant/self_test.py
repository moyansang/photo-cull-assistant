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
        from .workflow import run_scan

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
            app.input_var.set(str(root / "测试照片"))
            app.workspace_var.set(str(root / "自选工作区"))
            app._close()
            reopened = App(settings_dir=root)
            reopened.withdraw()
            assert reopened.input_var.get() == str(root / "测试照片")
            assert reopened.workspace_var.get() == str(root / "自选工作区")
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
            from .ai_project import ReviewProject, photo_id
            from .ai_review_ui import ReviewDialog
            ui_app = App(settings_dir=root)
            ui_app.withdraw()
            project = ReviewProject(root / 'workspace')
            task = project.create_task(result.assets, ui_app.crop_settings, {'intensity':'均衡保留'})
            batch = task['batches'][0]
            response = json.dumps(dict(task_id=task['id'], batch_id=batch['id'], photos=[dict(
                photo_id=photo_id(result.assets[0]), rating=4, suggest_reject=False,
                reason='合成测试', review_items=['原图复核'])]))
            assert project.ingest(task, batch, response) == []
            review = ReviewDialog(ui_app, project, result.assets, ui_app.crop_settings, root)
            review.update()
            assert len(review.review_tree.get_children()) == 1
            assert [review.notebook.tab(t, "text") for t in review.notebook.tabs()] == ["API 提交", "网页提交", "人工复核"]
            submission = project.create_web_submission(task, [batch["id"]])
            review._refresh_web(submission["id"])
            assert project.web_images(task, submission)
            report["merged_web_submission"] = True
            review.rating_var.set('5')
            review.pick_var.set('保持原标记')
            review._confirm_photo()
            export = project.export_final()
            assert project.data['photos'][photo_id(result.assets[0])]['final']['rating'] == 5
            review._close()
            ui_app._close()
            report['ai_task_review_and_confirmation'] = True
            from .ai_api_dialog import ApiConfigDialog
            from .ai_presets import PRESETS, preset_profile
            config_root = App(settings_dir=root)
            config_root.withdraw()
            config = ApiConfigDialog(config_root, root)
            config.update()
            assert len(PRESETS) == 5
            assert config.model_var.get() == preset_profile('qwen-vl-plus')['model']
            config.destroy()
            config_root._close()
            report['api_preset_dialog'] = True
            assert not (photos / "TEST001.xmp").exists()
            payload = json.loads(export.read_text(encoding="utf-8"))
            assert any(p.get("rating") == 5 for p in payload["photos"])
            report["lightroom_export_without_xmp"] = True
        report.update(ok=True, opencv=cv2.__version__, rawpy=rawpy.__version__)
    except Exception:
        report.update(ok=False, error=traceback.format_exc())
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["ok"]:
        raise SystemExit(1)
