import pytest

from ai_cull_assistant.scan_adaptive import (
    AdaptiveConcurrencyPolicy,
    WORKER_LEVELS,
)


def observe_full_batches(policy, workers, *, batches, elapsed):
    for _ in range(batches):
        assert policy.choose(workers) == workers
        policy.observe(workers, workers, elapsed)


def establish_two_worker_baseline(policy):
    # Six two-photo batches are needed to reach the 12-photo minimum.
    observe_full_batches(policy, 2, batches=6, elapsed=1.0)


def test_starts_at_two_and_never_exceeds_supported_cap():
    policy = AdaptiveConcurrencyPolicy()

    assert WORKER_LEVELS == (1, 2, 4, 6, 8)
    assert policy.choose(1) == 1
    assert policy.choose(3) == 2
    assert policy.choose(99) == 2
    assert policy.best_workers == 2


def test_waits_for_three_batches_and_twelve_photos_before_probing():
    policy = AdaptiveConcurrencyPolicy()

    observe_full_batches(policy, 2, batches=5, elapsed=1.0)
    assert policy.choose(8) == 2

    policy.observe(2, 2, 1.0)
    assert policy.choose(8) == 4
    assert policy.best_workers == 2


def test_accepts_next_level_only_after_eight_percent_gain():
    policy = AdaptiveConcurrencyPolicy()
    establish_two_worker_baseline(policy)  # 2 photos/second

    observe_full_batches(policy, 4, batches=3, elapsed=1.8)  # 2.22 photos/second

    assert policy.best_workers == 4
    assert policy.choose(8) == 6


def test_rejects_weak_probe_reverts_and_stops_future_probing():
    policy = AdaptiveConcurrencyPolicy()
    establish_two_worker_baseline(policy)  # 2 photos/second

    observe_full_batches(policy, 4, batches=3, elapsed=1.95)  # 2.05 photos/second

    assert policy.best_workers == 2
    assert policy.choose(8) == 2
    observe_full_batches(policy, 2, batches=12, elapsed=0.1)
    assert policy.choose(8) == 2


def test_partial_batches_do_not_contribute_to_measurement_window():
    policy = AdaptiveConcurrencyPolicy()

    for _ in range(20):
        assert policy.choose(8) == 2
        assert policy.observe(2, 1, 0.01) is False

    assert policy.choose(8) == 2
    establish_two_worker_baseline(policy)
    assert policy.choose(8) == 4


def test_temporary_cap_drop_does_not_poison_active_probe():
    policy = AdaptiveConcurrencyPolicy()
    establish_two_worker_baseline(policy)
    assert policy.choose(8) == 4

    for _ in range(20):
        assert policy.choose(2) == 2
        assert policy.observe(2, 2, 100.0) is False

    observe_full_batches(policy, 4, batches=3, elapsed=1.8)
    assert policy.best_workers == 4


def test_accepted_levels_ramp_one_step_at_a_time_to_eight():
    policy = AdaptiveConcurrencyPolicy()
    establish_two_worker_baseline(policy)
    observe_full_batches(policy, 4, batches=3, elapsed=1.8)
    observe_full_batches(policy, 6, batches=3, elapsed=2.4)
    observe_full_batches(policy, 8, batches=3, elapsed=2.9)

    assert policy.best_workers == 8
    assert policy.choose(8) == 8


def test_optional_preferred_level_is_used_then_reevaluated_downward():
    policy = AdaptiveConcurrencyPolicy(preferred=7)

    assert policy.best_workers == 6
    assert policy.choose(8) == 6
    observe_full_batches(policy, 6, batches=3, elapsed=2.0)
    assert policy.choose(8) == 4
    observe_full_batches(policy, 4, batches=3, elapsed=2.0)
    assert policy.choose(8) == 8


def test_poor_remembered_level_can_step_down_without_oscillating():
    policy = AdaptiveConcurrencyPolicy(preferred=6)
    observe_full_batches(policy, 6, batches=3, elapsed=3.0)  # 3 photos/second

    observe_full_batches(policy, 4, batches=3, elapsed=1.2)  # 3.33 photos/second
    assert policy.best_workers == 4
    assert policy.choose(8) == 2

    observe_full_batches(policy, 2, batches=6, elapsed=1.0)  # 2 photos/second
    assert policy.best_workers == 4
    assert policy.choose(8) == 4


@pytest.mark.parametrize(
    ("workers", "count", "elapsed"),
    ((3, 3, 1.0), (2, 2, 0.0), (2, 2, -1.0)),
)
def test_invalid_observations_are_ignored(workers, count, elapsed):
    policy = AdaptiveConcurrencyPolicy()
    policy.choose(8)

    assert policy.observe(workers, count, elapsed) is False
    assert policy.best_workers == 2
