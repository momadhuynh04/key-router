import json

import pytest
from fastapi.testclient import TestClient

from bridge.config import AgentSpec, BridgeConfig
from proxy.app import create_app


@pytest.fixture
def env(tmp_path, monkeypatch):
    workdir = tmp_path / "proj"
    workdir.mkdir()
    agents_file = tmp_path / "agents.json"
    pid_file = tmp_path / ".pid"
    log_file = tmp_path / "bridge.log"

    cfg = BridgeConfig(
        token="tok-123",
        allowed_roots=[str(tmp_path)],
        default_agent="a1",
        agents=[
            AgentSpec(id="a1", name="One", cli="claude", model="opus",
                      workdir=str(workdir), permission="edit", effort="high", tools="edit"),
        ],
    )
    agents_file.write_text(json.dumps(cfg.model_dump()), encoding="utf-8")

    monkeypatch.setattr("bridge.config.AGENTS_PATH", agents_file)
    monkeypatch.setattr("proxy.routers.agents.PID_PATH", pid_file)
    monkeypatch.setattr("proxy.routers.agents.LOG_PATH", log_file)

    return {
        "tmp_path": tmp_path,
        "workdir": workdir,
        "agents_file": agents_file,
        "pid_file": pid_file,
        "log_file": log_file,
    }


@pytest.fixture
def client(env):
    return TestClient(create_app())


class _FakeProc:
    """Fake process. Tests must never spawn a real bridge."""

    def __init__(self, *args, **kwargs):
        self.pid = 4242
        self.returncode = None

    def poll(self):
        return None


@pytest.fixture(autouse=True)
def no_real_spawn(monkeypatch):
    monkeypatch.setattr("proxy.routers.agents.subprocess.Popen", _FakeProc)


def _payload(workdir, **overrides):
    data = {
        "id": "a9",
        "name": "Nine",
        "cli": "claude",
        "model": "sonnet",
        "workdir": str(workdir),
        "permission": "edit",
        "effort": "medium",
        "tools": "edit",
        "restricted": False,
        "add_dirs": [],
    }
    data.update(overrides)
    return data


# ------------------------------------------------------------------- read

def test_list_agents_returns_agents_and_settings(client):
    data = client.get("/api/agents").json()
    assert [a["id"] for a in data["agents"]] == ["a1"]
    assert data["settings"]["token"] == "tok-123"
    assert data["settings"]["default_agent"] == "a1"
    assert data["warnings"] == []


# ------------------------------------------------------------------ write

def test_create_agent(client, env):
    resp = client.post("/api/agents", json=_payload(env["workdir"]))
    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()["agents"]]
    assert ids == ["a1", "a9"]


def test_upsert_replaces_instead_of_duplicating(client, env):
    client.post("/api/agents", json=_payload(env["workdir"]))
    resp = client.post("/api/agents", json=_payload(env["workdir"], name="Renamed"))
    ids = [a["id"] for a in resp.json()["agents"]]
    assert ids.count("a9") == 1
    assert [a for a in resp.json()["agents"] if a["id"] == "a9"][0]["name"] == "Renamed"


def test_invalid_id_rejected(client, env):
    resp = client.post("/api/agents", json=_payload(env["workdir"], id="Bad ID!"))
    assert resp.status_code == 400
    assert "invalid id" in resp.json()["detail"]


def test_invalid_permission_rejected(client, env):
    resp = client.post("/api/agents", json=_payload(env["workdir"], permission="whatever"))
    assert resp.status_code == 400
    assert "invalid permission" in resp.json()["detail"]


def test_workdir_outside_allowed_roots_rejected(client, env):
    outside = env["tmp_path"].parent / "outside-project"
    outside.mkdir(exist_ok=True)
    resp = client.post("/api/agents", json=_payload(outside))
    assert resp.status_code == 400
    assert "outside allowed_roots" in resp.json()["detail"]


def test_bypass_rejected_while_disallowed(client, env):
    resp = client.post("/api/agents", json=_payload(env["workdir"], permission="bypass"))
    assert resp.status_code == 400
    assert "bypass" in resp.json()["detail"]


def test_bypass_allowed_after_flag_enabled(client, env):
    client.post("/api/agents/settings", json={"allow_bypass": True})
    resp = client.post("/api/agents", json=_payload(env["workdir"], permission="bypass"))
    assert resp.status_code == 200


def test_delete_agent(client, env):
    client.post("/api/agents", json=_payload(env["workdir"]))
    resp = client.delete("/api/agents/a9")
    assert resp.status_code == 200
    assert [a["id"] for a in resp.json()["agents"]] == ["a1"]


def test_delete_unknown_agent_404(client):
    assert client.delete("/api/agents/nope").status_code == 404


def test_delete_default_agent_reassigns_default(client, env):
    client.post("/api/agents", json=_payload(env["workdir"]))
    resp = client.delete("/api/agents/a1")
    assert resp.json()["agents"][0]["id"] == "a9"
    assert client.get("/api/agents").json()["settings"]["default_agent"] == "a9"


def test_delete_last_agent_clears_default(client):
    client.delete("/api/agents/a1")
    assert client.get("/api/agents").json()["settings"]["default_agent"] is None


# --------------------------------------------------------------- settings

def test_update_settings_roots(client, env):
    roots = [str(env["tmp_path"]), str(env["tmp_path"].parent)]
    resp = client.post("/api/agents/settings", json={"allowed_roots": roots})
    assert resp.status_code == 200
    assert resp.json()["settings"]["allowed_roots"] == roots


def test_update_settings_rejects_unknown_default_agent(client):
    resp = client.post("/api/agents/settings", json={"default_agent": "ghost"})
    assert resp.status_code == 400
    assert "default_agent" in resp.json()["detail"]


def test_settings_can_clear_default_agent(client):
    resp = client.post("/api/agents/settings", json={"default_agent": ""})
    assert resp.status_code == 200
    assert resp.json()["settings"]["default_agent"] is None


def _write_bad_config(env, roots):
    cfg = BridgeConfig(
        token="tok-123",
        allowed_roots=[str(p) for p in roots],
        agents=[AgentSpec(id="a1", name="One", cli="claude", model="opus",
                          workdir=str(env["workdir"]), permission="edit")],
    )
    env["agents_file"].write_text(json.dumps(cfg.model_dump()), encoding="utf-8")


def test_settings_refuses_to_orphan_existing_agent(client, env):
    """Changing allowed_roots must not push an existing workdir outside them."""
    resp = client.post("/api/agents/settings",
                       json={"allowed_roots": [str(env["tmp_path"] / "nested")]})
    assert resp.status_code == 400
    assert "allowed_roots" in resp.json()["detail"]


def test_list_agents_warns_about_orphaned_workdir(client, env):
    _write_bad_config(env, [env["tmp_path"] / "elsewhere"])
    data = client.get("/api/agents").json()
    assert any("allowed_roots" in w for w in data["warnings"])


# ----------------------------------------------------------- validate path

def test_validate_path_inside(client, env):
    data = client.post("/api/agents/validate-path", json={"path": str(env["workdir"])}).json()
    assert data["allowed"] is True
    assert data["exists"] is True


def test_validate_path_outside(client, env):
    outside = env["tmp_path"].parent
    data = client.post("/api/agents/validate-path", json={"path": str(outside)}).json()
    assert data["allowed"] is False


def test_validate_path_reports_missing_dir(client, env):
    data = client.post("/api/agents/validate-path",
                       json={"path": str(env["tmp_path"] / "not-there")}).json()
    assert data["allowed"] is True
    assert data["exists"] is False


# ------------------------------------------------------------------- token

def test_regenerate_token_changes_value(client):
    before = client.get("/api/agents").json()["settings"]["token"]
    resp = client.post("/api/agents/token")
    assert resp.status_code == 200
    assert resp.json()["token"] != before


# ---------------------------------------------------------------- lifecycle

def test_bridge_status_when_stopped(client):
    data = client.get("/api/bridge/status").json()
    assert data["running"] is False
    assert data["pid"] is None
    assert data["port"] == 8083
    assert data["url"].endswith("/v1")


def test_bridge_stop_when_not_running(client):
    assert client.post("/api/bridge/stop").json()["status"] == "not-running"


def test_bridge_logs_empty(client):
    data = client.get("/api/bridge/logs").json()
    assert "(no log yet)" in data["lines"]


def test_bridge_start_refuses_invalid_config(client, env):
    """An invalid configuration must be refused BEFORE any process is spawned."""
    _write_bad_config(env, [env["tmp_path"] / "elsewhere"])
    resp = client.post("/api/bridge/start")
    assert resp.status_code == 400
    assert "Configuration is not valid" in resp.json()["detail"]
    assert not env["pid_file"].exists()


def test_bridge_start_spawns_and_records_pid(client, env):
    resp = client.post("/api/bridge/start")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert env["pid_file"].read_text(encoding="utf-8") == "4242"


def test_bridge_status_clears_stale_pid(client, env):
    env["pid_file"].write_text("999999", encoding="utf-8")
    data = client.get("/api/bridge/status").json()
    assert data["running"] is False
    assert not env["pid_file"].exists()
