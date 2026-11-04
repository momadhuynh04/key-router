import json
import os

import pytest

from config import secrets as store


@pytest.fixture
def secrets_path(tmp_path, monkeypatch):
    path = tmp_path / "secrets.json"
    monkeypatch.setattr(store, "_get_secrets_path", lambda: str(path))
    return path


def test_mask_secret():
    assert store.mask_secret("") == ""
    assert store.mask_secret("short") == "****"
    assert store.mask_secret("sk-1234567890abcd") == "sk-1****abcd"


def test_save_is_atomic_and_private(secrets_path):
    store.set_builtin_key("openrouter", "sk-json-123")

    data = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert data["keys"]["openrouter"] == "sk-json-123"
    if os.name == "posix":
        assert (os.stat(secrets_path).st_mode & 0o777) == 0o600


def test_builtin_key_roundtrip(secrets_path):
    assert store.get_builtin_key("openrouter") == ""
    assert store.builtin_key_source("openrouter") == "missing"

    store.set_builtin_key("openrouter", "sk-json-123")

    assert store.get_builtin_key("openrouter") == "sk-json-123"
    assert store.builtin_key_source("openrouter") == "json"
    assert store.delete_builtin_key("openrouter") is True
    assert store.get_builtin_key("openrouter") == ""
    assert store.delete_builtin_key("openrouter") is False


def test_custom_key_roundtrip(secrets_path):
    store.set_custom_key("myprov", "sk-custom")

    assert store.get_custom_key("myprov") == "sk-custom"
    assert store.delete_custom_key("myprov") is True
    assert store.get_custom_key("myprov") == ""


def test_base_url_defaults_and_override(secrets_path):
    assert store.get_builtin_base_url("deepseek_openai") == "https://api.deepseek.com"
    assert store.default_builtin_base_url("deepseek_openai") == "https://api.deepseek.com"

    store.set_builtin_base_url("deepseek_openai", "https://mirror.example/v1/")

    assert store.get_builtin_base_url("deepseek_openai") == "https://mirror.example/v1"
    assert store.default_builtin_base_url("deepseek_openai") == "https://api.deepseek.com"

    assert store.reset_builtin_base_url("deepseek_openai") is True
    assert store.get_builtin_base_url("deepseek_openai") == "https://api.deepseek.com"


def test_missing_secrets_file_is_empty(secrets_path):
    assert store.load_secrets() == {"keys": {}, "custom_keys": {}, "base_urls": {}}
