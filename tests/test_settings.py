import sys

from ai_cull_assistant.settings import application_dir, load_paths, save_paths


def test_first_launch_waits_for_workspace_selection(tmp_path):
    assert load_paths(tmp_path) == {"input": "", "workspace": "", "export": ""}


def test_paths_round_trip_even_if_not_mounted(tmp_path):
    paths = {"input": "Z:/照片", "workspace": "D:/我的工作区", "export": ""}
    save_paths(tmp_path, paths)
    assert load_paths(tmp_path) == paths


def test_corrupt_and_wrong_type_settings_fall_back(tmp_path):
    path = tmp_path / "settings.json"
    for content in ('broken', '[]', '{"workspace": null}'):
        path.write_text(content, encoding="utf-8")
        assert load_paths(tmp_path)["workspace"] == ""


def test_frozen_base_uses_executable_not_working_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "AI选片助手.exe"))
    assert application_dir() == tmp_path
