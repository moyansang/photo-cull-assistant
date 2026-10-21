#!/usr/bin/env python3
"""Read-only, explicit-list preflight for the existing offline sample contract.

This is an entry-point gate, not a replacement sample format, decoder, scorer,
license grant, or attestation of dataset independence. No directory scanning.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import time
import sys

import offline_evaluation as evaluation

PATH_SCHEMA = "real-sample-path-map-v1"
RAW_FORMATS = {"RW2", "ARW", "CR2", "CR3", "NEF", "ORF", "RAF", "DNG"}
RASTER_FORMATS = {"JPG", "JPEG", "PNG", "WEBP", "TIF", "TIFF", "BMP", "HEIC", "HEIF", "AVIF"}
SUPPORTED_FORMATS = RAW_FORMATS | RASTER_FORMATS
HISTORICAL_DATASETS = {"piq", "certh", "spaq"}


def validate_samples(samples):
    """Reuse v1 uniqueness/leakage validation without creating predictions.

    The ephemeral empty prediction envelope only satisfies the existing shared
    validator signature. Its hash values identify validator inputs, not a run,
    dependency lock, or measurement. It is never returned or persisted.
    """
    catalog = evaluation.make_catalog(evaluation.REPO, "HEAD")
    shared = {key: samples.get(key) for key in ("schema_version", "dataset_id", "dataset_version", "guideline_version", "manifest_version")}
    manifest = evaluation.canonical_hash(samples)
    predictions = dict(shared, kind="predictions", manifest_sha256=manifest,
                       prediction_version="validation-only-no-predictions", rows=[],
                       source_git=catalog["source_git"], run_id="validation-only-no-measurement",
                       dependency_lock_sha256=evaluation.canonical_hash(catalog),
                       configuration_sha256=manifest, decode_configuration_sha256=manifest)
    annotations = dict(shared, kind="annotations", manifest_sha256=manifest,
                       annotation_version="validation-only-no-labels", rows=[])
    rows = evaluation.validate_inputs(samples, predictions, annotations, catalog)[0]
    # A transformed member can have a new local hash yet share the registered
    # original. Preserve those identities across datasets instead of treating
    # resized copies as independent scenes. Missing historical records cannot
    # be inferred here.
    originals = defaultdict(set)
    for row in [*rows.values(), *samples.get("split_registry", [])]:
        for source in row.get("sources", []):
            if source.get("original_sha256"):
                originals[("original_sha256", source["original_sha256"])].add(row["split"])
            if all(_text(source.get(key)) for key in ("dataset_id", "dataset_version", "original_file_id")):
                originals[("original_file_id", source["dataset_id"], source["dataset_version"],
                           source["original_file_id"])].add(row["split"])
    evaluation.require(not any(len(splits) > 1 for splits in originals.values()),
                       "registered original/derivative crosses splits")
    return rows


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _file_identity(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def source_fingerprint(path):
    """JSON-safe mutation token; Windows uses the production native contract."""
    path = Path(path).resolve()
    if sys.platform == "win32":
        from ai_cull_assistant.image_decode import _windows_change_time, _stat_identity
        changed = _windows_change_time(path)
        if changed is None:
            raise OSError("reliable_native_windows_change_time_unavailable")
        signature = (str(path), *_stat_identity(path.stat()), ("win-change-time", changed))
    else:
        signature = (str(path), *_file_identity(path.stat()))
    return json.loads(json.dumps(signature))


def dependency_problems(sample, binding, config):
    """Check existing local capabilities only; never install or download."""
    problems = []
    mode = config.get("asset_modes", {}).get(sample["asset_id"], config.get("analysis_mode"))
    for package in ("PIL", "numpy", "cv2"):
        try:
            importlib.import_module(package)
        except (ImportError, OSError) as error:
            problems.append(f"dependency_unavailable:{package}:{type(error).__name__}")
    if binding.get("raw_source_id"):
        try:
            importlib.import_module("rawpy")
        except (ImportError, OSError) as error:
            problems.append(f"dependency_unavailable:rawpy:{type(error).__name__}")
    if mode != "landscape":
        model = evaluation.REPO / "src/ai_cull_assistant/data/face_detection_yunet_2023mar.onnx"
        if not model.is_file():
            problems.append("dependency_unavailable:yunet_model")
    if config.get("body_check"):
        try:
            from ai_cull_assistant.body_focus import find_body_models
            find_body_models()
        except (ImportError, OSError, RuntimeError) as error:
            problems.append(f"dependency_unavailable:body_models:{type(error).__name__}")
    return problems


def hash_file(path):
    """Hash only this explicit regular member; reject a change during reading."""
    before = path.stat()
    if not path.is_file():
        raise OSError("not a regular file")
    value = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
            size += len(chunk)
        after_handle = os.fstat(stream.fileno())
    after = path.stat()
    if _file_identity(before) != _file_identity(after_handle) or _file_identity(before) != _file_identity(after):
        raise OSError("input_changed_during_preflight")
    return value.hexdigest(), size


def _registry(samples):
    entries = samples.get("dataset_registry")
    if entries is None:
        return {}, ["dataset_registry_missing"]
    evaluation.require(isinstance(entries, list), "dataset_registry must be an array")
    result = {}
    for row in entries:
        evaluation.require(isinstance(row, dict), "dataset_registry entry must be an object")
        key = (row.get("dataset_id"), row.get("dataset_version"))
        evaluation.require(all(_text(v) for v in key), "dataset_registry requires dataset_id/version")
        evaluation.require(key not in result, "duplicate dataset_registry identity")
        result[key] = row
    return result, []


def _source_problems(source, registry):
    problems = []
    for key in ("dataset_id", "dataset_version", "original_file_id"):
        if not _text(source.get(key)):
            problems.append("source_metadata_missing:" + key)
    sha = source.get("original_sha256")
    if not isinstance(sha, str) or not evaluation.SHA.fullmatch(sha):
        problems.append("source_metadata_missing:original_sha256")
    for key in ("width", "height"):
        if type(source.get(key)) is not int or source[key] <= 0:
            problems.append("source_metadata_missing:" + key)
    if not isinstance(source.get("preprocessing_history"), list):
        problems.append("source_metadata_missing:preprocessing_history")
    elif not all(_text(item) or isinstance(item, dict) and item for item in source["preprocessing_history"]):
        problems.append("source_metadata_invalid:preprocessing_history")
    if source.get("preprocessing_history") == [] and source.get("original_sha256") != source.get("sha256"):
        problems.append("original_hash_differs_without_preprocessing_history")
    entry = registry.get((source.get("dataset_id"), source.get("dataset_version")))
    if entry is None:
        problems.append("source_dataset_not_registered")
    else:
        for key in ("source_url", "license", "attribution", "authorization", "historical_exposure"):
            if not _text(entry.get(key)):
                problems.append("dataset_metadata_missing:" + key)
        if entry.get("authorization_confirmed") is not True:
            problems.append("dataset_authorization_not_confirmed")
    return problems


def validate_path_map(samples, path_map):
    """Pure structural path validation; does not open image bytes."""
    srows = evaluation.unique_rows(samples["rows"], "samples")
    evaluation.require(isinstance(path_map, dict) and path_map.get("schema_version") == PATH_SCHEMA,
                       "unsupported path map schema")
    evaluation.require(path_map.get("manifest_sha256") == evaluation.canonical_hash(samples),
                       "path map manifest_sha256 mismatch")
    bindings = path_map.get("bindings")
    evaluation.require(isinstance(bindings, dict), "path map bindings must be object")
    evaluation.require(not (bindings.keys() - srows.keys()), "path map contains unlisted assets")
    paths_seen = {}
    # Validate all mappings before opening any image, including unused members.
    normalized = {}
    for aid, sample in srows.items():
        binding = bindings.get(aid)
        if binding is None:
            continue
        evaluation.require(isinstance(binding, dict), f"{aid}: invalid binding")
        member_paths = binding.get("sources")
        evaluation.require(isinstance(member_paths, dict), f"{aid}: sources mapping required")
        expected = {s["source_id"] for s in sample["sources"]}
        evaluation.require(set(member_paths) == expected, f"{aid}: source mapping must exactly match listed sources")
        resolved = {}
        for sid, value in member_paths.items():
            evaluation.require(_text(value) and Path(value).is_absolute(), f"{aid}/{sid}: explicit absolute path required")
            path = Path(value).resolve()
            identity = os.path.normcase(str(path))
            evaluation.require(identity not in paths_seen, f"source path reused: {aid}/{sid} and {paths_seen.get(identity)}")
            paths_seen[identity] = (aid, sid)
            resolved[sid] = path
        primary = binding.get("primary_path")
        evaluation.require(_text(primary) and Path(primary).is_absolute(), f"{aid}: explicit primary_path required")
        evaluation.require(Path(primary).resolve() in resolved.values(), f"{aid}: primary_path is not a listed source")
        normalized[aid] = resolved
    return normalized


def validate_source_registration(source, samples):
    """Return missing source/permission metadata without filling any fields."""
    registry, problems = _registry(samples)
    return problems + _source_problems(source, registry)


def preflight(samples, path_map, config):
    """Validate metadata first, then hash only explicitly bound listed files.

    Broken document identities raise ContractError. Per-case missing files,
    unsupported formats, source gaps, and needed subject decisions are retained.
    No authorization/history/scene/quality labels are invented.
    """
    started = time.perf_counter()
    srows = validate_samples(samples)
    evaluation.require(isinstance(path_map, dict) and path_map.get("schema_version") == PATH_SCHEMA,
                       "unsupported path map schema")
    manifest = evaluation.canonical_hash(samples)
    evaluation.require(path_map.get("manifest_sha256") == manifest, "path map manifest_sha256 mismatch")
    bindings = path_map.get("bindings")
    evaluation.require(isinstance(bindings, dict), "path map bindings must be object")
    evaluation.require(not (bindings.keys() - srows.keys()), "path map contains unlisted assets")
    evaluation.require(isinstance(config, dict), "measurement config must be object")
    registry, global_problems = _registry(samples)
    if "split_registry" not in samples:
        global_problems.append("split_registry_not_declared")
    if not _text(samples.get("registry_review")):
        global_problems.append("registry_review_missing")
    rows = []
    cost = {"hash_attempts": 0, "hash_files": 0, "hash_bytes": 0, "hash_seconds": 0.0}
    normalized = validate_path_map(samples, path_map)
    for aid, sample in srows.items():
        problems = list(global_problems)
        status = "ready"
        source_results = {}
        binding = bindings.get(aid)
        if binding is None:
            rows.append({"asset_id": aid, "status": "missing", "problems": ["path_binding_missing"], "sources": {}, "binding": None})
            continue
        dependency_gaps = dependency_problems(sample, binding, config)
        if dependency_gaps:
            problems.extend(dependency_gaps)
            status = "unsupported"
        for source in sample["sources"]:
            sid = source["source_id"]
            problems.extend(f"{sid}:{p}" for p in _source_problems(source, registry))
        # Session and burst are explicit nullable registrations, not guesses.
        for key in ("session_id", "burst_id"):
            if key not in sample:
                problems.append("relationship_registration_missing:" + key)
        if samples.get("evaluation_kind") != "synthetic":
            touched = any(any(name in str(s.get("dataset_id", "")).casefold() for name in HISTORICAL_DATASETS)
                          for s in sample["sources"])
            if touched and (samples["evaluation_kind"] != "development" or sample["split"] != "development"):
                problems.append("historical_dataset_requires_development_diagnostics")
        by_id = {source["source_id"]: source for source in sample["sources"]}
        for role, formats in (("raw_source_id", RAW_FORMATS), ("jpeg_source_id", RASTER_FORMATS)):
            sid = binding.get(role)
            if sid is not None and (sid not in by_id or by_id[sid]["format"].upper().lstrip(".") not in formats):
                problems.append("member_role_mismatch:" + role)
        assigned = {binding.get("raw_source_id"), binding.get("jpeg_source_id")} - {None}
        if assigned != set(by_id):
            problems.append("measurement_member_roles_incomplete")
        if len(sample["sources"]) > 1 and not (binding.get("raw_source_id") and binding.get("jpeg_source_id")):
            problems.append("explicit_raw_jpeg_pair_required")
        if len(sample["sources"]) == 2:
            raster = by_id.get(binding.get("jpeg_source_id"), {})
            if raster.get("format", "").upper().lstrip(".") not in {"JPG", "JPEG"}:
                problems.append("paired_raster_must_be_jpeg")
                status = "unsupported"
        if len(sample["sources"]) > 2:
            problems.append("more_than_two_measurement_members_unsupported")
        for sid, source in by_id.items():
            path = normalized[aid][sid]
            fmt = source["format"].upper().lstrip(".")
            if fmt not in SUPPORTED_FORMATS:
                problems.append(f"{sid}:format_unsupported:{fmt}")
                status = "unsupported"
                continue
            actual_ext = path.suffix.upper().lstrip(".")
            aliases = ({"JPG", "JPEG"}, {"TIF", "TIFF"})
            if actual_ext != fmt and not any({actual_ext, fmt} <= group for group in aliases):
                problems.append(f"{sid}:declared_format_extension_mismatch")
                status = "failed"
                continue
            if not path.exists():
                problems.append(f"{sid}:file_missing")
                status = "missing"
                continue
            # Do not read bytes when the user has not explicitly registered permission.
            permission = registry.get((source.get("dataset_id"), source.get("dataset_version")), {})
            if permission.get("authorization_confirmed") is not True:
                continue
            tick = time.perf_counter()
            cost["hash_attempts"] += 1
            try:
                fingerprint_before = source_fingerprint(path)
                actual_hash, size = hash_file(path)
                fingerprint = source_fingerprint(path)
                if fingerprint_before != fingerprint:
                    raise OSError("input_changed_during_preflight")
                cost["hash_files"] += 1
                cost["hash_bytes"] += size
                source_results[sid] = {"path": str(path), "sha256": actual_hash, "size_bytes": size, "fingerprint": fingerprint}
                if actual_hash != source["sha256"]:
                    problems.append(f"{sid}:content_hash_mismatch")
                    status = "failed"
            except OSError as error:
                problems.append(f"{sid}:read_failed:{type(error).__name__}:{error}")
                status = "failed"
            finally:
                cost["hash_seconds"] += time.perf_counter() - tick
        if status == "ready" and problems:
            status = "provenance_insufficient"
        if status == "ready" and sample["scene_type"] in {"unknown", "mixed"}:
            problems.append("explicit_subject_scope_required_no_scene_inference")
            status = "needs_confirmation"
        if status == "ready" and config.get("asset_modes", {}).get(aid, config.get("analysis_mode")) == "landscape" and sample["scene_type"] != "landscape":
            problems.append("landscape_mode_requires_explicit_landscape_sample")
            status = "needs_confirmation"
        rows.append({"asset_id": aid, "status": status, "problems": problems,
                     "sources": source_results, "binding": binding})
    basenames = Counter(path.name.casefold() for members in normalized.values() for path in members.values())
    cost["total_seconds"] = time.perf_counter() - started
    return {"manifest_sha256": manifest, "rows": rows, "problems": global_problems,
            "bindings": bindings, "status_counts": dict(Counter(row["status"] for row in rows)),
            "cost": cost, "same_basename_members": sorted(name for name, count in basenames.items() if count > 1),
            "independent_blind_test": False}
