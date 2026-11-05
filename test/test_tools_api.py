import json

import pytest
from fastapi.testclient import TestClient

from config import secrets as secrets_store
from config import tools as tools_store
from proxy.app import create_app


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "tools"
    registry = tmp_path / "tools.json"
    logs = tmp_path / "logs"
    data = tmp_path / "data"
    runtime = tmp_path / "runtime.json"
    secrets_path = tmp_path / "secrets.json"
    monkeypatch.setattr("config.tools._get_tools_root", lambda: str(root))
    monkeypatch.setattr("config.tools._get_registry_path", lambda: str(registry))
    monkeypatch.setattr("config.tools._get_logs_dir", lambda: str(logs))
    monkeypatch.setattr("config.tools._get_data_root", lambda: str(data))
    monkeypatch.setattr("config.tools._get_runtime_path", lambda: str(runtime))
    monkeypatch.setattr("config.secrets._get_secrets_path", lambda: str(secrets_path))
    tools_store.invalidate_tools_cache()
    yield {"root": root, "registry": registry, "logs": logs, "data": data, "runtime": runtime, "secrets": secrets_path}
    tools_store.invalidate_tools_cache()


@pytest.fixture
def client(env):
    return TestClient(create_app())


def _tool_dir(tmp_path, tool_id="git-demo", name="Git demo"):
    directory = tmp_path / "src" / tool_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "tool.json").write_text(json.dumps({
        "id": tool_id,
        "name": name,
        "version": "0.1.0",
        "description": "Demo",
        "start": ["{python}", "-m", tool_id.replace("-", "_"), "--port", "{port}"],
        "setup": ["{python}", "-c", "print('setup')"],
        "health": "/health",
        "default_port": 9200,
        "env": {
            "GITHUB_TOKEN": "tool:github",
            "TOOL_DIR": "tool:dir",
            "KEY_ROUTER_TOOL_DATA": "tool:data",
            "KEY_ROUTER_URL": "keyrouter:url",
        },
    }), encoding="utf-8")
    return directory


def test_list_tools_empty(client, env):
    response = client.get("/api/tools")
    assert response.status_code == 200
    data = response.json()
    assert data["installed"] == []
    assert data["tools_root"] == str(env["root"])
    assert "credentials" not in data


def _set_session_token(tool_id="git-demo", token="tool-secret"):
    runtime = tools_store.load_runtime()
    runtime["tools"].setdefault(tool_id, {})["session_token"] = token
    tools_store.save_runtime(runtime)


def test_tool_credentials_are_scoped_to_the_tool(client, env, tmp_path):
    source = _tool_dir(tmp_path)
    assert client.post("/api/tools/install", json={"local_path": str(source)}).status_code == 200
    _set_session_token()

    headers = {"X-Tool-Token": "tool-secret"}
    base = "/api/tools/git-demo/credentials"

    assert client.get(base).status_code == 401
    assert client.get(base, headers={"X-Tool-Token": "wrong"}).status_code == 401

    rows = client.get(base, headers=headers).json()["credentials"]
    assert [row["name"] for row in rows] == ["github"]

    raw = "ghp_supersecretvalue1234"
    saved = client.put(f"{base}/github", json={"value": raw}, headers=headers)
    assert saved.status_code == 200
    assert raw not in saved.text
    assert secrets_store.get_tool_key("github") == raw

    fetched = client.get(f"{base}/github", headers=headers)
    assert fetched.json()["value"] == raw

    assert client.get(f"{base}/nope", headers=headers).status_code == 400

    assert client.delete(f"{base}/github", headers=headers).status_code == 200
    assert secrets_store.get_tool_key("github") == ""
    assert client.delete(f"{base}/github", headers=headers).status_code == 404


def test_tool_credentials_unknown_tool(client):
    assert client.get("/api/tools/nope/credentials", headers={"X-Tool-Token": "x"}).status_code == 404


def test_install_local_logs_and_uninstall(client, env, tmp_path):
    source = _tool_dir(tmp_path)

    installed = client.post("/api/tools/install", json={"local_path": str(source)})
    assert installed.status_code == 200
    assert installed.json()["installed_tool"]["id"] == "git-demo"

    listed = client.get("/api/tools").json()
    assert [tool["id"] for tool in listed["installed"]] == ["git-demo"]

    log_file = env["logs"] / "git-demo.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("line one\nline two\n", encoding="utf-8")
    logs = client.get("/api/tools/git-demo/logs?lines=5")
    assert logs.status_code == 200
    assert "line two" in logs.json()["lines"]

    removed = client.delete("/api/tools/git-demo")
    assert removed.status_code == 200
    assert tools_store.load_registry()["tools"] == {}
    assert source.is_dir()


def test_reinstall_endpoint(client, env, monkeypatch):
    async def fake_reinstall(tool_id, repo="", branch=""):
        return {"id": tool_id, "installed": True}

    monkeypatch.setattr("proxy.tools_hub.reinstall_tool", fake_reinstall)

    response = client.post("/api/tools/git-demo/reinstall", json={})

    assert response.status_code == 200
    assert response.json()["installed_tool"]["id"] == "git-demo"


def test_reinstall_without_repo_is_400(client, env, monkeypatch):
    async def fake_reinstall(tool_id, repo="", branch=""):
        raise ValueError("No source repo recorded for this tool; provide owner/repo")

    monkeypatch.setattr("proxy.tools_hub.reinstall_tool", fake_reinstall)

    response = client.post("/api/tools/git-demo/reinstall", json={})

    assert response.status_code == 400
    assert "source repo" in response.json()["detail"]


def test_build_env_resolves_data_dir(env):
    from proxy import tools_hub

    manifest = {
        "id": "git-demo",
        "env": {
            "GITHUB_TOKEN": "tool:github",
            "KEY_ROUTER_TOOL_DATA": "tool:data",
            "KEY_ROUTER_URL": "keyrouter:url",
            "KEY_ROUTER_TOOL_TOKEN": "generated:token",
            "TOOL_DIR": "tool:dir",
        },
    }
    built = tools_hub._build_env(manifest, "/tmp/tool", 9301, "session-token")
    assert built["KEY_ROUTER_TOOL_DATA"] == str(env["data"] / "git-demo")
    assert (env["data"] / "git-demo").is_dir()
    assert built["KEY_ROUTER_TOOL_TOKEN"] == "session-token"
    assert built["TOOL_DIR"] == "/tmp/tool"


def test_install_unknown_local_path(client, env, tmp_path):
    response = client.post("/api/tools/install", json={"local_path": str(tmp_path / "missing")})
    assert response.status_code == 404


def test_start_stop_lifecycle(client, env, tmp_path, monkeypatch):
    source = _tool_dir(tmp_path)
    assert client.post("/api/tools/install", json={"local_path": str(source)}).status_code == 200

    state = {"alive": True}
    killed = []

    class FakeProc:
        pid = 4242

        def poll(self):
            return None

    class FakeResponse:
        status_code = 200

    def fake_kill(pid, sig=None):
        killed.append((pid, sig))
        state["alive"] = False

    monkeypatch.setattr("proxy.tools_hub.subprocess.Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr("proxy.tools_hub.pid_alive", lambda pid: state["alive"])
    monkeypatch.setattr("proxy.tools_hub.kill_tree", fake_kill)
    monkeypatch.setattr("proxy.tools_hub.reap", lambda pid: None)
    monkeypatch.setattr("proxy.tools_hub._wait_healthy", lambda *args: True)
    monkeypatch.setattr("proxy.tools_hub.allocate_port", lambda default: 9301)
    monkeypatch.setattr("proxy.tools_hub.httpx.get", lambda *args, **kwargs: FakeResponse())

    started = client.post("/api/tools/git-demo/start")
    assert started.status_code == 200
    body = started.json()
    assert body["running"] is True
    assert body["pid"] == 4242
    assert body["port"] == 9301
    assert "token=" in body["frame_url"]

    entry = tools_store.load_registry()["tools"]["git-demo"]
    assert "pid" not in entry
    runtime_entry = tools_store.load_runtime()["tools"]["git-demo"]
    assert runtime_entry["pid"] == 4242
    assert runtime_entry["port"] == 9301

    stopped = client.post("/api/tools/git-demo/stop")
    assert stopped.status_code == 200
    assert killed and killed[0][0] == 4242
    assert "pid" not in tools_store.load_runtime()["tools"].get("git-demo", {})


def test_start_can_reuse_a_session_token(env, client, tmp_path, monkeypatch):
    from proxy import tools_hub

    source = _tool_dir(tmp_path)
    assert client.post("/api/tools/install", json={"local_path": str(source)}).status_code == 200

    class FakeProc:
        pid = 4243

        def poll(self):
            return None

    class FakeResponse:
        status_code = 200

    monkeypatch.setattr("proxy.tools_hub.subprocess.Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr("proxy.tools_hub.pid_alive", lambda pid: True)
    monkeypatch.setattr("proxy.tools_hub.kill_tree", lambda *args, **kwargs: None)
    monkeypatch.setattr("proxy.tools_hub.reap", lambda pid: None)
    monkeypatch.setattr("proxy.tools_hub._wait_healthy", lambda *args: True)
    monkeypatch.setattr("proxy.tools_hub.allocate_port", lambda default: 9302)
    monkeypatch.setattr("proxy.tools_hub.httpx.get", lambda *args, **kwargs: FakeResponse())

    tools_hub.start_tool("git-demo", session_token="fixed-token")

    assert "session_token" not in tools_store.load_registry()["tools"]["git-demo"]
    assert tools_store.load_runtime()["tools"]["git-demo"]["session_token"] == "fixed-token"


def test_start_unknown_tool(client, env):
    assert client.post("/api/tools/nope/start").status_code == 404
    assert client.post("/api/tools/nope/setup").status_code == 404
    assert client.get("/api/tools/nope/logs").status_code == 404


def test_setup_without_command(client, env, tmp_path):
    directory = tmp_path / "src" / "plain-tool"
    directory.mkdir(parents=True)
    (directory / "tool.json").write_text(json.dumps({
        "id": "plain-tool",
        "name": "Plain",
        "start": ["{python}", "-c", "pass"],
    }), encoding="utf-8")
    assert client.post("/api/tools/install", json={"local_path": str(directory)}).status_code == 200
    response = client.post("/api/tools/plain-tool/setup")
    assert response.status_code == 200
    assert response.json()["status"] == "no-setup"
