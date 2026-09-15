"""Adaptive scan concurrency driven by measured photo throughput.

The resource budget remains responsible for the hard CPU and memory cap.  This
module only decides whether moving to the next supported worker level actually
improves throughput on the current machine.  It deliberately has no machine or
filesystem access so the decision is deterministic and easy to test.
"""
from __future__ import annotations

from dataclasses import dataclass


WORKER_LEVELS = (1, 2, 4, 6, 8)


@dataclass
class _Window:
    batches: int = 0
    photos: int = 0
    elapsed: float = 0.0

    def add(self, photos: int, elapsed: float) -> None:
        self.batches += 1
        self.photos += photos
        self.elapsed += elapsed

    @property
    def ready(self) -> bool:
        return self.batches >= 3 and self.photos >= 12

    @property
    def throughput(self) -> float:
        return self.photos / self.elapsed


def _supported_level(value: int) -> int:
    """Clamp an arbitrary positive worker cap to a supported level."""

    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return 1
    return max(level for level in WORKER_LEVELS if level <= max(1, value))


class AdaptiveConcurrencyPolicy:
    """Probe worker levels and retain the fastest useful setting.

    A fresh policy starts at two workers.  Once a complete measurement window
    has been collected, it probes exactly one level higher.  A probe is kept
    only when it improves throughput by at least ``minimum_gain``; otherwise
    the policy returns to the previous level and stops probing for this run.

    ``choose`` must be called before dispatching a batch, and that returned
    worker count should be passed to ``observe`` when the full batch finishes.
    Partial batches and batches forced below the active level by a temporary
    resource-cap reduction are ignored.
    """

    def __init__(
        self,
        *,
        preferred: int | None = None,
        minimum_gain: float = 0.08,
    ) -> None:
        if minimum_gain < 0:
            raise ValueError("minimum_gain must be non-negative")
        self.minimum_gain = float(minimum_gain)
        self._best_workers = _supported_level(2 if preferred is None else preferred)
        self._best_throughput: float | None = None
        self._probe_workers: int | None = None
        self._probe_direction: str | None = None
        self._check_lower_first = preferred is not None and self._best_workers > 1
        self._accepted_downward = False
        self._window = _Window()
        self._probing_stopped = False
        self._last_choice: int | None = None
        self._last_choice_was_constrained = False

    @property
    def best_workers(self) -> int:
        """The preferred worker level learned so far, independent of a cap."""

        return self._best_workers

    def choose(self, cap: int) -> int:
        """Choose a supported worker count that never exceeds ``cap``."""

        bounded_cap = _supported_level(cap)
        target = self._probe_workers or self._best_workers
        chosen = min(target, bounded_cap)
        self._last_choice = chosen
        self._last_choice_was_constrained = chosen != target
        return chosen

    def observe(self, workers: int, count: int, elapsed: float) -> bool:
        """Observe one completed batch and return whether a decision was made.

        Only full batches (``count == workers``) selected by the latest
        ``choose`` call are measured.  This keeps a final short batch or a
        temporary CPU/memory cap drop from distorting the comparison.
        """

        try:
            workers = int(workers)
            count = int(count)
            elapsed = float(elapsed)
        except (TypeError, ValueError, OverflowError):
            return False
        if (
            workers not in WORKER_LEVELS
            or count != workers
            or elapsed <= 0
            or self._last_choice != workers
            or self._last_choice_was_constrained
        ):
            return False

        target = self._probe_workers or self._best_workers
        if workers != target:
            return False

        self._window.add(count, elapsed)
        if not self._window.ready:
            return False

        throughput = self._window.throughput
        self._window = _Window()

        if self._probe_workers is None:
            self._best_throughput = throughput
            if self._check_lower_first:
                self._check_lower_first = False
                self._schedule_lower_probe()
            else:
                self._schedule_upper_probe()
            return True

        assert self._best_throughput is not None
        required = self._best_throughput * (1.0 + self.minimum_gain)
        if throughput >= required:
            self._best_workers = self._probe_workers
            self._best_throughput = throughput
            self._probe_workers = None
            if self._probe_direction == "down":
                self._accepted_downward = True
                self._schedule_lower_probe()
            else:
                self._schedule_upper_probe()
        else:
            self._probe_workers = None
            if self._probe_direction == "down":
                # The remembered level remains useful.  After confirming that
                # one step down is not faster, it is still safe to try one
                # step up for a machine that has gained capacity.  If an
                # earlier downward probe was accepted, the old higher level
                # has already lost the comparison and must not be retried.
                if self._accepted_downward:
                    self._probing_stopped = True
                    self._probe_direction = None
                else:
                    self._schedule_upper_probe()
            else:
                self._probing_stopped = True
        return True

    def _schedule_upper_probe(self) -> None:
        if self._probing_stopped:
            return
        index = WORKER_LEVELS.index(self._best_workers)
        if index + 1 < len(WORKER_LEVELS):
            self._probe_workers = WORKER_LEVELS[index + 1]
            self._probe_direction = "up"
        else:
            self._probe_direction = None

    def _schedule_lower_probe(self) -> None:
        index = WORKER_LEVELS.index(self._best_workers)
        if index > 0:
            self._probe_workers = WORKER_LEVELS[index - 1]
            self._probe_direction = "down"
        else:
            self._probe_direction = None
            self._probing_stopped = True


__all__ = ["AdaptiveConcurrencyPolicy", "WORKER_LEVELS"]
