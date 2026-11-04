import pytest
from fastapi.testclient import TestClient

from config.custom_providers import invalidate_custom_providers_cache
from config.secrets import get_builtin_key, get_custom_key
from proxy.app import create_app


@pytest.fixture
def env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    secrets_path = tmp_path / "secrets.json"
    monkeypatch.setattr("config.custom_providers._get_config_path", lambda: str(config_path))
    monkeypatch.setattr("config.secrets._get_secrets_path", lambda: str(secrets_path))
    invalidate_custom_providers_cache()
    yield
    invalidate_custom_providers_cache()


@pytest.fixture
def client(env):
    return TestClient(create_app())


def _provider_payload(**overrides):
    payload = {
        "id": "myprov",
        "display_name": "My Provider",
        "provider_api": "openai_compatible",
        "base_url": "https://api.my.ai/v1",
        "api_key": "sk-initial-1234567890",
        "headers": {},
        "models": [{"id": "m1", "name": "M1", "reasoning": False, "image": False}],
    }
    payload.update(overrides)
    return payload


def _key_row(data, scope, key_id):
    return next(k for k in data["keys"] if k["scope"] == scope and k["id"] == key_id)


def test_get_settings_shape(client):
    data = client.get("/api/settings").json()

    assert data["secrets_path"] == "secrets.json"
    assert {k["id"] for k in data["keys"] if k["scope"] == "builtin"} == {"openrouter", "deepseek", "google"}
    assert {b["id"] for b in data["base_urls"]} == {
        "openrouter", "deepseek_anthropic", "deepseek_openai", "google",
    }
    assert all(k["source"] == "missing" for k in data["keys"] if k["scope"] == "builtin")


def test_put_and_delete_builtin_key(client):
    response = client.put("/api/settings/keys/builtin/openrouter", json={"value": "sk-json-123456"})

    assert response.status_code == 200
    row = _key_row(response.json(), "builtin", "openrouter")
    assert row["source"] == "json"
    assert row["has_key"] is True
    assert row["preview"] == "sk-j****3456"
    assert get_builtin_key("openrouter") == "sk-json-123456"

    response = client.delete("/api/settings/keys/builtin/openrouter")

    assert response.status_code == 200
    assert _key_row(response.json(), "builtin", "openrouter")["source"] == "missing"


def test_put_and_delete_custom_key(client):
    client.post("/api/custom-providers", json=_provider_payload())

    response = client.put("/api/settings/keys/custom/myprov", json={"value": "sk-new-0987654321"})

    assert response.status_code == 200
    row = _key_row(response.json(), "custom", "myprov")
    assert row["source"] == "json"
    assert get_custom_key("myprov") == "sk-new-0987654321"

    response = client.delete("/api/settings/keys/custom/myprov")

    assert response.status_code == 200
    assert get_custom_key("myprov") == ""


def test_unknown_ids_and_scope(client):
    assert client.put("/api/settings/keys/builtin/nope", json={"value": "x"}).status_code == 404
    assert client.put("/api/settings/keys/custom/nope", json={"value": "x"}).status_code == 404
    assert client.put("/api/settings/keys/weird/openrouter", json={"value": "x"}).status_code == 404
    assert client.put("/api/settings/base-urls/nope", json={"value": "https://x"}).status_code == 404


def test_empty_key_rejected(client):
    response = client.put("/api/settings/keys/builtin/openrouter", json={"value": "   "})

    assert response.status_code == 400


def test_base_url_roundtrip(client):
    response = client.put("/api/settings/base-urls/deepseek_openai", json={"value": "https://mirror.example/v1/"})

    assert response.status_code == 200
    row = next(b for b in response.json()["base_urls"] if b["id"] == "deepseek_openai")
    assert row["value"] == "https://mirror.example/v1"
    assert row["source"] == "json"

    response = client.delete("/api/settings/base-urls/deepseek_openai")

    row = next(b for b in response.json()["base_urls"] if b["id"] == "deepseek_openai")
    assert row["source"] == "default"
    assert row["value"] == row["default"]


def test_base_url_validation(client):
    response = client.put("/api/settings/base-urls/google", json={"value": "not-a-url"})

    assert response.status_code == 400
