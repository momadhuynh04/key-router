import json
import httpx
from typing import AsyncIterator, Dict, Any
from provider.base import BaseProvider
from provider.openai_base import OpenAIBaseProvider
from config.settings import settings
from models.anthropic import AnthropicRequest, AnthropicResponse
from models.events import SSEEvent


class OpenRouterProvider(OpenAIBaseProvider):
    supports_openai: bool = True

    def __init__(self, target_model: str):
        super().__init__(
            target_model=target_model,
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key
        )

    def _get_headers(self) -> Dict[str, str]:
        headers = super()._get_headers()
        headers.update({
            "HTTP-Referer": "https://github.com/momadhuynh04/key-router",
            "X-Title": "key-router Proxy"
        })
        return headers


class OpenRouterAnthropicProvider(BaseProvider):
    supports_anthropic: bool = True

    def __init__(self, target_model: str):
        super().__init__(target_model)
        self.base_url = "https://openrouter.ai/api"
        self.api_key = settings.openrouter_api_key

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/momadhuynh04/key-router",
            "X-Title": "key-router Proxy",
            "anthropic-version": "2023-06-01",
        }

    def _sanitize_tools(self, body: Dict[str, Any]):
        tools = body.get("tools")
        if not isinstance(tools, list):
            return
        import re as _re
        def clean_schema(node):
            if isinstance(node, dict):
                if "pattern" in node:
                    pat = node.get("pattern")
                    if not isinstance(pat, str):
                        node.pop("pattern", None)
                    else:
                        try:
                            _re.compile(pat)
                        except re.error:
                            node.pop("pattern", None)
                    # Cohere strict validator rejects many valid ECMA patterns; drop pattern for cohere targets
                    if "cohere" in self.target_model.lower() or "north" in self.target_model.lower():
                        node.pop("pattern", None)
                for v in list(node.values()):
                    clean_schema(v)
            elif isinstance(node, list):
                for item in node:
                    clean_schema(item)
        for t in tools:
            if isinstance(t, dict):
                schema = t.get("input_schema")
                if isinstance(schema, dict):
                    clean_schema(schema)
                else:
                    clean_schema(t)
        body["tools"] = tools

    async def translate_request(self, anthropic_request: AnthropicRequest) -> Dict[str, Any]:
        body = anthropic_request.model_dump(exclude_none=True)
        body["model"] = self.target_model
        self._sanitize_tools(body)
        if "dots" in self.target_model.lower():
            body["extra_body"] = body.get("extra_body", {})
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = body.get("thinking", {}).get("type") == "enabled" if isinstance(body.get("thinking"), dict) else False
            if anthropic_request.max_tokens and anthropic_request.max_tokens < 8192:
                body["max_tokens"] = 16384
            elif not anthropic_request.max_tokens:
                body["max_tokens"] = 16384
            if anthropic_request.tools and not anthropic_request.tool_choice:
                body["tool_choice"] = {"type": "auto"}
        if "cohere" in self.target_model.lower() or "north" in self.target_model.lower():
            body.pop("extra_body", None)
            body.pop("chat_template_kwargs", None)
        return body

    async def translate_response(self, provider_response: Dict[str, Any]) -> AnthropicResponse:
        return AnthropicResponse(**provider_response)

    async def generate(self, request_body: Dict[str, Any]) -> AnthropicResponse:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            resp = await client.post(
                f"{self.base_url}/v1/messages",
                headers=self._get_headers(),
                json=request_body,
            )
            resp.raise_for_status()
            return await self.translate_response(resp.json())

    async def stream(self, request_body: Dict[str, Any]) -> AsyncIterator[SSEEvent]:
        request_body["stream"] = True
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/messages",
                headers=self._get_headers(),
                json=request_body,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        event_type = chunk.get("type", "ping")
                        yield SSEEvent(event=event_type, data=chunk)
                    except json.JSONDecodeError:
                        continue
