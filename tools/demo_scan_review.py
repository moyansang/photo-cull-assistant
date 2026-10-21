"""Reproducible synthetic scan/review workspace; never opens a user's photos.

Run from a source checkout using Python 3.12:
  python -X utf8 -Werror tools/demo_scan_review.py --out <NEW_DIRECTORY>
  Add --baseline-ref <FULL_SHA> for real production-chain equivalence.
The generated JPEGs are geometric fixtures, not quality ground truth.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
BASE = 'd0bbd2276de5e3fba9b779cefddd646f884edcf1'


def create_inputs(folder):
    from PIL import Image, ImageDraw, ImageFilter
    folder.mkdir(parents=True, exist_ok=False)
    image = Image.new('RGB', (480, 640), '#666666')
    draw = ImageDraw.Draw(image)
    for x in range(0, 480, 12):
        draw.line((x, 0, 480-x, 640), fill='#eeeeee', width=3)
    draw.ellipse((140, 100, 340, 350), outline='#121212', width=7)
    exif = Image.Exif()
    exif[36867] = '2026:01:01 12:00:00'
    image.save(folder / '同组 清楚纹理.jpg', exif=exif)
    blurred = image.filter(ImageFilter.GaussianBlur(7))
    blurred.save(folder / '同组 模糊纹理.jpg', exif=exif)
    blurred.close()
    rotated = image.transpose(Image.Transpose.ROTATE_90)
    exif[274] = 6
    rotated.save(folder / '方向 6.jpg', exif=exif)
    rotated.close()
    image.close()
    (folder / '读取失败.jpg').write_bytes(b'not an image: intentional synthetic failure')


def source_hashes(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.iterdir()) if p.is_file()}


def fixture_settings(photos):
    from ai_cull_assistant.crop_settings import CropSettings
    return CropSettings(photos={str(p.resolve()).casefold(): {
        'selected_faces': [[.25, .20, .50, .40]], 'preview_version': 'v05'}
        for p in photos.glob('*.jpg') if p.name != '读取失败.jpg'})


def production_scan(photos, workspace):
    from ai_cull_assistant.processing_job import start_job
    settings = fixture_settings(photos)
    options = dict(analysis_mode='portrait', technical_screening=True,
                   body_screening=False, grouping_preset='standard')
    result = start_job(photos, workspace, options, settings, mode='scan').run(
        options, settings, Event(), None)
    assert result is not None and len(result.assets) == 4
    assert any(a.screening_reason == 'decode_unavailable' for a in result.assets)
    return result, settings, options


def core_results(result):
    # No timings, preview-cache destinations or diagnostic scheduling fields.
    # All saved metrics, reasons, measurement signatures and group IDs compare.
    fields = ('stem', 'group_id', 'auto_rejected', 'screening_reason',
              'focus_score', 'face_found', 'clarity_version', 'clarity_evidence',
              'analysis_mode', 'local_recommendation', 'focus_scope')
    return json.loads(json.dumps([{key: getattr(a, key) for key in fields}
                                  for a in result.assets], ensure_ascii=False))


def _worker(source, photos, workspace, destination):
    env = dict(os.environ, PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-X', 'utf8', '-Werror', str(Path(__file__).resolve()),
               '--worker', str(photos), str(workspace), str(destination)]
    subprocess.run(command, env=env, check=True, timeout=180)


def baseline_compare(photos, output, reference=BASE):
    archive = subprocess.check_output(['git', '-C', str(ROOT), 'archive', reference, 'src'])
    baseline_file, current_file = output/'baseline-core.json', output/'current-core.json'
    with tempfile.TemporaryDirectory(prefix='scan-review-baseline-') as temp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(temp, filter='data')
        _worker(Path(temp)/'src', photos, output/'baseline-workspace', baseline_file)
    _worker(ROOT/'src', photos, output/'current-workspace', current_file)
    before = json.loads(baseline_file.read_text('utf-8'))
    after = json.loads(current_file.read_text('utf-8'))
    assert before == after, 'Production core results differ from the requested baseline'
    return {'baseline': reference, 'core_exact_equal': True, 'input_count': len(before),
            'before_sha256': hashlib.sha256(baseline_file.read_bytes()).hexdigest(),
            'after_sha256': hashlib.sha256(current_file.read_bytes()).hexdigest(),
            'accuracy_measured': False}


def exercise_review(result, settings):
    """Exercise common production services; the GUI uses these same stores."""
    from ai_cull_assistant.ai_project import photo_id
    from ai_cull_assistant.grouping import split_group_at, merge_adjacent_groups
    from ai_cull_assistant.scan_review_store import ScanReviewStore
    from ai_cull_assistant.scan_review_decisions import ReviewDecisions
    from ai_cull_assistant.scan_review_ui import evidence_text, result_state
    from ai_cull_assistant.session_store import load_session, save_session
    from ai_cull_assistant.workflow import persist_manual_groups
    original_core = core_results(result)
    store = ScanReviewStore(result.workspace_dir)
    decisions = ReviewDecisions(result.workspace_dir, result.assets, settings)
    rows = decisions.rows()
    for asset in result.assets:
        assert result_state(asset) in {'keep', 'reject', 'review', 'failed', 'not_run'}
        assert evidence_text(asset, rows.get(photo_id(asset)), store.photo(asset))
    asset = next(a for a in result.assets if a.screening_reason != 'decode_unavailable')
    store.record_feedback(asset, text='噪声和构图有疑问；并非确认失焦')
    entry = store.photo(asset)['feedback'][0]
    store.record_feedback(asset, text='保留动作；请检查手部', feedback_id=entry['id'])
    assert len(store.photo(asset)['feedback'][0]['history']) == 1
    assert core_results(result) == original_core
    decisions.set(asset, pick_status=1, rating=4)
    exported = json.loads(decisions.export().read_text('utf-8'))
    row = next(row for row in exported['photos'] if row['path'] == str(asset.primary_path.resolve()))
    assert row['rating'] == 4 and row['pick_status'] == 1
    assert row['focus_review'] is True  # Synthetic face has no reliable landmarks.
    assert core_results(result) == original_core
    # Existing split/merge functions, not a new grouping algorithm.
    for current in result.assets:
        current.group_id = 1
    assert split_group_at(result.assets, result.assets[1].stem)
    groups = list(dict.fromkeys(a.group_id for a in result.assets))
    assert len(groups) == 2 and merge_adjacent_groups(result.assets, *groups)
    persist_manual_groups(result)
    save_session(result)
    reopened = load_session(result.workspace_dir, result.input_dir)
    reopened_decisions = ReviewDecisions(result.workspace_dir, reopened.assets, settings)
    reopened_rows = reopened_decisions.rows()
    assert reopened_rows[photo_id(asset)]['final']['rating'] == 4
    assert ScanReviewStore(result.workspace_dir).photo(asset)['feedback'][0]['text'] == '保留动作；请检查手部'
    current = next(a for a in reopened.assets if photo_id(a) == photo_id(asset))
    reopened_decisions.set(current, rating=0)
    cleared = json.loads(reopened_decisions.export().read_text('utf-8'))
    assert next(r for r in cleared['photos'] if r['path'] == str(asset.primary_path.resolve()))['rating'] == 0
    reopened_decisions.withdraw(current)
    assert not reopened_decisions.rows()[photo_id(asset)]['final'].get('confirmed')
    return {'feedback_history_reopened': True, 'manual_keep_4_stars_exported': True,
            'technical_review_preserved': True, 'stars_explicitly_cleared': True,
            'manual_decision_withdrawn': True, 'existing_split_merge_reopened': True}


def exercise_recheck(result, settings, options):
    """Explicit whole-photo recheck, interruption/resume and atomic comparison."""
    from copy import deepcopy
    from ai_cull_assistant.scan_review_actions import prepare_scope
    from ai_cull_assistant.scan_review_store import ScanReviewStore, asset_key
    from ai_cull_assistant.processing_job import start_job, load_job
    from ai_cull_assistant.session_store import load_session
    target = next(a for a in result.assets if a.screening_reason != 'decode_unavailable')
    session = result.workspace_dir/'scan-session.json'
    groups = result.workspace_dir/'groups.json'
    original_session, original_groups = session.read_bytes(), groups.read_bytes()
    from ai_cull_assistant.image_decode import asset_source_signature
    draft = dict(kind='face', box=[.30,.25,.60,.60], coordinate_space='normalized_display',
                 preview_version='v05', source_path=str(target.primary_path),
                 source_signature=json.loads(json.dumps(asset_source_signature(target))))
    changed, context = prepare_scope(result, target, settings, draft)
    assert session.read_bytes() == original_session
    copy = deepcopy(result)
    next(a for a in copy.assets if asset_key(a) == asset_key(target)).ai_focus_dirty = True
    job = start_job(result.input_dir, result.workspace_dir, options, changed,
                    result=copy, mode='rescan', regroup=False, scan_review=context)
    stop = Event()
    stop.set()
    assert job.run(options, changed, stop, None) is None
    assert session.read_bytes() == original_session
    resumed = load_job(result.workspace_dir, result.input_dir)
    assert resumed.run(options, changed, Event(), None) is not None
    operation = ScanReviewStore(result.workspace_dir).photo(target)['rechecks'][-1]
    assert operation['status'] == 'completed' and operation['before'] != operation['after']
    assert groups.read_bytes() == original_groups
    assert load_session(result.workspace_dir, result.input_dir) is not None
    return {'granularity': 'whole_photo', 'stopped_before_publish': True,
            'resumed_and_reopened': True, 'before_after_preserved': True,
            'groups_bytes_unchanged': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--baseline-ref')
    parser.add_argument('--worker', nargs=3, metavar=('PHOTOS', 'WORKSPACE', 'REPORT'))
    args = parser.parse_args(argv)
    if args.worker:
        photos, workspace, report = map(Path, args.worker)
        result, _, _ = production_scan(photos, workspace)
        report.write_text(json.dumps(core_results(result), ensure_ascii=False, indent=2), 'utf-8')
        return
    if args.out is None:
        parser.error('--out must name a new independent directory')
    # Refuse every pre-existing destination, even an empty directory.
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    photos = output/'合成照片 Synthetic'
    create_inputs(photos)
    original = source_hashes(photos)
    if args.baseline_ref:
        report = baseline_compare(photos, output, args.baseline_ref)
        workspace = output/'current-workspace'
    else:
        sys.path.insert(0, str(ROOT/'src'))
        result, _, _ = production_scan(photos, output/'workspace')
        report = {'input_count': len(result.assets), 'accuracy_measured': False,
                  'core_exact_equal': 'not requested'}
        workspace = output/'workspace'
        (output/'current-core.json').write_text(
            json.dumps(core_results(result), ensure_ascii=False, indent=2), 'utf-8')
    sys.path.insert(0, str(ROOT/'src'))
    from ai_cull_assistant.session_store import load_session
    result = load_session(workspace, photos)
    settings = fixture_settings(photos)
    report['review_loop'] = exercise_review(result, settings)
    result = load_session(workspace, photos)
    options = dict(analysis_mode='portrait', technical_screening=True, body_screening=False, grouping_preset='standard')
    report['recheck_loop'] = exercise_recheck(result, settings, options)
    assert source_hashes(photos) == original, 'Original fixture bytes changed'
    report.update(source_bytes_unchanged=True, photos=str(photos), workspace=str(workspace),
                  api_calls=0, real_raw_tested=False, lightroom_host_tested=False)
    (output/'demo-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
