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
    bridge_sessions.store._meta = {}
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


def _chat_messages(client, auth, messages, chat_id):
    headers = dict(auth)
    headers["X-OpenWebUI-Chat-Id"] = chat_id
    return client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": MODEL_ID, "messages": messages, "stream": False},
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


# ------------------------------------------------------------------- images

DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def test_image_part_is_forwarded_as_stream_json(client, auth, fake_runner):
    calls = fake_runner()
    _chat(client, auth, "/start a1", chat_id="chat-1")
    _chat(
        client,
        auth,
        [
            {"type": "text", "text": "xem ảnh này"},
            {"type": "image_url", "image_url": {"url": DATA_URL}},
        ],
        chat_id="chat-1",
    )

    assert "--input-format" in calls[0]["argv"]
    payload = json.loads(calls[0]["prompt"])
    content = payload["message"]["content"]
    assert content[0] == {"type": "text", "text": "xem ảnh này"}
    assert content[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgoAAAANSUhEUg=="},
    }


def test_image_only_message_still_runs(client, auth, fake_runner):
    calls = fake_runner()
    _chat(client, auth, [{"type": "image_url", "image_url": {"url": DATA_URL}}], chat_id="chat-1")

    assert len(calls) == 1
    content = json.loads(calls[0]["prompt"])["message"]["content"]
    assert content[0]["type"] == "image"


def test_remote_image_url_is_passed_through(client, auth, fake_runner):
    calls = fake_runner()
    _chat(
        client,
        auth,
        [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}],
        chat_id="chat-1",
    )

    block = json.loads(calls[0]["prompt"])["message"]["content"][0]
    assert block == {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}}


def test_text_only_turn_keeps_plain_stdin(client, auth, fake_runner):
    calls = fake_runner()
    _chat(client, auth, "chỉ chữ thôi", chat_id="chat-1")

    assert "--input-format" not in calls[0]["argv"]
    assert calls[0]["prompt"] == "chỉ chữ thôi"


ASK_EVENTS = [
    {"type": "system", "subtype": "init", "session_id": "sess-1", "cwd": "/x"},
    {
        "type": "assistant",
        "session_id": "sess-1",
        "message": {"content": [{
            "type": "tool_use",
            "id": "q1",
            "name": "mcp__bridge-ask__ask_user",
            "input": {
                "question": "Chọn DB nào?",
                "options": [{"label": "Postgres"}, {"label": "SQLite"}],
            },
        }]},
    },
    {
        "type": "user",
        "session_id": "sess-1",
        "message": {"content": [{
            "type": "tool_result",
            "tool_use_id": "q1",
            "content": "Question shown to the user in the chat.",
        }]},
    },
    {
        "type": "result",
        "subtype": "success",
        "session_id": "sess-1",
        "is_error": False,
        "result": "",
    },
]


def test_ask_user_is_rendered_and_queued(client, auth, monkeypatch):
    calls = []

    async def _fake(cfg, argv, cwd, prompt, chat_key, agent_id, turns):
        calls.append(prompt)
        events = ASK_EVENTS if len(calls) == 1 else SAMPLE_EVENTS
        for event in events:
            yield event

    monkeypatch.setattr(bridge_app, "run_cli", _fake)

    _chat(client, auth, "/start a1", chat_id="chat-1")
    body = _chat(client, auth, "làm gì đó", chat_id="chat-1").json()["choices"][0]["message"]["content"]

    assert "Chọn DB nào?" in body
    assert "1. **Postgres**" in body
    assert "Question shown to the user" not in body
    pending = bridge_sessions.store.get_pending("chat-1")
    assert pending["question"] == "Chọn DB nào?"
    assert [o["label"] for o in pending["options"]] == ["Postgres", "SQLite"]


def test_next_message_is_framed_as_the_answer(client, auth, monkeypatch):
    calls = []

    async def _fake(cfg, argv, cwd, prompt, chat_key, agent_id, turns):
        calls.append(prompt)
        events = ASK_EVENTS if len(calls) == 1 else SAMPLE_EVENTS
        for event in events:
            yield event

    monkeypatch.setattr(bridge_app, "run_cli", _fake)

    _chat(client, auth, "/start a1", chat_id="chat-1")
    _chat(client, auth, "làm gì đó", chat_id="chat-1")
    _chat(client, auth, "2", chat_id="chat-1")

    second = calls[1]
    assert second.startswith("[User answered AskUserQuestion]")
    assert "Question: Chọn DB nào?" in second
    assert "Options: 1) Postgres; 2) SQLite" in second
    assert second.endswith("Answer: 2")
    assert bridge_sessions.store.get_pending("chat-1") is None


def test_plain_message_is_not_framed_without_a_pending_question(client, auth, fake_runner):
    calls = fake_runner()
    _chat(client, auth, "câu hỏi bình thường", chat_id="chat-1")

    assert calls[0]["prompt"] == "câu hỏi bình thường"


def test_restarting_an_agent_clears_a_pending_question(client, auth, monkeypatch):
    async def _fake(cfg, argv, cwd, prompt, chat_key, agent_id, turns):
        for event in ASK_EVENTS:
            yield event

    monkeypatch.setattr(bridge_app, "run_cli", _fake)

    _chat(client, auth, "/start a1", chat_id="chat-1")
    _chat(client, auth, "làm gì đó", chat_id="chat-1")
    assert bridge_sessions.store.get_pending("chat-1") is not None

    _chat(client, auth, "/start a1", chat_id="chat-1")
    assert bridge_sessions.store.get_pending("chat-1") is None


# -------------------------------------------------------------- sub-agents

def _install_sequence(monkeypatch, sequence):
    """Fake run_cli that walks a different event list per call."""
    calls = []

    async def _fake(cfg, argv, cwd, prompt, chat_key, agent_id, turns):
        calls.append({"argv": argv, "prompt": prompt, "agent_id": agent_id})
        events = sequence[min(len(calls) - 1, len(sequence) - 1)]
        for event in events:
            yield event

    monkeypatch.setattr(bridge_app, "run_cli", _fake)
    return calls


def test_call_runs_sub_agent_without_rebinding(client, auth, monkeypatch):
    calls = _install_sequence(monkeypatch, [SAMPLE_EVENTS])
    _chat(client, auth, "/start a1", chat_id="chat-1")

    body = _chat(client, auth, "/call a3 xem lại code", chat_id="chat-1").json()["choices"][0]["message"]["content"]

    assert "🧩 **a3**" in body
    assert "Đã sửa xong." in body
    assert calls[0]["agent_id"] == "a3"
    # the main binding never changes
    assert bridge_sessions.store.get("chat-1")["agent_id"] == "a1"
    assert bridge_sessions.store.get("chat-1")["session_id"] is None


def test_call_sub_agent_sees_chat_history(client, auth, monkeypatch):
    calls = _install_sequence(monkeypatch, [SAMPLE_EVENTS])
    messages = [
        {"role": "user", "content": "/start a1"},
        {"role": "assistant", "content": "✅ Bound agent `a1`."},
        {"role": "user", "content": "sửa file giúp tao"},
        {"role": "assistant", "content": "Đã sửa xong."},
        {"role": "user", "content": "/call a3 xem lại giúp tao"},
    ]
    _chat_messages(client, auth, messages, "chat-1")

    prompt = calls[0]["prompt"]
    assert "user: sửa file giúp tao" in prompt
    assert "assistant: Đã sửa xong." in prompt
    assert "[Task from the user]" in prompt
    assert "xem lại giúp tao" in prompt
    assert "/call a3" not in prompt


def test_sub_agent_handoff_reaches_main_agent(client, auth, monkeypatch):
    calls = _install_sequence(monkeypatch, [SAMPLE_EVENTS, SAMPLE_EVENTS])
    _chat(client, auth, "/start a1", chat_id="chat-1")
    _chat(client, auth, "/call a3 xem lại code", chat_id="chat-1")

    _chat(client, auth, "giờ làm tiếp", chat_id="chat-1")

    prompt = calls[1]["prompt"]
    assert calls[1]["agent_id"] == "a1"
    assert prompt.startswith("[Sub-agent handoff")
    assert "do not message them" in prompt
    assert "`a3`" in prompt
    assert "xem lại code" in prompt
    assert "Steps:" in prompt and "`Edit`" in prompt
    assert "Đã sửa xong." in prompt
    assert prompt.endswith("giờ làm tiếp")
    # the handoff is consumed once
    assert bridge_sessions.store.take_subruns("chat-1") == []


def test_call_works_before_the_chat_is_bound(client, auth, monkeypatch):
    calls = _install_sequence(monkeypatch, [SAMPLE_EVENTS, SAMPLE_EVENTS])

    _chat(client, auth, "/call a3 xem lại code", chat_id="chat-1")
    assert bridge_sessions.store.get("chat-1") is None

    _chat(client, auth, "giờ làm tiếp", chat_id="chat-1")

    assert calls[1]["agent_id"] == "a1"  # default agent auto-binds
    assert "[Sub-agent handoff" in calls[1]["prompt"]
    assert "xem lại code" in calls[1]["prompt"]


def test_call_without_prompt_shows_usage(client, auth, fake_runner):
    calls = fake_runner()
    body = _chat(client, auth, "/call a3", chat_id="chat-1").json()["choices"][0]["message"]["content"]
    assert "Usage: `/call <agent-id> <prompt>`" in body
    assert calls == []


def test_call_unknown_agent_lists_agents(client, auth, fake_runner):
    calls = fake_runner()
    body = _chat(client, auth, "/call nope làm gì đó", chat_id="chat-1").json()["choices"][0]["message"]["content"]
    assert "No agent named `nope`" in body
    assert "a1" in body
    assert calls == []


def test_sub_agent_ask_user_answer_returns_to_the_sub_agent(client, auth, monkeypatch):
    calls = _install_sequence(monkeypatch, [ASK_EVENTS, SAMPLE_EVENTS, SAMPLE_EVENTS])
    _chat(client, auth, "/start a1", chat_id="chat-1")
    _chat(client, auth, "/call a3 hỏi tôi đi", chat_id="chat-1")

    pending = bridge_sessions.store.get_pending("chat-1")
    assert pending["sub_agent_id"] == "a3"
    assert pending["sub_session_id"] == "sess-1"

    _chat(client, auth, "1", chat_id="chat-1")

    assert calls[1]["agent_id"] == "a3"
    assert calls[1]["prompt"].startswith("[User answered AskUserQuestion]")
    assert "Answer: 1" in calls[1]["prompt"]
    assert "--resume" in calls[1]["argv"]

    _chat(client, auth, "giờ làm tiếp", chat_id="chat-1")
    assert calls[2]["agent_id"] == "a1"
    assert "[Sub-agent handoff" in calls[2]["prompt"]


def test_handoff_renders_all_steps():
    runs = [{
        "agent_id": "a1",
        "workdir": "/x",
        "task": "t",
        "ok": True,
        "result": "r",
        "steps": [{"tool": "Bash", "input": f"cmd-{i}"} for i in range(20)],
    }]
    text = bridge_app._handoff_prefix(runs)
    assert "cmd-0" in text
    assert "cmd-19" in text
    assert "earlier" not in text


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
