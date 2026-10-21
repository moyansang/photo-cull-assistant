"""Explicit, local-only measurement observation. Never signs historical evidence.

Use MeasurementRecorder with screen_assets(evaluation_recorder=recorder), then
freeze a ScanResult into a new directory. No observer is installed by default.
The observer never changes measurement, subject, group or human decisions.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import inspect
import types
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

SCHEMA = "frozen-prediction-provenance-v2"
_active = ContextVar("measurement_receipt_observer", default=None)


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def copy_json(value):
    return json.loads(canonical(value))


def process_started_at():
    """OS process creation time, or unknown (fail closed)."""
    try:
        if sys.platform == "linux":
            fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
            boot = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines() if line.startswith("btime "))
            return boot + int(fields[19]) / os.sysconf("SC_CLK_TCK")
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            times = [wintypes.FILETIME() for _ in range(4)]
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            kernel.GetProcessTimes.restype = wintypes.BOOL
            if kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(t) for t in times)):
                return ((times[0].dwHighDateTime << 32) + times[0].dwLowDateTime) / 10000000 - 11644473600
    except (OSError, ValueError, StopIteration, IndexError):
        pass
    return None


def loaded_code_matches(repo):
    """Fail closed for foreign/stale/monkeypatched Python functions.

    This is local consistency, not remote attestation or a signed build.
    Process-age file checks also cover module-level constants/defaults.
    """
    for name, module in list(sys.modules.items()):
        if not name.startswith("ai_cull_assistant.") or module is None:
            continue
        path = Path(getattr(module, "__file__", "")).resolve()
        if not path.is_relative_to(repo / "src" / "ai_cull_assistant") or path.suffix != ".py":
            return False
        expected = {}
        def walk(code):
            expected[code.co_qualname] = code
            for item in code.co_consts:
                if isinstance(item, types.CodeType):
                    walk(item)
        walk(compile(path.read_bytes(), str(path), "exec", dont_inherit=True))
        for attribute, value in vars(module).items():
            if attribute in expected and isinstance(value, types.FunctionType) and value.__module__ != name:
                return False
            values = vars(value).values() if isinstance(value, type) and value.__module__ == name else (value,)
            for function in values:
                if isinstance(function, (staticmethod, classmethod)):
                    function = function.__func__
                if isinstance(function, types.FunctionType) and function.__module__ == name:
                    function = inspect.unwrap(function)
                    if function.__code__.co_filename == "<string>":  # dataclass-generated methods
                        continue
                    if expected.get(function.__code__.co_qualname) != function.__code__:
                        return False
    return True


def code_identity():
    """The executing checkout, not origin/main, identifies this run.

    Packaged builds without a verified build record are deliberately unsupported.
    No environment variables, remote URLs, credentials or git diff are stored.
    """
    repo = Path(__file__).resolve().parents[2]
    result = {"source_git": None, "dirty": None, "verified": False,
              "kind": "unverifiable_build", "module_hashes": {}}
    try:
        def git(*args):
            return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode("utf-8").strip()
        if Path(git("rev-parse", "--show-toplevel")).resolve() != repo:
            return result
        result.update(source_git=git("rev-parse", "HEAD"),
                      dirty=bool(git("--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all")),
                      kind="git_checkout")
        for path in sorted((repo / "src" / "ai_cull_assistant").glob("*.py")):
            result["module_hashes"][path.name] = digest(path.read_bytes())
        started = process_started_at()
        result["process_started_at"] = started
        files = list((repo / "src" / "ai_cull_assistant").glob("*.py"))
        stable = started is not None and all(max(p.stat().st_mtime, p.stat().st_ctime) <= started for p in files)
        # Windows ctime can mean creation time; require its real change timestamp.
        if sys.platform == "win32":
            from .image_decode import _windows_change_time
            changes = [_windows_change_time(p) for p in files]
            stable = stable and all(t is not None and t / 10000000 - 11644473600 <= started for t in changes)
        result["loaded_code_matches_checkout"] = loaded_code_matches(repo)
        result["checkout_unchanged_since_process_start"] = stable
        result["verified"] = not result["dirty"] and stable and result["loaded_code_matches_checkout"]
    except (OSError, subprocess.SubprocessError, UnicodeError, SyntaxError, TypeError, ValueError, AttributeError):
        pass
    return result


def dependencies():
    """Actual installed versions plus loaded native versions, NOT a lockfile."""
    versions = {}
    for name in ("Pillow", "numpy", "opencv-python-headless", "opencv-python", "rawpy", "ExifRead", "pi-heif", "ttkbootstrap"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    loaded = {}
    for name in ("PIL", "numpy", "cv2", "rawpy"):
        module = sys.modules.get(name)
        if module is not None:
            loaded[name] = {"version": str(getattr(module, "__version__", "unknown"))}
            if name == "rawpy":
                loaded[name]["libraw_version"] = list(getattr(module, "libraw_version", ()))
    return {"kind": "actual_dependency_snapshot", "production_dependency_lock": False,
            "python": sys.version, "platform": sys.platform, "installed": versions, "loaded": loaded}


def versions():
    from . import analysis_modes, primary_quality, face_focus, image_decode, focus_protocol
    return dict(analysis_modes=analysis_modes.VERSION, landscape=analysis_modes.LANDSCAPE_VERSION,
                primary_quality=primary_quality.POLICY_VERSION, face=face_focus.VERSION,
                decode=image_decode.DECODE_VERSION, focus_protocol=focus_protocol.PROTOCOL_VERSION,
                manual_face_scope=primary_quality.MANUAL_FACE_SCOPE_VERSION)


def settings_snapshot(asset, settings):
    from . import primary_quality as pq
    from .body_focus import VERSION as body_version
    from .face_focus import VERSION as face_version
    from .roi_contract import SCHEMA_VERSION
    from .subject import DETECTION_VERSION
    entry = settings.photos.get(settings.key(asset), {})
    return copy_json({"global": {k: getattr(settings, k) for k in pq._CROP_KEYS},
                      "photo": {k: entry[k] for k in (*pq._ENTRY_KEYS, pq.CONFIRMATION_KEY) if k in entry},
                      "versions": {"roi": SCHEMA_VERSION, "detection": DETECTION_VERSION,
                                   "face": face_version, "body": body_version}})


def recording_active():
    return _active.get() is not None


def observe_decode(path, configuration):
    active = _active.get()
    if active is None:
        return
    try:
        active[2].setdefault("decoder_calls", []).append({"source_member": str(Path(path).resolve()),
            "at": now(), "configuration": copy_json(configuration)})
    except (OSError, TypeError, ValueError):
        active[2]["recording_error"] = "decoder_configuration_unverifiable"


@contextmanager
def stage(asset, name, image, options=None, regions=None):
    """Called immediately around the actual scorer, after real source decoding."""
    active = _active.get()
    if active is None or active[1] is not asset:
        yield
        return
    recorder, _, capture = active
    try:
        entry = {"name": {"face_raw_upgrade": "face", "body_regions": "body", "body_focus": "body"}.get(name, name),
                 "operation": name, "status": "running", "measured_at": now(),
                 "decode": copy_json(image.info.get("decode_evidence") or {}),
                 "options": copy_json(options or {}), "regions": copy_json(regions or []),
                 "image_size": list(image.size), "coordinate_space": "normalized_display"}
        from .primary_quality import confirmed_selection
        settings = recorder._assets[str(asset.primary_path.resolve())][1]
        try:
            entry["settings_before"] = settings_snapshot(asset, settings)
            entry["confirmation_before"] = copy_json(confirmed_selection(asset, settings))
        except (OSError, TypeError, ValueError):
            capture["recording_error"] = "stage_settings_unverifiable"
        decode = entry["decode"]
        member = decode.get("source_path")
        entry["decoder_configuration"] = next((d["configuration"] for d in reversed(capture.get("decoder_calls", []))
            if d["source_member"] == member), None)
        entry.update(source_member=member, source_kind=decode.get("source_kind"),
                     input_sha256=capture.get("state_before", {}).get("source_hashes", {}).get(member))
        capture["stages"].append(entry)
    except Exception as exc:
        capture["recording_error"] = "stage_metadata_" + type(exc).__name__
        capture["stages"].append({"name": name, "status": "unverifiable"})
        yield
        return
    try:
        yield
    except BaseException:
        entry["status"] = "failed"
        raise
    else:
        config = entry["decoder_configuration"]
        entry["status"] = "complete" if entry["input_sha256"] and decode and config and config.get("kind") == "actual_decoder_call" and config.get("status") not in ("unverifiable_cached_decode", "unverifiable") else "unverifiable"
    finally:
        entry["finished_at"] = now()
        try:
            entry["settings_after"] = settings_snapshot(asset, settings)
            entry["confirmation_after"] = copy_json(confirmed_selection(asset, settings))
            if (entry.get("settings_before") != entry["settings_after"] or entry["settings_after"] != capture.get("settings_snapshot")
                    or entry.get("confirmation_before") != entry["confirmation_after"] or entry["confirmation_after"] != capture.get("confirmation_snapshot")):
                entry["status"] = "changed"
        except (OSError, TypeError, ValueError):
            entry["status"] = "unverifiable"


def cache_hit(asset, name):
    active = _active.get()
    if active is not None and active[1] is asset:
        active[2]["cache_reuse"].append({"name": name, "status": "cache_provenance_missing"})


class MeasurementRecorder:
    """One explicitly bounded batch, reusable for local recomputation, not rescans.

    Failed/cancelled/partial captures remain visible. Cache hits have no new
    measurement timestamp and cannot be retrospectively attested. The recorder
    is intentionally not implicitly propagated to GUI worker threads.
    """
    def __init__(self, *, synthetic=False, prepare_asset=None):
        if type(synthetic) is not bool:
            raise TypeError("synthetic must be a boolean")
        if prepare_asset is not None and not callable(prepare_asset):
            raise TypeError("prepare_asset must be callable")
        self.prepare_asset = prepare_asset
        self.synthetic = synthetic
        self.run_id = str(uuid.uuid4())
        self.started_at = now()
        identity_started = time.perf_counter()
        self.identity = code_identity()
        identity_seconds = time.perf_counter() - identity_started
        self.rows = {}
        self.history = []
        self.cost = {"source_bytes_read": 0, "hash_read_count": 0, "hash_seconds": 0.0,
                     "extra_decodes": 0, "identity_seconds": identity_seconds, "observation_seconds": 0.0}
        self.errors = []
        self._assets = {}

    def _hashes(self, asset, auxiliary=False):
        paths = {str(Path(p).resolve()) for p in asset.rating_target_paths}
        if auxiliary:
            paths = {str(Path(asset.preview_path).resolve())} - paths if asset.preview_path else set()
        result = {}
        for name in sorted(paths):
            path = Path(name)
            before = path.stat()
            started = time.perf_counter()
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
                    self.cost["source_bytes_read"] += len(block)
            self.cost["hash_seconds"] += time.perf_counter() - started
            self.cost["hash_read_count"] += 1
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
                raise ValueError("source changed while hashing")
            result[name] = h.hexdigest()
        return result

    def _state(self, asset, settings, mode, body_check, *, hash_inputs=True):
        started = time.perf_counter()
        try:
            return self._state_impl(asset, settings, mode, body_check, hash_inputs=hash_inputs)
        finally:
            self.cost["observation_seconds"] += time.perf_counter() - started

    def _state_impl(self, asset, settings, mode, body_check, *, hash_inputs=True):
        from .primary_quality import confirmed_selection
        from .image_decode import _stat_identity, _windows_change_time
        paths = set(asset.rating_target_paths)
        if asset.preview_path:
            paths.add(asset.preview_path)
        signatures = {}
        for p in paths:
            path = Path(p).resolve()
            signature = (str(path), *_stat_identity(path.stat()))
            if sys.platform == "win32":
                change = _windows_change_time(path)
                if change is None:
                    raise ValueError("receipt needs reliable local Windows change identity")
                signature += (("win-change-time", change),)
            signatures[str(path)] = copy_json(signature)
        return {"source_hashes": self._hashes(asset) if hash_inputs else None,
                "auxiliary_hashes": self._hashes(asset, auxiliary=True) if hash_inputs else None,
                "source_stats": signatures, "settings": settings_snapshot(asset, settings),
                "confirmation": copy_json(confirmed_selection(asset, settings)),
                "mode": mode, "body_check": body_check}

    @contextmanager
    def measure(self, asset, settings, mode, body_check):
        from .crop_settings import CropSettings
        settings = settings or CropSettings()
        key = str(asset.primary_path.resolve())
        if _active.get() is not None:
            raise RuntimeError("nested measurement recorders are unsupported")
        capture = {"status": "running", "kind": "new_measurement", "measured_at": now(),
                   "observed_at": now(), "stages": [], "cache_reuse": [], "versions": versions()}
        self._assets[key] = (asset, settings, mode, body_check)
        try:
            if self.prepare_asset is not None:
                # Explicit batch-only preparation shares the active decoder
                # observation and pixels with scoring. Default callers do not
                # prepare anything or gain any extra reads.
                source_state = self._state(asset, settings, mode, body_check)
                preparation_token = _active.set((self, asset, capture))
                try:
                    self.prepare_asset(asset)
                except BaseException as exc:
                    capture.update(status="cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed",
                                   preparation_error=f"{type(exc).__name__}: {exc}", finished_at=now())
                    self.history.append(copy_json(capture))
                    self.rows[key] = capture
                    raise
                finally:
                    _active.reset(preparation_token)
                prepared_state = self._state(asset, settings, mode, body_check)
                stable_fields = ("source_hashes", "settings", "confirmation", "mode", "body_check")
                if (any(source_state[k] != prepared_state[k] for k in stable_fields)
                        or any(prepared_state["source_stats"].get(p) != signature
                               for p, signature in source_state["source_stats"].items())):
                    raise ValueError("source/settings changed during preview preparation")
                capture["preparation"] = {"kind": "explicit_batch_preview", "source_state": source_state}
                capture["state_before"] = prepared_state
            else:
                capture["state_before"] = self._state(asset, settings, mode, body_check)
            capture["settings_snapshot"] = capture["state_before"]["settings"]
            capture["confirmation_snapshot"] = capture["state_before"]["confirmation"]
            from .image_decode import asset_source_signature
            capture["measurement_inputs_at_start"] = {"source_signature": copy_json(asset_source_signature(asset)),
                "parameters_digest": digest(canonical(capture["settings_snapshot"]).encode()), "enabled_parts": ["scene"]}
        except (OSError, ValueError, TypeError) as exc:
            if capture.get("preparation_error"):
                raise
            capture["recording_error"] = type(exc).__name__
        token = _active.set((self, asset, capture))
        try:
            yield
        except BaseException as exc:
            capture["status"] = "cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"
            raise
        else:
            capture["status"] = "complete"
        finally:
            _active.reset(token)
            capture["finished_at"] = now()
            outcome = capture["status"]
            try:
                capture["state_after"] = self._state(asset, settings, mode, body_check)
                capture["dependency_snapshot"] = dependencies()
                capture["evidence_sha256"] = digest(canonical(asset.clarity_evidence).encode())
                capture["focus_scope_sha256"] = digest(canonical(asset.focus_scope).encode())
                if capture.get("state_before") != capture["state_after"]:
                    capture["status"] = "changed"
                elif capture.get("cache_reuse"):
                    capture["kind"] = "cache_reuse" if not capture["stages"] else "partial_recomputation"
                    if capture["kind"] == "cache_reuse":
                        capture["measured_at"] = None
                    capture["status"] = "cache_provenance_missing"
                elif not capture["stages"] or any(s["status"] != "complete" for s in capture["stages"]):
                    if capture["status"] == "complete":
                        capture["status"] = ("not_measured" if not capture["stages"] else
                            "failed" if all(s["status"] == "failed" for s in capture["stages"]) else "partial")
                if capture.get("recording_error"):
                    capture["status"] = "unverifiable"
            except (OSError, ValueError, TypeError) as exc:
                capture.update(status="unverifiable", recording_error=type(exc).__name__)
            if outcome in ("cancelled", "failed"):
                capture["validity_status"] = capture["status"]
                capture["status"] = outcome
            self.history.append(copy_json(capture))
            self.rows[key] = capture

    def freeze(self, result, samples, bindings, output):
        """Atomically publish a new immutable set, never alter the live session.

        bindings: {asset_id: {primary_path: str, sources: {source_id: str}}}.
        Sample identities/authorizations are supplied by the caller, not guessed.
        Existing atomic_json and publication operation serialize snapshots; a
        sibling staging directory becomes visible by one rename. Failed staging
        is removed, while the live workspace and user decisions are untouched.
        """
        from .ai_project import atomic_json
        from .publication_transaction import operation
        from .session_store import save_session
        output = Path(output).resolve()
        repo = Path(__file__).resolve().parents[2]
        workspace = Path(result.workspace_dir).resolve()
        if output.exists() or output.is_relative_to(repo) or output.is_relative_to(workspace):
            raise ValueError("freeze requires a new directory outside repository and active workspace")
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = output.with_name("." + output.name + ".staging-" + self.run_id)
        staging.mkdir(exist_ok=False)
        try:
            with operation(workspace):
                collected = {}
                save_session(result, fresh=True, writer=lambda path, value: collected.update({Path(path).name: value}))
                session = collected["scan-session.json"]
                # No remote AI response belongs in the technical evaluation copy.
                for asset_doc in session["assets"]:
                    asset_doc["ai_focus_result"] = None
                atomic_json(staging / "scan-session.json", session)
                atomic_json(staging / "samples.json", samples)
                receipt = {"schema_version": SCHEMA, "kind": "measurement_time_receipt", "synthetic": self.synthetic,
                           "started_at": self.started_at, "recorded_at": now(), "source_git": self.identity.get("source_git"),
                           "code_identity": self.identity, "run_id": self.run_id, "prediction_version": "measurement-time-v2",
                           "session_sha256": digest((staging / "scan-session.json").read_bytes()),
                           "manifest_sha256": digest(canonical(samples).encode()), "rows": [], "records": {}}
                receipt["code_identity"] = copy_json(self.identity)
                if code_identity() != self.identity:
                    receipt["code_identity"]["verified"] = False
                    receipt["code_identity"]["change_during_run"] = True
                by_path = {str(a.primary_path.resolve()): i for i, a in enumerate(result.assets)}
                for sample in samples["rows"]:
                    binding = bindings.get(sample["asset_id"])
                    if binding is None:
                        continue
                    key = str(Path(binding["primary_path"]).resolve())
                    if key not in by_path:
                        raise ValueError("binding does not identify a session asset")
                    index = by_path[key]
                    asset = result.assets[index]
                    saved = session["assets"][index]
                    members = {sid: str(Path(path).resolve()) for sid, path in binding["sources"].items()}
                    selected = sample["evaluation_source_id"]
                    member = members[selected]
                    source = next(s for s in sample["sources"] if s["source_id"] == selected)
                    capture = copy_json(self.rows.get(key, {"status": "cache_provenance_missing", "kind": "cache_reuse", "stages": []}))
                    state = self._assets.get(key)
                    if state is not None:
                        try:
                            current = self._state(*state)
                            if current != capture.get("state_after"):
                                capture["status"] = "changed_before_freeze"
                        except (OSError, TypeError, ValueError):
                            capture["status"] = "unverifiable_at_freeze"
                    if capture.get("evidence_sha256") != digest(canonical(saved.get("clarity_evidence")).encode()) or capture.get("focus_scope_sha256") != digest(canonical(saved.get("focus_scope")).encode()):
                        capture["status"] = "evidence_changed_before_freeze"
                    evidence = saved.get("clarity_evidence") or {}
                    inputs = evidence.get("measurement_inputs")
                    if inputs is None:
                        inputs = capture.get("measurement_inputs_at_start", {})
                    stages = capture.get("stages", [])
                    primary = next((s for s in reversed(stages) if s["name"] in ("face", "landscape")),
                                   next((s for s in reversed(stages) if s["name"] == "body"), {}))
                    receipt["rows"].append({"asset_id": sample["asset_id"], "sample_version": sample["sample_version"],
                        "evaluated_source_id": selected, "input_sha256": source["sha256"], "asset_index": index,
                        "asset_sha256": digest(canonical(saved).encode()), "source_member": member,
                        "source_kind": primary.get("source_kind"), "source_members": members,
                        "measured_at": capture.get("measured_at", self.started_at), "measurement_inputs": inputs,
                        "frozen_inputs": inputs, "versions": capture.get("versions", versions()), "capture": capture})
                captures = [r["capture"] for r in receipt["rows"]]
                record_values = {"dependency_snapshot": {"kind": "actual_dependency_snapshot", "production_dependency_lock": False,
                                  "measurements": [c.get("dependency_snapshot") for c in captures]},
                                 "configuration": [c.get("settings_snapshot") for c in captures],
                                 "decode_configuration": [{"stages": c.get("stages"), "contract": "image_decode.source-first-v1:actual-decode-evidence"} for c in captures]}
                for name, value in record_values.items():
                    content = canonical(value)
                    receipt["records"][name] = {"content": content, "sha256": digest(content.encode())}
                receipt["recording_status"] = "complete" if captures and all(c.get("status") == "complete" for c in captures) else "partial"
                receipt["cost"] = copy_json(self.cost)
                receipt["completion"] = {"status": "complete", "session_sha256": receipt["session_sha256"], "manifest_sha256": receipt["manifest_sha256"]}
                atomic_json(staging / "measurement-receipt.json", receipt)
                completion = {"schema_version": "measurement-freeze-v1", "status": "complete", "files": {
                    name: digest((staging / name).read_bytes()) for name in ("scan-session.json", "samples.json", "measurement-receipt.json")}}
                atomic_json(staging / "complete.json", completion)
                # Recheck exactly written bytes before a single directory publish.
                if any(digest((staging / name).read_bytes()) != value for name, value in completion["files"].items()):
                    raise ValueError("freeze changed before publication")
                # Check again after every write, before publication. No new decode
                # or full hash: reliable source change identities catch rewrites.
                for row in receipt["rows"]:
                    cap = row["capture"]
                    if cap.get("status") != "complete":
                        continue
                    live = self._assets[str(result.assets[row["asset_index"]].primary_path.resolve())]
                    check = self._state(*live, hash_inputs=False)
                    prior = {**cap["state_after"], "source_hashes": None, "auxiliary_hashes": None}
                    asset = live[0]
                    if (check != prior or cap["evidence_sha256"] != digest(canonical(asset.clarity_evidence).encode())
                            or cap["focus_scope_sha256"] != digest(canonical(asset.focus_scope).encode())):
                        raise ValueError("measurement changed during freeze publication")
                if output.exists():
                    raise FileExistsError(output)
                staging.rename(output)
            return output
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
