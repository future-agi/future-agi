"""Real logging-pipeline capture helpers for SAML security tests."""

from __future__ import annotations

import io
import logging
import logging.config
from collections.abc import Iterator
from dataclasses import dataclass
from unittest.mock import Mock, patch

from django.conf import settings
from django.test.utils import override_settings


@dataclass
class SecurityLogCapture:
    """Captures rendered records without bypassing configured processors."""

    root_stream: io.StringIO
    saml_stream: io.StringIO
    sentry_transport: Mock
    breadcrumb_spy: Mock

    @property
    def rendered(self) -> str:
        return self.root_stream.getvalue() + self.saml_stream.getvalue()


def capture_security_logging() -> Iterator[SecurityLogCapture]:
    """Yield capture handles attached to the configured structured formatters.

    The helper reuses the real LOGGING dictionary and live structlog setup; it
    deliberately does not use ``structlog.testing.capture_logs``.
    """

    from contextlib import contextmanager

    from tfc.logging.config import configure_structlog

    @contextmanager
    def _capture() -> Iterator[SecurityLogCapture]:
        from tfc.logging.config import get_logging_config

        logging_config = get_logging_config(str(settings.BASE_DIR))
        logging.config.dictConfig(logging_config)
        configure_structlog()
        root_stream = io.StringIO()
        saml_stream = io.StringIO()
        root_logger = logging.getLogger()
        saml_logger = logging.getLogger("saml2")
        root_handler = next(
            handler
            for handler in root_logger.handlers
            if isinstance(handler, logging.StreamHandler)
        )
        saml_handler = next(
            handler
            for handler in saml_logger.handlers
            if isinstance(handler, logging.StreamHandler)
        )
        root_original_stream = root_handler.stream
        saml_original_stream = saml_handler.stream
        root_handler.setStream(root_stream)
        saml_handler.setStream(saml_stream)
        transport = Mock(name="sentry_transport")
        breadcrumbs = Mock(name="sentry_breadcrumb")
        try:
            with (
                override_settings(LOGGING=logging_config),
                patch("sentry_sdk.transport.HttpTransport", return_value=transport),
                patch("sentry_sdk.add_breadcrumb", breadcrumbs),
            ):
                yield SecurityLogCapture(
                    root_stream=root_stream,
                    saml_stream=saml_stream,
                    sentry_transport=transport,
                    breadcrumb_spy=breadcrumbs,
                )
        finally:
            root_handler.setStream(root_original_stream)
            saml_handler.setStream(saml_original_stream)
            logging.config.dictConfig(settings.LOGGING)

    return _capture()
