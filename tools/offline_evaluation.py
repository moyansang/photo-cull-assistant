#!/usr/bin/env python3
"""Evaluate frozen local recommendations against separately authored human labels.

Standard library only. Never reads photographs, invokes models, or changes the
application, workspace, algorithm, thresholds, or persisted production formats.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

SCHEMA = "offline-evaluation-v1"
TECHNICAL = ("clear", "unusable", "borderline", "unjudgeable", "UNKNOWN", "UNLABELED")
DECISIONS = ("keep", "reject", "review", "failure", "missing")
CULLING = ("K", "D", "U", "X", "UNKNOWN", "UNLABELED")
SOURCE_FILES = tuple(f"src/ai_cull_assistant/{name}.py" for name in (
    "analysis_modes", "face_focus", "body_focus", "primary_quality",
    "screening", "focus_protocol", "body_pipeline", "subject",
))
SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]+\Z")
REPO = Path(__file__).resolve().parents[1]


class ContractError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical_hash(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8"))


def read_json(path):
    # Duplicate object keys are data loss too, even before row validation.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def require(condition, message):
    if not condition:
        raise ContractError(message)


def text_field(row, field, context):
    value = row.get(field)
    require(isinstance(value, str) and bool(value.strip()), f"{context}: missing/invalid {field}")
    return value


def hash_field(row, field, context):
    value = text_field(row, field, context)
    require(bool(SHA.fullmatch(value)), f"{context}: invalid SHA-256 {field}")
    return value


def validate_envelope(document, kind):
    require(isinstance(document, dict), f"{kind}: expected object")
    require(document.get("schema_version") == SCHEMA, f"{kind}: unsupported schema_version")
    require(document.get("kind") == kind, f"{kind}: wrong document kind")
    for field in ("dataset_id", "dataset_version", "guideline_version"):
        text_field(document, field, kind)
    require(isinstance(document.get("rows"), list), f"{kind}: rows must be an array")


def unique_rows(rows, kind):
    output = {}
    for i, row in enumerate(rows):
        require(isinstance(row, dict), f"{kind}[{i}]: expected object")
        key = text_field(row, "asset_id", f"{kind}[{i}]")
        require(key not in output, f"{kind}: duplicate asset_id {key}")
        output[key] = row
    return output


def reason_literals(source):
    """Conservative source inventory, not a new canonical production registry.

    Only inspect expressions assigned/passed as reason/reasons (including
    *_reason(s)). Dynamic codes not visible here remain explicitly unknown.
    """
    tree = ast.parse(source)
    expressions = []
    def reason_name(name):
        return name in {"reason", "reasons"} or name.endswith(("_reason", "_reasons"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            expressions.extend(v for k, v in zip(node.keys, node.values)
                               if isinstance(k, ast.Constant) and isinstance(k.value, str)
                               and reason_name(k.value))
        elif isinstance(node, ast.keyword) and node.arg and reason_name(node.arg):
            expressions.append(node.value)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and reason_name(t.id)
                   or isinstance(t, ast.Attribute) and reason_name(t.attr) for t in targets):
                expressions.append(node.value)
    return sorted({child.value for expression in expressions if expression is not None
                   for child in ast.walk(expression) if isinstance(child, ast.Constant)
                   and isinstance(child.value, str) and IDENTIFIER.fullmatch(child.value)
                   and child.value not in {"keep", "reject", "review", "reason", "reasons", "state", "recommendation"}})


def git_bytes(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    return result.stdout


def make_catalog(repo, source_ref):
    commit = git_bytes(repo, "rev-parse", "--verify", f"{source_ref}^{{commit}}").decode().strip()
    sources = {}
    codes = defaultdict(list)
    for filename in SOURCE_FILES:
        raw = git_bytes(repo, "show", f"{commit}:{filename}")
        sources[filename] = digest(raw)
        for code in reason_literals(raw.decode("utf-8-sig")):
            codes[code].append(filename)
    return {"schema_version": SCHEMA, "kind": "reason_catalog", "source_git": commit,
            "method": "static reason-expression literals; incomplete for dynamic codes",
            "sources": sources, "codes": dict(sorted(codes.items()))}


def validate_catalog(catalog, predictions):
    require(isinstance(catalog, dict) and catalog.get("schema_version") == SCHEMA
            and catalog.get("kind") == "reason_catalog", "invalid reason catalog")
    require(catalog.get("source_git") == predictions.get("source_git"), "reason catalog source_git mismatch")
    require(isinstance(catalog.get("codes"), dict), "catalog codes must be object")
    require(isinstance(catalog.get("sources"), dict) and bool(catalog["sources"]), "catalog sources required")
    for path, sha in catalog["sources"].items():
        require(path in SOURCE_FILES and isinstance(sha, str) and SHA.fullmatch(sha), "invalid catalog source")
    for code, paths in catalog["codes"].items():
        require(isinstance(code, str) and isinstance(paths, list) and bool(paths)
                and all(isinstance(p, str) and p in catalog["sources"] for p in paths), "invalid catalog code source")


def split_overlap(rows):
    """Check registered identities across sets; never invent a random split."""
    seen = defaultdict(lambda: defaultdict(set))
    for row in rows:
        split = text_field(row, "split", "split registry")
        identities = [(field, row.get(field)) for field in (
            "asset_id", "capture_id", "variant_family_id", "comparison_group_id", "scene_family_id",
            "session_id", "burst_id") if row.get(field)]
        identities.extend(("person_id", p) for p in row.get("person_ids", []))
        identities.extend(("source_sha256", s["sha256"]) for s in row.get("sources", []))
        for field, value in identities:
            require(isinstance(value, str) and value, f"split registry: invalid {field}")
            seen[(field, value)][split].add(row["asset_id"])
    return [{"field": field, "value": value, "splits": sorted(splits),
             "asset_ids": sorted({asset for ids in splits.values() for asset in ids})}
            for (field, value), splits in sorted(seen.items()) if len(splits) > 1]


def validate_inputs(samples, predictions, annotations, catalog):
    for document, kind in ((samples, "samples"), (predictions, "predictions"), (annotations, "annotations")):
        validate_envelope(document, kind)
    for document in (predictions, annotations):
        require(document.get("manifest_sha256") == canonical_hash(samples), "manifest_sha256 mismatch")
    for field in ("dataset_id", "dataset_version", "guideline_version"):
        require(samples[field] == predictions[field] == annotations[field], f"{field} mismatch")
    for document, field in ((samples, "manifest_version"), (predictions, "prediction_version"),
                            (annotations, "annotation_version")):
        text_field(document, field, field)
    require(predictions.get("manifest_version") == samples["manifest_version"], "prediction manifest_version mismatch")
    require(annotations.get("manifest_version") == samples["manifest_version"], "annotation manifest_version mismatch")
    require(isinstance(predictions.get("source_git"), str) and COMMIT.fullmatch(predictions["source_git"]),
            "predictions: full source_git required")
    for field in ("dependency_lock_sha256", "configuration_sha256", "decode_configuration_sha256"):
        hash_field(predictions, field, "predictions provenance")
    text_field(predictions, "run_id", "predictions")
    require(samples.get("evaluation_kind") in ("synthetic", "development", "locked_regression", "independent_holdout"),
            "samples: invalid evaluation_kind")
    validate_catalog(catalog, predictions)
    srows = unique_rows(samples["rows"], "samples")
    prows = unique_rows(predictions["rows"], "predictions")
    arows = unique_rows(annotations["rows"], "annotations")
    require(not (prows.keys() - srows.keys()), "predictions: unmatched asset_id")
    require(not (arows.keys() - srows.keys()), "annotations: unmatched asset_id")
    captures, source_ids, hashes = set(), set(), set()
    for asset_id, sample in srows.items():
        for field in ("sample_version", "capture_id", "variant_family_id", "comparison_group_id",
                      "scene_family_id", "split", "source_authorization", "historical_exposure", "evaluation_source_id"):
            text_field(sample, field, asset_id)
        require(sample["capture_id"] not in captures, f"duplicate capture_id {sample['capture_id']}: combine RAW+JPG sources")
        captures.add(sample["capture_id"])
        for field in ("session_id", "burst_id"):
            require(sample.get(field) is None or isinstance(sample[field], str) and bool(sample[field].strip()),
                    f"{asset_id}: {field} must be nonempty string or null")
        require(isinstance(sample.get("person_ids"), list) and all(isinstance(x, str) and x for x in sample["person_ids"]),
                f"{asset_id}: person_ids array required (empty means none registered, not no people)")
        require(sample.get("scene_type") in ("portrait", "landscape", "mixed", "unknown"), f"{asset_id}: explicit scene_type required")
        require(isinstance(sample.get("sources"), list) and bool(sample["sources"]), f"{asset_id}: sources required")
        local_sources = {}
        for source in sample["sources"]:
            require(isinstance(source, dict), f"{asset_id}: invalid source")
            sid = text_field(source, "source_id", asset_id)
            sha = hash_field(source, "sha256", asset_id)
            text_field(source, "format", asset_id)
            require(sid not in source_ids, f"duplicate source_id {sid}")
            require(sha not in hashes, f"duplicate source bytes {sid}: register one statistical unit")
            source_ids.add(sid); hashes.add(sha); local_sources[sid] = sha
        require(sample["evaluation_source_id"] in local_sources, f"{asset_id}: evaluation_source_id absent from sources")
        for kind, row in (("prediction", prows.get(asset_id)), ("annotation", arows.get(asset_id))):
            if row is None:
                continue
            require(row.get("sample_version") == sample["sample_version"], f"{asset_id}: {kind} sample_version mismatch")
            sid = row.get("evaluated_source_id")
            require(sid == sample["evaluation_source_id"] and row.get("input_sha256") == local_sources.get(sid),
                    f"{asset_id}: {kind} source/hash mismatch")
        if asset_id in prows:
            prediction = prows[asset_id]
            require(prediction.get("status") in ("ok", "failed", "skipped"), f"{asset_id}: invalid run status")
            require(isinstance(prediction.get("reasons"), list) and all(isinstance(r, str) and r for r in prediction["reasons"]),
                    f"{asset_id}: reasons must be an array of raw nonempty strings")
            if prediction["status"] != "ok":
                text_field(prediction, "failure_reason", asset_id)
        require(isinstance(sample.get("exception_tags", []), list)
                and all(isinstance(x, str) and x for x in sample.get("exception_tags", [])), f"{asset_id}: invalid exception_tags")
        if asset_id in arows:
            annotation = arows[asset_id]
            require(annotation.get("label_source") == "human", f"{asset_id}: technical truth must be human-authored")
            text_field(annotation, "annotator_id", asset_id)
            for field in ("technical_label", "culling_label", "final_selection", "evidence_adequacy"):
                require(field in annotation, f"{asset_id}: annotation missing {field}")
            for field in ("technical_label", "culling_label"):
                require(annotation[field] is None or isinstance(annotation[field], str), f"{asset_id}: {field} must be string or null")
            require(annotation.get("final_selection") in ("keep", "reject", "undecided", None), f"{asset_id}: invalid final_selection")
            require(annotation["evidence_adequacy"] in ("adequate", "insufficient", "unknown", None), f"{asset_id}: invalid evidence_adequacy")
            require(isinstance(annotation.get("regions"), list), f"{asset_id}: regions array required")
            for region in annotation["regions"]:
                require(isinstance(region, dict), f"{asset_id}: invalid region")
                for field in ("region_id", "role", "visibility", "technical_label", "evidence_adequacy"):
                    text_field(region, field, f"{asset_id} region")
            if annotation.get("culling_label") == "X":
                text_field(annotation, "exclusion_reason", asset_id)
    registry = samples.get("split_registry", [])
    require(isinstance(registry, list), "split_registry must be array")
    for row in registry:
        require(isinstance(row, dict), "split_registry: row must be object")
        for field in ("asset_id", "capture_id", "variant_family_id", "comparison_group_id", "scene_family_id", "split"):
            text_field(row, field, "split_registry")
        for field in ("session_id", "burst_id"):
            require(row.get(field) is None or isinstance(row[field], str) and bool(row[field].strip()),
                    f"split_registry: {field} must be nonempty string or null")
        require(isinstance(row.get("person_ids", []), list)
                and all(isinstance(x, str) and x for x in row.get("person_ids", [])), "split_registry person_ids must be array of IDs")
        require(isinstance(row.get("sources", []), list), "split_registry sources must be array")
        for source in row.get("sources", []):
            require(isinstance(source, dict), "split_registry: invalid source")
            hash_field(source, "sha256", "split_registry")
    overlaps = split_overlap([*srows.values(), *registry])
    declarations = samples.get("declared_overlaps", [])
    require(isinstance(declarations, list), "declared_overlaps must be array")
    declared = {}
    for entry in declarations:
        require(isinstance(entry, dict), "declared_overlaps: expected object")
        for field in ("field", "value", "reason"):
            text_field(entry, field, "declared_overlaps")
        require(isinstance(entry.get("splits"), list) and len(entry["splits"]) >= 2
                and all(isinstance(x, str) and x for x in entry["splits"]), "invalid declared overlap splits")
        key = (entry["field"], entry["value"], tuple(sorted(set(entry["splits"]))))
        require(key not in declared, "duplicate declared overlap")
        declared[key] = entry
    for overlap in overlaps:
        key = (overlap["field"], overlap["value"], tuple(overlap["splits"]))
        require(key in declared, "undeclared cross-split overlap: " + json.dumps(overlap, ensure_ascii=False))
        overlap["declaration_reason"] = declared[key]["reason"]
    require(not (set(declared) - {(o["field"], o["value"], tuple(o["splits"])) for o in overlaps}),
            "declared overlap not found in registered metadata")
    return srows, prows, arows, overlaps


def label(value, vocabulary):
    return value if value in vocabulary else "UNLABELED" if value is None or value == "" else "UNKNOWN"


def ratio(numerator, denominator, population):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None,
            "population": population}


def summarize(rows):
    n = len(rows)
    matrix = {t: {d: 0 for d in DECISIONS} for t in TECHNICAL}
    culling_matrix = {t: {d: 0 for d in DECISIONS} for t in CULLING}
    for row in rows:
        matrix[row["technical_label"]][row["decision"]] += 1
        culling_matrix[row["culling_label"]][row["decision"]] += 1
    counts = Counter(row["decision"] for row in rows)
    truth = Counter(row["technical_label"] for row in rows)
    cull = Counter(row["culling_label"] for row in rows)
    keep, reject = counts["keep"], counts["reject"]
    eligible = cull["K"] + cull["D"] + cull["U"]
    rK, rD, rU = (culling_matrix[t]["reject"] for t in ("K", "D", "U"))
    eligible_reject = rK + rD + rU
    metrics = {
        "clear_false_reject_rate": ratio(matrix["clear"]["reject"], truth["clear"], "all human technical clear exposures, including failed/missing predictions"),
        "unusable_false_keep_rate": ratio(matrix["unusable"]["keep"], truth["unusable"], "all human technical unusable exposures, including failed/missing predictions"),
        "unusable_among_keep_rate": ratio(matrix["unusable"]["keep"], keep, "all predicted keep exposures, including unknown/unlabeled truth"),
        "clear_among_reject_rate": ratio(matrix["clear"]["reject"], reject, "all predicted reject exposures, including unknown/unlabeled truth"),
        "automatic_decision_coverage": ratio(keep + reject, n, "all manifest exposures"),
        "review_rate": ratio(counts["review"], n, "all manifest exposures"),
        "runtime_failure_rate": ratio(counts["failure"], n, "all manifest exposures; failed/skipped/invalid decision"),
        "missing_prediction_rate": ratio(counts["missing"], n, "all manifest exposures"),
        "human_handling_burden_rate": ratio(counts["review"] + counts["failure"] + counts["missing"], n, "all manifest exposures"),
        "human_technical_label_coverage": ratio(n-truth["UNLABELED"]-truth["UNKNOWN"], n, "all manifest exposures"),
        "keep_truth_known_coverage": ratio(sum(matrix[t]["keep"] for t in TECHNICAL[:4]), keep, "all predicted keep exposures"),
        "reject_truth_known_coverage": ratio(sum(matrix[t]["reject"] for t in TECHNICAL[:4]), reject, "all predicted reject exposures"),
        "K_false_reject_rate": ratio(rK, cull["K"], "human K exposures"),
        "D_false_keep_rate": ratio(culling_matrix["D"]["keep"], cull["D"], "human D exposures"),
        "unsafe_reject_rate_KU": ratio(rK+rU, eligible_reject, "predicted reject among human K/D/U only; X/unknown/unlabeled separately visible"),
        "safe_reject_precision_D": ratio(rD, eligible_reject, "predicted reject among human K/D/U only"),
        "reject_coverage_KDU": ratio(eligible_reject, eligible, "human K/D/U exposures"),
        "D_recall": ratio(rD, cull["D"], "human D exposures"),
    }
    attribution = defaultdict(Counter)
    for row in rows:
        for category in row["attributions"]:
            for reason in set(row["reasons_raw"]):
                attribution[category][reason] += 1
    return {"n": n, "technical_matrix": matrix, "culling_matrix": culling_matrix,
            "decision_counts": {d: counts[d] for d in DECISIONS}, "metrics": metrics,
            "run_status_counts": {status: sum(row["run_status"] == status for row in rows)
                                  for status in ("ok", "failed", "skipped", "missing")},
            "run_status_denominator": n,
            "attribution_counts": dict(sorted(Counter(a for row in rows for a in row["attributions"]).items())),
            "attribution_reason_exposure_counts": {k: dict(sorted(v.items())) for k, v in sorted(attribution.items())},
            "reason_occurrences": dict(sorted(Counter(r for row in rows for r in row["reasons_raw"]).items())),
            "reason_exposure_counts": dict(sorted(Counter(r for row in rows for r in set(row["reasons_raw"])).items())),
            "reason_distribution_denominator": n,
            "unknown_reason_exposure_counts": dict(sorted(Counter(r for row in rows for r in set(row["unknown_reasons"])).items())),
            "failure_reason_counts": dict(sorted(Counter(row["failure_reason"] for row in rows if row["decision"] == "failure").items())),
            "warnings": dict(sorted(Counter(w for row in rows for w in row["warnings"]).items()))}


def evaluate(samples, predictions, annotations, catalog):
    srows, prows, arows, overlaps = validate_inputs(samples, predictions, annotations, catalog)
    rows = []
    for asset_id, sample in srows.items():
        prediction, annotation = prows.get(asset_id), arows.get(asset_id)
        warnings = []
        if prediction is None:
            decision, failure = "missing", None
            warnings.append("missing_prediction")
        elif prediction["status"] != "ok":
            decision, failure = "failure", prediction["failure_reason"]
        elif prediction.get("recommendation") not in DECISIONS[:3]:
            decision, failure = "failure", "invalid_recommendation"
            warnings.append("invalid_recommendation")
        else:
            decision, failure = prediction["recommendation"], None
        technical = label(annotation.get("technical_label") if annotation else None, TECHNICAL[:4])
        culling = label(annotation.get("culling_label") if annotation else None, CULLING[:4])
        if technical in {"UNLABELED", "UNKNOWN"}:
            warnings.append("technical_" + technical.lower())
        if culling in {"UNLABELED", "UNKNOWN"}:
            warnings.append("culling_" + culling.lower())
        reasons = prediction["reasons"] if prediction else []
        unknown = [r for r in reasons if r not in catalog["codes"]]
        if unknown:
            warnings.append("unknown_reason_code")
        attributions = []
        if technical == "clear" and decision == "reject":
            attributions.append("clear_false_reject")
        if technical == "unusable" and decision == "keep":
            attributions.append("unusable_false_keep")
        if decision in {"review", "failure", "missing"}:
            attributions.append({"review": "needs_review", "failure": "runtime_failure", "missing": "missing_prediction"}[decision])
        if technical in {"UNKNOWN", "UNLABELED"}:
            attributions.append("technical_truth_unavailable")
        rows.append({"asset_id": asset_id, "capture_id": sample["capture_id"], "split": sample["split"],
                     "scene_type": sample["scene_type"], "scene_family_id": sample["scene_family_id"],
                     "comparison_group_id": sample["comparison_group_id"],
                     "source_format": next(s["format"] for s in sample["sources"] if s["source_id"] == sample["evaluation_source_id"]),
                     "technical_label": technical, "culling_label": culling,
                     "evidence_adequacy": (annotation.get("evidence_adequacy") or "unknown") if annotation else "unknown",
                     "historical_exposure": sample["historical_exposure"],
                     "annotation_raw": annotation, "prediction_raw": prediction,
                     "decision": decision, "run_status": prediction["status"] if prediction else "missing", "failure_reason": failure,
                     "reasons_raw": reasons, "unknown_reasons": unknown,
                     "final_selection": annotation.get("final_selection") if annotation else None,
                     "ai_temporary_labels": sample.get("ai_temporary_labels"),
                     "exception_tags": sample.get("exception_tags", []), "attributions": attributions, "warnings": warnings})
    strata = {}
    for field in ("split", "scene_type", "scene_family_id", "comparison_group_id", "source_format", "evidence_adequacy", "historical_exposure"):
        groups = defaultdict(list)
        for row in rows:
            groups[row[field]].append(row)
        strata[field] = {key: summarize(value) for key, value in sorted(groups.items())}
    exceptions = defaultdict(list)
    for row in rows:
        for tag in set(row["exception_tags"]):
            exceptions[tag].append(row)
    strata["exception_tags"] = {tag: summarize(group) for tag, group in sorted(exceptions.items())}
    macro = {}
    for metric in summarize(rows)["metrics"]:
        values = [group["metrics"][metric]["value"] for group in strata["scene_family_id"].values()
                  if group["metrics"][metric]["value"] is not None]
        macro[metric] = {"value": sum(values)/len(values) if values else None,
                         "defined_scene_families": len(values),
                         "all_scene_families": len(strata["scene_family_id"]),
                         "method": "unweighted mean of defined scene-family rates; no confidence interval"}
    return {"schema_version": SCHEMA, "kind": "evaluation_report", "status": "descriptive_only",
            "dataset_id": samples["dataset_id"], "dataset_version": samples["dataset_version"],
            "evaluation_kind": samples["evaluation_kind"],
            "real_world_accuracy": {"value": None, "status": "尚未测量" if samples["evaluation_kind"] == "synthetic" or not any(r["technical_label"] in TECHNICAL[:2] for r in rows) else "supplied human labels: risk metrics only; no general accuracy claim"},
            "split_integrity": {"registered_overlaps": overlaps, "independent_blind_test": False,
                                "status": "non-independent: declared overlaps" if overlaps else "no registered overlap; independence not established by metadata checks"},
            "acceptance": "not assessed; thresholds, independence, authorized real data and Windows validation are separate",
            "manifest_version": samples["manifest_version"], "annotation_version": annotations["annotation_version"],
            "prediction_version": predictions["prediction_version"], "prediction_source_git": predictions["source_git"],
            "reason_catalog_method": catalog.get("method"), "overall": summarize(rows),
            "strata": strata, "scene_family_macro": macro, "cases": rows,
            "limitations": ["No image bytes read; hashes and capture identities are registered metadata, not independently verified.",
                            "No confidence intervals: clustered inference requires a predeclared method and sufficient independent groups.",
                            "No group/identity/ranking accuracy claimed. No-face predictions never imply landscape.",
                            "Unknown and absent labels never become human technical truth; final choices and AI temporary labels are separate."]}


def synthetic_documents(catalog):
    """Toy metadata for a reproducible contract demonstration; never accuracy data."""
    common = {"schema_version": SCHEMA, "dataset_id": "SYNTHETIC-CONTRACT-DEMO",
              "dataset_version": "demo-v1", "guideline_version": "demo-v1", "manifest_version": "demo-v1"}
    samples = dict(common, kind="samples", evaluation_kind="synthetic", rows=[], split_registry=[], declared_overlaps=[])
    predictions = dict(common, kind="predictions", prediction_version="demo-v1", source_git=catalog["source_git"],
                       run_id="synthetic-contract-demo", dependency_lock_sha256=digest(b"SYNTHETIC NOT A DEPENDENCY LOCK"),
                       configuration_sha256=digest(b"SYNTHETIC NO ALGORITHM"),
                       decode_configuration_sha256=digest(b"SYNTHETIC NO DECODING"),
                       provenance_note="All rows and hashes are invented metadata; no photos, models or decoding used.", rows=[])
    annotations = dict(common, kind="annotations", annotation_version="demo-v1", rows=[])
    truths = ["clear", "unusable", "clear", "unusable", "unjudgeable", "future_label", None, "borderline"]
    decisions = ["reject", "keep", "review", "failure", "missing", "keep", "reject", "review"]
    for i, (truth, decision) in enumerate(zip(truths, decisions)):
        asset_id, source_id = f"synthetic-{i}", f"synthetic-source-{i}"
        sha = digest(source_id.encode())
        sample = {"asset_id": asset_id, "sample_version": "demo-v1", "capture_id": f"capture-{i}",
                  "variant_family_id": f"variant-{i}", "comparison_group_id": f"group-{i//2}",
                  "scene_family_id": f"scene-{i//2}", "split": "synthetic-development", "person_ids": [],
                  "scene_type": "unknown", "source_authorization": "invented metadata, no image files",
                  "historical_exposure": "synthetic", "evaluation_source_id": source_id,
                  "sources": [{"source_id": source_id, "sha256": sha, "format": "JPG"}],
                  "exception_tags": ["must_select"] if i == 0 else [],
                  "ai_temporary_labels": {"technical_label": "clear", "source": "synthetic AI label; ignored"} if i == 6 else None}
        if i == 0:
            sample["sources"].append({"source_id": "synthetic-raw-pair", "sha256": digest(b"invented RAW bytes"), "format": "RAW"})
        samples["rows"].append(sample)
        binding = {"asset_id": asset_id, "sample_version": "demo-v1", "evaluated_source_id": source_id, "input_sha256": sha}
        if decision != "missing":
            predictions["rows"].append(dict(binding, status="failed" if decision == "failure" else "ok",
                  recommendation=None if decision == "failure" else decision,
                  failure_reason="synthetic_decode_failure" if decision == "failure" else None,
                  reasons=["synthetic_future_reason_v99"] if i == 5 else ["source_unreadable_for_analysis"] if decision == "failure" else [],
                  face_found=False))
        if truth is not None:
            annotations["rows"].append(dict(binding, label_source="human", annotator_id="SYNTHETIC-RATER-NOT-A-PERSON",
                  technical_label=truth, culling_label={"clear": "K", "unusable": "D", "unjudgeable": "U", "borderline": "U"}.get(truth, "X"),
                  exclusion_reason="synthetic unknown-label case" if i == 5 else None,
                  final_selection="keep" if i in (0, 1) else "undecided", evidence_adequacy="unknown", regions=[]))
    for document in (predictions, annotations):
        document["manifest_sha256"] = canonical_hash(samples)
    return {"samples": samples, "predictions": predictions, "annotations": annotations, "catalog": catalog}


def outside_repository(path):
    resolved = path.resolve()
    require(not resolved.is_relative_to(REPO.resolve()), "generated outputs must be outside the source repository")
    return resolved


def write_new(path, value):
    outside_repository(path)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    cat = commands.add_parser("catalog", help="read reason literals from an exact local Git commit")
    cat.add_argument("--source-ref", required=True)
    cat.add_argument("--output", type=Path, required=True)
    demo = commands.add_parser("synthetic-demo", help="generate invented metadata and a contract-only report")
    demo.add_argument("--output", type=Path, required=True, help="new directory outside repository")
    run = commands.add_parser("evaluate", help="join frozen metadata only; no model/photograph access")
    for name in ("samples", "predictions", "annotations", "catalog"):
        run.add_argument("--" + name, type=Path, required=True)
    run.add_argument("--output", type=Path, required=True, help="new directory outside repository")
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            write_new(args.output, make_catalog(REPO, args.source_ref))
            return 0
        if args.command == "synthetic-demo":
            output = outside_repository(args.output)
            require(not output.exists(), "output directory already exists; refusing to overwrite")
            documents = synthetic_documents(make_catalog(REPO, "HEAD"))
            output.mkdir(parents=True)
            parameters = []
            for name, document in documents.items():
                path = output / (name + ".json")
                write_new(path, document)
                parameters.extend(["--" + name, str(path)])
            return main(["evaluate", *parameters, "--output", str(output / "report")])
        output = outside_repository(args.output)
        require(not output.exists(), "output directory already exists; refusing to overwrite")
        paths = {name: getattr(args, name) for name in ("samples", "predictions", "annotations", "catalog")}
        raw = {name: path.read_bytes() for name, path in paths.items()}
        # Parse exactly the bytes hashed in this run, with duplicate-key detection.
        documents = {name: read_json(path) for name, path in paths.items()}
        require(all(path.read_bytes() == raw[name] for name, path in paths.items()), "inputs changed while reading")
        try:
            report = evaluate(**documents)
        except ContractError as exc:
            output.mkdir(parents=True)
            write_new(output / "validation-errors.json", {"schema_version": SCHEMA, "status": "invalid_inputs",
                      "error": str(exc), "metrics": None, "input_sha256": {n: digest(b) for n, b in raw.items()}})
            print(str(exc), file=sys.stderr)
            return 2
        report["input_sha256"] = {name: digest(value) for name, value in raw.items()}
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        report["tool_git"] = git_bytes(REPO, "rev-parse", "HEAD").decode().strip()
        report["tool_file_sha256"] = digest(Path(__file__).read_bytes())
        report["tool_git_tree"] = git_bytes(REPO, "rev-parse", "HEAD^{tree}").decode().strip()
        report["tool_git_dirty"] = bool(git_bytes(REPO, "status", "--porcelain=v1", "--untracked-files=all"))
        require(all(path.read_bytes() == raw[name] for name, path in paths.items()), "inputs changed during evaluation")
        output.mkdir(parents=True)
        cases = report.pop("cases")
        write_new(output / "summary.json", report)
        with (output / "cases.jsonl").open("x", encoding="utf-8") as handle:
            for row in cases:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        print(f"Evaluated {len(cases)} exposures; {report['real_world_accuracy']['status']}; outputs: summary.json, cases.jsonl")
        return 0
    except (ContractError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"offline evaluation: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
