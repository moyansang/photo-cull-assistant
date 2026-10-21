#!/usr/bin/env python3
"""Explicit manifest-only, isolated synchronous measurement and frozen evaluation.

No directory scan, pairing inference, label inference, model download or GUI hook.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import offline_evaluation as oe
import frozen_prediction_export as exporter
from ai_cull_assistant.ai_project import atomic_json
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.measurement_receipts import MeasurementRecorder, code_identity
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.preview import build_preview
from ai_cull_assistant.primary_quality import confirmed_selection, CONFIRMATION_KEY
from ai_cull_assistant.publication_transaction import operation
from ai_cull_assistant.screening import screen_assets
from ai_cull_assistant.shared_decode import shared_decode
from ai_cull_assistant.workflow import ScanResult

SCHEMA = 'real-sample-batch-v1'
CONFIG_SCHEMA = 'real-sample-measurement-config-v1'
CROP_FIELDS = {'scale_factor', 'shift_factor', 'offset_x_factor', 'aspect_ratio', 'detection_confidence'}


def tool_identity():
    from evaluation_inputs import source_fingerprint
    names = ('real_sample_evaluation.py', 'evaluation_inputs.py', 'frozen_prediction_export.py', 'offline_evaluation.py')
    return {name: {'sha256': oe.digest((ROOT / 'tools' / name).read_bytes()),
                   'fingerprint': source_fingerprint(ROOT / 'tools' / name)} for name in names}


# Bind the executing tool chain at import, not a later checkout/main snapshot.
_LOADED_TOOLS = tool_identity()


def validate_config(config):
    oe.require(isinstance(config, dict) and config.get('schema_version') == CONFIG_SCHEMA, 'explicit measurement configuration required')
    oe.require(set(config) <= {'schema_version', 'analysis_mode', 'body_check', 'crop_settings', 'asset_modes'}, 'unknown configuration fields; arbitrary settings/credentials not accepted')
    oe.require(config.get('analysis_mode') in ('portrait', 'landscape'), 'explicit portrait/landscape mode required; no no-face inference')
    oe.require(type(config.get('body_check')) is bool, 'explicit body_check boolean required')
    values = config.get('crop_settings')
    oe.require(isinstance(values, dict) and set(values) == CROP_FIELDS, 'all effective crop settings must be explicit; no photos or unknown keys')
    settings = CropSettings.from_dict(values)
    for key in CROP_FIELDS:
        oe.require(getattr(settings, key) == values[key] and not isinstance(values[key], bool), f'crop setting {key} is invalid or would be clamped')
    modes = config.get('asset_modes', {})
    oe.require(isinstance(modes, dict) and all(isinstance(k, str) and v in ('portrait', 'landscape') for k, v in modes.items()), 'invalid asset_modes')
    return settings


def safe_output(output, path_map):
    output = oe.outside_repository(output)
    oe.require(not output.exists(), 'output already exists; use a new isolated directory')
    markers = ('scan-session.json', 'processing-settings.json', 'ai_project.json', 'workspace-identity.json', '.publication-operation.lock')
    for ancestor in output.parents:
        oe.require(not any((ancestor / name).exists() for name in markers), 'output may not be inside an active/existing workspace')
    for binding in path_map.get('bindings', {}).values():
        for value in binding.get('sources', {}).values():
            source = Path(value).resolve()
            oe.require(not output.is_relative_to(source.parent), 'output may not be inside a listed input directory')
    return output


def make_asset(sample, binding):
    primary = Path(binding['primary_path']).resolve()
    sources = {sid: Path(p).resolve() for sid, p in binding['sources'].items()}
    jpg = sources.get(binding.get('jpeg_source_id'))
    raw = sources.get(binding.get('raw_source_id'))
    # Stable unique internal stem prevents cross-directory filename collisions.
    stem = 'asset-' + hashlib.sha256(sample['asset_id'].encode('utf-8')).hexdigest()[:24]
    return PhotoAsset(stem, primary, primary, raw, jpg, datetime(1970, 1, 1), primary.suffix.lower())


def apply_confirmation(asset, asset_id, settings, confirmations, manifest_sha256):
    if confirmations is None:
        return settings
    oe.require(confirmations.get('schema_version') == 'real-sample-subject-confirmations-v1' and confirmations.get('manifest_sha256') == manifest_sha256, 'subject confirmation manifest/version mismatch')
    entries = confirmations.get('entries')
    oe.require(isinstance(entries, dict), 'subject confirmations entries object required')
    entry = entries.get(asset_id)
    if entry is None:
        return settings
    oe.require(isinstance(entry, dict) and CONFIRMATION_KEY in entry, 'existing complete subject confirmation required; quality annotations are not confirmations')
    configured = CropSettings(**{**asdict(settings), 'photos': {settings.key(asset): deepcopy(entry)}})
    oe.require(confirmed_selection(asset, configured) is not None, 'subject confirmation stale or invalid; reconfirm through existing subject workflow, not this evaluator')
    return configured


def _write_export(directory, documents):
    directory.mkdir(exist_ok=False)
    for key in ('predictions', 'annotations', 'catalog', 'diagnostics', 'summary'):
        atomic_json(directory / (key + '.json'), documents[key])
    with (directory / 'cases.jsonl').open('x', encoding='utf-8') as handle:
        for case in documents['cases']:
            handle.write(json.dumps(case, ensure_ascii=False, allow_nan=False) + '\n')


def run_batch(samples, path_map, config, output, *, confirmations=None, cancel_check=None, input_check=None):
    """Run explicit inputs. Test callbacks are in-process only, never CLI config.

    A root complete.json means all publication steps finished, not every photo
    was measurable. Cancel/failure keeps an incomplete audit directory, never a
    complete marker, and never overwrites an earlier batch.
    """
    from evaluation_inputs import preflight, source_fingerprint
    started = time.perf_counter()
    settings = validate_config(config)
    output = safe_output(output, path_map)
    oe.require(tool_identity() == _LOADED_TOOLS, 'tool source changed since import')
    immutable = oe.canonical_hash([samples, path_map, config, confirmations])
    def check_inputs():
        oe.require(oe.canonical_hash([samples, path_map, config, confirmations]) == immutable, 'input documents changed during run')
        if input_check:
            input_check()
    oe.require(set(config.get('asset_modes', {})) <= {s['asset_id'] for s in samples['rows']}, 'asset_modes contains unlisted asset')
    if confirmations:
        oe.require(set(confirmations.get('entries', {})) <= {s['asset_id'] for s in samples['rows']}, 'confirmation contains unlisted asset')
    recorder = MeasurementRecorder(synthetic=samples.get('evaluation_kind') == 'synthetic')
    oe.require(recorder.identity.get('verified') is True, 'clean verified executing source required; no historical/main identity substitution')
    inspected = preflight(samples, path_map, config)
    check_inputs()
    def check_sources():
        for checked in inspected['rows']:
            for source in checked.get('sources', {}).values():
                if 'fingerprint' in source:
                    oe.require(source_fingerprint(Path(source['path'])) == source['fingerprint'],
                               'listed source changed during batch; no complete publication')
    output.mkdir(parents=True, exist_ok=False)
    workspace = output / 'workspace'
    workspace.mkdir()
    recorder.prepare_asset = lambda asset: build_preview(asset, workspace / "previews")
    statuses = []
    assets, bindings = [], {}
    measurement_seconds = 0.0
    documents = None
    status = 'running'
    try:
        with operation(output):
            atomic_json(output / 'inputs.json', {'schema_version': SCHEMA, 'samples': samples,
                'configuration': config, 'manifest_sha256': oe.canonical_hash(samples),
                'path_map_sha256': oe.canonical_hash(path_map), 'confirmation_sha256': oe.canonical_hash(confirmations),
                'code_identity': recorder.identity, 'tool_identity': _LOADED_TOOLS})
            atomic_json(output / 'preflight.json', inspected)
            indexed = {row['asset_id']: row for row in inspected['rows']}
            for sample in samples['rows']:
                sid = sample['asset_id']
                item = {'asset_id': sid, 'status': 'pending', 'reasons': []}
                statuses.append(item)
                if cancel_check and cancel_check():
                    raise KeyboardInterrupt('explicit cancellation')
                check_inputs()
                checked = indexed[sid]
                if checked['status'] != 'ready':
                    item.update(status=checked['status'], reasons=checked.get('problems', []))
                    continue
                asset = make_asset(sample, path_map['bindings'][sid])
                mode = config.get('asset_modes', {}).get(sid, config['analysis_mode'])
                if sample['scene_type'] not in (mode, 'mixed'):
                    item.update(status='needs_confirmation', reasons=['explicit_scene_type_and_measurement_mode_disagree'])
                    continue
                try:
                    effective = apply_confirmation(asset, sid, settings, confirmations, oe.canonical_hash(samples))
                except (oe.ContractError, ValueError) as exc:
                    item.update(status='needs_confirmation', reasons=[str(exc)])
                    continue
                before = time.perf_counter()
                try:
                    # Same shared decode scope as production scan: preview and
                    # scoring borrow pixels, never repeatedly full-decode by design.
                    with shared_decode(asset):
                        screen_assets([asset], analysis_mode=mode, body_check=config['body_check'],
                                      crop_settings=effective, cache_dir=workspace / 'cache', evaluation_recorder=recorder)
                    item.update(status='measured', reasons=[asset.screening_reason] if asset.screening_reason else [])
                except KeyboardInterrupt:
                    item.update(status='cancelled', reasons=['measurement_cancelled'])
                    raise
                except Exception as exc:
                    item.update(status='failed', reasons=[f'{type(exc).__name__}: {exc}'])
                finally:
                    measurement_seconds += time.perf_counter() - before
                assets.append(asset)
                bindings[sid] = path_map['bindings'][sid]
            check_inputs()
            if cancel_check and cancel_check():
                raise KeyboardInterrupt('explicit cancellation before freeze')
            result = ScanResult(assets, workspace / 'previews', workspace / 'contacts', workspace,
                                workspace / 'groups.json', input_dir=None)
            frozen = recorder.freeze(result, samples, bindings, output / 'frozen')
            documents = exporter.load_completed_set(frozen)
            # Failed/missing/preflight cases stay in the complete manifest. Add
            # explicit nonmeasurement statuses, never synthesize recommendations.
            by_id = {r['asset_id']: r for r in documents['predictions']['rows']}
            for sample, item in zip(samples['rows'], statuses):
                row = by_id.get(sample['asset_id'])
                if item['status'] == 'measured':
                    if row is None or row['status'] != 'ok':
                        item['status'] = 'provenance_insufficient'
                        item['reasons'].append(row.get('failure_reason', 'missing_export') if row else 'missing_export')
                    else:
                        item['status'] = 'success'
                elif row is None and item['status'] != 'missing':
                    source = next(s for s in sample['sources'] if s['source_id'] == sample['evaluation_source_id'])
                    row = {'asset_id': sample['asset_id'], 'sample_version': sample['sample_version'],
                        'evaluated_source_id': source['source_id'], 'input_sha256': source['sha256'],
                        'status': 'failed', 'recommendation': None, 'reasons': [],
                        'failure_reason': item['status'] + ': ' + '; '.join(map(str, item['reasons']))}
                    documents['predictions']['rows'].append(row)
                item['prediction_status'] = row['status'] if row else 'missing'
            report = oe.evaluate(samples, documents['predictions'], documents['annotations'], documents['catalog'])
            documents['cases'] = report.pop('cases')
            documents['summary'] = report
            documents['diagnostics']['batch_statuses'] = statuses
            documents['diagnostics']['preflight_manifest_sha256'] = oe.canonical_hash(samples)
            _write_export(output / 'export', documents)
            check_inputs()
            oe.require(code_identity() == recorder.identity, 'source changed during batch publication')
            status = 'complete' if all(r['status'] == 'success' for r in statuses) else 'partial'
            audit = {'schema_version': SCHEMA, 'status': status, 'run_id': recorder.run_id,
                'source_git': recorder.identity['source_git'], 'manifest_sha256': oe.canonical_hash(samples),
                'rows': statuses, 'total_seconds': time.perf_counter() - started,
                'measurement_seconds': measurement_seconds, 'receipt_cost': recorder.cost,
                'preflight_cost': inspected.get('cost'),
                'timing_definition': 'elapsed through audit creation: includes preflight, measurement (preview and receipt observation), freeze/export; excludes audit/marker writes and final publication checks; hash_seconds is a subset of observation_seconds; do not add nested values',
                'real_world_accuracy': {'value': None, 'status': '尚未测量'}}
            atomic_json(output / 'batch-status.json', audit)
            names = ['inputs.json', 'preflight.json', 'batch-status.json', 'frozen/complete.json']
            names += ['export/' + n for n in ('predictions.json', 'annotations.json', 'catalog.json', 'diagnostics.json', 'summary.json', 'cases.jsonl')]
            marker = {'schema_version': SCHEMA, 'status': status, 'publication_complete': True,
                      'files': {n: oe.digest((output / n).read_bytes()) for n in names}}
            check_inputs()
            check_sources()
            oe.require(tool_identity() == _LOADED_TOOLS, 'tool source changed during batch')
            atomic_json(output / 'complete.json', marker)
            return audit
    except BaseException as exc:
        # Best effort only. Absence of complete.json is authoritative even when
        # disk failure prevents writing this diagnostic. Never erase evidence.
        for item in statuses:
            if item['status'] == 'pending':
                item.update(status='cancelled' if isinstance(exc, KeyboardInterrupt) else 'not_run', reasons=['batch_interrupted'])
        for sample in samples['rows'][len(statuses):]:
            statuses.append({'asset_id': sample['asset_id'], 'status': 'cancelled' if isinstance(exc, KeyboardInterrupt) else 'not_run', 'reasons': ['batch_interrupted']})
        try:
            atomic_json(output / 'batch-status.json', {'schema_version': SCHEMA,
                'status': 'cancelled' if isinstance(exc, KeyboardInterrupt) else 'failed',
                'error': f'{type(exc).__name__}: {exc}', 'rows': statuses,
                'total_seconds': time.perf_counter() - started, 'measurement_seconds': measurement_seconds,
                'receipt_cost': recorder.cost, 'publication_complete': False})
        except OSError:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('samples', 'paths', 'config', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--confirmations', type=Path)
    args = parser.parse_args(argv)
    try:
        paths = {k: getattr(args, k) for k in ('samples', 'paths', 'config', 'confirmations') if getattr(args, k) is not None}
        raw = {k: p.read_bytes() for k, p in paths.items()}
        docs = {k: exporter.parse_json(v) for k, v in raw.items()}
        def unchanged():
            oe.require(all(p.read_bytes() == raw[k] for k, p in paths.items()), 'input/config/confirmation document changed during batch')
        audit = run_batch(docs['samples'], docs['paths'], docs['config'], args.output,
                          confirmations=docs.get('confirmations'), input_check=unchanged)
        print(json.dumps({'status': audit['status'], 'output': str(args.output), 'real_world_accuracy': '尚未测量'}, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        print('batch cancelled; no complete publication', file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        print(f'real sample evaluation: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
