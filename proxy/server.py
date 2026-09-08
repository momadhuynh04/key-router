from proxy.app import create_app
from proxy.router import provider_router
import platform
from proxy.server_helpers import (
    PROXY_URL,
    _save_ide_detection,
    _safe_merge_json,
    _get_ide_settings_path,
    IDE_DEFINITIONS,
)
import proxy.server_helpers as _helpers

def _detect_ides(*a, **kw):
    return _helpers._detect_ides(*a, **kw)

def _launch_ide(*a, **kw):
    return _helpers._launch_ide(*a, **kw)

app = create_app()

# Back-compat: old tests patch proxy.server.provider_router
# Ensure patching either proxy.server.provider_router or proxy.router.provider_router affects handlers
import proxy.routers.anthropic as _anth
import proxy.routers.openai as _openai_r
import proxy.routers.responses as _resp_r
# Handlers import provider_router from proxy.router directly, so patch proxy.router.provider_router
# This shim makes proxy.server.provider_router.get_provider delegate to the current router instance
# so patch('proxy.server.provider_router.get_provider') also works via monkey-patching the shared instance.
