"""Error types for the isolated video subsystem.

All video errors carry an HTTP status + machine code so the routes can emit a
consistent ``{"error": {"code", "message"}}`` body (same shape as the image route).
"""


class VideoApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = int(status)
        self.code = code
        self.message = message


class VideoNotConfigured(VideoApiError):
    def __init__(self, message: str):
        super().__init__(503, "video_not_configured", message)


class VideoUpstreamError(VideoApiError):
    def __init__(self, message: str, status: int = 502, code: str = "upstream_error"):
        super().__init__(status, code, message)


class IdempotencyConflict(Exception):
    def __init__(self, message: str = "Idempotency-Key already used with a different payload."):
        super().__init__(message)
