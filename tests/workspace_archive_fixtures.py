from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import zipfile


_LEGACY_STATE_FILES = (
    "scan-session.json",
    "ai_project.json",
    "groups.json",
    "screening_results.json",
    "processing-settings.json",
    "workspace-settings.json",
    "workflow-stage.json",
    "session.log",
    "logs/session.log",
)


def create_legacy_workspace_archive(
    workspace: str | Path,
    *,
    remove_paths: tuple[str, ...] = ("previews",),
) -> Path:
    """Create the v1 archive shape emitted by retired application versions."""
    root = Path(workspace)
    payloads = {
        name: (root / name).read_bytes()
        for name in _LEGACY_STATE_FILES
        if (root / name).is_file()
    }
    if not payloads:
        raise ValueError("legacy archive fixture needs at least one state file")
    manifest = {
        "version": 1,
        "files": {
            name: {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            for name, payload in payloads.items()
        },
    }
    archive = root / ".workspace-archive.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, payload in payloads.items():
            bundle.writestr(name, payload)
        bundle.writestr(
            ".archive-manifest.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        )
    for name in payloads:
        (root / name).unlink()
    for relative in remove_paths:
        path = root / relative
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    (root / ".workspace-storage.json").write_text(
        json.dumps(
            {
                "version": 1,
                "status": "compacted",
                "archive": ".workspace-archive.zip",
                "preview_removed": "previews" in remove_paths,
                "archived_files": len(payloads),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return archive
