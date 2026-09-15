"""Isolated darktable OpenCL feasibility test; never changes scan backends.

Run with the project Python. Inputs are copied to a new output directory so
darktable cannot read/write the user's sidecars or Lightroom edits.
"""
import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from unittest.mock import patch

import numpy as np
from PIL import Image

from ai_cull_assistant.face_focus import assess_asset_focus, load_full_image
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.preview import build_preview


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=4, choices=range(1, 11))
    args = parser.parse_args()
    cli = args.cli.resolve(strict=True)
    sources = sorted(args.input.resolve(strict=True).glob('*.RW2'))[:args.count]
    if not sources:
        parser.error('No RW2 files found')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    inputs = root / 'inputs'
    inputs.mkdir()
    report = {'backend': str(cli), 'backend_sha256': sha256(cli), 'photos': [], 'note':
              'Sequential cold CLI invocations including PNG output; rawpy excludes PNG output. '
              'Darktable is a different development pipeline, not an equivalent LibRaw backend.'}
    original_hashes = {str(p): sha256(p) for p in sources}
    try:
        for source in sources:
            copied = inputs / source.name
            shutil.copy2(source, copied)
            asset = PhotoAsset(copied.stem, copied, copied, copied, None,
                               datetime.now(), copied.suffix)
            build_preview(asset, root / 'previews')
            start = time.perf_counter()
            reference = load_full_image(asset)
            row = {'name': source.name, 'rawpy_seconds': time.perf_counter() - start,
                   'rawpy_size': list(reference.size)}
            with patch('ai_cull_assistant.face_focus.load_full_image',
                       side_effect=lambda _: reference.copy()):
                row['rawpy_focus'] = asdict(assess_asset_focus(asset))
            baseline = np.asarray(reference, dtype=np.int16)
            for mode in ('cpu', 'gpu'):
                config = root / ('config-' + mode)
                cache = root / ('cache-' + mode)
                config.mkdir(exist_ok=True)
                cache.mkdir(exist_ok=True)
                output = root / (copied.stem + '-' + mode + '.png')
                command = [str(cli), copied.as_posix(), output.as_posix(), '--apply-custom-presets',
                           'false', '--icc-type', 'sRGB', '--core', '--configdir', config.as_posix(),
                           '--cachedir', cache.as_posix(), '--library', ':memory:', '-d', 'opencl',
                           '-d', 'perf', '--conf', 'write_sidecar_files=never',
                           '--conf', 'plugins/imageio/format/png/bpp=8',
                           '--conf', 'plugins/imageio/format/png/compression=0',
                           '--conf', 'plugins/darkroom/workflow=none',
                           '--conf', 'opencl=' + ('TRUE' if mode == 'gpu' else 'FALSE')]
                start = time.perf_counter()
                try:
                    process = subprocess.run(command, capture_output=True, timeout=180,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    seconds = time.perf_counter() - start
                    log = (process.stdout + process.stderr).decode('utf-8', errors='replace')
                    (root / (copied.stem + '-' + mode + '.log')).write_text(log, 'utf-8')
                    result = {'seconds': seconds, 'returncode': process.returncode,
                              'output_exists': output.is_file(),
                              'gpu_modules': re.findall(r"processed `([^']+)' on GPU", log),
                              'pipeline_seconds': [float(s) for s in re.findall(
                                  r'pixel pipeline processing took ([0-9.]+) secs', log)]}
                    if process.returncode == 0 and output.is_file():
                        with Image.open(output) as image:
                            rgb = image.convert('RGB')
                        result['size'] = list(rgb.size)
                        if rgb.size == reference.size:
                            result['mean_absolute_rgb_difference'] = float(
                                np.abs(np.asarray(rgb, dtype=np.int16) - baseline).mean())
                        with patch('ai_cull_assistant.face_focus.load_full_image',
                                   side_effect=lambda _: rgb.copy()):
                            result['focus'] = asdict(assess_asset_focus(asset))
                        rgb.close()
                    row['darktable_' + mode] = result
                except subprocess.TimeoutExpired as exc:
                    log = (exc.stdout or b'') + (exc.stderr or b'')
                    (root / (copied.stem + '-' + mode + '.log')).write_bytes(log)
                    row['darktable_' + mode] = {'error': 'timeout', 'seconds': 180}
            reference.close()
            report['photos'].append(row)
            (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
            print(source.name, 'completed', flush=True)
    finally:
        report['originals_unchanged'] = all(sha256(Path(p)) == digest
                                           for p, digest in original_hashes.items())
        (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    if not report['originals_unchanged']:
        raise RuntimeError('Original integrity check failed')


if __name__ == '__main__':
    main()
