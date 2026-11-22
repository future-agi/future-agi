"""
Centralized Sentry configuration for all backend components.

Keep Sentry and structlog separate.
Sentry SDK integrations handle error capture automatically.

Usage:
    from tfc.logging.sentry import init_sentry

    # In Django settings.py
    init_sentry(component="django")

    # In celery.py
    init_sentry(component="celery")

    # In Temporal worker startup
    init_sentry(component="temporal", tags={"queue": "tasks_l"})
"""

import json
import os
import re
from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

# Environment detection
ENV_TYPE = os.getenv("ENV_TYPE", "local")
IS_PRODUCTION = ENV_TYPE in ("prod", "production")
IS_STAGING = ENV_TYPE == "staging"

# Sentry is enabled in staging/prod by default. An explicit SENTRY_ENABLED env
# var (also read by settings.py) always wins so the two never diverge.
_sentry_enabled_override = os.getenv("SENTRY_ENABLED")
if _sentry_enabled_override not in (None, ""):
    SENTRY_ENABLED = _sentry_enabled_override.lower() == "true"
else:
    SENTRY_ENABLED = ENV_TYPE in ("staging", "prod", "production")
SENTRY_DSN = os.getenv("SENTRY_DSN")

# Noise control
# Loggers whose records must NEVER become Sentry issues. These are pure
# infrastructure/SDK loggers that emit ERROR records for transient, expected
# conditions (e.g. the OTel exporter failing to reach its collector). Left
# unchecked, a single unreachable collector produced tens of millions of
# identical events. Real application exceptions never originate here.
#
# NOTE: Sentry's LoggingIntegration patches logging.Logger.callHandlers, so a
# logger's `propagate=False`/`level` in Django's LOGGING dict does NOT stop it
# from becoming an event. ignore_logger() (below) and the before_send logger
# checks are the only reliable levers. ignore_logger() also discards the
# record's breadcrumb, so loggers whose records are still useful context
# (EXPECTED_OUTCOME_LOGGERS) are dropped in before_send only.
NOISY_LOGGER_PREFIXES = (
    "opentelemetry",  # OTLP exporter/instrumentation export failures
    "saml2",
    "xmlschema",
)

# Exact names of third-party loggers that report an outcome our code handles at
# ERROR. before_send drops every event built from a record of one of these
# loggers, whatever its message; child and similarly named loggers, and
# exceptions captured directly, are kept. The record stays a breadcrumb, so it
# is still context on any genuine event captured later in the same scope.
EXPECTED_OUTCOME_LOGGERS = frozenset(
    {
        # Django answers a request with an invalid Host header with 400 and logs
        # it here at ERROR. That is scanner/bot traffic, not a fault. The other
        # django.security.* loggers (SuspiciousOperation, SuspiciousFileOperation,
        # RequestDataTooBig, csrf, ...) stay captured.
        "django.security.DisallowedHost",
    }
)

# Exact logger names handed to sentry_sdk.integrations.logging.ignore_logger.
# Children are covered by the before_send prefix check as a backstop.
IGNORED_LOGGERS = (
    "opentelemetry",
    "opentelemetry.sdk.metrics._internal.export",
    "opentelemetry.sdk.trace.export",
    "opentelemetry.exporter.otlp.proto.grpc.exporter",
    "opentelemetry.exporter.otlp.proto.http.metric_exporter",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.exporter.otlp.proto.http._log_exporter",
    "saml2",
    "saml2.*",
    "xmlschema",
    "xmlschema.*",
)

# Suppress only log-only outcomes explicitly classified by their owning code.
# Exception events (including downstream 4xx and disconnects) are never classified
# from a generic exception name, status code, or message substring.
EXPECTED_LOG_OUTCOMES = frozenset(
    {
        (
            "ee.agenthub.explanation_agent.exp_agent",
            "explanation_summary_skipped",
            "insufficient_samples",
        ),
    }
)

# Keys whose values must be redacted before leaving the process. Matched
# case-insensitively as substrings against header/cookie/body/extra keys.
SENSITIVE_KEY_SUBSTRINGS = (
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "x-api-key",
    "access_key",
    "private_key",
    "csrf",
    # Form fields (SAMLResponse, RelayState) and the ACS view's locals for them.
    "samlresponse",
    "saml_response",
    "relaystate",
    "relay_state",
    "fai_saml_b",
    "candidate",
    # The AgentCC gateway header that carries an org's own provider key, such
    # as its TypeSafe key on a Jev eval (ee/jev/client.py). The scrubber below
    # matches whole names only, so it is listed in full.
    "x-agentcc-provider-key",
    # A rerun's changed credential values, and an agent version check's.
    "environment_values",
    "credential_values",
)
# LiteLLM caches by the full credential document, not by a digest. These
# aliases can hold a string or a tuple, neither of which has fields to scrub.
_CREDENTIAL_ALIASES = {
    "cache_credentials",
    "credential_cache_key",
    "resolved_cache_key",
}
_VERTEX_MODULE = "litellm.llms.vertex_ai.vertex_llm_base"
_VERTEX_LOAD_MESSAGE = (
    "Failed to load vertex credentials. Check to see if credentials "
    "containing partial/invalid information. Error: "
)
_VERTEX_SAFE_MESSAGE = "Vertex credential loading failed"
_REDACTED = "[Filtered]"

# Sample rates - configurable via env, lower in production to reduce costs
# Production: 10% sampling, Staging: 100% sampling
TRACES_SAMPLE_RATE = float(
    os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1" if IS_PRODUCTION else "1.0")
)
PROFILES_SAMPLE_RATE = float(
    os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1" if IS_PRODUCTION else "1.0")
)

_initialized_components: set = set()


# The SDK caps serialized strings at this size too. Hooks may also be called
# before serialization, so check sizes before scanning, copying or decoding.
_MAX_TEXT = 8192
_MAX_ITEMS = 128
_MAX_FRAMES = 512
_HTTP_METHODS = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"})
_INGESTION_FRAMES = (
    ("tracer.utils.trace_ingestion", "bulk_create_observation_span_task"),
    ("tracer.utils.otel", "bulk_convert_otel_spans_to_observation_spans"),
)
_HEADER_MEDIA_TYPES = frozenset(
    {
        "application/json", "application/x-protobuf", "application/grpc",
        "text/html", "text/plain", "*/*",
    }
)
_SPAN_STATUSES = frozenset(
    "ok cancelled unknown_error invalid_argument deadline_exceeded not_found "
    "already_exists permission_denied resource_exhausted failed_precondition aborted "
    "out_of_range unimplemented internal_error unavailable data_loss "
    "unauthenticated".split()
)
_USAGE_FIELDS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "max_tokens",
        "token_count",
    }
)
_USAGE_ASSIGNMENT = r"(?<![\w.-])(?:" + "|".join(sorted(_USAGE_FIELDS)) + r")="
_USAGE_COUNT = re.compile(_USAGE_ASSIGNMENT + r"[0-9]{1,18}(?=$|[ )])")
# Match every percent sign so unsupported directives cannot shift argument indices.
_LOG_DIRECTIVE = re.compile(_USAGE_ASSIGNMENT + r"%d(?=$|[ )])|%[sd]|%")
_SENSITIVE_NAMES = (*SENSITIVE_KEY_SUBSTRINGS, *_CREDENTIAL_ALIASES)
_SENSITIVE_TEXT = (*_SENSITIVE_NAMES, "credential", "<saml:", "<samlp:")


def _is_sensitive_key(key: Any) -> bool:
    if type(key) is not str or len(key) > 128:
        return True
    lowered = key.lower()
    return any(s in lowered for s in _SENSITIVE_NAMES)


def _scrub_text(value: Any) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT:
        return _REDACTED
    probe = _USAGE_COUNT.sub("", value).lower()
    if any(s in probe for s in _SENSITIVE_TEXT):
        return _REDACTED
    return value


def _scrub(value: Any) -> Any:
    """Bound the whole walk, not just the width of each branch."""
    slots = 512
    characters = 32_768

    def visit(item: Any, depth: int) -> Any:
        nonlocal slots, characters
        if depth > 6:
            raise ValueError
        if type(item) is str:
            characters -= len(item)
            if characters < 0:
                raise ValueError
            return _scrub_text(item)
        if item is None or type(item) in (bool, float):
            return item
        if type(item) is int and item.bit_length() <= 64:
            return item
        if type(item) in (list, tuple, dict) and len(item) <= min(_MAX_ITEMS, slots):
            # Reserve all children, including opaque/redacted ones.
            slots -= len(item)
            if type(item) is dict:
                if any(
                    k in item for k in ("private_key", "client_secret", "refresh_token")
                ):
                    return _REDACTED
                result: dict[str, Any] = {}
                for key, val in item.items():
                    if type(key) is not str or len(key) > 128:
                        raise ValueError
                    if key in _USAGE_FIELDS and type(val) is int and 0 <= val < 2**63:
                        result[key] = val
                    elif _is_sensitive_key(key):
                        result[key] = _REDACTED
                    else:
                        result[key] = visit(val, depth + 1)
                return result
            return [visit(v, depth + 1) for v in item]
        raise ValueError

    try:
        return visit(value, 0)
    except ValueError:
        return _REDACTED


def _vertex_log_message(logger: object, message: object) -> bool:
    return (
        type(logger) is str
        and logger == "LiteLLM"
        and type(message) is str
        and message.startswith(
            (_VERTEX_LOAD_MESSAGE, "Failed to load vertex credentials: ")
        )
    )


def _vertex_auth_frame(module: object, function: object) -> bool:
    if type(module) is not str or type(function) is not str:
        return False
    return module == _VERTEX_MODULE and function in (
        "load_auth",
        "get_access_token",
        "get_access_token_async",
        "refresh_auth",
    )


def _is_timestamp(value: Any) -> bool:
    return type(value) is datetime or (
        type(value) is str
        and len(value) <= 40
        and re.fullmatch(r"[0-9T:.+Z-]+", value) is not None
    )


def _http_status_code(data: dict) -> int | None:
    status = data.get("status_code")
    return status if type(status) is int and 100 <= status <= 599 else None


def _is_grpc_framework_logger(name: Any) -> bool:
    return type(name) is str and (
        name == "django_socio_grpc" or name.startswith("django_socio_grpc.")
    )


def _grpc_status_name(value: Any) -> str | None:
    # Scope breadcrumbs see the live enum; serialized error extras and restored
    # breadcrumbs may contain its repr. Never render a supplied request/object.
    from grpc import StatusCode

    if type(value) is StatusCode:
        return value.name
    if type(value) is str and len(value) <= 96:
        for code in StatusCode:
            if value == code.name or value == repr(code):
                return code.name
    return None


def _vertex_breadcrumb(crumb: dict, hint: dict) -> bool:
    message = crumb.get("message")
    if type(message) is str and (
        message == _VERTEX_SAFE_MESSAGE
        or _vertex_log_message(crumb.get("category"), message)
    ):
        return True
    exc_info = getattr(hint.get("log_record"), "exc_info", None)
    if not isinstance(exc_info, tuple) or len(exc_info) != 3:
        return False
    tb = exc_info[2]
    for _ in range(_MAX_FRAMES):
        if not isinstance(tb, TracebackType):
            break
        frame = tb.tb_frame
        if _vertex_auth_frame(frame.f_globals.get("__name__"), frame.f_code.co_name):
            return True
        tb = tb.tb_next
    return False


def _before_breadcrumb(crumb: dict, hint: dict) -> dict | None:
    # Called at scope ingestion and again on retained/restored trails. Rebuild
    # from bounded fields without mutating the record shared with later events.
    data = crumb.get("data")
    grpc_logger = _is_grpc_framework_logger(crumb.get("category"))
    if grpc_logger and "data" in crumb and type(data) is not dict:
        return None
    result: dict[str, Any] = {}
    timestamp = crumb.get("timestamp")
    if _is_timestamp(timestamp):
        result["timestamp"] = timestamp
    for key in ("category", "type", "level"):
        if key in crumb:
            result[key] = _scrub_text(crumb[key])
    if _vertex_breadcrumb(crumb, hint):
        result["message"] = _VERTEX_SAFE_MESSAGE
        return result
    if type(data) is dict:
        fields: dict[str, Any] = {}
        method = data.get("method")
        if type(method) is str and len(method) <= 7 and method in _HTTP_METHODS:
            fields["method"] = method
        if grpc_logger:
            code = _grpc_status_name(data.get("status_code"))
            if code is not None:
                fields["status_code"] = code
                # Keep only the known framework summary for our Export RPC, not
                # arbitrary exception text or a path supplied by a caller.
                label = "OK" if code == "OK" else code.title().replace("_", "")
                summary = f"{label} : ObservationSpan/Export"
                message = crumb.get("message")
                if type(message) is str and message == summary:
                    result["message"] = summary
        else:
            status = _http_status_code(data)
            if status is not None:
                fields["status_code"] = status
        result["data"] = fields
    return result


def _entries(container: dict, key: str) -> list | tuple | None:
    items = container.get(key)
    if (
        items is None
        or type(items) not in (list, tuple)
        or len(items) > _MAX_ITEMS
        or not all(type(item) is dict for item in items)
    ):
        container.pop(key, None)
        return None
    return cast("list | tuple", items)


def _scrub_frames(event: dict) -> str | None:
    """Return a safe replacement for text from sensitive or incomplete stacks."""
    remaining = _MAX_FRAMES
    complete = True
    replacement: str | None = None

    def inspect_stack(owner: dict) -> None:
        nonlocal remaining, complete, replacement
        # Restored events can carry a second, unsymbolicated copy of the stack.
        # Do not traverse it or forward its locals outside the frame budget.
        if "raw_stacktrace" in owner:
            owner.pop("raw_stacktrace")
            complete = False
        if "stacktrace" not in owner:
            return
        stack = owner["stacktrace"]
        if type(stack) is not dict:
            owner.pop("stacktrace")
            complete = False
            return
        # Check width and the event-wide budget *before* touching any frame.
        # Charge malformed lists too; never collect an SDK frame iterator here.
        frames = stack.get("frames")
        if (
            frames is None
            or type(frames) not in (list, tuple)
            or len(frames) > min(_MAX_ITEMS, remaining)
        ):
            stack.pop("frames", None)
            complete = False
            return
        remaining -= len(frames)
        for frame in frames:
            if type(frame) is not dict:
                stack.pop("frames")
                complete = False
                return
            frame.pop("vars", None)
            module, function = frame.get("module"), frame.get("function")
            if _vertex_auth_frame(module, function):
                replacement = _VERTEX_SAFE_MESSAGE
            elif replacement is None and type(module) is str and type(function) is str:
                # Both the task logger and the Temporal interceptor capture this
                # traceback. The latter has no logger; neither needs its text.
                if (module, function) in _INGESTION_FRAMES:
                    replacement = _REDACTED

    inspect_stack(event)
    for section in ("exception", "threads"):
        if section not in event:
            continue
        container = event[section]
        if type(container) is not dict:
            raise ValueError("Malformed Sentry stack container")
        items = _entries(container, "values")
        if items is None:
            complete = False
            continue
        for item in items:
            inspect_stack(item)
    return replacement or (None if complete else _REDACTED)


def _span_structure(span: dict) -> dict:
    # Span descriptions, tags, data and exception events can contain copies
    # of an exception's text. The separate transaction has no exception hint
    # to identify those copies. Rebuild it from structural fields only.
    result: dict[str, Any] = {}
    for key, pattern in (
        ("trace_id", r"[0-9a-fA-F]{32}"),
        ("span_id", r"[0-9a-fA-F]{16}"),
        ("parent_span_id", r"[0-9a-fA-F]{16}"),
        ("op", r"[\w.-]+"),
        ("origin", r"[\w.-]+"),
    ):
        value = span.get(key)
        if type(value) is str and len(value) <= 128 and re.fullmatch(pattern, value):
            if not key.endswith("_id"):
                result[key] = _scrub_text(value)
            elif int(value, 16):
                result[key] = value
    # SDK child spans carry status as a tag; the root trace uses a field.
    for owner in (span, span.get("tags")):
        if type(owner) is dict:
            status = owner.get("status")
            if type(status) is str and len(status) <= 32 and status in _SPAN_STATUSES:
                if owner is span:
                    result["status"] = status
                else:
                    result["tags"] = {"status": status}
    for key in ("start_timestamp", "timestamp"):
        value = span.get(key)
        if (
            _is_timestamp(value)
            or type(value) is float
            or (type(value) is int and value.bit_length() <= 64)
        ):
            result[key] = value
    # The SDK moves these identifiers to the envelope header after the hook.
    sampling = span.get("dynamic_sampling_context")
    if type(sampling) is dict:
        dsc = {}
        for key, pattern in (
            ("trace_id", r"[0-9a-fA-F]{32}"),
            ("public_key", r"[0-9a-fA-F]{32}"),
            ("org_id", r"[0-9]+"),
            ("sample_rate", r"0(?:\.[0-9]+)?|1(?:\.0+)?"),
            ("sample_rand", r"0(?:\.[0-9]+)?"),
            ("sampled", r"true|false"),
        ):
            value = sampling.get(key)
            if type(value) is str and len(value) <= 32 and re.fullmatch(pattern, value):
                if key != "trace_id" or int(value, 16):
                    dsc[key] = value
        if "trace_id" in dsc:
            result["dynamic_sampling_context"] = dsc
    data = span.get("data")
    if type(data) is dict:
        error_type = data.get("exception.type")
        if (
            type(error_type) is str
            and len(error_type) <= 128
            and re.fullmatch(r"[\w.]+", error_type)
        ):
            result["data"] = {"exception.type": _scrub_text(error_type)}
    return result


def _has_span_ids(clean: dict) -> bool:
    # _span_structure only keeps well-formed, nonzero IDs.
    return "trace_id" in clean and "span_id" in clean


def _scrub_transaction_spans(event: dict) -> None:
    spans = _entries(event, "spans")
    if spans is not None:
        event["spans"] = [
            clean for clean in map(_span_structure, spans) if _has_span_ids(clean)
        ]
    # The SDK promotes root-span tags to transaction-level tags. Do not
    # forward arbitrary values from that second representation either.
    if event.get("type") == "transaction" and "tags" in event:
        tags = event["tags"]
        event["tags"] = {
            key: _scrub_text(tags[key])
            for key in ("component", "region", "cloud_deployment")
            if type(tags) is dict and key in tags
        }
    if "contexts" in event:
        # set_context accepts arbitrary text, including on the root transaction.
        # Read trace independently: Profile.to_json needs its ID even when an
        # unrelated scope or transaction context exceeds the scrubber budget.
        contexts = event["contexts"]
        event["contexts"] = {}
        if type(contexts) is dict:
            trace = contexts.get("trace")
            if type(trace) is dict:
                event["contexts"]["trace"] = _span_structure(trace)
            response = contexts.get("response")
            if type(response) is dict:
                status = _http_status_code(response)
                if status is not None:
                    event["contexts"]["response"] = {"status_code": status}


def _log_params(params: Any, message: str) -> dict | list | None:
    # The serializer can already have turned an arbitrary object into text.
    # Unlabelled string arguments are opaque (including bare credentials);
    # counts, flags and nulls have a representation we can check directly.
    def scalar(value: Any) -> Any:
        if value is None or type(value) in (bool, float):
            return value
        if type(value) is str and len(value) <= 7 and value in _HTTP_METHODS:
            return value
        if type(value) is int and value.bit_length() <= 64:
            return value
        return _REDACTED

    if type(params) not in (tuple, list, dict) or len(params) > _MAX_ITEMS:
        return None
    if type(params) is dict:
        result = {}
        for key, value in params.items():
            if _is_sensitive_key(key) or f"%({key})" not in message:
                return None
            result[key] = scalar(value)
        return result
    return [scalar(value) for value in params]


def _usage_log_params(template: str, params: Any) -> list | None:
    """Allow exact usage-field=%d assignments without rendering any arguments."""
    if type(params) not in (list, tuple) or not 0 < len(params) <= _MAX_ITEMS:
        return None
    safe: list = []
    literals = []
    start = 0
    for directive in _LOG_DIRECTIVE.finditer(template):
        if len(safe) >= len(params) or directive[0] == "%":
            return None  # Unsupported directive or inconsistent arity.
        if directive[0].startswith("%"):
            safe.append(_REDACTED)
        else:
            count = params[len(safe)]
            if type(count) is not int or not 0 <= count < 2**63:
                return None
            safe.append(count)
            literals.append(template[start : directive.start()])
            start = directive.end()
    if not start or len(safe) != len(params):
        return None
    # Only the validated assignments are exempt from the sensitive-text check.
    literals.append(template[start:])
    return safe if _scrub_text(" ".join(literals)) != _REDACTED else None


def _parameterized_logentry(entry: dict, log_record: object) -> dict:
    template = entry.get("message")
    if type(template) is not str or len(template) > _MAX_TEXT or "%" not in template:
        return {"message": _REDACTED}
    # Validate the original template, not the SDK's rendering of record.msg.
    if log_record is not None:
        original = getattr(log_record, "msg", None)
        if type(original) is not str or original != template:
            return {"message": _REDACTED}
    formatted = entry.get("formatted")
    if type(formatted) is str and template == formatted:
        return {"message": _REDACTED}  # No distinct template to retain.
    message = _scrub_text(template)
    if message == _REDACTED:
        counts = _usage_log_params(template, entry["params"])
        if counts is not None:
            return {"message": template, "params": counts}
    elif "formatted" not in entry or _scrub_text(formatted) != _REDACTED:
        params = _log_params(entry["params"], message)
        if params is not None:
            return {"message": message, "params": params}
    return {"message": message}


def _scrub_request(event: dict) -> None:
    request = event.get("request")
    info = event.get("transaction_info")
    source = info.get("source") if type(info) is dict else None
    name = event.get("transaction")
    # DjangoIntegration(url) labels resolver templates 'route'; its unresolved
    # fallback and the ASGI middleware label the literal path 'url'. Never
    # re-resolve a path/import URLconf from an error hook during bootstrap.
    route = None
    if (
        source == "route"
        and type(name) is str
        and len(name) <= 1024
        and re.fullmatch(r"/(?!/)[\w./{}:-]*", name)
    ):
        route = name
    if source == "url" or (type(request) is dict and name is not None and not route):
        # A profiled transaction requires a name, even without a matched route.
        event["transaction"] = "unresolved request"
        event["transaction_info"] = {"source": "url"}
    if type(request) is not dict:
        event.pop("request", None)
        return

    clean: dict[str, Any] = {}
    if route:
        clean["url"] = route
    method = request.get("method")
    if type(method) is str and len(method) <= 7 and method in _HTTP_METHODS:
        clean["method"] = method
    for key in ("cookies", "query_string"):
        if key in request:
            clean[key] = _REDACTED
    if "data" in request and not _is_telemetry_url(request.get("url")):
        clean["data"] = _REDACTED
    headers = request.get("headers")
    if type(headers) is dict and len(headers) <= _MAX_ITEMS:
        safe_headers = {}
        for key, value in headers.items():
            if (
                type(key) is str
                and len(key) <= 12
                and key.lower() in ("accept", "content-type")
                and type(value) is str
                and len(value) <= 128
            ):
                media_type = value.partition(";")[0].strip().lower()
                if media_type in _HEADER_MEDIA_TYPES:
                    safe_headers[key.title()] = media_type
        clean["headers"] = safe_headers
    event["request"] = clean


def _scrub_extra(event: dict, grpc_logger: bool) -> None:
    # Framework extras include the raw protobuf request. Keep only validated
    # counts and protocol status, even when the failure precedes the receiver.
    if "extra" in event:
        extra = event["extra"]
        event["extra"] = {
            k: extra[k]
            for k in (*_USAGE_FIELDS, "count", "retry_count")
            if type(extra) is dict
            and k in extra
            and type(extra[k]) is int
            and 0 <= extra[k] < 2**63
        }
        if type(extra) is dict:
            status = (
                _grpc_status_name(extra.get("status_code"))
                if grpc_logger
                else _http_status_code(extra)
            )
            if status is not None:
                event["extra"]["status_code"] = status


def _scrub_event(
    event: dict, *, vertex_log: bool = False, log_record: object = None
) -> dict:
    grpc_logger = _is_grpc_framework_logger(event.get("logger"))
    _scrub_breadcrumbs(event)
    _scrub_transaction_spans(event)
    for key in ("logger", "level"):
        if key in event:
            event[key] = _scrub_text(event[key])
    _scrub_request(event)
    _scrub_extra(event, grpc_logger)

    replacement = _scrub_frames(event)
    if vertex_log:
        replacement = _VERTEX_SAFE_MESSAGE
    elif (
        grpc_logger
        or event.get("logger") == "tracer.services.grpc"
        or event.get("type") == "transaction"
    ):
        replacement = replacement or _REDACTED
    for value in event.get("exception", {}).get("values", []):
        if "value" in value:
            value["value"] = replacement or _scrub_text(value["value"])
        mechanism = value.get("mechanism")
        if type(mechanism) is dict:
            # The SDK can attach an independent description and arbitrary data
            # here. Keep how the error was captured and its place in the chain.
            safe_mechanism: dict[str, Any] = {"type": "generic"}
            for key in ("handled", "synthetic", "is_exception_group"):
                if type(mechanism.get(key)) is bool:
                    safe_mechanism[key] = mechanism[key]
            for key in ("exception_id", "parent_id"):
                if type(mechanism.get(key)) is int and 0 <= mechanism[key] < 2**63:
                    safe_mechanism[key] = mechanism[key]
            capture_type = mechanism.get("type")
            if type(capture_type) is str and capture_type in (
                "generic",
                "logging",
                "excepthook",
                "threading",
                "asyncio",
                "django",
                "celery",
                "asgi",
                "wsgi",
            ):
                safe_mechanism["type"] = capture_type
            value["mechanism"] = safe_mechanism
        else:
            value.pop("mechanism", None)
    if "message" in event:
        event["message"] = replacement or _scrub_text(event["message"])
    if "logentry" in event:
        entry = event["logentry"]
        # Never replay a format string, stringify parameters, or guess which
        # argument produced part of a rendered message.
        if replacement:
            event["logentry"] = {"message": replacement}
        elif type(entry) is not dict:
            event.pop("logentry")
        else:
            params = entry.get("params")
            # A formatted string may include a repr or an unlabelled secret;
            # never send it alongside parameters. Keep the static title.
            has_params = params is not None and not (
                type(params) in (list, tuple, dict) and not params
            )
            if has_params:
                event["logentry"] = _parameterized_logentry(entry, log_record)
            else:
                message = _scrub_text(entry.get("message"))
                formatted = _scrub_text(entry.get("formatted"))
                if "formatted" in entry and (
                    "message" not in entry or message != formatted
                ):
                    event["logentry"] = {"message": _REDACTED}
                else:
                    event["logentry"] = {
                        k: v
                        for k, v in (("message", message), ("formatted", formatted))
                        if k in entry
                    }

    # SDK annotations describe the original values (including arbitrary keys),
    # not the rebuilt event. They aren't diagnostic fields we can validate.
    event.pop("_meta", None)
    return event


def _event_scrubber() -> Any:
    """The denylist scrubber installed by init_sentry.

    It redacts values under denylisted names in the request, extras,
    breadcrumbs, spans and stack-frame local variables, but it matches whole
    names only (case-insensitively), not the substrings `_scrub` matches.
    """
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

    denylist = (
        list(DEFAULT_DENYLIST)
        + sorted(_CREDENTIAL_ALIASES)
        + [k for k in SENSITIVE_KEY_SUBSTRINGS if k not in DEFAULT_DENYLIST]
    )

    class FrameworkScrubber(EventScrubber):
        def scrub_request(self, event: "Event") -> None:
            # Request-local SDK processors can attach the HTTP request *after*
            # the application processor. Bound it before the recursive walk.
            try:
                _scrub_request(cast(dict, event))
            except Exception:  # noqa: BLE001 - never serialize uninspectable HTTP metadata
                event.pop("request", None)
                if "transaction" in event:
                    event["transaction"] = "unresolved request"
                event.pop("transaction_info", None)
            super().scrub_request(event)

        def scrub_frames(self, event: "Event") -> None:
            # attach_stacktrace may have just built a message stack. Its capture
            # and in-app marking have already happened; bound the remaining SDK
            # walk and serialization, including the task logger's fallback stack.
            if "threads" in event and "exception" not in event and "stacktrace" not in event:
                try:
                    replacement = _scrub_frames(cast(dict, event))
                except ValueError:
                    event.pop("threads", None)
                    replacement = _REDACTED
                if replacement:
                    if "message" in event:
                        event["message"] = replacement
                    if "logentry" in event:
                        event["logentry"] = {"message": replacement}
            super().scrub_frames(event)

        def scrub_extra(self, event: "Event") -> None:
            # Celery's request-local processor can add task arguments here too.
            _scrub_extra(
                cast(dict, event), _is_grpc_framework_logger(event.get("logger"))
            )
            super().scrub_extra(event)

    return FrameworkScrubber(denylist=denylist, recursive=True)


def _expected_feature_denial(hint: dict) -> bool:
    """Keep entitlement lookup faults even though they use a denial type."""
    exc_info = hint.get("exc_info")
    if not isinstance(exc_info, (tuple, list)) or len(exc_info) != 3:
        return False
    # Keep Sentry initialization independent of Django/DRF module loading.
    try:
        from tfc.ee_gating import FeatureUnavailable, entitlements_unavailable
    except ImportError:
        return False
    error = exc_info[1]
    return isinstance(error, FeatureUnavailable) and not entitlements_unavailable(error)


def _expected_log_outcome(event: dict, hint: dict) -> bool:
    """Normalize structlog dicts and Temporal JSON without interpreting text.

    Malformed/unknown payloads and any exception capture fail open. Source-level
    INFO/WARNING logging is the primary control; this is a log-only backstop.
    """
    if hint.get("exc_info") or event.get("exception"):
        return False
    record = hint.get("log_record")
    logentry = event.get("logentry")
    candidates = [
        getattr(record, "msg", None),
        event.get("message"),
    ]
    if isinstance(logentry, dict):
        candidates.extend([logentry.get("formatted"), logentry.get("message")])
    for payload in candidates:
        if type(payload) is str and len(payload) <= _MAX_TEXT:
            try:
                payload = json.loads(payload)
            except (ValueError, RecursionError):
                continue
        if type(payload) is not dict:
            continue
        identity = (event.get("logger"), payload.get("event"), payload.get("reason"))
        if not (
            all(type(value) is str and len(value) <= 128 for value in identity)
            and payload.get("outcome") == "handled"
        ):
            continue
        if identity in EXPECTED_LOG_OUTCOMES:
            return True
    return False


def _is_telemetry_url(url: object) -> bool:
    """True when ``url`` matches a path prefix whose body must never reach Sentry.

    Reuses ``SENSITIVE_BODY_PATH_PREFIXES`` (as its patterns) so the OTel
    middleware and the Sentry scrubber agree on which paths are sensitive;
    previously the prefix was hardcoded here and could silently drift from the
    middleware copy.
    """
    if type(url) is not str or len(url) > _MAX_TEXT:
        return True  # Unknown target: discard its body.
    from tfc.telemetry.middleware import SENSITIVE_BODY_PATHS

    return any(pattern.search(url) for pattern in SENSITIVE_BODY_PATHS)


def _scrub_breadcrumbs(event: dict) -> None:
    # Construct new records at send time, even for SDK-retained or restored
    # scope breadcrumbs that never passed through our ingestion hook.
    if "breadcrumbs" in event:
        crumbs = event["breadcrumbs"]
        wrapped = type(crumbs) is dict
        items = crumbs.get("values") if wrapped else crumbs
        clean = []
        if type(items) in (list, tuple) and len(items) <= _MAX_ITEMS:
            for crumb in items:
                if type(crumb) is dict:
                    safe = _before_breadcrumb(crumb, {})
                    if safe is not None:
                        clean.append(safe)
        event["breadcrumbs"] = {"values": clean} if wrapped else clean


def _before_send_transaction(event: "Event", hint: "Hint") -> "Event | None":
    try:
        clean = _scrub_event(cast(dict, event))
        if clean.get("type") == "transaction":
            trace = clean.get("contexts", {}).get("trace", {})
            if not _has_span_ids(trace):
                return None  # Never hand a malformed trace to profile serialization.
    except Exception:  # noqa: BLE001 - drop SDK transactions on any scrubbing failure
        return None
    return event


def _expected_vertex_credential_error(error: object) -> bool:
    # Import only for the matching LiteLLM record; Sentry startup does not
    # require the optional provider SDK.
    try:
        from google.auth.exceptions import MalformedError, RefreshError
    except ImportError:
        return False

    if isinstance(error, RefreshError):
        # google.oauth2._client attaches the token endpoint's decoded response
        # as the second argument. Never infer a refusal from its free-form text.
        response = error.args[1] if len(error.args) > 1 else None
        return (
            not error.retryable
            and isinstance(response, dict)
            and response.get("error") in ("invalid_grant", "invalid_client")
        )

    if isinstance(error, MalformedError):
        message = error.args[0] if len(error.args) == 1 else None
        if type(message) is not str or len(message) > _MAX_TEXT:
            return False
        return (
            message.startswith(
                "Service account info was not in the expected format, missing fields "
            )
            or message
            == "The private_key field was not found in the service account info."
        )

    # The key loader, credential-shape validation and token-response parser all
    # raise plain ValueError. Match only known credential validation diagnostics.
    if type(error) is not ValueError or len(error.args) != 1:
        return False
    message = error.args[0]
    return type(message) is str and message.startswith(
        (
            "Unable to load PEM file.",
            "Could not deserialize key data.",
            "Authorized user info was not in the expected format, missing fields ",
            "Invalid credentials type: <class '",
        )
    )


def _get_before_send() -> Callable:
    """
    Create the before_send hook that runs on every error event.

    Responsibilities, in order:
    1. Drop pure-infrastructure logger noise (OTel exporter, etc.) and records
       of the third-party loggers in EXPECTED_OUTCOME_LOGGERS.
    2. Drop typed feature refusals and explicitly classified log-only outcomes.
    3. Scrub secrets/PII from whatever remains.
    """

    def before_send(event: dict, hint: dict) -> dict | None:
        # 1. Infrastructure logger noise - never actionable as an issue.
        logger_name = event.get("logger") or ""
        # Match on a dot boundary: "saml2" must not swallow "saml2_auth".
        if isinstance(logger_name, str) and any(
            logger_name == p or logger_name.startswith(f"{p}.")
            for p in NOISY_LOGGER_PREFIXES
        ):
            return None
        # A third-party logger's record of an outcome our code handles, matched
        # by the record's exact logger name. Dedupe has already remembered the
        # record's exception; the SDK forgets it again when before_send drops
        # an exception event, so our own report of that same exception object
        # (LiteLLM raises it to our code) is still sent.
        record = hint.get("log_record")
        record_name = getattr(record, "name", None)
        if (
            type(record_name) is str
            and len(record_name) <= 128
            and record_name in EXPECTED_OUTCOME_LOGGERS
        ):
            return None
        # VertexBase.get_access_token logs and re-raises *all* load_auth
        # failures. Only malformed credentials and known refusals are expected.
        if record_name == "LiteLLM":
            msg = getattr(record, "msg", None)
            exc_info = getattr(record, "exc_info", None)
            if (
                isinstance(msg, str)
                and msg.startswith(_VERTEX_LOAD_MESSAGE)
                and isinstance(exc_info, tuple)
                and len(exc_info) == 3
                and _expected_vertex_credential_error(exc_info[1])
            ):
                return None

        # 2. A handled exception is not necessarily harmless. Only typed feature
        # refusals qualify; generic HTTP errors, lookup outages, disconnects and
        # text that merely resembles a skip remain actionable.
        if _expected_feature_denial(hint) or _expected_log_outcome(event, hint):
            return None

        # 3. Use the same bounded frame walk for all surviving events.
        try:
            _scrub_event(
                event,
                log_record=record,
                vertex_log=_vertex_log_message(
                    record_name, getattr(record, "msg", None)
                ),
            )
        except Exception:  # noqa: BLE001 - fail closed at the SDK hook boundary
            # Never emit the original payload after a partial scrub.
            return None

        return event

    return before_send


def _get_traces_sampler() -> Callable:
    """
    Create a traces_sampler function for intelligent sampling.

    This allows different sample rates for different transaction types.
    """

    def traces_sampler(sampling_context: dict) -> float:
        # Get the transaction context
        transaction_context = sampling_context.get("transaction_context", {})
        transaction_name = transaction_context.get("name", "")
        op = transaction_context.get("op", "")

        # Always sample error-related transactions
        if "error" in transaction_name.lower():
            return 1.0

        # Lower sample rate for health checks and static files
        if any(
            path in transaction_name
            for path in ["/health", "/ready", "/metrics", "/static", "/favicon"]
        ):
            return 0.0  # Don't trace health checks

        # Temporal workflow and activity transactions - use configured rate
        if op.startswith("temporal."):
            return TRACES_SAMPLE_RATE

        # Higher sample rate for API endpoints
        if "/api/" in transaction_name:
            return TRACES_SAMPLE_RATE

        # Default sample rate
        return TRACES_SAMPLE_RATE

    return traces_sampler


def init_sentry(
    component: str = "django",
    tags: dict | None = None,
) -> bool:
    """
    Initialize Sentry for a specific component.

    Args:
        component: Component name ("django", "celery", "temporal")
        tags: Additional tags to add to all events from this component

    Returns:
        True if Sentry was initialized, False otherwise
    """
    global _initialized_components

    # Prevent double initialization
    if component in _initialized_components:
        return True

    if not SENTRY_ENABLED or not SENTRY_DSN:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.httpx import HttpxIntegration
        from sentry_sdk.integrations.logging import (
            LoggingIntegration,
            ignore_logger,
        )

        from tfc.logging.sentry_payloads import PayloadBoundsIntegration

        # Suppress pure-infrastructure loggers at the source. This is the
        # single most important noise lever: it stops the OTel exporter's
        # transient "failed to reach collector" ERRORs from becoming issues.
        for logger_name in IGNORED_LOGGERS:
            ignore_logger(logger_name)

        import logging as _logging

        # Level at/above which a log record becomes a Sentry event. ERROR by
        # default; overridable via SENTRY_LOG_LEVEL (e.g. WARNING, CRITICAL).
        _event_level = getattr(
            _logging, os.getenv("SENTRY_LOG_LEVEL", "ERROR").upper(), _logging.ERROR
        )

        integrations = [
            # The ONLY path that turns ERROR logs into Sentry events. The
            # redundant Django EventHandler was removed from logging/config.py
            # so events are captured exactly once and Temporal/Celery (which do
            # not use the Django LOGGING dict) are covered too.
            LoggingIntegration(
                level=_logging.INFO,  # capture INFO+ as breadcrumbs
                event_level=_event_level,  # send ERROR+ as events
            ),
            HttpxIntegration(),  # Track outgoing HTTP requests via httpx
            PayloadBoundsIntegration(),
        ]

        # Add component-specific integrations
        if component == "django":
            import django.db.models.signals
            from sentry_sdk.integrations.django import DjangoIntegration

            integrations.append(
                DjangoIntegration(
                    transaction_style="url",
                    middleware_spans=True,
                    signals_spans=True,
                    signals_denylist=[
                        django.db.models.signals.pre_init,
                        django.db.models.signals.post_init,
                    ],
                    cache_spans=True,
                    http_methods_to_capture=("GET", "POST", "PUT", "DELETE", "PATCH"),
                )
            )

        if component in ("django", "celery"):
            from sentry_sdk.integrations.celery import CeleryIntegration

            integrations.append(
                CeleryIntegration(
                    monitor_beat_tasks=True,
                    propagate_traces=True,
                )
            )

        # Build default tags
        default_tags = {"component": component}
        if tags:
            default_tags.update(tags)

        # Defence-in-depth scrubbing: even with send_default_pii on, redact a
        # broad denylist of secret-bearing keys recursively before send.
        event_scrubber = _event_scrubber()

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=integrations,
            # Environment and release
            environment=ENV_TYPE,
            release=os.getenv("APP_VERSION", os.getenv("GIT_SHA", "unknown")),
            # Tracing configuration
            enable_tracing=True,
            traces_sampler=_get_traces_sampler(),
            # Profiling configuration
            profiles_sample_rate=PROFILES_SAMPLE_RATE,
            # Error capture configuration. PII is captured for debugging but
            # passed through the scrubber + before_send redaction below.
            send_default_pii=True,
            event_scrubber=event_scrubber,
            # Keep stack frames and request values readable without bloating events.
            max_value_length=_MAX_TEXT,
            max_request_body_size="never",  # Do not collect what the hook discards.
            max_breadcrumbs=100,
            attach_stacktrace=True,  # Attach stack traces to all messages
            include_source_context=True,  # Include source code context
            include_local_variables=False,  # Never ask the SDK to render arbitrary locals
            # In-app marking for better stack traces
            in_app_include=[
                "tfc",
                "accounts",
                "agentic_eval",
                "simulate",
                "tracer",
                "prompts",
            ],
            in_app_exclude=["celery", "kombu", "temporalio", "django"],
            # Hooks
            before_send=_get_before_send(),
            before_breadcrumb=_before_breadcrumb,
            before_send_transaction=_before_send_transaction,
            # Debug mode in staging for troubleshooting
            debug=IS_STAGING,
            # Session tracking
            auto_session_tracking=True,
            # DB query source tracking
            enable_db_query_source=True,
            db_query_source_threshold_ms=100,  # Add source for queries > 100ms
        )

        # Set default tags for this component
        sentry_sdk.set_tags(default_tags)
        # Tag events with region for multi-region observability
        sentry_sdk.set_tag("region", os.getenv("REGION", "us"))
        sentry_sdk.set_tag(
            "cloud_deployment", os.getenv("CLOUD_DEPLOYMENT", "") or "self-hosted"
        )

        _initialized_components.add(component)
        return True

    except Exception as e:
        print(f"Failed to initialize Sentry for {component}: {e}")
        return False


def set_user_context(user_id: str, email: str = "", username: str = "") -> None:
    """Set user context for Sentry events."""
    if not SENTRY_ENABLED:
        return

    try:
        import sentry_sdk

        sentry_sdk.set_user(
            {
                "id": str(user_id),
                "email": email,
                "username": username,
            }
        )
    except Exception:
        pass


def capture_exception_with_context(
    exception: Exception,
    context: dict | None = None,
    tags: dict | None = None,
) -> str | None:
    """
    Capture an exception with additional context.

    Args:
        exception: The exception to capture
        context: Additional context data (e.g., {"activity": {...}})
        tags: Additional tags for filtering (e.g., {"workflow": "experiment"})

    Returns:
        The Sentry event ID if captured, None otherwise
    """
    if not SENTRY_ENABLED:
        return None

    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            if context:
                for key, value in context.items():
                    scope.set_context(key, value)
            if tags:
                for key, value in tags.items():
                    scope.set_tag(key, value)
            return sentry_sdk.capture_exception(exception)
    except Exception:
        return None


def capture_message(
    message: str,
    level: str = "info",
    context: dict | None = None,
    tags: dict | None = None,
) -> str | None:
    """
    Capture a message to Sentry.

    Args:
        message: The message to capture
        level: Log level ("debug", "info", "warning", "error", "fatal")
        context: Additional context data
        tags: Additional tags for filtering

    Returns:
        The Sentry event ID if captured, None otherwise
    """
    if not SENTRY_ENABLED:
        return None

    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            if context:
                for key, value in context.items():
                    scope.set_context(key, value)
            if tags:
                for key, value in tags.items():
                    scope.set_tag(key, value)
            return sentry_sdk.capture_message(message, level=level)
    except Exception:
        return None


def add_breadcrumb(
    message: str,
    category: str = "custom",
    level: str = "info",
    data: dict | None = None,
) -> None:
    """
    Add a breadcrumb for debugging.

    Breadcrumbs are a trail of events that happened before an error.

    Args:
        message: Description of what happened
        category: Category for grouping (e.g., "http", "query", "user")
        level: Severity ("debug", "info", "warning", "error", "critical")
        data: Additional data to attach
    """
    if not SENTRY_ENABLED:
        return

    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(
            message=message,
            category=category,
            level=level,
            data=data or {},
        )
    except Exception:
        pass


def start_transaction(
    name: str,
    op: str = "task",
    description: str = "",
) -> Any:
    """
    Start a new Sentry transaction for performance monitoring.

    Usage:
        with start_transaction(name="process_experiment", op="workflow") as txn:
            # ... do work ...
            txn.set_tag("experiment_id", exp_id)

    Args:
        name: Transaction name (e.g., "process_experiment")
        op: Operation type (e.g., "http.server", "task", "workflow")
        description: Optional description

    Returns:
        Transaction context manager (or no-op if Sentry disabled)
    """
    if not SENTRY_ENABLED:
        from contextlib import nullcontext

        return nullcontext()

    try:
        import sentry_sdk

        return sentry_sdk.start_transaction(
            name=name,
            op=op,
            description=description,
        )
    except Exception:
        from contextlib import nullcontext

        return nullcontext()
