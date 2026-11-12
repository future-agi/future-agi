"""Configured diagnostic outputs preserve structure and normal evaluation."""

import io
import json
import logging
import logging.config
import sys
from urllib.parse import unquote

import pytest

from tfc.logging.checkout import _values
from tfc.logging.checkout_output import scrub_rendered
from tfc.logging.config import configure_structlog, get_logging_config

pytestmark = pytest.mark.integration
VALUE = "synthetic-output-segment"
PREFIX = "/agentcc/managed-credits/checkout/"


def configured_handlers(tmp_path, monkeypatch, environment):
    monkeypatch.setenv("ENV_TYPE", environment)
    configure_structlog(cache_logger_on_first_use=False)
    config = get_logging_config(str(tmp_path))
    configurator = logging.config.DictConfigurator(config)
    for name in config["formatters"]:
        configurator.config["formatters"][name] = configurator.configure_formatter(
            configurator.config["formatters"][name]
        )
    for name in config["filters"]:
        configurator.config["filters"][name] = configurator.configure_filter(
            configurator.config["filters"][name]
        )
    return [
        configurator.configure_handler(configurator.config["handlers"][name])
        for name in config["handlers"]
    ]


@pytest.mark.parametrize("environment", ["production", "local"])
@pytest.mark.parametrize("failure", ["format", "write"])
@pytest.mark.parametrize("checkout", [True, False])
def test_configured_error_output(tmp_path, monkeypatch, environment, failure, checkout):
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)
    monkeypatch.setattr(logging, "raiseExceptions", True)
    path = (PREFIX if checkout else "/ordinary/") + VALUE + "/"

    class Unwritable(io.StringIO):
        def write(self, text):
            raise OSError(path)

    for handler in configured_handlers(tmp_path, monkeypatch, environment):
        try:
            if failure == "write":
                handler.setStream(Unwritable())
            args = (path, "surplus") if failure == "format" else (path,)
            record = logging.LogRecord(
                "output", 40, __file__, 1, "target %s", args, None
            )
            handler.emit(record)
            assert record.args is args
        finally:
            handler.close()
    text = errors.getvalue()
    assert "Logging error" in text
    assert (VALUE not in text) if checkout else (VALUE in text)


@pytest.mark.parametrize("segment", ['"', "\\", "{", "}", ",", ":", "[", "]", "a"])
def test_scoped_values_preserve_json_structure(segment):
    original = {"event": "unrelated", "target": PREFIX + segment + "/", "count": 1}
    token = _values.set((segment,))
    try:
        result = json.loads(scrub_rendered(json.dumps(original)))
        ordinary = json.loads(
            scrub_rendered(json.dumps({"event": "ordinary", "count": 1}))
        )
    finally:
        _values.reset(token)
    assert result["count"] == 1
    assert ordinary == {"event": "ordinary", "count": 1}
    assert result["target"] == PREFIX + "[redacted]/"


@pytest.mark.parametrize("scoped", [True, False])
@pytest.mark.parametrize(
    "segment", ["12345678", "1e12345678", "synthetic.quoted.segment"]
)
def test_numeric_and_punctuated_segments_preserve_json_values(scoped, segment):
    original = {
        "event": PREFIX + segment + "/",
        "session": "target " + segment,
        "count": 12345678,
    }
    token = _values.set((segment,) if scoped else ())
    try:
        result = json.loads(scrub_rendered(json.dumps(original)))
    finally:
        _values.reset(token)
    assert result["count"] == original["count"]
    assert result["session"] == "target [redacted]"
    assert result["event"] == PREFIX + "[redacted]/"


@pytest.mark.parametrize(
    "first", ["synthetic.dotted.segment", "synthetic~tilde~segment"]
)
def test_multiple_different_session_spellings(first):
    text = json.dumps({"event": PREFIX + first + "/ then " + PREFIX + VALUE + "/"})
    result = scrub_rendered(text)
    assert first not in result
    assert VALUE not in result
    assert json.loads(result)["event"].count("[redacted]") == 2


@pytest.mark.parametrize("environment", ["production", "local"])
@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.parametrize("separators", [False, True])
def test_encoded_prefix_and_segment(
    tmp_path, monkeypatch, environment, depth, separators
):
    path = (PREFIX + VALUE + "/").replace("checkout", "checkou%74")
    path = path.replace(VALUE, "%73" + VALUE[1:])
    if separators:
        path = path.replace("/", "%2f")
    for _ in range(depth - 1):
        path = path.replace("%", "%25")
    for handler in configured_handlers(tmp_path, monkeypatch, environment):
        output = io.StringIO()
        handler.setStream(output)
        try:
            handler.emit(logging.LogRecord("output", 40, __file__, 1, path, (), None))
            text = output.getvalue()
            if environment == "production":
                json.loads(text)
            for _ in range(depth):
                text = unquote(text)
            assert VALUE not in text
        finally:
            handler.close()


def test_rollover_sizing_preserves_native_length_without_emitting(
    tmp_path, monkeypatch
):
    import logging.handlers

    from tfc.logging import checkout_output

    configure_structlog(cache_logger_on_first_use=False)
    path = tmp_path / "sizing.log"
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=1000, backupCount=1)
    handler.setFormatter(logging.Formatter())
    target = PREFIX + VALUE + "/"
    handler.stream.write("seed\n")
    handler.flush()
    handler.maxBytes = len("seed\n") + len(target)
    calls = []
    original = checkout_output.scrub_rendered

    def observe(output, **kwargs):
        calls.append(output)
        return original(output, **kwargs)

    monkeypatch.setattr(checkout_output, "scrub_rendered", observe)
    record = logging.LogRecord("output", 40, __file__, 1, target, (), None)
    try:
        assert handler.shouldRollover(record)
        assert calls == []
        assert path.read_text() == "seed\n"
        handler.emit(record)
        handler.flush()
        assert len(calls) == 1
        assert VALUE not in path.read_text()
        assert record.msg == target
    finally:
        handler.close()


def test_nested_emit_during_rollover_still_uses_output_boundary(tmp_path):
    import logging.handlers

    configure_structlog(cache_logger_on_first_use=False)
    output = io.StringIO()
    nested = logging.StreamHandler(output)
    nested.setFormatter(logging.Formatter())
    target = PREFIX + VALUE + "/"

    class Formatter(logging.Formatter):
        def format(self, record):
            nested.emit(record)
            return super().format(record)

    path = tmp_path / "nested.log"
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=1000, backupCount=1)
    handler.setFormatter(Formatter())
    handler.stream.write("seed\n")
    handler.flush()
    try:
        handler.emit(logging.LogRecord("output", 40, __file__, 1, target, (), None))
        handler.flush()
        assert output.getvalue()
        assert VALUE not in output.getvalue() + path.read_text()
    finally:
        handler.close()
        nested.close()


@pytest.mark.parametrize("environment", ["production", "local"])
def test_error_output_evaluates_once_per_native_use(tmp_path, monkeypatch, environment):
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)
    monkeypatch.setattr(logging, "raiseExceptions", True)
    calls = []

    class Message:
        def __str__(self):
            calls.append("str")
            return "target %s"

        def __repr__(self):
            calls.append("repr")
            return PREFIX + VALUE + "/"

    # A failed format calls str(msg), then handleError calls repr(msg). No
    # discovery pass may add an evaluation or read the original again.
    for handler in configured_handlers(tmp_path, monkeypatch, environment):
        try:
            before = len(calls)
            record = logging.LogRecord(
                "output", 40, __file__, 1, Message(), (1, 2), None
            )
            handler.emit(record)
            assert calls[before:] == ["str", "repr"]
        finally:
            handler.close()
    assert VALUE not in errors.getvalue()
