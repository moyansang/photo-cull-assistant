"""Synthetic statistical contracts; these are not photo-accuracy measurements."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

PATH = Path(__file__).resolve().parents[1] / "tools" / "offline_evaluation.py"
spec = importlib.util.spec_from_file_location("offline_evaluation", PATH)
oe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oe)


def envelopes(n=1):
    common = dict(schema_version=oe.SCHEMA, dataset_id="synthetic-only", dataset_version="v1", guideline_version="v1", manifest_version="v1")
    samples = dict(common, kind="samples", evaluation_kind="synthetic", rows=[], split_registry=[], declared_overlaps=[])
    predictions = dict(common, kind="predictions", prediction_version="v1", source_git="a"*40,
                       dependency_lock_sha256="b"*64, configuration_sha256="c"*64,
                       decode_configuration_sha256="d"*64, run_id="synthetic-run", rows=[])
    annotations = dict(common, kind="annotations", annotation_version="v1", rows=[])
    catalog = dict(schema_version=oe.SCHEMA, kind="reason_catalog", source_git="a"*40,
                   sources={oe.SOURCE_FILES[0]: "e"*64}, codes={"known_reason": [oe.SOURCE_FILES[0]]})
    for i in range(n):
        ident = f"asset-{i}"
        sample = dict(asset_id=ident, sample_version="v1", capture_id=f"capture-{i}", variant_family_id=f"variant-{i}",
                      comparison_group_id=f"comparison-{i}", scene_family_id=f"scene-{i}", split="development",
                      person_ids=[], scene_type="unknown", evaluation_source_id=f"source-{i}", source_authorization="synthetic metadata only",
                      historical_exposure="synthetic", exception_tags=[],
                      sources=[dict(source_id=f"source-{i}", sha256=f"{i+1:064x}", format="JPG")])
        binding = dict(asset_id=ident, sample_version="v1", evaluated_source_id=f"source-{i}", input_sha256=f"{i+1:064x}")
        samples["rows"].append(sample)
        predictions["rows"].append(dict(binding, recommendation="keep", status="ok", reasons=["known_reason"], face_found=False))
        annotations["rows"].append(dict(binding, technical_label="clear", culling_label="K", final_selection="keep",
                                        label_source="human", annotator_id="synthetic-rater", evidence_adequacy="adequate", regions=[]))
    return bind([samples, predictions, annotations, catalog])


def bind(documents):
    samples, predictions, annotations, _ = documents
    for doc in (predictions, annotations):
        doc["manifest_sha256"] = oe.canonical_hash(samples)
    return documents


def run(documents):
    return oe.evaluate(*bind(documents))


def metric(report, name):
    return report["overall"]["metrics"][name]


def test_zero_denominators_are_null_not_zero():
    report = run(envelopes(0))
    assert report["overall"]["n"] == 0
    assert all(m["denominator"] == 0 and m["value"] is None for m in report["overall"]["metrics"].values())
    assert report["real_world_accuracy"] == {"value": None, "status": "尚未测量"}


def test_all_review_cannot_appear_successful():
    docs = envelopes(3)
    for p in docs[1]["rows"]:
        p["recommendation"] = "review"
    r = run(docs)
    assert metric(r, "review_rate")["value"] == 1
    assert metric(r, "automatic_decision_coverage")["value"] == 0
    assert metric(r, "unusable_among_keep_rate")["value"] is None
    assert r["overall"]["technical_matrix"]["clear"]["review"] == 3


def test_no_labels_never_turns_human_keep_or_ai_into_truth():
    docs = envelopes(2)
    docs[2]["rows"] = []
    docs[0]["rows"][0]["ai_temporary_labels"] = {"technical_label": "clear", "final_selection": "keep"}
    r = run(docs)
    assert metric(r, "clear_false_reject_rate")["denominator"] == 0
    assert r["overall"]["technical_matrix"]["UNLABELED"]["keep"] == 2
    assert r["real_world_accuracy"]["value"] is None
    assert r["real_world_accuracy"]["status"] == "尚未测量"
    assert r["cases"][0]["ai_temporary_labels"]["technical_label"] == "clear"


def test_final_keep_is_not_technical_clear():
    docs = envelopes(1)
    docs[2]["rows"][0].update(technical_label="unusable", culling_label="D", final_selection="keep")
    r = run(docs)
    assert metric(r, "clear_false_reject_rate")["denominator"] == 0
    assert metric(r, "unusable_false_keep_rate")["value"] == 1


def test_complete_matrix_retains_missing_failed_unknown_and_excluded():
    docs = envelopes(5)
    docs[1]["rows"][0]["recommendation"] = "reject"
    docs[1]["rows"][1].update(status="failed", failure_reason="decode_failed", recommendation="keep")
    docs[1]["rows"].pop(2)
    docs[2]["rows"][3].update(technical_label="invented_label", culling_label="X", exclusion_reason="outside scope")
    docs[2]["rows"].pop(4)
    r = run(docs)
    assert r["overall"]["n"] == 5
    assert sum(sum(row.values()) for row in r["overall"]["technical_matrix"].values()) == 5
    assert sum(sum(row.values()) for row in r["overall"]["culling_matrix"].values()) == 5
    assert metric(r, "runtime_failure_rate")["value"] == 1/5
    assert metric(r, "missing_prediction_rate")["value"] == 1/5
    assert metric(r, "clear_false_reject_rate")["numerator"] == 1
    assert metric(r, "clear_false_reject_rate")["denominator"] == 3
    assert r["cases"][3]["annotation_raw"]["technical_label"] == "invented_label"


@pytest.mark.parametrize("document", [0, 1, 2])
def test_duplicate_assets_are_invalid(document):
    docs = envelopes()
    docs[document]["rows"].append(deepcopy(docs[document]["rows"][0]))
    with pytest.raises(oe.ContractError, match="duplicate asset_id"):
        run(docs)


def test_missing_prediction_is_distinct_from_review_and_runtime_failure():
    docs = envelopes()
    docs[1]["rows"] = []
    r = run(docs)
    assert r["cases"][0]["decision"] == "missing"
    assert metric(r, "runtime_failure_rate")["numerator"] == 0
    assert metric(r, "review_rate")["numerator"] == 0
    assert metric(r, "human_handling_burden_rate")["value"] == 1


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_failed_run_never_counts_cached_recommendation_as_automatic(status):
    docs = envelopes()
    docs[1]["rows"][0].update(status=status, failure_reason="source_unreadable_for_analysis", recommendation="reject")
    r = run(docs)
    assert metric(r, "automatic_decision_coverage")["numerator"] == 0
    assert metric(r, "clear_false_reject_rate")["numerator"] == 0
    assert r["overall"]["failure_reason_counts"] == {"source_unreadable_for_analysis": 1}


def test_unknown_reason_retains_raw_and_duplicate_occurrences():
    docs = envelopes()
    docs[1]["rows"][0]["reasons"] = ["future.dynamic(code)", "future.dynamic(code)", "known_reason"]
    r = run(docs)
    assert r["cases"][0]["reasons_raw"] == docs[1]["rows"][0]["reasons"]
    assert r["overall"]["reason_occurrences"]["future.dynamic(code)"] == 2
    assert r["overall"]["unknown_reason_exposure_counts"] == {"future.dynamic(code)": 1}


@pytest.mark.parametrize("field", ["asset_id", "capture_id", "variant_family_id", "comparison_group_id", "scene_family_id", "session_id", "burst_id"])
def test_undeclared_registered_cross_split_overlap_rejected(field):
    docs = envelopes()
    value = docs[0]["rows"][0].setdefault(field, "shared")
    other = deepcopy(docs[0]["rows"][0])
    other.update(asset_id="other", capture_id="other-capture", variant_family_id="other-variant", comparison_group_id="other-comparison", scene_family_id="other-scene", sources=[], split="test")
    other[field] = value
    docs[0]["split_registry"] = [other]
    with pytest.raises(oe.ContractError, match="undeclared cross-split overlap"):
        run(docs)


def test_declared_overlap_is_reported_as_non_independent():
    docs = envelopes()
    other = dict(asset_id="other", capture_id="other-capture", variant_family_id="other-variant", comparison_group_id="other-comparison", scene_family_id="scene-0", person_ids=[], split="test")
    docs[0]["split_registry"] = [other]
    docs[0]["declared_overlaps"] = [dict(field="scene_family_id", value="scene-0", splits=["development", "test"], reason="historical contamination; regression only")]
    r = run(docs)
    assert not r["split_integrity"]["independent_blind_test"]
    assert r["split_integrity"]["status"] == "non-independent: declared overlaps"
    assert len(r["split_integrity"]["registered_overlaps"]) == 1


@pytest.mark.parametrize("identity", ["person", "hash"])
def test_person_or_byte_identity_cross_split_overlap(identity):
    docs = envelopes()
    other = dict(asset_id="other", capture_id="other-capture", variant_family_id="other-variant", comparison_group_id="other-comparison", scene_family_id="other-scene", person_ids=[], split="test")
    if identity == "person":
        docs[0]["rows"][0]["person_ids"] = other["person_ids"] = ["person-1"]
    else:
        other["sources"] = [{"sha256": docs[0]["rows"][0]["sources"][0]["sha256"]}]
    docs[0]["split_registry"] = [other]
    with pytest.raises(oe.ContractError, match="undeclared cross-split overlap"):
        run(docs)


def test_raw_jpg_one_exposure_one_denominator():
    docs = envelopes()
    docs[0]["rows"][0]["sources"].append(dict(source_id="raw", format="RAW", sha256="f"*64))
    r = run(docs)
    assert metric(r, "automatic_decision_coverage")["denominator"] == 1
    assert r["overall"]["n"] == 1


def test_raw_jpg_alias_cannot_be_two_samples():
    docs = envelopes(2)
    docs[0]["rows"][1]["capture_id"] = "capture-0"
    with pytest.raises(oe.ContractError, match="duplicate capture_id"):
        run(docs)


def test_no_face_does_not_imply_landscape():
    r = run(envelopes())
    assert set(r["strata"]["scene_type"]) == {"unknown"}


@pytest.mark.parametrize("document,field,value", [(1,"dataset_version","stale"), (2,"guideline_version","stale"), (1,"source_git","short"), (1,"configuration_sha256","bad"), (1,"manifest_version","stale"), (2,"manifest_version","stale")])
def test_version_and_provenance_mismatch_invalid(document, field, value):
    docs = envelopes()
    docs[document][field] = value
    with pytest.raises(oe.ContractError):
        run(docs)


@pytest.mark.parametrize("document,field,value", [(1,"sample_version","stale"), (2,"sample_version","stale"), (1,"input_sha256","f"*64), (2,"evaluated_source_id","absent")])
def test_per_row_source_and_sample_mismatch_invalid(document, field, value):
    docs = envelopes()
    docs[document]["rows"][0][field] = value
    with pytest.raises(oe.ContractError, match="mismatch"):
        run(docs)


def test_changed_manifest_without_refreeze_invalid():
    docs = envelopes()
    docs[0]["rows"][0]["scene_type"] = "portrait"
    with pytest.raises(oe.ContractError, match="manifest_sha256"):
        oe.evaluate(*docs)


def test_ai_annotations_rejected():
    docs = envelopes()
    docs[2]["rows"][0]["label_source"] = "ai"
    with pytest.raises(oe.ContractError, match="human-authored"):
        run(docs)


def test_unknown_decision_is_failure_and_raw_retained():
    docs = envelopes()
    docs[1]["rows"][0]["recommendation"] = "future-decision"
    r = run(docs)
    assert r["cases"][0]["prediction_raw"]["recommendation"] == "future-decision"
    assert metric(r, "runtime_failure_rate")["value"] == 1


def test_exact_error_denominators_and_scene_macro():
    docs = envelopes(4)
    for i, pred in enumerate(docs[1]["rows"]):
        pred["recommendation"] = ["reject", "keep", "review", "keep"][i]
    docs[2]["rows"][1].update(technical_label="unusable", culling_label="D")
    docs[2]["rows"][2].update(technical_label="unjudgeable", culling_label="U")
    docs[0]["rows"][0]["exception_tags"] = ["must_select", "unique_usable"]
    r = run(docs)
    assert metric(r, "clear_false_reject_rate")["value"] == 1/2
    assert metric(r, "unusable_false_keep_rate")["value"] == 1
    assert metric(r, "unusable_among_keep_rate")["value"] == 1/2
    assert metric(r, "automatic_decision_coverage")["value"] == 3/4
    assert r["strata"]["exception_tags"]["must_select"]["attribution_counts"]["clear_false_reject"] == 1
    assert r["scene_family_macro"]["clear_false_reject_rate"]["value"] == 1/2


def test_reason_inventory_is_static_and_does_not_execute_source():
    source = "raise Exception('must not run')\nx={'reasons':['real_code'], 'other':'unrelated'}\nr.reason='another_code'\nfoo(reason='keyword_code')\n"
    assert oe.reason_literals(source) == ["another_code", "keyword_code", "real_code"]


def test_catalog_version_mismatch_rejected():
    docs = envelopes()
    docs[3]["source_git"] = "b"*40
    with pytest.raises(oe.ContractError, match="catalog source_git mismatch"):
        run(docs)


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"asset_id":"a", "asset_id":"b"}')
    with pytest.raises(oe.ContractError, match="duplicate JSON key"):
        oe.read_json(path)


def test_cli_report_and_read_only_inputs(tmp_path):
    docs = envelopes(2)
    before = {}
    args = [sys.executable, str(PATH), "evaluate"]
    for name, doc in zip(("samples", "predictions", "annotations", "catalog"), docs):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(doc), encoding="utf-8")
        before[path] = path.read_bytes()
        args.extend(["--" + name, str(path)])
    out = tmp_path / "report"
    result = subprocess.run([*args, "--output", str(out)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    summary = json.loads((out / "summary.json").read_text())
    assert len((out / "cases.jsonl").read_text().splitlines()) == 2
    assert summary["real_world_accuracy"]["value"] is None
    assert all(path.read_bytes() == raw for path, raw in before.items())
    again = subprocess.run([*args, "--output", str(out)], text=True, capture_output=True)
    assert again.returncode == 2


def test_cli_invalid_inputs_write_no_metrics(tmp_path):
    docs = envelopes()
    docs[1]["rows"].append(deepcopy(docs[1]["rows"][0]))
    args = [sys.executable, str(PATH), "evaluate"]
    for name, doc in zip(("samples", "predictions", "annotations", "catalog"), docs):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(doc), encoding="utf-8")
        args.extend(["--" + name, str(path)])
    out = tmp_path / "invalid"
    result = subprocess.run([*args, "--output", str(out)], text=True, capture_output=True)
    assert result.returncode == 2
    assert json.loads((out / "validation-errors.json").read_text())["metrics"] is None
    assert not (out / "summary.json").exists()


def test_outputs_inside_repository_rejected():
    with pytest.raises(oe.ContractError, match="outside"):
        oe.outside_repository(PATH.parent / "report.json")


def test_missing_prediction_preserves_frozen_source_stratum():
    docs = envelopes()
    docs[0]["rows"][0]["sources"][0]["format"] = "RAW"
    docs[1]["rows"] = []
    r = run(docs)
    assert set(r["strata"]["source_format"]) == {"RAW"}
    assert r["strata"]["source_format"]["RAW"]["metrics"]["missing_prediction_rate"]["value"] == 1


def test_failed_and_skipped_run_statuses_are_explicitly_separate():
    docs = envelopes(4)
    docs[1]["rows"][1].update(status="failed", failure_reason="failed")
    docs[1]["rows"][2].update(status="skipped", failure_reason="skipped")
    docs[1]["rows"].pop()
    r = run(docs)
    assert r["overall"]["run_status_counts"] == {"ok": 1, "failed": 1, "skipped": 1, "missing": 1}
    assert r["overall"]["run_status_denominator"] == 4


@pytest.mark.parametrize("field", ["technical_label", "culling_label"])
@pytest.mark.parametrize("value", [[], {}, 42, True])
def test_label_wrong_type_is_explicit_contract_error(field, value):
    docs = envelopes()
    docs[2]["rows"][0][field] = value
    with pytest.raises(oe.ContractError, match="must be string or null"):
        run(docs)


@pytest.mark.parametrize("field", ["technical_label", "culling_label"])
def test_unknown_label_strings_preserved_and_not_technical_truth(field):
    docs = envelopes()
    docs[2]["rows"][0][field] = "future-human-label"
    r = run(docs)
    assert r["cases"][0][field] == "UNKNOWN"
    assert r["cases"][0]["annotation_raw"][field] == "future-human-label"


def test_evaluated_source_cannot_be_selected_after_freeze():
    docs = envelopes()
    docs[0]["rows"][0]["sources"].append(dict(source_id="raw", format="RAW", sha256="f"*64))
    docs[1]["rows"][0].update(evaluated_source_id="raw", input_sha256="f"*64)
    with pytest.raises(oe.ContractError, match="source/hash mismatch"):
        run(docs)


@pytest.mark.parametrize("document", [1, 2])
def test_unmatched_prediction_or_annotation_is_invalid(document):
    docs = envelopes()
    docs[document]["rows"][0]["asset_id"] = "orphan"
    with pytest.raises(oe.ContractError, match="unmatched asset_id"):
        run(docs)


def test_same_source_bytes_cannot_be_counted_as_second_exposure():
    docs = envelopes(2)
    docs[0]["rows"][1]["sources"][0]["sha256"] = docs[0]["rows"][0]["sources"][0]["sha256"]
    with pytest.raises(oe.ContractError, match="duplicate source bytes"):
        run(docs)


def test_three_way_overlap_is_one_aggregated_declaration():
    docs = envelopes()
    docs[0]["split_registry"] = [dict(asset_id=f"other-{i}", capture_id=f"other-capture-{i}", variant_family_id=f"other-variant-{i}", comparison_group_id=f"other-comparison-{i}", scene_family_id="scene-0", person_ids=[], split=split) for i, split in enumerate(("validation", "test"))]
    docs[0]["declared_overlaps"] = [dict(field="scene_family_id", value="scene-0", splits=["development", "validation", "test"], reason="historical overlap")]
    r = run(docs)
    assert r["split_integrity"]["registered_overlaps"][0]["splits"] == ["development", "test", "validation"]


def test_fabricated_overlap_declaration_is_rejected():
    docs = envelopes()
    docs[0]["declared_overlaps"] = [dict(field="scene_family_id", value="absent", splits=["development", "test"], reason="not actually registered")]
    with pytest.raises(oe.ContractError, match="declared overlap not found"):
        run(docs)


def test_synthetic_demo_exact_counts():
    catalog = envelopes()[3]
    docs = oe.synthetic_documents(catalog)
    r = oe.evaluate(**docs)
    assert r["overall"]["n"] == 8
    assert r["overall"]["decision_counts"] == {"keep": 2, "reject": 2, "review": 2, "failure": 1, "missing": 1}
    assert r["overall"]["technical_matrix"]["UNKNOWN"]["keep"] == 1
    assert r["overall"]["technical_matrix"]["UNLABELED"]["reject"] == 1
    assert metric(r, "clear_false_reject_rate")["value"] == 1/2
    assert metric(r, "unusable_false_keep_rate")["value"] == 1/2
    assert r["real_world_accuracy"]["status"] == "尚未测量"


def test_cli_synthetic_demo_repeats_identical_counts(tmp_path):
    reports = []
    for suffix in ("a", "b"):
        out = tmp_path / suffix
        result = subprocess.run([sys.executable, str(PATH), "synthetic-demo", "--output", str(out)], text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        reports.append(json.loads((out / "report" / "summary.json").read_text()))
    assert reports[0]["overall"] == reports[1]["overall"]
    assert reports[0]["input_sha256"] == reports[1]["input_sha256"]


def test_blank_templates_contain_no_labels_and_require_freeze():
    root = PATH.parents[1] / "docs" / "offline-evaluation-templates"
    docs = [json.loads((root / f"{kind}.blank.json").read_text()) for kind in ("samples", "predictions", "annotations")]
    assert all(not d["rows"] for d in docs)
    assert docs[2]["annotation_version"] is None
    with pytest.raises(oe.ContractError):
        oe.evaluate(*docs, envelopes()[3])


@pytest.mark.parametrize("field", ["session_id", "burst_id"])
def test_optional_identity_bad_type_cannot_hide_leakage(field):
    docs = envelopes()
    docs[0]["rows"][0][field] = []
    with pytest.raises(oe.ContractError, match="must be nonempty string or null"):
        run(docs)


def test_invalid_run_status_rejected_instead_of_ignored():
    docs = envelopes()
    docs[1]["rows"][0]["status"] = "future-status"
    with pytest.raises(oe.ContractError, match="invalid run status"):
        run(docs)


def test_unusable_keep_with_unknown_keep_denominator_is_explicit():
    docs = envelopes(2)
    docs[2]["rows"][0].update(technical_label="unusable", culling_label="D")
    docs[2]["rows"].pop()
    r = run(docs)
    m = metric(r, "unusable_among_keep_rate")
    assert (m["numerator"], m["denominator"], m["value"]) == (1, 2, 1/2)
    assert metric(r, "keep_truth_known_coverage")["value"] == 1/2
    assert r["overall"]["technical_matrix"]["UNLABELED"]["keep"] == 1
