import logging

logger = logging.getLogger("key-router")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def log_error(msg: str, exc: Exception | None = None) -> None:
    if exc is not None:
        logger.error(f"{msg}: {exc.__class__.__name__}: {exc}")
    else:
        logger.error(msg)


def log_warn(msg: str) -> None:
    logger.warning(msg)


def log_info(msg: str) -> None:
    logger.info(msg)
