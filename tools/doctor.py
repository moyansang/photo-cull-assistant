#!/usr/bin/env python3
"""Read-only development environment diagnostics for Photo Cull Assistant."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "photo-cull-dev-doctor-v1"
MINIMUM_PYTHON = (3, 12)
REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SOURCE = REPO_ROOT / "src" / "ai_cull_assistant"

PACKAGE_PROBES = (
    ("photo-cull-mvp", "ai_cull_assistant", True, "Project package"),
    ("Pillow", "PIL", True, "Pillow"),
    ("numpy", "numpy", True, "NumPy"),
    ("ttkbootstrap", "ttkbootstrap", True, "ttkbootstrap"),
    ("opencv-python-headless", "cv2", True, "OpenCV"),
    ("rawpy", "rawpy", False, "RAW decoder"),
    ("ExifRead", "exifread", False, "ExifRead metadata fallback"),
    ("pi-heif", "pi_heif", False, "HEIF decoder"),
)

PINNED_MODELS = (
    (
        "src/ai_cull_assistant/data/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    (
        "src/ai_cull_assistant/data/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
    (
        "src/ai_cull_assistant/data/body_models/person_detection_mediapipe_2023mar.onnx",
        "47fd5599d6fa17608f03e0eb0ae230baa6e597d7e8a2c8199fe00abea55a701f",
    ),
    (
        "src/ai_cull_assistant/data/body_models/pose_estimation_mediapipe_2023mar.onnx",
        "9d89c599319a18fb7d2e28451a883476164543182bafca5f09eb2cf767ed2f3f",
    ),
)


def _check(
    check_id: str,
    category: str,
    required: bool,
    status: str,
    summary: str,
    **details: Any,
) -> dict[str, Any]:
    if status not in {"pass", "missing", "error"}:
        raise ValueError(f"Unsupported check status: {status}")
    result: dict[str, Any] = {
        "id": check_id,
        "category": category,
        "required": required,
        "status": status,
        "summary": summary,
    }
    if details:
        result["details"] = details
    return result


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except (OSError, ValueError):
        return False
    return True


def _display_path(path: Path, repo_root: Path = REPO_ROOT) -> str:
    resolved = path.resolve(strict=False)
    root = repo_root.resolve(strict=False)
    if _path_is_within(resolved, root):
        relative = resolved.relative_to(root).as_posix()
        return relative or "."
    home = Path.home().resolve(strict=False)
    if _path_is_within(resolved, home):
        relative = resolved.relative_to(home).as_posix()
        return f"<home>/{relative}" if relative else "<home>"
    return str(resolved)


def _redact_text(value: str, repo_root: Path) -> str:
    replacements = (
        (str(repo_root.resolve(strict=False)), "."),
        (str(Path.home().resolve(strict=False)), "<home>"),
    )
    redacted = value
    for prefix, replacement in replacements:
        variants = {prefix, prefix.replace("\\", "/"), prefix.replace("/", "\\")}
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                redacted = re.sub(re.escape(variant), lambda _: replacement, redacted, flags=re.IGNORECASE)
    return redacted


def sanitize_report(value: Any, repo_root: Path = REPO_ROOT) -> Any:
    """Return a JSON-compatible report with home paths redacted."""
    if isinstance(value, dict):
        return {str(key): sanitize_report(item, repo_root) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_report(item, repo_root) for item in value]
    if isinstance(value, Path):
        return _display_path(value, repo_root)
    if isinstance(value, str):
        return _redact_text(value, repo_root)
    return value


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )


def check_os() -> dict[str, Any]:
    return _check(
        "system.os",
        "system",
        True,
        "pass",
        f"{platform.system()} {platform.release()}",
        system=platform.system(),
        release=platform.release(),
        machine=platform.machine(),
    )


def check_python() -> dict[str, Any]:
    version = platform.python_version()
    supported = sys.version_info >= MINIMUM_PYTHON
    return _check(
        "system.python",
        "system",
        True,
        "pass" if supported else "error",
        f"Python {version}" if supported else f"Python {version}; 3.12 or newer is required",
        version=version,
        implementation=platform.python_implementation(),
        executable=Path(sys.executable),
        minimum="3.12",
    )


def check_git(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    executable = shutil.which("git")
    if not executable:
        return _check("system.git", "system", True, "missing", "Git executable was not found")
    try:
        version_result = _run([executable, "--version"])
        root_result = _run([executable, "-C", str(repo_root), "rev-parse", "--show-toplevel"])
        head_result = _run([executable, "-C", str(repo_root), "rev-parse", "HEAD"])
    except (OSError, subprocess.SubprocessError) as exc:
        return _check("system.git", "system", True, "error", f"Git probe failed: {exc}")
    if version_result.returncode or root_result.returncode or head_result.returncode:
        detail = root_result.stderr.strip() or head_result.stderr.strip() or version_result.stderr.strip()
        return _check("system.git", "system", True, "error", "Git is present but the repository probe failed", error=detail)
    return _check(
        "system.git",
        "system",
        True,
        "pass",
        version_result.stdout.strip(),
        executable=Path(executable),
        repository=Path(root_result.stdout.strip()),
        head=head_result.stdout.strip(),
    )


def check_tk_tcl() -> dict[str, Any]:
    try:
        import tkinter

        interpreter = tkinter.Tcl()
        patchlevel = str(interpreter.call("info", "patchlevel"))
    except ModuleNotFoundError:
        return _check("capability.tk_tcl", "capability", True, "missing", "Tkinter is not installed")
    except Exception as exc:  # Tcl initialization errors vary by platform build.
        return _check("capability.tk_tcl", "capability", True, "error", f"Tcl interpreter probe failed: {exc}")
    return _check(
        "capability.tk_tcl",
        "capability",
        True,
        "pass",
        f"Tk {tkinter.TkVersion}; Tcl {patchlevel}",
        tk_version=str(tkinter.TkVersion),
        tcl_version=patchlevel,
        probe="headless Tcl interpreter; no Tk window created",
    )


def _module_origin(module_name: str) -> Path | None:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return None
    if spec.origin and spec.origin not in {"built-in", "frozen"}:
        return Path(spec.origin)
    locations = spec.submodule_search_locations
    if locations:
        first = next(iter(locations), None)
        return Path(first) if first else None
    return None


def _distribution_details(distribution_name: str) -> tuple[str | None, Path | None]:
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return None, None
    return distribution.version, Path(distribution.locate_file(""))


def check_distribution(
    distribution_name: str,
    module_name: str,
    required: bool,
    label: str,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    try:
        version, distribution_root = _distribution_details(distribution_name)
        origin = _module_origin(module_name)
    except Exception as exc:
        return _check(
            f"package.{distribution_name.lower()}",
            "package",
            required,
            "error",
            f"{label} probe failed: {exc}",
        )
    details: dict[str, Any] = {
        "distribution": distribution_name,
        "module": module_name,
        "version": version,
        "distribution_root": distribution_root,
        "module_origin": origin,
    }
    if version is None and origin is None:
        return _check(
            f"package.{distribution_name.lower()}", "package", required, "missing", f"{label} is not installed", **details
        )
    if version is None:
        return _check(
            f"package.{distribution_name.lower()}",
            "package",
            required,
            "missing",
            f"{label} module resolves, but distribution metadata is missing",
            **details,
        )
    if origin is None:
        return _check(
            f"package.{distribution_name.lower()}",
            "package",
            required,
            "missing",
            f"{label} distribution is installed, but its import cannot be resolved",
            **details,
        )
    if module_name == "ai_cull_assistant":
        expected = repo_root / "src" / "ai_cull_assistant"
        details["expected_source"] = expected
        if not _path_is_within(origin, expected):
            return _check(
                "package.photo-cull-mvp",
                "package",
                True,
                "error",
                "ai_cull_assistant resolves outside this repository source tree",
                **details,
            )
    return _check(
        f"package.{distribution_name.lower()}",
        "package",
        required,
        "pass",
        f"{label} {version}",
        **details,
    )


def check_exiftool() -> dict[str, Any]:
    executable = shutil.which("exiftool")
    if not executable:
        return _check("capability.exiftool", "capability", False, "missing", "ExifTool was not found on PATH")
    try:
        result = _run([executable, "-ver"])
    except (OSError, subprocess.SubprocessError) as exc:
        return _check("capability.exiftool", "capability", False, "error", f"ExifTool probe failed: {exc}")
    if result.returncode:
        return _check(
            "capability.exiftool",
            "capability",
            False,
            "error",
            "ExifTool returned an error",
            executable=Path(executable),
            error=result.stderr.strip(),
        )
    return _check(
        "capability.exiftool",
        "capability",
        False,
        "pass",
        f"ExifTool {result.stdout.strip()}",
        executable=Path(executable),
        version=result.stdout.strip(),
    )


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_models(repo_root: Path = REPO_ROOT, verify_hashes: bool = False) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for relative, expected in PINNED_MODELS:
        path = repo_root / relative
        check_id = f"model.{path.name}"
        if not path.is_file():
            checks.append(_check(check_id, "model", True, "missing", f"Pinned model is missing: {relative}", path=path))
            continue
        details: dict[str, Any] = {"path": path, "size": path.stat().st_size, "hash_checked": verify_hashes}
        if verify_hashes:
            try:
                actual = _sha256(path)
            except OSError as exc:
                checks.append(_check(check_id, "model", True, "error", f"Could not hash {relative}: {exc}", **details))
                continue
            details.update(expected_sha256=expected, actual_sha256=actual)
            if actual != expected:
                checks.append(_check(check_id, "model", True, "error", f"Pinned model digest does not match: {relative}", **details))
                continue
        checks.append(_check(check_id, "model", True, "pass", f"Pinned model is present: {relative}", **details))
    return checks


def check_writable(directory: Path) -> dict[str, Any]:
    check_id = "filesystem.explicit_writable_directory"
    if not directory.is_dir():
        return _check(check_id, "filesystem", True, "missing", "Requested writable-test directory does not exist", directory=directory)
    probe_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".photo-cull-doctor-",
            suffix=".probe",
            dir=directory,
            delete=False,
        ) as stream:
            probe_path = Path(stream.name)
            stream.write(b"photo-cull-doctor\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        return _check(check_id, "filesystem", True, "error", f"Writable probe failed: {exc}", directory=directory)
    try:
        probe_path.unlink()
    except OSError as exc:
        return _check(
            check_id,
            "filesystem",
            True,
            "error",
            f"Probe file was created but could not be removed: {exc}",
            directory=directory,
            probe_file=probe_path,
        )
    return _check(
        check_id,
        "filesystem",
        True,
        "pass",
        "Requested directory accepted a create/write/delete probe",
        directory=directory,
    )


def _summary(checks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        "required": {"pass": 0, "missing": 0, "error": 0},
        "optional": {"pass": 0, "missing": 0, "error": 0},
    }
    for check in checks:
        group = "required" if check["required"] else "optional"
        counts[group][check["status"]] += 1
    exit_code = 1 if counts["required"]["missing"] or counts["required"]["error"] else 0
    return {
        **counts,
        "optional_missing": counts["optional"]["missing"],
        "required_failures": counts["required"]["missing"] + counts["required"]["error"],
        "exit_code": exit_code,
    }


def collect_report(
    repo_root: Path = REPO_ROOT,
    *,
    verify_model_hashes: bool = False,
    writable_directory: Path | None = None,
) -> dict[str, Any]:
    checks = [check_os(), check_python(), check_git(repo_root), check_tk_tcl()]
    checks.extend(
        check_distribution(distribution, module, required, label, repo_root)
        for distribution, module, required, label in PACKAGE_PROBES
    )
    checks.append(check_exiftool())
    checks.extend(check_models(repo_root, verify_model_hashes))
    if writable_directory is not None:
        checks.append(check_writable(writable_directory))
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository": {"root": repo_root, "expected_package_source": repo_root / "src" / "ai_cull_assistant"},
        "options": {
            "model_hashes_checked": verify_model_hashes,
            "writable_directory_checked": writable_directory is not None,
        },
        "summary": _summary(checks),
        "checks": checks,
    }
    return sanitize_report(report, repo_root)


def _human_report(report: dict[str, Any]) -> str:
    lines = [
        "Photo Cull Assistant development environment doctor",
        f"Schema: {report['schema_version']}",
        f"Repository: {report['repository']['root']}",
        "",
    ]
    for check in report["checks"]:
        label = "required" if check["required"] else "optional"
        lines.append(f"[{check['status'].upper():7}] [{label:8}] {check['id']}: {check['summary']}")
        details = check.get("details", {})
        for key in ("version", "module_origin", "distribution_root", "executable", "head", "path"):
            if details.get(key) is not None:
                lines.append(f"                    {key}: {details[key]}")
    summary = report["summary"]
    lines.extend(
        [
            "",
            f"Required failures: {summary['required_failures']}",
            f"Optional components missing: {summary['optional_missing']}",
            f"Exit code: {summary['exit_code']}",
        ]
    )
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect a Photo Cull Assistant development environment without downloading models, "
            "loading inference weights, reading settings, or scanning photos."
        ),
        epilog="Exit codes: 0 = required checks passed; 1 = required check missing/error; 2 = CLI or report-output error.",
    )
    parser.add_argument("--json", action="store_true", help="emit the versioned JSON report instead of text")
    parser.add_argument("--output", type=Path, help="also write the selected report format to this file")
    parser.add_argument(
        "--check-models",
        action="store_true",
        help="calculate and compare SHA-256 for the four pinned models (presence is always checked)",
    )
    parser.add_argument(
        "--check-writable",
        metavar="DIR",
        type=Path,
        help="create, write, and delete one private probe file in this explicitly supplied directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = collect_report(
        verify_model_hashes=args.check_models,
        writable_directory=args.check_writable.resolve(strict=False) if args.check_writable else None,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n" if args.json else _human_report(report)
    if args.output:
        try:
            target = args.output.resolve()
            if _path_is_within(target, REPO_ROOT):
                raise ValueError("report output must be outside the source repository")
            # An explicit report must never overwrite source, settings or a prior file.
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(rendered)
        except (OSError, ValueError) as exc:
            print(f"doctor: could not write report: {_redact_text(str(exc), REPO_ROOT)}", file=sys.stderr)
            return 2
    sys.stdout.write(rendered)
    return int(report["summary"]["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
