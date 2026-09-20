"""Small, dependency-free resource budget for concurrent photo scans.

Windows uses ``GlobalMemoryStatusEx`` for machine memory and
``GetProcessMemoryInfo`` for the current process working set.  Other platforms
use standard-library facilities when available.  A missing memory reading is
treated as a reason to scan serially, rather than guessing optimistically.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import os
from pathlib import Path
import sys
from typing import Callable


GIB = 1024**3
MIB = 1024**2
DEFAULT_PHOTO_BYTES = 512 * MIB
MIN_OS_RESERVE_BYTES = 2 * GIB


@dataclass(frozen=True)
class ResourceSnapshot:
    """A point-in-time view of the resources used to size a scan."""

    cpu_count: int | None
    total_memory_bytes: int | None
    available_memory_bytes: int | None
    process_rss_bytes: int | None

    # Short aliases make the object convenient for callers without making the
    # units ambiguous in its stored fields.
    @property
    def total_bytes(self) -> int | None:
        return self.total_memory_bytes

    @property
    def available_bytes(self) -> int | None:
        return self.available_memory_bytes

    @property
    def rss_bytes(self) -> int | None:
        return self.process_rss_bytes


def _positive(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def _windows_memory() -> tuple[int | None, int | None, int | None]:
    """Return total memory, available memory, and process working set."""

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    # PROCESS_MEMORY_COUNTERS has pointer-sized counters, so it remains valid
    # for both 32-bit and 64-bit packaged builds.
    size_t = ctypes.c_size_t

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", size_t),
            ("WorkingSetSize", size_t),
            ("QuotaPeakPagedPoolUsage", size_t),
            ("QuotaPagedPoolUsage", size_t),
            ("QuotaPeakNonPagedPoolUsage", size_t),
            ("QuotaNonPagedPoolUsage", size_t),
            ("PagefileUsage", size_t),
            ("PeakPagefileUsage", size_t),
        ]

    total = available = rss = None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        global_status = kernel32.GlobalMemoryStatusEx
        global_status.argtypes = [ctypes.POINTER(MemoryStatusEx)]
        global_status.restype = ctypes.c_int
        if global_status(ctypes.byref(status)):
            total = _positive(status.ullTotalPhys)
            available = _positive(status.ullAvailPhys)

        get_current_process = kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = ctypes.c_void_p
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        get_process_memory = psapi.GetProcessMemoryInfo
        get_process_memory.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        get_process_memory.restype = ctypes.c_int
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if get_process_memory(
            get_current_process(), ctypes.byref(counters), counters.cb
        ):
            rss = _positive(counters.WorkingSetSize)
    except (AttributeError, OSError, TypeError, ValueError):
        # Either result may still be useful if the other Win32 call failed.
        pass
    return total, available, rss


def _portable_memory() -> tuple[int | None, int | None, int | None]:
    total = available = rss = None
    try:
        page_size = _positive(os.sysconf("SC_PAGE_SIZE"))
        pages = _positive(os.sysconf("SC_PHYS_PAGES"))
        available_pages = _positive(os.sysconf("SC_AVPHYS_PAGES"))
        if page_size is not None and pages is not None:
            total = page_size * pages
        if page_size is not None and available_pages is not None:
            available = page_size * available_pages
    except (AttributeError, OSError, TypeError, ValueError):
        pass

    # Linux exposes current resident pages directly.  resource.ru_maxrss is a
    # peak value and therefore is used only as a conservative last resort.
    try:
        fields = Path("/proc/self/statm").read_text("ascii").split()
        page_size = _positive(os.sysconf("SC_PAGE_SIZE"))
        resident_pages = _positive(fields[1])
        if page_size is not None and resident_pages is not None:
            rss = page_size * resident_pages
    except (IndexError, OSError, TypeError, ValueError):
        try:
            import resource

            peak = _positive(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            if peak is not None:
                rss = peak if sys.platform == "darwin" else peak * 1024
        except (ImportError, OSError, ValueError):
            pass
    return total, available, rss


def capture_resource_snapshot() -> ResourceSnapshot:
    """Read CPU, physical memory, and current process resident memory."""

    cpu_count = _positive(os.cpu_count())
    if sys.platform == "win32":
        total, available, rss = _windows_memory()
    else:
        total, available, rss = _portable_memory()
    return ResourceSnapshot(cpu_count, total, available, rss)


class ResourceBudget:
    """Choose a conservative scan concurrency from CPU and observed memory.

    Create the budget before the serial warm-up photo, take another snapshot
    after it, then call ``observe``.  Supplying a sampled peak RSS is preferable:
    an RSS difference measured only at the start and end can miss temporary
    decoder/model allocations and therefore underestimate the true peak.
    """

    def __init__(
        self,
        snapshot_provider: Callable[[], ResourceSnapshot] = capture_resource_snapshot,
        *,
        initial_snapshot: ResourceSnapshot | None = None,
        reserve_bytes: int | None = None,
        per_photo_floor_bytes: int = DEFAULT_PHOTO_BYTES,
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self.initial_snapshot = initial_snapshot or snapshot_provider()
        self.reserve_bytes = _positive(reserve_bytes) if reserve_bytes is not None else None
        self.per_photo_floor_bytes = max(1, int(per_photo_floor_bytes))
        self.observed_per_photo_bytes: int | None = None
        self.steady_photo_bytes: int | None = None
        self.retained_growth_bytes = 0
        self._ready_workers: set[int] = set()

    def register_worker(self, worker_id: int, models_ready: bool) -> None:
        if models_ready:
            self._ready_workers.add(worker_id)

    def snapshot(self) -> ResourceSnapshot:
        return self._snapshot_provider()

    @staticmethod
    def _rss(value: ResourceSnapshot | int | None) -> int | None:
        if isinstance(value, ResourceSnapshot):
            return _positive(value.process_rss_bytes)
        return _positive(value)

    def observe(
        self,
        start_rss: ResourceSnapshot | int | None,
        end_rss: ResourceSnapshot | int | None,
        peak_rss_bytes: int | None = None,
    ) -> int:
        """Record one photo's RSS growth and return the conservative estimate.

        ``start_rss`` and ``end_rss`` may be snapshots or raw RSS byte counts.
        A caller that samples RSS during warm-up can pass its highest sample as
        ``peak_rss_bytes``.
        """

        start_rss = self._rss(start_rss)
        end_rss = self._rss(end_rss)
        peak_rss = self._rss(peak_rss_bytes)
        candidates = [value for value in (end_rss, peak_rss) if value is not None]
        growth = 0
        if start_rss is not None and candidates:
            growth = max(0, max(candidates) - start_rss)
        estimate = max(self.per_photo_floor_bytes, (growth * 5 + 3) // 4)
        # Resident allocations are already charged to available system memory.
        # Reused, initialized workers need only the transient peak above BOTH
        # endpoints. New workers still pay the full conservative cold estimate.
        if start_rss is not None and end_rss is not None and peak_rss is not None:
            transient = max(0, peak_rss - max(start_rss, end_rss))
            steady = max(self.per_photo_floor_bytes, (transient * 5 + 3) // 4)
            self.steady_photo_bytes = max(self.steady_photo_bytes or 0, steady)
            self.retained_growth_bytes = max(self.retained_growth_bytes, end_rss - start_rss)
        if self.observed_per_photo_bytes is None:
            self.observed_per_photo_bytes = estimate
        else:
            self.observed_per_photo_bytes = max(self.observed_per_photo_bytes, estimate)
        return self.observed_per_photo_bytes

    def choose_workers(self, snapshot: ResourceSnapshot | None = None, *, in_flight: int = 0) -> int:
        """Return the resource ceiling; throughput policy chooses within it."""

        current = snapshot or self.snapshot()
        cpu_count = _positive(current.cpu_count)
        if cpu_count is None or cpu_count < 4:
            cpu_limit = 1
        elif cpu_count < 8:
            cpu_limit = 2
        elif cpu_count < 12:
            cpu_limit = 4
        elif cpu_count < 16:
            cpu_limit = 6
        else:
            cpu_limit = 8

        total = _positive(current.total_memory_bytes)
        available = _positive(current.available_memory_bytes)
        self.last_decision = dict(cpu_limit=cpu_limit,
                                  total_memory_bytes=total, available_memory_bytes=available,
                                  process_rss_bytes=current.process_rss_bytes,
                                  cpu_count=cpu_count)
        if total is None or available is None:
            self.last_decision.update(limiting_factor='memory_read_unavailable', resource_cap=1)
            return 1

        reserve = self.reserve_bytes
        if reserve is None:
            reserve = max(MIN_OS_RESERVE_BYTES, total // 4)
        usable = max(0, available - reserve)
        per_photo = self.observed_per_photo_bytes or self.per_photo_floor_bytes
        # Available memory is headroom for NEW work; active photos already use
        # part of the process working set. The coordinator also retains the last
        # idle ceiling so partially allocated workers cannot raise that ceiling.
        steady = self.steady_photo_bytes or per_photo
        active = max(0, min(8, int(in_flight)))
        ready = len(self._ready_workers)
        # Assume active tasks occupy ready threads first. This leaves the most
        # conservative mix of cold/reusable threads for additional dispatch.
        idle_ready = max(0, ready - active)
        def required(extra):
            warm_slots = min(extra, idle_ready)
            return warm_slots * steady + (extra - warm_slots) * per_photo
        free_slots = max(n for n in range(9) if required(n) <= usable)
        memory_limit = max(1, min(8, active + free_slots))
        limit = min(cpu_limit, memory_limit, 8)
        cap = next(level for level in (8, 6, 4, 2, 1) if level <= limit)
        self.last_decision.update(reserve_bytes=reserve, usable_memory_bytes=usable,
                                  per_photo_bytes=per_photo, memory_limit=memory_limit,
                                  cold_worker_peak_bytes=per_photo,
                                  steady_photo_bytes=steady,
                                  retained_growth_bytes=self.retained_growth_bytes,
                                  ready_workers=ready,
                                  required_memory_bytes=required(max(0, cap-active)),
                                  free_slots=free_slots, in_flight=active,
                                  resource_cap=cap,
                                  limiting_factor='memory' if memory_limit < cpu_limit else 'cpu')
        return cap


# The scan integration uses this more task-specific name; ResourceBudget stays
# available to describe the policy independently and for focused tests.
ScanBudget = ResourceBudget


__all__ = [
    "DEFAULT_PHOTO_BYTES",
    "GIB",
    "MIB",
    "MIN_OS_RESERVE_BYTES",
    "ResourceBudget",
    "ResourceSnapshot",
    "ScanBudget",
    "capture_resource_snapshot",
]
