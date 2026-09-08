import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from config.logging import log_warn

def create_app() -> FastAPI:
    app = FastAPI(title="key-router Proxy")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from proxy.routers.anthropic import router as anthropic_router
    from proxy.routers.openai import router as openai_router
    from proxy.routers.responses import router as responses_router
    from proxy.routers.management import router as management_router

    app.include_router(anthropic_router)
    app.include_router(openai_router)
    app.include_router(responses_router)
    app.include_router(management_router)

    @app.api_route("/v1/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"], operation_id="catch_all_v1")
    async def catch_all_v1(path_name: str, request: Request):
        log_warn(f"[⚠️] Unhandled /v1 endpoint called: {request.method} /v1/{path_name}")
        try:
            body = await request.body()
            preview = body[:2048].decode('utf-8', errors='ignore')
            if len(body) > 2048:
                preview += f"... [truncated {len(body) - 2048} bytes]"
            log_warn(f"[⚠️] Body preview: {preview}")
        except Exception:
            pass
        return JSONResponse(
            status_code=404,
            content={"type": "error", "error": {"type": "api_error", "message": f"Endpoint /v1/{path_name} not implemented in key-router proxy."}}
        )

    webui_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webui", "dist")
    if os.path.exists(webui_dist):
        app.mount("/", StaticFiles(directory=webui_dist, html=True), name="webui")
    else:
        @app.get("/")
        async def root():
            return {"message": "WebUI not built. Run 'npm run build' in webui/ directory."}

    return app
