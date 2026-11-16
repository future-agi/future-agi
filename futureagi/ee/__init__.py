import logging

logger = logging.getLogger(__name__)


def _ee_stub(name):
    """Returns a callable that logs a warning when used in OSS mode."""

    def _raise(*args, **kwargs):
        logger.warning(f"Could not load ee feature: {name}", exc_info=True)

    _raise.__name__ = name
    _raise.__qualname__ = name
    return _raise
