"""Incremental, crash-safe checkpoints for processing jobs.

The main ``job.json`` file remains the base snapshot.  Journal records contain
the completed work for one photo and are replayed in sequence after a crash.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
from pathlib import Path
import re

from .ai_project import atomic_json


_JOURNAL_NAME = re.compile(r"^(\d{9})\.json$")
_MAX_SEQUENCE = 999_999_999
_PATCH_KEYS = {
    "index",
    "asset",
    "screening_key",
    "screening_result",
    "photo_options",
    "state",
}
_PROTECTED_STATE_KEYS = {"assets", "screening_results", "photo_options", "journal_seq"}


class ProcessingJournalError(RuntimeError):
    """A journal cannot be safely appended or replayed."""


def _sequence(value: object, *, allow_zero: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProcessingJournalError("journal sequence must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum or value > _MAX_SEQUENCE:
        raise ProcessingJournalError("journal sequence is out of range")
    return value


def _normalise_patch(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise ProcessingJournalError("journal patch must be an object")
    patch = dict(value)
    unknown = set(patch) - _PATCH_KEYS
    if unknown:
        raise ProcessingJournalError(f"journal patch has unknown fields: {sorted(unknown)!r}")
    if "index" not in patch or "asset" not in patch:
        raise ProcessingJournalError("journal patch requires index and asset")

    index = patch["index"]
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ProcessingJournalError("journal patch index must be a non-negative integer")
    if not isinstance(patch["asset"], Mapping):
        raise ProcessingJournalError("journal patch asset must be an object")

    has_screening_key = "screening_key" in patch
    has_screening_result = "screening_result" in patch
    if has_screening_key != has_screening_result:
        raise ProcessingJournalError(
            "journal patch screening_key and screening_result must be provided together"
        )
    if has_screening_key:
        if not isinstance(patch["screening_key"], str) or not patch["screening_key"]:
            raise ProcessingJournalError("journal patch screening_key must be a non-empty string")
        if not isinstance(patch["screening_result"], Mapping):
            raise ProcessingJournalError("journal patch screening_result must be an object")

    if "state" in patch:
        state = patch["state"]
        if not isinstance(state, Mapping) or not all(isinstance(key, str) for key in state):
            raise ProcessingJournalError("journal patch state must be an object with string keys")
        protected = set(state) & _PROTECTED_STATE_KEYS
        if protected:
            raise ProcessingJournalError(
                f"journal patch state contains protected fields: {sorted(protected)!r}"
            )

    # Fail before touching the journal directory when the value is not JSON-safe.
    try:
        return json.loads(json.dumps(patch, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ProcessingJournalError("journal patch is not JSON serializable") from exc


def append_patch(root: str | Path, seq: int, patch: Mapping) -> Path:
    """Atomically append one immutable per-photo patch to ``root/journal``.

    Repeating an already successful append with the same sequence and payload is
    idempotent.  Reusing a sequence for different work is treated as corruption.
    """
    sequence = _sequence(seq, allow_zero=False)
    normalised = _normalise_patch(patch)
    record = {"seq": sequence, "patch": normalised}
    target = Path(root) / "journal" / f"{sequence:09d}.json"
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise ProcessingJournalError(f"journal record {sequence} is corrupt") from exc
        if existing == record:
            return target
        raise ProcessingJournalError(f"journal sequence {sequence} already exists")
    atomic_json(target, record)
    return target


def _future_records(root: Path, acknowledged: int) -> list[tuple[int, Path]]:
    journal = root / "journal"
    if not journal.is_dir():
        return []
    records: list[tuple[int, Path]] = []
    try:
        children = list(journal.iterdir())
    except OSError as exc:
        raise ProcessingJournalError("journal directory cannot be read") from exc
    for path in children:
        match = _JOURNAL_NAME.fullmatch(path.name)
        if match is None or not path.is_file():
            # In particular, atomic_json's ``.json.tmp`` is never replayed.
            continue
        sequence = int(match.group(1))
        if sequence > acknowledged:
            records.append((sequence, path))
    records.sort(key=lambda item: item[0])
    expected = acknowledged + 1
    for sequence, _path in records:
        if sequence != expected:
            raise ProcessingJournalError(
                f"journal sequence gap: expected {expected}, found {sequence}"
            )
        expected += 1
    return records


def _load_record(path: Path, expected_sequence: int) -> dict:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ProcessingJournalError(f"journal record {expected_sequence} is corrupt") from exc
    if not isinstance(record, dict) or set(record) != {"seq", "patch"}:
        raise ProcessingJournalError(f"journal record {expected_sequence} has invalid fields")
    if record["seq"] != expected_sequence:
        raise ProcessingJournalError(f"journal record {expected_sequence} has the wrong sequence")
    return _normalise_patch(record["patch"])


def _apply_patch(data: dict, patch: dict, sequence: int) -> None:
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise ProcessingJournalError("job snapshot assets must be a list")
    index = patch["index"]
    if index >= len(assets):
        raise ProcessingJournalError(
            f"journal record {sequence} asset index {index} is out of bounds"
        )
    assets[index] = patch["asset"]

    if "screening_key" in patch:
        screening_results = data.setdefault("screening_results", {})
        if not isinstance(screening_results, dict):
            raise ProcessingJournalError("job snapshot screening_results must be an object")
        screening_results[patch["screening_key"]] = patch["screening_result"]

    if "photo_options" in patch:
        photo_options = data.setdefault("photo_options", {})
        if not isinstance(photo_options, dict):
            raise ProcessingJournalError("job snapshot photo_options must be an object")
        photo_options[str(index)] = patch["photo_options"]

    if "state" in patch:
        data.update(patch["state"])
    data["journal_seq"] = sequence


def replay_patches(root: str | Path, data: dict) -> dict:
    """Replay every contiguous journal record newer than the base snapshot.

    Validation and application happen on a copy.  On any gap, corrupt record, or
    invalid asset index, ``data`` is left untouched and an error is raised.
    """
    if not isinstance(data, dict):
        raise ProcessingJournalError("job snapshot must be an object")
    acknowledged = _sequence(data.get("journal_seq", 0), allow_zero=True)
    records = _future_records(Path(root), acknowledged)
    if not records:
        return data

    updated = deepcopy(data)
    for sequence, path in records:
        patch = _load_record(path, sequence)
        _apply_patch(updated, patch, sequence)
    data.clear()
    data.update(updated)
    return data
