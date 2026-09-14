import json
from concurrent.futures import ThreadPoolExecutor
from ai_cull_assistant.scan_diagnostics import collect_diagnostics, operation, note, DiagnosticReport
from ai_cull_assistant.workspace_log import append_log, visible_log, DETAIL_PREFIX


def test_thread_isolated_diagnostics_and_complete_log(tmp_path):
    def scan(index):
        with collect_diagnostics() as row:
            note('preview_source', 'raw_embedded_jpeg')
            for _ in range(index):
                with operation('raw_full_decode'):
                    pass
            with operation('yunet_rotation'):
                pass
        return row
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(scan, [1, 2]))
    append_log(tmp_path, '扫描开始')
    with DiagnosticReport(tmp_path) as report:
        for index, row in enumerate(rows):
            report.add(f'照片{index}.RW2', row, {'decode': 1.0})
    append_log(tmp_path, '扫描完成')
    text = (tmp_path/'logs/session.log').read_text('utf-8')
    assert visible_log(text) == '扫描开始\n扫描完成\n'
    details = [json.loads(line[len(DETAIL_PREFIX):]) for line in text.splitlines() if line.startswith(DETAIL_PREFIX)]
    assert [row['operations']['raw_full_decode']['count'] for row in details[:2]] == [1, 2]
    assert details[-1]['full_raw_decode_calls'] == 3
    assert details[-1]['repeated_full_decode_photos'] == 1
    assert details[-1]['rescue_photos'] == 2
    assert len(details[-1]['slowest_10']) == 2
    assert not (tmp_path/'diagnostics').exists()


def test_diagnostic_write_failure_does_not_fail_scan(tmp_path, monkeypatch):
    import ai_cull_assistant.scan_diagnostics as module
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(module, 'append_log', fail)
    with DiagnosticReport(tmp_path) as report:
        report.add('A.RW2', {'operations': {}, 'elapsed_seconds': 1}, {})
