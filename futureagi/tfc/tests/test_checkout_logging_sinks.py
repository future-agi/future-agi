"""Regressions for consumers outside middleware and the deployed middleware chain.

Logging scenarios are observed in what the configured sinks emit
(checkout_sinks), not in the in-memory record.
"""

import io
import logging
from urllib.parse import quote

import pytest
import structlog
from django.test import Client, override_settings
from django.urls import resolve

from tfc.logging.config import configure_structlog
from tfc.settings.settings import MIDDLEWARE as DEPLOYED_MIDDLEWARE
from tfc.tests.checkout_sinks import configured_sinks

pytestmark = pytest.mark.integration


def capture(name):
    return configured_sinks(name)


def assert_safe(surfaces, supplied):
    assert surfaces
    assert supplied not in str(surfaces), "supplied value reached an observable surface"


def assert_sinks_safe(sinks, supplied):
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(supplied) == [], "supplied value reached a configured sink"
    assert sinks.structured(), "a production line is not one JSON document"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "suffix, host, status",
    [
        ("", "testserver", 301),
        ("/unknown", "testserver", 404),
        ("//", "testserver", 404),
        ("/", "untrusted.invalid", 400),
    ],
)
def test_actual_route_with_deployed_middleware(suffix, host, status):
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-full-stack-681"
    route = f"/agentcc/managed-credits/checkout/{supplied}"
    assert resolve(route + "/").url_name == "agentcc-managed-credit-checkout-status"
    source = (
        "django.security.DisallowedHost"
        if status == 400
        else "django_structlog.middlewares.request"
    )
    with override_settings(
        MIDDLEWARE=DEPLOYED_MIDDLEWARE,
        DEBUG=False,
        APPEND_SLASH=True,
        ALLOWED_HOSTS=["testserver"],
        SECURE_SSL_REDIRECT=False,
    ):
        with capture(source) as (_, sinks):
            response = Client().get(route + suffix, HTTP_HOST=host)
    assert response.status_code == status
    assert response.wsgi_request.path == route + suffix
    if status == 301:
        assert response["Location"] == route + "/"
    assert_sinks_safe(sinks, supplied)


@pytest.mark.parametrize(
    "field", ["msg", "args", "joined", "path", "url", "nested", "bytes"]
)
@pytest.mark.parametrize("checkout", [True, False])
def test_new_logger_without_request_or_context(field, checkout):
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-late-sink-681"
    prefix = "/agentcc/managed-credits/checkout" if checkout else "/ordinary"
    url = "http://localhost/mounted" + prefix + "/" + supplied + "?query=1"
    with capture("new.consumer.added.after.configuration") as (logger, sinks):
        if field == "msg":
            logger.error(url)
        elif field == "args":
            logger.error("GET %s status %d", url, 404)
        elif field == "joined":
            logger.error("GET " + prefix + "/%s status %d", supplied, 404)
        else:
            value = (
                {"deep": [url, supplied]}
                if field == "nested"
                else url.encode()
                if field == "bytes"
                else url
            )
            logger.error("diagnostic", extra={field: value})
    if checkout:
        assert_sinks_safe(sinks, supplied)
    else:
        assert sinks.emitted()
        assert supplied in sinks.outputs()["record"]


@pytest.mark.parametrize(
    "supplied, directive", [("s", "%s"), ("r", "%r"), ("%s", "%s"), ("a", "%s")]
)
def test_redaction_preserves_percent_formatting(supplied, directive):
    configure_structlog(cache_logger_on_first_use=False)
    route = f"/agentcc/managed-credits/checkout/{supplied}/"
    with capture("new.formatting.consumer") as (logger, sinks):
        logger.error("GET " + directive, route)
    outputs = sinks.outputs()
    assert sinks.structured(), "a production line is not one JSON document"
    for name in ("record", "console-production", "file-production", "console-local"):
        assert "[redacted]" in outputs[name], name
        assert route not in outputs[name], name


@pytest.mark.parametrize("dispatch", ["handle", "format"])
def test_direct_handler_and_formatter_boundaries(dispatch):
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-direct-sink-681"
    record = logging.LogRecord(
        "new.direct.consumer",
        logging.ERROR,
        __file__,
        1,
        "GET %s",
        (f"/agentcc/managed-credits/checkout/{supplied}",),
        None,
    )
    with capture("new.direct.consumer") as (_, sinks):
        if dispatch == "handle":
            sinks.handle(record)
        else:
            # A caller's own formatting first (not a sink), then each sink.
            logging.Formatter().format(record)
            sinks.emit(record)
    assert_sinks_safe(sinks, supplied)


def test_structured_event_outside_request_is_sanitized_at_every_sink():
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-structured-sink-681"
    with capture("new.structured.consumer") as (_, sinks):
        structlog.get_logger("new.structured.consumer").error(
            "late",
            url=f"/agentcc/managed-credits/checkout/{supplied}/",
            nested=[supplied],
        )
    assert_sinks_safe(sinks, supplied)


@pytest.mark.parametrize("encoded", [False, True])
def test_sentry_events_transactions_and_breadcrumbs_are_value_free(
    monkeypatch, encoded
):
    from tfc.logging import sentry

    supplied = "synthetic-sentry-sink-681"
    path = f"/agentcc/managed-credits/checkout/{supplied}/"
    url = "http://localhost" + (quote(path, safe="") if encoded else path)
    options = {}
    monkeypatch.setattr(sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(sentry, "SENTRY_DSN", "http://synthetic@127.0.0.1/1")
    monkeypatch.setattr(sentry, "_initialized_components", set())
    import sentry_sdk

    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: options.update(kwargs))
    monkeypatch.setattr(sentry_sdk, "set_tags", lambda *args: None)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *args: None)
    assert sentry.init_sentry("django")
    event = {
        "request": {"url": url},
        "message": supplied,
        "exception": {
            "values": [{"stacktrace": {"frames": [{"vars": {"session_id": supplied}}]}}]
        },
        "breadcrumbs": {"values": [{"data": {"url": url}, "message": supplied}]},
    }
    import copy

    for name in ("before_send", "before_send_transaction"):
        result = options[name](copy.deepcopy(event), {})
        assert result is not None
        assert_safe([result], supplied)
    assert "before_breadcrumb" in options
    result = options["before_breadcrumb"](
        {"data": {"url": url}, "message": supplied}, {}
    )
    assert_safe([result], supplied)
    ordinary = {"request": {"url": f"http://localhost/ordinary/{supplied}/"}}
    assert options["before_send"](copy.deepcopy(ordinary), {}) == ordinary


@pytest.mark.parametrize("section", ["exception", "threads"])
def test_sentry_checkout_correlation_cannot_hide_secret_field_names(section):
    from tfc.logging import sentry

    # Deliberately disjoint: replacing the correlated segment cannot happen to
    # redact this synthetic credential as a side effect.
    canary = "synthetic-opaque-708j-canary"
    segment = "key"
    assert segment not in canary
    event = {
        "logger": "django.request",
        "message": "unexpected failure",
        "extra": {
            "checkout_url": f"/agentcc/managed-credits/checkout/{segment}/",
            "api_key": canary,
            "count": 7,
        },
        section: {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "agentcc.services.auth_bridge",
                                "vars": {
                                    "raw_key": canary,
                                    "result": {"key": canary, "id": "retained"},
                                    "count": 7,
                                },
                            }
                        ]
                    }
                }
            ]
        },
    }
    result = sentry._get_before_send()(event, {})
    assert result is not None
    assert canary not in str(result), "synthetic credential reached the Sentry hook"
    frame = result[section]["values"][0]["stacktrace"]["frames"][0]
    assert frame["vars"] == {
        "raw_[redacted]": sentry._REDACTED,
        "result": {"[redacted]": sentry._REDACTED, "id": "retained"},
        "count": 7,
    }
    assert result["extra"] == {
        "checkout_url": "/agentcc/managed-credits/checkout/[redacted]/",
        "api_[redacted]": sentry._REDACTED,
        "count": 7,
    }
    assert result["message"] == "unexpected failure"


def test_sentry_checkout_keeps_correlation_from_scrubbed_sensitive_fields():
    from tfc.logging import sentry

    supplied = "synthetic-removed-correlation-708j"
    event = {
        "message": "checkout failed " + supplied,
        "extra": {
            "api_key": f"/agentcc/managed-credits/checkout/{supplied}/",
            "count": 7,
        },
    }
    result = sentry._get_before_send()(event, {})
    assert result is not None
    assert supplied not in str(result), "checkout correlation was lost during scrubbing"
    assert result["message"] == "checkout failed [redacted]"
    assert result["extra"] == {"api_key": sentry._REDACTED, "count": 7}


@pytest.mark.parametrize(
    "path", ["/agentcc/mcp/credentials/", "/telemetry/registration/"]
)
def test_sentry_checkout_keeps_mcp_and_telemetry_body_removal(path):
    from tfc.logging import sentry

    canary = "synthetic-opaque-708j-canary"
    event = {
        "message": "unexpected failure",
        "request": {"url": "https://example.test" + path, "data": {"value": canary}},
        "extra": {"checkout_url": "/agentcc/managed-credits/checkout/key/"},
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"module": "django.request", "vars": {"raw": canary}}
                        ]
                    }
                }
            ]
        },
    }
    result = sentry._get_before_send()(event, {})
    assert result is not None
    assert canary not in str(result), "sensitive request data reached the Sentry hook"
    assert result["request"] == {"url": "https://example.test" + path}
    assert result["exception"]["values"][0]["stacktrace"]["frames"] == [
        {"module": "django.request"}
    ]
    assert result["extra"]["checkout_url"] == (
        "/agentcc/managed-credits/checkout/[redacted]/"
    )


@pytest.fixture
def sentry_hooks_without_checkout_scope(monkeypatch):
    import sentry_sdk

    from tfc.logging import checkout, sentry

    options = {}
    monkeypatch.setattr(sentry, "SENTRY_ENABLED", True)
    monkeypatch.setattr(sentry, "SENTRY_DSN", "http://synthetic@127.0.0.1/1")
    monkeypatch.setattr(sentry, "_initialized_components", set())
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: options.update(kwargs))
    monkeypatch.setattr(sentry_sdk, "set_tags", lambda *args: None)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *args: None)
    token = checkout._values.set(())
    try:
        assert sentry.init_sentry("django")
        yield options
    finally:
        checkout._values.reset(token)


@pytest.mark.parametrize("hook", ["before_send", "before_send_transaction"])
@pytest.mark.parametrize(
    "path, carrier",
    [
        ("/agentcc/mcp/credentials/", "request"),
        ("/agentcc/mcp/credentials/", "exception"),
        ("/agentcc/mcp/credentials/", "threads"),
        ("/agentcc/connectors/apps/", "request"),
        ("/agentcc/connectors/apps/", "exception"),
        ("/agentcc/connectors/apps/", "threads"),
        ("/agentcc/mcp/probe/", "request"),
        ("/agentcc/mcp/probe/", "exception"),
        ("/agentcc/mcp/probe/", "threads"),
        ("/telemetry/registration/", "request"),
        ("/telemetry/registration/", "exception"),
    ],
)
def test_sentry_checkout_keeps_correlation_from_removed_request_or_locals(
    sentry_hooks_without_checkout_scope, hook, path, carrier
):
    from tfc.logging.checkout import find_checkout_values

    supplied = "synthetic-mcp-correlation-708j"
    canary = "synthetic-opaque-708j-canary"
    assert not supplied.startswith("cs_")  # Shape redaction cannot mask this bug.
    assert supplied not in canary
    sections = (
        ("exception", "threads") if path.startswith("/agentcc/") else ("exception",)
    )
    event = {
        "message": "unexpected failure",
        "request": {"url": "https://example.test" + path, "data": {"value": canary}},
        "extra": {"note": supplied, "count": 7},
    }
    for section in sections:
        event[section] = {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"module": "django.request", "vars": {"raw": canary}}
                        ]
                    }
                }
            ]
        }
    if carrier == "request":
        removed_carrier = event["request"]["data"]
    else:
        removed_carrier = event[carrier]["values"][0]["stacktrace"]["frames"][0]["vars"]
    # The ONLY URL carrier is in data/locals which the base must delete. The
    # surviving echo is deliberately bare and there is no active checkout scope.
    removed_carrier["checkout_url"] = f"/agentcc/managed-credits/checkout/{supplied}/"
    assert find_checkout_values(event) == (supplied,)

    send = sentry_hooks_without_checkout_scope[hook]
    result = send(event, {})
    assert result is not None
    assert result["extra"] == {"note": "[redacted]", "count": 7}
    assert supplied not in str(result), "removed URL carrier lost checkout correlation"
    assert canary not in str(result), "sensitive body or locals reached the Sentry hook"
    assert result["request"] == {"url": "https://example.test" + path}
    for section in sections:
        assert result[section]["values"][0]["stacktrace"]["frames"] == [
            {"module": "django.request"}
        ]
    assert result["message"] == "unexpected failure"
    # A snapshot belongs to one event; it must not create a new ambient scope.
    ordinary = {"extra": {"note": supplied, "count": 7}}
    assert send(ordinary, {}) == ordinary


@pytest.mark.parametrize("hook", ["before_send", "before_send_transaction"])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    "body_key", ["body", "data", "http.request.body", "request_body"]
)
def test_sentry_checkout_keeps_correlation_from_removed_telemetry_breadcrumb(
    sentry_hooks_without_checkout_scope, hook, wrapped, body_key
):
    from tfc.logging.checkout import find_checkout_values

    supplied = "synthetic-breadcrumb-correlation-708j"
    canary = "synthetic-opaque-708j-canary"
    assert not supplied.startswith("cs_")
    assert supplied not in canary
    data = {
        "url": "https://example.test/telemetry/registration/",
        "method": "POST",
        body_key: {
            "checkout_url": f"/agentcc/managed-credits/checkout/{supplied}/",
            "value": canary,
        },
    }
    breadcrumbs = [{"category": "http", "data": data}]
    event = {
        "message": "unexpected failure",
        "request": {"url": "https://example.test/ordinary/"},
        "extra": {"note": supplied, "count": 7},
        "breadcrumbs": {"values": breadcrumbs} if wrapped else breadcrumbs,
    }
    assert find_checkout_values(event) == (supplied,)
    send = sentry_hooks_without_checkout_scope[hook]
    result = send(event, {})
    assert result is not None
    assert result["extra"] == {"note": "[redacted]", "count": 7}
    assert supplied not in str(result), "removed breadcrumb lost checkout correlation"
    assert canary not in str(result), "telemetry body reached the Sentry hook"
    kept = result["breadcrumbs"]["values"] if wrapped else result["breadcrumbs"]
    assert kept == [
        {
            "category": "http",
            "data": {
                "url": "https://example.test/telemetry/registration/",
                "method": "POST",
            },
        }
    ]
    assert result["request"] == {"url": "https://example.test/ordinary/"}
    assert result["message"] == "unexpected failure"
    ordinary = {"extra": {"note": supplied, "count": 7}}
    assert send(ordinary, {}) == ordinary


@pytest.mark.parametrize("chained", [False, True])
def test_late_exception_without_request_or_context(chained):
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-exception-sink-681"
    with capture("new.exception.consumer") as (logger, sinks):
        try:
            try:
                raise ValueError(f"/agentcc/managed-credits/checkout/{supplied}/")
            except ValueError as cause:
                if chained:
                    raise RuntimeError("late wrapper") from cause
                raise
        except (ValueError, RuntimeError):
            logger.exception("late failure")
    assert_sinks_safe(sinks, supplied)


def test_deep_and_cyclic_metadata_is_sanitized():
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-deep-sink-681"
    metadata = [f"/agentcc/managed-credits/checkout/{supplied}/"]
    metadata.append(metadata)
    for _ in range(12):
        metadata = [metadata]
    with capture("new.deep.consumer") as (logger, sinks):
        logger.error("late", extra={"metadata": metadata, "echo": supplied})
    assert_sinks_safe(sinks, supplied)


def test_configured_structured_formatter_on_direct_handler(tmp_path):
    from tfc.logging.config import get_logging_config

    supplied = "synthetic-formatter-681"
    configure_structlog(cache_logger_on_first_use=False)
    config = get_logging_config(str(tmp_path))["formatters"]["structured"]
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=config["processor"],
            foreign_pre_chain=config["foreign_pre_chain"],
        )
    )
    handler.handle(
        logging.LogRecord(
            "late",
            logging.ERROR,
            __file__,
            1,
            "%s",
            (f"/agentcc/managed-credits/checkout/{supplied}",),
            None,
        )
    )
    assert_safe([stream.getvalue()], supplied)
