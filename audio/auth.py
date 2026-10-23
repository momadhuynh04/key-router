"""Optional bearer auth for the /v1/audio* routes only (same key as video)."""

from fastapi import Header

from audio.errors import AudioApiError
from config.settings import settings


async def require_audio_auth(authorization: str | None = Header(default=None)) -> None:
    key = settings.keyrouter_api_key
    if not key:
        return
    if authorization != f"Bearer {key}":
        raise AudioApiError(401, "unauthorized", "Missing or invalid API key.")
