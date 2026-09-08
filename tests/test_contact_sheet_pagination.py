from datetime import datetime
from pathlib import Path

from ai_cull_assistant.contact_sheet import paginate_by_group
from ai_cull_assistant.models import PhotoAsset


def make_asset(stem: str, group_id: int):
    path = Path(f'/tmp/{stem}.jpg')
    return PhotoAsset(
        stem=stem,
        display_path=path,
        primary_path=path,
        raw_path=None,
        jpg_path=path,
        captured_at=datetime(2026, 1, 1),
        ext='.jpg',
        group_id=group_id,
    )


def test_paginate_by_group_does_not_split_group_when_it_fits_on_a_fresh_page():
    assets = [
        make_asset('A1', 1), make_asset('A2', 1), make_asset('A3', 1),
        make_asset('B1', 2), make_asset('B2', 2), make_asset('B3', 2),
    ]

    pages = paginate_by_group(assets, photos_per_page=4)

    assert [[a.stem for a in page] for page in pages] == [
        ['A1', 'A2', 'A3'],
        ['B1', 'B2', 'B3'],
    ]


def test_paginate_by_group_splits_only_oversized_group():
    assets = [make_asset(f'A{i}', 1) for i in range(1, 7)]

    pages = paginate_by_group(assets, photos_per_page=4)

    assert [[a.stem for a in page] for page in pages] == [
        ['A1', 'A2', 'A3', 'A4'],
        ['A5', 'A6'],
    ]


def test_layout_pagination_caps_page_height_for_many_single_photo_groups():
    from ai_cull_assistant.contact_sheet import MAX_PAGE_HEIGHT, estimate_page_height, paginate_for_layout

    assets = [make_asset(f'S{i:02d}', i) for i in range(1, 17)]

    pages = paginate_for_layout(assets, photos_per_page=16, columns=4)

    assert len(pages) > 1
    assert all(estimate_page_height(page, 4) <= MAX_PAGE_HEIGHT for page in pages)
    assert sum(len(page) for page in pages) == 16
