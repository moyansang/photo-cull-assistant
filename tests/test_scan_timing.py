from concurrent.futures import ThreadPoolExecutor
from ai_cull_assistant import scan_timing as timing


def test_nested_timings_do_not_double_count(monkeypatch):
    clock = iter([0, 1, 3, 5])
    monkeypatch.setattr(timing, 'perf_counter', lambda: next(clock))
    with timing.collect_timings() as result:
        with timing.timed('grouping'):
            with timing.timed('detection'):
                pass
    assert result == {'detection': 2, 'grouping': 3}


def test_each_worker_has_its_own_timing_dictionary():
    def run(key):
        with timing.collect_timings() as result:
            with timing.timed(key):
                pass
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(run, ['preview', 'clarity']))
    assert set(values[0]) == {'preview'}
    assert set(values[1]) == {'clarity'}
