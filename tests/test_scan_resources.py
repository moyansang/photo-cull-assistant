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
