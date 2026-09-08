from datetime import datetime, timedelta
from pathlib import Path

from ai_cull_assistant.grouping import assign_groups
from ai_cull_assistant.models import PhotoAsset


def make_asset(stem: str, seconds: float):
    base = datetime(2026, 1, 1, 12, 0, 0)
    t = base + timedelta(seconds=seconds)
    path = Path(f'/tmp/{stem}.jpg')
    return PhotoAsset(
        stem=stem,
        display_path=path,
        primary_path=path,
        raw_path=None,
        jpg_path=path,
        captured_at=t,
        ext='.jpg',
    )


def test_assign_groups_by_time():
    assets = [make_asset('A', 0), make_asset('B', 0.5), make_asset('C', 5)]
    assign_groups(assets, 'standard')
    assert [a.group_id for a in assets] == [1, 1, 2]
