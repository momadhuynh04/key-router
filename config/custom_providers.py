import json
import os
import re
from functools import lru_cache
from typing import Dict, Any

from config.secrets import get_custom_key, mask_secret

ID_RE = re.compile(r"^[a-z0-9_-]{2,32}$")
BUILTIN_IDS = {"openrouter", "deepseekplatform", "googleaistudio"}

def _get_config_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "config.json")

def _read_config() -> Dict[str, Any]:
    path = _get_config_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def _write_config(data: Dict[str, Any]) -> None:
    path = _get_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def validate_spec(spec: Dict[str, Any]) -> None:
    pid = spec.get("id", "")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        raise ValueError("Provider ID must be 2-32 chars: lowercase letters, numbers, hyphens, underscores")
    if pid in BUILTIN_IDS:
        raise ValueError(f"Provider ID '{pid}' conflicts with built-in provider")
    display = spec.get("display_name", "")
    if not isinstance(display, str) or not display.strip():
        raise ValueError("Display name is required")
    api = spec.get("provider_api", "")
    if api not in ("openai_compatible", "anthropic"):
        raise ValueError("provider_api must be 'openai_compatible' or 'anthropic'")
    base_url = spec.get("base_url", "")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("Base URL is required")
    if not re.match(r"^https?://", base_url.strip()):
        raise ValueError("Base URL must start with http:// or https://")
    models = spec.get("models", [])
    if not isinstance(models, list) or len(models) == 0:
        raise ValueError("At least one model is required")
    seen = set()
    for m in models:
        if not isinstance(m, dict):
            raise ValueError("Each model must be an object with id and name")
        mid = m.get("id", "")
        if not isinstance(mid, str) or not mid.strip():
            raise ValueError("Each model must have a non-empty id")
        if mid in seen:
            raise ValueError(f"Duplicate model id '{mid}'")
        seen.add(mid)
        if not isinstance(m.get("name", ""), str) or not m.get("name", "").strip():
            raise ValueError(f"Model '{mid}' must have a display name")
    headers = spec.get("headers")
    if headers is not None:
        if not isinstance(headers, dict):
            raise ValueError("headers must be an object")
        for k, v in headers.items():
            if not isinstance(k, str) or not k.strip():
                raise ValueError("Header name must be non-empty string")
            if not isinstance(v, str):
                raise ValueError(f"Header '{k}' value must be string")

def load_custom_providers() -> Dict[str, Dict[str, Any]]:
    return _load_custom_providers_cached()

@lru_cache(maxsize=1)
def _load_custom_providers_cached() -> Dict[str, Dict[str, Any]]:
    data = _read_config()
    raw = data.get("custom_providers", {})
    if not isinstance(raw, dict):
        return {}
    return dict(raw)

def invalidate_custom_providers_cache() -> None:
    _load_custom_providers_cached.cache_clear()

def save_custom_providers(providers: Dict[str, Dict[str, Any]]) -> None:
    data = _read_config()
    data["custom_providers"] = providers
    _write_config(data)
    invalidate_custom_providers_cache()

def save_provider(spec: Dict[str, Any]) -> None:
    providers = dict(load_custom_providers())
    pid = spec["id"]
    providers[pid] = spec
    save_custom_providers(providers)

def delete_provider(pid: str) -> bool:
    providers = dict(load_custom_providers())
    if pid not in providers:
        return False
    del providers[pid]
    save_custom_providers(providers)
    return True

def get_api_key_for_provider(spec: Dict[str, Any]) -> str:
    return get_custom_key(spec.get("id", ""))

def models_headers(spec: Dict[str, Any], api_key: str | None = None) -> Dict[str, str]:
    key = get_api_key_for_provider(spec) if api_key is None else api_key
    if spec.get("provider_api") == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"} if key else {}
    else:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    headers.update(spec.get("headers") or {})
    return headers

def get_masked_providers() -> Dict[str, Dict[str, Any]]:
    providers = load_custom_providers()
    masked: Dict[str, Dict[str, Any]] = {}
    for pid, spec in providers.items():
        key = get_api_key_for_provider(spec)
        masked[pid] = {
            "id": spec.get("id"),
            "display_name": spec.get("display_name"),
            "provider_api": spec.get("provider_api"),
            "base_url": spec.get("base_url"),
            "has_key": bool(key),
            "api_key_preview": mask_secret(key),
            "key_source": "json" if key else "missing",
            "headers": spec.get("headers") or {},
            "models": spec.get("models") or [],
        }
    return masked
