"""Supplementary native sparse-water/reflection controls; no human labels."""
from pathlib import Path
import argparse
import json
import numpy as np
from PIL import Image
from evaluate import open_rgb, noisy, load_baseline, sha
from ai_cull_assistant.analysis_modes import landscape_evidence

parser = argparse.ArgumentParser()
parser.add_argument('--spaq-data', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
baseline, baseline_hash = load_baseline('70f73a99')
path = args.spaq_data / 'images/04290.jpg'
image = open_rgb(path)
w, h = image.size
canvas = np.full((1024, 1024, 3), 128, np.uint8)
boxes = []
for slot, col in enumerate((1, 2)):
    cx, cy = round((col + .5) * w / 4), round(2.5 * h / 4)
    box = (cx - 128, cy - 128, cx + 128, cy + 128)
    boxes.append(box)
    canvas[:256, slot * 256:(slot + 1) * 256] = np.asarray(image.crop(box))
records = []
for sigma in [0, 8, 16]:
    for seed in [123, 456, 789]:
        variant = Image.fromarray(noisy(canvas, sigma, seed))
        old, new = baseline(variant), landscape_evidence(variant)
        records.append({'sigma_8bit': sigma, 'seed': seed, 'baseline': old['recommendation'],
                        'candidate': new['recommendation'], 'raw_clear': new['clear_regions'],
                        'supported_clear': new['supported_clear_regions'], 'regions': new['regions']})
result = {'source': str(path), 'source_sha256': sha(path), 'boxes': boxes,
          'baseline_ref': '70f73a99', 'baseline_source_sha256': baseline_hash, 'records': records}
args.output.mkdir(parents=True, exist_ok=True)
(args.output / 'water-results.json').write_text(json.dumps(result, indent=2, allow_nan=False))
print(json.dumps([{k: v for k, v in r.items() if k != 'regions'} for r in records], indent=2))
