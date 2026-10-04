"""Real logging-pipeline capture helpers for SAML security tests."""

from __future__ import annotations

import io
import logging
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
        logging_config = settings.LOGGING.copy()
        configure_structlog()
        formatters = settings.LOGGING.get("formatters", {})
        formatter_config = formatters.get("structured", {})
        formatter = logging.Formatter(formatter_config.get("format"))
        root_stream = io.StringIO()
        saml_stream = io.StringIO()
        root_handler = logging.StreamHandler(root_stream)
        saml_handler = logging.StreamHandler(saml_stream)
        root_handler.setFormatter(formatter)
        saml_handler.setFormatter(formatter)
        root_logger = logging.getLogger()
        saml_logger = logging.getLogger("saml2")
        root_logger.addHandler(root_handler)
        saml_logger.addHandler(saml_handler)
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
            root_logger.removeHandler(root_handler)
            saml_logger.removeHandler(saml_handler)

    return _capture()
