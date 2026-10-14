"""Reproducible development checks, not human-labeled accuracy/calibration.

Uses only previously downloaded sources. Variants stay in memory; source bytes
are never rewritten. A pinned git baseline exercises the previous production
function instead of a reimplementation. Run from the repository root.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import statistics
import subprocess
import time

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps

from ai_cull_assistant import analysis_modes as modes


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def open_rgb(path):
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert('RGB')


def load_baseline(ref):
    source = subprocess.check_output(['git', 'show', f'{ref}:src/ai_cull_assistant/analysis_modes.py'], text=True)
    scope = {'__name__': 'ai_cull_assistant._noise_eval_baseline', '__package__': 'ai_cull_assistant'}
    exec(compile(source, f'git:{ref}:analysis_modes.py', 'exec'), scope)
    return scope['landscape_evidence'], hashlib.sha256(source.encode()).hexdigest()


def noisy(pixels, sigma, seed, *, monochrome=False):
    shape = (*pixels.shape[:2], 1) if monochrome else pixels.shape
    noise = np.random.default_rng(seed).normal(0, sigma, shape).astype(np.float32)
    return np.clip(pixels.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--koniq-data', type=Path, required=True)
    parser.add_argument('--spaq-data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-ref', default='70f73a99')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    baseline, baseline_hash = load_baseline(args.baseline_ref)
    k, s = args.koniq_data, args.spaq_data
    inputs = [('spaq_' + path.stem, path) for path in sorted((s / 'images').glob('*.jpg'))]
    inputs += [('koniq_' + Path(row['name']).stem, k / 'images' / row['name'])
               for row in json.loads((k / 'scene_selection.json').read_text())]
    hashes = {str(path): sha(path) for _, path in inputs}
    records = []

    def pair(name, image, kind, **metadata):
        old = baseline(image)
        new = modes.landscape_evidence(image)
        stripped = [{key: value for key, value in region.items() if key != 'detail_support'}
                    for region in new['regions']]
        assert stripped == old['regions'], 'Original region evidence must not change'
        assert new['recommendation'] != 'reject'
        assert not (old['recommendation'] == 'review' and new['recommendation'] == 'keep')
        row = {'name': name, 'kind': kind, **metadata, 'baseline': old['recommendation'],
               'candidate': new['recommendation'], 'raw_clear': new['clear_regions'],
               'supported_clear': new['supported_clear_regions'], 'reasons': new['reasons'],
               'regions': new['regions']}
        records.append(row)
        return row

    original_images = []
    for name, path in inputs:
        image = open_rgb(path)
        original_images.append((name, image))
        pair(name, image, 'original', source=str(path))
    print('Originals complete', flush=True)
    for path in sorted((k / 'variants').glob('*.jpg')):
        hashes[str(path)] = sha(path)
        pair(path.stem, open_rgb(path), 'saved_variant', source=str(path))
    print('Saved variants complete', flush=True)
    for name, path in [('spaq_08862', s / 'images/08862.jpg'),
                       ('field_blur3', k / 'variants/169779095__blur3.jpg')]:
        pixels = np.asarray(open_rgb(path))
        for sigma in [0, 4, 8, 16, 24, 32]:
            for seed in range(20261014, 20261019):
                pair(f'{name}_noise{sigma}_seed{seed}', Image.fromarray(noisy(pixels, sigma, seed)),
                     'real_noise', source=str(path), sigma_8bit=sigma, seed=seed)
    print('Real-input noise checks complete', flush=True)

    # Two distinct native 256px crops on a flat canvas: only two tiles contain
    # natural detail. These crops share sources, and are not independent scenes.
    texture_sources = [('grass', k / 'images/169779095.jpg', 2, (1, 2)),
                       ('foliage', s / 'images/01517.jpg', 1, (1, 2)),
                       ('rock', k / 'images/1674951001.jpg', 1, (0, 1)),
                       ('fence', k / 'images/1679551869.jpg', 1, (1, 2))]
    for name, path, row, columns in texture_sources:
        image = open_rgb(path)
        w, h = image.size
        canvas = np.full((1024, 1024, 3), 128, np.uint8)
        boxes = []
        for slot, col in enumerate(columns):
            cx, cy = round((col + .5) * w / 4), round((row + .5) * h / 4)
            box = (cx - 128, cy - 128, cx + 128, cy + 128)
            canvas[:256, slot * 256:(slot + 1) * 256] = np.asarray(image.crop(box))
            boxes.append(box)
        for sigma in [0, 8, 16]:
            for seed in [123, 456, 789]:
                pair(f'{name}_sparse_noise{sigma}_seed{seed}', Image.fromarray(noisy(canvas, sigma, seed)),
                     'sparse_natural_texture', source=str(path), boxes=boxes, sigma_8bit=sigma, seed=seed)
    print('Natural texture checks complete', flush=True)

    yy, xx = np.indices((512, 512))
    for cell in [3, 4, 8, 16, 24]:
        board = ((xx // cell + yy // cell) % 2 * 255).astype(np.uint8)
        sharp = Image.fromarray(np.repeat(board[:, :, None], 3, axis=2))
        for blur in [0, 3, 5]:
            pixels = np.asarray(sharp.filter(ImageFilter.GaussianBlur(blur)))
            for sigma in [0, 4, 8, 16, 32]:
                for seed in [123, 456, 789]:
                    pair(f'checker{cell}_blur{blur}_noise{sigma}_seed{seed}',
                         Image.fromarray(noisy(pixels, sigma, seed)), 'synthetic_checker',
                         family=f'checker{cell}_blur{blur}', sigma_8bit=sigma, seed=seed)
    for period in [6, 9, 12, 16, 32, 64, 128]:
        for amplitude in [20, 60, 100]:
            pixels = 128 + amplitude * np.sin(xx * 2 * np.pi / period) * np.sin(yy * 2 * np.pi / period)
            pixels = np.repeat(pixels[:, :, None], 3, axis=2)
            for sigma in [0, 4, 8, 16, 32]:
                for seed in [123, 456, 789]:
                    pair(f'sines{period}_amp{amplitude}_noise{sigma}_seed{seed}',
                         Image.fromarray(noisy(pixels, sigma, seed)), 'synthetic_sine',
                         family=f'sines{period}_amp{amplitude}', sigma_8bit=sigma, seed=seed)
    print('Synthetic threshold-adjacent checks complete', flush=True)

    # These deliberately challenge the assumed independent-Gaussian model.
    base = np.asarray(open_rgb(k / 'variants/169779095__blur3.jpg'))
    for seed in [123, 456, 789]:
        n = noisy(base, 16, seed)
        for quality in [50, 75, 95]:
            buffer = io.BytesIO()
            Image.fromarray(n).save(buffer, format='JPEG', quality=quality)
            buffer.seek(0)
            pair(f'field_noise16_jpeg{quality}_seed{seed}', Image.open(buffer).convert('RGB'),
                 'compressed_noise', quality=quality, seed=seed)
        rng = np.random.default_rng(seed)
        additive = rng.normal(size=base.shape).astype(np.float32)
        additive = cv2.GaussianBlur(additive, (0, 0), .7)
        additive *= 16 / additive.std()
        pixels = np.clip(base.astype(np.float32) + additive, 0, 255).astype(np.uint8)
        pair(f'field_correlated_noise16_seed{seed}', Image.fromarray(pixels), 'correlated_noise', seed=seed)
    print('Noise-model limitations checked', flush=True)

    timings = {name: {'baseline': [], 'candidate': []} for name, _ in original_images}
    for repeat in range(3):
        for name, image in original_images:
            functions = [('baseline', baseline), ('candidate', modes.landscape_evidence)]
            if repeat % 2:
                functions.reverse()
            for label, function in functions:
                start = time.perf_counter()
                function(image)
                timings[name][label].append(time.perf_counter() - start)
    median_timings = {name: {label: statistics.median(values) for label, values in timing.items()}
                      for name, timing in timings.items()}
    totals = {label: sum(v[label] for v in median_timings.values()) for label in ['baseline', 'candidate']}
    assert all(sha(Path(path)) == digest for path, digest in hashes.items()), 'Source bytes changed'
    summary = {kind: {'count': len(rows), 'baseline': dict(Counter(r['baseline'] for r in rows)),
                      'candidate': dict(Counter(r['candidate'] for r in rows)),
                      'changes': [r['name'] for r in rows if r['baseline'] != r['candidate']]}
               for kind in sorted({r['kind'] for r in records})
               if (rows := [r for r in records if r['kind'] == kind])}
    synthetic = [r for r in records if r['kind'].startswith('synthetic_')]
    zero = {(r['family'], r['seed']): r for r in synthetic if r['sigma_8bit'] == 0}
    promotions = [r for r in synthetic if r['sigma_8bit'] > 0
                  and zero[(r['family'], r['seed'])]['baseline'] == 'review' and r['baseline'] == 'keep']
    summary['synthetic_promotions'] = {'baseline_promoted': len(promotions),
                                       'still_promoted': sum(r['candidate'] == 'keep' for r in promotions),
                                       'remaining': [r['name'] for r in promotions if r['candidate'] == 'keep']}
    output = {'baseline_ref': args.baseline_ref, 'baseline_source_sha256': baseline_hash,
              'candidate_source_sha256': sha(Path(modes.__file__)),
              'landscape_version': modes.LANDSCAPE_VERSION, 'opencv': cv2.__version__,
              'input_sha256': hashes, 'summary': summary, 'records': records,
              'timing_seconds': {'per_image_medians': median_timings, 'three_repeats': timings,
                                 'sum_14_image_medians': totals, 'ratio': totals['candidate'] / totals['baseline']}}
    (args.output / 'results.json').write_text(json.dumps(output, indent=2, allow_nan=False))
    print(json.dumps({'summary': summary, 'timing_seconds': output['timing_seconds']}, indent=2), flush=True)


if __name__ == '__main__':
    main()
