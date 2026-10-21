#!/usr/bin/env python3
"""Read frozen session evidence without loading the application or photographs.

A measurement-time provenance receipt is mandatory. This exporter can verify
bindings and saved-record coherence, not authenticate a receipt's author or
establish the current state of an original photograph. Never create a receipt
retrospectively to lend provenance to an old session.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys

try:  # Works both as a script and through importlib without production imports.
    from tools import offline_evaluation as oe
except ModuleNotFoundError:
    import offline_evaluation as oe

REPO = Path(__file__).resolve().parents[1]
PROVENANCE_SCHEMA = "frozen-prediction-provenance-v1"
CAPTURE_SCHEMA = "frozen-prediction-provenance-v2"
ContractError = oe.ContractError
VERSION_FIELDS = {
    "analysis_modes": ("analysis_modes", "VERSION"),
    "landscape": ("analysis_modes", "LANDSCAPE_VERSION"),
    "primary_quality": ("primary_quality", "POLICY_VERSION"),
    "face": ("face_focus", "VERSION"),
    "decode": ("image_decode", "DECODE_VERSION"),
    "focus_protocol": ("focus_protocol", "PROTOCOL_VERSION"),
    "manual_face_scope": ("primary_quality", "MANUAL_FACE_SCOPE_VERSION"),
}
SUPPORTED_VERSIONS = {
    "analysis_modes": "manual-modes-v1", "landscape": "landscape-noise-guard-v1",
    "primary_quality": "confirmed-primary-quality-v2-effective-region",
    "face": "native-face-v6-effective-region", "decode": "source-first-v1",
    "focus_protocol": "focus-technical-v4", "manual_face_scope": "unique-contained-manual-face-v1",
}
LIMITATIONS = [
    "Saved-evidence coherence only; no current-photo or live-settings freshness verification.",
    "The external measurement-time receipt is an attestation, not an authenticated signature or proof of history.",
    "No photographs, JSON-referenced paths, production modules, models, decoders, AI services or human labels are read or generated.",
    "Only automatic face-only portrait scopes and explicit landscape evidence are supported; human/manual, body-enabled and legacy portrait evidence fail conservatively.",
    "Source hashes are supplied measurement-time bindings; original photograph bytes are not rehashed.",
    "Real-world accuracy has not been measured; no acceptance, independence or production-enable conclusion.",
]


def require(condition, message):
    oe.require(condition, message)


def sha(value, context):
    require(isinstance(value, str) and bool(oe.SHA.fullmatch(value)) and value != "0" * 64,
            f"{context}: nonzero SHA-256 required")
    return value


def parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid(value):
        raise ContractError(f"non-finite JSON number: {value}")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)


def expected_versions(repo, source_git):
    """Inspect static constants at the receipt's verified commit, never imports."""
    require(isinstance(source_git, str) and bool(oe.COMMIT.fullmatch(source_git)),
            "receipt requires full lowercase source_git")
    commit = oe.git_bytes(repo, "rev-parse", "--verify", f"{source_git}^{{commit}}").decode().strip()
    require(commit == source_git, "source_git does not resolve to the specified commit")
    result, trees = {}, {}
    for name, (module, constant) in VERSION_FIELDS.items():
        if module not in trees:
            trees[module] = ast.parse(oe.git_bytes(repo, "show", f"{commit}:src/ai_cull_assistant/{module}.py").decode("utf-8-sig"))
        values = [node.value.value for node in trees[module].body
                  if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                  and isinstance(node.value.value, str)
                  and any(isinstance(target, ast.Name) and target.id == constant for target in node.targets)]
        require(len(values) == 1, f"source commit: unsupported version constant {module}.{constant}")
        result[name] = values[0]
    return result


def timestamp(value, context):
    require(isinstance(value, str) and bool(value.strip()), f"{context}: timestamp required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{context}: invalid timestamp") from exc
    require(result.tzinfo is not None, f"{context}: timestamp requires timezone")
    require(result <= datetime.now(timezone.utc), f"{context}: future timestamp")
    return result


def receipt_records(provenance):
    records = provenance.get("records")
    require(isinstance(records, dict), "receipt: existing frozen records required")
    hashes = {}
    dependency = "dependency_snapshot" if provenance.get("schema_version") == CAPTURE_SCHEMA else "dependency_lock"
    for name in (dependency, "configuration", "decode_configuration"):
        record = records.get(name)
        require(isinstance(record, dict), f"receipt: missing {name} record")
        content = oe.text_field(record, "content", name)
        supplied = sha(record.get("sha256"), name)
        require(oe.digest(content.encode("utf-8")) == supplied, f"receipt: {name} record hash mismatch")
        hashes[("dependency_lock" if name == "dependency_snapshot" else name) + "_sha256"] = supplied
    return hashes


def reason_records(evidence):
    """Keep every raw local reason occurrence, including unknown strings."""
    records = []
    def visit(value, path, reason_array=False):
        if reason_array:
            require(isinstance(value, list) and all(isinstance(v, str) and bool(v) for v in value),
                    f"{path}: invalid raw reasons")
            records.extend({"pointer": path + "/" + str(i), "value": v} for i, v in enumerate(value))
        elif isinstance(value, dict):
            for key, child in value.items():
                escaped = key.replace("~", "~0").replace("/", "~1")
                pointer = path + "/" + escaped
                if key == "reason" or key.endswith("_reason"):
                    require(isinstance(child, str) and bool(child), f"{pointer}: invalid raw reason")
                    records.append({"pointer": pointer, "value": child})
                elif key == "reasons" or key.endswith("_reasons"):
                    visit(child, pointer, True)
                elif key == "reasons_by_part":
                    require(isinstance(child, dict), f"{pointer}: expected reason map")
                    for part, codes in child.items():
                        visit(codes, pointer + "/" + part, True)
                elif not key.startswith("ai_"):
                    visit(child, pointer)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, path + "/" + str(i))
    visit(evidence, "/clarity_evidence")
    return records


def check_source_signature(inputs, asset, session):
    signature = inputs.get("source_signature")
    require(isinstance(signature, list) and bool(signature), "missing measured source_signature")
    expected = {role: asset[role] for role in ("primary_path", "jpg_path", "raw_path") if asset.get(role) is not None}
    require(expected and all(isinstance(p, str) and bool(p) for p in expected.values()), "invalid asset source members")
    stats = session.get("source_stats")
    require(isinstance(stats, dict), "session source_stats required")
    seen, identities = set(), {}
    for entry in signature:
        require(isinstance(entry, list) and len(entry) == 2 and isinstance(entry[0], str), "invalid measured source_signature entry")
        role, identity = entry
        require(role in expected and role not in seen, "ambiguous measured source roles")
        seen.add(role)
        require(isinstance(identity, list) and len(identity) in (6, 7)
                and all(type(v) is int and v >= 0 for v in identity[1:6]), "incomplete measured source identity")
        if len(identity) == 7:
            token = identity[6]
            require(isinstance(token, list) and len(token) == 2, "invalid Windows source identity token")
            require((token[0] == "win-change-time" and type(token[1]) is int and token[1] > 0)
                    or (token[0] == "sha256" and isinstance(token[1], str) and bool(oe.SHA.fullmatch(token[1]))
                        and token[1] != "0" * 64), "unverified Windows source identity token")
        require(identity[0] == expected[role], "measured source path does not match frozen asset")
        require(identity[0] not in identities or identities[identity[0]] == identity,
                "conflicting measured identities for the same source member")
        identities[identity[0]] = identity
        frozen_stats = stats.get(expected[role])
        require(isinstance(frozen_stats, list) and len(frozen_stats) == 2
                and all(type(v) is int and v >= 0 for v in frozen_stats), "missing frozen source stats")
        require(identity[1:3] == frozen_stats, "measured source stats are stale against frozen session")
    require(seen == set(expected), "incomplete source member measurement signature")
    return signature


def check_portrait(asset, evidence, inputs, versions):
    require(evidence.get("version") == versions["analysis_modes"], "unsupported portrait evidence version")
    require(evidence.get("measurement_inputs") == inputs, "saved measurement_inputs mismatch")
    require(inputs.get("enabled_parts") == ["face"] and evidence.get("enabled_parts") == ["face"],
            "body-enabled or incomplete portrait measurement is unsupported")
    policy = evidence.get("primary_quality")
    scope = asset.get("focus_scope")
    require(isinstance(policy, dict) and isinstance(scope, dict), "saved primary policy and focus scope required")
    require(policy.get("version") == versions["primary_quality"], "unsupported primary quality version")
    require(scope.get("version") == versions["focus_protocol"] and scope.get("mode") == "portrait", "unsupported focus scope version/mode")
    require(policy.get("enabled_parts") == scope.get("enabled_parts") == ["face"], "policy/scope part mismatch")
    require(policy.get("recommendation") == evidence.get("recommendation"), "saved local recommendation disagreement")
    expected_state = {"keep": "clear", "reject": "severe_blur", "review": "uncertain"}.get(policy.get("recommendation"))
    require(expected_state is not None and policy.get("state") == evidence.get("state") == expected_state,
            "saved portrait state/recommendation disagreement")
    origin = policy.get("selection_origin")
    require(origin in ("human", "automatic") and scope.get("selection_origin") == origin, "ambiguous saved selection origin")
    selection_digest = sha(policy.get("selection_digest"), "selection_digest")
    require(scope.get("selection_digest") == selection_digest, "saved selection scope mismatch")
    stamp = "measurement_confirmation_digest" if origin == "human" else "measurement_selection_digest"
    require(evidence.get(stamp) == selection_digest, "stale measurement selection stamp")
    source_digest = oe.canonical_hash(inputs["source_signature"])
    require(scope.get("source_digest") == source_digest and scope.get("photo_identity") == "photo:" + source_digest,
            "saved scope source mismatch")
    selection = evidence.get("automatic_selection") if origin == "automatic" else None
    require(origin == "automatic", "human/manual selection snapshot not fully saved; unsupported portrait scope")
    require(policy.get("confirmation_digest") is None and scope.get("confirmation_digest") is None, "automatic selection carries human confirmation")
    require(isinstance(selection, dict) and selection.get("version") == "automatic-primary-selection-v1"
            and selection.get("origin") == "automatic" and selection.get("human_confirmed") is False,
            "missing saved automatic selection")
    require(selection.get("digest") == selection_digest
            and oe.canonical_hash({k: v for k, v in selection.items() if k != "digest"}) == selection_digest,
            "saved automatic selection digest mismatch")
    require(all(selection.get(k) == value for k, value in inputs.items()), "automatic selection measured inputs mismatch")
    require(scope.get("settings_digest") == inputs.get("parameters_digest"), "saved automatic settings mismatch")
    proof = evidence.get("automatic_subject")
    require(isinstance(proof, dict) and proof.get("version") == "single-detected-face-v1"
            and type(proof.get("candidate_count")) is int and proof["candidate_count"] == 1
            and proof.get("landmarks_available") is True
            and selection.get("detector_provenance") == proof, "ambiguous/missing saved detector provenance")
    box = proof.get("box")
    require(isinstance(box, list) and len(box) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in box),
            "invalid saved normalized face region")
    x, y, width, height = box
    require(x >= 0 and y >= 0 and width > 0 and height > 0
            and x + width <= 1 + 1e-9 and y + height <= 1 + 1e-9,
            "saved face region outside source")
    regions = selection.get("regions")
    require(isinstance(regions, list) and len(regions) == 1 and isinstance(regions[0], dict)
            and regions[0].get("kind") == "face" and regions[0].get("box") == proof.get("box"),
            "saved automatic region/proof mismatch")
    require(scope.get("subjects") == [{"id": regions[0].get("id"), "kind": "face"}],
            "saved automatic subjects mismatch")
    candidates = proof.get("candidate_provenance")
    if candidates is not None:
        require(isinstance(candidates, dict) and candidates.get("diagnostic_version") == "subject-candidates-v1"
                and candidates.get("coordinate_space") == "normalized_exif_oriented"
                and type(candidates.get("chosen_initial_index")) is int and candidates["chosen_initial_index"] == 0
                and candidates.get("chosen_source") == "initial_yunet"
                and isinstance(candidates.get("initial_candidates"), list) and len(candidates["initial_candidates"]) == 1
                and isinstance(candidates["initial_candidates"][0], dict)
                and candidates["initial_candidates"][0].get("box") == proof.get("box")
                and candidates["initial_candidates"][0].get("landmarks_available") is True,
                "ambiguous saved detector candidates")
    sha(inputs.get("parameters_digest"), "measurement parameters_digest")
    subjects = scope.get("subjects")
    require(isinstance(subjects, list) and bool(subjects)
            and all(isinstance(s, dict) and s.get("kind") == "face" and isinstance(s.get("id"), str)
                    and s["id"].startswith("face:") for s in subjects), "non-face or invalid scope subjects")
    require(len({s["id"] for s in subjects}) == len(subjects), "duplicate scope subjects")
    require(scope.get("digest") == oe.canonical_hash({k: v for k, v in scope.items() if k != "digest"}), "saved focus scope digest mismatch")
    # Face decode provenance cannot establish the source used by separate body stages.
    require(not evidence.get("body_results") and not evidence.get("body_candidates") and not evidence.get("body"),
            "independent body-stage decode provenance unavailable")
    raw_subjects = evidence.get("subjects")
    require(isinstance(raw_subjects, list) and len(raw_subjects) == 1
            and isinstance(raw_subjects[0], dict) and raw_subjects[0].get("evidence_kind") == "face"
            and raw_subjects[0].get("normalized_box") == proof.get("box")
            and not raw_subjects[0].get("body") and not raw_subjects[0].get("body_evidence"),
            "saved face-only subject provenance mismatch")
    return policy["recommendation"]


def check_capture(mapping, sample, asset, versions, recorded_at):
    """Validate only new, complete capture records; never retrofit old evidence."""
    capture = mapping.get("capture")
    require(isinstance(capture, dict) and capture.get("status") == "complete"
            and capture.get("kind") == "new_measurement", "measurement capture incomplete, cached or unavailable")
    start = timestamp(capture.get("measured_at"), "capture start")
    finish = timestamp(capture.get("finished_at"), "capture finish")
    require(start <= finish <= recorded_at and mapping.get("measured_at") == capture["measured_at"],
            "capture measurement/freeze times disagree")
    dependency = capture.get("dependency_snapshot")
    require(isinstance(dependency, dict) and dependency.get("kind") == "actual_dependency_snapshot"
            and dependency.get("production_dependency_lock") is False
            and isinstance(dependency.get("installed"), dict) and isinstance(dependency.get("loaded"), dict)
            and isinstance(dependency.get("python"), str) and isinstance(dependency.get("platform"), str),
            "actual measurement dependency snapshot missing")
    before, after = capture.get("state_before"), capture.get("state_after")
    require(isinstance(before, dict) and before and before == after, "capture input state changed or missing")
    require(before.get("settings") == capture.get("settings_snapshot")
            and before.get("confirmation") == capture.get("confirmation_snapshot"),
            "capture settings/confirmation snapshots disagree")
    require(before.get("mode") == asset.get("analysis_mode"), "capture analysis mode changed")
    require(capture.get("evidence_sha256") == oe.canonical_hash(asset.get("clarity_evidence"))
            and capture.get("focus_scope_sha256") == oe.canonical_hash(asset.get("focus_scope")),
            "captured evidence/scope differs from frozen result")
    hashes = before.get("source_hashes")
    require(isinstance(hashes, dict) and hashes, "capture source hashes missing")
    paths = {asset.get(k) for k in ("primary_path", "jpg_path", "raw_path") if asset.get(k)}
    require(set(hashes) == paths, "capture source member set mismatch")
    for path, value in hashes.items():
        sha(value, "capture member")
    members = mapping.get("source_members")
    require(isinstance(members, dict) and set(members) == {s["source_id"] for s in sample["sources"]},
            "capture manifest member mapping incomplete")
    require(len(set(members.values())) == len(members), "ambiguous source member mapping")
    sources = {members[s["source_id"]]: s for s in sample["sources"]}
    require(set(sources) == paths, "sample source members differ from captured exposure")
    require(all(hashes[p] == source["sha256"] for p, source in sources.items()), "captured source content hash mismatch")
    require(members.get(mapping["evaluated_source_id"]) == mapping["source_member"], "capture selected source member mismatch")
    stages = capture.get("stages")
    require(isinstance(stages, list) and stages, "no actual measurement stage")
    for stage in stages:
        require(isinstance(stage, dict) and stage.get("name") in ("face", "body", "landscape")
                and stage.get("status") == "complete", "incomplete or unknown measurement stage")
        require(start <= timestamp(stage.get("measured_at"), "stage start")
                <= timestamp(stage.get("finished_at"), "stage finish") <= finish, "stage times outside measurement")
        require(stage.get("settings_before") == stage.get("settings_after") == capture.get("settings_snapshot")
                and stage.get("confirmation_before") == stage.get("confirmation_after") == capture.get("confirmation_snapshot"),
                "stage effective settings/confirmation changed")
        path = stage.get("source_member")
        require(path in hashes and stage.get("input_sha256") == hashes[path], "stage source content binding mismatch")
        decode = stage.get("decode")
        require(isinstance(decode, dict) and decode.get("version") == versions["decode"]
                and decode.get("source_path") == path and decode.get("source_kind") == stage.get("source_kind"),
                "independent stage decoder binding mismatch")
        require(stage.get("coordinate_space") == "normalized_display", "unsupported stage coordinate space")
        size = stage.get("image_size")
        require(isinstance(size, list) and len(size) == 2 and all(type(n) is int and n > 0 for n in size),
                "stage native image size missing")
        require(isinstance(stage.get("options"), dict), "stage decoder options missing")
        configuration = stage.get("decoder_configuration")
        require(isinstance(configuration, dict) and configuration.get("kind") == "actual_decoder_call"
                and configuration.get("status") not in ("unverifiable", "unverifiable_cached_decode"), "actual decoder configuration missing")
        kind = stage.get("source_kind")
        require(configuration.get("source_kind") == ("standard_image" if kind == "paired_jpeg" else kind)
                and isinstance(configuration.get("backend"), str) and bool(configuration["backend"])
                and isinstance(configuration.get("backend_version"), str), "decoder implementation/source configuration missing")
        if kind == "raw_full_decode":
            post = configuration.get("postprocess")
            require(isinstance(post, dict) and {"use_camera_wb", "half_size", "no_auto_bright", "output_bps"} <= set(post)
                    and all(type(post[k]) is bool for k in ("use_camera_wb", "half_size", "no_auto_bright"))
                    and type(post["output_bps"]) is int and isinstance(configuration.get("orientation"), str)
                    and type(configuration.get("unpack")) is bool, "RAW postprocess configuration incomplete")
        fmt = sources[path]["format"].upper().lstrip(".")
        require(fmt in {"RAW", "RW2", "ARW", "CR2", "CR3", "NEF", "ORF", "RAF", "DNG",
                        "JPEG", "JPG", "IMAGE/JPEG", "PNG", "WEBP", "TIF", "TIFF", "BMP", "HEIC", "HEIF", "AVIF"},
                "unsupported stage source format")
        raw = fmt in {"RAW", "RW2", "ARW", "CR2", "CR3", "NEF", "ORF", "RAF", "DNG"}
        require(kind in ("paired_jpeg", "standard_image", "raw_full_decode", "raw_embedded_jpeg", "raw_embedded_bitmap")
                and raw == kind.startswith("raw_"), "stage format/decoder mismatch")
        if kind == "paired_jpeg":
            require(asset.get("raw_path") and path == asset.get("jpg_path") and fmt in {"JPEG", "JPG", "IMAGE/JPEG"},
                    "stage paired JPEG source mismatch")
        elif raw:
            require(path == asset.get("raw_path"), "stage RAW member mismatch")
        else:
            require(path != asset.get("raw_path"), "stage standard member mismatch")
    if asset.get("analysis_mode") == "landscape":
        require(all(s["name"] == "landscape" for s in stages), "landscape stage identity mismatch")
    return capture


def _valid_regions(regions):
    require(isinstance(regions, list) and bool(regions), "captured selected regions missing")
    ids = set()
    for region in regions:
        require(isinstance(region, dict) and region.get("kind") in ("face", "body"), "invalid selected region")
        identity = region.get("id")
        require(isinstance(identity, str) and identity.startswith(region["kind"] + ":") and identity not in ids,
                "invalid/duplicate selected region identity")
        ids.add(identity)
        box = region.get("box")
        require(isinstance(box, list) and len(box) == 4 and all(type(v) in (int, float) and math.isfinite(v) for v in box),
                "invalid captured normalized region")
        x, y, w, h = box
        require(x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= 1 + 1e-9 and y + h <= 1 + 1e-9,
                "captured region outside source")
    return ids


def check_portrait_v2(asset, evidence, inputs, versions, capture):
    """Use independently recorded stages and complete confirmation, no scoring."""
    policy, scope = evidence.get("primary_quality"), asset.get("focus_scope")
    require(isinstance(policy, dict) and isinstance(scope, dict), "portrait policy/scope missing")
    # Automatic face-only retains every old validation. Merely having a new
    # envelope is not permission to relax its detector provenance checks.
    if policy.get("selection_origin") == "automatic":
        require(inputs.get("enabled_parts") == ["face"], "automatic body scope not yet supported")
        require(all(s["name"] == "face" for s in capture["stages"]), "automatic face-only stage identity mismatch")
        require(capture.get("confirmation_snapshot") is None, "automatic result carries confirmation snapshot")
        proof = evidence.get("automatic_subject", {})
        for stage in capture["stages"]:
            regions = stage.get("regions")
            require(isinstance(regions, list) and len(regions) == 1 and isinstance(regions[0], list)
                    and len(regions[0]) == 2 and regions[0][0] == proof.get("box"),
                    "automatic stage region differs from saved detector")
        return check_portrait(asset, evidence, inputs, versions)
    require(policy.get("selection_origin") == scope.get("selection_origin") == "human", "unsupported selection origin")
    confirmation = capture.get("confirmation_snapshot")
    require(isinstance(confirmation, dict) and confirmation.get("human_confirmed") is True
            and confirmation.get("version") == versions["primary_quality"], "complete human confirmation missing")
    digest = sha(confirmation.get("digest"), "confirmation digest")
    require(oe.canonical_hash({k: v for k, v in confirmation.items() if k != "digest"}) == digest,
            "confirmation snapshot digest mismatch")
    require(confirmation.get("source_signature") == inputs.get("source_signature"), "confirmation source mismatch")
    require(policy.get("confirmation_digest") == policy.get("selection_digest") == scope.get("confirmation_digest")
            == scope.get("selection_digest") == evidence.get("measurement_confirmation_digest") == digest,
            "human confirmation/policy/scope stamp mismatch")
    require(evidence.get("version") == versions["analysis_modes"] and evidence.get("measurement_inputs") == inputs,
            "portrait measurement input/version mismatch")
    parts = inputs.get("enabled_parts")
    require(parts in (["face"], ["face", "body"]) and evidence.get("enabled_parts") == policy.get("enabled_parts")
            == scope.get("enabled_parts") == parts, "portrait enabled parts mismatch")
    require(policy.get("version") == versions["primary_quality"] and scope.get("version") == versions["focus_protocol"]
            and scope.get("mode") == "portrait", "portrait scope/policy version mismatch")
    require(scope.get("digest") == oe.canonical_hash({k: v for k, v in scope.items() if k != "digest"}),
            "portrait scope digest mismatch")
    source_digest = oe.canonical_hash(inputs["source_signature"])
    require(scope.get("source_digest") == source_digest and scope.get("photo_identity") == "photo:" + source_digest,
            "portrait scope source mismatch")
    regions = confirmation.get("regions")
    ids = _valid_regions(regions)
    settings = capture.get("settings_snapshot")
    require(isinstance(settings, dict) and isinstance(settings.get("global"), dict)
            and isinstance(settings.get("photo"), dict) and isinstance(settings.get("versions"), dict),
            "effective settings snapshot incomplete")
    require(settings["photo"].get("primary_subject_confirmation") == confirmation, "saved human confirmation snapshot mismatch")
    photo = {k: v for k, v in settings["photo"].items() if k != "primary_subject_confirmation"}
    expected = oe.canonical_hash({"global": settings["global"], "photo": photo, "versions": settings["versions"]})
    require(confirmation.get("parameters_digest") == expected, "human confirmation effective parameters mismatch")
    v = settings["versions"]
    require(set(v) == {"roi", "detection", "face", "body"} and v["face"] == versions["face"],
            "effective settings version snapshot mismatch")
    measured = {"global": settings["global"], "photo": photo, "confirmation": confirmation,
                "versions": [v[k] for k in ("roi", "detection", "face", "body")], "enabled_parts": parts}
    if "selected_bodies" in photo:
        measured["manual_body_scope"] = "explicit-body-scope-v1"
    require(inputs.get("parameters_digest") == oe.canonical_hash(measured), "measured effective parameters mismatch")
    has_face = any(r["kind"] == "face" for r in regions)
    expected_scope_settings = oe.canonical_hash([expected, versions["manual_face_scope"]]) if has_face else expected
    require(scope.get("settings_digest") == expected_scope_settings, "human scope settings mismatch")
    if has_face:
        require(inputs.get("manual_face_scope_contract") == scope.get("manual_face_scope_contract")
                == versions["manual_face_scope"], "manual face uniqueness contract missing")
    boxes = {kind: sorted([r["box"] for r in regions if r["kind"] == kind]) for kind in ("face", "body")}
    for stage in capture["stages"]:
        actual = stage.get("regions")
        require(isinstance(actual, list), "stage regions missing")
        if stage["name"] == "face":
            require(all(isinstance(r, list) and len(r) == 2 for r in actual), "face stage participant geometry missing")
            actual_boxes = [r[0] for r in actual]
            expected_boxes = boxes["face"]
        else:
            actual_boxes = actual
            expected_boxes = boxes["face"] if stage.get("operation") == "body_focus" else boxes["body"]
        require(actual_boxes and sorted(actual_boxes) == expected_boxes, "stage regions differ from confirmed manual range")
    needed = {r["kind"] for r in regions}
    if "body" in parts:
        needed.add("body")
    require(needed <= {s["name"] for s in capture["stages"]}, "independent required face/body stage missing")
    require(all(s["name"] in needed for s in capture["stages"]), "measurement stages disagree with requested portrait scope")
    subjects = scope.get("subjects")
    require(isinstance(subjects, list) and subjects and all(isinstance(s, dict) and s.get("id") in ids
            and s.get("kind") in ("face", "body") for s in subjects), "human scope subject mismatch")
    require(len({s["id"] for s in subjects}) == len(subjects), "duplicate human scope subjects")
    # A merged face/body scope is only accepted when the saved policy explicitly
    # accounts for every selected region. Do not recompute the association.
    people = policy.get("subjects")
    require(isinstance(people, list) and people, "saved human policy subjects missing")
    represented = {rid for person in people if isinstance(person, dict)
                   for rid in person.get("region_ids", [person.get("id")])}
    require(represented == ids and {p.get("id") for p in people} == {s["id"] for s in subjects},
            "saved policy does not account for complete manual range")
    recommendation = policy.get("recommendation")
    require(recommendation == evidence.get("recommendation") and recommendation in ("keep", "review", "reject"),
            "saved manual recommendation mismatch")
    state = {"keep": "clear", "review": "uncertain", "reject": "severe_blur"}[recommendation]
    require(policy.get("state") == evidence.get("state") == state, "saved manual state mismatch")
    return recommendation


def map_row(mapping, sample, asset, session, versions, recorded_at, *, capture_schema=False):
    result = {"asset_id": sample["asset_id"], "sample_version": sample["sample_version"],
              "evaluated_source_id": sample["evaluation_source_id"], "input_sha256": mapping["input_sha256"],
              "status": "failed", "recommendation": None, "reasons": []}
    diagnostic = {"asset_id": sample["asset_id"], "asset_index": mapping["asset_index"],
                  "session_pointer": "/assets/" + str(mapping["asset_index"]),
                  "evidence_pointer": "/assets/" + str(mapping["asset_index"]) + "/clarity_evidence",
                  "saved_local_evidence": asset.get("clarity_evidence"), "saved_focus_scope": asset.get("focus_scope"),
                  "measurement_versions": mapping.get("versions"), "measurement_inputs": mapping.get("measurement_inputs"),
                  "frozen_inputs": mapping.get("frozen_inputs"), "source_member": mapping.get("source_member"),
                  "source_kind": mapping.get("source_kind"), "reason_records": []}
    try:
        evidence = asset.get("clarity_evidence")
        require(isinstance(evidence, dict), "missing saved local clarity evidence")
        reasons = reason_records(evidence)
        diagnostic["reason_records"] = reasons
        result["reasons"] = [r["value"] for r in reasons]
        require(asset.get("clarity_version") == "clarity-v2", "unsupported saved clarity version")
        require(timestamp(mapping.get("measured_at"), "row measured_at") <= recorded_at, "measurement after receipt timestamp")
        require(mapping.get("versions") == versions, "measurement versions mismatch with actual source_git")
        require(versions == SUPPORTED_VERSIONS, "unsupported adapter evidence contract versions")
        inputs = mapping.get("measurement_inputs")
        require(isinstance(inputs, dict) and inputs == mapping.get("frozen_inputs"), "missing, stale or changed frozen measurement inputs")
        signature = check_source_signature(inputs, asset, session)
        for _, identity in signature:
            if identity[0] == mapping.get("source_member") and len(identity) == 7 and identity[6][0] == "sha256":
                require(identity[6][1] == mapping["input_sha256"], "measurement content token disagrees with selected sample hash")
        for flag in ("ai_focus_dirty", "ai_focus_stale", "person_review_pending"):
            require(asset.get(flag) is False, f"saved evidence stale/pending or missing boolean freshness: {flag}")
        require(not evidence.get("selection_pending"), "saved subject selection pending")
        mode = asset.get("analysis_mode")
        require(mode in ("portrait", "landscape") and evidence.get("mode") == mode, "unsupported/ambiguous analysis mode")
        capture = check_capture(mapping, sample, asset, versions, recorded_at) if capture_schema else None
        decode = evidence.get("decode")
        if capture is not None:
            stage = next((s for s in capture["stages"] if s["source_member"] == mapping.get("source_member")
                          and s["source_kind"] == mapping.get("source_kind")), None)
            require(stage is not None, "evaluated source has no actual measurement stage")
            # Body-only outputs may not carry the face decoder field. The v2
            # independent stage is the authority, never a guessed face source.
            if decode is None:
                decode = stage["decode"]
        require(isinstance(decode, dict) and decode.get("version") == versions["decode"], "missing/unsupported measurement decode provenance")
        member = oe.text_field(mapping, "source_member", "row")
        require(member in [asset.get(role) for role in ("primary_path", "jpg_path", "raw_path")], "actual source member absent from frozen asset")
        require(decode.get("source_path") == member and decode.get("source_kind") == mapping.get("source_kind"), "actual measurement decode source mismatch")
        kind = decode.get("source_kind")
        require(kind in ("paired_jpeg", "standard_image", "raw_full_decode", "raw_embedded_jpeg", "raw_embedded_bitmap"), "unsupported decode source kind")
        source = next(s for s in sample["sources"] if s["source_id"] == sample["evaluation_source_id"])
        source_format = source["format"].upper().lstrip(".")
        raw_formats = {"RAW", "RW2", "ARW", "CR2", "CR3", "NEF", "ORF", "RAF", "DNG"}
        standard_formats = {"JPEG", "JPG", "IMAGE/JPEG", "PNG", "WEBP", "TIF", "TIFF", "BMP", "HEIC", "HEIF", "AVIF"}
        require(source_format in raw_formats | standard_formats, "unsupported sample source format")
        require((source_format in raw_formats) == kind.startswith("raw_"), "sample source format disagrees with actual decoder member")
        if kind == "paired_jpeg":
            require(source_format in {"JPEG", "JPG", "IMAGE/JPEG"}, "paired JPEG sample format mismatch")
            require(asset.get("raw_path") and member == asset.get("jpg_path"), "paired JPEG provenance mismatch")
        elif kind.startswith("raw_"):
            require(member == asset.get("raw_path"), "RAW provenance mismatch")
        else:
            require(member != asset.get("raw_path"), "standard decode ambiguously identifies RAW")
        if mode == "portrait":
            recommendation = (check_portrait_v2(asset, evidence, inputs, versions, capture)
                              if capture is not None else check_portrait(asset, evidence, inputs, versions))
        else:
            require(evidence.get("version") == versions["landscape"], "unsupported landscape evidence version")
            recommendation = evidence.get("recommendation")
            require(recommendation in ("keep", "review"), "unsupported landscape recommendation; saved production landscape does not reject")
            require(evidence.get("state") == {"keep": "clear", "review": "uncertain"}[recommendation],
                    "saved landscape state/recommendation disagreement")
        require(recommendation in ("keep", "reject", "review"), "unknown/missing saved recommendation")
        if "local_recommendation" in asset:
            require(asset["local_recommendation"] == recommendation, "saved local recommendation field disagreement")
        require(recommendation == "review" or evidence.get("reliability") == "adequate", "automatic saved decision lacks adequate reliability")
        require(decode.get("evidence_sufficient") is not False or recommendation == "review", "insufficient decoded evidence carries automatic decision")
        if capture is not None and recommendation != "review":
            require(all(s["decode"].get("evidence_sufficient") is not False for s in capture["stages"]),
                    "insufficient independent stage carries automatic decision")
        result.update(status="ok", recommendation=recommendation)
    except ContractError as exc:
        result["failure_reason"] = str(exc)
        diagnostic["failure_reason"] = str(exc)
    diagnostic["status"] = result["status"]
    return result, diagnostic


def build_export(session, samples, provenance, *, session_sha256, repo=REPO, completion_proof=None):
    """Produce evaluator documents from a supplied immutable receipt.

    Document/source bindings are fatal on mismatch; unsupported saved evidence
    becomes an explicit failed prediction. Unmapped samples remain missing.
    """
    require(isinstance(session, dict) and type(session.get("version")) is int and session["version"] == 1,
            "unsupported scan-session version")
    require(isinstance(session.get("assets"), list) and all(isinstance(a, dict) for a in session["assets"]), "session assets array required")
    require(isinstance(provenance, dict) and provenance.get("schema_version") in (PROVENANCE_SCHEMA, CAPTURE_SCHEMA)
            and provenance.get("kind") == "measurement_time_receipt", "external measurement-time provenance receipt required")
    require(type(provenance.get("synthetic")) is bool, "receipt synthetic flag required")
    require(not provenance["synthetic"] or samples.get("evaluation_kind") == "synthetic", "synthetic receipt cannot support real evaluation")
    require(provenance.get("session_sha256") == sha(session_sha256, "session bytes"), "receipt frozen session byte hash mismatch")
    require(provenance.get("manifest_sha256") == oe.canonical_hash(samples), "receipt sample manifest binding mismatch")
    recorded_at = timestamp(provenance.get("recorded_at"), "receipt recorded_at")
    capture_schema = provenance.get("schema_version") == CAPTURE_SCHEMA
    if capture_schema:
        require(isinstance(completion_proof, dict)
                and completion_proof.get("session_sha256") == session_sha256
                and completion_proof.get("samples_sha256") == oe.canonical_hash(samples)
                and completion_proof.get("provenance_sha256") == oe.canonical_hash(provenance),
                "v2 requires independently loaded completed-set proof")
        identity = provenance.get("code_identity")
        require(isinstance(identity, dict) and identity.get("verified") is True
                and identity.get("dirty") is False
                and identity.get("source_git") == provenance.get("source_git"),
                "v2 code/build identity is not verifiable and clean")
        complete = provenance.get("completion")
        require(isinstance(complete, dict) and complete.get("status") == "complete"
                and complete.get("session_sha256") == provenance["session_sha256"]
                and complete.get("manifest_sha256") == provenance["manifest_sha256"],
                "v2 frozen publication incomplete or bindings mismatch")
        require(provenance.get("recording_status") in ("complete", "partial"), "invalid v2 recording status")
    hashes = receipt_records(provenance)
    if capture_schema:
        captures = [r.get("capture", {}) for r in provenance.get("rows", [])]
        expected_records = {
            "configuration": [c.get("settings_snapshot") for c in captures],
            "decode_configuration": [{"stages": c.get("stages"),
                "contract": "image_decode.source-first-v1:actual-decode-evidence"} for c in captures],
            "dependency_snapshot": {"kind": "actual_dependency_snapshot", "production_dependency_lock": False,
                "measurements": [c.get("dependency_snapshot") for c in captures]},
        }
        for name, expected in expected_records.items():
            try:
                actual = json.loads(provenance["records"][name]["content"])
            except (ValueError, TypeError) as exc:
                raise oe.ContractError(f"invalid v2 {name} JSON") from exc
            require(actual == expected, f"v2 {name} is not bound to captured measurements")
    versions = expected_versions(repo, provenance.get("source_git"))
    catalog = oe.make_catalog(repo, provenance["source_git"])
    envelope = {key: samples.get(key) for key in ("dataset_id", "dataset_version", "guideline_version", "manifest_version")}
    predictions = dict(envelope, schema_version=oe.SCHEMA, kind="predictions", manifest_sha256=oe.canonical_hash(samples),
                       source_git=provenance["source_git"], run_id=oe.text_field(provenance, "run_id", "receipt"),
                       prediction_version=oe.text_field(provenance, "prediction_version", "receipt"), rows=[], **hashes)
    annotations = dict(envelope, schema_version=oe.SCHEMA, kind="annotations", annotation_version="blank-human-annotation-template-v1",
                       manifest_sha256=oe.canonical_hash(samples), rows=[])
    # Validate the complete sample/capture/split contract before touching mappings.
    oe.evaluate(samples, predictions, annotations, catalog)
    srows = oe.unique_rows(samples["rows"], "samples")
    for sample in srows.values():
        for source in sample["sources"]:
            sha(source["sha256"], "sample source")
    mappings = provenance.get("rows")
    require(isinstance(mappings, list), "receipt rows required")
    mapped, indices, members, diagnostics = set(), set(), set(), []
    for mapping in mappings:
        require(isinstance(mapping, dict), "receipt row must be object")
        asset_id = oe.text_field(mapping, "asset_id", "receipt row")
        require(asset_id in srows and asset_id not in mapped, "unknown or duplicate receipt asset_id")
        mapped.add(asset_id)
        sample = srows[asset_id]
        require(mapping.get("sample_version") == sample["sample_version"], "receipt sample_version mismatch")
        require(mapping.get("evaluated_source_id") == sample["evaluation_source_id"], "receipt selected source mismatch")
        chosen = next(source for source in sample["sources"] if source["source_id"] == sample["evaluation_source_id"])
        require(sha(mapping.get("input_sha256"), "receipt source") == chosen["sha256"], "receipt source hash mismatch")
        index = mapping.get("asset_index")
        require(type(index) is int and 0 <= index < len(session["assets"]) and index not in indices, "invalid or reused receipt asset_index")
        indices.add(index)
        asset = session["assets"][index]
        require(sha(mapping.get("asset_sha256"), "receipt asset") == oe.canonical_hash(asset), "receipt frozen asset hash mismatch")
        member = oe.text_field(mapping, "source_member", "receipt row")
        require(member not in members, "duplicate receipt source member")
        members.add(member)
        owners = [i for i, row in enumerate(session["assets"]) if member in [row.get(role) for role in ("primary_path", "jpg_path", "raw_path")]]
        require(owners == [index], "ambiguous or unmatched frozen source member")
        prediction, diagnostic = map_row(mapping, sample, asset, session, versions, recorded_at, capture_schema=capture_schema)
        predictions["rows"].append(prediction)
        diagnostics.append(diagnostic)
    report = oe.evaluate(samples, predictions, annotations, catalog)
    cases = report.pop("cases")
    diagnostics_document = {"schema_version": provenance["schema_version"], "kind": "export_diagnostics", "status": "exported",
                            "receipt_authentication": "not independently authenticated", "freshness": "saved frozen-record coherence only",
                            "synthetic": provenance["synthetic"], "rows": diagnostics,
                            "dependency_record_kind": "actual_runtime_snapshot_not_production_lock" if capture_schema else "supplied_dependency_lock_record",
                            "missing_asset_ids": sorted(set(srows) - mapped),
                            "limitations": [line for line in LIMITATIONS if not capture_schema or not line.startswith("Only automatic")]
                                + (["V2 manual scopes require full captured confirmation and independent stages; automatic body scopes and historical cache reconstruction remain unsupported."] if capture_schema else []),
                            "real_world_accuracy": {"value": None, "status": "尚未测量"}}
    return {"predictions": predictions, "catalog": catalog, "annotations": annotations,
            "diagnostics": diagnostics_document, "summary": report, "cases": cases}


def completed_set_documents(directory):
    """Read only the four fixed local files; never follow receipt-provided paths."""
    directory = Path(directory).resolve()
    require(not any(".staging-" in part for part in directory.parts), "staging directories are not completed freezes")
    names = ("scan-session.json", "samples.json", "measurement-receipt.json")
    raw_marker = (directory / "complete.json").read_bytes()
    marker = parse_json(raw_marker)
    require(marker.get("schema_version") == "measurement-freeze-v1" and marker.get("status") == "complete"
            and isinstance(marker.get("files"), dict) and set(marker["files"]) == set(names),
            "invalid completed-set marker")
    raw = {name: (directory / name).read_bytes() for name in names}
    require(all(oe.digest(raw[name]) == marker["files"][name] for name in names), "completed-set file hash mismatch")
    documents = {key: parse_json(raw[name]) for key, name in zip(("session", "samples", "provenance"), names)}
    proof = {"session_sha256": oe.digest(raw["scan-session.json"]),
             "samples_sha256": oe.canonical_hash(documents["samples"]),
             "provenance_sha256": oe.canonical_hash(documents["provenance"])}
    require((directory / "complete.json").read_bytes() == raw_marker
            and all((directory / name).read_bytes() == raw[name] for name in names), "completed set changed while reading")
    return documents, proof


def load_completed_set(directory, *, repo=REPO):
    documents, proof = completed_set_documents(directory)
    return build_export(**documents, session_sha256=proof["session_sha256"], repo=repo, completion_proof=proof)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("session", "samples", "provenance"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new output directory outside repository")
    args = parser.parse_args(argv)
    output = None
    raw = {}
    try:
        output = oe.outside_repository(args.output)
        require(not output.exists(), "output directory already exists; refusing to overwrite")
        paths = {name: getattr(args, name) for name in ("session", "samples", "provenance")}
        # Parse exactly the bytes hashed; no data-supplied path is followed.
        raw = {name: path.read_bytes() for name, path in paths.items()}
        documents = {name: parse_json(value) for name, value in raw.items()}
        proof = None
        if documents["provenance"].get("schema_version") == CAPTURE_SCHEMA:
            directory = paths["provenance"].resolve().parent
            expected_names = {"session": "scan-session.json", "samples": "samples.json", "provenance": "measurement-receipt.json"}
            require(all(paths[key].resolve() == directory / name for key, name in expected_names.items()),
                    "v2 CLI inputs must be one completed frozen set")
            completed, proof = completed_set_documents(directory)
            require(completed == documents, "completed set changed before export")
        result = build_export(**documents, session_sha256=oe.digest(raw["session"]), completion_proof=proof)
        require(all(path.read_bytes() == raw[name] for name, path in paths.items()), "inputs changed during export")
        hashes = {name: oe.digest(value) for name, value in raw.items()}
        result["diagnostics"].update(input_sha256=hashes, generated_at=datetime.now(timezone.utc).isoformat(),
                                     tool_file_sha256=oe.digest(Path(__file__).read_bytes()),
                                     tool_git=oe.git_bytes(REPO, "rev-parse", "HEAD").decode().strip(),
                                     tool_git_dirty=bool(oe.git_bytes(REPO, "--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all")))
        result["summary"]["input_sha256"] = hashes
        output.mkdir(parents=True, exist_ok=False)
        for name in ("predictions", "catalog", "annotations", "diagnostics", "summary"):
            oe.write_new(output / (name + ".json"), result[name])
        with (output / "cases.jsonl").open("x", encoding="utf-8") as handle:
            for case in result["cases"]:
                handle.write(json.dumps(case, ensure_ascii=False, allow_nan=False) + "\n")
        print(f"Exported {len(result['predictions']['rows'])} saved mappings; real-world accuracy not measured")
        return 0
    except (ContractError, OSError, ValueError, TypeError, KeyError, StopIteration, subprocess.CalledProcessError) as exc:
        if output is not None and not output.exists():
            try:
                output.mkdir(parents=True, exist_ok=False)
                oe.write_new(output / "validation-errors.json", {"schema_version": PROVENANCE_SCHEMA,
                             "status": "invalid_inputs", "error": str(exc), "metrics": None,
                             "input_sha256": {name: oe.digest(value) for name, value in raw.items()}})
            except OSError:
                pass
        print(f"frozen prediction export: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
