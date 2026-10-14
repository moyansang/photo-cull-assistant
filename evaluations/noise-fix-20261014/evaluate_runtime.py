"""Isolated analysis-only timing; run when other benchmark workers are idle."""
from pathlib import Path
import argparse
import json
import statistics
import time
from evaluate import load_baseline, open_rgb, sha
from ai_cull_assistant import analysis_modes as modes

parser = argparse.ArgumentParser()
parser.add_argument('--koniq-data', type=Path, required=True)
parser.add_argument('--spaq-data', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
baseline, baseline_hash = load_baseline('70f73a99')
k, s = args.koniq_data, args.spaq_data
inputs = [('spaq_' + path.stem, path) for path in sorted((s / 'images').glob('*.jpg'))]
inputs += [('koniq_' + Path(row['name']).stem, k / 'images' / row['name'])
           for row in json.loads((k / 'scene_selection.json').read_text())]
images = [(name, open_rgb(path)) for name, path in inputs]
timings = {name: {'baseline': [], 'candidate': []} for name, _ in images}
for _, image in images:
    baseline(image)
    modes.landscape_evidence(image)
for repeat in range(5):
    for name, image in images:
        functions = [('baseline', baseline), ('candidate', modes.landscape_evidence)]
        if repeat % 2:
            functions.reverse()
        for label, function in functions:
            start = time.perf_counter()
            function(image)
            timings[name][label].append(time.perf_counter() - start)
medians = {name: {label: statistics.median(values) for label, values in timing.items()}
           for name, timing in timings.items()}
totals = {label: sum(v[label] for v in medians.values()) for label in ['baseline', 'candidate']}
result = {'scope': 'Analysis only, already-decoded originals; five alternating-order repeats after warmup.',
          'baseline_ref': '70f73a99', 'baseline_source_sha256': baseline_hash,
          'candidate_source_sha256': sha(Path(modes.__file__)),
          'per_image_medians': medians, 'all_repeats': timings,
          'sum_14_image_medians': totals, 'ratio': totals['candidate'] / totals['baseline']}
args.output.mkdir(parents=True, exist_ok=True)
(args.output / 'runtime-isolated.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
