import os
from pathlib import Path

from PIL import Image

from ai_cull_assistant.group_store import save_groups
from ai_cull_assistant.workflow import run_scan


def make_jpg(path: Path, mtime: float):
    Image.new('RGB', (80, 60), 'white').save(path, 'JPEG')
    os.utime(path, (mtime, mtime))


def test_run_scan_creates_group_store_and_reuses_saved_groups(tmp_path):
    photo_dir = tmp_path / 'photos'
    workspace = tmp_path / 'workspace'
    photo_dir.mkdir()
    make_jpg(photo_dir / 'A.jpg', 1000.0)
    make_jpg(photo_dir / 'B.jpg', 1000.5)
    make_jpg(photo_dir / 'C.jpg', 1010.0)

    first = run_scan(photo_dir, workspace, grouping_preset='standard', photos_per_page=4, columns=2)
    store_path = workspace / 'groups.json'
    assert store_path.exists()
    assert [a.group_id for a in first.assets] == [1, 1, 2]

    # Simulate a user's manual merge, then persist it.
    for asset in first.assets:
        asset.group_id = 1
    save_groups(first.assets, store_path, source='manual', collection_key=str(photo_dir.resolve()))

    second = run_scan(photo_dir, workspace, grouping_preset='strict', photos_per_page=4, columns=2)
    assert [a.group_id for a in second.assets] == [1, 1, 1]
    assert second.groups_loaded_from_store is True
