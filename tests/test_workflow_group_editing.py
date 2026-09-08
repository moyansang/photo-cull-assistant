import json
from datetime import datetime
from pathlib import Path

from PIL import Image

from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.workflow import ScanResult, persist_manual_groups, regenerate_contact_sheets, reset_auto_groups


def make_asset(tmp_path: Path, stem: str, group_id: int, second: int):
    preview = tmp_path / f'{stem}_preview.jpg'
    Image.new('RGB', (80, 60), 'white').save(preview)
    image_path = tmp_path / f'{stem}.jpg'
    Image.new('RGB', (80, 60), 'white').save(image_path)
    return PhotoAsset(
        stem=stem,
        display_path=image_path,
        primary_path=image_path,
        raw_path=None,
        jpg_path=image_path,
        captured_at=datetime(2026, 1, 1, 12, 0, second),
        ext='.jpg',
        group_id=group_id,
        preview_path=preview,
    )


def make_result(tmp_path: Path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    contact_dir = workspace / 'contact_sheets'
    preview_dir = workspace / 'previews'
    contact_dir.mkdir()
    preview_dir.mkdir()
    assets = [
        make_asset(tmp_path, 'A', 1, 0),
        make_asset(tmp_path, 'B', 1, 1),
        make_asset(tmp_path, 'C', 2, 10),
    ]
    return ScanResult(
        assets=assets,
        preview_dir=preview_dir,
        contact_dir=contact_dir,
        workspace_dir=workspace,
        group_store_path=workspace / 'groups.json',
        groups_loaded_from_store=False,
    )


def test_persist_manual_groups_marks_store_source_manual(tmp_path):
    result = make_result(tmp_path)

    persist_manual_groups(result)

    payload = json.loads(result.group_store_path.read_text(encoding='utf-8'))
    assert payload['source'] == 'manual'


def test_reset_auto_groups_overwrites_manual_grouping(tmp_path):
    result = make_result(tmp_path)
    for asset in result.assets:
        asset.group_id = 1
    persist_manual_groups(result)

    reset_auto_groups(result, 'standard')

    assert [a.group_id for a in result.assets] == [1, 1, 2]
    payload = json.loads(result.group_store_path.read_text(encoding='utf-8'))
    assert payload['source'] == 'auto'


def test_regenerate_contact_sheets_removes_stale_pages(tmp_path):
    result = make_result(tmp_path)
    stale = result.contact_dir / 'contact_sheet_999.jpg'
    stale.write_bytes(b'stale')

    pages = regenerate_contact_sheets(result, photos_per_page=2, columns=2)

    assert stale.exists() is False
    assert len(pages) == 2
    assert all(path.exists() for path in pages)
