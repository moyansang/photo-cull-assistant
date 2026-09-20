from ai_cull_assistant.scan_resources import (
    GIB,
    MIB,
    ResourceBudget,
    ResourceSnapshot,
    ScanBudget,
)


def snapshot(cpu, total_gib, available_gib, rss_mib=256):
    return ResourceSnapshot(
        cpu_count=cpu,
        total_memory_bytes=None if total_gib is None else total_gib * GIB,
        available_memory_bytes=None if available_gib is None else available_gib * GIB,
        process_rss_bytes=None if rss_mib is None else rss_mib * MIB,
    )


def test_resource_rich_machine_allows_six_or_eight_workers():
    current = snapshot(12, 32, 20)
    budget = ResourceBudget(lambda: current)

    assert budget.choose_workers() == 6
    assert ResourceBudget(lambda: snapshot(24, 32, 20)).choose_workers() == 8


def test_cpu_limits_default_and_small_machines():
    medium = snapshot(6, 16, 8)
    small = snapshot(2, 16, 8)

    assert ResourceBudget(lambda: medium).choose_workers() == 2
    assert ResourceBudget(lambda: small).choose_workers() == 1


def test_available_memory_after_os_reserve_limits_workers():
    # 16 GiB reserves 4 GiB for the OS, leaving 700 MiB for scan work.
    current = ResourceSnapshot(16, 16 * GIB, 4 * GIB + 700 * MIB, 300 * MIB)
    budget = ResourceBudget(lambda: current)

    assert budget.choose_workers() == 1


def test_unknown_memory_fails_closed_to_serial_scan():
    unknown = snapshot(16, None, None, None)

    assert ResourceBudget(lambda: unknown).choose_workers() == 1


def test_observed_peak_growth_reduces_memory_concurrency():
    initial = snapshot(16, 16, 8, 300)
    current = snapshot(16, 16, 6, 900)
    budget = ResourceBudget(lambda: current, initial_snapshot=initial)

    estimate = budget.observe(initial, current, peak_rss_bytes=2 * GIB + 300 * MIB)

    assert estimate == 5 * GIB // 2  # 25% headroom above observed growth
    # 6 GiB available minus the 4 GiB OS reserve fits one observed photo.
    assert budget.choose_workers() == 1


def test_observation_subtracts_initial_rss_and_applies_floor():
    initial = snapshot(8, 32, 20, 900)
    after = snapshot(8, 32, 20, 1100)
    budget = ResourceBudget(lambda: after, initial_snapshot=initial)

    assert budget.observe(initial, after) == 512 * MIB
    assert budget.observed_per_photo_bytes == 512 * MIB


def test_snapshot_provider_is_injectable_and_scan_name_is_same_policy():
    readings = iter((snapshot(8, 16, 8), snapshot(4, 16, 8)))
    budget = ScanBudget(lambda: next(readings))

    assert budget.initial_snapshot.cpu_count == 8
    assert budget.snapshot().cpu_count == 4
    assert ScanBudget is ResourceBudget


def test_active_photos_are_not_counted_twice_against_free_memory():
    current = snapshot(16, 16, 5)
    budget = ResourceBudget(lambda: current)
    # Reserve 4 GiB, 1 GiB free for two NEW photos in addition to two active.
    assert budget.choose_workers() == 2
    assert budget.choose_workers(in_flight=2) == 4
    assert budget.last_decision['free_slots'] == 2
    assert budget.last_decision['limiting_factor'] == 'memory'
    # If other applications consume the headroom, no extra photo is admitted.
    assert budget.choose_workers(snapshot(16, 16, 3), in_flight=2) == 2


def test_reused_worker_excludes_retained_memory_but_new_worker_pays_cold_cost():
    current = snapshot(16, 16, 6)
    budget = ResourceBudget(lambda: current)
    # Cold growth 800MiB -> 1000MiB with margin. Transient 400MiB -> floor512.
    budget.observe(100*MIB, 500*MIB, peak_rss_bytes=900*MIB)
    budget.register_worker(101, True)
    assert budget.observed_per_photo_bytes == 1000*MIB
    assert budget.steady_photo_bytes == 512*MIB
    assert budget.choose_workers() == 2
    assert budget.last_decision['required_memory_bytes'] == 1512*MIB
    budget.register_worker(102, False)
    assert budget.choose_workers() == 2
    assert budget.last_decision['ready_workers'] == 1
    budget.register_worker(102, True)
    budget.register_worker(103, True)
    budget.register_worker(104, True)
    assert budget.choose_workers() == 4
    assert budget.last_decision['required_memory_bytes'] == 2048*MIB
    assert budget.choose_workers(snapshot(16, 16, 4.4)) == 1


def test_later_large_transient_raises_steady_budget_and_missing_reading_stays_cold():
    budget = ResourceBudget(lambda: snapshot(16, 16, 6))
    budget.observe(100*MIB, 500*MIB, peak_rss_bytes=900*MIB)
    budget.observe(500*MIB, 500*MIB, peak_rss_bytes=1300*MIB)
    assert budget.steady_photo_bytes == 1000*MIB
    other = ResourceBudget(lambda: snapshot(16, 16, 6))
    other.observe(100*MIB, 900*MIB)
    assert other.steady_photo_bytes is None
    other.register_worker(1, True)
    assert other.choose_workers() == 2
    assert other.last_decision['steady_photo_bytes'] == 1000*MIB
