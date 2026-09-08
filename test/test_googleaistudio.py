import json
import pytest
from unittest.mock import patch, MagicMock

from provider.googleaistudio.adapter import GoogleAIStudioProvider
from models.anthropic import AnthropicRequest, Message


@pytest.mark.asyncio
async def test_translate_request_text_and_tool():
    p = GoogleAIStudioProvider(target_model="gemini-2.0-flash")
    req = AnthropicRequest(
        model="claude-3-haiku",
        messages=[Message(role="user", content="hi")],
        system="You are helpful",
        tools=[{"name": "get_weather", "description": "desc", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}],
        tool_choice={"type": "any"},
        temperature=0.7,
        stop_sequences=["STOP"],
    )
    body = await p.translate_request(req)
    assert body["systemInstruction"]["parts"][0]["text"] == "You are helpful"
    assert body["contents"][0]["role"] == "user"
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "get_weather"
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
    assert body["generationConfig"]["temperature"] == 0.7
    assert body["generationConfig"]["stopSequences"] == ["STOP"]


@pytest.mark.asyncio
async def test_translate_request_merges_same_role():
    p = GoogleAIStudioProvider(target_model="gemini-2.0-flash")
    req = AnthropicRequest(
        model="m",
        messages=[
            Message(role="user", content="hello"),
            Message(role="user", content="world"),
        ],
    )
    body = await p.translate_request(req)
    assert len(body["contents"]) == 1
    assert body["contents"][0]["role"] == "user"
    assert len(body["contents"][0]["parts"]) == 2


@pytest.mark.asyncio
async def test_translate_response_text():
    p = GoogleAIStudioProvider(target_model="gemini-2.0-flash")
    data = {
        "candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    }
    resp = await p.translate_response(data)
    assert resp.content[0]["text"] == "hello"
    assert resp.stop_reason == "end_turn"
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 5


@pytest.mark.asyncio
async def test_translate_response_tool_call():
    p = GoogleAIStudioProvider(target_model="gemini-2.0-flash")
    data = {
        "candidates": [{"content": {"parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}}], "role": "model"}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3},
    }
    resp = await p.translate_response(data)
    assert resp.content[0]["type"] == "tool_use"
    assert resp.content[0]["name"] == "get_weather"
    assert resp.content[0]["input"] == {"city": "Paris"}
    assert resp.stop_reason == "tool_use"


def test_map_finish_reason_max_tokens_priority():
    m = GoogleAIStudioProvider._map_finish_reason("MAX_TOKENS", True)
    assert m == "max_tokens"
    assert GoogleAIStudioProvider._map_finish_reason("STOP", False) == "end_turn"
    assert GoogleAIStudioProvider._map_finish_reason("SAFETY", False) == "stop_sequence"


@pytest.mark.asyncio
async def test_translate_response_blocked():
    p = GoogleAIStudioProvider(target_model="gemini-2.0-flash")
    data = {"promptFeedback": {"blockReason": "SAFETY"}}
    with pytest.raises(ValueError, match="blocked"):
        await p.translate_response(data)


@pytest.mark.asyncio
@patch("httpx.AsyncClient.stream")
async def test_stream_text_and_tool(mock_stream):
    from config.settings import settings
    settings.google_api_key = "dummy"
    class MockResp:
        status_code = 200
        async def aread(self): return b""
        def raise_for_status(self): pass
        async def aiter_lines(self):
            yield 'data: {"candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": null}]}'
            yield 'data: {"candidates": [{"content": {"parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}}], "role": "model"}, "finishReason": "STOP"}], "usageMetadata": {"candidatesTokenCount": 5}}'
            yield 'data: [DONE]'

    class MockCM:
        async def __aenter__(self): return MockResp()
        async def __aexit__(self, *a): pass

    mock_stream.return_value = MockCM()
    p = GoogleAIStudioProvider(target_model="gemini-2.0-flash")
    events = []
    async for ev in p.stream({"contents": []}):
        events.append(ev)
    types = [e.event for e in events]
    assert "message_start" in types
    assert "message_stop" in types
    assert any(e.data.get("delta", {}).get("stop_reason") == "tool_use" for e in events if e.event == "message_delta")
    # tool_use block
    starts = [e for e in events if e.event == "content_block_start" and e.data.get("content_block", {}).get("type") == "tool_use"]
    assert len(starts) == 1
    assert starts[0].data["content_block"]["name"] == "get_weather"
