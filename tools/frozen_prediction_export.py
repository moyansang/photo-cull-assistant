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
    for name in ("dependency_lock", "configuration", "decode_configuration"):
        record = records.get(name)
        require(isinstance(record, dict), f"receipt: missing {name} record")
        content = oe.text_field(record, "content", name)
        supplied = sha(record.get("sha256"), name)
        require(oe.digest(content.encode("utf-8")) == supplied, f"receipt: {name} record hash mismatch")
        hashes[name + "_sha256"] = supplied
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


def map_row(mapping, sample, asset, session, versions, recorded_at):
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
        decode = evidence.get("decode")
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
            recommendation = check_portrait(asset, evidence, inputs, versions)
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
        result.update(status="ok", recommendation=recommendation)
    except ContractError as exc:
        result["failure_reason"] = str(exc)
        diagnostic["failure_reason"] = str(exc)
    diagnostic["status"] = result["status"]
    return result, diagnostic


def build_export(session, samples, provenance, *, session_sha256, repo=REPO):
    """Produce evaluator documents from a supplied immutable receipt.

    Document/source bindings are fatal on mismatch; unsupported saved evidence
    becomes an explicit failed prediction. Unmapped samples remain missing.
    """
    require(isinstance(session, dict) and type(session.get("version")) is int and session["version"] == 1,
            "unsupported scan-session version")
    require(isinstance(session.get("assets"), list) and all(isinstance(a, dict) for a in session["assets"]), "session assets array required")
    require(isinstance(provenance, dict) and provenance.get("schema_version") == PROVENANCE_SCHEMA
            and provenance.get("kind") == "measurement_time_receipt", "external measurement-time provenance receipt required")
    require(type(provenance.get("synthetic")) is bool, "receipt synthetic flag required")
    require(not provenance["synthetic"] or samples.get("evaluation_kind") == "synthetic", "synthetic receipt cannot support real evaluation")
    require(provenance.get("session_sha256") == sha(session_sha256, "session bytes"), "receipt frozen session byte hash mismatch")
    require(provenance.get("manifest_sha256") == oe.canonical_hash(samples), "receipt sample manifest binding mismatch")
    recorded_at = timestamp(provenance.get("recorded_at"), "receipt recorded_at")
    hashes = receipt_records(provenance)
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
        prediction, diagnostic = map_row(mapping, sample, asset, session, versions, recorded_at)
        predictions["rows"].append(prediction)
        diagnostics.append(diagnostic)
    report = oe.evaluate(samples, predictions, annotations, catalog)
    cases = report.pop("cases")
    diagnostics_document = {"schema_version": PROVENANCE_SCHEMA, "kind": "export_diagnostics", "status": "exported",
                            "receipt_authentication": "not independently authenticated", "freshness": "saved frozen-record coherence only",
                            "synthetic": provenance["synthetic"], "rows": diagnostics,
                            "missing_asset_ids": sorted(set(srows) - mapped), "limitations": LIMITATIONS,
                            "real_world_accuracy": {"value": None, "status": "尚未测量"}}
    return {"predictions": predictions, "catalog": catalog, "annotations": annotations,
            "diagnostics": diagnostics_document, "summary": report, "cases": cases}


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
        result = build_export(**documents, session_sha256=oe.digest(raw["session"]))
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
