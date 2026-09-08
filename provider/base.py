from abc import ABC, abstractmethod
from typing import AsyncIterator, Dict, Any
from models.anthropic import AnthropicRequest, AnthropicResponse
from models.events import SSEEvent

class BaseProvider(ABC):
    supports_anthropic: bool = False
    supports_openai: bool = False

    def __init__(self, target_model: str):
        self.target_model = target_model

    @abstractmethod
    async def translate_request(self, anthropic_request: AnthropicRequest) -> Dict[str, Any]:
        """Convert Anthropic Messages body to provider-native body."""
        pass

    @abstractmethod
    async def translate_response(self, provider_response: Dict[str, Any]) -> AnthropicResponse:
        """Convert provider-native response to Anthropic Messages response."""
        pass

    @abstractmethod
    async def stream(self, request_body: Dict[str, Any]) -> AsyncIterator[SSEEvent]:
        """Send request and yield Anthropic-compatible SSE events."""
        yield SSEEvent(event="ping", data={}) # yield for type hint

    @abstractmethod
    async def generate(self, request_body: Dict[str, Any]) -> AnthropicResponse:
        """Send request and return AnthropicResponse (non-streaming)."""
        pass
