"""Structured failure events for boundaries handling private SQL, IDs or credentials."""

import logging
import os
from contextlib import suppress

import structlog


class _RedactedExceptionSink:
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def __getattr__(self, name):
        # The configured structlog processors inspect the live stdlib logger's
        # name, disabled flag and effective level.
        return getattr(self.logger, name)

    def exception(self, msg, *args, **kwargs):
        # ProcessorFormatter consumes structlog's exc_info. Logger.exception would
        # then capture the *active* exception again on the raw LogRecord (including
        # for Sentry). Supply an unraised marker: no message, chain or frame locals
        # from the real failure, and no need for exporters to infer a stack.
        marker = RuntimeError("Private exception details omitted")
        kwargs["exc_info"] = (RuntimeError, marker, None)
        self.logger.exception(msg, *args, **kwargs)


class PrivateBoundaryLogger:
    """Log an exception class and safe fields, never the exception or its chain."""

    def __init__(self, name: str):
        self._logger = structlog.wrap_logger(
            _RedactedExceptionSink(name),
            wrapper_class=structlog.stdlib.BoundLogger,
        )

    def exception(self, event: str, error: BaseException, **safe_fields) -> None:
        # Do not let the structlog processor chain render the active exception
        # either. Callers supply only fixed codes and non-private metadata.
        try:
            self._logger.exception(
                event, error_type=type(error).__name__, exc_info=False, **safe_fields
            )
        except Exception:  # noqa: BLE001 - a broken log handler must never abort cleanup or replace the original error
            # Do not recurse into the failed handler or print its exception chain.
            with suppress(OSError):  # stderr may be closed during teardown
                os.write(2, b'{"event":"private_boundary_log_unavailable"}\n')
