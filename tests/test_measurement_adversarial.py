"""Adversarial capture/publication and fresh-process identity regressions."""
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from ai_cull_assistant import ai_project, analysis_modes, image_decode, measurement_receipts as mr
from ai_cull_assistant.screening import screen_assets
from test_measurement_receipts import asset, capture, docs, result


def _run_python_utf8(arguments, *, cwd, env):
    # Parent -X utf8 is not inherited by Python children. Override even a
    # caller's legacy PYTHONIOENCODING, and decode failures strictly too.
    env = dict(env, PYTHONIOENCODING='utf-8:strict')
    return subprocess.run([sys.executable, '-X', 'utf8', *arguments],
                          cwd=cwd, env=env, check=True, capture_output=True,
                          text=True, encoding='utf-8', errors='strict', timeout=30)


def test_child_failure_preserves_chinese_stderr_under_utf8_contract(tmp_path):
    folder = tmp_path / '中文 路径'
    folder.mkdir()
    script = folder / '失败原因.py'
    script.write_text('raise RuntimeError("真正的错误原因：合成失败")\n', encoding='utf-8')
    env = dict(os.environ, PYTHONUTF8='0', PYTHONIOENCODING='ascii')
    with pytest.raises(subprocess.CalledProcessError) as failure:
        _run_python_utf8([str(script)], cwd=folder, env=env)
    assert failure.value.returncode != 0
    assert 'RuntimeError: 真正的错误原因：合成失败' in failure.value.stderr
    assert str(script) in failure.value.stderr
    assert 'UnicodeDecodeError' not in failure.value.stderr
    assert 'UnicodeEncodeError' not in failure.value.stderr
    assert failure.value.stdout == ''


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
    repo = tmp_path / '合成 checkout'
    package = repo / 'src' / 'ai_cull_assistant'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    shutil.copyfile(mr.__file__, package / 'measurement_receipts.py')
    # Copy only the real native identity helpers into this isolated fixture.
    # Do not mock Windows change times or import the actual checkout at runtime.
    native_helpers = '\n\n'.join(inspect.getsource(function) for function in (
        image_decode._windows_extended_path,
        image_decode._windows_identity_api,
        image_decode._windows_change_time,
    ))
    (package / 'image_decode.py').write_text(
        'from functools import lru_cache\n\n' + native_helpers
        + '\n\ndef source_signature(path):\n    return ()\n', encoding='utf-8')
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
    env = dict(os.environ, PYTHONPATH=str(repo / 'src'), PYTHONDONTWRITEBYTECODE='1')
    response = _run_python_utf8(['-c', script], cwd=repo, env=env)
    states = json.loads(response.stdout)
    assert states['before']['verified'] is True
    assert states['mismatch']['loaded_code_matches_checkout'] is False
    assert states['mismatch']['verified'] is False
    assert states['executed'] == 1
    assert states['after']['dirty'] is False
    assert states['after']['verified'] is False
    assert states['after']['checkout_unchanged_since_process_start'] is False
