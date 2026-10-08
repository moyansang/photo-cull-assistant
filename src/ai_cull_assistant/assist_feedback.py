"""Durable review progress and explicitly confirmed face-box references.

This module deliberately stores decisions, not learned appearance data.  A
reference cluster is reused only when its seed, or one of its retained
confirmed samples, has the exact same id, box, and source signature.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .ai_project import atomic_json


VERSION = 1
MAX_SAMPLES = 32
STATUSES = frozenset({"pending", "confirmed", "rejected", "skipped", "edited"})


def _normalized_json(value: Any, *, label: str) -> Any:
    """Return a stable JSON-native copy, rejecting lossy/non-finite values."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} must not contain non-finite numbers")
        return value
    if isinstance(value, (list, tuple)):
        return [_normalized_json(item, label=label) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError(f"{label} object keys must be strings")
        return {
            key: _normalized_json(value[key], label=label)
            for key in sorted(value)
        }
    raise TypeError(f"{label} must contain only JSON-serializable values")


def _box(value: Any, *, label: str = "box") -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{label} must contain four finite numbers")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{label} must contain four finite numbers")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError(f"{label} must contain four finite numbers")
        result.append(number)
    return result


def _optional_box(value: Any, *, label: str = "box") -> list[float] | None:
    return None if value is None else _box(value, label=label)


def _identifier(value: Any, *, label: str = "id") -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _identity(identifier: str, box: list[float] | None, signature: Any) -> dict[str, Any]:
    return {"id": identifier, "signature": signature, "box": box}


def _same_identity(value: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(value.get(key) == expected[key] for key in ("id", "signature", "box"))


def _source_key(seed: dict[str, Any]) -> str:
    encoded = json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


class AssistFeedback:
    """Persist one assistance review session and its explicit references."""

    def __init__(self, workspace, reference_id, reference_box, signature):
        self.workspace = Path(workspace)
        self.path = self.workspace / "assist-feedback.json"
        self.reference = _identity(
            _identifier(reference_id, label="reference_id"),
            _box(reference_box, label="reference_box"),
            _normalized_json(signature, label="signature"),
        )
        self.data = self._load()
        self._cluster = self._find_cluster()
        if self._cluster is None:
            self._cluster = {
                "source_key": _source_key(self.reference),
                "seed": copy.deepcopy(self.reference),
                "samples": [],
                "tombstones": [],
                "negative_exclusions": [],
                "session": {"position": None, "rows": {}},
            }
            self.data["clusters"].append(self._cluster)
        self.source_key = self._cluster["source_key"]

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": VERSION, "clusters": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            self._validate_document(value)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid assist feedback file: {self.path}") from error
        return value

    @classmethod
    def _validate_document(cls, value: Any) -> None:
        if not isinstance(value, dict) or value.get("version") != VERSION:
            raise ValueError("unsupported assist feedback document")
        clusters = value.get("clusters")
        if not isinstance(clusters, list):
            raise ValueError("clusters must be a list")
        keys: set[str] = set()
        for cluster in clusters:
            if not isinstance(cluster, dict):
                raise ValueError("cluster must be an object")
            source_key = _identifier(cluster.get("source_key"), label="source_key")
            if source_key in keys:
                raise ValueError("duplicate source_key")
            keys.add(source_key)
            cls._validate_identity(cluster.get("seed"), "seed")
            samples = cluster.get("samples")
            tombstones = cluster.get("tombstones")
            negative_exclusions = cluster.get("negative_exclusions")
            if (not isinstance(samples, list) or not isinstance(tombstones, list)
                    or not isinstance(negative_exclusions, list)):
                raise ValueError("reference collections must be lists")
            for sample in samples:
                cls._validate_identity(sample, "sample")
            for sample in tombstones:
                cls._validate_identity(sample, "tombstone")
            for exclusion in negative_exclusions:
                cls._validate_identity(exclusion, "negative_exclusion", optional_box=True)
            session = cluster.get("session")
            if not isinstance(session, dict):
                raise ValueError("session must be an object")
            if session.get("matching_mode", "appearance") not in ("appearance", "identity"):
                raise ValueError("invalid matching mode")
            position = session.get("position")
            if position is not None:
                _identifier(position, label="position")
            rows = session.get("rows")
            if not isinstance(rows, dict):
                raise ValueError("rows must be an object")
            for identifier, row in rows.items():
                _identifier(identifier)
                cls._validate_row(row, identifier, source_key)

    @staticmethod
    def _validate_identity(value: Any, label: str, *, optional_box: bool = False) -> None:
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
        _identifier(value.get("id"), label=f"{label}.id")
        if optional_box:
            _optional_box(value.get("box"), label=f"{label}.box")
        else:
            _box(value.get("box"), label=f"{label}.box")
        _normalized_json(value.get("signature"), label=f"{label}.signature")

    @staticmethod
    def _validate_row(value: Any, identifier: str, source_key: str) -> None:
        if not isinstance(value, dict) or value.get("id") != identifier:
            raise ValueError("row id does not match its key")
        if value.get("source_key") != source_key:
            raise ValueError("row source_key does not match its cluster")
        if value.get("status") not in STATUSES or not isinstance(value.get("accepted"), bool):
            raise ValueError("invalid row decision")
        box = _optional_box(value.get("box"), label="row.box")
        original = _optional_box(value.get("original_box"), label="row.original_box")
        delta = _optional_box(value.get("correction_delta"), label="row.correction_delta")
        if box is None:
            if original is not None or delta is not None:
                raise ValueError("row without a box cannot have correction metadata")
            if value["status"] == "confirmed" and value["accepted"]:
                raise ValueError("accepted confirmation requires a box")
            _normalized_json(value.get("signature"), label="row.signature")
            return
        if original is None or delta is None:
            raise ValueError("row with a box requires correction metadata")
        expected = [box[index] - original[index] for index in range(4)]
        if any(abs(delta[index] - expected[index]) > 1e-12 for index in range(4)):
            raise ValueError("row correction_delta is inconsistent")
        _normalized_json(value.get("signature"), label="row.signature")

    def _find_cluster(self):
        for cluster in self.data["clusters"]:
            if _same_identity(cluster["seed"], self.reference):
                return cluster
            if any(_same_identity(sample, self.reference) for sample in cluster["samples"]):
                return cluster
        return None

    def _save(self) -> None:
        atomic_json(self.path, self.data)

    def save_row(self, id, signature, box, status, accepted, original_box=None):
        identifier = _identifier(id)
        if status not in STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(STATUSES))}")
        if not isinstance(accepted, bool):
            raise TypeError("accepted must be a boolean")
        normalized_signature = _normalized_json(signature, label="signature")
        normalized_box = _optional_box(box)
        if status == "confirmed" and accepted and normalized_box is None:
            raise ValueError("accepted confirmation requires a box")
        previous = self._cluster["session"]["rows"].get(identifier)
        if normalized_box is None:
            if original_box is not None:
                raise ValueError("original_box requires a box")
            normalized_original = None
            correction_delta = None
        elif (original_box is None and previous is not None
                and previous["signature"] == normalized_signature
                and previous["original_box"] is not None):
            normalized_original = list(previous["original_box"])
        elif original_box is None:
            normalized_original = list(normalized_box)
        else:
            normalized_original = _box(original_box, label="original_box")
        if normalized_box is not None:
            correction_delta = [
                normalized_box[index] - normalized_original[index] for index in range(4)
            ]
        row = {
            "id": identifier,
            "signature": normalized_signature,
            "box": normalized_box,
            "original_box": normalized_original,
            "correction_delta": correction_delta,
            "status": status,
            "accepted": accepted,
            "source_key": self.source_key,
        }
        self._cluster["session"]["rows"][identifier] = row

        # A row contributes a reusable reference only after an explicit accept.
        samples = self._cluster["samples"]
        removed = [sample for sample in samples if sample["id"] == identifier]
        samples[:] = [sample for sample in samples if sample["id"] != identifier]
        if status == "confirmed" and accepted:
            sample = _identity(identifier, list(normalized_box), copy.deepcopy(normalized_signature))
            samples.append(sample)
            if len(samples) > MAX_SAMPLES:
                self._cluster["tombstones"].extend(samples[:-MAX_SAMPLES])
                del samples[:-MAX_SAMPLES]
            self._cluster["tombstones"] = [
                value for value in self._cluster["tombstones"] if value["id"] != identifier
            ]
        elif removed:
            self._cluster["tombstones"].extend(removed)
        if status == "rejected":
            self._cluster["negative_exclusions"] = [
                value for value in self._cluster["negative_exclusions"]
                if value["id"] != identifier
            ]
        self._save()
        return copy.deepcopy(row)

    def rows(self):
        return copy.deepcopy(self._cluster["session"]["rows"])

    def samples(self):
        return [
            {"id": sample["id"], "signature": copy.deepcopy(sample["signature"]),
             "box": list(sample["box"])}
            for sample in self._cluster["samples"]
        ]

    def rejected(self):
        exclusions = self._cluster["negative_exclusions"]
        result = []
        for row in self._cluster["session"]["rows"].values():
            if row["status"] != "rejected" or row["box"] is None:
                continue
            identity = _identity(row["id"], row["box"], row["signature"])
            if any(_same_identity(value, identity) for value in exclusions):
                continue
            result.append(copy.deepcopy(identity))
        return result

    def remove_sample(self, id):
        identifier = _identifier(id)
        samples = self._cluster["samples"]
        removed = [sample for sample in samples if sample["id"] == identifier]
        if not removed:
            return False
        samples[:] = [sample for sample in samples if sample["id"] != identifier]
        self._cluster["tombstones"].extend(removed)
        self._save()
        return True

    def clear_samples(self):
        """Remove retained positive references while keeping review decisions."""

        samples = self._cluster["samples"]
        count = len(samples)
        if count:
            self._cluster["tombstones"].extend(samples)
            samples.clear()
            self._save()
        return count

    def clear_references(self):
        """Clear positive and negative matching effects without losing decisions."""

        positive_count = len(self._cluster["samples"])
        self._cluster["samples"].clear()
        self._cluster["tombstones"].clear()
        exclusions = []
        for row in self._cluster["session"]["rows"].values():
            if row["status"] == "rejected" and row["box"] is not None:
                exclusions.append(_identity(row["id"], row["box"], row["signature"]))
        self._cluster["negative_exclusions"] = exclusions
        self._save()
        return {"samples": positive_count, "rejected": len(exclusions)}

    def matching_mode(self):
        return self._cluster['session'].get('matching_mode', 'appearance')

    def set_matching_mode(self, mode):
        if mode not in ('appearance', 'identity'):
            raise ValueError('invalid matching mode')
        self._cluster['session']['matching_mode'] = mode
        self._save()

    def get_position(self):
        return self._cluster["session"]["position"]

    def set_position(self, target_id):
        if target_id is not None:
            target_id = _identifier(target_id, label="target_id")
        self._cluster["session"]["position"] = target_id
        self._save()
