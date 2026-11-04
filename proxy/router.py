from config.model_map import model_mapper
from config.custom_providers import load_custom_providers, get_api_key_for_provider
from provider.openrouter.adapter import OpenRouterProvider, OpenRouterAnthropicProvider
from provider.deepseekplatform.adapter import DeepSeekAnthropicProvider, DeepSeekOpenAIProvider
from provider.base import BaseProvider


BUILTIN_PROVIDER_NAMES = {"openrouter", "deepseekplatform", "googleaistudio"}


class ProviderRouter:
    def _resolve_direct(self, requested_model: str):
        """Resolve `<provider>/<model>` without consulting config.json routes.

        This is how the Test lab and power users address a provider directly,
        e.g. `openrouter/anthropic/claude-sonnet-4.5`.
        """
        if "/" not in requested_model:
            return None
        maybe_provider, maybe_model = requested_model.split("/", 1)
        if maybe_provider in BUILTIN_PROVIDER_NAMES:
            return maybe_provider, maybe_model
        try:
            if maybe_provider in load_custom_providers():
                return maybe_provider, maybe_model
        except Exception:
            pass
        return None

    def _resolve(self, requested_model: str):
        direct = self._resolve_direct(requested_model)
        if direct is not None:
            return direct
        return model_mapper.resolve(requested_model)

    def _custom_provider(self, provider_name: str, target_model: str) -> BaseProvider | None:
        spec = load_custom_providers().get(provider_name)
        if not spec:
            return None
        api_key = get_api_key_for_provider(spec)
        if not api_key and not spec.get("headers"):
            raise ValueError(
                f"Missing API key for provider '{provider_name}' — add it in Settings (stored in secrets.json)"
            )
        headers = spec.get("headers") or None
        base_url = spec.get("base_url", "")
        if spec.get("provider_api") == "anthropic":
            from provider.custom.adapter import GenericAnthropicProvider
            return GenericAnthropicProvider(
                target_model=target_model, base_url=base_url, api_key=api_key, extra_headers=headers
            )
        from provider.custom.adapter import GenericOpenAIProvider
        return GenericOpenAIProvider(
            target_model=target_model, base_url=base_url, api_key=api_key, extra_headers=headers
        )

    def get_provider(self, requested_model: str) -> BaseProvider:
        provider_name, target_model = self._resolve(requested_model)

        if provider_name == "openrouter":
            return OpenRouterProvider(target_model=target_model)
        elif provider_name == "deepseekplatform":
            return DeepSeekAnthropicProvider(target_model=target_model)
        elif provider_name == "googleaistudio":
            from provider.googleaistudio.adapter import GoogleAIStudioProvider
            return GoogleAIStudioProvider(target_model=target_model)

        custom = self._custom_provider(provider_name, target_model)
        if custom is not None:
            return custom

        raise ValueError(f"Unknown provider '{provider_name}'")

    def get_provider_for_anthropic(self, requested_model: str) -> BaseProvider:
        orig = ProviderRouter.get_provider.__get__(self, type(self))
        if self.get_provider != orig:
            return self.get_provider(requested_model)
        provider_name, target_model = self._resolve(requested_model)

        if provider_name == "openrouter":
            return OpenRouterAnthropicProvider(target_model=target_model)
        elif provider_name == "deepseekplatform":
            return DeepSeekAnthropicProvider(target_model=target_model)
        elif provider_name == "googleaistudio":
            from provider.googleaistudio.adapter import GoogleAIStudioProvider
            return GoogleAIStudioProvider(target_model=target_model)

        custom = self._custom_provider(provider_name, target_model)
        if custom is not None:
            return custom

        raise ValueError(f"Unknown provider '{provider_name}'")

    def get_provider_for_openai(self, requested_model: str) -> BaseProvider:
        orig = ProviderRouter.get_provider.__get__(self, type(self))
        if self.get_provider != orig:
            return self.get_provider(requested_model)
        provider_name, target_model = self._resolve(requested_model)

        if provider_name == "openrouter":
            return OpenRouterProvider(target_model=target_model)
        elif provider_name == "deepseekplatform":
            return DeepSeekOpenAIProvider(target_model=target_model)
        elif provider_name == "googleaistudio":
            from provider.googleaistudio.adapter import GoogleAIStudioProvider
            return GoogleAIStudioProvider(target_model=target_model)

        custom = self._custom_provider(provider_name, target_model)
        if custom is not None:
            return custom

        raise ValueError(f"Unknown provider '{provider_name}'")


provider_router = ProviderRouter()
