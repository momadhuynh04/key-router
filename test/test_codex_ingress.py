import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from models.anthropic import AnthropicResponse, AnthropicUsage
from models.events import SSEEvent
from models.openai_compat import OpenAIRequest
from proxy.openai_ingress import (
    openai_chat_to_anthropic,
    anthropic_response_to_openai_chat,
    anthropic_events_to_openai_stream,
)
from proxy.server import app

client = TestClient(app)


# ----------------------------------------
# 1. Request translation: OpenAI → Anthropic
# ----------------------------------------

def _build_openai_request(**overrides):
    payload = {
        "model": "gpt-5-codex",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False,
    }
    payload.update(overrides)
    return OpenAIRequest(**payload)

def test_request_basic_fields():
    req = _build_openai_request(
        temperature=0.5,
        top_p=0.9,
        max_completion_tokens=123,
        stop="END",
    )
    result = openai_chat_to_anthropic(req)
    assert result.model == "gpt-5-codex"
    assert result.max_tokens == 123
    assert result.temperature == 0.5
    assert result.top_p == 0.9
    assert result.stop_sequences == ["END"]
    assert result.messages[-1].role == "user"
    assert result.stream is False

def test_request_system_and_developer_roles_merge_into_system():
    req = _build_openai_request(messages=[
        {"role": "system", "content": "Be terse."},
        {"role": "developer", "content": "Use Python."},
        {"role": "user", "content": "Hi"},
    ])
    result = openai_chat_to_anthropic(req)
    assert result.system == "Be terse.\nUse Python."

def test_request_content_parts_array_flattened():
    req = _build_openai_request(messages=[
        {"role": "user", "content": [
            {"type": "text", "text": "Part one"},
            {"type": "image_url", "image_url": {"url": "x"}},  # invalid URL → ignored
            {"type": "text", "text": "Part two"},
        ]}
    ])
    result = openai_chat_to_anthropic(req)
    assert result.messages[0].content == "Part one\nPart two"

def test_request_image_url_is_preserved():
    req = _build_openai_request(messages=[
        {"role": "user", "content": [
            {"type": "text", "text": "What is this?"},
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
            }},
        ]}
    ])
    result = openai_chat_to_anthropic(req)
    content = result.messages[0].content
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "What is this?"}
    assert content[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": "/9j/4AAQSkZJRg=="},
    }

def test_request_assistant_tool_calls_and_tool_results():
    req = _build_openai_request(
        tools=[{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}
            }
        }],
        messages=[
            {"role": "user", "content": "Weather?"},
            {"role": "assistant", "tool_calls": [{
                "id": "call_abc",
                "type": "function",
                "function": {"name": "get_weather", "arguments": "{\"city\": \"Hue\"}"}
            }]},
            {"role": "tool", "tool_call_id": "call_abc", "content": "sunny"},
        ],
    )
    result = openai_chat_to_anthropic(req)

    assert len(result.tools) == 1
    assert result.tools[0]["name"] == "get_weather"
    assert result.tools[0]["input_schema"]["properties"]["city"] == {"type": "string"}

    assistant_msg = result.messages[1]
    assert assistant_msg.role == "assistant"
    tool_use = [b for b in assistant_msg.content if b["type"] == "tool_use"][0]
    assert tool_use["id"] == "call_abc"
    assert tool_use["name"] == "get_weather"
    assert tool_use["input"] == {"city": "Hue"}

    tool_msg = result.messages[2]
    assert tool_msg.role == "user"
    assert tool_msg.content[0]["type"] == "tool_result"
    assert tool_msg.content[0]["tool_use_id"] == "call_abc"
    assert tool_msg.content[0]["content"] == "sunny"

def test_request_tool_choice_mapping():
    required = openai_chat_to_anthropic(_build_openai_request(tool_choice="required"))
    assert required.tool_choice == {"type": "any"}

    named = openai_chat_to_anthropic(_build_openai_request(
        tool_choice={"type": "function", "function": {"name": "get_weather"}}
    ))
    assert named.tool_choice == {"type": "tool", "name": "get_weather"}

def test_request_reasoning_effort_preserved():
    req = _build_openai_request(reasoning_effort="high")
    result = openai_chat_to_anthropic(req)
    assert getattr(result, "reasoning_effort") == "high"


# ----------------------------------------
# 2. Response translation: Anthropic → OpenAI
# ----------------------------------------

def _anthropic_response(content, stop_reason="end_turn"):
    return AnthropicResponse(
        id="msg_123",
        model="deepseek-v4",
        content=content,
        stop_reason=stop_reason,
        usage=AnthropicUsage(input_tokens=11, output_tokens=7),
    )

def test_response_text_translation():
    resp = _anthropic_response([{"type": "text", "text": "Answer"}])
    out = anthropic_response_to_openai_chat(resp, requested_model="gpt-5-codex")

    assert out["object"] == "chat.completion"
    assert out["model"] == "gpt-5-codex"
    assert out["id"].startswith("chatcmpl-")
    choice = out["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == "Answer"
    assert out["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

def test_response_tool_calls_translation():
    resp = _anthropic_response([
        {"type": "text", "text": "Calling tool"},
        {"type": "tool_use", "id": "toolu_call_abc", "name": "get_weather", "input": {"city": "Hue"}},
    ], stop_reason="tool_use")
    out = anthropic_response_to_openai_chat(resp, requested_model="gpt-5-codex")

    message = out["choices"][0]["message"]
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert message["content"] == "Calling tool"
    tc = message["tool_calls"][0]
    assert tc["id"] == "call_abc"  # toolu_ prefix stripped back
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_weather"
    assert json.loads(tc["function"]["arguments"]) == {"city": "Hue"}


# ----------------------------------------
# 3. Streaming translation: Anthropic SSE events → OpenAI chunks
# ----------------------------------------

async def _events_from(events):
    for ev in events:
        yield ev

async def _collect_lines(events, model="gpt-5-codex"):
    lines = []
    async for line in anthropic_events_to_openai_stream(_events_from(events), model):
        lines.append(line)
    return lines

@pytest.mark.asyncio
async def test_stream_text_conversion():
    events = [
        SSEEvent(event="message_start", data={
            "type": "message_start",
            "message": {"id": "msg_1", "usage": {"input_tokens": 5}}
        }),
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "Hi"}
        }),
        SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0}),
        SSEEvent(event="message_delta", data={
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 3}
        }),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]

    lines = await _collect_lines(events)
    chunks = [
        json.loads(l[len("data: "):]) for l in lines
        if l.startswith("data: ") and l != "data: [DONE]\n\n"
    ]

    assert lines[-1] == "data: [DONE]\n\n"
    # First chunk carries the assistant role
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    # Text delta chunk
    deltas = [c["choices"][0]["delta"] for c in chunks]
    assert any(d.get("content") == "Hi" for d in deltas)
    # Final chunk has finish_reason + usage
    final = chunks[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"] == {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}

@pytest.mark.asyncio
async def test_stream_tool_call_conversion():
    events = [
        SSEEvent(event="message_start", data={
            "type": "message_start",
            "message": {"id": "msg_1", "usage": {"input_tokens": 4}}
        }),
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_call_xyz", "name": "run_cmd", "input": {}}
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": "{\"cmd\":"}
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": "\"ls\"}"}
        }),
        SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0}),
        SSEEvent(event="message_delta", data={
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 6}
        }),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]

    lines = await _collect_lines(events)
    chunks = [json.loads(l[len("data: "):]) for l in lines if l.startswith("data: ") and l != "data: [DONE]\n\n"]

    start_chunks = [c for c in chunks if c["choices"][0]["delta"].get("tool_calls")
                    and c["choices"][0]["delta"]["tool_calls"][0].get("id")]
    assert len(start_chunks) == 1
    assert start_chunks[0]["choices"][0]["delta"]["tool_calls"][0]["id"] == "call_xyz"
    assert start_chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "run_cmd"

    arg_deltas = [c["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
                  for c in chunks if c["choices"][0]["delta"].get("tool_calls")
                  and c["choices"][0]["delta"]["tool_calls"][0].get("function", {}).get("arguments")]
    assert arg_deltas == ["{\"cmd\":", "\"ls\"}"]

    final = chunks[-1]
    assert final["choices"][0]["finish_reason"] == "tool_calls"

@pytest.mark.asyncio
async def test_stream_error_event_emits_error_chunk():
    events = [
        SSEEvent(event="error", data={"type": "error", "error": {"type": "api_error", "message": "boom"}}),
    ]
    lines = await _collect_lines(events)
    error_lines = [l for l in lines if '"error"' in l]
    assert error_lines, f"No error chunk in {lines}"
    error_payload = json.loads(error_lines[0][len("data: "):])
    assert error_payload["error"]["message"] == "boom"
    assert lines[-1] == "data: [DONE]\n\n"


# ----------------------------------------
# 4. Endpoint integration: POST /v1/chat/completions
# ----------------------------------------

@patch('proxy.server.provider_router.get_provider')
def test_endpoint_non_streaming(mock_get_provider):
    mock_provider = MagicMock()
    mock_provider.translate_request = AsyncMock(return_value={})

    async def fake_generate(body):
        return _anthropic_response([{"type": "text", "text": "From Codex path"}])

    mock_provider.generate = fake_generate
    mock_get_provider.return_value = mock_provider

    response = client.post("/v1/chat/completions", json=_build_openai_request().__dict__ | {
        "model": "gpt-5-codex",
        "messages": [{"role": "user", "content": "Hello"}],
    })
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "gpt-5-codex"
    assert data["choices"][0]["message"]["content"] == "From Codex path"

@patch('proxy.server.provider_router.get_provider')
def test_endpoint_streaming(mock_get_provider):
    mock_provider = MagicMock()
    mock_provider.translate_request = AsyncMock(return_value={})

    async def fake_stream(body):
        yield SSEEvent(event="message_start", data={
            "type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}
        })
        yield SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "streamed"}
        })
        yield SSEEvent(event="message_delta", data={
            "type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}
        })
        yield SSEEvent(event="message_stop", data={"type": "message_stop"})

    mock_provider.stream = fake_stream
    mock_get_provider.return_value = mock_provider

    response = client.post("/v1/chat/completions", json={
        "model": "gpt-5-codex",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True,
    })
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    body = response.text
    assert '"content":"streamed"' in body.replace(" ", "")
    assert '"finish_reason":"stop"' in body.replace(" ", "")
    assert body.rstrip().endswith("data: [DONE]")

@patch('proxy.server.provider_router.get_provider')
def test_endpoint_missing_mapping_returns_openai_error(mock_get_provider):
    mock_get_provider.side_effect = ValueError("No mapping found for model 'gpt-99'")
    response = client.post("/v1/chat/completions", json={
        "model": "gpt-99",
        "messages": [{"role": "user", "content": "Hello"}],
    })
    assert response.status_code == 500
    assert "No mapping found" in response.json()["error"]["message"]
