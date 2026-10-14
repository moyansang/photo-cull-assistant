"""Reproducible crop-enabled collision timing; synthetic, not accuracy evidence.

Run from repository root with PYTHONPATH=src. Baseline is frozen at the pre-crop
commit. The scalar-crop variant uses current matching semantics, replacing only
the batching dispatch with its reference scalar loop. No source photo is read.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import numpy as np
from PIL import Image

from ai_cull_assistant import grouping as current
from ai_cull_assistant.models import PhotoAsset

BASELINE = '70f73a99b1902f37a135701898a462feac68df80'


def make_asset(index):
    path = Path('/crop-collision-synthetic') / f'P{index:04}.png'
    return PhotoAsset(path.stem, path, path, None, path, datetime(2026, 1, 1),
                      '.png', analysis_mode='landscape')


def scalar_crop_first(asset, visual, candidates, representatives, cfg, stats):
    for _distance, position in candidates:
        anchor, anchor_visual, _group_id = representatives[position]
        if current._crop_aligned_similar(anchor, asset, anchor_visual, visual, cfg, stats):
            return position
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--counts', type=int, nargs='+', default=[64, 128, 256])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    rng = np.random.default_rng(983)
    images = []
    for _ in range(max(args.counts)):
        rows = np.concatenate([rng.permutation([0] * 4 + [255] * 4)
                               for _ in range(8)]).astype(np.uint8)
        images.append(Image.fromarray(np.tile(rows[:, None], (1, 64))).resize(
            (512, 512), Image.Resampling.NEAREST))
    results = []
    with tempfile.TemporaryDirectory(prefix='crop-collision-review-') as temporary:
        path = Path(temporary) / 'baseline.py'
        path.write_bytes(subprocess.check_output(
            ['git', 'show', BASELINE + ':src/ai_cull_assistant/grouping.py'], cwd=repo))
        spec = importlib.util.spec_from_file_location('ai_cull_assistant.collision_baseline', path)
        baseline = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = baseline
        spec.loader.exec_module(baseline)
        features = {
            'baseline': [baseline._visual_from_image(image) for image in images],
            'candidate': [current._visual_from_image(image) for image in images],
        }
        assert all(len(value.crop_variants) == 2 for value in features['candidate'])
        assert {view.whole for value in features['candidate']
                for view in (value, *value.crop_variants)} == {0}
        batch_dispatch = current._first_crop_matching_representative
        original_features = current.visual_features
        try:
            for label, module in [('baseline', baseline), ('scalar_crop', current),
                                  ('batched_crop', current)]:
                values = features['baseline' if label == 'baseline' else 'candidate']
                module.visual_features = lambda asset, values=values: values[int(asset.stem[1:])]
                current._first_crop_matching_representative = (
                    scalar_crop_first if label == 'scalar_crop' else batch_dispatch)
                for count in args.counts:
                    assets = [make_asset(index) for index in range(count)]
                    stats = module.GroupingStats()
                    start = time.perf_counter()
                    module.assign_groups(assets, stats=stats)
                    elapsed = time.perf_counter() - start
                    group_count = len({asset.group_id for asset in assets})
                    assert group_count == count
                    results.append({'variant': label, 'count': count,
                                    'seconds': elapsed, 'group_count': group_count,
                                    'stats': asdict(stats)})
                    print(json.dumps(results[-1]), flush=True)
        finally:
            current._first_crop_matching_representative = batch_dispatch
            current.visual_features = original_features
    record = {
        'baseline_commit': BASELINE,
        'production_grouping_sha256': hashlib.sha256(
            (repo / 'src/ai_cull_assistant/grouping.py').read_bytes()).hexdigest(),
        'fixture': {'seed': 983, 'source_size': [64, 64], 'prepared_size': [512, 512],
                    'resampling': 'NEAREST', 'description': 'Eight independently shuffled '
                    '4-black/4-white row blocks, tiled across columns; all whole/crop dHashes zero.'},
        'limits': 'Index/comparison timing on precomputed synthetic features. '
                  'Excludes image loading and extraction. Single runs, not throughput or accuracy claims.',
        'results': results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    main()
