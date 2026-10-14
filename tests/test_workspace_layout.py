from pathlib import Path

import pytest

from ai_cull_assistant.workspace_layout import workspace_path


@pytest.mark.parametrize(
    ("name", "relative"),
    [
        (".analysis-cache", Path("cache/analysis")),
        (".processing", Path("cache/processing")),
        ("lightroom_results.json", Path("exports/lightroom_results.json")),
        ("session.log", Path("logs/session.log")),
    ],
)
def test_new_workspace_uses_structured_internal_layout(tmp_path, name, relative):
    workspace = tmp_path / "workspace"

    assert workspace_path(workspace, name) == workspace / relative
    assert not workspace.exists()


@pytest.mark.parametrize(
    ("name", "directory"),
    [
        (".analysis-cache", True),
        (".processing", True),
        ("lightroom_results.json", False),
        ("session.log", False),
    ],
)
def test_existing_legacy_location_is_preserved(tmp_path, name, directory):
    workspace = tmp_path / "workspace"
    legacy = workspace / name
    if directory:
        legacy.mkdir(parents=True)
    else:
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.touch()

    assert workspace_path(workspace, name) == legacy


def test_legacy_location_wins_when_both_layouts_exist(tmp_path):
    workspace = tmp_path / "workspace"
    legacy = workspace / ".analysis-cache"
    legacy.mkdir(parents=True)
    (workspace / "cache" / "analysis").mkdir(parents=True)

    assert workspace_path(workspace, ".analysis-cache") == legacy


def test_unknown_workspace_name_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown workspace path"):
        workspace_path(tmp_path, "groups.json")
