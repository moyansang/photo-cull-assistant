"""Additive dataset/group strata; synthetic records only, no accuracy claim."""
from copy import deepcopy
import json

import pytest

from test_offline_evaluation import envelopes, run, oe


def test_optional_registration_keeps_existing_metrics_and_strata_unchanged():
    docs = envelopes(4)
    before = run(deepcopy(docs))
    for i, row in enumerate(docs[0]["rows"]):
        row.update(dataset_id=f"registered-{i % 2}", dataset_version="release-2",
                   session_id="session", burst_id=f"burst-{i % 2}")
    after = run(docs)
    assert after["overall"] == before["overall"]
    assert after["scene_family_macro"] == before["scene_family_macro"]
    for field in ("split", "scene_type", "scene_family_id", "comparison_group_id",
                  "source_format", "evidence_adequacy", "historical_exposure", "exception_tags"):
        assert after["strata"][field] == before["strata"][field]


def test_selected_source_registration_precedes_sample_and_manifest():
    docs = envelopes(3)
    docs[0]["rows"][0].update(dataset_id="sample", dataset_version="sample-v")
    chosen = docs[0]["rows"][0]["sources"][0]
    chosen.update(dataset_id="source", dataset_version="source-v", original_file_id="original",
                  license="test-license", attribution="test author", preprocessing_history=[])
    docs[0]["rows"][1].update(dataset_id="sample", dataset_version="sample-v")
    report = run(docs)
    assert [(c["dataset_id"], c["dataset_version"]) for c in report["cases"]] == [
        ("source", "source-v"), ("sample", "sample-v"), ("synthetic-only", "v1")]
    assert report["cases"][0]["source_registration_raw"]["selected_source"] == chosen
    assert report["strata"]["dataset_release"][json.dumps(["source", "source-v"] )]["n"] == 1


def test_failure_and_missing_remain_in_dataset_and_group_denominators():
    docs = envelopes(4)
    for row in docs[0]["rows"]:
        row.update(dataset_id="source-a", dataset_version="one", session_id="shoot", burst_id="burst")
    docs[1]["rows"][0].update(status="failed", failure_reason="explicit test failure")
    docs[1]["rows"].pop(1)
    docs[1]["rows"][1]["recommendation"] = "review"
    report = run(docs)
    for field, key in (("dataset_id", "source-a"), ("dataset_version", "one"),
                       ("session_id", "shoot"), ("burst_id", "burst")):
        layer = report["strata"][field][key]
        assert layer["n"] == layer["run_status_denominator"] == 4
        assert layer["run_status_counts"] == {"ok": 2, "failed": 1, "skipped": 0, "missing": 1}
        for metric in ("runtime_failure_rate", "missing_prediction_rate", "review_rate"):
            assert layer["metrics"][metric]["numerator"] == 1
            assert layer["metrics"][metric]["denominator"] == 4
        assert layer["metrics"]["unusable_false_keep_rate"]["denominator"] == 0
        assert layer["metrics"]["unusable_false_keep_rate"]["value"] is None


def test_legacy_missing_group_and_null_optional_metadata_are_explicit():
    docs = envelopes(2)
    docs[0]["rows"][0].update(dataset_id=None, dataset_version=None, session_id=None, burst_id=None)
    docs[0]["rows"][0]["sources"][0].update(dataset_id=None, dataset_version=None)
    report = run(docs)
    assert report["strata"]["session_id"]["UNREGISTERED"]["n"] == 2
    assert report["strata"]["burst_id"]["UNREGISTERED"]["n"] == 2
    assert report["strata"]["dataset_id"]["synthetic-only"]["n"] == 2


def test_dataset_version_is_not_collapsed_into_one_release():
    docs = envelopes(2)
    for i, row in enumerate(docs[0]["rows"]):
        row.update(dataset_id="same-dataset", dataset_version=f"v{i}")
    report = run(docs)
    assert len(report["strata"]["dataset_release"]) == 2
    assert report["strata"]["dataset_id"]["same-dataset"]["n"] == 2


def test_dataset_names_do_not_mask_cross_split_scene_leakage():
    docs = envelopes(2)
    docs[0]["rows"][0].update(dataset_id="one", split="development")
    docs[0]["rows"][1].update(dataset_id="two", split="holdout", scene_family_id="scene-0")
    with pytest.raises(oe.ContractError, match="undeclared cross-split overlap"):
        run(docs)


@pytest.mark.parametrize("level", ["sample", "source"])
@pytest.mark.parametrize("value", [[], 123, "", " "])
def test_invalid_optional_dataset_registration_rejected(level, value):
    docs = envelopes()
    row = docs[0]["rows"][0]
    (row if level == "sample" else row["sources"][0])["dataset_id"] = value
    with pytest.raises(oe.ContractError, match="optional dataset_id"):
        run(docs)


@pytest.mark.parametrize("level", ["source", "sample"])
@pytest.mark.parametrize("missing", ["dataset_id", "dataset_version"])
def test_partial_identity_rejected_not_spliced_across_levels(level, missing):
    docs = envelopes()
    row = docs[0]["rows"][0]
    row.update(dataset_id="sample-B", dataset_version="sample-v2")
    source = row["sources"][0]
    if level == "source":
        source.update(dataset_id="source-A", dataset_version="source-v1")
    target = source if level == "source" else row
    target.pop(missing)
    with pytest.raises(oe.ContractError, match="complete identity pair"):
        run(docs)


@pytest.mark.parametrize("level", [0, 1, 2])
@pytest.mark.parametrize("missing", ["dataset_id", "dataset_version"])
def test_pair_resolver_rejects_half_identity_at_each_level(level, missing):
    records = [{}, {}, {}]
    records[level] = {"dataset_id": "registered", "dataset_version": "v1"}
    records[level].pop(missing)
    with pytest.raises(oe.ContractError, match="complete identity pair"):
        oe.dataset_identity(*records)


@pytest.mark.parametrize("level", [0, 1, 2])
def test_pair_resolver_falls_back_only_as_complete_pair(level):
    records = [{"dataset_id": None, "dataset_version": None} for _ in range(3)]
    records[level] = {"dataset_id": "registered", "dataset_version": "v1"}
    assert oe.dataset_identity(*records) == ("registered", "v1")
