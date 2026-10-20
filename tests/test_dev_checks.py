from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from tools import check_dev


ROOT = Path(__file__).resolve().parents[1]


def test_selection_is_explicit_and_contains_only_expected_test_files():
    assert check_dev.CORE_TESTS == (
        "tests/test_af_canon.py",
        "tests/test_af_nikon.py",
    )
    assert check_dev.TOOL_TESTS == (
        "tests/test_dev_checks.py",
        "tests/test_dev_doctor.py",
        "tests/test_dev_handoff.py",
    )
    assert all(item.startswith("tests/test_") and item.endswith(".py")
               for item in check_dev.TEST_SELECTION)


def test_process_uses_argv_without_shell(monkeypatch, tmp_path):
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(check_dev.subprocess, "run", fake_run)
    hostile = "value; echo should-not-run"
    result = check_dev._run_process(["program", hostile], cwd=tmp_path)
    assert result.exit_code == 0
    assert observed["argv"] == ["program", hostile]
    assert observed["kwargs"]["shell"] is False


def test_process_start_error_has_nonzero_status(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise FileNotFoundError("missing executable")

    monkeypatch.setattr(check_dev.subprocess, "run", fail)
    result = check_dev._run_process(["missing"], cwd=tmp_path)
    assert result.exit_code == 127
    assert "FileNotFoundError" in result.stderr


def test_process_timeout_has_nonzero_status(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], check_dev.PROCESS_TIMEOUT_SECONDS)

    monkeypatch.setattr(check_dev.subprocess, "run", timeout)
    result = check_dev._run_process(["slow"], cwd=tmp_path)
    assert result.exit_code == 124
    assert "TimeoutExpired" in result.stderr


def test_syntax_check_reports_relative_file_and_nonzero(tmp_path):
    good = tmp_path / "good.py"
    bad = tmp_path / "bad.py"
    good.write_text("answer = 42\n", encoding="utf-8")
    bad.write_text("if True print('bad')\n", encoding="utf-8")
    code, failures = check_dev.check_syntax(tmp_path, ["good.py", "bad.py"])
    assert code == 1
    assert failures == [{
        "file": "bad.py",
        "type": "SyntaxError",
        "line": 1,
        "offset": 9,
    }]


def test_junit_counts_results_and_records_skip_reason(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text(
        """<?xml version='1.0' encoding='utf-8'?>
<testsuites><testsuite>
  <testcase classname="tests.test_ok" name="test_pass" />
  <testcase classname="tests.test_bad" name="test_fail"><failure /></testcase>
  <testcase classname="tests.test_bad" name="test_error"><error /></testcase>
  <testcase classname="tests.test_skip" name="test_skip"><skipped message="Linux only" /></testcase>
</testsuite></testsuites>""",
        encoding="utf-8",
    )
    summary = check_dev.junit_summary(report)
    assert summary == {
        "tests": 4,
        "passed": 1,
        "failures": 1,
        "errors": 1,
        "skipped": 1,
        "skipped_cases": [{
            "node_id": "tests.test_skip::test_skip",
            "reason": "Linux only",
        }],
    }


def test_atomic_json_replaces_destination_with_utf8(tmp_path):
    destination = tmp_path / "report.json"
    destination.write_text("old", encoding="utf-8")
    check_dev.atomic_json(destination, {"label": "中文", "ok": True})
    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "label": "中文",
        "ok": True,
    }
    assert not list(tmp_path.glob(".report.json.*.tmp"))


def test_report_path_rejects_tracked_and_unignored_repository_files(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.json"
    tracked.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.json"], cwd=tmp_path, check=True)
    with pytest.raises(ValueError, match="tracked"):
        check_dev.validate_report_path(tmp_path, tracked)
    with pytest.raises(ValueError, match="ignored"):
        check_dev.validate_report_path(tmp_path, tmp_path / "new.json")


def test_report_path_allows_outside_or_ignored_destination(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("/reports/\n", encoding="utf-8")
    ignored = repo / "reports" / "dev.json"
    outside = tmp_path / "outside.json"
    assert check_dev.validate_report_path(repo, ignored) == ignored.resolve()
    assert check_dev.validate_report_path(repo, outside) == outside.resolve()


def test_report_path_rejects_tracked_junit_sidecar_and_git_directory(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("/report.json\n", encoding="utf-8")
    sidecar = tmp_path / "report.junit.xml"
    sidecar.write_text("<testsuite />", encoding="utf-8")
    subprocess.run(["git", "add", "report.junit.xml"], cwd=tmp_path, check=True)
    with pytest.raises(ValueError, match="tracked"):
        check_dev.validate_report_path(tmp_path, tmp_path / "report.json")
    with pytest.raises(ValueError, match=r"\.git"):
        check_dev.validate_report_path(tmp_path, tmp_path / ".git" / "report.json")


def test_main_propagates_failed_check_status(monkeypatch, tmp_path):
    monkeypatch.setattr(check_dev, "validate_report_path", lambda repo, path: path)
    monkeypatch.setattr(check_dev, "run_checks", lambda repo, path: (1, {}))
    assert check_dev.main(["--report", str(tmp_path / "report.json")]) == 1


def test_pytest_nonzero_replaces_stale_junit_and_fails_report(monkeypatch, tmp_path):
    report = tmp_path / "check.json"
    stale_junit = report.with_suffix(".junit.xml")
    stale_junit.write_text(
        '<testsuite><testcase classname="stale" name="old" /></testsuite>',
        encoding="utf-8",
    )
    monkeypatch.setattr(check_dev, "repository_state", lambda repo: {
        "head": "a" * 40,
        "tree": "b" * 40,
        "dirty": False,
    })
    monkeypatch.setattr(check_dev, "tracked_python_files", lambda repo: ["tools/check_dev.py"])
    monkeypatch.setattr(check_dev, "check_syntax", lambda repo, files: (0, []))
    monkeypatch.setattr(check_dev, "TEST_SELECTION", ("tests/test_dev_checks.py",))
    monkeypatch.setattr(
        check_dev,
        "_run_process",
        lambda argv, **kwargs: check_dev.ProcessResult(3, stderr="pytest failed"),
    )

    code, payload = check_dev.run_checks(ROOT, report)

    assert code == 1
    assert payload["summary"]["status"] == "failed"
    assert payload["summary"]["tests"]["tests"] == 0
    assert payload["commands"][1]["exit_code"] == 3
    assert not stale_junit.exists()
    assert json.loads(report.read_text(encoding="utf-8"))["summary"]["status"] == "failed"


def test_repository_change_during_checks_fails_and_records_final_state(monkeypatch, tmp_path):
    initial = {"head": "a" * 40, "tree": "b" * 40, "dirty": False}
    final = {"head": "c" * 40, "tree": "d" * 40, "dirty": False}
    states = iter((initial, final))
    monkeypatch.setattr(check_dev, "repository_state", lambda repo: next(states))
    monkeypatch.setattr(check_dev, "tracked_python_files", lambda repo: ["tools/check_dev.py"])
    monkeypatch.setattr(check_dev, "check_syntax", lambda repo, files: (0, []))
    monkeypatch.setattr(check_dev, "TEST_SELECTION", ("tests/test_dev_checks.py",))
    monkeypatch.setattr(
        check_dev,
        "_run_process",
        lambda argv, **kwargs: check_dev.ProcessResult(0),
    )

    code, payload = check_dev.run_checks(ROOT, tmp_path / "changed.json")

    assert code == 1
    assert payload["repository"] == final
    assert payload["summary"]["status"] == "failed"
    assert payload["commands"][-1]["name"] == "repository-stability"
    assert payload["commands"][-1]["status"] == "failed"


def test_zero_exit_without_junit_is_not_reported_as_success(monkeypatch, tmp_path):
    state = {"head": "a" * 40, "tree": "b" * 40, "dirty": False}
    monkeypatch.setattr(check_dev, "repository_state", lambda repo: state)
    monkeypatch.setattr(check_dev, "tracked_python_files", lambda repo: ["tools/check_dev.py"])
    monkeypatch.setattr(check_dev, "check_syntax", lambda repo, files: (0, []))
    monkeypatch.setattr(check_dev, "TEST_SELECTION", ("tests/test_dev_checks.py",))
    monkeypatch.setattr(
        check_dev,
        "_run_process",
        lambda argv, **kwargs: check_dev.ProcessResult(0),
    )

    code, payload = check_dev.run_checks(ROOT, tmp_path / "missing-junit.json")

    pytest_record = payload["commands"][1]
    assert code == 1
    assert pytest_record["status"] == "failed"
    assert pytest_record["junit"]["evidence_error"] == (
        "pytest exited successfully but did not create JUnit XML"
    )
    assert payload["summary"]["tests"]["tests"] == 0
