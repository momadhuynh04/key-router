import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse, JSONResponse
from config.logging import logger, log_error
from models.openai_compat import OpenAIRequest
from proxy.router import provider_router
from proxy.errors import openai_error
from proxy.openai_ingress import openai_chat_to_anthropic, anthropic_response_to_openai_chat, anthropic_events_to_openai_stream, openai_error_line

router = APIRouter()


@router.post("/v1/chat/completions")
async def handle_chat_completions(request: OpenAIRequest):
    logger.info(f"[🤖] Received request from OpenAI-compatible client (Codex) for model: {request.model}")
    try:
        anthropic_request = openai_chat_to_anthropic(request)
        provider = provider_router.get_provider_for_openai(anthropic_request.model)
        provider_request_body = await provider.translate_request(anthropic_request)

        if request.stream:
            async def event_generator():
                try:
                    async for line in anthropic_events_to_openai_stream(
                        provider.stream(provider_request_body), request.model
                    ):
                        yield line
                except httpx.HTTPStatusError as e:
                    error_detail = e.response.text if hasattr(e, 'response') else str(e)
                    error_msg = f"Provider API Error {e.response.status_code}: {error_detail[:2000]}"
                    log_error(error_msg)
                    yield openai_error_line(error_msg)
                    yield "data: [DONE]\n\n"
                except Exception as e:
                    log_error(f"chat.completions stream failed: {e}", e)
                    yield openai_error_line(str(e)[:2000])
                    yield "data: [DONE]\n\n"

            return StreamingResponse(event_generator(), media_type="text/event-stream")
        else:
            response = await provider.generate(provider_request_body)
            return anthropic_response_to_openai_chat(response, request.model)

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text if hasattr(e, 'response') else str(e)
        error_msg = f"Provider API Error {e.response.status_code}: {error_detail[:2000]}"
        log_error(error_msg)
        return openai_error(e.response.status_code, error_msg)
    except Exception as e:
        return openai_error(500, str(e)[:2000])
