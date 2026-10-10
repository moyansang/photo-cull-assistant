"""Verify that source distributions carry the pinned baseline without a cache."""
import importlib.util
import io
import json
from pathlib import Path
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("body_model_download", ROOT / "body_models/download.py")
download = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(download)


def test_existing_baselines_verify_and_restore_without_network(monkeypatch):
    def network_forbidden(*args, **kwargs):
        pytest.fail("Existing pinned assets must not require network access")
    monkeypatch.setattr(download, "urlopen", network_forbidden)
    verified = download.verify(download.DEFAULT_DESTINATION)
    assert download.download(download.DEFAULT_DESTINATION) == verified
    assert len(verified) == 2


def test_verify_missing_directory_is_read_only(tmp_path, monkeypatch):
    missing = tmp_path / "absent"
    monkeypatch.setattr(download, "urlopen", lambda *a, **kw: pytest.fail("verify attempted network"))
    with pytest.raises(RuntimeError, match="Missing or invalid"):
        download.verify(missing)
    assert not missing.exists()


def test_verify_rejects_corrupt_asset_without_repair(tmp_path):
    manifest = json.loads((ROOT / "body_models/manifest.json").read_text("utf-8"))
    path = tmp_path / manifest["models"][0]["filename"]
    path.write_bytes(b"corrupt-model")
    with pytest.raises(RuntimeError, match="Missing or invalid"):
        download.verify(tmp_path)
    assert path.read_bytes() == b"corrupt-model"


def test_explicit_download_rejects_wrong_upstream_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "urlopen", lambda *a, **kw: io.BytesIO(b"not-the-pinned-model"))
    with pytest.raises(RuntimeError):
        download.download(tmp_path)
    assert not list(tmp_path.iterdir())


def test_package_data_includes_models_and_distribution_notices():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    package = ROOT / "src/ai_cull_assistant"
    selected = {p for pattern in config["tool"]["setuptools"]["package-data"]["ai_cull_assistant"]
                for p in package.glob(pattern)}
    manifest = json.loads((ROOT / "body_models/manifest.json").read_text("utf-8"))
    expected = {package / "data/body_models" / entry["filename"] for entry in manifest["models"]}
    expected.update(package / "data/body_models" / n for n in ("SOURCE.txt", "LICENSE-APACHE-2.0.txt"))
    assert expected <= selected
    assert all(p.is_file() for p in expected)
