from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


RAW_EXTENSIONS = {".rw2", ".arw", ".cr2", ".cr3", ".nef", ".orf", ".raf", ".dng"}
JPEG_EXTENSIONS = {".jpg", ".jpeg"}
IMAGE_EXTENSIONS = RAW_EXTENSIONS | JPEG_EXTENSIONS | {".png", ".webp", ".tif", ".tiff"}


@dataclass(slots=True)
class PhotoAsset:
    stem: str
    display_path: Path
    primary_path: Path
    raw_path: Optional[Path]
    jpg_path: Optional[Path]
    captured_at: datetime
    ext: str
    group_id: int = 0
    preview_path: Optional[Path] = None
    dhash: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    auto_rejected: bool = False
    screening_reason: Optional[str] = None
    focus_score: Optional[float] = None
    face_found: bool = False
    subject_features: object = None
    subject_checked: bool = False
    subject_confidence: float = .8
    ai_focus_result: dict | None = None
    ai_focus_dirty: bool = False

    @property
    def rating_target_paths(self) -> list[Path]:
        paths: list[Path] = []
        if self.raw_path:
            paths.append(self.raw_path)
        if self.jpg_path and self.jpg_path not in paths:
            paths.append(self.jpg_path)
        if not paths:
            paths.append(self.primary_path)
        return paths

    @property
    def xmp_base_path(self) -> Path:
        if self.raw_path:
            return self.raw_path
        return self.primary_path


@dataclass(slots=True)
class SelectionRecord:
    stem: str
    rating: int
    source_line: str
