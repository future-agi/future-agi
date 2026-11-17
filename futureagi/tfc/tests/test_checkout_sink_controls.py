"""Native logging controls stay independent of the configured output boundary."""

import logging
import sys

import pytest

from tfc.logging.config import configure_structlog
from tfc.tests.checkout_sinks import configured_sinks, native_emit

pytestmark = pytest.mark.integration
PREFIX = "/agentcc/managed-credits/checkout/"


def test_native_control_survives_sink_configuration():
    configure_structlog(cache_logger_on_first_use=False)
    boundary = logging.StreamHandler.emit
    value = "synthetic-native-control-0428"
    with native_emit():
        for _ in range(2):
            with configured_sinks("checkout.native.control", otlp=False) as (
                log,
                sinks,
            ):
                log.error("GET %s", PREFIX + value + "/")
                assert not getattr(
                    logging.StreamHandler.emit, "_checkout_output_boundary", False
                ), "sink configuration reinstalled the output boundary"
            for output in sinks.outputs().values():
                assert value in output, "native output was still sanitized"
    assert logging.StreamHandler.emit is boundary
    with configured_sinks("checkout.protected.control", otlp=False) as (log, sinks):
        log.error("GET %s", PREFIX + value + "/")
    assert sinks.emitted()
    assert sinks.leaks(value) == []


def test_native_control_restores_boundary_after_exception():
    configure_structlog(cache_logger_on_first_use=False)
    boundary = logging.StreamHandler.emit
    with pytest.raises(RuntimeError, match="synthetic control exit"):
        with native_emit():
            with configured_sinks("checkout.native.exception", otlp=False):
                assert logging.StreamHandler.emit is boundary.__wrapped__
                raise RuntimeError("synthetic control exit")
    assert logging.StreamHandler.emit is boundary


def test_native_control_can_be_nested():
    configure_structlog(cache_logger_on_first_use=False)
    boundary = logging.StreamHandler.emit
    with native_emit():
        native = logging.StreamHandler.emit
        with native_emit():
            with configured_sinks("checkout.native.nested", otlp=False):
                assert logging.StreamHandler.emit is native
        assert logging.StreamHandler.emit is native
    assert logging.StreamHandler.emit is boundary


def test_converted_parity_detects_extra_application_evaluation(monkeypatch):
    from tfc.logging import checkout_output
    from tfc.tests.test_checkout_boundary_storage import (
        test_rendering_uses_a_copy_without_application_hooks as converted,
    )

    calls = []
    scrub = checkout_output.scrub_rendered

    def extra_evaluation(text, **kwargs):
        record = sys._getframe(1).f_locals.get("record")
        if record is not None and type(record.msg).__name__ == "ApplicationValue":
            str(record.msg)
            calls.append("extra")
        return scrub(text, **kwargs)

    monkeypatch.setattr(checkout_output, "scrub_rendered", extra_evaluation)
    with pytest.raises(AssertionError):
        converted("logger", "msg", "custom")
    assert calls, "the extra-evaluation control did not run"
