import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from models.anthropic import AnthropicResponse, AnthropicUsage
from models.events import SSEEvent
from proxy.responses_ingress import (
    responses_to_anthropic,
    anthropic_response_to_responses_object,
    anthropic_events_to_responses_stream,
    _extract_text_tool_calls,
    looks_like_action_narration,
)
from proxy.server import app

client = TestClient(app)


# ----------------------------------------
# 1. Request translation: Responses → Anthropic
# ----------------------------------------

def _build_request(**overrides):
    payload = {
        "model": "gpt-5-codex",
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": "Hello"}]}],
        "stream": False,
    }
    payload.update(overrides)
    from models.openai_compat import ResponsesRequest
    return ResponsesRequest(**payload)

def test_request_instructions_and_developer_role_to_system():
    req = _build_request(
        instructions="You are a coding agent.",
        input=[
            {"type": "message", "role": "developer",
             "content": [{"type": "input_text", "text": "<skills>...</skills>"}]},
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "Hi"}]},
        ],
    )
    result = responses_to_anthropic(req)
    assert result.system == "You are a coding agent.\n\n<skills>...</skills>"
    assert len(result.messages) == 1
    assert result.messages[0].role == "user"

def test_request_string_input():
    req = _build_request(input="Just say hi")
    result = responses_to_anthropic(req)
    assert result.messages == [dict(role="user", content="Just say hi")] or \
           (len(result.messages) == 1 and result.messages[0].content == "Just say hi")

def test_request_input_image_data_url_preserved():
    req = _build_request(input=[{
        "type": "message", "role": "user",
        "content": [
            {"type": "input_text", "text": "What is in this image?"},
            {"type": "input_image",
             "image_url": "data:image/png;base64,iVBORw0KGgoAAAANS",
             "detail": "auto"},
        ],
    }])
    result = responses_to_anthropic(req)
    content = result.messages[0].content
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "What is in this image?"}
    assert content[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgoAAAANS"},
    }

def test_request_input_image_url_preserved():
    req = _build_request(input=[{
        "type": "message", "role": "user",
        "content": [
            {"type": "input_image", "image_url": "https://example.com/screenshot.png"},
        ],
    }])
    result = responses_to_anthropic(req)
    assert result.messages[0].content == [{
        "type": "image",
        "source": {"type": "url", "url": "https://example.com/screenshot.png"},
    }]

def test_request_function_call_roundtrip_items():
    req = _build_request(
        tools=[{
            "type": "function",
            "name": "exec_command",
            "description": "Runs a command in a PTY",
            "strict": False,
            "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        }],
        input=[
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "list files"}]},
            {"type": "function_call", "id": "fc_1", "call_id": "call_9",
             "name": "exec_command", "arguments": "{\"cmd\": \"ls\"}"},
            {"type": "function_call_output", "call_id": "call_9", "output": "a.txt\nb.txt"},
            {"type": "reasoning", "summary": []},  # ignored
        ],
    )
    result = responses_to_anthropic(req)

    assert result.tools is not None and len(result.tools) == 1
    tool = result.tools[0]
    assert tool["name"] == "exec_command"
    assert tool["input_schema"]["properties"]["cmd"] == {"type": "string"}

    assert len(result.messages) == 3
    tool_use = result.messages[1].content[0]
    assert tool_use["type"] == "tool_use"
    assert tool_use["id"] == "call_9"
    assert tool_use["input"] == {"cmd": "ls"}

    tool_result = result.messages[2].content[0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "call_9"
    assert tool_result["content"] == "a.txt\nb.txt"

def test_request_reasoning_before_function_call_becomes_thinking():
    req = _build_request(
        input=[
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "list files"}]},
            {"type": "reasoning",
             "summary": [{"type": "summary_text", "text": "I should run ls."}]},
            {"type": "function_call", "id": "fc_1", "call_id": "call_9",
             "name": "exec_command", "arguments": "{\"cmd\": \"ls\"}"},
            {"type": "function_call_output", "call_id": "call_9", "output": "a.txt"},
        ],
    )
    result = responses_to_anthropic(req)

    assistant = result.messages[1].content
    assert assistant[0] == {"type": "thinking", "thinking": "I should run ls."}
    assert assistant[1]["type"] == "tool_use"
    assert assistant[1]["id"] == "call_9"


def test_request_reasoning_flushes_before_next_user():
    req = _build_request(
        input=[
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "hi"}]},
            {"type": "reasoning",
             "summary": [{"type": "summary_text", "text": "The user greeted me."}]},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "Hello!"}]},
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "again"}]},
        ],
    )
    result = responses_to_anthropic(req)

    assert [m.role for m in result.messages] == ["user", "assistant", "user"]
    assert result.messages[1].content == [
        {"type": "thinking", "thinking": "The user greeted me."},
        {"type": "text", "text": "Hello!"},
    ]
    assert result.messages[2].content == "again"


def test_request_non_function_tools_skipped():
    req = _build_request(tools=[
        {"type": "web_search", "external_web_access": False},
        {"type": "namespace", "name": "multi_agent_v1", "tools": []},
    ])
    result = responses_to_anthropic(req)
    assert result.tools is None

def test_request_max_output_tokens_and_reasoning_effort():
    req = _build_request(max_output_tokens=256, reasoning={"effort": "high"})
    result = responses_to_anthropic(req)
    assert result.max_tokens == 256
    assert getattr(result, "reasoning_effort") == "high"


# ----------------------------------------
# 2. Response object translation
# ----------------------------------------

def _anthropic_response(content, stop_reason="end_turn"):
    return AnthropicResponse(
        id="msg_x",
        model="dolphin-mistral",
        content=content,
        stop_reason=stop_reason,
        usage=AnthropicUsage(input_tokens=10, output_tokens=4),
    )

def test_response_object_text_only():
    out = anthropic_response_to_responses_object(
        _anthropic_response([{"type": "text", "text": "PONG"}]), requested_model="gpt-5-codex")
    assert out["object"] == "response"
    assert out["status"] == "completed"
    assert out["model"] == "gpt-5-codex"
    message_item = out["output"][0]
    assert message_item["type"] == "message"
    assert message_item["role"] == "assistant"
    assert message_item["content"][0]["type"] == "output_text"
    assert message_item["content"][0]["text"] == "PONG"
    assert out["usage"]["total_tokens"] == 14

def test_response_object_thinking_becomes_reasoning_item():
    out = anthropic_response_to_responses_object(
        _anthropic_response([
            {"type": "thinking", "thinking": "I should list the files."},
            {"type": "text", "text": "Listing"},
            {"type": "tool_use", "id": "toolu_call_7", "name": "exec_command", "input": {"cmd": "ls"}},
        ], stop_reason="tool_use"),
        requested_model="gpt-5-codex")

    types = [i["type"] for i in out["output"]]
    assert types == ["reasoning", "message", "function_call"]
    reasoning = out["output"][0]
    assert reasoning["summary"] == [{"type": "summary_text", "text": "I should list the files."}]
    assert reasoning["status"] == "completed"


def test_response_object_with_function_call():
    out = anthropic_response_to_responses_object(
        _anthropic_response([
            {"type": "text", "text": "Listing"},
            {"type": "tool_use", "id": "toolu_call_7", "name": "exec_command", "input": {"cmd": "ls"}},
        ], stop_reason="tool_use"),
        requested_model="gpt-5-codex")

    types = [i["type"] for i in out["output"]]
    assert types == ["message", "function_call"]
    fc = out["output"][1]
    assert fc["call_id"] == "call_7"  # toolu_ stripped
    assert fc["name"] == "exec_command"
    assert json.loads(fc["arguments"]) == {"cmd": "ls"}
    assert fc["status"] == "completed"


# ----------------------------------------
# 3. Streaming translation: typed SSE events
# ----------------------------------------

async def _events_from(events):
    for ev in events:
        yield ev

async def _collect(events):
    lines = []
    async for line in anthropic_events_to_responses_stream(_events_from(events), "gpt-5-codex"):
        lines.append(line)
    parsed = []
    name = None
    for line in lines:
        for sub in line.split("\n"):
            if sub.startswith("event: "):
                name = sub[len("event: "):].strip()
            elif sub.startswith("data: ") and name:
                parsed.append((name, json.loads(sub[len("data: "):])))
    return lines, parsed

@pytest.mark.asyncio
async def test_stream_text_full_event_lifecycle():
    events = [
        SSEEvent(event="message_start", data={
            "type": "message_start",
            "message": {"id": "m", "usage": {"input_tokens": 6}}
        }),
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "PONG"}
        }),
        SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0}),
        SSEEvent(event="message_delta", data={
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}
        }),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]

    lines, parsed = await _collect(events)
    names = [n for n, _ in parsed]

    # Codex hard-requires the stream to end with response.completed
    assert names[-1] == "response.completed"
    assert names[0] == "response.created"
    assert "response.output_item.added" in names
    assert "response.output_text.delta" in names
    assert "response.output_text.done" in names
    assert "response.output_item.done" in names

    delta_payload = dict(parsed)["response.output_text.delta"]
    assert delta_payload["delta"] == "PONG"

    completed = dict([(n, p) for n, p in parsed])["response.completed"]
    assert completed["response"]["status"] == "completed"
    assert completed["response"]["output"][0]["content"][0]["text"] == "PONG"
    assert completed["response"]["usage"]["input_tokens"] == 6
    assert completed["response"]["usage"]["total_tokens"] == 7

@pytest.mark.asyncio
async def test_stream_thinking_reasoning_lifecycle():
    events = [
        SSEEvent(event="message_start", data={
            "type": "message_start",
            "message": {"id": "m", "usage": {"input_tokens": 4}}
        }),
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking", "thinking": ""}
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "I should "}
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "run ls."}
        }),
        SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0}),
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_call_ab", "name": "exec_command", "input": {}}
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"cmd":"ls"}'}
        }),
        SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 1}),
        SSEEvent(event="message_delta", data={
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 3}
        }),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]

    _, parsed = await _collect(events)
    names = [n for n, _ in parsed]
    assert "response.reasoning_summary_part.added" in names
    assert "response.reasoning_summary_text.delta" in names
    assert "response.reasoning_summary_text.done" in names

    deltas = "".join(p["delta"] for n, p in parsed if n == "response.reasoning_summary_text.delta")
    assert deltas == "I should run ls."

    completed = dict((n, p) for n, p in parsed)["response.completed"]
    types = [i["type"] for i in completed["response"]["output"]]
    assert types == ["reasoning", "function_call"]
    assert completed["response"]["output"][0]["summary"][0]["text"] == "I should run ls."


@pytest.mark.asyncio
async def test_stream_function_call_event_lifecycle():
    events = [
        SSEEvent(event="message_start", data={
            "type": "message_start",
            "message": {"id": "m", "usage": {"input_tokens": 5}}
        }),
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_call_ab", "name": "exec_command", "input": {}}
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
            "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 3}
        }),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]

    _, parsed = await _collect(events)
    by_name = {}
    for n, p in parsed:
        by_name.setdefault(n, []).append(p)

    added = by_name["response.output_item.added"][0]["item"]
    assert added["type"] == "function_call"
    assert added["call_id"] == "call_ab"
    assert added["name"] == "exec_command"

    deltas = "".join(p["delta"] for p in by_name["response.function_call_arguments.delta"])
    assert deltas == '{"cmd":"ls"}'

    done = by_name["response.output_item.done"][0]["item"]
    assert done["status"] == "completed"
    assert json.loads(done["arguments"]) == {"cmd": "ls"}

    completed = by_name["response.completed"][0]
    assert completed["response"]["output"][0]["type"] == "function_call"


# ----------------------------------------
# 4. Endpoint integration: POST /v1/responses
# ----------------------------------------

@patch('proxy.server.provider_router.get_provider')
def test_endpoint_non_streaming(mock_get_provider):
    mock_provider = MagicMock()
    mock_provider.translate_request = AsyncMock(return_value={})

    async def fake_generate(body):
        return _anthropic_response([{"type": "text", "text": "Via Responses API"}])

    mock_provider.generate = fake_generate
    mock_get_provider.return_value = mock_provider

    response = client.post("/v1/responses", json={
        "model": "gpt-5-codex",
        "input": "Say hi",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "response"
    assert data["output"][0]["content"][0]["text"] == "Via Responses API"

@patch('proxy.server.provider_router.get_provider')
def test_endpoint_streaming_ends_with_completed(mock_get_provider):
    mock_provider = MagicMock()
    mock_provider.translate_request = AsyncMock(return_value={})

    async def fake_stream(body):
        yield SSEEvent(event="message_start", data={
            "type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}
        })
        yield SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "ok"}
        })
        yield SSEEvent(event="message_stop", data={"type": "message_stop"})

    mock_provider.stream = fake_stream
    mock_get_provider.return_value = mock_provider

    response = client.post("/v1/responses", json={
        "model": "gpt-5-codex",
        "input": "Say ok",
        "stream": True,
    })
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: response.created" in response.text
    assert "event: response.completed" in response.text


# ----------------------------------------
# 5. Text tool-call rescue (weak models imitating tool syntax)
# ----------------------------------------

EXEC_SCHEMA = {
    "type": "object",
    "properties": {
        "cmd": {"type": "string"},
        "workdir": {"type": "string"},
        "yield_time_ms": {"type": "integer"},
        "tty": {"type": "boolean"},
    },
}
TOOL_SCHEMAS = {
    "exec_command": EXEC_SCHEMA,
    "update_plan": {"type": "object", "properties": {"explanation": {"type": "string"}}},
}

def test_extract_text_tool_calls_basic():
    text = (
        "commentary: I'll explore the project structure.\n"
        "functions.exec:\n"
        '{"cmd": "ls -la /tmp", "cwd": "/tmp"}\n'
    )
    calls = _extract_text_tool_calls(text, TOOL_SCHEMAS)
    assert calls == [("exec_command", {"cmd": "ls -la /tmp", "cwd": "/tmp"})]

def test_extract_ignores_unknown_tools_and_prose():
    prose = "I could use functions.exec: but let me explain the plan first."
    assert _extract_text_tool_calls(prose, TOOL_SCHEMAS) == []

    unknown = 'web_search:\n{"query": "test"}'
    assert _extract_text_tool_calls(unknown, TOOL_SCHEMAS) == []

def test_extract_handles_multiline_json():
    text = 'functions.update_plan:\n{\n  "plan": [{"step": "a", "status": "pending"}]\n}'
    calls = _extract_text_tool_calls(text, TOOL_SCHEMAS)
    assert calls == [("update_plan", {"plan": [{"step": "a", "status": "pending"}]})]

def test_extract_xml_parameter_format():
    """Nemotron/Llama style observed in the wild."""
    text = (
        '<tool_call> FUNCTION exec_command '
        '<parameter name="cmd">ls -la /mnt/Data/ProjectStorage/probrowser </parameter> '
        '<parameter name="yield_time_ms">5000</parameter> '
        '<parameter name="tty">false</parameter> '
        "</FUNCTION></tool_call>"
    )
    calls = _extract_text_tool_calls(text, TOOL_SCHEMAS)
    assert calls == [("exec_command", {
        "cmd": "ls -la /mnt/Data/ProjectStorage/probrowser",
        "yield_time_ms": 5000,  # coerced per schema
        "tty": False,
    })]

def test_extract_broken_multiline_format():
    """EXACT mangled output captured from a real nemotron session:
    multi-line tags + broken attribute (name="cmd> without closing quote)."""
    text = (
        "<tool_call>\n"
        "FUNCTION\n"
        "exec_command\n"
        '<parameter name="cmd>\n'
        "ls -la /mnt/Data/ProjectStorage/probrowser\n"
        "</parameter>\n"
        "</FUNCTION>\n"
        "</tool_call>"
    )
    calls = _extract_text_tool_calls(text, TOOL_SCHEMAS)
    assert calls == [("exec_command", {"cmd": "ls -la /mnt/Data/ProjectStorage/probrowser"})]

def test_extract_attribute_style_format():
    """Another real nemotron variant: <function=...> / <parameter=...> with gibberish."""
    text = (
        "めて\n"
        "Commentary: I'll explore the project structureめて\n"
        "Commentary: Let me get an overview<tool_call>\n"
        "<function=functions.exec>\n"
        "<parameter=cmd>\n"
        "ls -la /mnt/Data/ProjectStorage/mdjdb\n"
        "</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )
    calls = _extract_text_tool_calls(text, TOOL_SCHEMAS)
    assert calls == [("exec_command", {"cmd": "ls -la /mnt/Data/ProjectStorage/mdjdb"})]

def test_extract_json_tool_call_format():
    """Llama 3 / Qwen style."""
    text = '<tool_call>{"name": "exec_command", "arguments": {"cmd": "ls"}} </tool_call>'
    calls = _extract_text_tool_calls(text, TOOL_SCHEMAS)
    assert calls == [("exec_command", {"cmd": "ls"})]

def test_response_object_rescue_appends_function_call():
    resp = _anthropic_response([{
        "type": "text",
        "text": 'commentary: exploring\nfunctions.exec:\n{"cmd": "ls"}',
    }])
    out = anthropic_response_to_responses_object(resp, "gpt-5-codex", tool_schemas=TOOL_SCHEMAS)
    types = [i["type"] for i in out["output"]]
    assert types == ["message", "function_call"]
    fc = out["output"][1]
    assert fc["name"] == "exec_command"
    assert json.loads(fc["arguments"]) == {"cmd": "ls"}
    assert fc["status"] == "completed"

def test_response_object_no_rescue_when_native_call_exists():
    resp = _anthropic_response([
        {"type": "text", "text": 'functions.exec:\n{"cmd": "ls"}'},
        {"type": "tool_use", "id": "toolu_call_1", "name": "update_plan", "input": {}},
    ], stop_reason="tool_use")
    out = anthropic_response_to_responses_object(resp, "gpt-5-codex", tool_schemas=TOOL_SCHEMAS)
    assert [i["type"] for i in out["output"]].count("function_call") == 1  # native only

@pytest.mark.asyncio
async def test_stream_rescue_emits_function_call_item():
    events = [
        SSEEvent(event="message_start", data={
            "type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}
        }),
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": 'commentary: exploring\nfunctions.exec:\n{"cmd": "ls"}'}
        }),
        SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0}),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]

    lines = []
    async for line in anthropic_events_to_responses_stream(_events_from(events), "gpt-5-codex", TOOL_SCHEMAS):
        lines.append(line)

    text = "".join(lines)
    assert "event: response.output_item.added" in text
    assert '"name": "exec_command"' in text
    # function_call item must appear in the completed response output
    completed = [l for l in lines if "response.completed" in l][0]
    payload = json.loads(completed.split("data: ", 1)[1])
    types = [i["type"] for i in payload["response"]["output"]]
    assert "function_call" in types

@pytest.mark.asyncio
async def test_stream_no_rescue_when_native_tool_call():
    events = [
        SSEEvent(event="message_start", data={
            "type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}
        }),
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_c1", "name": "exec_command", "input": {}}
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"cmd": "ls"}'}
        }),
        SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0}),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]

    lines = []
    async for line in anthropic_events_to_responses_stream(_events_from(events), "gpt-5-codex", TOOL_SCHEMAS):
        lines.append(line)

    text = "".join(lines)
    # exactly one function_call item — the native one, no rescue duplicate
    assert text.count('"name": "exec_command"') >= 1
    completed = [l for l in lines if "response.completed" in l][0]
    payload = json.loads(completed.split("data: ", 1)[1])
    fcs = [i for i in payload["response"]["output"] if i["type"] == "function_call"]
    assert len(fcs) == 1
    assert fcs[0]["call_id"] == "c1"  # native id preserved, not a rescue call_ id


# ----------------------------------------
# 6. Agentic retry — narration instead of tool call
# ----------------------------------------

def test_narration_detection():
    assert looks_like_action_narration("Let me check the files first.")
    assert looks_like_action_narration("I'll explore the project structure.")
    assert looks_like_action_narration("Để mình chạy thử tool nha.")
    assert looks_like_action_narration("Mình đang demo cho bạn đây.")
    assert not looks_like_action_narration("The answer is 4.")
    assert not looks_like_action_narration("")

def _text_stream_events(text):
    return [
        SSEEvent(event="message_start", data={
            "type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}
        }),
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text}
        }),
        SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0}),
        SSEEvent(event="message_delta", data={
            "type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}
        }),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]

def _tool_stream_events(name="exec_command", args='{"cmd": "ls"}'):
    return _text_stream_events("x")[:1] + [
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_n1", "name": name, "input": {}}
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": args}
        }),
        SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0}),
        SSEEvent(event="message_delta", data={
            "type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 2}
        }),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]

@patch('proxy.server.provider_router.get_provider')
def test_endpoint_retries_narration_then_succeeds(mock_get_provider):
    """First attempt narrates ('Let me check...'), retry must produce the tool call."""
    mock_provider = MagicMock()
    captured_systems = []

    async def fake_translate(req):
        captured_systems.append(req.system)
        return {}

    mock_provider.translate_request = fake_translate

    streams = [
        _text_stream_events("Let me check the files first."),   # narration
        _tool_stream_events(),                                   # real tool call
    ]

    async def fake_stream(body):
        for ev in streams.pop(0):
            yield ev

    mock_provider.stream = fake_stream
    mock_get_provider.return_value = mock_provider

    response = client.post("/v1/responses", json={
        "model": "gpt-5-codex",
        "stream": True,
        "tools": [{"type": "function", "name": "exec_command", "parameters": {"type": "object", "properties": {}}}],
        "input": "list files",
    })
    assert response.status_code == 200
    body = response.text
    assert "event: response.output_item.added" in body
    assert '"name": "exec_command"' in body
    # exactly 2 attempts: narration + nudge-augmented retry
    assert len(captured_systems) == 2
    assert "autonomous coding agent" in (captured_systems[1] or "")

@patch('proxy.server.provider_router.get_provider')
def test_endpoint_no_retry_when_tool_called(mock_get_provider):
    mock_provider = MagicMock()
    calls = {"n": 0}

    async def fake_translate(req):
        calls["n"] += 1
        return {}

    mock_provider.translate_request = fake_translate

    async def fake_stream(body):
        for ev in _tool_stream_events():
            yield ev

    mock_provider.stream = fake_stream
    mock_get_provider.return_value = mock_provider

    response = client.post("/v1/responses", json={
        "model": "gpt-5-codex",
        "stream": True,
        "tools": [{"type": "function", "name": "exec_command", "parameters": {"type": "object", "properties": {}}}],
        "input": "list files",
    })
    assert response.status_code == 200
    assert calls["n"] == 1  # single attempt

@patch('proxy.server.provider_router.get_provider')
def test_endpoint_no_retry_on_plain_answer(mock_get_provider):
    """A genuine text answer (no action intent) must not be retried."""
    mock_provider = MagicMock()
    calls = {"n": 0}

    async def fake_translate(req):
        calls["n"] += 1
        return {}

    mock_provider.translate_request = fake_translate

    async def fake_stream(body):
        for ev in _text_stream_events("The answer is 4."):
            yield ev

    mock_provider.stream = fake_stream
    mock_get_provider.return_value = mock_provider

    response = client.post("/v1/responses", json={
        "model": "gpt-5-codex",
        "stream": True,
        "tools": [{"type": "function", "name": "exec_command", "parameters": {"type": "object", "properties": {}}}],
        "input": "what is 2+2",
    })
    assert response.status_code == 200
    assert calls["n"] == 1

@patch('proxy.server.provider_router.get_provider')
def test_endpoint_nonstreaming_retries_narration(mock_get_provider):
    mock_provider = MagicMock()

    responses = [
        _anthropic_response([{"type": "text", "text": "Để mình chạy ls xem sao."}]),
        _anthropic_response([{"type": "tool_use", "id": "toolu_c9", "name": "exec_command", "input": {"cmd": "ls"}}],
                            stop_reason="tool_use"),
    ]

    async def fake_generate(body):
        return responses.pop(0)

    async def fake_translate(req):
        return {}

    mock_provider.generate = fake_generate
    mock_provider.translate_request = fake_translate
    mock_get_provider.return_value = mock_provider

    response = client.post("/v1/responses", json={
        "model": "gpt-5-codex",
        "tools": [{"type": "function", "name": "exec_command", "parameters": {"type": "object", "properties": {}}}],
        "input": "list files",
    })
    assert response.status_code == 200
    data = response.json()
    fcs = [i for i in data["output"] if i["type"] == "function_call"]
    assert len(fcs) == 1 and fcs[0]["name"] == "exec_command"
