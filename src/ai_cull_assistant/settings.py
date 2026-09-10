"""Directory preferences stored beside the portable executable."""
import json
from pathlib import Path
import sys


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def load_paths(base: Path) -> dict[str, str]:
    defaults = {"input": "", "workspace": str(base / "工作区"), "export": str(base / "精选")}
    try:
        saved = json.loads((base / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    if isinstance(saved, dict):
        defaults.update({key: saved[key] for key in defaults if isinstance(saved.get(key), str)})
    return defaults


def save_paths(base: Path, paths: dict[str, str]) -> None:
    save_values(base, paths)


def read_values(base: Path) -> dict:
    try:
        value = json.loads((base / "settings.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_values(base: Path, values: dict) -> None:
    saved = read_values(base)
    saved.update(values)
    temporary = base / "settings.json.tmp"
    temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(base / "settings.json")
