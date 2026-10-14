"""Keep cyclic Tk object finalization on the test's main thread.

UI tests create/destroy many Tcl interpreters and pump update(), not mainloop().
Automatic cyclic GC in a preview worker can otherwise finalize an old Tk
Variable there, block for the absent mainloop, and cascade into timeout/grab
failures in unrelated tests. Assertions, worker execution and timeouts remain
unchanged; only cyclic collection is explicitly performed on the owner thread.
"""
import gc
import threading
import pytest


@pytest.fixture(scope='session', autouse=True)
def main_thread_cyclic_collection():
    enabled = gc.isenabled()
    gc.disable()
    yield
    gc.collect()
    if enabled:
        gc.enable()


@pytest.fixture(autouse=True)
def collect_closed_ui_between_tests():
    yield
    assert threading.current_thread() is threading.main_thread()
    gc.collect()
