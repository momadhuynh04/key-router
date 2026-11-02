import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxy.app import create_app


def write_skill(root: Path, slug: str, name: str = "", description: str = "desc") -> Path:
    path = root / slug
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name or slug}\ndescription: {description}\n---\n\n# {name or slug}\n",
        encoding="utf-8",
    )
    return path


def fake_environment():
    return {
        "node": {"version": "v26.5.1", "ok": True, "required": "22.20"},
        "npx": True,
        "install_supported": True,
        "cli_spec": "skills@1.7.0",
        "providers": {
            "claude": {"detected": True, "roots": []},
            "codex": {"detected": True, "roots": []},
        },
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(
        json.dumps({"token": "t", "allowed_roots": [str(tmp_path)]}),
        encoding="utf-8",
    )

    monkeypatch.setattr("bridge.config.AGENTS_PATH", agents_file)
    monkeypatch.setattr("config.skills.get_home", lambda: home)
    monkeypatch.setattr("config.skills.environment", lambda home=None: fake_environment())
    return {"tmp_path": tmp_path, "home": home, "project": project}


@pytest.fixture
def client(env):
    return TestClient(create_app())


# ------------------------------------------------------------------- list

def test_list_skills_global_and_project(client, env):
    write_skill(env["home"] / ".claude" / "skills", "alpha", description="Alpha")
    write_skill(env["home"] / ".agents" / "skills", "beta", description="Beta")
    write_skill(env["project"] / ".claude" / "skills", "proj-skill", description="Project")

    resp = client.get("/api/skills", params={"project": str(env["project"])})
    assert resp.status_code == 200
    data = resp.json()
    assert data["project"]["valid"] is True
    assert data["environment"]["node"]["ok"] is True

    claude_global = {s["id"] for s in data["skills"]["claude"]["global"]}
    claude_project = {s["id"] for s in data["skills"]["claude"]["project"]}
    codex_global = {s["id"] for s in data["skills"]["codex"]["global"]}
    assert claude_global == {"alpha"}
    assert claude_project == {"proj-skill"}
    assert codex_global == {"beta"}


def test_list_invalid_project_returns_reason(client, env):
    resp = client.get("/api/skills", params={"project": "/etc/passwd"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["project"]["valid"] is False
    assert data["project"]["error"]
    assert data["skills"]["claude"]["project"] == []


# ------------------------------------------------------------- marketplace

def test_marketplace_short_query_returns_suggestions(client):
    resp = client.get("/api/skills/marketplace", params={"q": "a"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["skills"] == []
    assert data["suggestions"]


def test_marketplace_search_uses_helper(client, monkeypatch):
    calls = []

    async def fake_fetch(q, limit):
        calls.append((q, limit))
        return ([{"id": "owner/repo/skill", "source": "owner/repo", "name": "skill", "installs": 3}], False, [])

    monkeypatch.setattr("proxy.routers.skills._fetch_marketplace", fake_fetch)
    resp = client.get("/api/skills/marketplace", params={"q": "react", "limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert data["skills"][0]["name"] == "skill"
    assert calls == [("react", 5)]


# ----------------------------------------------------------------- install

def test_install_calls_domain(client, env, monkeypatch):
    captured = {}

    def fake_install(source, skill, providers, scope, project):
        captured.update({
            "source": source,
            "skill": skill,
            "providers": providers,
            "scope": scope,
            "project": project,
        })
        return {"status": "success", "installed": [], "warnings": []}

    monkeypatch.setattr("config.skills.install_skill", fake_install)
    resp = client.post("/api/skills/install", json={
        "source": "owner/repo",
        "skill": "my-skill",
        "scope": "project",
        "project": str(env["project"]),
        "providers": ["claude", "codex"],
    })
    assert resp.status_code == 200
    assert captured == {
        "source": "owner/repo",
        "skill": "my-skill",
        "providers": ["claude", "codex"],
        "scope": "project",
        "project": str(env["project"]),
    }


def test_install_validation_and_failure_statuses(client, monkeypatch):
    def bad_install(*args, **kwargs):
        raise ValueError("bad source")

    monkeypatch.setattr("config.skills.install_skill", bad_install)
    resp = client.post("/api/skills/install", json={"source": "-bad", "skill": "x"})
    assert resp.status_code == 400

    def timeout_install(*args, **kwargs):
        raise TimeoutError("too slow")

    monkeypatch.setattr("config.skills.install_skill", timeout_install)
    resp = client.post("/api/skills/install", json={"source": "owner/repo", "skill": "x"})
    assert resp.status_code == 504


# ------------------------------------------------------------ toggle/read

def test_toggle_and_content(client, env):
    write_skill(env["home"] / ".claude" / "skills", "alpha", description="Alpha")

    resp = client.post("/api/skills/toggle", json={
        "provider": "claude",
        "scope": "global",
        "id": "alpha",
        "enabled": False,
    })
    assert resp.status_code == 200
    assert resp.json()["skill"]["enabled"] is False

    resp = client.get("/api/skills/content", params={
        "provider": "claude",
        "scope": "global",
        "id": "alpha",
    })
    assert resp.status_code == 200
    assert "name: alpha" in resp.json()["content"]


def test_toggle_read_only_system_skill(client, env):
    write_skill(env["home"] / ".codex" / "skills" / ".system", "system-skill")
    resp = client.post("/api/skills/toggle", json={
        "provider": "codex",
        "scope": "global",
        "id": "system-skill",
        "enabled": False,
    })
    assert resp.status_code == 400
    assert "read-only" in resp.json()["detail"]


def test_content_missing_returns_404(client):
    resp = client.get("/api/skills/content", params={
        "provider": "claude",
        "scope": "global",
        "id": "missing",
    })
    assert resp.status_code == 404


# ---------------------------------------------------------------- delete

def test_delete_skill(client, env, monkeypatch):
    write_skill(env["home"] / ".claude" / "skills", "alpha")
    removed = []

    def fake_remove(provider, scope, project, skill_id):
        removed.append((provider, scope, skill_id))
        return {"removed": ["/tmp/alpha"], "via": "cli"}

    monkeypatch.setattr("config.skills.remove_skill", fake_remove)
    resp = client.delete("/api/skills/claude/global/alpha")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert removed == [("claude", "global", "alpha")]


def test_delete_read_only_and_missing(client, env):
    write_skill(env["home"] / ".codex" / "skills" / ".system", "system-skill")
    resp = client.delete("/api/skills/codex/global/system-skill")
    assert resp.status_code == 400
    assert "read-only" in resp.json()["detail"]

    resp = client.delete("/api/skills/claude/global/missing")
    assert resp.status_code == 404
