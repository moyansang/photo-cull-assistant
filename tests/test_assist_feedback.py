import json

import pytest

from ai_cull_assistant.assist_feedback import AssistFeedback, MAX_SAMPLES


SEED_BOX = (.1, .2, .3, .4)
SEED_SIGNATURE = (100, 200)


def feedback(tmp_path, identifier="people/ref.jpg", box=SEED_BOX, signature=SEED_SIGNATURE):
    return AssistFeedback(tmp_path, identifier, box, signature)


def test_persists_rows_position_and_correction_metadata(tmp_path):
    store = feedback(tmp_path)
    row = store.save_row(
        "people/target.jpg", (300, 400), (.2, .3, .4, .5), "edited", True,
        original_box=(.1, .1, .4, .4),
    )
    store.set_position("people/next.jpg")

    restored = feedback(tmp_path)
    assert restored.get_position() == "people/next.jpg"
    assert restored.rows() == {"people/target.jpg": row}
    assert row["signature"] == [300, 400]
    assert row["original_box"] == [.1, .1, .4, .4]
    assert row["correction_delta"] == pytest.approx([.1, .2, 0, .1])
    assert row["source_key"] == restored.source_key


def test_only_explicit_confirmations_are_positive_and_rejections_are_negative(tmp_path):
    store = feedback(tmp_path)
    store.save_row("confirmed.jpg", (1, 1), (.1, .1, .2, .2), "confirmed", True)
    store.save_row("not-accepted.jpg", (2, 2), (.2, .2, .2, .2), "confirmed", False)
    store.save_row("rejected.jpg", (3, 3), (.3, .3, .2, .2), "rejected", False)
    store.save_row("skipped.jpg", (4, 4), (.4, .4, .2, .2), "skipped", False)

    assert store.samples() == [
        {"id": "confirmed.jpg", "signature": [1, 1], "box": [.1, .1, .2, .2]}
    ]
    assert store.rejected() == [
        {"id": "rejected.jpg", "signature": [3, 3], "box": [.3, .3, .2, .2]}
    ]

    store.save_row("confirmed.jpg", (1, 1), (.15, .1, .2, .2), "edited", True)
    assert store.samples() == []
    assert store.rows()["confirmed.jpg"]["status"] == "edited"


def test_changed_target_signature_starts_fresh_correction_metadata(tmp_path):
    store = feedback(tmp_path)
    store.save_row(
        "changed.jpg", (1, 1), (.2, .2, .3, .3), "edited", True,
        original_box=(.1, .1, .3, .3),
    )
    row = store.save_row("changed.jpg", (2, 2), (.4, .4, .2, .2), "pending", False)

    assert row["original_box"] == row["box"]
    assert row["correction_delta"] == [0.0, 0.0, 0.0, 0.0]


@pytest.mark.parametrize("status", ["pending", "skipped", "rejected", "edited"])
def test_nonconfirmed_decisions_can_be_saved_without_a_candidate_box(tmp_path, status):
    store = feedback(tmp_path)
    row = store.save_row("no-box.jpg", (1, 2), None, status, False)

    assert row["box"] is None
    assert row["original_box"] is None
    assert row["correction_delta"] is None
    assert store.rejected() == []
    assert feedback(tmp_path).rows()["no-box.jpg"] == row


def test_accepted_confirmation_requires_a_candidate_box(tmp_path):
    store = feedback(tmp_path)
    with pytest.raises(ValueError, match="requires a box"):
        store.save_row("no-box.jpg", (1, 2), None, "confirmed", True)
    assert not (tmp_path / "assist-feedback.json").exists()


def test_cluster_reuse_requires_exact_seed_or_confirmed_sample(tmp_path):
    original = feedback(tmp_path)
    original.save_row("people/confirmed.jpg", (500, 600), (.2, .2, .3, .3), "confirmed", True)

    same_seed = feedback(tmp_path)
    sample_seed = feedback(tmp_path, "people/confirmed.jpg", (.2, .2, .3, .3), (500, 600))
    changed_signature = feedback(tmp_path, signature=(100, 201))
    changed_box = feedback(tmp_path, box=(.1, .2, .3, .41))
    changed_id = feedback(tmp_path, identifier="people/other.jpg")

    assert same_seed.source_key == original.source_key
    assert sample_seed.source_key == original.source_key
    assert len({changed_signature.source_key, changed_box.source_key, changed_id.source_key}) == 3
    assert original.source_key not in {
        changed_signature.source_key, changed_box.source_key, changed_id.source_key
    }


def test_removing_samples_keeps_approved_rows_until_explicit_reconfirm(tmp_path):
    store = feedback(tmp_path)
    store.save_row("one.jpg", (1, 2), (.1, .1, .2, .2), "confirmed", True)
    store.save_row("two.jpg", (3, 4), (.2, .2, .2, .2), "confirmed", True)

    assert store.remove_sample("one.jpg") is True
    assert store.remove_sample("missing.jpg") is False
    assert store.rows()["one.jpg"]["accepted"] is True
    assert [sample["id"] for sample in store.samples()] == ["two.jpg"]

    assert store.clear_samples() == 1
    restored = feedback(tmp_path)
    assert restored.samples() == []
    assert set(restored.rows()) == {"one.jpg", "two.jpg"}

    restored.save_row("one.jpg", (1, 2), (.1, .1, .2, .2), "confirmed", True)
    assert [sample["id"] for sample in restored.samples()] == ["one.jpg"]


def test_clear_references_disables_matching_effects_without_losing_progress(tmp_path):
    store = feedback(tmp_path)
    store.save_row("positive.jpg", (1, 2), (.1, .1, .2, .2), "confirmed", True)
    store.save_row("negative.jpg", (3, 4), (.2, .2, .2, .2), "rejected", False)

    assert store.clear_references() == {"samples": 1, "rejected": 1}
    assert store.samples() == []
    assert store.rejected() == []
    assert set(store.rows()) == {"positive.jpg", "negative.jpg"}

    restored = feedback(tmp_path)
    assert restored.samples() == []
    assert restored.rejected() == []
    restored.save_row("negative.jpg", (3, 4), (.2, .2, .2, .2), "rejected", False)
    assert [row["id"] for row in restored.rejected()] == ["negative.jpg"]


def test_positive_reference_history_is_bounded(tmp_path):
    store = feedback(tmp_path)
    for index in range(MAX_SAMPLES + 3):
        store.save_row(
            f"{index}.jpg", (index, index), (.1, .1, .2, .2), "confirmed", True
        )
    assert len(store.samples()) == MAX_SAMPLES
    assert store.samples()[0]["id"] == "3.jpg"


def test_malformed_file_is_preserved_and_not_overwritten(tmp_path):
    path = tmp_path / "assist-feedback.json"
    malformed = b'{"version":1,"clusters":['
    path.write_bytes(malformed)

    with pytest.raises(ValueError, match="Invalid assist feedback file"):
        feedback(tmp_path)
    assert path.read_bytes() == malformed
    assert not path.with_suffix(".json.tmp").exists()


def test_rejects_lossy_signatures_before_creating_a_file(tmp_path):
    with pytest.raises(TypeError, match="JSON-serializable"):
        feedback(tmp_path, signature={"bad": object()})
    assert not (tmp_path / "assist-feedback.json").exists()

    store = feedback(tmp_path)
    with pytest.raises(ValueError, match="non-finite"):
        store.save_row("bad.jpg", (float("nan"), 1), (.1, .1, .2, .2), "pending", False)
    assert not (tmp_path / "assist-feedback.json").exists()


def test_saved_document_uses_atomic_target_and_normalized_json(tmp_path):
    store = AssistFeedback(
        tmp_path,
        "人物/参考图.jpg",
        (0, 0, 1, 1),
        {"mtime": 2, "nested": ("a", 1)},
    )
    store.set_position(None)

    document = json.loads((tmp_path / "assist-feedback.json").read_text("utf-8"))
    assert document["version"] == 1
    seed = document["clusters"][0]["seed"]
    assert seed == {
        "id": "人物/参考图.jpg",
        "signature": {"mtime": 2, "nested": ["a", 1]},
        "box": [0.0, 0.0, 1.0, 1.0],
    }
    assert not (tmp_path / "assist-feedback.json.tmp").exists()
