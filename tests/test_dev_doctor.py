from __future__ import annotations

import importlib.metadata
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dev_doctor", ROOT / "tools" / "doctor.py")
doctor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(doctor)


def test_missing_optional_module_is_reported_without_required_failure(monkeypatch):
    def missing_distribution(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(doctor.importlib.metadata, "distribution", missing_distribution)
    monkeypatch.setattr(doctor, "_module_origin", lambda name: None)

    check = doctor.check_distribution("rawpy", "rawpy", False, "RAW decoder")
    summary = doctor._summary([check])

    assert check["status"] == "missing"
    assert check["required"] is False
    assert summary["optional_missing"] == 1
    assert summary["required_failures"] == 0
    assert summary["exit_code"] == 0


def test_project_package_rejects_an_import_from_another_checkout(tmp_path, monkeypatch):
    repo = tmp_path / "current-repo"
    expected = repo / "src" / "ai_cull_assistant"
    expected.mkdir(parents=True)
    wrong = tmp_path / "old-repo" / "src" / "ai_cull_assistant" / "__init__.py"
    wrong.parent.mkdir(parents=True)
    wrong.write_text("", encoding="utf-8")

    monkeypatch.setattr(doctor, "_distribution_details", lambda name: ("1.2.3", tmp_path / "site-packages"))
    monkeypatch.setattr(doctor, "_module_origin", lambda name: wrong)

    check = doctor.check_distribution("photo-cull-mvp", "ai_cull_assistant", True, "Project package", repo)

    assert check["status"] == "error"
    assert "outside this repository" in check["summary"]
    assert check["details"]["module_origin"] == wrong
    assert check["details"]["expected_source"] == expected


def test_model_hash_check_rejects_wrong_digest(tmp_path, monkeypatch):
    relative = "src/ai_cull_assistant/data/example.onnx"
    model = tmp_path / relative
    model.parent.mkdir(parents=True)
    model.write_bytes(b"wrong model bytes")
    monkeypatch.setattr(doctor, "PINNED_MODELS", ((relative, "0" * 64),))

    check = doctor.check_models(tmp_path, verify_hashes=True)[0]

    assert check["status"] == "error"
    assert check["details"]["expected_sha256"] == "0" * 64
    assert check["details"]["actual_sha256"] != "0" * 64
    assert check["details"]["hash_checked"] is True


def test_json_output_redacts_home_paths_and_keeps_repo_paths_relative(tmp_path, monkeypatch, capsys):
    home = tmp_path / "private-home"
    repo = home / "work" / "repo"
    package = repo / "src" / "ai_cull_assistant" / "__init__.py"
    foreign = home / "venvs" / "photo-cull" / "Lib" / "site-packages"
    package.parent.mkdir(parents=True)
    foreign.mkdir(parents=True)
    monkeypatch.setattr(doctor.Path, "home", classmethod(lambda cls: home))

    raw = {
        "schema_version": doctor.SCHEMA_VERSION,
        "repository": {"root": repo},
        "checks": [
            {
                "id": "privacy",
                "category": "test",
                "required": True,
                "status": "error",
                "summary": f"resolved {package}; environment at {foreign}",
                "details": {"module_origin": package, "distribution_root": foreign},
            }
        ],
        "summary": {"required_failures": 1, "optional_missing": 0, "exit_code": 1},
    }
    report = doctor.sanitize_report(raw, repo)
    rendered = json.dumps(report)
    print(rendered)
    captured = capsys.readouterr().out

    assert str(home) not in captured
    assert str(home).replace("\\", "/") not in captured
    assert "src/ai_cull_assistant/__init__.py" in captured
    assert "<home>/venvs/photo-cull/Lib/site-packages" in captured.replace("\\", "/")


def test_explicit_writable_probe_removes_only_its_own_file(tmp_path):
    existing = tmp_path / "keep.txt"
    existing.write_text("keep", encoding="utf-8")

    check = doctor.check_writable(tmp_path)

    assert check["status"] == "pass"
    assert existing.read_text("utf-8") == "keep"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["keep.txt"]


def test_output_cannot_overwrite_existing_or_create_inside_repository(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    existing = tmp_path / "settings.json"
    existing.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(doctor, "REPO_ROOT", repo)
    monkeypatch.setattr(doctor, "collect_report", lambda **kwargs: {"summary": {"exit_code": 0}})

    assert doctor.main(["--json", "--output", str(existing)]) == 2
    assert existing.read_text("utf-8") == "preserve"
    assert doctor.main(["--json", "--output", str(repo / "new.json")]) == 2
    assert not (repo / "new.json").exists()
    output = tmp_path / "new-report.json"
    assert doctor.main(["--json", "--output", str(output)]) == 0
    assert json.loads(output.read_text("utf-8")) == {"summary": {"exit_code": 0}}
