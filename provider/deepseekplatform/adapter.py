import json
import httpx
from typing import AsyncIterator, Dict, Any
from provider.base import BaseProvider
from provider.openai_base import OpenAIBaseProvider
from models.anthropic import AnthropicRequest, AnthropicResponse
from models.events import SSEEvent
from config.settings import settings

class DeepSeekAnthropicProvider(BaseProvider):
    supports_anthropic: bool = True

    def __init__(self, target_model: str):
        super().__init__(target_model)
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url_anthropic.rstrip("/")

    async def translate_request(self, anthropic_request: AnthropicRequest) -> Dict[str, Any]:
        body = anthropic_request.model_dump(exclude_none=True)
        body["model"] = self.target_model
        return body

    async def translate_response(self, provider_response: Dict[str, Any]) -> AnthropicResponse:
        return AnthropicResponse(**provider_response)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def generate(self, request_body: Dict[str, Any]) -> AnthropicResponse:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/v1/messages",
                headers=self._get_headers(),
                json=request_body,
                timeout=120.0,
            )
            resp.raise_for_status()
            return await self.translate_response(resp.json())

    async def stream(self, request_body: Dict[str, Any]) -> AsyncIterator[SSEEvent]:
        request_body["stream"] = True
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/messages",
                headers=self._get_headers(),
                json=request_body,
                timeout=120.0,
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


DeepSeekProvider = DeepSeekAnthropicProvider


class DeepSeekOpenAIProvider(OpenAIBaseProvider):
    supports_openai: bool = True

    def __init__(self, target_model: str):
        super().__init__(
            target_model=target_model,
            base_url=settings.deepseek_base_url_openai.rstrip("/"),
            api_key=settings.deepseek_api_key,
        )
