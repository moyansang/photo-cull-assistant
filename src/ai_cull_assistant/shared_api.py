"""Shared selection of the API profile used by scanning and review UI."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .ai_api import load_profiles
from .settings import read_values, save_values


NO_API_PROFILE_ID = "manual-web"
NO_API_LABEL = "不使用 API（网页手动）"
SELECTED_API_PROFILE_KEY = "selected_api_profile_id"


def selected_profile_id(settings_dir: str | Path) -> str:
    value = read_values(Path(settings_dir)).get(SELECTED_API_PROFILE_KEY)
    return value if isinstance(value, str) and value else NO_API_PROFILE_ID


def save_selected_profile_id(settings_dir: str | Path, profile_id: str | None) -> None:
    """Persist only a profile identifier; credentials remain in Credential Manager."""
    value = profile_id if isinstance(profile_id, str) and profile_id else NO_API_PROFILE_ID
    save_values(Path(settings_dir), {SELECTED_API_PROFILE_KEY: value})


def selected_api_profile(settings_dir: str | Path, profile_id: str | None = None) -> dict | None:
    """Return an isolated profile snapshot, or ``None`` for manual web use."""
    chosen = profile_id if isinstance(profile_id, str) and profile_id else selected_profile_id(settings_dir)
    if chosen == NO_API_PROFILE_ID:
        return None
    for profile in load_profiles(Path(settings_dir)):
        if profile.get("id") == chosen:
            return deepcopy(profile)
    return None


def profile_options(settings_dir: str | Path) -> list[tuple[str, str]]:
    """Return ``(label, id)`` pairs suitable for the homepage combobox."""
    result = [(NO_API_LABEL, NO_API_PROFILE_ID)]
    used = {NO_API_LABEL}
    for profile in load_profiles(Path(settings_dir)):
        label = str(profile["name"])
        if label in used:
            label = f"{label}（{profile['model']}）"
        if label in used:
            label = f"{label} [{str(profile['id'])[:8]}]"
        used.add(label)
        result.append((label, str(profile["id"])))
    return result
