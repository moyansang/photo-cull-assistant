from copy import deepcopy
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import evaluation_inputs as inputs
import offline_evaluation as evaluation


@pytest.fixture
def documents(tmp_path, monkeypatch):
    # Structural/byte tests deliberately do not claim installed decoder validation.
    monkeypatch.setattr(inputs, "dependency_problems", lambda *args: [])
    path = tmp_path / "中文 空格" / "same.png"
    path.parent.mkdir()
    path.write_bytes(b"explicit synthetic bytes, not decoder acceptance")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    sample = {"asset_id": "a", "capture_id": "c", "variant_family_id": "v", "comparison_group_id": "g",
              "scene_family_id": "scene", "sample_version": "1", "split": "development", "scene_type": "landscape",
              "source_authorization": "synthetic fixture authored here", "historical_exposure": "development",
              "evaluation_source_id": "s", "person_ids": [], "session_id": None, "burst_id": None,
              "sources": [{"source_id": "s", "sha256": sha, "format": "PNG", "dataset_id": "fixture",
                           "dataset_version": "1", "original_file_id": "original", "original_sha256": sha,
                           "width": 20, "height": 20, "preprocessing_history": []}]}
    samples = {"schema_version": evaluation.SCHEMA, "kind": "samples", "dataset_id": "combined", "dataset_version": "1",
               "guideline_version": "1", "manifest_version": "1", "evaluation_kind": "development",
               "rows": [sample], "split_registry": [], "registry_review": "fixture has no external history",
               "dataset_registry": [{"dataset_id": "fixture", "dataset_version": "1", "source_url": "local:synthetic",
                                     "license": "synthetic fixture only", "attribution": "test author",
                                     "authorization": "synthetic input", "authorization_confirmed": True,
                                     "historical_exposure": "development"}]}
    mapping = {"schema_version": inputs.PATH_SCHEMA, "manifest_sha256": evaluation.canonical_hash(samples),
               "bindings": {"a": {"primary_path": str(path), "sources": {"s": str(path)},
                                    "jpeg_source_id": "s", "raw_source_id": None}}}
    return samples, mapping, {"analysis_mode": "landscape"}


def run(documents):
    samples, mapping, config = documents
    mapping["manifest_sha256"] = evaluation.canonical_hash(samples)
    return inputs.preflight(samples, mapping, config)


def test_chinese_space_path_hash_and_read_cost(documents):
    result = run(documents)
    assert result["rows"][0]["status"] == "ready"
    assert result["cost"]["hash_files"] == result["cost"]["hash_attempts"] == 1
    assert result["cost"]["hash_bytes"] > 0
    assert result["cost"]["total_seconds"] >= result["cost"]["hash_seconds"] >= 0
    assert result["independent_blind_test"] is False


def test_changed_content_is_not_measurement_ready(documents):
    Path(documents[1]["bindings"]["a"]["primary_path"]).write_bytes(b"changed")
    result = run(documents)["rows"][0]
    assert result["status"] == "failed"
    assert "s:content_hash_mismatch" in result["problems"]


def test_missing_file_kept_in_full_rows(documents):
    Path(documents[1]["bindings"]["a"]["primary_path"]).unlink()
    assert run(documents)["rows"][0]["status"] == "missing"


def test_missing_binding_kept(documents):
    documents[1]["bindings"].clear()
    assert run(documents)["rows"][0]["status"] == "missing"


@pytest.mark.parametrize("key", ["license", "source_url", "attribution", "authorization", "historical_exposure"])
def test_missing_dataset_registration_not_filled(documents, key):
    del documents[0]["dataset_registry"][0][key]
    assert run(documents)["rows"][0]["status"] == "provenance_insufficient"


@pytest.mark.parametrize("key", ["original_file_id", "original_sha256", "width", "height", "preprocessing_history"])
def test_missing_source_metadata_is_gap(documents, key):
    del documents[0]["rows"][0]["sources"][0][key]
    assert run(documents)["rows"][0]["status"] == "provenance_insufficient"


def test_unconfirmed_permission_does_not_read_bytes(documents, monkeypatch):
    documents[0]["dataset_registry"][0]["authorization_confirmed"] = False
    monkeypatch.setattr(inputs, "hash_file", lambda _: pytest.fail("unauthorized byte read"))
    result = run(documents)
    assert result["rows"][0]["status"] == "provenance_insufficient"
    assert result["cost"]["hash_attempts"] == 0


def test_unknown_scene_needs_confirmation_not_landscape_inference(documents):
    documents[0]["rows"][0]["scene_type"] = "unknown"
    assert run(documents)["rows"][0]["status"] == "needs_confirmation"


def test_declared_portrait_not_changed_by_landscape_config(documents):
    documents[0]["rows"][0]["scene_type"] = "portrait"
    assert run(documents)["rows"][0]["status"] == "needs_confirmation"
    assert documents[0]["rows"][0]["scene_type"] == "portrait"


def test_hash_change_during_read_preserves_failure(documents, monkeypatch):
    def fail(_):
        raise OSError("input_changed_during_preflight")
    monkeypatch.setattr(inputs, "hash_file", fail)
    result = run(documents)
    assert result["rows"][0]["status"] == "failed"
    assert "input_changed_during_preflight" in result["rows"][0]["problems"][0]
    assert result["cost"]["hash_attempts"] == 1


@pytest.mark.parametrize("field", ["asset_id", "capture_id"])
def test_duplicate_statistical_identity_rejected(documents, field):
    row = deepcopy(documents[0]["rows"][0])
    if field != "asset_id":
        row["asset_id"] = "other"
    documents[0]["rows"].append(row)
    with pytest.raises(evaluation.ContractError, match="duplicate"):
        run(documents)


def test_manifest_binding_required_before_read(documents, monkeypatch):
    documents[1]["manifest_sha256"] = "f" * 64
    monkeypatch.setattr(inputs, "hash_file", lambda _: pytest.fail("should not read"))
    with pytest.raises(evaluation.ContractError, match="manifest_sha256"):
        inputs.preflight(*documents)


def test_no_unlisted_asset_or_source_access(documents, monkeypatch):
    documents[1]["bindings"]["a"]["sources"]["unlisted"] = "/private/unlisted.jpg"
    monkeypatch.setattr(inputs, "hash_file", lambda _: pytest.fail("should not read"))
    with pytest.raises(evaluation.ContractError, match="exactly match"):
        run(documents)


def test_cross_directory_same_basename_not_guessed_pair(documents, tmp_path):
    sample = deepcopy(documents[0]["rows"][0])
    sample.update(asset_id="b", capture_id="c2", evaluation_source_id="s2")
    path = tmp_path / "different" / "same.png"
    path.parent.mkdir()
    path.write_bytes(b"distinct explicit bytes")
    sample["sources"][0].update(source_id="s2", original_file_id="original2", sha256=hashlib.sha256(path.read_bytes()).hexdigest(), original_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    documents[0]["rows"].append(sample)
    documents[1]["bindings"]["b"] = {"primary_path": str(path), "sources": {"s2": str(path)}, "jpeg_source_id": "s2"}
    result = run(documents)
    assert result["status_counts"] == {"ready": 2}
    assert result["same_basename_members"] == ["same.png"]


def add_raw(documents, tmp_path, jpeg=True):
    if jpeg:
        binding = documents[1]["bindings"]["a"]
        old = Path(binding["primary_path"])
        renamed = old.with_suffix(".jpg")
        old.rename(renamed)
        binding["primary_path"] = str(renamed)
        binding["sources"]["s"] = str(renamed)
        documents[0]["rows"][0]["sources"][0]["format"] = "JPEG"
    path = tmp_path / "explicit-not-name-pair.dng"
    path.write_bytes(b"synthetic raw placeholder, no RAW acceptance")
    source = deepcopy(documents[0]["rows"][0]["sources"][0])
    source.update(source_id="raw", format="DNG", original_file_id="original-raw", sha256=hashlib.sha256(path.read_bytes()).hexdigest(), original_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    documents[0]["rows"][0]["sources"].append(source)
    documents[1]["bindings"]["a"]["sources"]["raw"] = str(path)
    return source


def test_raw_jpg_pair_requires_explicit_role(documents, tmp_path):
    add_raw(documents, tmp_path)
    result = run(documents)["rows"][0]
    assert result["status"] == "provenance_insufficient"
    assert "explicit_raw_jpeg_pair_required" in result["problems"]
    documents[1]["bindings"]["a"]["raw_source_id"] = "raw"
    assert run(documents)["rows"][0]["status"] == "ready"


def test_raw_role_cannot_point_to_raster(documents):
    documents[1]["bindings"]["a"]["raw_source_id"] = "s"
    assert "member_role_mismatch:raw_source_id" in run(documents)["rows"][0]["problems"]


def test_historical_dataset_not_renamed_independent(documents):
    source = documents[0]["rows"][0]["sources"][0]
    source["dataset_id"] = "old-CERTH"
    documents[0]["dataset_registry"][0]["dataset_id"] = "old-CERTH"
    documents[0]["evaluation_kind"] = "independent_holdout"
    assert "historical_dataset_requires_development_diagnostics" in run(documents)["rows"][0]["problems"]


def test_registered_cross_split_person_leakage_rejected(documents):
    sample = documents[0]["rows"][0]
    sample["person_ids"] = ["same-person"]
    prior = {key: sample[key] for key in ("asset_id", "capture_id", "variant_family_id", "comparison_group_id", "scene_family_id", "split", "person_ids")}
    prior.update(asset_id="prior", capture_id="prior", variant_family_id="prior", comparison_group_id="prior", scene_family_id="prior", split="holdout")
    documents[0]["split_registry"] = [prior]
    with pytest.raises(evaluation.ContractError, match="cross-split"):
        run(documents)


def test_legacy_offline_samples_still_valid_but_new_gate_reports_gaps(documents):
    del documents[0]["dataset_registry"]
    del documents[0]["registry_review"]
    inputs.validate_samples(documents[0])
    assert run(documents)["rows"][0]["status"] == "provenance_insufficient"


def test_readonly_inputs_and_metadata(documents):
    before = deepcopy(documents)
    path = Path(documents[1]["bindings"]["a"]["primary_path"])
    content = path.read_bytes()
    result = inputs.preflight(*documents)
    assert result["rows"][0]["status"] == "ready"
    assert documents == before
    assert path.read_bytes() == content


def test_original_hash_requires_preprocessing_history(documents):
    documents[0]["rows"][0]["sources"][0]["original_sha256"] = "a" * 64
    assert "s:original_hash_differs_without_preprocessing_history" in run(documents)["rows"][0]["problems"]


def test_resized_original_cannot_cross_split_via_new_local_hash(documents):
    source = deepcopy(documents[0]["rows"][0]["sources"][0])
    source["sha256"] = "b" * 64
    previous = {"asset_id": "previous", "capture_id": "previous", "variant_family_id": "different",
                "comparison_group_id": "previous", "scene_family_id": "previous", "split": "holdout",
                "sources": [source]}
    documents[0]["split_registry"] = [previous]
    with pytest.raises(evaluation.ContractError, match="original/derivative crosses"):
        run(documents)


def test_asset_mode_overrides_global_landscape(documents):
    documents[0]["rows"][0]["scene_type"] = "portrait"
    documents[2]["asset_modes"] = {"a": "portrait"}
    assert run(documents)["rows"][0]["status"] == "ready"


def test_missing_dependency_is_retained_unsupported(documents, monkeypatch):
    monkeypatch.setattr(inputs, "dependency_problems", lambda *args: ["dependency_unavailable:rawpy"])
    result = run(documents)["rows"][0]
    assert result["status"] == "unsupported"
    assert "dependency_unavailable:rawpy" in result["problems"]


def test_fingerprint_is_json_safe_and_changes_on_rewrite(documents):
    path = Path(documents[1]["bindings"]["a"]["primary_path"])
    initial = run(documents)["rows"][0]["sources"]["s"]["fingerprint"]
    assert isinstance(initial, list)
    path.write_bytes(b"changed")
    assert inputs.source_fingerprint(path) != initial


def test_paired_png_is_unsupported_not_claimed_jpeg(documents, tmp_path):
    add_raw(documents, tmp_path, jpeg=False)
    documents[1]["bindings"]["a"]["raw_source_id"] = "raw"
    result = run(documents)["rows"][0]
    assert result["status"] == "unsupported"
    assert "paired_raster_must_be_jpeg" in result["problems"]


def test_single_raw_requires_explicit_raw_role(documents):
    binding = documents[1]["bindings"]["a"]
    old = Path(binding["primary_path"])
    renamed = old.with_suffix(".dng")
    old.rename(renamed)
    binding.update(primary_path=str(renamed), sources={"s": str(renamed)}, jpeg_source_id=None)
    documents[0]["rows"][0]["sources"][0]["format"] = "DNG"
    assert "measurement_member_roles_incomplete" in run(documents)["rows"][0]["problems"]
    binding["raw_source_id"] = "s"
    assert run(documents)["rows"][0]["status"] == "ready"
