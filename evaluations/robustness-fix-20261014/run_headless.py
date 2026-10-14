"""Re-run the exact previously reviewed 873 headless cases plus robustness tests."""
from pathlib import Path
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
nodeids = json.loads((HERE / 'headless-baseline-nodeids.json').read_text())
assert len(nodeids) == len(set(nodeids)) == 873
command = [sys.executable, '-m', 'pytest', '-q', *nodeids,
           'tests/test_grouping_crop_alignment.py', 'tests/test_landscape_noise_guard.py',
           '--junitxml=' + str(HERE / 'headless.xml')]
env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
with (HERE / 'headless.log').open('w') as output:
    result = subprocess.run(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
print((HERE / 'headless.log').read_text())
raise SystemExit(result.returncode)
