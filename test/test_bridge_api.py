import json

import pytest
from fastapi.testclient import TestClient

from bridge import app as bridge_app
from bridge import sessions as bridge_sessions
from bridge.config import MODEL_ID, AgentSpec, BridgeConfig
from bridge.runner import RunError

TOKEN = "test-token-abc"

SAMPLE_EVENTS = [
    {"type": "system", "subtype": "init", "session_id": "sess-1", "cwd": "/x"},
    {
        "type": "assistant",
        "session_id": "sess-1",
        "message": {"content": [
            {"type": "text", "text": "Đã sửa xong."},
            {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "a.py"}},
        ]},
    },
    {
        "type": "result",
        "subtype": "success",
        "session_id": "sess-1",
        "is_error": False,
        "result": "Đã sửa xong.",
        "total_cost_usd": 0.02,
        "duration_ms": 3000,
        "num_turns": 2,
    },
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    workdir = tmp_path / "proj"
    workdir.mkdir()
    agents_file = tmp_path / "agents.json"
    state_file = tmp_path / "state.json"

    cfg = BridgeConfig(
        token=TOKEN,
        allowed_roots=[str(tmp_path)],
        default_agent="a1",
        turn_timeout_sec=30,
        agents=[
            AgentSpec(id="a1", name="Agent One", cli="claude", model="opus",
                      workdir=str(workdir), permission="edit", effort="high", tools="edit"),
            AgentSpec(id="a2", name="Agent Two", cli="codex", model="gpt-5.6",
                      workdir=str(workdir), permission="edit"),
            AgentSpec(id="a3", name="Agent Three", cli="claude", model="sonnet",
                      workdir=str(workdir), permission="plan", effort="low", tools="readonly"),
        ],
    )
    agents_file.write_text(json.dumps(cfg.model_dump()), encoding="utf-8")

    monkeypatch.setattr("bridge.config.AGENTS_PATH", agents_file)
    monkeypatch.setattr("bridge.sessions.STATE_PATH", state_file)

    bridge_sessions.store._data = {}
    bridge_app.LIMITER.active = 0
    bridge_app.LIMITER.limit = 2

    return {"cfg": cfg, "workdir": workdir, "state_file": state_file}


@pytest.fixture
def client(env):
    return TestClient(bridge_app.create_app())


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def fake_runner(monkeypatch):
    """Swap run_cli for a generator that emits sample events, recording the argv used."""
    calls = []

    def _install(events=None):
        events = SAMPLE_EVENTS if events is None else events

        async def _fake(cfg, argv, cwd, prompt, chat_key, agent_id, turns):
            calls.append({"argv": argv, "cwd": cwd, "prompt": prompt,
                          "chat_key": chat_key, "agent_id": agent_id})
            for event in events:
                yield event

        monkeypatch.setattr(bridge_app, "run_cli", _fake)
        return calls

    return _install


def _chat(client, auth, content, chat_id=None, stream=False):
    headers = dict(auth)
    if chat_id:
        headers["X-OpenWebUI-Chat-Id"] = chat_id
    return client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": MODEL_ID, "messages": [{"role": "user", "content": content}], "stream": stream},
    )


# --------------------------------------------------------------------- auth

def test_models_requires_token(client):
    assert client.get("/v1/models").status_code == 401


def test_models_rejects_wrong_token(client):
    resp = client.get("/v1/models", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"


def test_chat_requires_token(client):
    resp = client.post("/v1/chat/completions", json={"model": MODEL_ID, "messages": []})
    assert resp.status_code == 401


# ------------------------------------------------------------------- models

def test_exactly_one_model_is_exposed(client, auth):
    data = client.get("/v1/models", headers=auth).json()
    assert data["object"] == "list"
    assert len(data["data"]) == 1
    assert data["data"][0]["id"] == MODEL_ID


def test_health(client, auth):
    data = client.get("/health").json()
    assert data["status"] == "ok"
    assert data["agents"] == 3


# --------------------------------------------------------------- directives

def test_agents_directive_lists_configured_agents(client, auth):
    resp = _chat(client, auth, "/agents")
    assert resp.status_code == 200
    body = resp.json()["choices"][0]["message"]["content"]
    assert "a1" in body and "a2" in body and "a3" in body
    assert "· default" in body


def test_start_unknown_agent_lists_options(client, auth):
    body = _chat(client, auth, "/start nope").json()["choices"][0]["message"]["content"]
    assert "No agent named `nope`" in body
    assert "a1" in body


def test_start_agent_confirms_configuration(client, auth):
    body = _chat(client, auth, "/start a2").json()["choices"][0]["message"]["content"]
    assert "a2" in body
    assert "codex" in body


def test_stop_unbinds_session(client, auth):
    _chat(client, auth, "/start a1", chat_id="chat-1")
    assert bridge_sessions.store.get("chat-1") is not None

    body = _chat(client, auth, "/stop", chat_id="chat-1").json()["choices"][0]["message"]["content"]
    assert "Agent stopped" in body or "Nothing was running" in body
    assert bridge_sessions.store.get("chat-1") is None


# ---------------------------------------------------------------- run a turn

def test_prompt_runs_bound_agent(client, auth, fake_runner):
    calls = fake_runner()
    _chat(client, auth, "/start a1", chat_id="chat-1")

    body = _chat(client, auth, "sửa file giúp tao", chat_id="chat-1").json()
    assert len(calls) == 1
    assert calls[0]["agent_id"] == "a1"
    assert calls[0]["chat_key"] == "chat-1"
    # prompt travels over stdin, never in argv
    assert calls[0]["prompt"] == "sửa file giúp tao"
    assert "sửa file giúp tao" not in calls[0]["argv"]

    content = body["choices"][0]["message"]["content"]
    assert "Đã sửa xong." in content
    assert "Edit" in content
    assert "$0.0200" in content


def test_prompt_auto_starts_default_agent(client, auth, fake_runner):
    calls = fake_runner()
    _chat(client, auth, "làm gì đó", chat_id="chat-9")
    assert len(calls) == 1
    assert calls[0]["agent_id"] == "a1"


def test_session_id_is_persisted_for_resume(client, auth, fake_runner, env):
    fake_runner()
    _chat(client, auth, "/start a1", chat_id="chat-1")
    _chat(client, auth, "lượt một", chat_id="chat-1")

    entry = bridge_sessions.store.get("chat-1")
    assert entry["session_id"] == "sess-1"

    # the next turn must resume the same CLI session
    _chat(client, auth, "lượt hai", chat_id="chat-1")
    state = json.loads(env["state_file"].read_text(encoding="utf-8"))
    assert state["chat-1"]["session_id"] == "sess-1"


def test_resume_flag_used_on_second_turn(client, auth, fake_runner):
    calls = fake_runner()
    _chat(client, auth, "/start a1", chat_id="chat-1")
    _chat(client, auth, "lượt một", chat_id="chat-1")
    _chat(client, auth, "lượt hai", chat_id="chat-1")

    assert "--resume" in calls[1]["argv"]
    assert "--session-id" not in calls[1]["argv"]


def test_chats_do_not_share_state(client, auth, fake_runner):
    calls = fake_runner()
    _chat(client, auth, "/start a3", chat_id="chat-A")
    _chat(client, auth, "việc của A", chat_id="chat-A")
    _chat(client, auth, "việc của B", chat_id="chat-B")

    assert calls[0]["agent_id"] == "a3"
    assert calls[0]["chat_key"] == "chat-A"
    # chat-B has nothing bound -> auto default a1, must not reuse chat-A's agent
    assert calls[1]["agent_id"] == "a1"
    assert calls[1]["chat_key"] == "chat-B"


def test_codex_agent_reports_phase_two(client, auth, fake_runner):
    calls = fake_runner()
    _chat(client, auth, "/start a2", chat_id="chat-1")
    content = _chat(client, auth, "chạy đi", chat_id="chat-1").json()["choices"][0]["message"]["content"]
    assert "Phase 2" in content
    assert calls == []


def test_missing_default_agent_returns_help(client, auth, tmp_path, monkeypatch):
    from bridge.config import BridgeConfig as Cfg

    cfg = Cfg(token=TOKEN, allowed_roots=[str(tmp_path)], default_agent=None, agents=[])
    monkeypatch.setattr("bridge.config.AGENTS_PATH", tmp_path / "agents.json")
    (tmp_path / "agents.json").write_text(json.dumps(cfg.model_dump()), encoding="utf-8")

    body = _chat(client, auth, "xin chào").json()["choices"][0]["message"]["content"]
    assert "No agent bound" in body


def test_resume_failure_falls_back_to_a_new_session(client, auth, monkeypatch):
    """A stale session id must not brick the chat: retry once without --resume."""
    calls = []

    async def _fake(cfg, argv, cwd, prompt, chat_key, agent_id, turns):
        calls.append(argv)
        if "--resume" in argv:
            raise RunError("session not found")
        for event in SAMPLE_EVENTS:
            yield event

    monkeypatch.setattr(bridge_app, "run_cli", _fake)

    _chat(client, auth, "/start a1", chat_id="chat-1")
    _chat(client, auth, "first turn", chat_id="chat-1")

    body = _chat(client, auth, "second turn", chat_id="chat-1").json()["choices"][0]["message"]["content"]

    assert "could not resume" in body
    assert "Đã sửa xong." in body
    assert "--resume" in calls[1]
    assert "--resume" not in calls[2]
    # the fresh run re-captured a session id, so later turns resume again
    assert bridge_sessions.store.get("chat-1")["session_id"] == "sess-1"


def test_first_turn_failure_is_reported_without_retry(client, auth, monkeypatch):
    calls = []

    async def _fake(cfg, argv, cwd, prompt, chat_key, agent_id, turns):
        calls.append(argv)
        raise RunError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(bridge_app, "run_cli", _fake)

    _chat(client, auth, "/start a1", chat_id="chat-1")
    body = _chat(client, auth, "hi", chat_id="chat-1").json()["choices"][0]["message"]["content"]

    assert "boom" in body
    assert "could not resume" not in body
    assert len(calls) == 1


# ------------------------------------------------------------------- stream

def test_streaming_shape(client, auth, fake_runner):
    fake_runner()
    _chat(client, auth, "/start a1", chat_id="chat-1")
    resp = _chat(client, auth, "chạy", chat_id="chat-1", stream=True)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert '"role":"assistant"' in body.replace(" ", "")
    assert "Đã sửa xong." in body
    assert '"finish_reason":"stop"' in body.replace(" ", "")
    assert body.rstrip().endswith("data: [DONE]")


# ---------------------------------------------------------------- limiter

def test_limiter_blocks_when_full(client, auth, fake_runner):
    fake_runner()
    bridge_app.LIMITER.limit = 1
    bridge_app.LIMITER.active = 1  # simulate one turn already running

    body = _chat(client, auth, "chạy", chat_id="chat-1").json()["choices"][0]["message"]["content"]
    assert "already running" in body or "limit" in body
