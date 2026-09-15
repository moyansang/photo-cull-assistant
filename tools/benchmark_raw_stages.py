"""Measure a bounded RAW-only scan serially, without APIs or user caches."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch

import rawpy
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import IMAGE_EXTENSIONS
from ai_cull_assistant.processing_job import start_job
from ai_cull_assistant.scan_resources import capture_resource_snapshot
from ai_cull_assistant.scan_tuning import ScanHistory
from ai_cull_assistant.workspace_log import DETAIL_PREFIX


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, help='Optional prior benchmark JSON with verdicts')
    args = parser.parse_args()
    source = args.input.resolve(strict=True)
    output = args.output.resolve()
    if output == source or source in output.parents or output in source.parents:
        parser.error('Input and output must be separate directories')
    paths = sorted(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)
    if not 1 <= len(paths) <= 80 or any(p.suffix.lower() != '.rw2' for p in paths):
        parser.error('Use a directory containing 1 to 80 RW2 images only')
    before = {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in paths}
    output.mkdir(parents=True, exist_ok=False)
    options = {'technical_screening': True, 'body_screening': False}
    logs = []
    report = {'rawpy_version': rawpy.__version__, 'workers': 1,
              'resources': asdict(capture_resource_snapshot()), 'body_screening': False,
              'note': 'Serial photos; default internal RAW threads. Fresh workspace, OS disk cache not cleared.'}
    def log(line):
        logs.append(line)
        print(line, flush=True)
    def history(snapshot, body):
        return ScanHistory(snapshot, body, output / 'tuning.json')
    started = time.perf_counter()
    try:
        with patch('ai_cull_assistant.processing_job.ScanHistory', side_effect=history), \
             patch('ai_cull_assistant.scan_resources.ResourceBudget.choose_workers', return_value=1):
            job = start_job(source, output / 'workspace', options, CropSettings(), mode='scan')
            result = job.run(options, CropSettings(), threading.Event(), None, on_log=log)
        report['wall_seconds'] = time.perf_counter() - started
        text = (output / 'workspace/logs/session.log').read_text('utf-8')
        details = [json.loads(line[len(DETAIL_PREFIX):]) for line in text.splitlines()
                   if line.startswith(DETAIL_PREFIX)]
        report['diagnostics'] = [d for d in details if d.get('type') == 'summary'][-1]
        stages = {}
        for row in details:
            if row.get('type') == 'summary':
                continue
            for key, value in row['stage_seconds'].items():
                stages[key] = stages.get(key, 0) + value
        report['photo_stage_seconds'] = stages
        rows = [dict(filename=a.primary_path.name, rejected=a.auto_rejected,
                     reason=a.screening_reason, face=a.face_found, group=a.group_id,
                     focus_score=a.focus_score, evidence=a.clarity_evidence) for a in result.assets]
        report['verdicts'] = rows
        if args.baseline:
            baseline = json.loads(args.baseline.read_text('utf-8'))['verdicts']
            report['exact_results_match'] = json.loads(json.dumps(rows)) == baseline
        report['logs'] = logs
    finally:
        report['original_metadata_unchanged'] = all(
            (Path(p).stat().st_size, Path(p).stat().st_mtime_ns) == stat for p, stat in before.items())
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    if not report['original_metadata_unchanged'] or report.get('exact_results_match') is False:
        raise RuntimeError('Input metadata or baseline result verification failed; see report.json')
    print('REPORT:', output / 'report.json', flush=True)


if __name__ == '__main__':
    main()
