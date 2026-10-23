import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from config.model_map import ModelMapper
from models.anthropic import AnthropicRequest, AnthropicResponse, AnthropicUsage, Message
from models.events import SSEEvent
from models.openai_compat import OpenAIRequest
from provider.openai_base import OpenAIBaseProvider, sanitize_response_format
from proxy.openai_ingress import openai_chat_to_anthropic
from proxy.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. ModelMapper — "adcraft" resolves by exact key only
# ---------------------------------------------------------------------------

@pytest.fixture
def mapper(tmp_path):
    return ModelMapper(config_path=str(tmp_path / "config.json"))


def test_adcraft_resolves_by_exact_key(mapper):
    mapper.set_mapping("adcraft", "openrouter/nex-agi/nex-n2.5-pro:free")
    assert mapper.resolve("adcraft") == ("openrouter", "nex-agi/nex-n2.5-pro:free")


def test_adcraft_ignores_family_fallback(mapper):
    # A 'codex' key is present, but 'adcraft' must never be caught by the
    # gpt/codex regex — an unmapped adcraft raises instead.
    mapper.set_mapping("codex", "openrouter/some-codex-model")
    with pytest.raises(ValueError, match="No mapping found"):
        mapper.resolve("adcraft")


# ---------------------------------------------------------------------------
# 2. response_format sanitization
# ---------------------------------------------------------------------------

def test_sanitize_json_schema_downgraded_to_json_object():
    result = sanitize_response_format({"type": "json_schema", "json_schema": {"name": "x", "schema": {}}})
    assert result == {"type": "json_object"}


def test_sanitize_json_object_preserved():
    assert sanitize_response_format({"type": "json_object"}) == {"type": "json_object"}


def test_sanitize_unknown_or_missing_dropped():
    assert sanitize_response_format({"type": "weird"}) is None
    assert sanitize_response_format(None) is None
    assert sanitize_response_format("json_object") is None


@pytest.mark.asyncio
async def test_translate_request_downgrades_json_schema():
    provider = OpenAIBaseProvider(base_url="http://test", api_key="k", target_model="m")
    req = AnthropicRequest(model="m", messages=[Message(role="user", content="hi")])
    req.response_format = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}}
    body = await provider.translate_request(req)
    assert body["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_translate_request_omits_response_format_when_absent():
    provider = OpenAIBaseProvider(base_url="http://test", api_key="k", target_model="m")
    req = AnthropicRequest(model="m", messages=[Message(role="user", content="hi")])
    body = await provider.translate_request(req)
    assert "response_format" not in body


# ---------------------------------------------------------------------------
# 2b. JSON mode requires the word "json" in the prompt (DeepSeek/OpenAI 400)
# ---------------------------------------------------------------------------

def _messages_text(body):
    return " ".join(
        m["content"] if isinstance(m.get("content"), str) else "" for m in body["messages"]
    ).lower()


@pytest.mark.asyncio
async def test_json_object_injects_json_hint_when_missing():
    provider = OpenAIBaseProvider(base_url="http://test", api_key="k", target_model="deepseek-chat")
    req = AnthropicRequest(model="deepseek-chat", messages=[Message(role="user", content="Submit a structured result.")])
    req.response_format = {"type": "json_object"}
    body = await provider.translate_request(req)
    assert body["response_format"] == {"type": "json_object"}
    assert "json" in _messages_text(body)


@pytest.mark.asyncio
async def test_json_object_hint_appended_to_existing_system():
    provider = OpenAIBaseProvider(base_url="http://test", api_key="k", target_model="m")
    req = AnthropicRequest(model="m", messages=[Message(role="user", content="hi")], system="Be terse.")
    req.response_format = {"type": "json_object"}
    body = await provider.translate_request(req)
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"].startswith("Be terse.")
    assert "json" in body["messages"][0]["content"].lower()


@pytest.mark.asyncio
async def test_json_object_no_hint_when_prompt_mentions_json():
    provider = OpenAIBaseProvider(base_url="http://test", api_key="k", target_model="m")
    req = AnthropicRequest(model="m", messages=[Message(role="user", content="Return JSON please.")])
    req.response_format = {"type": "json_object"}
    body = await provider.translate_request(req)
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"


# ---------------------------------------------------------------------------
# 3. Ingress carries response_format OpenAI → Anthropic internal
# ---------------------------------------------------------------------------

def test_ingress_carries_response_format():
    req = OpenAIRequest(model="adcraft", messages=[{"role": "user", "content": "hi"}],
                        response_format={"type": "json_object"})
    out = openai_chat_to_anthropic(req)
    assert getattr(out, "response_format") == {"type": "json_object"}


def test_ingress_omits_absent_response_format():
    req = OpenAIRequest(model="adcraft", messages=[{"role": "user", "content": "hi"}])
    out = openai_chat_to_anthropic(req)
    assert getattr(out, "response_format", None) is None


# ---------------------------------------------------------------------------
# 4. /v1/chat/completions with the AdCraft tool contract
# ---------------------------------------------------------------------------

def _tool_call_response(name="submit_structured_result", payload=None):
    return AnthropicResponse(
        id="msg_1", model="test",
        content=[{"type": "tool_use", "id": "toolu_abc", "name": name, "input": payload or {"title": "Test ad"}}],
        stop_reason="tool_use",
        usage=AnthropicUsage(input_tokens=5, output_tokens=8),
    )


@patch('proxy.router.provider_router.get_provider')
def test_chat_completions_returns_tool_call(mock_get_provider):
    mock_provider = MagicMock()
    mock_provider.translate_request = AsyncMock(return_value={})

    async def fake_generate(body):
        return _tool_call_response()

    mock_provider.generate = fake_generate
    mock_get_provider.return_value = mock_provider

    resp = client.post("/v1/chat/completions", json={
        "model": "adcraft",
        "messages": [{"role": "user", "content": "make an ad"}],
        "tools": [{"type": "function", "function": {"name": "submit_structured_result", "parameters": {"type": "object"}}}],
        "response_format": {"type": "json_object"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["finish_reason"] == "tool_calls"
    tc = data["choices"][0]["message"]["tool_calls"][0]
    assert tc["function"]["name"] == "submit_structured_result"
    assert json.loads(tc["function"]["arguments"]) == {"title": "Test ad"}


@patch('proxy.router.provider_router.get_provider')
def test_chat_completions_stream_retries_until_tool_call(mock_get_provider):
    """First attempt returns nothing; the agentic retry must nudge a tool call."""
    mock_provider = MagicMock()
    calls = {"n": 0}

    async def fake_translate(req):
        calls["n"] += 1
        return {"attempt": calls["n"]}

    mock_provider.translate_request = fake_translate

    async def fake_stream(body):
        if body.get("attempt") == 1:
            yield SSEEvent(event="message_start", data={"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}})
            yield SSEEvent(event="message_stop", data={"type": "message_stop"})
        else:
            yield SSEEvent(event="message_start", data={"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}})
            yield SSEEvent(event="content_block_start", data={"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_x", "name": "submit_structured_result", "input": {}}})
            yield SSEEvent(event="content_block_delta", data={"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\"title\":\"A\"}"}})
            yield SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0})
            yield SSEEvent(event="message_delta", data={"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}})
            yield SSEEvent(event="message_stop", data={"type": "message_stop"})

    mock_provider.stream = fake_stream
    mock_get_provider.return_value = mock_provider

    resp = client.post("/v1/chat/completions", json={
        "model": "adcraft",
        "messages": [{"role": "user", "content": "make an ad"}],
        "stream": True,
        "tools": [{"type": "function", "function": {"name": "submit_structured_result", "parameters": {"type": "object"}}}],
    })
    assert resp.status_code == 200
    body = resp.text.replace(" ", "")
    assert '"finish_reason":"tool_calls"' in body
    assert calls["n"] >= 2


# ---------------------------------------------------------------------------
# 5. AdCraft management endpoints
# ---------------------------------------------------------------------------

@pytest.fixture
def adcraft_mapper(tmp_path):
    m = ModelMapper(config_path=str(tmp_path / "config.json"))
    m.set_mapping("adcraft", "openrouter/nex-agi/nex-n2.5-pro:free")
    return m


def test_adcraft_status_endpoint(adcraft_mapper):
    with patch('proxy.routers.management.model_mapper', adcraft_mapper):
        resp = client.get("/api/adcraft/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["provider"] == "openrouter"
    assert data["model"] == "nex-agi/nex-n2.5-pro:free"
    assert data["endpoint"].endswith("/v1")
    assert data["chat_completions_url"].endswith("/v1/chat/completions")
    assert data["api_key"] == "key-router"
    assert data["api_key_env"] == "KEY_ROUTER_API_KEY"


def test_adcraft_set_model_endpoint(adcraft_mapper):
    with patch('proxy.routers.management.model_mapper', adcraft_mapper):
        resp = client.post("/api/adcraft/model", json={"target": "deepseekplatform/deepseek-chat"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["model"] == "deepseek-chat"
    assert adcraft_mapper.get_all()["adcraft"] == "deepseekplatform/deepseek-chat"


def test_adcraft_set_model_rejects_bad_target(adcraft_mapper):
    with patch('proxy.routers.management.model_mapper', adcraft_mapper):
        resp = client.post("/api/adcraft/model", json={"target": "no-slash"})
    assert resp.status_code == 400


@patch('proxy.router.provider_router.get_provider')
def test_adcraft_test_endpoint_success(mock_get_provider, adcraft_mapper):
    mock_provider = MagicMock()
    mock_provider.target_model = "nex-agi/nex-n2.5-pro:free"
    mock_provider.translate_request = AsyncMock(return_value={"response_format": {"type": "json_object"}})

    async def fake_generate(body):
        return _tool_call_response()

    mock_provider.generate = fake_generate
    mock_get_provider.return_value = mock_provider

    with patch('proxy.routers.management.model_mapper', adcraft_mapper):
        resp = client.post("/api/adcraft/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["tool_called"] is True
    assert data["finish_reason"] == "tool_calls"
    assert data["response_format_sent"] == {"type": "json_object"}


def test_adcraft_test_endpoint_unconfigured(tmp_path):
    empty = ModelMapper(config_path=str(tmp_path / "config.json"))
    with patch('proxy.routers.management.model_mapper', empty):
        resp = client.post("/api/adcraft/test")
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
