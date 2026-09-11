import json
import os
from pathlib import Path
import subprocess

import pytest

from ai_cull_assistant.output_cleanup import clear_generated_outputs


def _write(path: Path, value: bytes = b"generated") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return path


def _redirect_directory(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as exc:
        if os.name != "nt":
            pytest.skip(f"directory symlinks unavailable: {exc}")
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if created.returncode:
        pytest.skip(f"directory junctions unavailable: {created.stderr or created.stdout}")


def test_cleanup_removes_generated_sheets_and_log_but_preserves_user_data(tmp_path):
    workspace = tmp_path / "workspace"
    task = "a" * 32
    generated = [
        _write(workspace / "contact_sheets" / "sheet_001_G001-G002_A-B.jpg"),
        _write(workspace / "contact_sheets" / "main" / "sheet_001_G001-G002_A-B.jpg"),
        _write(workspace / "contact_sheets" / "rejected_review" / "sheet_001_G003-G003_C-C.jpg"),
        _write(workspace / "ai_tasks" / task / "B001" / "sheet_001_G001-G001_PA-PA.jpg"),
        _write(workspace / "ai_tasks" / task / "web" / "W001" / "images" / "B001_sheet_001.jpg"),
    ]
    preserved = [
        _write(workspace / "contact_sheets" / "main" / "my-reference.jpg", b"user"),
        _write(workspace / "previews" / "sheet_001_G001-G001_A-A.jpg", b"preview"),
        _write(workspace / "originals" / "source.jpg", b"source"),
        _write(workspace / "exports" / "lightroom-results.json", b"export"),
        _write(workspace / "ai_tasks" / task / "B001" / "prompt.txt", b"prompt"),
        _write(workspace / "ai_tasks" / task / "B001" / "reply.json", b"reply"),
    ]
    _write(workspace / "session.log", b"old log")
    session = {"version": 1, "main_pages": [str(generated[1])], "rejected_pages": [str(generated[2])], "assets": []}
    (workspace / "scan-session.json").write_text(json.dumps(session), encoding="utf-8")

    result = clear_generated_outputs(workspace)

    assert result.removed_contact_sheets == len(generated)
    assert not result.errors
    assert all(not path.exists() for path in generated)
    assert all(path.read_bytes() for path in preserved)
    assert not (workspace / "session.log").exists()
    saved = json.loads((workspace / "scan-session.json").read_text("utf-8"))
    assert saved["main_pages"] == []
    assert saved["rejected_pages"] == []


@pytest.mark.parametrize("target_inside_workspace", [False, True])
def test_cleanup_does_not_follow_redirected_generated_directory(tmp_path, target_inside_workspace):
    workspace = tmp_path / "workspace"
    target_root = workspace / "originals" if target_inside_workspace else tmp_path / "outside"
    outside = _write(target_root / "sheet_001_G001-G001_A-A.jpg", b"outside")
    link = workspace / "contact_sheets" / "main"
    link.parent.mkdir(parents=True)
    _redirect_directory(link, outside.parent)
    try:
        result = clear_generated_outputs(workspace)

        assert not result.errors
        assert outside.read_bytes() == b"outside"
    finally:
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            link.rmdir()


def test_cleanup_preserves_source_photo_even_when_its_path_looks_generated(tmp_path):
    workspace = tmp_path / "workspace"
    source = _write(workspace / "contact_sheets" / "main" / "sheet_1000_G001-G001_A-A.jpg", b"source")
    generated = _write(workspace / "contact_sheets" / "main" / "sheet_1001_G001-G001_B-B.jpg")
    session = {
        "version": 1,
        "source_stats": {str(source): [source.stat().st_size, source.stat().st_mtime_ns]},
        "assets": [{"primary_path": str(source), "rating_target_paths": [str(source)]}],
        "main_pages": [str(generated)],
        "rejected_pages": [],
    }
    (workspace / "scan-session.json").write_text(json.dumps(session), encoding="utf-8")

    result = clear_generated_outputs(workspace)

    assert not result.errors
    assert source.read_bytes() == b"source"
    assert not generated.exists()
