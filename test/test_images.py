import base64
import json
import time

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from config.model_map import ModelMapper
from config.image_models import get_image_model_config
from config.settings import settings
from provider.image_gen import (
    ImageError,
    _openrouter_generate,
    _replicate_generate,
    augment_prompt,
    detect_media_type,
    image_dimensions,
    nearest_supported_ratio,
    ratio_to_size,
    select_references,
)
from proxy.routers.images import tiny_png_data_url
from proxy.server import app

client = TestClient(app)

PNG = base64.b64decode(tiny_png_data_url().split(",", 1)[1])


# ---------------------------------------------------------------------------
# 1. Routing + normalization helpers
# ---------------------------------------------------------------------------

def test_adcraft_image_exact_match(tmp_path):
    mapper = ModelMapper(config_path=str(tmp_path / "config.json"))
    mapper.set_mapping("adcraft-image", "openrouter/google/gemini-2.5-flash-image")
    assert mapper.resolve("adcraft-image") == ("openrouter", "google/gemini-2.5-flash-image")


def test_adcraft_image_no_family_fallback(tmp_path):
    mapper = ModelMapper(config_path=str(tmp_path / "config.json"))
    mapper.set_mapping("codex", "openrouter/some-codex-model")
    with pytest.raises(ValueError, match="No mapping found"):
        mapper.resolve("adcraft-image")


def test_nearest_supported_ratio():
    assert nearest_supported_ratio("16:9", ["1:1", "16:9"]) == "16:9"
    assert nearest_supported_ratio("9:16", ["1:1", "16:9"]) == "1:1"
    assert nearest_supported_ratio("4:3", ["1:1", "16:9"]) == "1:1"
    assert nearest_supported_ratio(None, ["1:1"]) is None


def test_ratio_to_size():
    assert ratio_to_size("16:9") == (1344, 768)
    assert ratio_to_size("garbage") == (1024, 1024)


def test_select_references_priority_and_truncation():
    refs = [
        {"role": "scene_reference", "value": "scene"},
        {"role": "product_reference", "value": "product"},
        {"role": "character_reference", "value": "character"},
    ]
    selected, dropped = select_references(refs, 2)
    assert [r["value"] for r in selected] == ["character", "product"]
    assert [r["value"] for r in dropped] == ["scene"]


def test_select_references_unsupported_raises_422():
    with pytest.raises(ImageError) as exc:
        select_references([{"role": "character_reference", "value": "x"}], 0)
    assert exc.value.status == 422
    assert exc.value.code == "references_unsupported"


def test_augment_prompt_includes_roles():
    prompt = augment_prompt("make an ad", [{"role": "character_reference", "value": "x"}])
    assert "Reference 1 is the character reference." in prompt


# ---------------------------------------------------------------------------
# 2. Image validation helpers
# ---------------------------------------------------------------------------

def test_detect_media_and_dimensions_png():
    assert detect_media_type(PNG) == "image/png"
    assert image_dimensions(PNG, "image/png") == (1, 1)


def test_detect_media_rejects_unknown():
    assert detect_media_type(b"not an image") is None


# ---------------------------------------------------------------------------
# 3. OpenRouter backend
# ---------------------------------------------------------------------------

def _openrouter_cfg():
    return {
        "backend": "openrouter",
        "upstream_model": "google/gemini-2.5-flash-image",
        "max_references": 3,
        "aspect_ratio_mode": "ratio",
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "output_format_field": "output_format",
    }


@pytest.mark.asyncio
@patch.object(settings, "openrouter_api_key", "test-key")
@patch("httpx.AsyncClient.post")
async def test_openrouter_generate_normalizes_references(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "created": 123,
        "data": [{"b64_json": base64.b64encode(PNG).decode(), "media_type": "image/png", "extra": "ignored"}],
    }
    mock_post.return_value = mock_resp

    refs = ["https://example.com/a.png", "data:image/png;base64,AAAA"]
    raw = await _openrouter_generate(
        _openrouter_cfg(),
        "prompt\n\nReference 1 is the character reference.",
        "16:9",
        "png",
        refs,
        time.monotonic() + 30,
    )
    assert raw == PNG

    body = mock_post.call_args.kwargs["json"]
    assert body["model"] == "google/gemini-2.5-flash-image"
    assert body["prompt"].startswith("prompt")
    assert body["n"] == 1
    assert body["aspect_ratio"] == "16:9"
    assert body["output_format"] == "png"
    assert body["input_references"] == [
        {"type": "image_url", "image_url": {"url": refs[0]}},
        {"type": "image_url", "image_url": {"url": refs[1]}},
    ]
    # Regression: OpenRouter rejects bare strings ("expected object, received string").
    assert all(isinstance(item, dict) for item in body["input_references"])


@pytest.mark.asyncio
@patch.object(settings, "openrouter_api_key", "test-key")
@patch("httpx.AsyncClient.post")
async def test_openrouter_generate_maps_429_with_retry_after(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.json.return_value = {"error": {"message": "rate limited"}}
    mock_resp.headers = {"retry-after": "7"}
    mock_post.return_value = mock_resp

    with pytest.raises(ImageError) as exc:
        await _openrouter_generate(_openrouter_cfg(), "p", None, None, None, time.monotonic() + 30)
    assert exc.value.status == 429
    assert exc.value.code == "rate_limited"
    assert exc.value.headers.get("Retry-After") == "7"


# ---------------------------------------------------------------------------
# 4. Replicate backend
# ---------------------------------------------------------------------------

def _replicate_cfg():
    return {
        "backend": "replicate",
        "upstream_model": "black-forest-labs/flux-kontext-pro",
        "max_references": 1,
        "reference_field": "input_image",
        "aspect_ratio_mode": "ratio",
        "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "output_format_field": "output_format",
    }


@pytest.mark.asyncio
@patch.object(settings, "replicate_api_token", "test-token")
@patch("httpx.AsyncClient.get")
@patch("httpx.AsyncClient.post")
async def test_replicate_generate_builds_input_and_downloads(mock_post, mock_get):
    prediction = MagicMock()
    prediction.status_code = 201
    prediction.json.return_value = {
        "id": "p1",
        "status": "succeeded",
        "output": "https://replicate.delivery/out.png",
        "urls": {"get": "https://api.replicate.com/v1/predictions/p1"},
    }
    mock_post.return_value = prediction

    download = MagicMock()
    download.status_code = 200
    download.content = PNG
    mock_get.return_value = download

    raw = await _replicate_generate(
        _replicate_cfg(), "prompt", "16:9", "png", ["https://ref/image.png"], time.monotonic() + 30
    )
    assert raw == PNG

    payload = mock_post.call_args.kwargs["json"]["input"]
    assert payload["input_image"] == "https://ref/image.png"
    assert payload["aspect_ratio"] == "16:9"
    assert payload["output_format"] == "png"
    assert mock_post.call_args.kwargs["headers"]["Prefer"] == "wait=60"


@pytest.mark.asyncio
@patch.object(settings, "replicate_api_token", "test-token")
@patch("httpx.AsyncClient.get")
@patch("httpx.AsyncClient.post")
async def test_replicate_polls_until_succeeded(mock_post, mock_get):
    initial = MagicMock()
    initial.status_code = 201
    initial.json.return_value = {"id": "p1", "status": "starting", "urls": {"get": "https://api.replicate.com/v1/predictions/p1"}}
    mock_post.return_value = initial

    poll = MagicMock()
    poll.status_code = 200
    poll.json.return_value = {"id": "p1", "status": "succeeded", "output": ["https://replicate.delivery/out.png"]}

    download = MagicMock()
    download.status_code = 200
    download.content = PNG
    mock_get.side_effect = [poll, download]

    raw = await _replicate_generate(_replicate_cfg(), "prompt", None, None, None, time.monotonic() + 30)
    assert raw == PNG


# ---------------------------------------------------------------------------
# 5. HTTP endpoint contract
# ---------------------------------------------------------------------------

@pytest.fixture
def image_mapper(tmp_path):
    mapper = ModelMapper(config_path=str(tmp_path / "config.json"))
    mapper.set_mapping("adcraft-image", "openrouter/google/gemini-2.5-flash-image")
    return mapper


def _fake_result():
    return {
        "b64_json": base64.b64encode(PNG).decode(),
        "media_type": "image/png",
        "bytes": len(PNG),
        "width": 1,
        "height": 1,
    }


def _cfg():
    return {
        "backend": "openrouter",
        "upstream_model": "google/gemini-2.5-flash-image",
        "max_references": 3,
        "supported_aspect_ratios": ["1:1", "16:9"],
        "output_format_field": "output_format",
    }


@patch("proxy.routers.images.generate_image", new_callable=AsyncMock)
@patch("proxy.routers.images.resolve_image_config", new_callable=AsyncMock)
def test_endpoint_returns_strict_two_key_shape(mock_resolve, mock_generate, image_mapper):
    mock_resolve.return_value = _cfg()
    mock_generate.return_value = _fake_result()

    with patch("proxy.routers.images.model_mapper", image_mapper):
        resp = client.post("/v1/images/generations", json={
            "model": "adcraft-image",
            "prompt": "A red apple",
            "n": 1,
            "aspect_ratio": "16:9",
            "output_format": "png",
            "quality": "hd",
            "size": "1024x1024",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["created"], int)
    assert len(data["data"]) == 1
    item = data["data"][0]
    assert set(item) == {"b64_json", "media_type"}
    assert item["media_type"] == "image/png"
    base64.b64decode(item["b64_json"])


@patch("proxy.routers.images.generate_image", new_callable=AsyncMock)
@patch("proxy.routers.images.resolve_image_config", new_callable=AsyncMock)
def test_alias_v1_images(mock_resolve, mock_generate, image_mapper):
    mock_resolve.return_value = _cfg()
    mock_generate.return_value = _fake_result()

    with patch("proxy.routers.images.model_mapper", image_mapper):
        resp = client.post("/v1/images", json={"model": "adcraft-image", "prompt": "x"})

    assert resp.status_code == 200
    assert set(resp.json()["data"][0]) == {"b64_json", "media_type"}


@patch("proxy.routers.images.resolve_image_config", new_callable=AsyncMock)
def test_references_unsupported_returns_422(mock_resolve, image_mapper):
    cfg = _cfg()
    cfg["max_references"] = 0
    mock_resolve.return_value = cfg

    with patch("proxy.routers.images.model_mapper", image_mapper):
        resp = client.post("/v1/images/generations", json={
            "model": "adcraft-image",
            "prompt": "x",
            "input_references": [{
                "type": "input_image",
                "role": "character_reference",
                "source": {"type": "image_url", "value": "https://example.com/a.png"},
            }],
        })

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "references_unsupported"


@patch("proxy.routers.images.resolve_image_config", new_callable=AsyncMock)
def test_unknown_model_returns_404(mock_resolve, image_mapper):
    mock_resolve.return_value = _cfg()
    with patch("proxy.routers.images.model_mapper", image_mapper):
        resp = client.post("/v1/images/generations", json={"model": "nope", "prompt": "x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_model"


# ---------------------------------------------------------------------------
# 6. Management endpoints
# ---------------------------------------------------------------------------

def _image_mapper_with_target(tmp_path, target="replicate/black-forest-labs/flux-kontext-pro"):
    mapper = ModelMapper(config_path=str(tmp_path / "config.json"))
    mapper.set_mapping("adcraft-image", target)
    return mapper


def test_image_status_endpoint(tmp_path):
    mapper = _image_mapper_with_target(tmp_path)
    with patch("proxy.routers.management.model_mapper", mapper):
        resp = client.get("/api/adcraft-image/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["backend"] == "replicate"
    assert data["upstream_model"] == "black-forest-labs/flux-kontext-pro"
    assert data["endpoint"].endswith("/v1/images/generations")
    assert data["alias_endpoint"].endswith("/v1/images")
    assert data["api_key"] == "key-router"


def test_image_set_model_endpoint(tmp_path):
    mapper = _image_mapper_with_target(tmp_path)
    with patch("proxy.routers.management.model_mapper", mapper):
        resp = client.post("/api/adcraft-image/model", json={"target": "openrouter/google/gemini-2.5-flash-image"})
        assert resp.status_code == 200
        assert resp.json()["backend"] == "openrouter"
        assert mapper.get_all()["adcraft-image"] == "openrouter/google/gemini-2.5-flash-image"

        bad = client.post("/api/adcraft-image/model", json={"target": "not-a-backend/model"})
        assert bad.status_code == 400
        no_slash = client.post("/api/adcraft-image/model", json={"target": "noslash"})
        assert no_slash.status_code == 400


@patch("provider.image_gen.fetch_openrouter_image_models", new_callable=AsyncMock)
def test_image_models_endpoint(mock_fetch):
    mock_fetch.return_value = [
        {
            "id": "google/gemini-2.5-flash-image",
            "name": "Gemini 2.5 Flash Image",
            "supported_parameters": {
                "input_references": {"max": 3},
                "aspect_ratio": {"values": ["1:1", "16:9"]},
            },
        }
    ]
    resp = client.get("/api/adcraft-image/models")
    assert resp.status_code == 200
    data = resp.json()
    option = data["openrouter"][0]
    assert option["id"] == "google/gemini-2.5-flash-image"
    assert option["target"] == "openrouter/google/gemini-2.5-flash-image"
    assert option["max_references"] == 3
    assert option["aspect_ratios"] == ["1:1", "16:9"]
    # Replicate entries come from config.json
    assert any(item["id"] == "black-forest-labs/flux-kontext-pro" for item in data["replicate"])


@patch("proxy.routers.images.run_generation", new_callable=AsyncMock)
def test_image_test_endpoint(mock_run, tmp_path):
    mapper = _image_mapper_with_target(tmp_path)
    mock_run.return_value = {
        **_fake_result(),
        "backend": "replicate",
        "upstream_model": "black-forest-labs/flux-kontext-pro",
        "elapsed_ms": 1234,
        "references_used": 1,
    }
    with patch("proxy.routers.management.model_mapper", mapper):
        resp = client.post("/api/adcraft-image/test", json={"with_reference": True})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["b64_json"]
    assert data["elapsed_ms"] == 1234
    assert data["references_used"] == 1
    assert data["with_reference"] is True


def test_image_test_endpoint_unconfigured(tmp_path):
    mapper = ModelMapper(config_path=str(tmp_path / "config.json"))
    with patch("proxy.routers.management.model_mapper", mapper):
        resp = client.post("/api/adcraft-image/test", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
    assert resp.json()["error"]["code"] == "not_configured"
