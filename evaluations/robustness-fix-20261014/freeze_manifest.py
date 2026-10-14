"""Inventory this candidate's implementation and reproducible evidence files."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
implementation = ['src/ai_cull_assistant/grouping.py', 'src/ai_cull_assistant/analysis_modes.py',
                  'tests/test_grouping_crop_alignment.py', 'tests/test_landscape_noise_guard.py',
                  'docs/research-grouping.md']
headless = json.loads((HERE / 'headless-manifest.json').read_text())
for path, expected in headless['sha256'].items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected, path
paths = [ROOT / path for path in implementation]
for folder in (HERE, ROOT / 'evaluations/noise-fix-20261014'):
    paths.extend(path for path in folder.rglob('*') if path.is_file()
                 and '__pycache__' not in path.parts and path.name != 'final-manifest.json')
files = {str(path.relative_to(ROOT)): {'bytes':path.stat().st_size,
         'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(set(paths))}
manifest = {'base_head':subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
            'scope':'candidate robustness implementation and evidence; no source photos included',
            'reviewed_headless_tests':headless['test_count'], 'tested_source_hashes_verified':True,
            'file_count':len(files), 'total_bytes':sum(v['bytes'] for v in files.values()), 'files':files}
(HERE / 'final-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({k:v for k,v in manifest.items() if k != 'files'},indent=2))
