#!/usr/bin/env python3
"""Create authorized synthetic inputs for the formal batch CLI, not real truth."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import hashlib
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import offline_evaluation as oe
from ai_cull_assistant.crop_settings import CropSettings


def generate(output):
    from PIL import Image
    output = oe.outside_repository(output)
    oe.require(not output.exists(), 'fixture output exists')
    output.mkdir(parents=True, exist_ok=False)
    rows, bindings = [], {}
    for i in range(3):
        path = output / 'inputs' / f'场景 {i}' / '同名 样本.png'
        path.parent.mkdir(parents=True)
        image = Image.new('RGB', (640, 480))
        image.putdata([((255-i*30,)*3 if (x//(5+i)+y//5)%2 else (0,0,0)) for y in range(480) for x in range(640)])
        image.save(path); image.close()
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        aid, sid = f'generated-asset-{i}', f'generated-source-{i}'
        rows.append({'asset_id': aid, 'capture_id': f'generated-exposure-{i}', 'sample_version': '1',
            'variant_family_id': f'generated-variant-{i}', 'comparison_group_id': f'generated-group-{i}',
            'scene_family_id': f'generated-scene-{i}', 'split': 'development', 'scene_type': 'landscape',
            'source_authorization': 'generated checker pixels only', 'historical_exposure': 'synthetic engineering fixture',
            'evaluation_source_id': sid, 'person_ids': [], 'session_id': 'fixture-session', 'burst_id': None,
            'exception_tags': ['synthetic_checker'], 'sources': [{'source_id': sid, 'sha256': sha, 'format': 'PNG',
                'dataset_id': 'generated-checkers', 'dataset_version': '1', 'original_file_id': aid,
                'original_sha256': sha, 'width': 640, 'height': 480, 'preprocessing_history': []}]})
        bindings[aid] = {'primary_path': str(path), 'sources': {sid: str(path)}, 'jpeg_source_id': sid, 'raw_source_id': None}
    samples = {'schema_version': oe.SCHEMA, 'kind': 'samples', 'dataset_id': 'synthetic-batch-fixture', 'dataset_version': '1',
        'guideline_version': 'synthetic-engineering-only-v1', 'manifest_version': '1', 'evaluation_kind': 'synthetic',
        'rows': rows, 'split_registry': [], 'registry_review': 'generated-only; not real independent data',
        'dataset_registry': [{'dataset_id': 'generated-checkers', 'dataset_version': '1', 'source_url': 'local:generated',
            'license': 'generated test pixels', 'attribution': 'real_sample_fixture.py', 'authorization': 'synthetic testing only',
            'authorization_confirmed': True, 'historical_exposure': 'synthetic engineering fixture'}]}
    paths = {'schema_version': 'real-sample-path-map-v1', 'manifest_sha256': oe.canonical_hash(samples), 'bindings': bindings}
    config = {'schema_version': 'real-sample-measurement-config-v1', 'analysis_mode': 'landscape', 'body_check': False,
              'crop_settings': {k: getattr(CropSettings(), k) for k in ('scale_factor', 'shift_factor', 'offset_x_factor', 'aspect_ratio', 'detection_confidence')}}
    for name, value in [('samples',samples),('paths',paths),('config',config)]:
        oe.write_new(output / (name+'.json'),value)
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        print(generate(args.output)); return 0
    except (OSError,ValueError) as exc:
        print(str(exc),file=sys.stderr); return 2

if __name__ == '__main__':
    raise SystemExit(main())
