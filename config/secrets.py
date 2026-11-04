import json
import os
import tempfile
from typing import Any, Dict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BUILTIN_KEYS = ("openrouter", "deepseek", "google")

BUILTIN_BASE_URLS: Dict[str, str] = {
    "openrouter": "https://openrouter.ai/api",
    "deepseek_anthropic": "https://api.deepseek.com/anthropic",
    "deepseek_openai": "https://api.deepseek.com",
    "google": "https://generativelanguage.googleapis.com/v1beta",
}


def _get_secrets_path() -> str:
    return os.path.join(BASE_DIR, "secrets.json")


def _empty() -> Dict[str, Dict[str, str]]:
    return {"keys": {}, "custom_keys": {}, "base_urls": {}}


def _read_raw() -> Dict[str, Any]:
    path = _get_secrets_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_secrets() -> Dict[str, Dict[str, str]]:
    raw = _read_raw()
    data = _empty()
    for section in data:
        values = raw.get(section)
        if not isinstance(values, dict):
            continue
        data[section] = {
            k: v for k, v in values.items()
            if isinstance(k, str) and isinstance(v, str) and v
        }
    return data


def save_secrets(data: Dict[str, Dict[str, str]]) -> None:
    path = _get_secrets_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".secrets-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def mask_secret(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "****"
    return f"{text[:4]}****{text[-4:]}"


def get_builtin_key(name: str) -> str:
    return load_secrets()["keys"].get(name, "")


def set_builtin_key(name: str, value: str) -> None:
    if name not in BUILTIN_KEYS:
        raise KeyError(name)
    value = (value or "").strip()
    if not value:
        raise ValueError("API key value is required")
    data = load_secrets()
    data["keys"][name] = value
    save_secrets(data)


def delete_builtin_key(name: str) -> bool:
    data = load_secrets()
    if name not in data["keys"]:
        return False
    del data["keys"][name]
    save_secrets(data)
    return True


def get_custom_key(provider_id: str) -> str:
    if not provider_id:
        return ""
    return load_secrets()["custom_keys"].get(provider_id, "")


def set_custom_key(provider_id: str, value: str) -> None:
    provider_id = (provider_id or "").strip()
    value = (value or "").strip()
    if not provider_id:
        raise ValueError("Provider ID is required")
    if not value:
        raise ValueError("API key value is required")
    data = load_secrets()
    data["custom_keys"][provider_id] = value
    save_secrets(data)


def delete_custom_key(provider_id: str) -> bool:
    data = load_secrets()
    if provider_id not in data["custom_keys"]:
        return False
    del data["custom_keys"][provider_id]
    save_secrets(data)
    return True


def get_builtin_base_url(name: str) -> str:
    value = load_secrets()["base_urls"].get(name, "")
    if value:
        return value
    return BUILTIN_BASE_URLS.get(name, "")


def default_builtin_base_url(name: str) -> str:
    return BUILTIN_BASE_URLS.get(name, "")


def set_builtin_base_url(name: str, value: str) -> None:
    if name not in BUILTIN_BASE_URLS:
        raise KeyError(name)
    value = (value or "").strip().rstrip("/")
    if not value:
        raise ValueError("Base URL value is required")
    data = load_secrets()
    data["base_urls"][name] = value
    save_secrets(data)


def reset_builtin_base_url(name: str) -> bool:
    data = load_secrets()
    if name not in data["base_urls"]:
        return False
    del data["base_urls"][name]
    save_secrets(data)
    return True


def builtin_key_source(name: str) -> str:
    return "json" if get_builtin_key(name) else "missing"
