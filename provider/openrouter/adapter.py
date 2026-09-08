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

    async def translate_request(self, anthropic_request: AnthropicRequest) -> Dict[str, Any]:
        body = anthropic_request.model_dump(exclude_none=True)
        body["model"] = self.target_model
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
