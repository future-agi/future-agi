"""Access-log arguments stay correlated after the request scope has ended.

Each configured handler is held to its own contract. The handlers the
access loggers are routed to under the application's LOGGING configuration
(console and file) redact a checkout access line and leave any other line
exactly as rendered. saml_console replaces every message it receives with a
fixed stand-in by design, so whatever reaches it is value-free and carries
that stand-in.
"""

import io
import json
import logging
import logging.config
import sys
import time
from enum import IntEnum

import pytest

from tfc.logging.checkout import _values
from tfc.logging.checkout_encoding import decoded_levels
from tfc.logging.checkout_requests import AccessPathFilter
from tfc.logging.config import configure_structlog, get_logging_config
from tfc.tests.test_checkout_request_path_source import delivered

pytestmark = pytest.mark.integration
PREFIX = "/agentcc/managed-credits/checkout/"
VALUE = "synthetic-access-correlation-session"
SAML_STAND_IN = "saml dependency event"


def named_handlers(tmp_path, monkeypatch, environment):
    """The configuration and each configured handler, by its configured name."""
    monkeypatch.setenv("ENV_TYPE", environment)
    configure_structlog(cache_logger_on_first_use=False)
    config = get_logging_config(str(tmp_path))
    configurator = logging.config.DictConfigurator(config)
    for kind in ("formatters", "filters"):
        configure = getattr(configurator, "configure_" + kind[:-1])
        for name in config[kind]:
            configurator.config[kind][name] = configure(configurator.config[kind][name])
    handlers = {
        name: configurator.configure_handler(configurator.config["handlers"][name])
        for name in config["handlers"]
    }
    return config, handlers


def routed(config, name):
    """Handler names a record of logger `name` reaches under dictConfig rules."""
    names, current = [], name
    while True:
        entry = config["loggers"].get(current)
        if entry is not None:
            names.extend(entry.get("handlers", ()))
            if not entry.get("propagate", True):
                return names
        if not current:
            return names
        current = current.rpartition(".")[0]


def assert_value_free(text):
    for level in decoded_levels(text):
        assert VALUE not in level, text


def request_path(checkout, encoding):
    prefix = PREFIX if checkout else "/ordinary/"
    path = prefix + VALUE + "/"
    if encoding in ("single", "double"):
        path = path.replace("/", "%2f").replace("synthetic", "%73ynthetic")
        return path.replace("%", "%25") if encoding == "double" else path
    if encoding.startswith("json"):
        escaped_prefix = "".join(f"\\u{ord(char):04x}" for char in prefix)
        escaped_value = "".join(f"\\u{ord(char):04x}" for char in VALUE)
        return (
            (escaped_prefix if encoding != "json-value" else prefix)
            + (escaped_value if encoding != "json-prefix" else VALUE)
            + "/"
        )
    return path


def access_record(name, path):
    """Use the server logger's source filter, with no active request scope."""
    if name == "granian.access":
        from granian.log import DEFAULT_ACCESSLOG_FMT, log_request_builder

        request = {
            "addr_remote": "127.0.0.1",
            "path": path,
            "qs": "echo=" + VALUE,
            "method": "GET",
            "scheme": "http",
            "protocol": "HTTP/1.1",
            "headers": lambda name: None,
        }

        def emit():
            log_request_builder(DEFAULT_ACCESSLOG_FMT)(
                time.time(), time.perf_counter(), request, 200
            )

    else:

        def emit():
            logging.getLogger(name).info('"%s" echo=%s', "GET " + path, VALUE)

    logger = logging.getLogger(name)
    disabled = logger.disabled
    logger.disabled = False
    try:
        (record,) = delivered(name, emit)
    finally:
        logger.disabled = disabled
    return record


@pytest.mark.parametrize("environment", ["production", "local"])
@pytest.mark.parametrize("failure", ["none", "format", "write"])
@pytest.mark.parametrize("name", ["granian.access", "django.server"])
@pytest.mark.parametrize("checkout", [True, False])
@pytest.mark.parametrize(
    "encoding",
    ["literal", "single", "double", "json-prefix", "json-value", "json-both"],
)
def test_access_arguments_at_configured_outputs(
    tmp_path, monkeypatch, environment, failure, name, checkout, encoding
):
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)
    monkeypatch.setattr(logging, "raiseExceptions", True)

    class FailFormat(logging.Formatter):
        def format(self, record):
            raise ValueError("synthetic formatter failure")

    class FailWrite(io.StringIO):
        def write(self, text):
            raise OSError("synthetic stream failure")

    # Access logging occurs after middleware has unwound. Keep the scope empty
    # for both source redaction and the later handler/error output boundary.
    token = _values.set(())
    handlers = {}
    try:
        config, handlers = named_handlers(tmp_path, monkeypatch, environment)
        # Both access loggers reach the console and file handlers, never the
        # SAML dependency handler.
        assert routed(config, name) == ["console", "file"]
        assert set(handlers) == {"console", "file", "saml_console"}
        for handler_name, handler in handlers.items():
            # A fresh record per handler: a handler's filter may rewrite it.
            record = access_record(name, request_path(checkout, encoding))
            # The text a native rendering would show, before any handler.
            native = "Message: %r\nArguments: %s\n" % (record.msg, record.args)  # noqa: UP031
            message = record.getMessage()
            output = io.StringIO()
            handler.setStream(FailWrite() if failure == "write" else output)
            if failure == "format":
                handler.setFormatter(FailFormat())
            errors.seek(0)
            errors.truncate()
            handler.handle(record)
            text = output.getvalue() + errors.getvalue()
            assert text, (name, handler_name, failure)
            if handler_name == "saml_console":
                assert_value_free(text)
                assert "[redacted]" not in text
                if failure == "none":
                    rendered = output.getvalue()
                    assert SAML_STAND_IN in rendered
                    if environment == "production":
                        assert json.loads(rendered)["event"] == SAML_STAND_IN
                else:
                    assert (
                        f"Message: {SAML_STAND_IN!r}\nArguments: ()\n"
                        in errors.getvalue()
                    )
            elif checkout:
                assert_value_free(text)
                assert "[redacted]" in text
            elif failure == "none":
                # Unchanged: the rendered event is the record's own message.
                rendered = output.getvalue()
                if environment == "production":
                    assert json.loads(rendered)["event"] == message
                else:
                    assert message in rendered
            else:
                assert native in errors.getvalue()
            if failure != "none":
                assert "--- Logging error ---" in errors.getvalue()
                assert "Arguments:" in errors.getvalue()
            elif environment == "production":
                json.loads(output.getvalue())
    finally:
        for handler in handlers.values():
            handler.close()
        _values.reset(token)


@pytest.mark.parametrize("mapping", [True, False])
def test_access_source_preserves_nontext_arguments_without_evaluation(mapping):
    calls = []

    class Carrier:
        def __str__(self):
            calls.append("str")
            return VALUE

        def __repr__(self):
            calls.append("repr")
            return VALUE

        def read(self):
            calls.append("read")
            return VALUE

    class Number(int):
        def __str__(self):
            calls.append("number-str")
            return VALUE

        def __repr__(self):
            calls.append("number-repr")
            return VALUE

    class Status(IntEnum):
        OK = 200

    carrier, number, status = Carrier(), Number(logging.INFO), Status.OK
    original = (PREFIX + VALUE + "/", "echo=" + VALUE, carrier, number, status)
    args = dict(
        zip(
            ("path", "query_string", "carrier", "size", "status"), original, strict=True
        )
    )
    record = logging.LogRecord(
        "granian.access" if mapping else "django.server",
        number,
        __file__,
        1,
        "%s",
        (args,) if mapping else original,
        None,
    )
    assert AccessPathFilter().filter(record)
    assert calls == []
    assert record.levelno is number
    result = tuple(record.args.values()) if mapping else record.args
    assert result[:2] == (PREFIX + "[redacted]/", "echo=[redacted]")
    assert all(
        after is before for after, before in zip(result[2:], original[2:], strict=True)
    )
    assert original[:2] == (PREFIX + VALUE + "/", "echo=" + VALUE)
    assert args["path"] == original[0]
    assert args["query_string"] == original[1]
