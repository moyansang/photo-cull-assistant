"""Optional local diagnostics, separate from the UI log and analysis results."""
from contextlib import contextmanager
from threading import local
from time import perf_counter
from datetime import datetime
import json
import uuid
from .workspace_log import append_log, DETAIL_PREFIX

_state = local()


@contextmanager
def collect_diagnostics():
    previous = getattr(_state, 'record', None)
    record = {'operations': {}}
    _state.record = record
    started = perf_counter()
    try:
        yield record
    finally:
        record['elapsed_seconds'] = perf_counter() - started
        _state.record = previous


def note(key, value):
    record = getattr(_state, 'record', None)
    if record is not None:
        record[key] = value


@contextmanager
def operation(name):
    record = getattr(_state, 'record', None)
    started = perf_counter()
    try:
        yield
    finally:
        if record is not None:
            entry = record['operations'].setdefault(name, {'count': 0, 'seconds': 0.0})
            entry['count'] += 1
            entry['seconds'] += perf_counter() - started


class DiagnosticReport:
    def __init__(self, workspace):
        self.workspace = workspace
        self.run_id = datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        self.summary = {'photos': 0, 'full_raw_decode_calls': 0, 'full_raw_decode_photos': 0,
                        'repeated_full_decode_photos': 0, 'preview_raw_fallback_photos': 0,
                        'rescue_photos': 0, 'operations': {}, 'slowest_10': []}

    def __enter__(self):
        return self

    def scheduler(self, budget, workers, cap, active, reason):
        snapshot = budget.snapshot()
        row = dict(type='scheduler', run_id=self.run_id, workers=workers,
                   resource_cap=cap, active=active, reason=reason,
                   cpu_count=snapshot.cpu_count,
                   total_memory_bytes=snapshot.total_memory_bytes,
                   available_memory_bytes=snapshot.available_memory_bytes,
                   process_rss_bytes=snapshot.process_rss_bytes,
                   per_photo_bytes=getattr(budget, 'observed_per_photo_bytes', None),
                   reserve_bytes=getattr(budget, 'reserve_bytes', None)
                       or max(2 * 1024**3, (snapshot.total_memory_bytes or 0) // 4))
        if reason != 'warmup':
            row.update(getattr(budget, 'last_decision', {}))
            row['resource_cap'] = cap
        try:
            append_log(self.workspace, DETAIL_PREFIX+json.dumps(row, ensure_ascii=False))
        except OSError:
            pass

    def add(self, filename, diagnostics, timings):
        row = dict(diagnostics, filename=filename, stage_seconds=dict(timings), run_id=self.run_id)
        try:
            append_log(self.workspace, DETAIL_PREFIX+json.dumps(row, ensure_ascii=False))
        except OSError:
            return
        summary = self.summary
        summary['photos'] += 1
        ops = row['operations']
        full = ops.get('raw_full_decode', {}).get('count', 0)
        summary['full_raw_decode_calls'] += full
        summary['full_raw_decode_photos'] += int(full > 0)
        summary['repeated_full_decode_photos'] += int(full > 1)
        summary['preview_raw_fallback_photos'] += int('raw_preview_half_decode' in ops)
        summary['rescue_photos'] += int(any(key in ops for key in ('yunet_rotation', 'head_detection', 'head_face_retry')))
        for key, value in ops.items():
            entry = summary['operations'].setdefault(key, {'count': 0, 'seconds': 0.0})
            entry['count'] += value['count']
            entry['seconds'] += value['seconds']
        summary['slowest_10'] = sorted(summary['slowest_10']+[row], key=lambda item: item['elapsed_seconds'], reverse=True)[:10]

    def __exit__(self, kind, value, traceback):
        try:
            append_log(self.workspace, DETAIL_PREFIX+json.dumps(dict(self.summary,
                run_id=self.run_id, type='summary', error_interrupted=kind is not None), ensure_ascii=False))
        except OSError:
            pass
