"""Adversarial capture/publication and fresh-process identity regressions."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from ai_cull_assistant import ai_project, analysis_modes, measurement_receipts as mr
from ai_cull_assistant.screening import screen_assets
from test_measurement_receipts import asset, capture, docs, result


def test_transient_input_edit_and_restore_invalidates_actual_capture(tmp_path, monkeypatch):
    photo = asset(tmp_path)
    original = analysis_modes.landscape_evidence
    def transient(image):
        data = photo.primary_path.read_bytes()
        photo.primary_path.write_bytes(data + b'transient edit')
        photo.primary_path.write_bytes(data)
        return original(image)
    monkeypatch.setattr(analysis_modes, 'landscape_evidence', transient)
    recorder = mr.MeasurementRecorder(synthetic=True)
    screen_assets([photo], analysis_mode='landscape', evaluation_recorder=recorder)
    assert recorder.rows[str(photo.primary_path)]['status'] == 'changed'


def test_input_edit_during_receipt_write_cannot_publish_valid_freeze(tmp_path, monkeypatch):
    photo, recorder = capture(tmp_path, monkeypatch)
    samples, bindings = docs(photo)
    original = ai_project.atomic_json
    def edit_during_write(path, value):
        original(path, value)
        if Path(path).name == 'measurement-receipt.json':
            photo.primary_path.write_bytes(photo.primary_path.read_bytes() + b'changed while freezing')
    monkeypatch.setattr(ai_project, 'atomic_json', edit_during_write)
    output = tmp_path / 'must-not-publish'
    try:
        recorder.freeze(result(photo, tmp_path), samples, bindings, output)
    except (OSError, ValueError):
        assert not output.exists()
    else:
        receipt = json.loads((output / 'measurement-receipt.json').read_text())
        assert receipt['recording_status'] != 'complete'


def test_long_running_clean_checkout_restored_file_is_unverifiable(tmp_path):
    # Standalone synthetic Git checkout, never mutates the user's repository.
    repo = tmp_path / 'synthetic-checkout'
    package = repo / 'src' / 'ai_cull_assistant'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    shutil.copyfile(mr.__file__, package / 'measurement_receipts.py')
    (package / 'image_decode.py').write_text('def source_signature(path):\n    return ()\n')
    (package / 'helper.py').write_text('def version():\n    return 1\n')
    def git(*args):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
    git('init')
    git('add', 'src')
    git('-c', 'user.name=Synthetic Test', '-c', 'user.email=synthetic@example.invalid', 'commit', '-m', 'synthetic fixture')
    # Linux btime has whole-second granularity; avoid false age rejection.
    time.sleep(1.1)
    script = '''
import json
from pathlib import Path
from ai_cull_assistant import measurement_receipts as mr, helper
before = mr.code_identity()
p = Path(helper.__file__)
old = p.read_bytes()
p.write_bytes(b"def version():\\n    return 2\\n")
mismatch = mr.code_identity()
p.write_bytes(old)
after = mr.code_identity()
print(json.dumps({'before': before, 'mismatch': mismatch, 'after': after, 'executed': helper.version()}))
'''
    import os
    env = dict(os.environ, PYTHONPATH=str(repo / 'src'), PYTHONDONTWRITEBYTECODE='1')
    response = subprocess.run([sys.executable, '-c', script], cwd=repo, env=env,
                              check=True, capture_output=True, text=True)
    states = json.loads(response.stdout)
    assert states['before']['verified'] is True
    assert states['mismatch']['loaded_code_matches_checkout'] is False
    assert states['executed'] == 1
    assert states['after']['dirty'] is False
    assert states['after']['verified'] is False
    assert states['after']['checkout_unchanged_since_process_start'] is False
