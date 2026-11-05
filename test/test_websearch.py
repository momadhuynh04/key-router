import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from models.anthropic import AnthropicRequest, AnthropicResponse, AnthropicUsage, Message
from models.events import SSEEvent
from proxy import websearch

SEARX_HTML = (
    '<article class="result result-default">'
    '<h3><a href="https://spring.io/guides/gs/spring-boot" class="url_header">Getting Started</a></h3>'
    '<p class="content">Build an application with Spring Boot.</p>'
    "</article>"
)


@pytest.fixture(autouse=True)
def searxng_configured(monkeypatch):
    monkeypatch.setattr(websearch.secrets_store, "get_searxng_url", lambda: "http://searxng.test")


def _tool_call_events(call_id="call_1", query="python release", name="web_search"):
    return [
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": call_id, "name": name},
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"query": "%s"}' % query},
        }),
        SSEEvent(event="content_block_stop", data={"type": "content_block_stop", "index": 0}),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]


def _text_events(text="Done"):
    return [
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""},
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text},
        }),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]


class _FakeProvider:
    def __init__(self, rounds):
        self.rounds = rounds
        self.index = 0
        self.bodies = []

    async def translate_request(self, request):
        return {"messages": len(request.messages)}

    async def stream(self, body):
        self.bodies.append(body)
        events = self.rounds[self.index]
        self.index += 1
        for event in events:
            yield event

    async def generate(self, body):
        self.bodies.append(body)
        response = self.rounds[self.index]
        self.index += 1
        return response


def _request(**overrides):
    payload = {
        "model": "some-model",
        "messages": [Message(role="user", content="what is new in python?")],
        "max_tokens": 512,
    }
    payload.update(overrides)
    return AnthropicRequest(**payload)


def _bash_tool():
    return {"name": "Bash", "input_schema": {"type": "object", "properties": {}}}


def test_searx_parser_reads_article_results():
    parser = websearch._SearxParser()
    parser.feed(SEARX_HTML)
    assert parser.results == [{
        "title": "Getting Started",
        "url": "https://spring.io/guides/gs/spring-boot",
        "snippet": "Build an application with Spring Boot.",
    }]


def test_enable_replaces_hosted_search_tool():
    request = _request(tools=[{"type": "web_search_20250305", "name": "web_search"}, _bash_tool()])
    websearch.enable(request)
    assert [t.get("name") for t in request.tools] == ["Bash", "web_search"]
    assert request.tools[-1]["input_schema"]["required"] == ["query"]
    assert request.web_search is True


def test_enable_keeps_tools_when_client_offers_no_search():
    request = _request(tools=[_bash_tool()])
    websearch.enable(request)
    assert [t["name"] for t in request.tools] == ["Bash"]
    assert not getattr(request, "web_search", False)


def test_enable_does_not_duplicate_existing_function_tool():
    request = _request(tools=[websearch.search_tool_definition()], web_search=True)
    websearch.enable(request)
    assert [t["name"] for t in request.tools] == ["web_search"]


def test_enable_always_adds_tool_when_client_declares_nothing():
    request = _request(tools=[_bash_tool()])
    websearch.enable(request, always=True)
    assert [t["name"] for t in request.tools] == ["Bash", "web_search"]
    assert request.web_search is True


def test_enable_without_searxng_strips_hosted_tool_but_offers_nothing(monkeypatch):
    monkeypatch.setattr(websearch.secrets_store, "get_searxng_url", lambda: "")
    request = _request(tools=[{"type": "web_search_20250305", "name": "web_search"}, _bash_tool()])
    websearch.enable(request, always=True)
    assert [t["name"] for t in request.tools] == ["Bash"]
    assert not getattr(request, "web_search", False)


def test_unconfigured_search_explains_itself(monkeypatch):
    monkeypatch.setattr(websearch.secrets_store, "get_searxng_url", lambda: "")
    results = asyncio.run(websearch._run_calls([
        {"id": "c1", "name": "web_search", "input": {"query": "x"}},
    ]))
    assert results["c1"] == websearch.NOT_CONFIGURED


def test_format_results_lists_sources():
    text = websearch.format_results("q", [{"title": "T", "url": "https://x", "snippet": "S"}])
    assert 'Web search results for "q"' in text
    assert "1. T — https://x" in text
    assert "S" in text


def test_search_results_are_cached():
    websearch._cache.clear()
    with patch.object(websearch, "_fetch_results", AsyncMock(return_value=[{"title": "T", "url": "u", "snippet": "s"}])) as fetch:
        first = asyncio.run(websearch.search("same query"))
        second = asyncio.run(websearch.search("same query"))
    assert first == second == [{"title": "T", "url": "u", "snippet": "s"}]
    assert fetch.await_count == 1


def test_extract_tool_uses_reassembles_streamed_arguments():
    calls = websearch.extract_tool_uses(_tool_call_events(query="rust news"))
    assert calls == [{"id": "call_1", "name": "web_search", "input": {"query": "rust news"}}]


def test_stream_runs_search_and_returns_final_answer():
    request = _request(tools=[websearch.search_tool_definition()], web_search=True)
    provider = _FakeProvider([_tool_call_events(), _text_events("Python 3.15.2")])
    with patch.object(websearch, "search", AsyncMock(return_value=[
        {"title": "Download Python", "url": "https://www.python.org/downloads/", "snippet": "Latest release"},
    ])):
        events = asyncio.run(websearch.run_stream(provider, request, {"messages": 1}))

    assert any(e.data.get("delta", {}).get("text") == "Python 3.15.2" for e in events)
    assert provider.index == 2
    assert request.messages[-2].role == "assistant"
    assert request.messages[-2].content[0]["name"] == "web_search"
    result = request.messages[-1].content[0]
    assert result["type"] == "tool_result"
    assert "https://www.python.org/downloads/" in result["content"]


def test_stream_intercepts_claude_websearch_alias():
    request = _request(tools=[websearch.search_tool_definition()], web_search=True)
    provider = _FakeProvider([
        _tool_call_events(name="WebSearch"),
        _text_events("Answer"),
    ])
    with patch.object(websearch, "search", AsyncMock(return_value=[
        {"title": "T", "url": "https://example.com", "snippet": "S"},
    ])):
        events = asyncio.run(websearch.run_stream(provider, request, {}))

    assert any(e.data.get("delta", {}).get("text") == "Answer" for e in events)
    assert request.messages[-2].content[0]["name"] == "WebSearch"
    assert "https://example.com" in request.messages[-1].content[0]["content"]


def test_stream_leaves_client_tool_calls_alone():
    request = _request(tools=[websearch.search_tool_definition()], web_search=True)
    client_call = [
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "call_9", "name": "Bash"},
        }),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]
    provider = _FakeProvider([client_call])
    events = asyncio.run(websearch.run_stream(provider, request, {}))
    assert events == client_call
    assert provider.index == 1
    assert len(request.messages) == 1


def test_stream_mixed_turn_strips_our_tool_call():
    request = _request(tools=[websearch.search_tool_definition()], web_search=True)
    mixed = [
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "call_bash", "name": "Bash"},
        }),
        SSEEvent(event="content_block_start", data={
            "type": "content_block_start", "index": 1,
            "content_block": {"type": "tool_use", "id": "call_ws", "name": "web_search"},
        }),
        SSEEvent(event="content_block_delta", data={
            "type": "content_block_delta", "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"query": "x"}'},
        }),
        SSEEvent(event="message_stop", data={"type": "message_stop"}),
    ]
    provider = _FakeProvider([mixed])
    with patch.object(websearch, "search", AsyncMock()) as search:
        events = asyncio.run(websearch.run_stream(provider, request, {}))

    assert [e.data.get("index") for e in events if e.data.get("index") is not None] == [0]
    assert provider.index == 1
    search.assert_not_awaited()


def test_generate_mixed_turn_keeps_only_client_tools():
    request = _request(tools=[websearch.search_tool_definition()], web_search=True)
    provider = _FakeProvider([
        AnthropicResponse(
            id="m1", model="m", stop_reason="tool_use",
            content=[
                {"type": "tool_use", "id": "call_bash", "name": "Bash", "input": {"command": "ls"}},
                {"type": "tool_use", "id": "call_ws", "name": "web_search", "input": {"query": "x"}},
            ],
            usage=AnthropicUsage(input_tokens=1, output_tokens=1),
        ),
    ])
    with patch.object(websearch, "search", AsyncMock()) as search:
        response = asyncio.run(websearch.run_generate(provider, request, {}))

    assert [b["name"] for b in response.content] == ["Bash"]
    search.assert_not_awaited()


def test_generate_runs_search_then_answers():
    request = _request(tools=[websearch.search_tool_definition()], web_search=True)
    provider = _FakeProvider([
        AnthropicResponse(
            id="m1", model="m", stop_reason="tool_use",
            content=[{"type": "tool_use", "id": "call_1", "name": "web_search", "input": {"query": "x"}}],
            usage=AnthropicUsage(input_tokens=1, output_tokens=1),
        ),
        AnthropicResponse(
            id="m2", model="m", stop_reason="end_turn",
            content=[{"type": "text", "text": "Answer"}],
            usage=AnthropicUsage(input_tokens=1, output_tokens=1),
        ),
    ])
    with patch.object(websearch, "search", AsyncMock(return_value=[])):
        response = asyncio.run(websearch.run_generate(provider, request, {}))

    assert response.content == [{"type": "text", "text": "Answer"}]
    assert request.messages[-1].content[0]["content"].startswith('No web results found')
