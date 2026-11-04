import re
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import secrets as secrets_store
from config.custom_providers import get_api_key_for_provider, load_custom_providers

router = APIRouter()

BUILTIN_LABELS = {
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
    "google": "Google AI Studio",
}

BASE_URL_LABELS = {
    "openrouter": "OpenRouter",
    "deepseek_anthropic": "DeepSeek (Anthropic)",
    "deepseek_openai": "DeepSeek (OpenAI)",
    "google": "Google AI Studio",
}


class KeyValue(BaseModel):
    value: str


class BaseUrlValue(BaseModel):
    value: str


def _settings_dto() -> Dict[str, Any]:
    values = secrets_store.load_secrets()

    keys = []
    for name in secrets_store.BUILTIN_KEYS:
        value = secrets_store.get_builtin_key(name)
        keys.append({
            "scope": "builtin",
            "id": name,
            "label": BUILTIN_LABELS.get(name, name),
            "has_key": bool(value),
            "preview": secrets_store.mask_secret(value),
            "source": secrets_store.builtin_key_source(name),
        })

    for pid, spec in load_custom_providers().items():
        value = get_api_key_for_provider(spec)
        keys.append({
            "scope": "custom",
            "id": pid,
            "label": spec.get("display_name") or pid,
            "has_key": bool(value),
            "preview": secrets_store.mask_secret(value),
            "source": "json" if value else "missing",
        })

    base_urls = []
    for name, default in secrets_store.BUILTIN_BASE_URLS.items():
        base_urls.append({
            "id": name,
            "label": BASE_URL_LABELS.get(name, name),
            "value": secrets_store.get_builtin_base_url(name),
            "default": default,
            "source": "json" if values["base_urls"].get(name) else "default",
        })

    return {"keys": keys, "base_urls": base_urls, "secrets_path": "secrets.json"}


def _invalidate_caches() -> None:
    from proxy.routers.management import invalidate_available_models
    invalidate_available_models()


def _require_builtin(key_id: str) -> None:
    if key_id not in secrets_store.BUILTIN_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown built-in key '{key_id}'")


def _require_custom(key_id: str) -> None:
    if key_id not in load_custom_providers():
        raise HTTPException(status_code=404, detail=f"Provider '{key_id}' not found")


@router.get("/api/settings")
async def get_settings():
    return _settings_dto()


@router.put("/api/settings/keys/{scope}/{key_id}")
async def set_api_key(scope: str, key_id: str, body: KeyValue):
    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=400, detail="API key value is required")
    if scope == "builtin":
        _require_builtin(key_id)
        secrets_store.set_builtin_key(key_id, value)
    elif scope == "custom":
        _require_custom(key_id)
        secrets_store.set_custom_key(key_id, value)
    else:
        raise HTTPException(status_code=404, detail=f"Unknown key scope '{scope}'")
    _invalidate_caches()
    return _settings_dto()


@router.delete("/api/settings/keys/{scope}/{key_id}")
async def delete_api_key(scope: str, key_id: str):
    if scope == "builtin":
        _require_builtin(key_id)
        secrets_store.delete_builtin_key(key_id)
    elif scope == "custom":
        _require_custom(key_id)
        secrets_store.delete_custom_key(key_id)
    else:
        raise HTTPException(status_code=404, detail=f"Unknown key scope '{scope}'")
    _invalidate_caches()
    return _settings_dto()


@router.put("/api/settings/base-urls/{name}")
async def set_base_url(name: str, body: BaseUrlValue):
    if name not in secrets_store.BUILTIN_BASE_URLS:
        raise HTTPException(status_code=404, detail=f"Unknown base URL '{name}'")
    value = body.value.strip()
    if not re.match(r"^https?://\S+$", value):
        raise HTTPException(status_code=400, detail="Base URL must start with http:// or https://")
    secrets_store.set_builtin_base_url(name, value)
    _invalidate_caches()
    return _settings_dto()


@router.delete("/api/settings/base-urls/{name}")
async def reset_base_url(name: str):
    if name not in secrets_store.BUILTIN_BASE_URLS:
        raise HTTPException(status_code=404, detail=f"Unknown base URL '{name}'")
    secrets_store.reset_builtin_base_url(name)
    _invalidate_caches()
    return _settings_dto()
