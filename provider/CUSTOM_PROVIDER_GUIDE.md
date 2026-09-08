# How to Build a Custom Provider Adapter

To add support for a custom LLM provider in `key-router`, you need to write a provider adapter that extends `BaseProvider` or `OpenAIBaseProvider` and register it in the router.

## 1. Create your Provider Adapter
Create a new directory in `provider/` (e.g., `provider/my_provider/`) and add an `adapter.py` file.

If your provider is OpenAI-compatible, you can simply inherit from `OpenAIBaseProvider`:

```python
from provider.openai_base import OpenAIBaseProvider

class MyProvider(OpenAIBaseProvider):
    def __init__(self, target_model: str):
        # Pass the base URL and your API key here
        super().__init__(
            base_url="https://api.myprovider.com/v1",
            api_key="sk-your-api-key",
            target_model=target_model
        )
```

If your provider has a completely different API (like Google Gemini native API, Anthropic native API, etc.), you must inherit from `BaseProvider` and implement the abstract methods:

```python
from provider.base import BaseProvider
from models.anthropic import AnthropicRequest

class MyCustomNativeProvider(BaseProvider):
    def __init__(self, target_model: str):
        self.target_model = target_model
        self.api_key = "..."

    async def translate_request(self, request: AnthropicRequest) -> dict:
        # Convert Anthropic format to your provider's format
        pass

    async def stream(self, provider_request: dict):
        # Make the request and yield SSEEvent objects formatted for Anthropic
        pass
```

## 2. Register your Provider
Open `proxy/router.py` and import your new provider, then add it to the `ProviderRouter` logic:

```python
from provider.my_provider.adapter import MyProvider

class ProviderRouter:
    def get_provider(self, requested_model: str) -> BaseProvider:
        provider_name, target_model = model_mapper.resolve(requested_model)
        
        if provider_name == "openrouter":
            return OpenRouterProvider(target_model=target_model)
        elif provider_name == "myprovider":
            return MyProvider(target_model=target_model)
        # ...
```

## 3. Map Models
In your `.env` file, map the Claude model to your new provider:

```env
MODEL_OPUS="myprovider/your-target-model-name"
```

## 4. (Optional) Add to WebUI
To make your provider selectable in the WebUI launcher:
1. Edit `proxy/server.py` and update `models_data` dictionary to fetch/include your provider's available models.
2. Edit `webui/src/App.tsx` and add your provider to the `Target Provider` `<select>` dropdown.
