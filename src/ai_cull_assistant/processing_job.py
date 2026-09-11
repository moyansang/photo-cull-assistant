"""Crash-safe, resumable scanning and contact-sheet generation jobs.

Generated files stay below ``workspace/.processing`` until the entire job is
complete.  The public workspace is replaced only during the final publish.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import shutil
from threading import Event
from typing import Callable, Mapping
import uuid

from PIL import Image, ImageDraw

from . import contact_sheet as sheet
from .ai_project import atomic_json
from .crop_settings import CropSettings
from .group_store import load_groups, save_groups
from .grouping import assign_groups
from .models import PhotoAsset
from .preview import build_preview
from .scanner import iter_image_files, scan_folder
from .screening import ScreeningResult, save_screening_results, screen_assets
from .session_store import save_session
from .subject import SubjectFeatures
from .workflow import ScanResult


JOB_VERSION = 1
ProgressCallback = Callable[[int], None]


class SourceChangedError(ValueError):
    """The interrupted job no longer describes the source photo folder."""


class ProcessingJobError(RuntimeError):
    pass


def _normalise_options(options: Mapping | None) -> dict:
    source = dict(options or {})
    return {
        "grouping_preset": str(source.get("grouping_preset", "standard")),
        "photos_per_page": max(1, int(source.get("photos_per_page", 20))),
        "columns": max(1, int(source.get("columns", 4))),
        "technical_screening": bool(source.get("technical_screening", True)),
    }


def _crop_dict(crops: CropSettings | Mapping | None) -> dict:
    if isinstance(crops, CropSettings):
        return asdict(crops)
    return dict(crops or {})


def _source_inventory(input_dir: Path) -> dict[str, list[int]]:
    inventory: dict[str, list[int]] = {}
    for path in iter_image_files(input_dir):
        stat = path.stat()
        inventory[str(path.resolve())] = [stat.st_size, stat.st_mtime_ns]
    return inventory


def _asset_to_dict(asset: PhotoAsset) -> dict:
    value = asdict(asset)
    for key in ("display_path", "primary_path", "raw_path", "jpg_path", "preview_path"):
        value[key] = str(value[key].resolve()) if value[key] else None
    value["captured_at"] = asset.captured_at.isoformat()
    return value


def _asset_from_dict(value: dict) -> PhotoAsset:
    row = dict(value)
    for key in ("display_path", "primary_path", "raw_path", "jpg_path", "preview_path"):
        row[key] = Path(row[key]) if row.get(key) else None
    row["captured_at"] = datetime.fromisoformat(row["captured_at"])
    feature = row.get("subject_features")
    if isinstance(feature, dict):
        for key in ("face", "head"):
            if feature.get(key):
                feature[key] = tuple(feature[key])
        row["subject_features"] = SubjectFeatures(**feature)
    return PhotoAsset(**row)


def _asset_key(asset: PhotoAsset) -> str:
    return str(asset.primary_path.resolve()).casefold()


def _active_path(workspace: Path) -> Path:
    return workspace / ".processing" / "active.json"


def start_job(
    input_dir: str | Path,
    workspace: str | Path,
    options: Mapping,
    crops: CropSettings | Mapping | None,
    result: ScanResult | None = None,
    regroup: bool = False,
) -> "ProcessingJob":
    """Create a persisted scan or regeneration job without touching live output."""
    input_path = Path(input_dir).resolve()
    workspace_path = Path(workspace).resolve()
    if not input_path.is_dir():
        raise ValueError("照片文件夹不存在")
    processing_root = workspace_path / ".processing"
    active_path = _active_path(workspace_path)
    if active_path.exists():
        try:
            active = json.loads(active_path.read_text("utf-8"))
            active_root = processing_root / str(active.get("job_id", ""))
        except (OSError, ValueError, TypeError):
            active_root = Path()
        if active_root.is_dir():
            raise ProcessingJobError("已有未完成任务，请先继续处理")
        active_path.unlink(missing_ok=True)

    assets = list(result.assets) if result is not None else scan_folder(input_path)
    if not assets:
        raise ValueError("照片文件夹中没有支持的照片")
    identifier = uuid.uuid4().hex
    root = processing_root / identifier
    (root / "output").mkdir(parents=True, exist_ok=False)
    normalised = _normalise_options(options)
    kind = "regenerate" if result is not None else "scan"
    completed_photos = len(assets) if result is not None else 0
    data = {
        "version": JOB_VERSION,
        "id": identifier,
        "kind": kind,
        "input_dir": str(input_path),
        "workspace": str(workspace_path),
        "created_options": normalised,
        "last_options": normalised,
        "created_crops": _crop_dict(crops),
        "regroup": bool(regroup),
        "source_stats": _source_inventory(input_path),
        "assets": [_asset_to_dict(asset) for asset in assets],
        "completed_photos": completed_photos,
        "photo_options": {},
        "screening_results": {},
        "grouping_complete": result is not None and not regroup,
        "actual_grouping_preset": None,
        "group_source": "preserved" if result is not None and not regroup else None,
        "groups_loaded_from_store": result is not None and not regroup,
        "pages": [],
        "percent": 0,
    }
    job = ProcessingJob(root, data)
    job._persist()
    atomic_json(active_path, {"version": JOB_VERSION, "job_id": identifier, "input_dir": str(input_path)})
    return job


def load_job(workspace: str | Path, input_dir: str | Path) -> "ProcessingJob | None":
    """Load the active job for this source, invalidating only it if photos changed."""
    workspace_path = Path(workspace).resolve()
    input_path = Path(input_dir).resolve()
    active_path = _active_path(workspace_path)
    if not active_path.is_file():
        return None
    try:
        active = json.loads(active_path.read_text("utf-8"))
        if Path(active.get("input_dir", "")).resolve() != input_path:
            return None
        identifier = str(active["job_id"])
        if uuid.UUID(identifier).hex != identifier:
            raise ValueError("invalid job id")
        root = workspace_path / ".processing" / identifier
        if root.resolve().parent != (workspace_path / ".processing").resolve():
            raise ValueError("invalid job path")
        data = json.loads((root / "job.json").read_text("utf-8"))
        if Path(data.get("workspace", "")).resolve() != workspace_path:
            raise ValueError("workspace mismatch")
        if data.get("id") != identifier or Path(data.get("input_dir", "")).resolve() != input_path:
            raise ValueError("job identity mismatch")
        job = ProcessingJob(root, data)
        job._check_artifacts()
        job._validate_sources()
        return job
    except SourceChangedError:
        raise
    except Exception as exc:
        safe_parent = (workspace_path / ".processing").resolve()
        if "root" in locals() and root.is_dir() and root.resolve().parent == safe_parent:
            shutil.rmtree(root, ignore_errors=True)
        active_path.unlink(missing_ok=True)
        raise ProcessingJobError("中断任务记录损坏，已清除本次任务") from exc


class ProcessingJob:
    def __init__(self, root: Path, data: dict):
        if data.get("version") != JOB_VERSION:
            raise ProcessingJobError("中断任务版本不支持")
        self.root = Path(root)
        self._data = data
        self.assets = [_asset_from_dict(row) for row in data.get("assets", [])]

    @property
    def kind(self) -> str:
        return str(self._data["kind"])

    @property
    def percent(self) -> int:
        return int(self._data.get("percent", 0))

    @property
    def actual_grouping_preset(self) -> str | None:
        value = self._data.get("actual_grouping_preset")
        return str(value) if value else None

    @property
    def grouping_preset(self) -> str | None:
        """Alias used by the UI to record the preset that actually ran."""
        return self.actual_grouping_preset

    @property
    def workspace(self) -> Path:
        return Path(self._data["workspace"])

    @property
    def input_dir(self) -> Path:
        return Path(self._data["input_dir"])

    @property
    def output(self) -> Path:
        return self.root / "output"

    def run(
        self,
        options: Mapping,
        crops: CropSettings | Mapping | None,
        stop_event: Event,
        progress: ProgressCallback | None,
    ) -> ScanResult | None:
        """Continue work.  A requested stop takes effect after the current unit."""
        current = _normalise_options(options)
        crop_settings = crops if isinstance(crops, CropSettings) else CropSettings.from_dict(crops or {})
        self._validate_sources()
        self._data["last_options"] = current
        self._persist()
        self._notify(progress)
        if stop_event.is_set():
            return None

        while self._data["completed_photos"] < len(self.assets):
            index = int(self._data["completed_photos"])
            asset = self.assets[index]
            build_preview(asset, self.output / "previews")
            if current["technical_screening"]:
                screened = screen_assets([asset])[asset.stem]
            else:
                asset.auto_rejected = False
                asset.screening_reason = "screening_disabled"
                asset.focus_score = None
                asset.face_found = False
                screened = ScreeningResult(False, "screening_disabled", False)
            self._data["screening_results"][_asset_key(asset)] = asdict(screened)
            self._data["photo_options"][str(index)] = {
                "technical_screening": current["technical_screening"]
            }
            self._data["completed_photos"] = index + 1
            self._checkpoint(progress)
            if stop_event.is_set():
                return None

        if not self._data["grouping_complete"]:
            loaded = False
            if not self._data.get("regroup") and self.kind == "scan":
                loaded = load_groups(
                    self.assets,
                    self.workspace / "groups.json",
                    collection_key=str(self.input_dir),
                )
            if not loaded:
                assign_groups(self.assets, current["grouping_preset"])
            self._data["group_source"] = self._stored_group_source() if loaded else "auto"
            self._data["groups_loaded_from_store"] = loaded
            self._data["actual_grouping_preset"] = self._prior_grouping_preset(current) if loaded else current["grouping_preset"]
            self._data["grouping_complete"] = True
            self._checkpoint(progress)
            if stop_event.is_set():
                return None
        elif self._data.get("actual_grouping_preset") is None:
            self._data["actual_grouping_preset"] = self._prior_grouping_preset(current)
            self._persist()

        for mode in ("main", "rejected"):
            completed_keys = {
                key for page in self._data["pages"] if page["mode"] == mode for key in page["asset_keys"]
            }
            category = [
                asset for asset in self.assets
                if (asset.auto_rejected == (mode == "rejected")) and _asset_key(asset) not in completed_keys
            ]
            planned = sheet.paginate_for_layout(
                category,
                photos_per_page=current["photos_per_page"],
                columns=current["columns"],
            )
            existing_count = sum(1 for page in self._data["pages"] if page["mode"] == mode)
            total = existing_count + len(planned)
            for chunk in planned:
                index = sum(1 for page in self._data["pages"] if page["mode"] == mode) + 1
                folder = self.output / "contact_sheets" / ("main" if mode == "main" else "rejected_review")
                filename = sheet.page_filename(chunk, index)
                target = folder / filename
                _render_page(
                    self.assets,
                    chunk,
                    target,
                    page_index=index,
                    total_pages=total,
                    columns=current["columns"],
                    review_mode=mode == "rejected",
                    crop_settings=crop_settings,
                )
                self._data["pages"].append({
                    "mode": mode,
                    "path": str(target.relative_to(self.output)),
                    "asset_keys": [_asset_key(asset) for asset in chunk],
                    "options": {
                        "photos_per_page": current["photos_per_page"],
                        "columns": current["columns"],
                    },
                })
                self._checkpoint(progress)
                if stop_event.is_set():
                    return None

        self._validate_sources()
        return self._publish(progress)

    def _prior_grouping_preset(self, current: dict) -> str:
        path = self.workspace / "processing-settings.json"
        try:
            value = json.loads(path.read_text("utf-8")).get("grouping_preset")
            return str(value) if value else current["grouping_preset"]
        except (OSError, ValueError, TypeError):
            return current["grouping_preset"]

    def _stored_group_source(self) -> str:
        try:
            source = json.loads((self.workspace / "groups.json").read_text("utf-8")).get("source")
        except (OSError, ValueError, TypeError):
            return "stored"
        return str(source) if source in ("manual", "auto") else "stored"

    def _progress_value(self) -> int:
        count = max(1, len(self.assets))
        photo_part = 70 * int(self._data["completed_photos"]) // count
        group_part = 5 if self._data["grouping_complete"] else 0
        estimate = max(1, (len(self.assets) + max(1, int(self._data["created_options"]["photos_per_page"])) - 1)
                       // max(1, int(self._data["created_options"]["photos_per_page"])))
        page_part = min(24, 24 * len(self._data["pages"]) // estimate)
        return min(99, photo_part + group_part + page_part)

    def _checkpoint(self, progress: ProgressCallback | None) -> None:
        self._data["assets"] = [_asset_to_dict(asset) for asset in self.assets]
        self._data["percent"] = max(self.percent, self._progress_value())
        self._persist()
        self._notify(progress)

    def _notify(self, progress: ProgressCallback | None) -> None:
        if progress is None:
            return
        try:
            progress(self.percent)
        except Exception:
            # Progress display must never corrupt or abort background work.
            pass

    def _persist(self) -> None:
        atomic_json(self.root / "job.json", self._data)

    def _validate_sources(self) -> None:
        try:
            for source, expected in self._data.get("source_stats", {}).items():
                stat = Path(source).stat()
                if [stat.st_size, stat.st_mtime_ns] != expected:
                    raise OSError("source changed")
        except OSError as exc:
            self._invalidate()
            raise SourceChangedError("照片文件夹中的照片出现问题，本次中断任务结果已清除") from exc

    def _check_artifacts(self) -> None:
        for asset in self.assets[: int(self._data.get("completed_photos", 0))]:
            if self.kind == "scan" and (asset.preview_path is None or not Path(asset.preview_path).is_file()):
                raise ProcessingJobError("已完成的预览文件丢失")
        for page in self._data.get("pages", []):
            if not (self.output / page["path"]).is_file():
                raise ProcessingJobError("已完成的联系表文件丢失")

    def _invalidate(self) -> None:
        processing_root = self.workspace / ".processing"
        try:
            if self.root.resolve().parent == processing_root.resolve():
                shutil.rmtree(self.root, ignore_errors=True)
        finally:
            active = _active_path(self.workspace)
            try:
                value = json.loads(active.read_text("utf-8"))
            except (OSError, ValueError, TypeError):
                value = {}
            if value.get("job_id") == self._data.get("id"):
                active.unlink(missing_ok=True)

    def _publish(self, progress: ProgressCallback | None) -> ScanResult:
        output = self.output
        screening = {
            asset.stem: ScreeningResult(**self._data["screening_results"][_asset_key(asset)])
            for asset in self.assets
            if _asset_key(asset) in self._data["screening_results"]
        }
        if self.kind == "scan":
            save_screening_results(screening, output / "screening_results.json")
        save_groups(
            self.assets,
            output / "groups.json",
            source=str(self._data.get("group_source") or "auto"),
            collection_key=str(self.input_dir),
        )

        destinations: list[tuple[Path, Path]] = [(output / "contact_sheets", self.workspace / "contact_sheets")]
        if self.kind == "scan":
            destinations.extend([
                (output / "previews", self.workspace / "previews"),
                (output / "screening_results.json", self.workspace / "screening_results.json"),
            ])
        if self.kind == "scan" or self._data.get("regroup"):
            destinations.append((output / "groups.json", self.workspace / "groups.json"))

        backup = self.root / "backup"
        backup.mkdir(exist_ok=True)
        passive = [self.workspace / "scan-session.json", self.workspace / "processing-settings.json"]
        existed_before = {path: path.exists() for path in [item[1] for item in destinations] + passive}
        moved_old: list[tuple[Path, Path]] = []
        moved_new: list[tuple[Path, Path]] = []
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
            for destination in [item[1] for item in destinations] + passive:
                if destination.exists():
                    saved = backup / f"{len(moved_old):03d}-{destination.name}"
                    destination.replace(saved)
                    moved_old.append((saved, destination))
            for source, destination in destinations:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.exists():
                    source.replace(destination)
                    moved_new.append((destination, source))

            if self.kind == "scan":
                stage_preview = self.root / "output" / "previews"
                for asset in self.assets:
                    if asset.preview_path:
                        relative = Path(asset.preview_path).relative_to(stage_preview)
                        asset.preview_path = self.workspace / "previews" / relative
            main_pages = [self.workspace / page["path"] for page in self._data["pages"] if page["mode"] == "main"]
            rejected_pages = [self.workspace / page["path"] for page in self._data["pages"] if page["mode"] == "rejected"]
            rejected_count = sum(1 for asset in self.assets if asset.auto_rejected)
            result = ScanResult(
                assets=self.assets,
                preview_dir=self.workspace / "previews",
                contact_dir=self.workspace / "contact_sheets",
                workspace_dir=self.workspace,
                group_store_path=self.workspace / "groups.json",
                groups_loaded_from_store=bool(self._data.get("groups_loaded_from_store")),
                input_dir=self.input_dir,
                rejected_count=rejected_count,
                screening_results_path=(self.workspace / "screening_results.json") if (self.workspace / "screening_results.json").exists() else None,
                main_pages=main_pages,
                rejected_pages=rejected_pages,
            )
            save_session(result, fresh=True)
            atomic_json(self.workspace / "processing-settings.json", {
                "version": 1,
                "grouping_preset": self.actual_grouping_preset,
                "last_options": self._data["last_options"],
            })
        except Exception:
            for destination, source in reversed(moved_new):
                if destination.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    destination.replace(source)
            backed_destinations = {destination for _saved, destination in moved_old}
            for destination in passive:
                if destination in backed_destinations or not existed_before[destination]:
                    if destination.is_dir():
                        shutil.rmtree(destination, ignore_errors=True)
                    else:
                        destination.unlink(missing_ok=True)
            for saved, destination in reversed(moved_old):
                if saved.exists():
                    saved.replace(destination)
            if self.kind == "scan":
                formal_preview = self.workspace / "previews"
                staged_preview = self.output / "previews"
                for asset in self.assets:
                    if asset.preview_path:
                        try:
                            relative = Path(asset.preview_path).relative_to(formal_preview)
                        except ValueError:
                            continue
                        asset.preview_path = staged_preview / relative
                self._data["assets"] = [_asset_to_dict(asset) for asset in self.assets]
                self._persist()
            raise

        shutil.rmtree(backup, ignore_errors=True)
        self._data["percent"] = 100
        self._notify(progress)
        active = _active_path(self.workspace)
        active.unlink(missing_ok=True)
        shutil.rmtree(self.root, ignore_errors=True)
        return result


def _render_page(
    all_assets: list[PhotoAsset],
    chunk: list[PhotoAsset],
    target: Path,
    *,
    page_index: int,
    total_pages: int,
    columns: int,
    review_mode: bool,
    crop_settings: CropSettings,
) -> None:
    """Render one checkpointable page with the normal contact-sheet appearance."""
    target.parent.mkdir(parents=True, exist_ok=True)
    groups = sheet._chunk_groups(chunk)
    page_width = sheet.MARGIN * 2 + columns * sheet.CELL_W
    page_height = sheet.MARGIN * 2 + sheet.PAGE_HEADER_H
    for _, group_assets in groups:
        page_height += sheet.GROUP_HEADER_H + ((len(group_assets) + columns - 1) // columns) * sheet.CELL_H
    canvas = Image.new("RGB", (page_width, page_height), sheet.PAGE_BG)
    draw = ImageDraw.Draw(canvas)
    title_font = sheet._load_font(30)
    group_font = sheet._load_font(23)
    filename_font = sheet._load_font(25)
    small_font = sheet._load_font(18)
    title = "Rejected Review" if review_mode else "AI Photo Culling Sheet"
    draw.text((sheet.MARGIN, sheet.MARGIN), f"{title}  {page_index}/{max(1, total_pages)}", fill=sheet.TEXT_COLOR, font=title_font)
    draw.text((sheet.MARGIN, sheet.MARGIN + 42), f"Photos: {len(chunk)}   Groups: {len(groups)}", fill=sheet.TEXT_COLOR, font=small_font)
    y = sheet.MARGIN + sheet.PAGE_HEADER_H
    group_members: dict[int, list[PhotoAsset]] = {}
    for asset in all_assets:
        group_members.setdefault(asset.group_id, []).append(asset)
    for group_id, group_assets in groups:
        fill = sheet.REJECT_BG if review_mode else sheet.GROUP_BG
        draw.rectangle([sheet.MARGIN, y, page_width - sheet.MARGIN, y + sheet.GROUP_HEADER_H - 4], fill=fill)
        draw.text((sheet.MARGIN + 12, y + 9), f"G{group_id:03d}   ·   {len(group_members[group_id])} photos", fill=sheet.TEXT_COLOR, font=group_font)
        y += sheet.GROUP_HEADER_H
        positions = {id(asset): index for index, asset in enumerate(group_members[group_id], 1)}
        for offset, asset in enumerate(group_assets):
            row, column = divmod(offset, columns)
            sheet._draw_cell(
                canvas, draw, asset,
                sheet.MARGIN + column * sheet.CELL_W,
                y + row * sheet.CELL_H,
                positions[id(asset)], filename_font, small_font,
                review_mode=review_mode, crop_settings=crop_settings,
            )
        y += ((len(group_assets) + columns - 1) // columns) * sheet.CELL_H
    canvas.save(target, "JPEG", quality=93, optimize=True)
