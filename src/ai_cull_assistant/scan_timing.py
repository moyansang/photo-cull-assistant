"""Thread-local stage timings; no image data or configuration secrets recorded."""
from contextlib import contextmanager
from functools import wraps
from threading import local
from time import perf_counter

_state = local()
LABELS = {'preview': '预览生成', 'detection': '人物检测', 'decode': 'RAW/原图解码',
          'clarity': '清晰度分析', 'save': '结果保存', 'grouping': '分组'}


@contextmanager
def collect_timings():
    previous = getattr(_state, 'values', None)
    previous_stack = getattr(_state, 'stack', [])
    values = {}
    _state.values = values
    _state.stack = []
    try:
        yield values
    finally:
        _state.values = previous
        _state.stack = previous_stack


@contextmanager
def timed(stage):
    values = getattr(_state, 'values', None)
    start = perf_counter()
    stack = getattr(_state, 'stack', [])
    frame = [0.0]
    stack.append(frame)
    try:
        yield
    finally:
        elapsed = perf_counter() - start
        stack.pop()
        if stack:
            stack[-1][0] += elapsed
        if values is not None:
            values[stage] = values.get(stage, 0.0) + max(0.0, elapsed - frame[0])


def measure(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with timed(stage):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def merge_timings(target, values):
    for key, value in values.items():
        target[key] = target.get(key, 0.0) + value


def timing_summary(values):
    return '；'.join(f'{label} {values.get(key, 0):.1f} 秒' for key, label in LABELS.items())
