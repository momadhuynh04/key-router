"""Video drivers — one adapter per generation backend."""

from video.drivers.base import DriverStatus, VideoDriver
from video.drivers.modal_gateway import ModalGatewayDriver
from video.drivers.paid_api import PaidApiDriver

__all__ = ["DriverStatus", "VideoDriver", "ModalGatewayDriver", "PaidApiDriver"]
