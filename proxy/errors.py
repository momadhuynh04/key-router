from fastapi.responses import JSONResponse


def _sanitize(msg: str, limit: int = 2000) -> str:
    if not isinstance(msg, str):
        msg = str(msg)
    return msg[:limit]


def anthropic_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": "api_error", "message": _sanitize(message)}},
    )


def openai_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": _sanitize(message), "type": "api_error", "code": None}},
    )


def response_failed_sse(message: str) -> str:
    import json
    return f'event: response.failed\ndata: {json.dumps({"type": "response.failed", "response": {"error": {"code": "api_error", "message": _sanitize(message)}}})}\n\n'
