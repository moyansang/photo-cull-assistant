from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contact_sheet import ContactSheetSet, generate_contact_sheet_sets
from .exporter import copy_selected
from .grouping import assign_groups
from .group_store import load_groups, save_groups
from .models import PhotoAsset
from .crop_settings import CropSettings
from .preview import build_preview
from .scanner import scan_folder
from .screening import ScreeningResult, save_screening_results, screen_assets
from .selection_parser import parse_selection_text
from .lightroom_results import write_lightroom_results


@dataclass(slots=True)
class ScanResult:
    assets: list[PhotoAsset]
    preview_dir: Path
    contact_dir: Path
    workspace_dir: Path
    group_store_path: Path
    groups_loaded_from_store: bool = False
    input_dir: Path | None = None
    rejected_count: int = 0
    screening_results_path: Path | None = None
    main_pages: list[Path] | None = None
    rejected_pages: list[Path] | None = None


def run_scan(
    input_dir: str | Path,
    workspace_dir: str | Path,
    grouping_preset: str = "standard",
    photos_per_page: int = 20,
    columns: int = 4,
    *,
    technical_screening: bool = True,
    crop_settings: CropSettings = CropSettings(),
) -> ScanResult:
    workspace = Path(workspace_dir)
    preview_dir = workspace / "previews"
    contact_dir = workspace / "contact_sheets"
    group_store_path = workspace / "groups.json"
    screening_results_path = workspace / "screening_results.json"
    input_path = Path(input_dir).resolve()
    assets = scan_folder(input_path)
    for asset in assets:
        build_preview(asset, preview_dir)

    collection_key = str(input_path)
    groups_loaded = load_groups(assets, group_store_path, collection_key=collection_key)
    if not groups_loaded:
        assign_groups(assets, grouping_preset)
        save_groups(assets, group_store_path, source="auto", collection_key=collection_key)

    rejected_count = 0
    if technical_screening:
        screening_results = screen_assets(assets)
        save_screening_results(screening_results, screening_results_path)
        rejected_count = apply_auto_rejects(assets, screening_results)
    else:
        for asset in assets:
            asset.auto_rejected = False
            asset.screening_reason = "screening_disabled"
            asset.focus_score = None
            asset.face_found = False

    write_lightroom_results(assets, workspace / "lightroom_results.json")
    sheets = generate_contact_sheet_sets(
        assets,
        contact_dir,
        photos_per_page=photos_per_page,
        columns=columns,
        crop_settings=crop_settings,
    )
    return ScanResult(
        assets=assets,
        preview_dir=preview_dir,
        contact_dir=contact_dir,
        workspace_dir=workspace,
        group_store_path=group_store_path,
        groups_loaded_from_store=groups_loaded,
        input_dir=input_path,
        rejected_count=rejected_count,
        screening_results_path=screening_results_path if technical_screening else None,
        main_pages=sheets.main_pages,
        rejected_pages=sheets.rejected_pages,
    )


def apply_auto_rejects(
    assets: list[PhotoAsset],
    screening_results: dict[str, ScreeningResult],
) -> int:
    asset_map = {asset.stem: asset for asset in assets}
    count = 0
    for stem, result in screening_results.items():
        if not result.rejected:
            continue
        asset = asset_map.get(stem)
        if asset is None:
            continue
        asset.auto_rejected = True
        asset.screening_reason = result.reason
        count += 1
    return count


def apply_selection_text(
    assets: list[PhotoAsset],
    selection_text: str,
    export_dir: str | Path | None = None,
    copy_min_rating: int = 4,
    *,
    results_path: str | Path,
) -> tuple[int, int, dict[str, int]]:
    records = parse_selection_text(selection_text)
    stem_to_rating = {record.stem: record.rating for record in records}
    applied = 0
    missing = 0
    asset_map = {asset.stem: asset for asset in assets}
    for stem, rating in stem_to_rating.items():
        asset = asset_map.get(stem)
        if asset is None:
            missing += 1
            continue
        applied += 1

    write_lightroom_results(assets, results_path, stem_to_rating)
    if export_dir:
        copy_selected(assets, stem_to_rating, export_dir, min_rating=copy_min_rating)

    return applied, missing, stem_to_rating


def persist_manual_groups(result: ScanResult) -> Path:
    result.groups_loaded_from_store = True
    key = str(result.input_dir.resolve()) if result.input_dir is not None else None
    return save_groups(result.assets, result.group_store_path, source="manual", collection_key=key)


def reset_auto_groups(result: ScanResult, grouping_preset: str = "standard") -> Path:
    assign_groups(result.assets, grouping_preset)
    result.groups_loaded_from_store = False
    key = str(result.input_dir.resolve()) if result.input_dir is not None else None
    return save_groups(result.assets, result.group_store_path, source="auto", collection_key=key)


def regenerate_contact_sheet_sets(
    result: ScanResult,
    photos_per_page: int = 20,
    columns: int = 4,
    crop_settings: CropSettings = CropSettings(),
) -> ContactSheetSet:
    sheets = generate_contact_sheet_sets(
        result.assets,
        result.contact_dir,
        photos_per_page=photos_per_page,
        columns=columns,
        crop_settings=crop_settings,
    )
    result.main_pages = sheets.main_pages
    result.rejected_pages = sheets.rejected_pages
    return sheets


def regenerate_contact_sheets(
    result: ScanResult,
    photos_per_page: int = 20,
    columns: int = 4,
) -> list[Path]:
    """Backward-compatible wrapper returning the main culling pages."""
    return regenerate_contact_sheet_sets(
        result,
        photos_per_page=photos_per_page,
        columns=columns,
    ).main_pages
