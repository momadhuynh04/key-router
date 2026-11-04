import pytest
from fastapi.testclient import TestClient

from config.custom_providers import (
    delete_provider,
    get_api_key_for_provider,
    get_masked_providers,
    invalidate_custom_providers_cache,
    load_custom_providers,
    save_provider,
    validate_spec,
)
from config.model_map import model_mapper
from config.secrets import get_custom_key, set_custom_key
from proxy.app import create_app
from proxy.router import provider_router


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    secrets_path = tmp_path / "secrets.json"
    monkeypatch.setattr("config.custom_providers._get_config_path", lambda: str(path))
    monkeypatch.setattr("config.secrets._get_secrets_path", lambda: str(secrets_path))
    invalidate_custom_providers_cache()
    yield path
    invalidate_custom_providers_cache()


@pytest.fixture
def client(config_path):
    return TestClient(create_app())


def _spec(**overrides):
    spec = {
        "id": "myprov",
        "display_name": "My Provider",
        "provider_api": "openai_compatible",
        "base_url": "https://api.my.ai/v1",
        "headers": {},
        "models": [{"id": "m1", "name": "M1", "reasoning": False, "image": False}],
    }
    spec.update(overrides)
    return spec


def _payload(**overrides):
    payload = {
        "id": "myprov",
        "display_name": "My Provider",
        "provider_api": "openai_compatible",
        "base_url": "https://api.my.ai/v1",
        "api_key": "sk-live-test-1234567890",
        "headers": {},
        "models": [{"id": "m1", "name": "M1", "reasoning": False, "image": False}],
    }
    payload.update(overrides)
    return payload


def test_validate_spec_accepts_valid():
    validate_spec(_spec())


def test_validate_spec_rejects_duplicate_models():
    with pytest.raises(ValueError, match="Duplicate model id"):
        validate_spec(_spec(models=[
            {"id": "m1", "name": "M1"},
            {"id": "m1", "name": "M1 again"},
        ]))


def test_validate_spec_rejects_bad_base_url():
    with pytest.raises(ValueError, match="http"):
        validate_spec(_spec(base_url="api.my.ai/v1"))


def test_save_and_delete_provider(config_path):
    save_provider(_spec())
    assert "myprov" in load_custom_providers()
    assert delete_provider("myprov") is True
    assert delete_provider("myprov") is False


def test_custom_key_roundtrip(config_path):
    set_custom_key("myprov", "sk-stored-1234567890")

    assert get_custom_key("myprov") == "sk-stored-1234567890"
    assert get_api_key_for_provider(_spec()) == "sk-stored-1234567890"


def test_router_uses_custom_key(config_path):
    save_provider(_spec())
    set_custom_key("myprov", "sk-router-123456")

    provider = provider_router._custom_provider("myprov", "m1")

    assert provider is not None
    assert provider.api_key == "sk-router-123456"


def test_masked_provider_hides_api_key(config_path):
    save_provider(_spec())
    set_custom_key("myprov", "sk-stored-1234567890")

    masked = get_masked_providers()["myprov"]

    assert masked["has_key"] is True
    assert masked["key_source"] == "json"
    assert "sk-stored-1234567890" not in str(masked)
    assert "api_key_env" not in masked


def test_list_custom_providers(client):
    assert client.get("/api/custom-providers").json() == {"providers": {}}

    client.post("/api/custom-providers", json=_payload())

    providers = client.get("/api/custom-providers").json()["providers"]
    assert providers["myprov"]["display_name"] == "My Provider"
    assert providers["myprov"]["provider_api"] == "openai_compatible"
    assert providers["myprov"]["has_key"] is True


def test_create_stores_raw_key(client):
    response = client.post("/api/custom-providers", json=_payload())

    assert response.status_code == 200
    assert get_custom_key("myprov") == "sk-live-test-1234567890"


def test_create_requires_key_or_headers(client):
    response = client.post("/api/custom-providers", json=_payload(api_key=""))

    assert response.status_code == 400
    assert "API key" in response.json()["detail"]


def test_update_without_key_keeps_existing(client):
    client.post("/api/custom-providers", json=_payload())

    response = client.post("/api/custom-providers", json=_payload(api_key="", display_name="Renamed"))

    assert response.status_code == 200
    assert response.json()["providers"]["myprov"]["display_name"] == "Renamed"
    assert get_custom_key("myprov") == "sk-live-test-1234567890"
    assert len(load_custom_providers()) == 1


def test_update_with_new_key_replaces_it(client):
    client.post("/api/custom-providers", json=_payload())

    client.post("/api/custom-providers", json=_payload(api_key="sk-new-key-0987654321"))

    assert get_custom_key("myprov") == "sk-new-key-0987654321"


def test_delete_blocked_while_routes_exist(client, monkeypatch):
    client.post("/api/custom-providers", json=_payload())
    monkeypatch.setattr(model_mapper, "mappings", {"claude": "myprov/m1"})

    response = client.delete("/api/custom-providers/myprov")

    assert response.status_code == 409
    assert "myprov/m1" in response.json()["detail"]
    assert "myprov" in load_custom_providers()


def test_delete_succeeds_without_routes(client, monkeypatch):
    client.post("/api/custom-providers", json=_payload())
    monkeypatch.setattr(model_mapper, "mappings", {"claude": "openrouter/some-model"})

    response = client.delete("/api/custom-providers/myprov")

    assert response.status_code == 200
    assert "myprov" not in response.json()["providers"]
    assert "myprov" not in load_custom_providers()
    assert get_custom_key("myprov") == ""


def test_delete_missing_provider(client):
    response = client.delete("/api/custom-providers/nope")

    assert response.status_code == 404
