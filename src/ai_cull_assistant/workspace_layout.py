"""Resolve internal workspace paths while preserving legacy workspaces."""
from __future__ import annotations

from pathlib import Path


_LAYOUT = {
    ".analysis-cache": Path("cache") / "analysis",
    ".processing": Path("cache") / "processing",
    "lightroom_results.json": Path("exports") / "lightroom_results.json",
    "session.log": Path("logs") / "session.log",
}


def workspace_path(workspace: str | Path, name: str) -> Path:
    """Return the compatible location for one internal workspace artifact.

    Existing workspaces keep using a legacy root-level artifact, even when the
    legacy artifact is an empty directory. New workspaces use the structured
    layout. This function only selects a path; it never creates or moves it.
    """
    try:
        current = _LAYOUT[name]
    except KeyError as exc:
        raise ValueError(f"unknown workspace path: {name}") from exc
    root = Path(workspace)
    legacy = root / name
    return legacy if legacy.exists() else root / current
