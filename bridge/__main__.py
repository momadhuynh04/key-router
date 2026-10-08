"""Entry point: `python -m bridge`."""
from __future__ import annotations

import logging

import uvicorn

from bridge.app import create_app
from bridge.config import ensure_config


def main() -> None:
    # Spawn logging must reach bridge.log; uvicorn only configures its own loggers.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = ensure_config()
    print(f"agent-bridge listening on http://{cfg.host}:{cfg.port} ({len(cfg.agents)} agent(s))")
    uvicorn.run(create_app(), host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
