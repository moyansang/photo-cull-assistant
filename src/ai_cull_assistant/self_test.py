"""Opt-in portable-build smoke test using temporary generated photos only."""
import json
from pathlib import Path
import tempfile
import time
import traceback


def run(report_path: str) -> None:
    report = {}
    try:
        import cv2
        import rawpy
        import exifread
        from PIL import Image
        from .app import App
        from .yunet import detect
        import numpy as np
        from .workflow import run_scan

        assert detect(np.zeros((320, 320, 3), np.uint8)) == []
        report["yunet_model_inference"] = True
        import sys
        if getattr(sys, 'frozen', False):
            from .body_focus import _get_models
            detector, pose = _get_models()
            blank = np.zeros((320, 320, 3), np.uint8)
            assert len(detector.infer(blank)) == 0
            pose.infer(blank, np.asarray([100, 40, 180, 100, 140, 200, 140, 40,
                                         140, 100, 140, 40, .99], dtype=np.float32))
            report['bundled_body_models_inference'] = True
        with tempfile.TemporaryDirectory(prefix="aicull-smoke-") as folder:
            root = Path(folder)
            app = App(settings_dir=root)
            app.withdraw()
            app.update()
            report["window_title"] = app.title()
            from .app import GROUPING_LABELS
            assert app.preset_var.get() == "标准"
            assert not app.body_screening_var.get()
            assert list(GROUPING_LABELS.values()) == ["strict", "standard", "loose"]
            assert app.workspace_var.get() != str(root)
            (root / "测试照片").mkdir()
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
            from .face_focus import assess_asset_focus, VERSION as FOCUS_VERSION
            focus_settings = CropSettings()
            focus_settings.photos[focus_settings.key(result.assets[0])] = {'manual_face': [.1, .1, .8, .8]}
            focus = assess_asset_focus(result.assets[0], crop_settings=focus_settings, cache_dir=root / 'focus-cache')
            assert focus.analysis_version == FOCUS_VERSION and focus.face_found
            assert focus.reason == 'face_focus_uncertain' and not focus.rejected
            assert list((root / 'focus-cache').rglob('*.json'))
            report['native_face_analysis_and_cache'] = True
            test_app = App(settings_dir=root)
            test_app.withdraw()
            test_app.input_var.set(str(photos))
            test_app.workspace_var.set(str(root / "workspace"))
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
            deadline = time.monotonic() + 15
            while test_app._processing_busy and time.monotonic() < deadline:
                test_app.update(); time.sleep(.02)
            assert not test_app._processing_busy
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
            # Blank synthetic input must fail clarity admission. A deliberately
            # injected clear verdict below isolates the selection UI smoke test.
            assert not ReviewProject._asset_is_admitted(result.assets[0])
            result.assets[0].clarity_version = 'clarity-v2'
            result.assets[0].clarity_evidence = {'state': 'clear'}
            result.assets[0].screening_reason = 'subject_not_obviously_blurred'
            result.assets[0].ai_focus_result = None
            task = project.create_task(result.assets, ui_app.crop_settings, {'intensity':'均衡保留'})
            batch = task['batches'][0]
            response = json.dumps(dict(task_id=task['id'], batch_id=batch['id'], photos=[dict(
                photo_id=photo_id(result.assets[0]), rating=4, suggest_reject=False,
                reason='合成测试', review_items=['原图复核'])]))
            assert project.ingest(task, batch, response) == []
            review = ReviewDialog(ui_app, project, result.assets, ui_app.crop_settings, root)
            deadline = time.monotonic() + 15
            while review._preparing_task and time.monotonic() < deadline:
                review.update(); time.sleep(.02)
            assert not review._preparing_task
            task = project.current_task(); batch = task["batches"][0]
            review.notebook.select(review.review_tab)
            review.update()
            assert len(review.review_tree.get_children()) == 1
            assert [review.notebook.tab(t, "text") for t in review.notebook.tabs()] == ["API 选片", "网页选片", "选片结果"]
            submission = project.create_web_submission(task, [batch["id"]])
            review._refresh_web(submission["id"])
            assert project.web_images(task, submission)
            report["merged_web_submission"] = True

            response = json.dumps(dict(task_id=task['id'], batch_id=batch['id'], photos=[dict(
                photo_id=photo_id(result.assets[0]), rating=5, suggest_reject=False,
                reason='合成测试', review_items=[])]))
            assert project.ingest(task, batch, response) == []
            export = project.export_final(ai_ratings=True)
            review._close()
            from .ai_api_dialog import ApiConfigDialog
            ui_app._open_api_config()
            editor = ui_app._api_config_window
            ui_app.update()
            assert ui_app.grab_current() is None
            editor.withdraw(); ui_app.update()
            assert ui_app.grab_current() is None
            ui_app._open_api_config(); ui_app.update()
            assert ui_app._api_config_window is editor
            editor.destroy(); ui_app.update()
            assert not editor.winfo_exists()
            report['api_editor_background_recovery'] = True

            ui_app._close()
            report['ai_task_results_and_export'] = True
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
            from .processing_job import start_job, load_job
            from threading import Event
            options = dict(grouping_preset='standard', photos_per_page=8, columns=2, technical_screening=False)
            resumed_workspace = root / 'resume-smoke'
            job = start_job(photos, resumed_workspace, options, CropSettings())
            stop = Event()
            def stop_after_unit(percent):
                if percent > 0: stop.set()
            assert job.run(options, CropSettings(), stop, stop_after_unit) is None
            loaded = load_job(resumed_workspace, photos)
            assert loaded is not None
            stop.clear()
            completed = loaded.run(options, CropSettings(), stop, lambda percent: None)
            assert completed is not None and completed.main_pages
            assert load_job(resumed_workspace, photos) is None
            report['stop_restart_resume_processing'] = True
            staged_workspace = root / 'staged-smoke'
            local = start_job(photos, staged_workspace, options, CropSettings(), mode='scan').run(
                options, CropSettings(), Event(), lambda percent: None)
            assert local is not None and not local.main_pages
            local.assets[0].ai_focus_dirty = True
            rescanned = start_job(photos, staged_workspace, options, CropSettings(), result=local, mode='rescan').run(
                options, CropSettings(), Event(), lambda percent: None)
            assert rescanned is not None and not rescanned.main_pages and not rescanned.assets[0].ai_focus_dirty
            rendered = start_job(photos, staged_workspace, options, CropSettings(), result=rescanned, mode='sheets').run(
                options, CropSettings(), Event(), lambda percent: None)
            assert rendered.main_pages and all(page.is_file() for page in rendered.main_pages)
            report['separate_scan_rescan_sheets'] = True

        report.update(ok=True, opencv=cv2.__version__, rawpy=rawpy.__version__)
    except Exception:
        report.update(ok=False, error=traceback.format_exc())
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["ok"]:
        raise SystemExit(1)
