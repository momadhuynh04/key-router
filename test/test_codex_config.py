import os
import tomllib
import pytest

from config.codex_config import (
    KEY_ROUTER_PROVIDER_ID,
    API_KEY_ENV,
    API_KEY_VALUE,
    DEFAULT_BASE_URL,
    get_codex_config_path,
    setup_codex_config,
    persist_codex_env_var,
    _dump_toml,
)


# ----------------------------------------
# 1. TOML serializer round-trips
# ----------------------------------------

def test_dump_toml_scalars_and_tables():
    data = {
        "model": "gpt-5-codex",
        "disable_response_storage": True,
        "retries": 3,
        "ratio": 0.5,
        "tags": ["a", "b"],
        "model_providers": {
            KEY_ROUTER_PROVIDER_ID: {
                "name": "key-router",
                "base_url": DEFAULT_BASE_URL,
                "wire_api": "chat",
            }
        },
    }
    parsed = tomllib.loads(_dump_toml(data))
    assert parsed["model"] == "gpt-5-codex"
    assert parsed["disable_response_storage"] is True
    assert parsed["retries"] == 3
    assert parsed["ratio"] == 0.5
    assert parsed["tags"] == ["a", "b"]
    provider = parsed["model_providers"][KEY_ROUTER_PROVIDER_ID]
    assert provider == {"name": "key-router", "base_url": DEFAULT_BASE_URL, "wire_api": "chat"}


# ----------------------------------------
# 2. setup_codex_config behavior
# ----------------------------------------

def test_setup_creates_fresh_config(tmp_path):
    path = tmp_path / ".codex" / "config.toml"
    changed = setup_codex_config(config_path=str(path))
    assert changed is True
    assert path.exists()

    with open(path, "rb") as f:
        data = tomllib.load(f)

    assert data["model_provider"] == KEY_ROUTER_PROVIDER_ID
    provider = data["model_providers"][KEY_ROUTER_PROVIDER_ID]
    assert provider["base_url"] == DEFAULT_BASE_URL
    assert provider["env_key"] == API_KEY_ENV  # codex >= 0.149 requires env_key
    assert provider["wire_api"] == "responses"
    assert provider["name"] == "key-router"

def test_setup_preserves_existing_user_settings(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'model = "my-custom-model"\n'
        "approval_policy = \"never\"\n"
        "\n"
        "[model_providers.other]\n"
        'name = "Other"\n'
        'base_url = "https://example.com/v1"\n',
        encoding="utf-8",
    )

    setup_codex_config(config_path=str(path))

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # User keys untouched
    assert data["model"] == "my-custom-model"
    assert data["approval_policy"] == "never"
    assert data["model_providers"]["other"]["base_url"] == "https://example.com/v1"
    # Managed section added
    assert data["model_providers"][KEY_ROUTER_PROVIDER_ID]["wire_api"] == "responses"

def test_setup_idempotent_returns_false_when_unchanged(tmp_path):
    path = tmp_path / "config.toml"
    first = setup_codex_config(config_path=str(path))
    mtime_first = os.path.getmtime(path)
    second = setup_codex_config(config_path=str(path))

    assert first is True
    assert second is False
    assert os.path.getmtime(path) == mtime_first

def test_setup_updates_changed_base_url(tmp_path):
    path = tmp_path / "config.toml"
    setup_codex_config(config_path=str(path), base_url=DEFAULT_BASE_URL)
    changed = setup_codex_config(config_path=str(path), base_url="http://127.0.0.1:9999/v1")

    assert changed is True
    with open(path, "rb") as f:
        data = tomllib.load(f)
    assert data["model_providers"][KEY_ROUTER_PROVIDER_ID]["base_url"] == "http://127.0.0.1:9999/v1"

def test_persist_env_var_writes_environment_d(tmp_path, monkeypatch):
    """On Linux the env var is persisted via environment.d for GUI apps."""
    import platform
    if platform.system() != "Linux":
        pytest.skip("Linux-only test")

    applied = persist_codex_env_var(home=str(tmp_path))
    conf = tmp_path / ".config" / "environment.d" / "key-router.conf"
    assert applied and str(conf) in applied
    assert conf.read_text() == f"{API_KEY_ENV}={API_KEY_VALUE}\n"

    # Idempotent — same content, still reported as applied
    applied_again = persist_codex_env_var(home=str(tmp_path))
    assert str(conf) in applied_again
    assert conf.read_text() == f"{API_KEY_ENV}={API_KEY_VALUE}\n"

def test_get_codex_config_path_uses_home():
    path = get_codex_config_path(home="/home/tester")
    assert path == os.path.join("/home/tester", ".codex", "config.toml")
