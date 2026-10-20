"""Offline, same-history Git handoffs. Never merge, checkout, push or install."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone


FORMAT = "photo-cull-handoff"
SCHEMA = 1
HEX = re.compile(r"[0-9a-f]{40}")
PRIVATE_ROOTS = {
    "AGENTS.md", "项目交接说明.md", "目录说明.md", "项目待办.md", "项目策略.md",
    "settings", "workspaces", "工作区", "精选", ".venv", "dist", "build",
    "logs", "exports", "previews", "ai_tasks", "focus-evidence",
    "training-data", "training-checkpoints", "training-runs",
}
PRIVATE_FILES = {
    "ai-api-profiles.json", "workspace-settings.json", "scan-session.json",
    "ai_project.json", "processing-settings.json", "session.log",
}
RESERVED = re.compile(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?")


class HandoffError(ValueError):
    pass


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # Prevent inherited repository selection or replacement objects changing identity.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_REPLACE_REF_BASE"):
        env.pop(key, None)
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
         "-c", "maintenance.auto=false", "-c", "gc.auto=0",
         "-C", str(repo), *args],
        capture_output=True, env=env, timeout=180,
    )
    if check and result.returncode:
        # Do not embed potentially private paths, URLs or commit messages in reports.
        raise HandoffError(f"git {args[0]} failed (exit {result.returncode}); check the local repository and prerequisites")
    return result


def output(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.decode("utf-8").strip()


def repository(path: Path) -> Path:
    path = path.resolve()
    root = Path(output(path, "rev-parse", "--show-toplevel")).resolve()
    return root


def sha(value: str) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise HandoffError("Use a full lowercase 40-character commit SHA")
    return value


def clean(repo: Path) -> None:
    if output(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise HandoffError("Commit or separately preserve tracked/untracked changes before handoff")


def branch_ref(repo: Path, branch: str) -> str:
    if not isinstance(branch, str) or branch.startswith("-"):
        raise HandoffError("Invalid branch")
    ref = "refs/heads/" + branch
    git(repo, "check-ref-format", ref)
    return ref


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(part)
    return hasher.hexdigest()


def portable_path(name: str) -> None:
    if not isinstance(name, str):
        raise HandoffError("Invalid path type")
    p = PurePosixPath(name)
    if not name or p.is_absolute() or "\\" in name or str(p) != name:
        raise HandoffError("Non-portable path in Git tree")
    for part in p.parts:
        if (part in (".", "..") or part.casefold() == ".git" or
                part.rstrip(" .") != part or RESERVED.fullmatch(part) or
                re.search(r'[<>:"|?*\x00-\x1f]', part)):
            raise HandoffError("Non-portable path in Git tree")
    root = p.parts[0]
    if root.casefold() in {x.casefold() for x in PRIVATE_ROOTS}:
        raise HandoffError(f"Local-only root is tracked: {root}")
    if name.casefold() in {x.casefold() for x in PRIVATE_FILES}:
        raise HandoffError(f"Local-only file is tracked: {name}")
    if any((part == ".env" or part.startswith(".env.")) and part != ".env.example"
           for part in p.parts):
        raise HandoffError("Environment secret file is tracked")
    if name.startswith(("experiments/data/", "experiments/results/")):
        raise HandoffError("Experiment input/output is tracked")


def tree(repo: Path, commit: str) -> dict[str, dict]:
    entries = {}
    folded = set()
    for record in git(repo, "ls-tree", "-rz", "--full-tree", sha(commit)).stdout.split(b"\0"):
        if not record:
            continue
        info, name_bytes = record.split(b"\t", 1)
        mode, kind, oid = info.decode("ascii").split()
        name = name_bytes.decode("utf-8")
        portable_path(name)
        if kind != "blob" or mode not in ("100644", "100755"):
            raise HandoffError("Symlinks and submodules require a separate transfer design")
        key = unicodedata.normalize("NFC", name).casefold()
        if key in folded:
            raise HandoffError("Case-colliding paths cannot be transferred to Windows")
        folded.add(key)
        entries[name] = {"mode": mode, "git_blob": oid}
    return entries


def change_manifest(repo: Path, base: str | None, head: str) -> list[dict]:
    # Check every transferred commit: adding a private file then deleting it is
    # still a disclosure through the bundle history.
    for commit in output(repo, "rev-list", f"{base}..{head}" if base else head).splitlines():
        tree(repo, commit)
    before, after = tree(repo, base) if base else {}, tree(repo, head)
    changes = []
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) == after.get(name):
            continue
        entry = {
            "path": name,
            "status": "added" if name not in before else "deleted" if name not in after else "modified",
            "before": before.get(name), "after": after.get(name),
        }
        if name in after:
            entry["sha256"] = digest(git(repo, "cat-file", "blob", after[name]["git_blob"]).stdout)
        if name in before:
            entry["before_sha256"] = digest(git(repo, "cat-file", "blob", before[name]["git_blob"]).stdout)
        changes.append(entry)
    return changes


def identities(repo: Path, head: str) -> dict:
    entries = tree(repo, head)
    names = [n for n in entries if n.endswith(".onnx") or n in
             ("pyproject.toml", "requirements-dev-checks.txt", "body_models/manifest.json")]
    return {n: {"git_blob": entries[n]["git_blob"],
                "sha256": digest(git(repo, "cat-file", "blob", entries[n]["git_blob"]).stdout)}
            for n in sorted(names)}


def read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
        raise HandoffError("Missing, linked or oversized JSON file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise HandoffError("JSON object required")
    return value


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def fields(value, allowed: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) - allowed:
        raise HandoffError("Unexpected fields or object type in shared metadata")
    return value


def check_report(report: dict, head: str, tree_id: str) -> None:
    fields(report, {"schema_version", "generated_at", "repository", "platform",
                    "selection", "commands", "summary"})
    fields(report.get("repository"), {"head", "tree", "dirty"})
    fields(report.get("platform"), {"system", "release", "machine",
                                    "python_version", "python_implementation"})
    fields(report.get("summary"), {"status", "commands", "tests"})
    count_fields = {"tests", "passed", "failures", "errors", "skipped", "skipped_cases"}
    fields(report["summary"].get("tests"), count_fields)
    if not {"selection", "commands"} <= report.keys():
        raise HandoffError("Use the complete report produced by tools/check_dev.py")
    if "commands" in report["summary"]:
        fields(report["summary"]["commands"], {"passed", "failed", "skipped"})
    if "selection" in report:
        selection = fields(report["selection"], {"syntax", "tests", "scope"})
        fields(selection.get("syntax"), {"mode", "count", "files"})
        for name in selection["syntax"].get("files", []) + selection.get("tests", []):
            portable_path(name)
    commands = report.get("commands", [])
    if not isinstance(commands, list):
        raise HandoffError("Report commands must be an array")
    required_commands = {"tracked-python-syntax", "selected-pytest", "repository-stability"}
    if (len(commands) != 3 or any(not isinstance(c, dict) for c in commands) or
            {c.get("name") for c in commands} != required_commands):
        raise HandoffError("Check report must include all three developer check commands")
    for command in commands:
        fields(command, {"name", "argv", "status", "exit_code", "duration_seconds",
                         "files_checked", "failures", "junit", "missing", "initial", "final"})
        if command.get("status") != "passed" or command.get("exit_code") != 0:
            raise HandoffError("Every recorded check command must have passed")
        for key in ("initial", "final"):
            if key in command:
                fields(command[key], {"head", "tree", "dirty"})
                if command[key] != report["repository"]:
                    raise HandoffError("Repository changed during the recorded checks")
        if "junit" in command:
            fields(command["junit"], count_fields | {"file", "evidence_error"})
            if command["junit"].get("evidence_error") is not None:
                raise HandoffError("The test report has incomplete JUnit evidence")
            if any(command["junit"].get(key) != report["summary"]["tests"].get(key)
                   for key in count_fields):
                raise HandoffError("JUnit and summary counts disagree")
        for failure in command.get("failures", []):
            fields(failure, {"file", "type", "line", "offset"})
    for counts in [report["summary"]["tests"]] + [c["junit"] for c in commands if "junit" in c]:
        for skipped in counts.get("skipped_cases", []):
            fields(skipped, {"node_id", "reason"})
    # Share only the fixed check schema, with no raw output or local absolute paths.
    encoded = json.dumps(report, ensure_ascii=False)
    if re.search(r"[A-Za-z]:[\\/]|/(?:home|Users|tmp|mnt|workspace|private|root|var)/|sk-[A-Za-z0-9_-]{20,}", encoded):
        raise HandoffError("Check report contains a local path or credential-like value; use a sanitized check report")
    if report.get("schema_version") != 1:
        raise HandoffError("Unsupported check report schema")
    source = report.get("repository", {})
    if source.get("head") != head or source.get("tree") != tree_id or source.get("dirty") is not False:
        raise HandoffError("Check report must match the clean HEAD and tree being transferred")
    summary = report.get("summary", {})
    counts = summary.get("tests", {})
    if (summary.get("status") != "passed" or
            any(type(counts.get(k)) is not int or counts[k] < 0
                for k in ("tests", "passed", "failures", "errors", "skipped")) or
            counts.get("failures") != 0 or counts.get("errors") != 0 or counts.get("passed", 0) < 1 or
            counts.get("tests") != sum(counts.get(k, 0) for k in ("passed", "failures", "errors", "skipped"))):
        raise HandoffError("Check report has no successful test run or inconsistent counts")
    if report.get("platform", {}).get("system") not in ("Windows", "Linux"):
        raise HandoffError("A Windows or Linux check report is required")
    if commands and summary.get("commands") != {"passed": len(commands), "failed": 0, "skipped": 0}:
        raise HandoffError("Command summary disagrees with recorded checks")


def outside(repo: Path, path: Path) -> Path:
    path = path.resolve()
    if path == repo or path.is_relative_to(repo):
        raise HandoffError("Transfer output must be outside the source repository")
    if path.exists():
        raise HandoffError("Output already exists; choose a new delivery directory")
    if not path.parent.is_dir():
        raise HandoffError("Output parent must already exist")
    return path


def checksums(folder: Path, names: list[str]) -> None:
    (folder / "SHA256SUMS.txt").write_text(
        "".join(f"{file_digest(folder / name)}  {name}\n" for name in names),
        encoding="ascii", newline="\n"
    )


def create(repo: Path, base: str | None, branch: str, dest: Path, report_path: Path,
           scope: list[str], deferred: list[str]) -> dict:
    repo = repository(repo)
    clean(repo)
    base = sha(base) if base is not None else None
    ref = branch_ref(repo, branch)
    head = output(repo, "rev-parse", "--verify", ref + "^{commit}")
    if output(repo, "symbolic-ref", "-q", "HEAD") != ref:
        raise HandoffError("Export the currently checked-out branch")
    if base == head:
        raise HandoffError("Incremental handoff needs commits after its base")
    if base:
        git(repo, "merge-base", "--is-ancestor", base, head)
    elif output(repo, "rev-parse", "--is-shallow-repository") != "false":
        raise HandoffError("Bootstrap requires complete history; a shallow bundle cannot seed an empty repository")
    tree_id = output(repo, "rev-parse", head + "^{tree}")
    report = read_json(report_path)
    check_report(report, head, tree_id)
    changes = change_manifest(repo, base, head)
    resources = identities(repo, head)
    dest = outside(repo, dest)
    with tempfile.TemporaryDirectory(prefix=".handoff-", dir=dest.parent) as temp:
        staging = Path(temp) / "delivery"
        staging.mkdir()
        bundle = staging / "source.bundle"
        git(repo, "bundle", "create", str(bundle), f"{base}..{ref}" if base else ref)
        git(repo, "bundle", "verify", str(bundle))
        write_json(staging / "checks.json", report)
        manifest = {
            "format": FORMAT, "schema_version": SCHEMA,
            "artifact_kind": "source_increment" if base else "source_bootstrap",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {"base_commit": base, "head_commit": head, "tree": tree_id,
                       "branch": branch, "worktree_clean": True},
            "scope": scope, "deferred": deferred, "changes": changes,
            "dependencies_and_models": resources,
            "production_dependency_lock": "not_yet_complete",
            "history_policy": "single_named_branch_only; known_private_paths_checked; content_review_required",
            "bundle": {"file": "source.bundle", "sha256": file_digest(bundle)},
            "checks": {"file": "checks.json", "sha256": file_digest(staging / "checks.json"),
                       "platform": report["platform"]["system"], "summary": report["summary"]},
            "windows_application_acceptance": "pending", "installed": False,
        }
        write_json(staging / "handoff.json", manifest)
        checksums(staging, ["handoff.json", "checks.json", "source.bundle"])
        # Refuse concurrent source edits before publishing the package.
        clean(repo)
        if output(repo, "rev-parse", "HEAD") != head:
            raise HandoffError("Source changed during export")
        staging.rename(dest)
    return manifest


def package(folder: Path, expected_base: str | None) -> tuple[Path, dict, dict]:
    folder = folder.resolve()
    names = {"handoff.json", "checks.json", "source.bundle", "SHA256SUMS.txt"}
    if (not folder.is_dir() or {p.name for p in folder.iterdir()} != names or
            any(p.is_symlink() or not p.is_file() for p in folder.iterdir())):
        raise HandoffError("Package must contain exactly the four ordinary handoff files")
    manifest = read_json(folder / "handoff.json")
    fields(manifest, {"format", "schema_version", "artifact_kind", "created_at", "source",
                      "scope", "deferred", "changes", "dependencies_and_models",
                      "production_dependency_lock", "history_policy", "bundle", "checks",
                      "windows_application_acceptance", "installed"})
    fields(manifest.get("source"), {"base_commit", "head_commit", "tree", "branch", "worktree_clean"})
    kind = "source_increment" if expected_base is not None else "source_bootstrap"
    if (manifest.get("format") != FORMAT or manifest.get("schema_version") != SCHEMA or
            manifest.get("artifact_kind") != kind or manifest.get("installed") is not False):
        raise HandoffError("Unsupported handoff format or installation state")
    source = manifest.get("source", {})
    if (source.get("base_commit") != (sha(expected_base) if expected_base is not None else None)
            or source.get("worktree_clean") is not True):
        raise HandoffError("Package base does not match the receiving agreement")
    sha(source.get("head_commit"))
    sha(source.get("tree"))
    for key, name in (("bundle", "source.bundle"), ("checks", "checks.json")):
        metadata = fields(manifest.get(key), {"file", "sha256", "platform", "summary"})
        path = folder / name
        if (metadata.get("file") != name or path.is_symlink() or not path.is_file() or
                file_digest(path) != metadata.get("sha256")):
            raise HandoffError(f"Invalid or modified {name}")
    expected_sums = "".join(f"{file_digest(folder / name)}  {name}\n"
                            for name in ("handoff.json", "checks.json", "source.bundle"))
    sums = folder / "SHA256SUMS.txt"
    if sums.is_symlink() or not sums.is_file() or sums.read_text(encoding="ascii") != expected_sums:
        raise HandoffError("SHA256SUMS.txt does not match package contents")
    report = read_json(folder / "checks.json")
    check_report(report, source["head_commit"], source["tree"])
    if (manifest["checks"].get("summary") != report["summary"] or
            manifest["checks"].get("platform") != report["platform"]["system"]):
        raise HandoffError("Report summary does not match the manifest")
    return folder, manifest, report


def prerequisites(bundle: Path) -> list[str]:
    result = []
    with bundle.open("rb") as stream:
        header = stream.readline(128)
        if header not in (b"# v2 git bundle\n", b"# v3 git bundle\n"):
            raise HandoffError("Unsupported Git bundle header")
        for _ in range(10000):
            line = stream.readline(65536)
            if line == b"\n":
                return result
            if not line or len(line) == 65536:
                break
            if line.startswith(b"-"):
                result.append(sha(line[1:41].decode("ascii")))
    raise HandoffError("Invalid Git bundle header")


def verify(repo: Path | None, folder: Path, expected_base: str | None,
           expected_head: str | None = None) -> dict:
    repo = repository(repo) if repo is not None else None
    folder, manifest, _ = package(folder, expected_base)
    source = manifest["source"]
    base, head = source["base_commit"], source["head_commit"]
    if expected_head is None:
        raise HandoffError("Supply the expected HEAD from the sender's separate delivery record")
    if expected_head is not None and head != sha(expected_head):
        raise HandoffError("Package HEAD does not match the receiving agreement")
    if base:
        if repo is None:
            raise HandoffError("An incremental package needs an existing receiving repository")
        git(repo, "cat-file", "-e", base + "^{commit}")
    bundle = folder / "source.bundle"
    if prerequisites(bundle) != ([base] if base else []):
        raise HandoffError("Bundle prerequisites must be exactly the agreed base (none for bootstrap)")
    # All incoming objects and history validation stay in a disposable bare repo.
    with tempfile.TemporaryDirectory(prefix="photo-cull-verify-") as temp:
        quarantine = Path(temp)
        git(quarantine, "init", "--bare", "--quiet", "--template=")
        ref = branch_ref(quarantine, source["branch"])
        heads = output(quarantine, "bundle", "list-heads", str(bundle)).splitlines()
        if heads != [head + " " + ref]:
            raise HandoffError("Bundle must advertise exactly the declared branch and HEAD")
        if base:
            objects = Path(output(repo, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
            # Git treats a CR as part of the object-directory name on Windows.
            (quarantine / "objects" / "info" / "alternates").write_bytes(
                (objects.as_posix() + "\n").encode("utf-8"))
            shallow = Path(output(repo, "rev-parse", "--path-format=absolute", "--git-path", "shallow"))
            if shallow.is_file():
                shutil.copyfile(shallow, quarantine / "shallow")
            git(quarantine, "update-ref", "refs/handoff/base", base)
        git(quarantine, "bundle", "verify", str(bundle))
        git(quarantine, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head",
            str(bundle), ref + ":refs/handoff/incoming")
        if base:
            git(quarantine, "merge-base", "--is-ancestor", base, head)
        if output(quarantine, "rev-parse", head + "^{tree}") != source["tree"]:
            raise HandoffError("Transferred tree does not match manifest")
        if change_manifest(quarantine, base, head) != manifest.get("changes"):
            raise HandoffError("Changed-file manifest does not match actual Git objects")
        if identities(quarantine, head) != manifest.get("dependencies_and_models"):
            raise HandoffError("Dependency/model manifest does not match actual Git objects")
    return manifest


def receive(repo: Path, folder: Path, expected_base: str, target: str, expected_head: str) -> dict:
    repo = repository(repo)
    clean(repo)
    if not target.startswith("refs/remotes/handoff/"):
        raise HandoffError("Destination must be a new refs/remotes/handoff/... reference")
    git(repo, "check-ref-format", target)
    manifest = verify(repo, folder, expected_base, expected_head)
    head = manifest["source"]["head_commit"]
    existing = git(repo, "rev-parse", "--verify", target, check=False)
    if existing.returncode == 0:
        if existing.stdout.decode().strip() == head:
            return {"status": "already_imported", "ref": target, "head": head}
        raise HandoffError("Destination exists at another commit; choose a new reference")
    # Download objects only. Do not alter FETCH_HEAD, the index or a local branch.
    git(repo, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head",
        str(folder.resolve() / "source.bundle"),
        "refs/heads/" + manifest["source"]["branch"])
    git(repo, "update-ref", target, head, "0" * 40)
    return {"status": "imported", "ref": target, "head": head}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    subs = p.add_subparsers(dest="command", required=True)
    for name in ("create", "bootstrap"):
        export = subs.add_parser(name, help="Export a clean, tested single branch")
        if name == "create":
            export.add_argument("--base", required=True)
        export.add_argument("--branch", required=True)
        export.add_argument("--out", type=Path, required=True)
        export.add_argument("--report", type=Path, required=True)
        export.add_argument("--scope", action="append", required=True)
        export.add_argument("--deferred", action="append", default=[])
    for name in ("verify", "receive"):
        sub = subs.add_parser(name)
        sub.add_argument("--package", type=Path, required=True)
        sub.add_argument("--expected-base", required=True)
        sub.add_argument("--expected-head", required=True)
        if name == "receive":
            sub.add_argument("--ref", required=True)
    initial = subs.add_parser("verify-bootstrap", help="Validate a bootstrap in an empty isolated repository")
    initial.add_argument("--package", type=Path, required=True)
    initial.add_argument("--expected-head", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command in ("create", "bootstrap"):
            result = create(args.repo, args.base if args.command == "create" else None,
                            args.branch, args.out, args.report,
                            args.scope, args.deferred)
        elif args.command == "verify-bootstrap":
            result = verify(None, args.package, None, args.expected_head)
        elif args.command == "verify":
            result = verify(args.repo, args.package, args.expected_base, args.expected_head)
        else:
            result = receive(args.repo, args.package, args.expected_base, args.ref, args.expected_head)
        # Avoid printing free-form sender text or the complete file inventory.
        print(json.dumps({"ok": True, "operation": args.command,
                          "source": result.get("source"), "ref": result.get("ref"),
                          "status": result.get("status", "verified")}, ensure_ascii=True))
        return 0
    except (HandoffError, OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError,
            subprocess.SubprocessError) as exc:
        print(f"Handoff failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
