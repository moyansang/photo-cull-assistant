#!/usr/bin/env python3
"""Run the portable, dependency-light developer checks.

This is a fast gate for syntax, pure metadata contracts, and developer tools.
It is intentionally smaller than the application's complete regression suite.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
import xml.etree.ElementTree as ET


SCHEMA_VERSION = 1
PROCESS_TIMEOUT_SECONDS = 300
CORE_TESTS = (
    "tests/test_af_canon.py",
    "tests/test_af_nikon.py",
)
TOOL_TESTS = (
    "tests/test_dev_checks.py",
    "tests/test_dev_doctor.py",
    "tests/test_dev_handoff.py",
)
TEST_SELECTION = CORE_TESTS + TOOL_TESTS


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


def _run_process(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> ProcessResult:
    """Run one argv vector without a command shell."""
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            check=False,
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return ProcessResult(124, stderr=f"TimeoutExpired after {exc.timeout} seconds")
    except OSError as exc:
        return ProcessResult(127, stderr=f"{type(exc).__name__}: {exc}")
    return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


def _git(repo: Path, *args: str) -> ProcessResult:
    env = os.environ.copy()
    for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_REPLACE_REF_BASE",
    ):
        env.pop(key, None)
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    return _run_process(
        [
            "git", "-c", f"core.hooksPath={os.devnull}",
            "-c", "core.fsmonitor=false", "-C", str(repo), *args,
        ],
        cwd=repo,
        env=env,
    )


def repository_state(repo: Path) -> dict[str, object]:
    head = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    status = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if head.exit_code or tree.exit_code or status.exit_code:
        raise RuntimeError("cannot read Git repository state")
    return {
        "head": head.stdout.strip(),
        "tree": tree.stdout.strip(),
        "dirty": bool(status.stdout),
    }


def tracked_python_files(repo: Path) -> list[str]:
    result = _git(repo, "ls-files", "-z", "--", "*.py")
    if result.exit_code:
        raise RuntimeError("cannot list tracked Python files")
    files = sorted(item for item in result.stdout.split("\0") if item)
    if not files:
        raise RuntimeError("Git did not report any tracked Python files")
    return files


def check_syntax(repo: Path, files: list[str]) -> tuple[int, list[dict[str, object]]]:
    failures: list[dict[str, object]] = []
    for relative in files:
        try:
            with tokenize.open(repo / relative) as handle:
                source = handle.read()
            compile(source, relative, "exec", dont_inherit=True)
        except (OSError, SyntaxError, UnicodeError) as exc:
            item: dict[str, object] = {
                "file": relative,
                "type": type(exc).__name__,
            }
            if isinstance(exc, SyntaxError):
                item["line"] = exc.lineno
                item["offset"] = exc.offset
            failures.append(item)
    return (0 if not failures else 1), failures


def junit_summary(path: Path, repo: Path | None = None) -> dict[str, object]:
    counts = {"tests": 0, "passed": 0, "failures": 0, "errors": 0, "skipped": 0}
    skipped_cases: list[dict[str, str]] = []
    if not path.is_file():
        return {**counts, "skipped_cases": skipped_cases}
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    counts["tests"] = len(cases)
    for case in cases:
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        if failure is not None:
            counts["failures"] += 1
        elif error is not None:
            counts["errors"] += 1
        elif skipped is not None:
            counts["skipped"] += 1
            class_name = case.get("classname", "")
            name = case.get("name", "")
            node_id = f"{class_name}::{name}" if class_name else name
            reason = skipped.get("message", "")
            if repo is not None:
                node_id = _safe_console_output(node_id, repo)
                reason = _safe_console_output(reason, repo)
            skipped_cases.append({
                "node_id": node_id,
                "reason": reason[:500],
            })
        else:
            counts["passed"] += 1
    return {**counts, "skipped_cases": skipped_cases}


def _relative_to_repo(repo: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None


def _validate_generated_path(repo: Path, path: Path) -> None:
    relative = _relative_to_repo(repo, path)
    if relative is None:
        return
    if relative == ".git" or relative.casefold().startswith(".git/"):
        raise ValueError("generated files must not be written inside .git")
    tracked = _git(repo, "ls-files", "--error-unmatch", "--", relative)
    if tracked.exit_code == 0:
        raise ValueError("generated files must not overwrite a tracked file")
    ignored = _git(repo, "check-ignore", "-q", "--", relative)
    if ignored.exit_code != 0:
        raise ValueError("generated files inside the repository must be ignored")


def validate_report_path(repo: Path, report: Path) -> Path:
    report = report.expanduser().resolve()
    _validate_generated_path(repo, report)
    _validate_generated_path(repo, report.with_suffix(".junit.xml"))
    return report


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_console_output(value: str, repo: Path) -> str:
    cleaned = value
    for raw, marker in ((str(repo), "<repo>"), (str(Path.home()), "<home>")):
        if raw:
            cleaned = cleaned.replace(raw, marker).replace(Path(raw).as_posix(), marker)
    return cleaned[-8000:]


def _command_record(
    name: str,
    argv: list[str],
    exit_code: int,
    duration: float,
    **extra: object,
) -> dict[str, object]:
    return {
        "name": name,
        "argv": argv,
        "status": "passed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "duration_seconds": round(duration, 3),
        **extra,
    }


def run_checks(repo: Path, report: Path) -> tuple[int, dict[str, object]]:
    initial_state = repository_state(repo)
    files = tracked_python_files(repo)
    missing_tests = [item for item in TEST_SELECTION if not (repo / item).is_file()]

    syntax_started = time.monotonic()
    syntax_code, syntax_failures = check_syntax(repo, files)
    commands: list[dict[str, object]] = [_command_record(
        "tracked-python-syntax",
        ["<in-process>", "compile", "git-tracked-python"],
        syntax_code,
        time.monotonic() - syntax_started,
        files_checked=len(files),
        failures=syntax_failures,
    )]

    junit = report.with_suffix(".junit.xml")
    junit.unlink(missing_ok=True)
    pytest_code = 4 if missing_tests else 0
    pytest_stdout = ""
    pytest_stderr = ""
    pytest_started = time.monotonic()
    display_argv = [
        "python", "-X", "utf8", "-m", "pytest", *TEST_SELECTION,
        "-q", "-p", "no:cacheprovider", "--basetemp", "<temporary>",
        "--junitxml", junit.name,
    ]
    if not missing_tests:
        temporary_root = Path(tempfile.mkdtemp(prefix="photo-cull-dev-checks-"))
        try:
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONPATH"] = str(repo / "src")
            pytest_argv = [
                sys.executable, "-X", "utf8", "-m", "pytest", *TEST_SELECTION,
                "-q", "-p", "no:cacheprovider", "--basetemp", str(temporary_root / "pytest"),
                "--junitxml", str(junit),
            ]
            result = _run_process(pytest_argv, cwd=repo, env=env)
            pytest_code = result.exit_code
            pytest_stdout = result.stdout
            pytest_stderr = result.stderr
        finally:
            # pytest owns only this isolated directory; leave no repository output.
            shutil.rmtree(temporary_root, ignore_errors=True)
    junit_exists = junit.is_file()
    tests = junit_summary(junit, repo)
    evidence_error = None
    if pytest_code == 0 and not junit_exists:
        pytest_code = 1
        evidence_error = "pytest exited successfully but did not create JUnit XML"
    elif pytest_code == 0 and tests["tests"] == 0:
        pytest_code = 1
        evidence_error = "pytest exited successfully but JUnit XML contains no test cases"
    if evidence_error:
        pytest_stderr += f"Developer check error: {evidence_error}\n"
    commands.append(_command_record(
        "selected-pytest",
        display_argv,
        pytest_code,
        time.monotonic() - pytest_started,
        junit={"file": junit.name, "evidence_error": evidence_error, **tests},
        missing=missing_tests,
    ))

    state = repository_state(repo)
    stable = state == initial_state
    commands.append(_command_record(
        "repository-stability",
        ["<in-process>", "compare-git-snapshots"],
        0 if stable else 1,
        0.0,
        initial=initial_state,
        final=state,
    ))

    failed_commands = sum(item["status"] == "failed" for item in commands)
    overall = "passed" if failed_commands == 0 else "failed"
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "repository": state,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        },
        "selection": {
            "syntax": {"mode": "git-tracked-python", "count": len(files), "files": files},
            "tests": list(TEST_SELECTION),
            "scope": "fast selected checks; not the complete regression suite",
        },
        "commands": commands,
        "summary": {
            "status": overall,
            "commands": {
                "passed": len(commands) - failed_commands,
                "failed": failed_commands,
                "skipped": 0,
            },
            "tests": tests,
        },
    }
    atomic_json(report, payload)
    if pytest_stdout:
        print(_safe_console_output(pytest_stdout, repo), end="")
    if pytest_stderr:
        print(_safe_console_output(pytest_stderr, repo), file=sys.stderr, end="")
    print(f"Developer checks: {overall}; report: {report}")
    return (0 if overall == "passed" else 1), payload


def default_report_path() -> Path:
    return Path(tempfile.gettempdir()) / "photo-cull-dev-check-report.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=default_report_path())
    args = parser.parse_args(argv)
    script_repo = Path(__file__).resolve().parents[1]
    try:
        report = validate_report_path(script_repo, args.report)
        code, _ = run_checks(script_repo, report)
        return code
    except (OSError, RuntimeError, ValueError, ET.ParseError) as exc:
        print(f"Developer checks could not run: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
