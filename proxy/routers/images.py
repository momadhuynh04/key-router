"""OpenAI-compatible image generation ingress for AdCraft.

AdCraft always calls POST /v1/images/generations (alias: /v1/images) with
model="adcraft-image". The real backend and per-model quirks live behind the
`adcraft-image` mapping; the response is normalized to exactly:

    {"created": <int>, "data": [{"b64_json": "...", "media_type": "image/png"}]}
"""

import base64
import binascii
import struct
import time
import zlib
from functools import lru_cache
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config.image_models import SUPPORTED_BACKENDS
from config.logging import logger, log_warn
from config.model_map import model_mapper
from config.settings import settings
from provider.image_gen import (
    ImageError,
    augment_prompt,
    generate_image,
    resolve_image_config,
    select_references,
)

router = APIRouter()


class ImageReferenceSource(BaseModel):
    type: Optional[str] = None
    value: str
    model_config = {"extra": "ignore"}


class ImageReference(BaseModel):
    type: Optional[str] = "input_image"
    role: Optional[str] = None
    source: Optional[ImageReferenceSource] = None
    model_config = {"extra": "ignore"}


class ImageGenerationRequest(BaseModel):
    model: Optional[str] = None
    prompt: Optional[str] = None
    n: Optional[int] = 1
    aspect_ratio: Optional[str] = None
    output_format: Optional[str] = None
    input_references: Optional[List[ImageReference]] = None
    # Unknown fields (quality, size, background, …) are silently ignored.
    model_config = {"extra": "ignore"}


def tiny_png_data_url() -> str:
    """Build a valid 1x1 PNG data URL for the reference test (no Pillow needed)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    raw = b"\x00" + bytes((220, 40, 40, 255))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


@lru_cache(maxsize=1)
def reference_test_image_data_url(size: int = 256) -> str:
    """256x256 PNG with a red circle on a light background.

    Used by the video "Test with image" action so image-to-video conditioning is
    actually exercised (a 1x1 pixel tells us nothing about the model following it).
    """
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)

    cx = cy = size / 2
    radius_sq = (size * 0.3) ** 2
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # PNG filter: none
        for x in range(size):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius_sq:
                raw += bytes((220, 40, 40))
            else:
                v = min(255, 235 + x * 20 // size)
                raw += bytes((v, v, v))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _normalize_output_format(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip().lower()
    if value == "jpg":
        value = "jpeg"
    return value if value in ("png", "jpeg", "webp") else None


async def run_generation(request: ImageGenerationRequest, deadline_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Resolve the alias, normalize inputs and generate one image.

    Raises ImageError on any failure. Used by both the HTTP endpoint and the
    management "Test" action.
    """
    start = time.monotonic()
    deadline = start + (deadline_seconds if deadline_seconds is not None else settings.image_deadline_seconds)

    model = (request.model or "").strip()
    prompt = (request.prompt or "").strip()
    if not model:
        raise ImageError(400, "invalid_request", "'model' is required.")
    if not prompt:
        raise ImageError(400, "invalid_request", "'prompt' is required.")

    try:
        backend, upstream_model = model_mapper.resolve(model)
    except ValueError as exc:
        raise ImageError(404, "unknown_model", str(exc))
    if backend not in SUPPORTED_BACKENDS:
        raise ImageError(
            400,
            "unsupported_backend",
            f"Backend '{backend}' is not an image backend (expected openrouter or replicate).",
        )

    cfg = await resolve_image_config(backend, upstream_model)

    refs: List[Dict[str, Any]] = []
    for ref in request.input_references or []:
        if ref.source and ref.source.value:
            refs.append({"role": ref.role, "value": ref.source.value})

    selected, dropped = select_references(refs, int(cfg.get("max_references") or 0))
    if dropped:
        log_warn(
            f"[🖼️] Dropped {len(dropped)} reference(s) beyond the limit "
            f"({cfg.get('max_references')}) for {backend}/{upstream_model}"
        )

    final_prompt = augment_prompt(prompt, selected)
    result = await generate_image(
        cfg,
        final_prompt,
        request.aspect_ratio,
        _normalize_output_format(request.output_format),
        [ref["value"] for ref in selected],
        deadline,
    )
    result.update(
        {
            "backend": backend,
            "upstream_model": upstream_model,
            "elapsed_ms": int((time.monotonic() - start) * 1000),
            "references_used": len(selected),
        }
    )
    logger.info(
        f"[🖼️] image ok backend={backend} model={upstream_model} refs={len(selected)} "
        f"bytes={result['bytes']} elapsed={result['elapsed_ms']}ms"
    )
    return result


def _error_response(status: int, code: str, message: str, headers: Optional[Dict[str, str]] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}}, headers=headers)


async def _handle(request: ImageGenerationRequest) -> JSONResponse:
    try:
        result = await run_generation(request)
        return JSONResponse(
            status_code=200,
            content={
                "created": int(time.time()),
                "data": [
                    {
                        "b64_json": result["b64_json"],
                        "media_type": result["media_type"],
                    }
                ],
            },
        )
    except ImageError as exc:
        log_warn(f"[🖼️] image error {exc.status} {exc.code}: {exc.message}")
        return _error_response(exc.status, exc.code, exc.message, exc.headers or None)
    except httpx.TimeoutException:
        return _error_response(504, "upstream_timeout", "Upstream image request timed out.")
    except Exception as exc:
        log_warn(f"[🖼️] image internal error: {exc}")
        return _error_response(500, "internal_error", str(exc)[:500])


@router.post("/v1/images/generations")
async def generate_images(request: ImageGenerationRequest):
    return await _handle(request)


@router.post("/v1/images")
async def generate_images_alias(request: ImageGenerationRequest):
    return await _handle(request)
