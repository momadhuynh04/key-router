"""Optional bearer auth for the /v1/videos* routes only.

If `KEYROUTER_API_KEY` is empty the proxy stays unauthenticated (current
behaviour for every other route). When it is set, only the video routes enforce
it — text/image/agent routes are untouched.
"""

from fastapi import Header

from config.settings import settings
from video.errors import VideoApiError


async def require_video_auth(authorization: str | None = Header(default=None)) -> None:
    key = settings.keyrouter_api_key
    if not key:
        return
    if authorization != f"Bearer {key}":
        raise VideoApiError(401, "unauthorized", "Missing or invalid API key.")
