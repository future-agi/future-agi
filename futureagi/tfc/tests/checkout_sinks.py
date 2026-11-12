"""Observe what the configured log sinks emit (test helper, not a test module).

No checkout session value may reach a sink the product
configures; raw in-memory LogRecords are not a sink. Converted inventory tests
dispatch their original scenario (logger, handler or formatter boundary) to
the sinks below and assert on the emitted output instead of on the record:

- "record": a stdlib StreamHandler whose formatter renders the surfaces the
  former first-consumer handlers read (the formatted message, str(vars(record))
  and str() of a "payload" extra), written through the StreamHandler emit
  boundary; a test may add further formatter sinks (extra=...);
- "console-production" and "file-production": the configured console handler
  and RotatingFileHandler with the production JSON renderer;
- "console-local": the configured console handler with the local renderer;
- "otlp": the OTLP LoggingHandler with the configured HTTP log exporter; the
  output is the final request bytes the exporter would send (checked for
  leaks). The SDK drops a record whose attributes it cannot encode, and its
  attribute cleaner recurses without bound into self-similar values (a
  UserString, a cyclic list) with or without the boundary; such a failure is
  recorded on the sink (sink.failures) instead of failing the scenario.

saml_console is not attached: it serves only the saml2 and xmlschema loggers,
and its own filter replaces every message before rendering.
A "recorder" handler keeps the delivered records (no rendering) for the
controls that assert records are delivered unchanged; it is not a sink.
"""

import io
import json
import logging
import logging.config
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from tfc.logging.config import configure_structlog, get_logging_config

PRODUCTION_SINKS = ("console-production", "file-production")


class RecordFormatter(logging.Formatter):
    """The surfaces a raw first consumer read, rendered as one entry."""

    def format(self, record):
        rows = [str(vars(record)), super().format(record)]
        if "payload" in vars(record):
            rows.append(str(vars(record)["payload"]))
        return " ".join(rows)


class Recorder(logging.Handler):
    """Keeps delivered records without rendering them (not a sink)."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _configured(directory, environment):
    previous = os.environ.get("ENV_TYPE")
    os.environ["ENV_TYPE"] = environment
    try:
        configure_structlog(cache_logger_on_first_use=False)
        config = get_logging_config(str(directory))
        built = logging.config.DictConfigurator(config)
        for name in config["formatters"]:
            built.config["formatters"][name] = built.configure_formatter(
                built.config["formatters"][name]
            )
        for name in config["filters"]:
            built.config["filters"][name] = built.configure_filter(
                built.config["filters"][name]
            )
        console = built.configure_handler(built.config["handlers"]["console"])
        file_handler = built.configure_handler(built.config["handlers"]["file"])
    finally:
        if previous is None:
            os.environ.pop("ENV_TYPE", None)
        else:
            os.environ["ENV_TYPE"] = previous
    return console, file_handler, directory / "logs" / "wsgi.log"


def _otlp():
    from opentelemetry.exporter.otlp.proto.http import Compression
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor

    from tfc.telemetry.checkout_output import log_exporter

    exporter = log_exporter(
        "http/protobuf",
        endpoint="http://127.0.0.1:9/v1/logs",
        compression=Compression.NoCompression,
    )
    payloads = []

    class Response:
        ok = True
        status_code = 200
        reason = "OK"
        text = ""
        content = b""

    def post(*args, **kwargs):
        payloads.append(kwargs["data"])
        return Response()

    exporter._session.post = post
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))

    class RecordedLoggingHandler(LoggingHandler):
        failures = []

        def emit(self, record):
            try:
                super().emit(record)
            except Exception as error:  # an SDK failure: nothing is exported
                self.failures.append(type(error).__name__)

    handler = RecordedLoggingHandler(logger_provider=provider)
    handler.failures = []
    return handler, provider, payloads


class Sinks:
    """The attached sinks; dispatch helpers mirror the original boundaries."""

    def __init__(self, *, otlp=True, extra=None):
        self._directory = Path(tempfile.mkdtemp(prefix="checkout-sinks-"))
        self.recorder = Recorder()
        self.streams, self.files, self._providers = {}, {}, []
        self.handlers = {}
        for name, formatter in {"record": RecordFormatter(), **(extra or {})}.items():
            handler = logging.StreamHandler(io.StringIO())
            handler.setFormatter(formatter)
            self.handlers[name] = handler
            self.streams[name] = handler.stream
        for environment in ("production", "local"):
            console, file_handler, path = _configured(
                self._directory / environment, environment
            )
            console.setStream(io.StringIO())
            self.handlers["console-" + environment] = console
            self.streams["console-" + environment] = console.stream
            if environment == "production":
                self.handlers["file-production"] = file_handler
                self.files["file-production"] = (file_handler, path)
            else:
                file_handler.close()
        self.payloads = None
        self._final = None
        if otlp:
            handler, provider, self.payloads = _otlp()
            self.handlers["otlp"] = handler
            self._providers.append(provider)

    @property
    def records(self):
        return self.recorder.records

    def all_handlers(self):
        return [self.recorder, *self.handlers.values()]

    def handle(self, record):
        """The handler boundary: each sink's Handler.handle."""
        for handler in self.all_handlers():
            handler.handle(record)

    def emit(self, record):
        """Direct delivery to each sink's emit (no handler filters)."""
        for handler in self.all_handlers():
            handler.emit(record)

    def outputs(self):
        """What each sink emitted, by sink name (kept after close)."""
        if self._final is not None:
            return dict(self._final)
        result = {name: stream.getvalue() for name, stream in self.streams.items()}
        for name, (handler, path) in self.files.items():
            handler.flush()
            result[name] = path.read_text() if path.exists() else ""
        if self.payloads is not None:
            result["otlp"] = b"".join(self.payloads).decode("utf-8", "replace")
        return result

    def text(self):
        return "\n".join(self.outputs().values())

    def emitted(self):
        """True when every text sink wrote output.

        The OTLP sink is checked by leaks() only: the SDK legitimately drops a
        record whose attribute values it cannot encode.
        """
        outputs = self.outputs()
        return all(outputs[name] for name in self.streams) and all(
            outputs[name] for name in self.files
        )

    def leaks(self, value):
        """The sinks whose emitted output contains value (empty when none)."""
        encoded = value.encode() if isinstance(value, str) else value
        names = [
            name
            for name, text in self.outputs().items()
            if name != "otlp" and value in text
        ]
        if self.payloads is not None and encoded in b"".join(self.payloads):
            names.append("otlp")
        return names

    def structured(self):
        """True when every production line is one parseable JSON document."""
        outputs = self.outputs()
        for name in PRODUCTION_SINKS:
            for line in outputs[name].splitlines():
                if line.strip():
                    json.loads(line)
        return True

    def close(self):
        if self._final is not None:
            return
        self._final = self.outputs()
        for handler in self.handlers.values():
            handler.close()
        for provider in self._providers:
            provider.shutdown()
        shutil.rmtree(self._directory, ignore_errors=True)


@contextmanager
def configured_sinks(
    name, *, otlp=True, extra=None
) -> Iterator[tuple[logging.Logger, Sinks]]:
    """A non-propagating logger whose handlers are the configured sinks."""
    source = logging.getLogger(name)
    saved = (source.handlers[:], source.propagate, source.level, source.disabled)
    sinks = Sinks(otlp=otlp, extra=extra)
    source.handlers = sinks.all_handlers()
    source.propagate = False
    source.disabled = False
    source.setLevel(logging.DEBUG)
    try:
        yield source, sinks
    finally:
        source.handlers, source.propagate, level, source.disabled = saved
        source.setLevel(level)
        sinks.close()


def dispatch(source, sinks, record, boundary):
    """Deliver record through the scenario's original boundary."""
    if boundary == "logger":
        source.handle(record)
    elif boundary == "handler":
        sinks.handle(record)
    else:
        # The scenario formats the record first (a caller's own formatter,
        # not a sink), then the sinks emit it.
        logging.Formatter().format(record)
        sinks.emit(record)


@contextmanager
def native_emit():
    """Bypass the emit boundary (test-only), for evaluation-parity checks.

    Configure first, then keep subsequent sink configuration from reinstalling
    the boundary. Other configuration (formatters, request filters and OTLP)
    stays the same, so only the stream emit implementation differs. Nesting
    and exceptional exits restore the implementation that was active on entry.
    """
    configure_structlog(cache_logger_on_first_use=False)
    boundary = logging.StreamHandler.emit
    with patch("tfc.logging.checkout_output.install_output_boundaries"):
        logging.StreamHandler.emit = getattr(boundary, "__wrapped__", boundary)
        try:
            yield
        finally:
            logging.StreamHandler.emit = boundary
