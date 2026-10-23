"""Alias → driver resolution for the video subsystem."""

from typing import Any, Dict, Tuple

from video.config import get_video_model_config
from video.drivers.base import VideoDriver
from video.drivers.modal_gateway import ModalGatewayDriver
from video.drivers.paid_api import PaidApiDriver

_DRIVERS = {
    "modal_gateway": ModalGatewayDriver,
    "paid_api": PaidApiDriver,
}


def resolve_driver(alias: str) -> Tuple[VideoDriver, Dict[str, Any]]:
    cfg = get_video_model_config(alias)
    driver_name = cfg["driver"]
    driver_cls = _DRIVERS.get(driver_name)
    if driver_cls is None:
        raise ValueError(
            f"Unsupported video driver '{driver_name}' for alias '{alias}'. "
            f"Expected one of: {', '.join(sorted(_DRIVERS))}."
        )
    return driver_cls(cfg), cfg
