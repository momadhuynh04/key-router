"""Alias → driver resolution for the audio subsystem."""

from typing import Any, Dict, Tuple

from audio.config import get_audio_model_config
from audio.drivers.base import AudioDriver
from audio.drivers.modal_gateway import ModalAudioGatewayDriver

_DRIVERS = {
    "modal_gateway": ModalAudioGatewayDriver,
}


def resolve_audio_driver(alias: str) -> Tuple[AudioDriver, Dict[str, Any]]:
    cfg = get_audio_model_config(alias)
    driver_name = cfg["driver"]
    driver_cls = _DRIVERS.get(driver_name)
    if driver_cls is None:
        raise ValueError(
            f"Unsupported audio driver '{driver_name}' for alias '{alias}'. "
            f"Expected one of: {', '.join(sorted(_DRIVERS))}."
        )
    return driver_cls(cfg), cfg
