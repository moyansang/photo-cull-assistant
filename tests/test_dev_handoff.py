"""Real offline Git roundtrips and rejection cases; no user repository writes."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location(
    "dev_handoff", Path(__file__).resolve().parents[1] / "tools" / "handoff.py")
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


def commit(repo, name, content):
    (repo / name).write_text(content, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-qm", "Synthetic handoff fixture")
    return git(repo, "rev-parse", "HEAD")


def report(repo, path):
    data = {
        "schema_version": 1,
        "repository": {"head": git(repo, "rev-parse", "HEAD"),
                       "tree": git(repo, "rev-parse", "HEAD^{tree}"), "dirty": False},
        "platform": {"system": "Windows"},
        "summary": {"status": "passed", "tests": {
            "tests": 2, "passed": 1, "failures": 0, "errors": 0, "skipped": 1}},
    }
    # Synthetic report for the isolated fixture repository, not a real test claim.
    data["selection"] = {"syntax": {"mode": "git-tracked-python", "count": 0, "files": []},
                         "tests": ["tests/fixture.py"], "scope": "synthetic fixture"}
    data["commands"] = [
        {"name": "tracked-python-syntax", "argv": ["<in-process>"],
         "status": "passed", "exit_code": 0, "duration_seconds": 0},
        {"name": "selected-pytest", "argv": ["python", "-m", "pytest"],
         "status": "passed", "exit_code": 0, "duration_seconds": 0,
         "junit": {"file": "checks.junit.xml", "evidence_error": None,
                   **data["summary"]["tests"]}},
        {"name": "repository-stability", "argv": ["<in-process>"],
         "status": "passed", "exit_code": 0, "duration_seconds": 0,
         "initial": dict(data["repository"]), "final": dict(data["repository"])},
    ]
    data["summary"]["commands"] = {"passed": 3, "failed": 0, "skipped": 0}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "topic/roundtrip")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    git(source, "config", "commit.gpgsign", "false")
    base = commit(source, "baseline.txt", "baseline\n")
    receiver = tmp_path / "receiver"
    subprocess.run(["git", "clone", "-q", str(source), str(receiver)], check=True)
    head = commit(source, "change.txt", "中文 increment\n")
    checks = report(source, tmp_path / "checks.json")
    return source, receiver, base, head, checks, tmp_path / "package"


def create(f):
    source, _, base, _, checks, dest = f
    return handoff.create(source, base, "topic/roundtrip", dest, checks, ["synthetic"], [])


def test_offline_roundtrip_preserves_receiver_worktree_and_supports_return(fixture, tmp_path):
    source, receiver, base, head, checks, dest = fixture
    manifest = create(fixture)
    before = git(receiver, "rev-parse", "HEAD")
    assert manifest["checks"]["summary"]["tests"]["skipped"] == 1
    assert handoff.verify(receiver, dest, base, head)["source"]["head_commit"] == head
    assert git(receiver, "rev-parse", "HEAD") == before
    assert not (receiver / "change.txt").exists()
    ref = "refs/remotes/handoff/roundtrip"
    assert handoff.receive(receiver, dest, base, ref, head)["status"] == "imported"
    assert handoff.receive(receiver, dest, base, ref, head)["status"] == "already_imported"
    assert git(receiver, "rev-parse", "HEAD") == before
    assert not (receiver / ".git" / "FETCH_HEAD").exists()
    assert git(receiver, "status", "--porcelain") == ""
    # Integration is a separate explicit user action, outside the import tool.
    git(receiver, "merge", "--ff-only", ref)
    git(receiver, "config", "user.name", "Fixture")
    git(receiver, "config", "user.email", "fixture@example.invalid")
    git(receiver, "config", "commit.gpgsign", "false")
    returned = commit(receiver, "windows-fix.txt", "return change\n")
    handoff.create(receiver, head, "topic/roundtrip", tmp_path / "return",
                   report(receiver, tmp_path / "return-checks.json"), ["return"], [])
    handoff.receive(source, tmp_path / "return", head, "refs/remotes/handoff/return", returned)
    assert git(source, "rev-parse", "refs/remotes/handoff/return") == returned
    assert git(source, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("mutation", ["dirty", "stale", "failed", "wrong_tree", "bad_counts"])
def test_rejects_uncommitted_or_mismatched_report(fixture, mutation):
    source, _, _, _, checks, _ = fixture
    data = json.loads(checks.read_text())
    if mutation == "dirty":
        (source / "untracked.txt").write_text("not committed")
    elif mutation == "stale":
        data["repository"]["head"] = "0" * 40
    elif mutation == "failed":
        data["summary"]["status"] = "failed"
    elif mutation == "wrong_tree":
        data["repository"]["tree"] = "0" * 40
    else:
        data["summary"]["tests"]["tests"] = 20
    checks.write_text(json.dumps(data))
    with pytest.raises(handoff.HandoffError):
        create(fixture)


def test_rejects_wrong_base_or_missing_common_history(fixture, tmp_path):
    _, receiver, base, head, _, dest = fixture
    create(fixture)
    with pytest.raises(handoff.HandoffError):
        handoff.verify(receiver, dest, "0" * 40, head)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    git(unrelated, "init", "-q")
    with pytest.raises(handoff.HandoffError):
        handoff.verify(unrelated, dest, base, head)


@pytest.mark.parametrize("name", ["source.bundle", "checks.json", "handoff.json", "SHA256SUMS.txt"])
def test_rejects_tampering(fixture, name):
    _, receiver, base, head, _, dest = fixture
    create(fixture)
    with (dest / name).open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises((handoff.HandoffError, json.JSONDecodeError)):
        handoff.verify(receiver, dest, base, head)


def test_rejects_forged_change_manifest_even_with_rehashed_json(fixture):
    _, receiver, base, head, _, dest = fixture
    create(fixture)
    data = handoff.read_json(dest / "handoff.json")
    data["changes"] = []
    handoff.write_json(dest / "handoff.json", data)
    handoff.checksums(dest, ["handoff.json", "checks.json", "source.bundle"])
    with pytest.raises(handoff.HandoffError, match="Changed-file"):
        handoff.verify(receiver, dest, base, head)


def test_rejects_overwrite_and_local_branch_destination(fixture):
    source, receiver, base, head, _, dest = fixture
    create(fixture)
    with pytest.raises(handoff.HandoffError, match="already exists"):
        create(fixture)
    with pytest.raises(handoff.HandoffError, match="Destination"):
        handoff.receive(receiver, dest, base, "refs/heads/main", head)
    ref = "refs/remotes/handoff/already-used"
    git(receiver, "update-ref", ref, base)
    with pytest.raises(handoff.HandoffError, match="exists"):
        handoff.receive(receiver, dest, base, ref, head)
    with pytest.raises(handoff.HandoffError, match="outside"):
        handoff.outside(source, source / "ignored-output")


def test_private_file_in_intermediate_commit_cannot_hide_in_deletion(fixture):
    source, _, _, _, checks, _ = fixture
    commit(source, "AGENTS.md", "private fixture\n")
    git(source, "rm", "-q", "AGENTS.md")
    git(source, "commit", "-qm", "Remove private fixture")
    report(source, checks)
    with pytest.raises(handoff.HandoffError, match="Local-only"):
        create(fixture)


@pytest.mark.parametrize("name", ["../escape", "C:/absolute", "dir\\file", "AUX.txt",
                                 "dir/space.", ".git/config", "foo/.env"])
def test_nonportable_or_private_paths_rejected(name):
    with pytest.raises(handoff.HandoffError):
        handoff.portable_path(name)


def test_rejects_case_colliding_paths_without_checkout(fixture):
    source, _, _, _, checks, _ = fixture
    blob = git(source, "rev-parse", "HEAD:baseline.txt")
    git(source, "update-index", "--add", "--cacheinfo", f"100644,{blob},BASELINE.txt")
    git(source, "commit", "-qm", "Synthetic case collision")
    report(source, checks)
    with pytest.raises(handoff.HandoffError):
        create(fixture)


def test_bootstrap_clones_same_head_without_exporting_other_refs(fixture, tmp_path):
    source, _, base, head, checks, dest = fixture
    # Make a disjoint private commit in another ref, never in the outgoing history.
    blob = git(source, "rev-parse", "HEAD:baseline.txt")
    tree_input = f"100644 blob {blob}\tAGENTS.md\n".encode()
    private_tree = subprocess.run(["git", "-C", str(source), "mktree"],
                                 input=tree_input, capture_output=True, check=True).stdout.decode().strip()
    private_commit = git(source, "commit-tree", private_tree, "-m", "Private historical fixture")
    git(source, "update-ref", "refs/heads/archive/private", private_commit)
    handoff.create(source, None, "topic/roundtrip", dest, checks, ["bootstrap"], [])
    assert handoff.verify(None, dest, None, head)["source"]["base_commit"] is None
    restored = tmp_path / "restored"
    subprocess.run(["git", "clone", "-q", "--single-branch", "--no-tags",
                    "--branch", "topic/roundtrip", str(dest / "source.bundle"), str(restored)],
                   check=True)
    assert git(restored, "rev-parse", "HEAD") == head
    assert git(restored, "merge-base", "--is-ancestor", base, head) == ""
    probe = subprocess.run(["git", "-C", str(restored), "cat-file", "-e", private_commit],
                           capture_output=True)
    assert probe.returncode != 0
    assert not (restored / "AGENTS.md").exists()


def test_wrong_expected_head_and_extra_package_files_are_rejected(fixture):
    _, receiver, base, head, _, dest = fixture
    create(fixture)
    with pytest.raises(handoff.HandoffError, match="HEAD"):
        handoff.verify(receiver, dest, base, base)
    (dest / "private-extra.txt").write_text("extra")
    with pytest.raises(handoff.HandoffError, match="exactly"):
        handoff.verify(receiver, dest, base, head)


@pytest.mark.parametrize("change", ["extra_field", "absolute_path", "malformed"])
def test_nonshareable_reports_rejected(fixture, change):
    _, _, _, _, checks, _ = fixture
    data = json.loads(checks.read_text())
    if change == "extra_field":
        data["private_notes"] = "not part of the shareable schema"
    elif change == "absolute_path":
        data["platform"]["release"] = "C:\\Users\\someone\\private"
    else:
        data["repository"] = []
    checks.write_text(json.dumps(data))
    with pytest.raises(handoff.HandoffError):
        create(fixture)


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_symlink_and_gitlink_tree_entries_rejected(fixture, mode):
    source, _, _, head, _, _ = fixture
    oid = head if mode == "160000" else git(source, "rev-parse", "HEAD:baseline.txt")
    git(source, "update-index", "--add", "--cacheinfo", f"{mode},{oid},unsafe-link")
    git(source, "commit", "-qm", "Synthetic link fixture")
    with pytest.raises(handoff.HandoffError, match="Symlinks"):
        handoff.tree(source, git(source, "rev-parse", "HEAD"))


def test_extra_bundle_prerequisite_is_rejected(fixture):
    _, receiver, base, head, _, dest = fixture
    create(fixture)
    bundle = dest / "source.bundle"
    data = bundle.read_bytes()
    end = data.index(b"\n\n")
    bundle.write_bytes(data[:end] + b"\n-" + b"0" * 40 + b" extra prerequisite" + data[end:])
    manifest = handoff.read_json(dest / "handoff.json")
    manifest["bundle"]["sha256"] = handoff.file_digest(bundle)
    handoff.write_json(dest / "handoff.json", manifest)
    handoff.checksums(dest, ["handoff.json", "checks.json", "source.bundle"])
    with pytest.raises(handoff.HandoffError, match="prerequisites"):
        handoff.verify(receiver, dest, base, head)


@pytest.mark.parametrize("mutation", [None, "command_failed", "state_changed", "missing_junit",
                                    "missing_commands", "missing_selection"])
def test_full_runner_report_contract(fixture, mutation):
    _, _, _, head, checks, _ = fixture
    data = handoff.read_json(checks)
    data["commands"] = [
        {"name": "selected-pytest", "argv": ["python", "-m", "pytest"],
         "status": "passed", "exit_code": 0, "duration_seconds": 0.1, "missing": [],
         "junit": {"file": "checks.junit.xml", "evidence_error": None,
                   **data["summary"]["tests"]}},
        {"name": "repository-stability", "argv": ["<in-process>"],
         "status": "passed", "exit_code": 0, "duration_seconds": 0,
         "initial": dict(data["repository"]), "final": dict(data["repository"])},
        {"name": "tracked-python-syntax", "argv": ["<in-process>"],
         "status": "passed", "exit_code": 0, "duration_seconds": 0},
    ]
    data["summary"]["commands"] = {"passed": 3, "failed": 0, "skipped": 0}
    if mutation == "command_failed":
        data["commands"][0]["status"] = "failed"
    elif mutation == "state_changed":
        data["commands"][1]["initial"]["dirty"] = True
    elif mutation == "missing_junit":
        data["commands"][0]["junit"]["evidence_error"] = "no test cases"
    elif mutation == "missing_commands":
        data["commands"] = []
    elif mutation == "missing_selection":
        del data["selection"]
    if mutation:
        with pytest.raises(handoff.HandoffError):
            handoff.check_report(data, head, data["repository"]["tree"])
    else:
        handoff.check_report(data, head, data["repository"]["tree"])
