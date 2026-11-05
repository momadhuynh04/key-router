"""Keyword web search executed inside key-router.

There is no search-API key and no paid provider: the query goes to a SearXNG
instance you run yourself (configured in the dashboard, stored in `secrets.json`)
and the model decides when to search through a normal `web_search` function tool
that this proxy intercepts. The client never sees the tool call — it only gets
the finished answer.

Flow (both ingresses):

    model emits tool_use(web_search, {"query": ...})
      → proxy runs the search, appends a tool_result to the request
      → proxy re-calls the provider and returns only the final answer

A request may search at most MAX_SEARCHES_PER_REQUEST times; after that the tool
is withdrawn and the model must answer with the context it already has.
"""

import json
import re
import time
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import secrets as secrets_store
from config.logging import log_warn, logger
from models.anthropic import AnthropicRequest, AnthropicResponse, Message
from models.events import SSEEvent

SEARCH_TOOL_NAME = "web_search"
# Claude Code's own WebSearch tool is gated by its permission layer, so a model
# that calls that name instead of ours would just get denied. Intercept both.
SEARCH_TOOL_NAMES = {"web_search", "websearch"}
MAX_SEARCHES_PER_REQUEST = 3
MAX_RESULTS = 5
MAX_CACHE_ENTRIES = 128
CACHE_TTL_SECONDS = 600

NOT_CONFIGURED = (
    "Web search is not configured on this server. Tell the user to open the "
    "dashboard Settings → Web search and point it at a SearXNG instance; answer "
    "from your own knowledge for now and say the search was unavailable."
)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_cache: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}


def configured() -> bool:
    """True when a SearXNG URL is saved (dashboard Settings → Web search)."""
    return bool(secrets_store.get_searxng_url())


def search_tool_definition() -> Dict[str, Any]:
    return {
        "name": SEARCH_TOOL_NAME,
        "description": (
            "Search the web for current information. Use it when the answer depends on "
            "recent events, versions, prices or documentation you are not sure about. "
            "Returns the top results with title, URL and snippet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
            },
            "required": ["query"],
        },
    }


def is_hosted_search(tool: Dict[str, Any]) -> bool:
    """True for a client-declared hosted search tool (Codex `web_search`,
    Anthropic `web_search_20250305`) — it is not a function tool we can forward."""
    return str(tool.get("type", "")).startswith("web_search")


def _is_search_name(name: str) -> bool:
    return str(name or "").strip().lower() in SEARCH_TOOL_NAMES


def enable(anthropic_request: AnthropicRequest, always: bool = False) -> None:
    """Swap a client-declared hosted search tool for the proxy's own function tool.

    `always=True` adds it even when the client declares nothing (Codex app builds
    do not offer `web_search`). Without a configured SearXNG the hosted tool is
    still removed — the proxy cannot execute it — but ours is not offered.
    """
    tools = [t for t in (anthropic_request.tools or []) if isinstance(t, dict)]
    kept = [t for t in tools if not is_hosted_search(t)]
    stripped = len(kept) != len(tools)
    anthropic_request.tools = kept or None
    if not configured() or not (always or stripped):
        return
    appendable = list(anthropic_request.tools or [])
    if not any(t.get("name") == SEARCH_TOOL_NAME for t in appendable):
        appendable.append(search_tool_definition())
    anthropic_request.tools = appendable
    anthropic_request.web_search = True


class _SearxParser(HTMLParser):
    """SearXNG result list: <article class="result"><h3><a href>title</a></h3><p class="content">."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, str]] = []
        self._in_article = False
        self._in_heading = False
        self._capture: Optional[str] = None
        self._buffer: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "article" and "result" in classes:
            self.results.append({"title": "", "url": "", "snippet": ""})
            self._in_article = True
        elif self._in_article and tag == "h3":
            self._in_heading = True
        elif self._in_heading and tag == "a" and self.results:
            self.results[-1]["url"] = attributes.get("href") or ""
            self._capture, self._buffer = "title", []
        elif self._in_article and tag == "p" and "content" in classes and self.results:
            self._capture, self._buffer = "snippet", []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture == "title":
            self.results[-1]["title"] = " ".join("".join(self._buffer).split())
            self._capture = None
        elif tag == "p" and self._capture == "snippet":
            self.results[-1]["snippet"] = " ".join("".join(self._buffer).split())
            self._capture = None
        elif tag == "h3":
            self._in_heading = False
        elif tag == "article":
            self._in_article = False


async def _fetch_results(query: str, limit: int) -> List[Dict[str, str]]:
    base = secrets_store.get_searxng_url().rstrip("/")
    if not base:
        return []
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
        # The HTML view avoids requiring `formats: [html, json]` in the instance config.
        response = await client.get(
            f"{base}/search", params={"q": query}, headers={"User-Agent": _USER_AGENT}
        )
        response.raise_for_status()
    parser = _SearxParser()
    parser.feed(response.text)
    return [r for r in parser.results if r["url"]][:limit]


async def search(query: str, limit: int = MAX_RESULTS) -> List[Dict[str, str]]:
    """Search the web and return up to `limit` results (title, url, snippet)."""
    key = query.strip().lower()
    if not key:
        return []
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    try:
        results = await _fetch_results(query, limit)
    except Exception as exc:  # network, HTTP error, or an unexpected page shape
        log_warn(f"[🌐] web search failed for {query!r}: {exc}")
        return []
    if len(_cache) >= MAX_CACHE_ENTRIES:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), results)
    return results


def format_results(query: str, results: List[Dict[str, str]]) -> str:
    if not results:
        return f'No web results found for "{query}".'
    lines = [f'Web search results for "{query}":']
    for index, result in enumerate(results, 1):
        lines.append(f"{index}. {result['title']} — {result['url']}")
        if result.get("snippet"):
            lines.append(f"   {result['snippet']}")
    return "\n".join(lines)


def extract_tool_uses(events: List[SSEEvent]) -> List[Dict[str, Any]]:
    """Collect tool_use blocks from a buffered Anthropic SSE stream."""
    pending: Dict[Any, Dict[str, Any]] = {}
    order: List[Any] = []
    for event in events:
        data = event.data
        event_type = data.get("type")
        if event_type == "content_block_start":
            block = data.get("content_block") or {}
            if block.get("type") == "tool_use":
                index = data.get("index")
                pending[index] = {
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "partial": "",
                }
                order.append(index)
        elif event_type == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "input_json_delta":
                entry = pending.get(data.get("index"))
                if entry is not None:
                    entry["partial"] += delta.get("partial_json") or ""
    calls = []
    for index in order:
        entry = pending[index]
        try:
            payload = json.loads(entry["partial"]) if entry["partial"].strip() else {}
        except json.JSONDecodeError:
            payload = {}
        calls.append({
            "id": entry["id"],
            "name": entry["name"],
            "input": payload if isinstance(payload, dict) else {},
        })
    return calls


def response_tool_uses(response: AnthropicResponse) -> List[Dict[str, Any]]:
    return [
        {"id": b.get("id", ""), "name": b.get("name", ""), "input": b.get("input") or {}}
        for b in response.content
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]


def _search_calls(calls: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
    """Split our tool calls from the turn.

    Returns (ours, mixed). A mixed turn also asks the client to run its own tools,
    so we cannot answer it ourselves and must not leak a tool_use the client
    cannot execute.
    """
    ours = [call for call in calls if _is_search_name(call["name"])]
    return ours, bool(ours) and len(ours) != len(calls)


def _drop_search_blocks(events: List[SSEEvent]) -> List[SSEEvent]:
    """Remove our web_search tool_use blocks from an outgoing stream."""
    ours = set()
    for event in events:
        if event.data.get("type") == "content_block_start":
            block = event.data.get("content_block") or {}
            if block.get("type") == "tool_use" and _is_search_name(block.get("name")):
                ours.add(event.data.get("index"))
    if not ours:
        return events
    return [
        event for event in events
        if not (
            event.data.get("type") in ("content_block_start", "content_block_delta", "content_block_stop")
            and event.data.get("index") in ours
        )
    ]


def _without_search_blocks(response: AnthropicResponse) -> AnthropicResponse:
    response.content = [
        block for block in response.content
        if not (block.get("type") == "tool_use" and _is_search_name(block.get("name")))
    ]
    return response


async def _run_calls(calls: List[Dict[str, Any]]) -> Dict[str, str]:
    results: Dict[str, str] = {}
    for call in calls:
        if not configured():
            results[call["id"]] = NOT_CONFIGURED
            continue
        query = str((call.get("input") or {}).get("query") or "").strip()
        if not query:
            results[call["id"]] = "web_search needs a non-empty `query`."
            continue
        found = await search(query)
        logger.info(f"[🌐] web_search {query!r} → {len(found)} result(s)")
        results[call["id"]] = format_results(query, found)
    return results


def _append_exchange(
    anthropic_request: AnthropicRequest, calls: List[Dict[str, Any]], results: Dict[str, str]
) -> None:
    anthropic_request.messages.append(Message(role="assistant", content=[
        {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["input"]}
        for call in calls
    ]))
    anthropic_request.messages.append(Message(role="user", content=[
        {"type": "tool_result", "tool_use_id": call["id"], "content": results[call["id"]]}
        for call in calls
    ]))


def _withdraw(anthropic_request: AnthropicRequest) -> None:
    anthropic_request.tools = [
        t for t in (anthropic_request.tools or []) if t.get("name") != SEARCH_TOOL_NAME
    ]
    note = (
        "The web search budget for this turn is exhausted. Answer now with the results "
        "you already have; do not call web_search again."
    )
    if isinstance(anthropic_request.system, list):
        anthropic_request.system.append({"type": "text", "text": note})
    else:
        anthropic_request.system = ((anthropic_request.system or "") + "\n\n" + note).strip()


async def run_stream(
    provider, anthropic_request: AnthropicRequest, body: Dict[str, Any]
) -> List[SSEEvent]:
    """Buffer a provider stream, running any web_search calls the model makes."""
    events: List[SSEEvent] = []
    for used in range(MAX_SEARCHES_PER_REQUEST + 1):
        events = [event async for event in provider.stream(body)]
        calls, mixed = _search_calls(extract_tool_uses(events))
        if not calls:
            return events
        if mixed or used == MAX_SEARCHES_PER_REQUEST:
            return _drop_search_blocks(events)
        _append_exchange(anthropic_request, calls, await _run_calls(calls))
        if used + 1 >= MAX_SEARCHES_PER_REQUEST:
            _withdraw(anthropic_request)
        body = await provider.translate_request(anthropic_request)
    return _drop_search_blocks(events)


async def run_generate(
    provider, anthropic_request: AnthropicRequest, body: Dict[str, Any]
) -> AnthropicResponse:
    """Same as run_stream for the non-streaming path."""
    response = await provider.generate(body)
    for used in range(MAX_SEARCHES_PER_REQUEST):
        calls, mixed = _search_calls(response_tool_uses(response))
        if not calls:
            return response
        if mixed:
            return _without_search_blocks(response)
        _append_exchange(anthropic_request, calls, await _run_calls(calls))
        if used + 1 >= MAX_SEARCHES_PER_REQUEST:
            _withdraw(anthropic_request)
        body = await provider.translate_request(anthropic_request)
        response = await provider.generate(body)
    return _without_search_blocks(response)
