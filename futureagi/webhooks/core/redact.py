"""Error reports that carry no secret (K2).

An error reporter that captures frame variables (FA's Sentry runs with ``include_local_variables=True`` and scrubs by
variable name only) would otherwise send the decrypted secrets, the URL, the custom header values and the body that
the frames below the dispatcher hold. :func:`log_exception` clears the locals of every finished frame of the exception
(and of its cause and context) before logging it, so the report keeps the stack, the exception type and its message,
and no frame variables below the frame that caught it.
"""

from __future__ import annotations

import logging
import traceback


def clear_exception_frames(exc: BaseException | None) -> None:
    """Drop the locals of every finished frame in ``exc``'s traceback, its cause and its context. Frames still
    executing (the one handling the exception) cannot be cleared and are skipped by :func:`traceback.clear_frames`."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        traceback.clear_frames(exc.__traceback__)
        if exc.__cause__ is not None and id(exc.__cause__) not in seen:
            clear_exception_frames(exc.__cause__)
        exc = exc.__context__


def log_exception(
    logger: logging.Logger, message: str, exc: BaseException, **extra
) -> None:
    """``logger.error(message, exc_info=exc)`` after :func:`clear_exception_frames`."""
    clear_exception_frames(exc)
    logger.error(
        message,
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={"error_type": type(exc).__name__, **extra},
    )
