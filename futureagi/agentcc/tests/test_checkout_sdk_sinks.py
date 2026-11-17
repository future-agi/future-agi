"""Real pinned SDK, fake HTTP, configured console/file and telemetry boundary.

A callHandlers tap models consumers such as Sentry's LoggingIntegration (which
runs before/independently of handler propagation). No telemetry is exported.
Non-Stripe positive controls must reach every sink, so silence is not vacuous.
"""

import io
import logging
import logging.config
import secrets
from contextlib import redirect_stdout

import pytest

from agentcc.services import checkout_verification
from agentcc.services.checkout_verification import (
    CheckoutReadRefused,
    read_checkout_session,
)
from agentcc.tests.test_checkout_verification_sdk import (
    FakeStripeHTTP,
    _body,
    _error_body,
    _ok,
)
from tfc.logging.config import configure_structlog, get_logging_config

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
@pytest.mark.parametrize("answer", ["paid", "not-found", "server-error", "timeout"])
def test_sdk_payload_absent_from_configured_sinks(
    tmp_path, monkeypatch, settings, request, level, answer
):
    import stripe

    marker = "SYNTHETIC_PRIVATE_BODY_" + secrets.token_hex(8)
    key = "rk_test_SYNTHETIC_NOT_A_CREDENTIAL_" + secrets.token_hex(8)
    session = "cs_test_SyntheticSinkCheck" + secrets.token_hex(16)
    settings.STRIPE_SECRET_KEY = key
    headers = {"request-id": "req_SyntheticSinkCheck"}
    response = {
        "paid": _ok(
            _body(
                session,
                customer_details={"email": marker + "@example.test"},
                metadata={"private": marker},
            )
        ),
        "not-found": (_error_body(session, marker), 404, headers),
        "server-error": (_error_body(session, marker), 500, headers),
        "timeout": stripe.APIConnectionError(marker, should_retry=True),
    }[answer]
    http = FakeStripeHTTP(response)
    monkeypatch.setattr(checkout_verification, "_http_client", lambda: http)
    monkeypatch.setenv("LOG_LEVEL", level)
    monkeypatch.setenv("LOG_STREAM", "stdout")
    console = io.StringIO()
    telemetry = []
    original_call_handlers = logging.Logger.callHandlers

    def telemetry_boundary(logger, record):
        if logger.name in {"stripe", "fgp_sink_positive_control"}:
            telemetry.append(record.getMessage() + str(vars(record)))
        return original_call_handlers(logger, record)

    monkeypatch.setattr(logging.Logger, "callHandlers", telemetry_boundary)
    with redirect_stdout(console):
        config = get_logging_config(str(tmp_path))
        configure_structlog(cache_logger_on_first_use=False)
        configurator = logging.config.DictConfigurator(config)
        for name in sorted(configurator.config["formatters"]):
            configurator.config["formatters"][name] = configurator.configure_formatter(
                configurator.config["formatters"][name]
            )
        handlers = [
            configurator.configure_handler(configurator.config["handlers"][name])
            for name in ("console", "file")
        ]
        try:
            for name in ("stripe", "fgp_sink_positive_control"):
                logger = logging.getLogger(name)
                monkeypatch.setattr(logger, "handlers", handlers)
                monkeypatch.setattr(logger, "propagate", False)
                monkeypatch.setattr(logger, "disabled", False)
                old_level = logger.level
                # setLevel clears logging's enablement cache; restore it too.
                request.addfinalizer(
                    lambda logger=logger, old_level=old_level: logger.setLevel(
                        old_level
                    )
                )
                logger.setLevel(level)
            logging.getLogger("fgp_sink_positive_control").warning(
                "synthetic_sink_control"
            )
            if answer == "paid":
                state = read_checkout_session(session)
                assert state.payment_status == "paid"
            else:
                with pytest.raises(CheckoutReadRefused):
                    read_checkout_session(session)
            for handler in handlers:
                handler.flush()
            output = [
                console.getvalue(),
                (tmp_path / "logs" / "wsgi.log").read_text(),
                "\n".join(telemetry),
            ]
            for surface in output:
                if "synthetic_sink_control" not in surface:
                    pytest.fail(
                        "configured sink positive control missing", pytrace=False
                    )
                if any(value in surface for value in (key, session, marker)):
                    pytest.fail(
                        "Stripe payload reached a configured output boundary",
                        pytrace=False,
                    )
            assert len(http.calls) == 1
            assert http.closed is True
        finally:
            for handler in handlers:
                handler.close()
