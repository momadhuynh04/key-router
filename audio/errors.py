"""Error types for the isolated audio (video-to-SFX) subsystem."""


class AudioApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = int(status)
        self.code = code
        self.message = message


class AudioNotConfigured(AudioApiError):
    def __init__(self, message: str):
        super().__init__(503, "audio_not_configured", message)


class AudioUpstreamError(AudioApiError):
    def __init__(self, message: str, status: int = 502, code: str = "upstream_error"):
        super().__init__(status, code, message)


class AudioIdempotencyConflict(Exception):
    def __init__(self, message: str = "Idempotency-Key already used with a different payload."):
        super().__init__(message)
