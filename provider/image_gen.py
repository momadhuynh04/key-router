"""Image generation backends for the AdCraft `adcraft-image` route.

AdCraft always speaks one contract; this module hides every backend difference:
OpenRouter's image API (base64 passthrough) and Replicate (prediction + polling +
URL download). It also normalizes references, aspect ratios and output formats.
"""

import asyncio
import base64
import math
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config.image_models import get_image_model_config
from config.logging import log_warn
from config.settings import settings

OPENROUTER_IMAGES_URL = "https://openrouter.ai/api/v1/images"
OPENROUTER_IMAGES_MODELS_URL = "https://openrouter.ai/api/v1/images/models"
REPLICATE_API_URL = "https://api.replicate.com/v1"

ROLE_PRIORITY = {
    "character_reference": 0,
    "product_reference": 1,
    "scene_reference": 2,
}
ROLE_LABEL = {
    "character_reference": "character",
    "product_reference": "product",
    "scene_reference": "scene",
}

ASPECT_SIZES = {
    "1:1": (1024, 1024),
    "16:9": (1344, 768),
    "9:16": (768, 1344),
    "4:3": (1152, 864),
    "3:4": (864, 1152),
}


class ImageError(Exception):
    """Error surfaced to the client as {"error": {"code", "message"}}."""

    def __init__(self, status: int, code: str, message: str, headers: Optional[Dict[str, str]] = None):
        super().__init__(message)
        self.status = int(status)
        self.code = code
        self.message = message
        self.headers = headers or {}


# ---------------------------------------------------------------------------
# Aspect ratio + reference normalization
# ---------------------------------------------------------------------------

def _ratio_value(value: Optional[str]) -> Optional[float]:
    try:
        a, b = (value or "").split(":", 1)
        a_f, b_f = float(a), float(b)
        if a_f <= 0 or b_f <= 0:
            return None
        return a_f / b_f
    except (ValueError, AttributeError):
        return None


def nearest_supported_ratio(requested: Optional[str], supported: Optional[List[str]]) -> Optional[str]:
    """Return the requested ratio if supported, else the closest one."""
    ratios = [r for r in (supported or []) if isinstance(r, str) and r != "auto"]
    if not requested:
        return None
    if not ratios:
        return requested
    if requested in ratios:
        return requested
    want = _ratio_value(requested)
    if want is None:
        return ratios[0]
    best = ratios[0]
    best_delta = None
    for ratio in ratios:
        value = _ratio_value(ratio)
        if value is None:
            continue
        delta = abs(value - want)
        if best_delta is None or delta < best_delta:
            best, best_delta = ratio, delta
    return best


def ratio_to_size(ratio: Optional[str]) -> Tuple[int, int]:
    if ratio in ASPECT_SIZES:
        return ASPECT_SIZES[ratio]
    value = _ratio_value(ratio)
    if value is None:
        return 1024, 1024
    height = round(math.sqrt((1024 * 1024) / value))
    width = round(height * value)
    width = max(32, (width // 32) * 32)
    height = max(32, (height // 32) * 32)
    return width, height


def select_references(refs: Optional[List[Dict[str, Any]]], max_refs: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Priority-sort references and keep at most `max_refs`.

    Raises 422 when the target model supports no references at all.
    """
    valid = [r for r in (refs or []) if isinstance(r, dict) and r.get("value")]
    if not valid:
        return [], []
    if max_refs <= 0:
        raise ImageError(
            422,
            "references_unsupported",
            "The selected image model does not support reference images.",
        )
    ranked = sorted(
        enumerate(valid),
        key=lambda pair: (ROLE_PRIORITY.get(pair[1].get("role") or "", 99), pair[0]),
    )
    selected = [r for _, r in ranked[:max_refs]]
    dropped = [r for _, r in ranked[max_refs:]]
    return selected, dropped


def augment_prompt(prompt: str, refs: List[Dict[str, Any]]) -> str:
    """Append role notes so models that only accept image URLs still understand roles."""
    if not refs:
        return prompt
    notes = []
    for index, ref in enumerate(refs, 1):
        label = ROLE_LABEL.get(ref.get("role") or "")
        if label:
            notes.append(f"Reference {index} is the {label} reference.")
    if not notes:
        return prompt
    return f"{prompt}\n\n" + "\n".join(notes)


# ---------------------------------------------------------------------------
# Image validation / metadata
# ---------------------------------------------------------------------------

def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def _timeout(deadline: float) -> httpx.Timeout:
    return httpx.Timeout(max(1.0, _remaining(deadline)))


def detect_media_type(data: bytes) -> Optional[str]:
    if not data:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _jpeg_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    i, size = 2, len(data)
    while i + 9 < size:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 4 > size:
            break
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if length < 2:
            break
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 <= size:
                height = int.from_bytes(data[i + 5:i + 7], "big")
                width = int.from_bytes(data[i + 7:i + 9], "big")
                return width, height
        i += 2 + length
    return None


def _webp_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return width, height
    return None


def image_dimensions(data: bytes, media_type: str) -> Optional[Tuple[int, int]]:
    try:
        if media_type == "image/png" and len(data) >= 24:
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if media_type == "image/jpeg":
            return _jpeg_dimensions(data)
        if media_type == "image/webp":
            return _webp_dimensions(data)
    except Exception:
        return None
    return None


def _finalize_image(raw: bytes) -> Dict[str, Any]:
    if not raw:
        raise ImageError(502, "empty_image", "Upstream returned an empty image.")
    media_type = detect_media_type(raw)
    if media_type is None:
        raise ImageError(502, "invalid_image", "Upstream returned data that is not a PNG/JPEG/WEBP image.")
    b64 = base64.b64encode(raw).decode("ascii")
    if len(b64) > settings.image_max_response_bytes:
        raise ImageError(
            413,
            "image_too_large",
            f"Generated image is too large ({len(raw)} bytes) and cannot be returned within the limit.",
        )
    dims = image_dimensions(raw, media_type) or (None, None)
    return {
        "b64_json": b64,
        "media_type": media_type,
        "bytes": len(raw),
        "width": dims[0],
        "height": dims[1],
    }


# ---------------------------------------------------------------------------
# OpenRouter capabilities (for the WebUI dropdown + accurate limits)
# ---------------------------------------------------------------------------

_OR_MODELS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_OR_MODELS_TTL = 600.0


def caps_from_model(model: Dict[str, Any]) -> Dict[str, Any]:
    params = model.get("supported_parameters") or {}
    refs = params.get("input_references")
    max_refs = 0
    if isinstance(refs, dict) and isinstance(refs.get("max"), (int, float)):
        max_refs = int(refs["max"])
    aspect = params.get("aspect_ratio")
    ratios = aspect.get("values") if isinstance(aspect, dict) else []
    return {
        "id": model.get("id"),
        "name": model.get("name") or model.get("id"),
        "max_references": max_refs,
        "aspect_ratios": ratios if isinstance(ratios, list) else [],
        "supports_output_format": "output_format" in params,
    }


async def fetch_openrouter_image_models(force: bool = False) -> List[Dict[str, Any]]:
    now = time.time()
    cached = _OR_MODELS_CACHE.get("data")
    if not force and cached is not None and (now - _OR_MODELS_CACHE.get("ts", 0.0)) < _OR_MODELS_TTL:
        return cached

    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/momadhuynh04/key-router",
        "X-Title": "key-router Proxy",
    }
    if settings.openrouter_api_key:
        headers["Authorization"] = f"Bearer {settings.openrouter_api_key}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(OPENROUTER_IMAGES_MODELS_URL, headers=headers)
        if resp.status_code == 200:
            data = resp.json().get("data") or []
            if isinstance(data, list):
                _OR_MODELS_CACHE["ts"] = now
                _OR_MODELS_CACHE["data"] = data
                return data
        else:
            log_warn(f"OpenRouter image models returned HTTP {resp.status_code}")
    except Exception as e:
        log_warn(f"OpenRouter image models fetch failed: {e}")
    return _OR_MODELS_CACHE.get("data") or []


def _openrouter_caps(model_id: str) -> Optional[Dict[str, Any]]:
    for model in _OR_MODELS_CACHE.get("data") or []:
        if model.get("id") == model_id:
            return caps_from_model(model)
    return None


async def resolve_image_config(backend: str, upstream_model: str) -> Dict[str, Any]:
    """Merge config.json overrides with live OpenRouter capabilities.

    For OpenRouter the live capability list is authoritative: it tells us the real
    reference limit, supported aspect ratios and whether `output_format` is
    accepted. Config values are the fallback when the capability fetch fails.
    """
    target_id = f"{backend}/{upstream_model}"
    cfg = get_image_model_config(target_id)

    if backend == "openrouter":
        if _OR_MODELS_CACHE.get("data") is None:
            await fetch_openrouter_image_models()
        caps = _openrouter_caps(upstream_model)
        if caps:
            cfg["max_references"] = caps["max_references"]
            if caps["aspect_ratios"]:
                cfg["supported_aspect_ratios"] = caps["aspect_ratios"]
            cfg["output_format_field"] = "output_format" if caps["supports_output_format"] else None
    return cfg


# ---------------------------------------------------------------------------
# Upstream error + download helpers
# ---------------------------------------------------------------------------

def _upstream_error(resp: httpx.Response, backend: str) -> ImageError:
    status = resp.status_code
    try:
        payload = resp.json()
    except Exception:
        payload = {}
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        message = err.get("message") or str(err)
    elif isinstance(err, str):
        message = err
    else:
        message = getattr(resp, "text", "")[:500] or "Upstream error"
    code = "rate_limited" if status == 429 else ("upstream_error" if status >= 500 else "upstream_rejected")
    headers: Dict[str, str] = {}
    if status == 429:
        retry_after = resp.headers.get("retry-after") if hasattr(resp, "headers") else None
        if retry_after:
            headers["Retry-After"] = retry_after
    return ImageError(status, code, f"{backend}: {message}"[:800], headers=headers)


async def _download(client: httpx.AsyncClient, url: str, deadline: float) -> bytes:
    resp = await client.get(url, timeout=_timeout(deadline))
    if resp.status_code != 200:
        raise ImageError(502, "image_download_failed", f"Failed to download generated image (HTTP {resp.status_code}).")
    return resp.content


def _first_output_url(output: Any) -> Optional[str]:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        for item in output:
            url = _first_output_url(item)
            if url:
                return url
    if isinstance(output, dict):
        for key in ("url", "image", "image_url", "output"):
            url = _first_output_url(output.get(key))
            if url:
                return url
    return None


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

async def _openrouter_generate(
    cfg: Dict[str, Any],
    prompt: str,
    aspect_ratio: Optional[str],
    output_format: Optional[str],
    ref_values: Optional[List[str]],
    deadline: float,
) -> bytes:
    api_key = settings.openrouter_api_key
    if not api_key:
        raise ImageError(500, "missing_api_key", "OPENROUTER_API_KEY is not set in .env.")

    body: Dict[str, Any] = {"model": cfg["upstream_model"], "prompt": prompt, "n": 1}
    if aspect_ratio:
        ratio = nearest_supported_ratio(aspect_ratio, cfg.get("supported_aspect_ratios") or [])
        if ratio:
            body["aspect_ratio"] = ratio
    format_field = cfg.get("output_format_field")
    if output_format and format_field:
        body[format_field] = output_format
    if ref_values:
        # OpenRouter expects objects, not bare strings:
        #   {"type": "image_url", "image_url": {"url": "https://… | data:…"}}
        body["input_references"] = [
            {"type": "image_url", "image_url": {"url": value}}
            for value in ref_values
        ]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/momadhuynh04/key-router",
        "X-Title": "key-router Proxy",
    }

    async with httpx.AsyncClient(timeout=_timeout(deadline)) as client:
        resp = await client.post(OPENROUTER_IMAGES_URL, headers=headers, json=body)
        if resp.status_code != 200:
            raise _upstream_error(resp, "openrouter")
        try:
            payload = resp.json()
        except Exception:
            raise ImageError(502, "invalid_upstream_response", "OpenRouter returned a non-JSON image response.")

        items = payload.get("data") if isinstance(payload, dict) else None
        if not items or not isinstance(items[0], dict):
            raise ImageError(502, "empty_upstream_response", "OpenRouter returned no image data.")

        item = items[0]
        b64 = item.get("b64_json")
        if b64:
            try:
                return base64.b64decode(b64, validate=True)
            except Exception:
                raise ImageError(502, "invalid_image", "OpenRouter returned invalid base64 image data.")
        url = item.get("url")
        if url:
            return await _download(client, url, deadline)
        raise ImageError(502, "empty_upstream_response", "OpenRouter image item had neither b64_json nor url.")


async def _replicate_generate(
    cfg: Dict[str, Any],
    prompt: str,
    aspect_ratio: Optional[str],
    output_format: Optional[str],
    ref_values: Optional[List[str]],
    deadline: float,
) -> bytes:
    token = settings.replicate_api_token
    if not token:
        raise ImageError(500, "missing_api_key", "REPLICATE_API_TOKEN is not set in .env.")

    upstream = cfg.get("upstream_model") or ""
    if "/" not in upstream:
        raise ImageError(400, "invalid_model", f"Replicate model '{upstream}' must be 'owner/name'.")
    owner, name = upstream.split("/", 1)

    model_input: Dict[str, Any] = {"prompt": prompt}
    if ref_values:
        field = cfg.get("reference_field")
        if not field:
            raise ImageError(422, "references_unsupported", "This Replicate model has no reference field configured.")
        if int(cfg.get("max_references") or 0) <= 1:
            model_input[field] = ref_values[0]
        else:
            model_input[field] = list(ref_values)

    if aspect_ratio:
        mode = cfg.get("aspect_ratio_mode") or "none"
        ratio = nearest_supported_ratio(aspect_ratio, cfg.get("supported_aspect_ratios") or [])
        if mode == "ratio" and ratio:
            model_input[cfg.get("aspect_ratio_field") or "aspect_ratio"] = ratio
        elif mode == "size":
            width, height = ratio_to_size(ratio or aspect_ratio)
            model_input[cfg.get("width_field") or "width"] = width
            model_input[cfg.get("height_field") or "height"] = height

    format_field = cfg.get("output_format_field")
    if output_format and format_field:
        model_input[format_field] = output_format
    for key, value in (cfg.get("extra_input") or {}).items():
        model_input.setdefault(key, value)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "wait=60",
    }
    create_url = f"{REPLICATE_API_URL}/models/{owner}/{name}/predictions"

    async with httpx.AsyncClient(timeout=_timeout(deadline)) as client:
        resp = await client.post(create_url, headers=headers, json={"input": model_input})
        if resp.status_code not in (200, 201):
            raise _upstream_error(resp, "replicate")
        try:
            prediction = resp.json()
        except Exception:
            raise ImageError(502, "invalid_upstream_response", "Replicate returned a non-JSON prediction response.")

        while prediction.get("status") in ("starting", "processing", None):
            if _remaining(deadline) <= 1:
                raise ImageError(504, "upstream_timeout", "Replicate prediction did not finish in time.")
            poll_url = (prediction.get("urls") or {}).get("get")
            if not poll_url:
                break
            await asyncio.sleep(min(1.5, max(0.2, _remaining(deadline))))
            poll = await client.get(poll_url, headers={"Authorization": f"Bearer {token}"})
            if poll.status_code != 200:
                raise _upstream_error(poll, "replicate")
            prediction = poll.json()

        status = prediction.get("status")
        if status != "succeeded":
            message = prediction.get("error") or f"Replicate prediction status: {status}"
            raise ImageError(502, "upstream_failed", str(message)[:800])

        output_url = _first_output_url(prediction.get("output"))
        if not output_url:
            raise ImageError(502, "empty_upstream_response", "Replicate prediction had no output URL.")
        return await _download(client, output_url, deadline)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def generate_image(
    cfg: Dict[str, Any],
    prompt: str,
    aspect_ratio: Optional[str],
    output_format: Optional[str],
    ref_values: Optional[List[str]],
    deadline: float,
) -> Dict[str, Any]:
    """Generate one image, retrying once on 5xx/timeout. Returns the normalized result."""
    backend = cfg.get("backend")
    last_error: Optional[Exception] = None

    for attempt in range(2):
        try:
            if backend == "openrouter":
                raw = await _openrouter_generate(cfg, prompt, aspect_ratio, output_format, ref_values, deadline)
            elif backend == "replicate":
                raw = await _replicate_generate(cfg, prompt, aspect_ratio, output_format, ref_values, deadline)
            else:
                raise ImageError(400, "unsupported_backend", f"Unsupported image backend '{backend}'.")
            return _finalize_image(raw)
        except ImageError as e:
            last_error = e
            if e.status >= 500 and attempt == 0 and _remaining(deadline) > 2:
                log_warn(f"[🔁] Image upstream error {e.status} ({backend}) — retrying once")
                continue
            raise
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError):
            last_error = ImageError(504, "upstream_timeout", "Upstream image request timed out.")
            if attempt == 0 and _remaining(deadline) > 2:
                log_warn(f"[🔁] Image upstream timeout ({backend}) — retrying once")
                continue
            raise last_error

    if isinstance(last_error, ImageError):
        raise last_error
    raise ImageError(500, "internal_error", "Image generation failed.")
