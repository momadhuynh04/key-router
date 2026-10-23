import asyncio
import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.parse
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.logging import log_warn
from config.model_map import model_mapper
from config.codex_config import (
    setup_codex_config,
    persist_codex_env_var,
    get_codex_config_path,
    DEFAULT_BASE_URL as CODEX_BASE_URL,
    API_KEY_ENV,
    API_KEY_VALUE,
)
import proxy.server_helpers as _helpers
from proxy.server_helpers import (
    IDE_DEFINITIONS,
    PROXY_URL,
    _save_ide_detection,
    _get_ide_settings_path,
    _safe_merge_json,
    _proxy_env,
    _setup_claude_env,
    _setup_ide_settings,
    _find_linux_terminal,
    _launch_terminal,
    _resolve_launch,
)

router = APIRouter()
cached_available_models: Dict = {}


class ModelMappingRequest(BaseModel):
    source_model: str
    target: str

class LaunchRequest(BaseModel):
    path: Optional[str] = None
    repo_url: Optional[str] = None

class IDESetupRequest(BaseModel):
    editors: list[str] = []

class IDELaunchRequest(BaseModel):
    editor: str
    path: Optional[str] = None

class CodexSetupRequest(BaseModel):
    base_url: Optional[str] = None

class CustomModelSpec(BaseModel):
    id: str
    name: str
    reasoning: bool = False
    image: bool = False

class CustomProviderSpec(BaseModel):
    id: str
    display_name: str
    provider_api: str
    base_url: str
    api_key: str
    headers: Optional[Dict[str, str]] = None
    models: list[CustomModelSpec]


@router.get("/api/health")
async def health_check():
    return {"status": "ok"}


@router.get("/api/ide-detect")
async def ide_detect():
    import proxy.server as _srv
    fn = getattr(_srv, "_detect_ides", _helpers._detect_ides)
    detected = await asyncio.to_thread(fn)
    if detected:
        _save_ide_detection(detected)
    return {"detected": detected}


@router.get("/api/ide-detect-refresh")
async def ide_detect_refresh():
    import proxy.server as _srv
    fn = getattr(_srv, "_detect_ides", _helpers._detect_ides)
    detected = await asyncio.to_thread(fn)
    _save_ide_detection(detected)
    return {"detected": detected}


@router.post("/api/ide-setup")
async def ide_setup(request: IDESetupRequest):
    results = []
    claude_changed = _setup_claude_env()
    results.append({"target": "claude_settings", "configured": claude_changed})
    codex_changed = setup_codex_config()
    persist_codex_env_var()
    results.append({"target": "codex_config", "configured": codex_changed})
    import proxy.server as _srv2
    _det = getattr(_srv2, "_detect_ides", _helpers._detect_ides)
    detected = await asyncio.to_thread(_det)
    for ide_def in IDE_DEFINITIONS:
        if ide_def["id"] in request.editors and ide_def["id"] in detected:
            if ide_def["supports_claude_extension"]:
                changed = _setup_ide_settings(ide_def["config_dir"])
                results.append({"target": ide_def["id"], "configured": changed})
            else:
                results.append({"target": ide_def["id"], "configured": False, "note": "IDE does not support Claude Code extension — use terminal inside this IDE with `claude` CLI instead"})
    return {"status": "success", "results": results}


@router.post("/api/ide-launch")
async def ide_launch(request: IDELaunchRequest):
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    target_cwd = base_dir
    if request.path and os.path.isdir(request.path):
        target_cwd = request.path
    import proxy.server as _srv3
    _det3 = getattr(_srv3, "_detect_ides", _helpers._detect_ides)
    _launch_fn = getattr(_srv3, "_launch_ide", _helpers._launch_ide)
    detected = await asyncio.to_thread(_det3)
    info = detected.get(request.editor)
    if not info:
        raise HTTPException(status_code=404, detail=f"IDE '{request.editor}' not found. Run /api/ide-detect-refresh first.")
    try:
        _launch_fn(info["binary"], target_cwd)
        return {"status": "success", "message": f"Launched {info['name']}!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/models")
async def get_models():
    return {"mappings": model_mapper.get_all()}


@router.get("/api/available-models")
async def get_available_models():
    global cached_available_models
    if cached_available_models:
        return cached_available_models
    models_data = {
        "openrouter": [],
        "deepseekplatform": ["deepseek-chat", "deepseek-reasoner", "deepseek-coder"]
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://openrouter.ai/api/v1/models", timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                models_data["openrouter"] = [m["id"] for m in data.get("data", [])]
    except Exception as e:
        log_warn(f"Error fetching OpenRouter models: {e}")
        models_data["openrouter"] = ["qwen/qwen-turbo", "meta-llama/llama-3-8b-instruct"]
    try:
        from config.settings import settings
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.deepseek.com/models", headers={"Authorization": f"Bearer {settings.deepseek_api_key}"}, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                models_data["deepseekplatform"] = [m["id"] for m in data.get("data", [])]
            else:
                models_data["deepseekplatform"] = ["deepseek-chat", "deepseek-reasoner"]
    except Exception as e:
        log_warn(f"Error fetching DeepSeek models: {e}")
        models_data["deepseekplatform"] = ["deepseek-chat", "deepseek-reasoner", "deepseek-coder"]
    try:
        from config.settings import settings as _gs
        if _gs.google_api_key:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{_gs.google_base_url.rstrip('/')}/models?key={_gs.google_api_key}", timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json()
                    ids = []
                    for m in data.get("models", []):
                        methods = m.get("supportedGenerationMethods") or []
                        if "generateContent" in methods:
                            mid = m.get("name", "")
                            if mid.startswith("models/"):
                                mid = mid[len("models/"):]
                            if mid:
                                ids.append(mid)
                    if ids:
                        models_data["googleaistudio"] = ids
        if "googleaistudio" not in models_data:
            models_data["googleaistudio"] = ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.5-flash"]
    except Exception as e:
        log_warn(f"Error fetching Google models: {e}")
        models_data.setdefault("googleaistudio", ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.5-flash"])
    try:
        from config.custom_providers import load_custom_providers as _load_cp
        for _pid, _spec in _load_cp().items():
            models_data[_pid] = [m.get("id") for m in (_spec.get("models") or []) if isinstance(m, dict) and m.get("id")]
    except Exception:
        pass
    cached_available_models = models_data
    return models_data


@router.get("/api/custom-providers")
async def list_custom_providers():
    from config.custom_providers import get_masked_providers
    return {"providers": get_masked_providers()}


@router.post("/api/custom-providers")
async def create_custom_provider(request: CustomProviderSpec):
    from config.custom_providers import load_custom_providers, save_custom_providers, normalize_api_key_input
    raw_env = normalize_api_key_input(request.api_key)
    if not raw_env.strip():
        headers = request.headers or {}
        has_headers = any(k.strip() and v.strip() for k, v in headers.items()) if isinstance(headers, dict) else False
        if not has_headers:
            raise HTTPException(status_code=400, detail="API key ENV var is required (or provide auth via headers)")
    else:
        try:
            from config.custom_providers import validate_spec as _vs
            _vs({"id": request.id, "display_name": request.display_name, "provider_api": request.provider_api, "base_url": request.base_url, "api_key_env": raw_env, "headers": request.headers, "models": [m.model_dump() for m in request.models]})
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
    spec = {
        "id": request.id.strip().lower(),
        "display_name": request.display_name.strip(),
        "provider_api": request.provider_api,
        "base_url": request.base_url.strip().rstrip("/"),
        "api_key_env": raw_env,
        "headers": request.headers or {},
        "models": [m.model_dump() for m in request.models],
    }
    providers = dict(load_custom_providers())
    providers[spec["id"]] = spec
    save_custom_providers(providers)
    global cached_available_models
    cached_available_models = {}
    from config.custom_providers import get_masked_providers as _masked
    masked = _masked()
    return {"status": "success", "providers": masked}


@router.delete("/api/custom-providers/{provider_id}")
async def delete_custom_provider(provider_id: str):
    from config.custom_providers import delete_provider, load_custom_providers, get_masked_providers
    providers = load_custom_providers()
    if provider_id not in providers:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    orphaned = [k for k, v in model_mapper.get_all().items() if v.startswith(f"{provider_id}/")]
    delete_provider(provider_id)
    global cached_available_models
    cached_available_models = {}
    return {"status": "success", "providers": get_masked_providers(), "orphaned_mappings": orphaned}


@router.get("/api/custom-providers/{provider_id}/models")
async def fetch_custom_provider_models(provider_id: str):
    import os as _os
    from config.custom_providers import load_custom_providers
    spec = load_custom_providers().get(provider_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    api_key = _os.environ.get(spec.get("api_key_env", ""), "") if spec.get("api_key_env") else ""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if spec.get("headers"):
        headers.update(spec["headers"])
    base = spec.get("base_url", "").rstrip("/")
    candidates = [f"{base}/models", f"{base}/v1/models"] if not base.endswith("/models") else [base]
    for url in candidates:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data.get("data"), list):
                        ids = [m.get("id") for m in data["data"] if isinstance(m, dict) and m.get("id")]
                        return {"ids": ids}
                    if isinstance(data, list):
                        return {"ids": [str(x) for x in data]}
        except Exception:
            continue
    return {"ids": []}


@router.post("/api/models")
async def update_model(request: ModelMappingRequest):
    model_mapper.set_mapping(request.source_model, request.target)
    return {"status": "success", "mappings": model_mapper.get_all()}


# ---------------------------------------------------------------------------
# AdCraft integration — AdCraft only ever sends model="adcraft" to
# /v1/chat/completions; the real target model is chosen here in the WebUI.
# ---------------------------------------------------------------------------

AD_CRAFT_ALIAS = "adcraft"


class AdCraftModelRequest(BaseModel):
    target: str


def _adcraft_status_payload() -> Dict:
    from config.settings import settings
    mapping = model_mapper.get_all().get(AD_CRAFT_ALIAS)
    provider = None
    model = None
    if mapping and "/" in mapping:
        provider, model = mapping.split("/", 1)
    base = f"http://{settings.host}:{settings.port}"
    return {
        "alias": AD_CRAFT_ALIAS,
        "mapping": mapping,
        "provider": provider,
        "model": model,
        "configured": bool(mapping),
        "endpoint": f"{base}/v1",
        "chat_completions_url": f"{base}/v1/chat/completions",
        # The proxy performs no auth — any API key is accepted. Expose the same
        # placeholder key-router uses for Codex so clients have a value to paste.
        "api_key": API_KEY_VALUE,
        "api_key_env": API_KEY_ENV,
    }


@router.get("/api/adcraft/status")
async def adcraft_status():
    return _adcraft_status_payload()


@router.post("/api/adcraft/model")
async def adcraft_set_model(request: AdCraftModelRequest):
    target = (request.target or "").strip()
    if "/" not in target:
        raise HTTPException(status_code=400, detail="target must be 'provider/model_name'")
    model_mapper.set_mapping(AD_CRAFT_ALIAS, target)
    return {"status": "success", **_adcraft_status_payload()}


@router.post("/api/adcraft/test")
async def adcraft_test():
    """Send a simulated AdCraft request (OpenAI format + submit_structured_result tool)."""
    from models.openai_compat import OpenAIRequest as _OpenAIRequest
    from proxy.openai_ingress import openai_chat_to_anthropic, anthropic_response_to_openai_chat
    from proxy.router import provider_router

    status = _adcraft_status_payload()
    if not status["configured"]:
        return {"status": "error", "error": "No 'adcraft' model mapping configured.", **status}

    payload = {
        "model": AD_CRAFT_ALIAS,
        "stream": False,
        "messages": [
            {"role": "system", "content": "You are a video production agent."},
            {"role": "user", "content": "Submit a structured JSON result for a 5-second test ad."},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "submit_structured_result",
                "description": "Submit the structured result",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "duration_seconds": {"type": "integer"},
                    },
                    "required": ["title"],
                },
            },
        }],
        "tool_choice": "auto",
        "response_format": {"type": "json_object"},
        "max_tokens": 512,
    }
    try:
        req = _OpenAIRequest(**payload)
        anthropic_request = openai_chat_to_anthropic(req)
        provider = provider_router.get_provider_for_openai(anthropic_request.model)
        body = await provider.translate_request(anthropic_request)
        response = await provider.generate(body)
        out = anthropic_response_to_openai_chat(response, AD_CRAFT_ALIAS)
        message = out["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        return {
            "status": "success",
            "target_model": getattr(provider, "target_model", None),
            "response_format_sent": body.get("response_format"),
            "tool_called": any(
                tc.get("function", {}).get("name") == "submit_structured_result" for tc in tool_calls
            ),
            "finish_reason": out["choices"][0]["finish_reason"],
            "content": message.get("content"),
            "tool_calls": tool_calls,
            "usage": out.get("usage"),
            **status,
        }
    except Exception as e:
        log_warn(f"AdCraft test failed: {e}")
        return {"status": "error", "error": str(e)[:2000], **status}


# ---------------------------------------------------------------------------
# AdCraft Image integration
# ---------------------------------------------------------------------------

AD_CRAFT_IMAGE_ALIAS = "adcraft-image"


class AdCraftImageModelRequest(BaseModel):
    target: str


class AdCraftImageTestRequest(BaseModel):
    with_reference: bool = False
    reference_url: Optional[str] = None


def _adcraft_image_status_payload() -> Dict:
    from config.settings import settings
    from config.image_models import get_image_model_config

    mapping = model_mapper.get_all().get(AD_CRAFT_IMAGE_ALIAS)
    backend = None
    upstream = None
    cfg: Dict = {}
    if mapping and "/" in mapping:
        backend, upstream = mapping.split("/", 1)
        try:
            cfg = get_image_model_config(mapping)
        except ValueError:
            cfg = {}
    base = f"http://{settings.host}:{settings.port}"
    return {
        "alias": AD_CRAFT_IMAGE_ALIAS,
        "mapping": mapping,
        "backend": backend,
        "upstream_model": upstream,
        "configured": bool(mapping),
        "config": cfg,
        "endpoint": f"{base}/v1/images/generations",
        "alias_endpoint": f"{base}/v1/images",
        "api_key": API_KEY_VALUE,
        "api_key_env": API_KEY_ENV,
        "openrouter_configured": bool(settings.openrouter_api_key),
        "replicate_configured": bool(settings.replicate_api_token),
    }


@router.get("/api/adcraft-image/status")
async def adcraft_image_status():
    return _adcraft_image_status_payload()


@router.post("/api/adcraft-image/model")
async def adcraft_image_set_model(request: AdCraftImageModelRequest):
    from config.image_models import get_image_model_config

    target = (request.target or "").strip()
    if "/" not in target:
        raise HTTPException(status_code=400, detail="target must be 'backend/upstream_model'")
    try:
        cfg = get_image_model_config(target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if cfg["backend"] not in ("openrouter", "replicate"):
        raise HTTPException(status_code=400, detail=f"Unsupported backend '{cfg['backend']}'")
    model_mapper.set_mapping(AD_CRAFT_IMAGE_ALIAS, target)
    return {"status": "success", **_adcraft_image_status_payload()}


@router.get("/api/adcraft-image/models")
async def adcraft_image_models(force: bool = False):
    from config.image_models import load_image_models
    from provider.image_gen import caps_from_model, fetch_openrouter_image_models

    openrouter_models = await fetch_openrouter_image_models(force=force)
    openrouter_options = []
    for model in openrouter_models:
        caps = caps_from_model(model)
        if not caps.get("id"):
            continue
        openrouter_options.append({
            "id": caps["id"],
            "name": caps.get("name") or caps["id"],
            "target": f"openrouter/{caps['id']}",
            "max_references": caps.get("max_references", 0),
            "aspect_ratios": caps.get("aspect_ratios", []),
            "supports_output_format": caps.get("supports_output_format", False),
        })
    openrouter_options.sort(key=lambda item: item["name"].lower())

    replicate_options = []
    for target, spec in load_image_models().items():
        if not isinstance(spec, dict) or not target.startswith("replicate/"):
            continue
        upstream = spec.get("upstream_model") or target.split("/", 1)[1]
        replicate_options.append({
            "id": upstream,
            "name": upstream,
            "target": target,
            "max_references": spec.get("max_references", 0),
            "aspect_ratios": spec.get("supported_aspect_ratios", []),
            "supports_output_format": bool(spec.get("output_format_field")),
        })
    replicate_options.sort(key=lambda item: item["id"].lower())

    return {"openrouter": openrouter_options, "replicate": replicate_options}


@router.post("/api/adcraft-image/test")
async def adcraft_image_test(request: AdCraftImageTestRequest | None = None):
    from proxy.routers.images import (
        ImageGenerationRequest,
        ImageReference,
        ImageReferenceSource,
        run_generation,
        tiny_png_data_url,
    )
    from provider.image_gen import ImageError as _ImageError

    body = request or AdCraftImageTestRequest()
    status = _adcraft_image_status_payload()
    status_public = {k: v for k, v in status.items() if k != "config"}
    if not status["configured"]:
        return {
            "status": "error",
            "error": {"code": "not_configured", "message": "No 'adcraft-image' mapping configured."},
            "with_reference": body.with_reference,
            **status_public,
        }

    refs = None
    if body.with_reference:
        refs = [
            ImageReference(
                type="input_image",
                role="character_reference",
                source=ImageReferenceSource(
                    type="image_url" if body.reference_url else "data_url",
                    value=body.reference_url or tiny_png_data_url(),
                ),
            )
        ]

    payload = ImageGenerationRequest(
        model=AD_CRAFT_IMAGE_ALIAS,
        prompt="A simple red apple on a white background, product photo.",
        n=1,
        aspect_ratio="1:1",
        output_format="png",
        input_references=refs,
    )
    try:
        result = await run_generation(payload, deadline_seconds=90)
        return {
            "status": "success",
            "backend": result["backend"],
            "upstream_model": result["upstream_model"],
            "elapsed_ms": result["elapsed_ms"],
            "bytes": result["bytes"],
            "width": result["width"],
            "height": result["height"],
            "media_type": result["media_type"],
            "references_used": result["references_used"],
            "with_reference": body.with_reference,
            "b64_json": result["b64_json"],
            **status_public,
        }
    except _ImageError as exc:
        return {
            "status": "error",
            "error": {"code": exc.code, "message": exc.message},
            "http_status": exc.status,
            "with_reference": body.with_reference,
            **status_public,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": {"code": "internal_error", "message": str(exc)[:500]},
            "with_reference": body.with_reference,
            **status_public,
        }


# ---------------------------------------------------------------------------
# AdCraft Video integration
# ---------------------------------------------------------------------------

AD_CRAFT_VIDEO_ALIAS = "adcraft-video"


class AdCraftVideoModelRequest(BaseModel):
    target: str


class AdCraftVideoTestRequest(BaseModel):
    prompt: Optional[str] = None
    wait_seconds: float = 60.0
    with_reference: bool = False
    reference_url: Optional[str] = None


def _adcraft_video_status_payload() -> Dict:
    from config.settings import settings
    from video.config import get_video_model_config

    try:
        cfg = get_video_model_config(AD_CRAFT_VIDEO_ALIAS)
    except ValueError:
        cfg = None
    base = f"http://{settings.host}:{settings.port}"
    return {
        "alias": AD_CRAFT_VIDEO_ALIAS,
        "configured": cfg is not None,
        "config": cfg,
        "driver": cfg.get("driver") if cfg else None,
        "family": cfg.get("family") if cfg else None,
        "quality_tier": cfg.get("quality_tier") if cfg else None,
        "endpoint": f"{base}/v1/videos",
        "content_endpoint": f"{base}/v1/videos/{{id}}/content",
        "api_key": API_KEY_VALUE,
        "api_key_env": API_KEY_ENV,
        "auth_required": bool(settings.keyrouter_api_key),
        "gateway_configured": bool(settings.modal_video_gateway_url),
        "gateway_url": settings.modal_video_gateway_url or None,
    }


@router.get("/api/adcraft-video/status")
async def adcraft_video_status():
    return _adcraft_video_status_payload()


@router.get("/api/adcraft-video/models")
async def adcraft_video_models():
    from video.config import get_video_model_config, load_video_models

    options = []
    try:
        current_cfg = get_video_model_config(AD_CRAFT_VIDEO_ALIAS)
    except ValueError:
        current_cfg = None

    def _is_current(cfg: Dict) -> bool:
        if not current_cfg:
            return False
        keys = ("driver", "family", "quality_tier")
        return tuple(cfg.get(k) for k in keys) == tuple(current_cfg.get(k) for k in keys)

    for alias in sorted(load_video_models().keys()):
        # `adcraft-video` is the destination alias AdCraft calls, not a preset:
        # it is a copy of whichever preset was last saved, so listing it makes the
        # dropdown show duplicate families. Current state is shown in the header.
        if alias == AD_CRAFT_VIDEO_ALIAS:
            continue
        try:
            cfg = get_video_model_config(alias)
        except ValueError:
            continue
        options.append({
            "id": alias,
            "name": alias,
            "target": alias,
            "driver": cfg["driver"],
            "family": cfg.get("family"),
            "quality_tier": cfg.get("quality_tier"),
            "max_references": cfg.get("max_references"),
            "resolutions": cfg.get("resolutions"),
            "aspect_ratios": cfg.get("aspect_ratios"),
            "duration_range": cfg.get("duration_range"),
            "supports_native_audio": cfg.get("supports_native_audio"),
            "current": _is_current(cfg),
        })
    return {"models": options}


@router.post("/api/adcraft-video/model")
async def adcraft_video_set_model(request: AdCraftVideoModelRequest):
    from video.config import get_video_model_config, load_video_models, save_video_alias

    target = (request.target or "").strip()
    if target not in load_video_models():
        raise HTTPException(status_code=400, detail=f"Unknown video profile '{target}'")
    cfg = get_video_model_config(target)
    spec = {
        "driver": cfg["driver"],
        "app": cfg.get("app"),
        "family": cfg.get("family"),
        "quality_tier": cfg.get("quality_tier"),
        "provider": cfg.get("provider"),
        "upstream_model": cfg.get("upstream_model"),
        "max_references": cfg.get("max_references"),
        "duration_range": cfg.get("duration_range"),
        "resolutions": cfg.get("resolutions"),
        "aspect_ratios": cfg.get("aspect_ratios"),
        "supports_native_audio": cfg.get("supports_native_audio"),
        "extra": cfg.get("extra") or {},
    }
    save_video_alias(AD_CRAFT_VIDEO_ALIAS, spec)
    return {"status": "success", **_adcraft_video_status_payload()}


@router.post("/api/adcraft-video/test")
async def adcraft_video_test(request: AdCraftVideoTestRequest | None = None):
    from models.video import FINAL_STATUSES, VideoGenerationRequest
    from proxy.routers.videos import DEFAULT_ALIAS, advance_job, serialize_job, submit_job
    from video.errors import VideoApiError
    from video.jobs import job_store

    body = request or AdCraftVideoTestRequest()
    status_payload = _adcraft_video_status_payload()
    if not status_payload["configured"]:
        return {
            "status": "error",
            "error": {"code": "not_configured", "message": "No 'adcraft-video' profile configured."},
            **status_payload,
        }

    family = str((status_payload.get("config") or {}).get("family") or "")
    needs_image = bool(body.with_reference) or "i2v" in family.lower()
    image_url = None
    if needs_image:
        from proxy.routers.images import reference_test_image_data_url

        image_url = body.reference_url or reference_test_image_data_url()

    default_prompt = (
        "The red circle in the reference image slowly turns into a glowing orb, "
        "cinematic, gentle camera push-in."
        if image_url
        else "A calm ocean wave at sunset, cinematic, gentle camera drift."
    )
    req = VideoGenerationRequest(
        model=DEFAULT_ALIAS,
        prompt=body.prompt or default_prompt,
        duration_seconds=5,
        resolution="480p",
        aspect_ratio="16:9",
        image_url=image_url,
    )
    try:
        job, _ = await submit_job(req)
    except VideoApiError as exc:
        return {
            "status": "error",
            "error": {"code": exc.code, "message": exc.message},
            "http_status": exc.status,
            **status_payload,
        }

    deadline = time.monotonic() + max(0.0, float(body.wait_seconds or 0))
    while time.monotonic() < deadline:
        latest = await asyncio.to_thread(job_store.get, job["id"])
        if latest is None:
            break
        job = await advance_job(latest)
        if job.get("status") in FINAL_STATUSES:
            break
        await asyncio.sleep(2.0)

    return {
        "status": "completed" if job.get("status") == "completed" else "pending",
        "job": serialize_job(job),
        "with_reference": bool(image_url),
        **status_payload,
    }


@router.get("/api/adcraft-video/jobs")
async def adcraft_video_jobs(limit: int = 20):
    from models.video import FINAL_STATUSES
    from proxy.routers.videos import advance_job, serialize_job
    from video.jobs import job_store

    rows = await asyncio.to_thread(job_store.list_recent, int(limit))
    # Advance the newest non-final jobs so the list is not stale (bounded, parallel).
    pending = [r for r in rows if r.get("status") not in FINAL_STATUSES][:5]
    if pending:
        await asyncio.gather(*(advance_job(r) for r in pending), return_exceptions=True)
        rows = await asyncio.to_thread(job_store.list_recent, int(limit))
    return {"jobs": [serialize_job(row) for row in rows]}


@router.get("/api/adcraft-video/jobs/{job_id}")
async def adcraft_video_job(job_id: str):
    from proxy.routers.videos import advance_job, serialize_job
    from video.jobs import job_store

    row = await asyncio.to_thread(job_store.get, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    row = await advance_job(row)
    return serialize_job(row)


@router.get("/api/adcraft-video/jobs/{job_id}/content")
async def adcraft_video_job_content(job_id: str):
    """UI preview endpoint (no client auth), so the browser can play the mp4."""
    from fastapi.responses import FileResponse
    from proxy.routers.videos import advance_job
    from video.jobs import job_store

    row = await asyncio.to_thread(job_store.get, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    row = await advance_job(row)
    if row.get("status") != "completed":
        raise HTTPException(status_code=409, detail=f"Job is '{row.get('status')}', not ready")
    path = job_store.video_path(job_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Video file is no longer available")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")


# ---------------------------------------------------------------------------
# AdCraft SFX (video-to-SFX) integration
# ---------------------------------------------------------------------------

AD_CRAFT_AUDIO_ALIAS = "adcraft-audio"


class AdCraftAudioTestRequest(BaseModel):
    prompt: Optional[str] = None
    video_job_id: Optional[str] = None
    wait_seconds: float = 60.0


def _adcraft_audio_status_payload() -> Dict:
    from audio.config import get_audio_model_config
    from config.settings import settings

    try:
        cfg = get_audio_model_config(AD_CRAFT_AUDIO_ALIAS)
    except ValueError:
        cfg = None
    base = f"http://{settings.host}:{settings.port}"
    return {
        "alias": AD_CRAFT_AUDIO_ALIAS,
        "configured": cfg is not None,
        "config": cfg,
        "driver": cfg.get("driver") if cfg else None,
        "family": cfg.get("family") if cfg else None,
        "endpoint": f"{base}/v1/audio/video-to-sfx",
        "api_key": API_KEY_VALUE,
        "api_key_env": API_KEY_ENV,
        "auth_required": bool(settings.keyrouter_api_key),
        "gateway_configured": bool(settings.modal_audio_gateway_url),
        "gateway_url": settings.modal_audio_gateway_url or None,
        "video_allow_hosts": settings.audio_video_allow_hosts,
    }


@router.get("/api/adcraft-audio/status")
async def adcraft_audio_status():
    return _adcraft_audio_status_payload()


@router.get("/api/adcraft-audio/models")
async def adcraft_audio_models():
    from audio.config import get_audio_model_config, load_audio_models

    options = []
    for alias in sorted(load_audio_models().keys()):
        try:
            cfg = get_audio_model_config(alias)
        except ValueError:
            continue
        options.append({
            "id": alias,
            "name": alias,
            "target": alias,
            "driver": cfg["driver"],
            "family": cfg.get("family"),
            "quality_tier": cfg.get("quality_tier"),
            "max_video_seconds": cfg.get("max_video_seconds"),
            "response_format": cfg.get("response_format"),
        })
    return {"models": options}


@router.get("/api/adcraft-audio/jobs")
async def adcraft_audio_jobs(limit: int = 20):
    from audio.jobs import audio_job_store
    from models.audio import FINAL_STATUSES
    from proxy.routers.audio import advance_job, serialize_job

    rows = await asyncio.to_thread(audio_job_store.list_recent, int(limit))
    pending = [r for r in rows if r.get("status") not in FINAL_STATUSES][:5]
    if pending:
        await asyncio.gather(*(advance_job(r) for r in pending), return_exceptions=True)
        rows = await asyncio.to_thread(audio_job_store.list_recent, int(limit))
    return {"jobs": [serialize_job(row) for row in rows]}


@router.get("/api/adcraft-audio/jobs/{job_id}")
async def adcraft_audio_job(job_id: str):
    from audio.jobs import audio_job_store
    from proxy.routers.audio import advance_job, serialize_job

    row = await asyncio.to_thread(audio_job_store.get, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Audio job '{job_id}' not found")
    row = await advance_job(row)
    return serialize_job(row)


@router.get("/api/adcraft-audio/jobs/{job_id}/content")
async def adcraft_audio_job_content(job_id: str):
    """UI preview endpoint (no client auth), so the browser can play the WAV."""
    from fastapi.responses import FileResponse
    from audio.jobs import audio_job_store
    from proxy.routers.audio import advance_job

    row = await asyncio.to_thread(audio_job_store.get, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Audio job '{job_id}' not found")
    row = await advance_job(row)
    if row.get("status") != "succeeded":
        raise HTTPException(status_code=409, detail=f"Job is '{row.get('status')}', not ready")
    path = audio_job_store.video_path(job_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Audio file is no longer available")
    return FileResponse(path, media_type="audio/wav", filename=f"{job_id}.wav")


@router.post("/api/adcraft-audio/test")
async def adcraft_audio_test(request: AdCraftAudioTestRequest | None = None):
    import base64 as _b64
    import subprocess as _sp
    import tempfile as _tmp

    from audio.errors import AudioApiError
    from audio.jobs import audio_job_store
    from models.audio import FINAL_STATUSES, AudioGenerationRequest
    from proxy.routers.audio import advance_job, serialize_job, submit_audio_job
    from video.jobs import job_store as video_job_store

    body = request or AdCraftAudioTestRequest()
    status_payload = _adcraft_audio_status_payload()
    if not status_payload["configured"]:
        return {
            "status": "error",
            "error": {"code": "not_configured", "message": "No 'adcraft-audio' alias configured."},
            **status_payload,
        }

    video_bytes = None
    source = None
    if body.video_job_id:
        path = video_job_store.video_path(body.video_job_id)
        if os.path.exists(path):
            video_bytes = await asyncio.to_thread(lambda: open(path, "rb").read())
            source = f"video_job:{body.video_job_id}"

    if video_bytes is None:
        for row in await asyncio.to_thread(video_job_store.list_recent, 20):
            if row.get("status") == "completed":
                path = video_job_store.video_path(row["id"])
                if os.path.exists(path):
                    video_bytes = await asyncio.to_thread(lambda p=path: open(p, "rb").read())
                    source = f"video_job:{row['id']}"
                    break

    if video_bytes is None:
        # No video generated yet: synthesize a short clip so the test still runs.
        def _make_clip() -> Optional[bytes]:
            with _tmp.NamedTemporaryFile(suffix=".mp4", delete=False) as fh:
                path = fh.name
            try:
                proc = _sp.run(
                    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                     "-i", "testsrc2=size=640x360:rate=16:duration=2",
                     "-pix_fmt", "yuv420p", "-c:v", "libx264", path],
                    capture_output=True, timeout=60,
                )
                if proc.returncode != 0 or not os.path.exists(path):
                    return None
                with open(path, "rb") as fh:
                    return fh.read()
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        video_bytes = await asyncio.to_thread(_make_clip)
        source = "generated:testsrc2"

    if not video_bytes:
        return {
            "status": "error",
            "error": {"code": "no_video_source", "message": "No completed video and ffmpeg is unavailable."},
            **status_payload,
        }

    req = AudioGenerationRequest(
        model=AD_CRAFT_AUDIO_ALIAS,
        prompt=body.prompt or "footsteps on dry gravel, cloth rustle, distant birds",
        duration_seconds=5,
        response_format="wav",
        video_b64=_b64.b64encode(video_bytes).decode("ascii"),
    )
    try:
        job, _ = await submit_audio_job(req)
    except AudioApiError as exc:
        return {
            "status": "error",
            "error": {"code": exc.code, "message": exc.message},
            "http_status": exc.status,
            "video_source": source,
            **status_payload,
        }

    deadline = time.monotonic() + max(0.0, float(body.wait_seconds or 0))
    while time.monotonic() < deadline:
        latest = await asyncio.to_thread(audio_job_store.get, job["id"])
        if latest is None:
            break
        job = await advance_job(latest)
        if job.get("status") in FINAL_STATUSES:
            break
        await asyncio.sleep(2.0)

    return {
        "status": "succeeded" if job.get("status") == "succeeded" else "pending",
        "job": serialize_job(job),
        "video_source": source,
        **status_payload,
    }


@router.post("/api/launch")
async def launch_claude(request: LaunchRequest):
    cmd, target_cwd = _resolve_launch(request, "claude")
    try:
        _launch_terminal(cmd, target_cwd)
        return {"status": "success", "message": "Launched Claude Code!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/codex-detect")
async def codex_detect():
    detected = {}
    binary_path = shutil.which("codex")
    if binary_path:
        version = ""
        try:
            result = subprocess.run([binary_path, "--version"], capture_output=True, text=True, timeout=10)
            version = result.stdout.strip().split("\n")[0] if result.returncode == 0 else ""
        except Exception:
            pass
        detected = {
            "binary": binary_path,
            "version": version,
            "name": "Codex CLI",
            "last_detected": datetime.datetime.now().isoformat()
        }
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.json")
        data = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    pass
        data["codex_cli_detected"] = detected
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return {"detected": detected}


@router.post("/api/codex-setup")
async def codex_setup(request: CodexSetupRequest | None = None):
    base_url = (request.base_url if request and request.base_url else CODEX_BASE_URL)
    changed = setup_codex_config(base_url=base_url)
    persisted = persist_codex_env_var()
    return {
        "status": "success",
        "configured": changed,
        "config_path": get_codex_config_path(),
        "base_url": base_url,
        "env_persisted": persisted
    }


@router.post("/api/codex-launch")
async def codex_launch(request: LaunchRequest):
    cmd, target_cwd = _resolve_launch(request, "codex")
    try:
        _launch_terminal(cmd, target_cwd)
        return {"status": "success", "message": "Launched Codex!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/browse-folder")
async def browse_folder():
    try:
        system = platform.system()
        def _run_linux_picker():
            for picker in ["zenity", "kdialog", "yad"]:
                if shutil.which(picker):
                    try:
                        if picker == "zenity":
                            cmd = [picker, "--file-selection", "--directory", "--title=Select Project Folder"]
                        elif picker == "yad":
                            cmd = [picker, "--file-selection", "--directory", "--title=Select Project Folder"]
                        else:
                            cmd = [picker, "--getexistingdirectory", "--title=Select Project Folder"]
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                        path = result.stdout.strip()
                        if path:
                            return path
                    except Exception:
                        pass
            return None
        def _run_win_picker():
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                path = filedialog.askdirectory(parent=root, title="Select Project Folder")
                root.destroy()
                return path if path else None
            except Exception:
                return None
        def _run_tkinter_picker():
            script = '''
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
print(filedialog.askdirectory(parent=root, title="Select Project Folder"))
'''
            try:
                result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
                return result.stdout.strip()
            except Exception:
                return None
        if system == "Linux":
            path = await asyncio.to_thread(_run_linux_picker)
            if path:
                return {"path": path}
        if system == "Windows":
            path = await asyncio.to_thread(_run_win_picker)
            if path:
                return {"path": path}
        path = await asyncio.to_thread(_run_tkinter_picker)
        if path:
            return {"path": path}
    except Exception:
        pass
    return {"path": ""}


@router.api_route("/api/hello", methods=["GET", "HEAD"])
async def api_hello():
    return {"message": "key-router proxy is running"}
