import json
import os
import stat

import pytest

from config import tools as tools_store


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "tools"
    registry = tmp_path / "tools.json"
    logs = tmp_path / "logs"
    runtime = tmp_path / "runtime.json"
    monkeypatch.setattr("config.tools._get_tools_root", lambda: str(root))
    monkeypatch.setattr("config.tools._get_registry_path", lambda: str(registry))
    monkeypatch.setattr("config.tools._get_logs_dir", lambda: str(logs))
    monkeypatch.setattr("config.tools._get_runtime_path", lambda: str(runtime))
    tools_store.invalidate_tools_cache()
    yield {"root": root, "registry": registry, "logs": logs, "runtime": runtime}
    tools_store.invalidate_tools_cache()


def _manifest(**overrides):
    data = {
        "id": "demo-tool",
        "name": "Demo",
        "version": "0.1.0",
        "description": "Demo tool",
        "start": ["{python}", "-m", "demo", "--port", "{port}"],
        "health": "/health",
        "default_port": 9200,
    }
    data.update(overrides)
    return data


def test_validate_manifest_defaults():
    manifest = tools_store.validate_manifest(_manifest(setup=["{python}", "-m", "pip", "install", "x"]))
    assert manifest["id"] == "demo-tool"
    assert manifest["health"] == "/health"
    assert manifest["default_port"] == 9200
    assert manifest["setup"] == ["{python}", "-m", "pip", "install", "x"]
    assert manifest["env"] == {}


@pytest.mark.parametrize("tool_id", ["Demo", "-bad", "a", "has space", ""])
def test_validate_manifest_rejects_bad_id(tool_id):
    with pytest.raises(ValueError):
        tools_store.validate_manifest(_manifest(id=tool_id))


def test_validate_manifest_requires_start():
    with pytest.raises(ValueError):
        tools_store.validate_manifest(_manifest(start=[]))
    with pytest.raises(ValueError):
        tools_store.validate_manifest(_manifest(start=[1]))


def test_validate_manifest_rejects_bad_port_and_env():
    with pytest.raises(ValueError):
        tools_store.validate_manifest(_manifest(default_port=80))
    with pytest.raises(ValueError):
        tools_store.validate_manifest(_manifest(env={"A": 1}))


def test_registry_roundtrip(env):
    tools_store.save_registry({"tools": {"demo": {"id": "demo", "name": "Demo"}}})
    loaded = tools_store.load_registry()
    assert loaded["tools"]["demo"]["name"] == "Demo"
    assert json.loads(env["registry"].read_text())["tools"]["demo"]["name"] == "Demo"


def test_registry_is_owner_only(env):
    tools_store.save_registry({"tools": {}})
    if os.name == "posix":
        assert stat.S_IMODE(env["registry"].stat().st_mode) == 0o600


def test_registry_copy_is_independent(env):
    tools_store.save_registry({"tools": {"a": {"id": "a"}}})
    copy = tools_store.registry_copy()
    copy["tools"]["a"]["id"] = "changed"
    assert tools_store.load_registry()["tools"]["a"]["id"] == "a"


def test_bad_registry_file_falls_back_to_empty(env):
    env["registry"].write_text("{not json", encoding="utf-8")
    tools_store.invalidate_tools_cache()
    assert tools_store.load_registry() == {"tools": {}}


def test_load_manifest_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        tools_store.load_manifest(str(tmp_path))


def test_state_root_lives_in_the_repo(tmp_path, monkeypatch):
    from config import tools as tools_store

    monkeypatch.setattr(tools_store, "BASE_DIR", str(tmp_path))
    assert tools_store._state_root() == str(tmp_path / ".key-router")


def test_legacy_state_root_is_migrated(tmp_path, monkeypatch):
    from config import tools as tools_store

    legacy = tmp_path / "home" / ".key-router"
    (legacy / "data" / "git-tool").mkdir(parents=True)
    (legacy / "data" / "git-tool" / "state.json").write_text("{}", encoding="utf-8")
    (legacy / "logs").mkdir()
    (legacy / "logs" / "git-tool.log").write_text("log", encoding="utf-8")
    (legacy / "tools.json").write_text('{"tools": {}}', encoding="utf-8")

    repo = tmp_path / "repo"
    monkeypatch.setattr(tools_store, "BASE_DIR", str(repo))
    monkeypatch.setattr(tools_store, "LEGACY_STATE_ROOT", str(legacy))
    monkeypatch.setattr(tools_store, "_migrated", False)

    tools_store._ensure_state()

    root = repo / ".key-router"
    assert (root / "tools.json").is_file()
    assert (root / "logs" / "git-tool.log").is_file()
    assert (root / "data" / "git-tool" / "state.json").is_file()
    assert not legacy.exists()
