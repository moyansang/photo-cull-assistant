import json

import pytest

from ai_cull_assistant.processing_journal import (
    ProcessingJournalError,
    append_patch,
    replay_patches,
)


def _snapshot(count=2):
    return {
        "assets": [{"name": f"old-{index}"} for index in range(count)],
        "screening_results": {},
        "photo_options": {},
        "completed_photos": 0,
        "percent": 0,
    }


def _patch(index, name, *, completed=None):
    patch = {
        "index": index,
        "asset": {"name": name, "nested": {"saved": True}},
        "screening_key": f"key-{index}",
        "screening_result": {"rejected": False, "reason": "sharp"},
        "photo_options": {"technical_screening": True},
    }
    if completed is not None:
        patch["state"] = {
            "completed_photos": completed,
            "percent": completed * 40,
            "local_screening_pending": None,
            "errors": [],
        }
    return patch


def test_old_snapshot_without_journal_is_unchanged(tmp_path):
    data = _snapshot()
    original = json.loads(json.dumps(data))

    assert replay_patches(tmp_path, data) is data
    assert data == original
    assert "journal_seq" not in data


def test_crash_before_and_after_base_snapshot_acknowledges_patch(tmp_path):
    data = _snapshot()
    path = append_patch(tmp_path, 1, _patch(0, "new-0", completed=1))
    assert path == tmp_path / "journal" / "000000001.json"
    assert not path.with_suffix(".json.tmp").exists()

    # A crash before job.json is rewritten replays the durable per-photo record.
    replay_patches(tmp_path, data)
    assert data["assets"][0]["name"] == "new-0"
    assert data["completed_photos"] == 1
    assert data["journal_seq"] == 1

    # A later job.json snapshot acknowledges record 1, so replay leaves it alone.
    snapshotted = json.loads(json.dumps(data))
    snapshotted["assets"][0]["name"] = "snapshot-value"
    replay_patches(tmp_path, snapshotted)
    assert snapshotted["assets"][0]["name"] == "snapshot-value"
    assert snapshotted["journal_seq"] == 1


def test_replay_updates_indices_and_multiple_related_entries(tmp_path):
    data = _snapshot(3)
    append_patch(tmp_path, 1, _patch(2, "new-2", completed=1))
    append_patch(tmp_path, 2, _patch(0, "new-0", completed=2))

    result = replay_patches(tmp_path, data)

    assert result is data
    assert [row["name"] for row in data["assets"]] == ["new-0", "old-1", "new-2"]
    assert data["screening_results"] == {
        "key-2": {"rejected": False, "reason": "sharp"},
        "key-0": {"rejected": False, "reason": "sharp"},
    }
    assert data["photo_options"] == {
        "2": {"technical_screening": True},
        "0": {"technical_screening": True},
    }
    assert data["completed_photos"] == 2
    assert data["percent"] == 80
    assert data["local_screening_pending"] is None
    assert data["errors"] == []
    assert data["journal_seq"] == 2


@pytest.mark.parametrize("failure", ["gap", "corrupt", "wrong-sequence", "out-of-bounds"])
def test_corrupt_future_journal_is_detected_without_partial_replay(tmp_path, failure):
    data = _snapshot()
    original = json.loads(json.dumps(data))
    append_patch(tmp_path, 1, _patch(0, "new-0", completed=1))

    if failure == "gap":
        (tmp_path / "journal" / "000000001.json").rename(
            tmp_path / "journal" / "000000002.json"
        )
    elif failure == "corrupt":
        (tmp_path / "journal" / "000000002.json").write_text("{broken", encoding="utf-8")
    elif failure == "wrong-sequence":
        append_patch(tmp_path, 2, _patch(1, "new-1", completed=2))
        record = json.loads(
            (tmp_path / "journal" / "000000002.json").read_text(encoding="utf-8")
        )
        record["seq"] = 3
        (tmp_path / "journal" / "000000002.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
    else:
        append_patch(tmp_path, 2, _patch(8, "bad", completed=2))

    # A failed atomic write may leave this file; it is never considered a record.
    (tmp_path / "journal" / "000000009.json.tmp").write_text("{broken", encoding="utf-8")
    with pytest.raises(ProcessingJournalError):
        replay_patches(tmp_path, data)
    assert data == original


def test_append_is_idempotent_but_rejects_sequence_reuse(tmp_path):
    patch = _patch(0, "new")
    first = append_patch(tmp_path, 1, patch)
    assert append_patch(tmp_path, 1, patch) == first

    with pytest.raises(ProcessingJournalError, match="already exists"):
        append_patch(tmp_path, 1, _patch(0, "different"))
