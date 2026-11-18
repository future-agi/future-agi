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
from typing import Any

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
_CREDENTIAL_FIELDS = {"private_key", "client_secret", "refresh_token"}
# The SDK may already have rendered or truncated a JSON string or a dict repr.
_CREDENTIAL_FIELD_IN_TEXT = re.compile(
    r"""["'](?:private_key|client_secret|refresh_token)["']\s*:"""
)
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


def _is_sensitive_key(key: Any) -> bool:
    """True if a dict/header key looks like it carries a secret or PII token."""
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return lowered in _CREDENTIAL_ALIASES or any(
        s in lowered for s in SENSITIVE_KEY_SUBSTRINGS
    )


_SAML_VALUE_MARKERS = ("<saml:", "<samlp:", "SAMLResponse=", "fai_saml_b_")


def _scrub(value: Any, depth: int = 0) -> Any:
    """Recursively redact values under sensitive keys (defensive, bounded)."""
    if depth > 6:
        return value
    if isinstance(value, str) and (
        any(marker in value for marker in _SAML_VALUE_MARKERS)
        or _CREDENTIAL_FIELD_IN_TEXT.search(value)
    ):
        return _REDACTED
    if isinstance(value, dict):
        if not _CREDENTIAL_FIELDS.isdisjoint(value):
            # Sections such as extra or headers must remain mappings in Sentry.
            return dict.fromkeys(value, _REDACTED)
        return {
            k: (_REDACTED if _is_sensitive_key(k) else _scrub(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(v, depth + 1) for v in value]
    return value


# ``<sensitive name>=<value>`` or ``<sensitive name>: <value>`` inside free
# text, with an optional quote around the name and an optional auth scheme. A
# quoted value runs to its matching closing quote, spaces and newlines
# included; a quote after a backslash does not close it (``repr`` and JSON
# double the backslash of an escaped quote, so any run of backslashes counts as
# escaping the next character). A quoted value with no
# matching closing quote (cut off, or closed by the other kind of quote) runs to
# the end of the text: over-redacting is better than leaking. An unquoted value
# runs to the next whitespace or delimiter: there is no way to tell where an
# unquoted value with spaces ends, so callers that log one as a %s argument are
# covered by matching that argument by position instead.
_SENSITIVE_PAIR_IN_TEXT = re.compile(
    r"""(?P<key>[\w.\-]*(?:"""
    + "|".join(re.escape(key) for key in SENSITIVE_KEY_SUBSTRINGS)
    + r""")[\w.\-]*)(?P<sep>["']?\s*[:=]\s*)"""
    r"""(?:(?P<quote>["'])(?P<quoted>(?:\\+[\s\S]?|(?!(?P=quote))[^\\])*)"""
    r"""(?:(?P<close>(?P=quote))|\Z)"""
    r"""|(?P<value>(?:(?:Bearer|Basic|Token)\s+)?[^\s"',;&}\]]+))""",
    re.IGNORECASE,
)


def _value_group(match: re.Match) -> str:
    return "quoted" if match["quote"] else "value"


# LLM usage figures such as ``max_tokens=4096`` or ``token_count=12`` name a
# count, not a credential; keep them readable.
_TOKEN_USAGE_NAME = re.compile(
    r"tokens$|token_(?:count|usage|limit|budget)s?$", re.IGNORECASE
)


def _scrub_text(value: Any) -> Any:
    """Redact credentials in free text such as an exception or log message.

    A credential document or SAML payload replaces the whole text, as
    ``_scrub`` does for any string. Otherwise only the value of each
    ``<sensitive name>=<value>`` pair is replaced, so the rest of the
    diagnostic text survives.
    """
    if not isinstance(value, str):
        return value
    scrubbed = _scrub(value)
    if scrubbed is not value:
        return scrubbed

    def redact(match: re.Match) -> str:
        if _TOKEN_USAGE_NAME.search(match["key"]) or match.string.startswith(
            _REDACTED, match.start(_value_group(match))
        ):
            return match[0]
        return (
            f"{match['key']}{match['sep']}{match['quote'] or ''}"
            f"{_REDACTED}{match['close'] or ''}"
        )

    return _SENSITIVE_PAIR_IN_TEXT.sub(redact, value)


def _scrub_text_deep(value: Any, depth: int = 0) -> Any:
    """``_scrub`` a structure, then ``_scrub_text`` every string left in it."""
    value = _scrub(value, depth)
    if depth > 6:
        return value
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, dict):
        return {k: _scrub_text_deep(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_text_deep(v, depth + 1) for v in value]
    return value


def _redacted_starts(text: Any) -> set[int] | None:
    """Where each value ``_scrub_text`` would redact in ``text`` starts; None
    when it would redact all of it."""
    if not isinstance(text, str):
        return set()
    if _scrub(text) is not text:
        return None
    return {
        match.start(_value_group(match))
        for match in _SENSITIVE_PAIR_IN_TEXT.finditer(text)
        if not _TOKEN_USAGE_NAME.search(match["key"])
    }


def _occurrences(text: str, part: str) -> list[int]:
    found, index = [], text.find(part)
    while index != -1:
        found.append(index)
        index = text.find(part, index + 1)
    return found


def _scrub_log_params(params: Any, formatted: Any) -> tuple[Any, bool]:
    """Redact log arguments that ``formatted`` shows as credential values.

    ``logger.error("api_key=%s", key)`` passes the credential as a bare
    argument with no name of its own. An argument is redacted when its
    rendering starts exactly where a redacted value starts in the formatted
    message (so a spaced credential goes whole, and a short argument such as
    1 is not redacted merely for occurring inside one). Every argument is also
    scrubbed on its own. Returns the params and whether any was redacted.
    """
    starts = _redacted_starts(formatted)
    redacted = False

    def one(param: Any) -> Any:
        nonlocal redacted
        rendered = str(param)
        if rendered and (
            (starts is None and isinstance(param, str))
            or (
                starts
                and isinstance(formatted, str)
                and any(i in starts for i in _occurrences(formatted, rendered))
            )
        ):
            redacted = True
            return _REDACTED
        return _scrub_text_deep(param)

    if isinstance(params, (list, tuple)):
        return [one(param) for param in params], redacted
    if isinstance(params, dict):
        scrubbed = {}
        for key, param in params.items():
            if _is_sensitive_key(key):
                redacted = True
                scrubbed[key] = _REDACTED
            else:
                scrubbed[key] = one(param)
        return scrubbed, redacted
    return params, redacted


def _scrub_message_fields(event: dict, values: list[dict]) -> None:
    """Scrub exception values and the event's message/logentry text in place."""
    for value in values:
        if "value" in value:
            value["value"] = _scrub_text(value["value"])
    if "message" in event:
        event["message"] = _scrub_text(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        redacted = False
        if "params" in logentry:
            logentry["params"], redacted = _scrub_log_params(
                logentry["params"], logentry.get("formatted")
            )
        if redacted and isinstance(logentry.get("message"), str):
            # Re-render from the redacted arguments: the formatted text would
            # otherwise keep whatever of an argument the text match missed,
            # such as the second word of a spaced credential.
            params = logentry["params"]
            try:
                logentry["formatted"] = logentry["message"] % (
                    params if isinstance(params, dict) else tuple(params)
                )
            except (TypeError, ValueError, KeyError):
                logentry["formatted"] = _REDACTED
        for key in ("message", "formatted"):
            if key in logentry:
                logentry[key] = _scrub_text(logentry[key])


def _vertex_log_message(logger: object, message: object) -> bool:
    return (
        logger == "LiteLLM"
        and isinstance(message, str)
        and message.startswith(
            (_VERTEX_LOAD_MESSAGE, "Failed to load vertex credentials: ")
        )
    )


def _vertex_auth_frame(module: object, function: object) -> bool:
    return module == _VERTEX_MODULE and function in (
        "load_auth",
        "get_access_token",
        "get_access_token_async",
        "refresh_auth",
    )


def _before_breadcrumb(crumb: dict, hint: dict) -> dict:
    # A dropped log event still creates a breadcrumb on the *next* event.
    # Strip the rendered message and its arguments before storing it in scope.
    record = hint.get("log_record")
    sensitive = _vertex_log_message(crumb.get("category"), crumb.get("message"))
    if record is not None and record.exc_info:
        tb = record.exc_info[2]
        while tb is not None and not sensitive:
            frame = tb.tb_frame
            sensitive = _vertex_auth_frame(
                frame.f_globals.get("__name__"), frame.f_code.co_name
            )
            tb = tb.tb_next
    if sensitive:
        crumb["message"] = _VERTEX_SAFE_MESSAGE
        crumb.pop("data", None)
    else:
        # Other breadcrumbs can carry a serialized credential without coming
        # from the provider logger (for example, an HTTP breadcrumb's data).
        for key in ("message", "data"):
            if key in crumb:
                crumb[key] = _scrub_text_deep(crumb[key])
    return crumb


def _scrub_event(event: dict, *, vertex_log: bool = False) -> None:
    """
    Redact obvious secrets/PII in-place before the event leaves the process.

    Complements Sentry's server-side scrubbing and the EventScrubber denylist
    so secrets never transit the wire even when send_default_pii is on.
    """
    request = event.get("request")
    if isinstance(request, dict):
        # Cookies are session/CSRF tokens almost by definition - redact wholesale.
        if request.get("cookies"):
            request["cookies"] = _REDACTED
        for section in ("headers", "data", "env"):
            if isinstance(request.get(section), dict):
                request[section] = _scrub(request[section])
        # Strip query-string credentials wholesale rather than parse them.
        if isinstance(request.get("query_string"), str) and any(
            s in request["query_string"].lower() for s in SENSITIVE_KEY_SUBSTRINGS
        ):
            request["query_string"] = _REDACTED

    if isinstance(event.get("extra"), dict):
        event["extra"] = _scrub(event["extra"])
    contexts = event.get("contexts")
    if isinstance(contexts, dict):
        event["contexts"] = _scrub(contexts)

    def entries(container: object, key: str) -> list[dict]:
        items = container.get(key) if isinstance(container, dict) else None
        if not isinstance(items, (list, tuple)):
            return []
        return [item for item in items if isinstance(item, dict)]

    values = entries(event.get("exception"), "values")
    threads = entries(event.get("threads"), "values")
    frames = [
        frame
        for item in (*values, *threads)
        for frame in entries(item.get("stacktrace"), "frames")
    ]
    if vertex_log or any(
        _vertex_auth_frame(frame.get("module"), frame.get("function"))
        for frame in frames
    ):
        # Both the provider and its callers can hold raw credential fields under
        # unrelated names. Keep the stack and exception types, not locals or
        # free-form diagnostics (which can quote the rejected input verbatim).
        for frame in frames:
            frame.pop("vars", None)
        for value in values:
            value["value"] = _VERTEX_SAFE_MESSAGE
        if "logentry" in event:
            event["logentry"] = {"message": _VERTEX_SAFE_MESSAGE}
        if "message" in event:
            event["message"] = _VERTEX_SAFE_MESSAGE
    else:
        for frame in frames:
            if isinstance(frame.get("vars"), dict):
                frame["vars"] = _scrub_text_deep(frame["vars"])
        _scrub_message_fields(event, values)


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
    return EventScrubber(denylist=denylist, recursive=True)


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
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, RecursionError):
                continue
        if not isinstance(payload, dict):
            continue
        identity = (event.get("logger"), payload.get("event"), payload.get("reason"))
        if not (
            all(isinstance(value, str) for value in identity)
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
    if not url:
        return False
    from tfc.telemetry.middleware import SENSITIVE_BODY_PATHS

    return any(pattern.search(str(url)) for pattern in SENSITIVE_BODY_PATHS)


def _scrub_deployment_telemetry_event(event: dict) -> dict:
    # Sentry's HTTP integrations record outbound POSTs to /telemetry/ as
    # breadcrumbs with the request body in ``data.body``/``data.data``. The
    # inbound ``request.url`` check below would not match those (the exception
    # frame's URL is whatever the caller hit, not the breadcrumb's target), so
    # the registration payload would still ship to Sentry via breadcrumbs even
    # though the inbound branch scrubbed the request body. Walk breadcrumbs
    # unconditionally and strip body fields whose ``url`` points at a telemetry
    # path before running the inbound scrub.
    breadcrumbs = event.get("breadcrumbs") or []
    # Sentry SDK versions do not agree on the normalized breadcrumb shape:
    # error events commonly expose {"values": [...]}, while transaction
    # events can already expose the values list.  The scrubber runs inside
    # ``before_send_transaction`` and must never turn a successful request
    # into a 500 merely because the SDK chose the latter representation.
    if isinstance(breadcrumbs, dict):
        breadcrumbs = breadcrumbs.get("values") or []
    if not isinstance(breadcrumbs, (list, tuple)):
        breadcrumbs = []

    for breadcrumb in breadcrumbs:
        if not isinstance(breadcrumb, dict):
            continue
        data = breadcrumb.get("data") or {}
        if not isinstance(data, dict):
            continue
        if _is_telemetry_url(data.get("url")):
            for key in ("body", "data", "http.request.body", "request_body"):
                data.pop(key, None)
            breadcrumb["data"] = data

    request = event.get("request") or {}
    if not isinstance(request, dict) or not _is_telemetry_url(request.get("url")):
        return event

    request.pop("data", None)
    for exception in (event.get("exception") or {}).get("values", []):
        for frame in (exception.get("stacktrace") or {}).get("frames", []):
            frame.pop("vars", None)
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
        message = str(error)
        return (
            message.startswith(
                "Service account info was not in the expected format, missing fields "
            )
            or message
            == "The private_key field was not found in the service account info."
        )

    # The key loader, credential-shape validation and token-response parser all
    # raise plain ValueError. Match only known credential validation diagnostics.
    return type(error) is ValueError and str(error).startswith(
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
        event = _scrub_deployment_telemetry_event(event)

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
        if record_name in EXPECTED_OUTCOME_LOGGERS:
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

        # 3. Scrub secrets/PII from the surviving event.
        try:
            _scrub_event(
                event,
                vertex_log=_vertex_log_message(
                    record_name, getattr(record, "msg", None)
                ),
            )
        except Exception:
            # Never let scrubbing failures drop a real error.
            pass

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
            max_value_length=8192,
            # "medium" caps bodies at ~10KB: enough to debug, small enough that
            # large uploads/payloads don't dominate the event volume or leak.
            max_request_body_size="medium",
            max_breadcrumbs=100,
            attach_stacktrace=True,  # Attach stack traces to all messages
            include_source_context=True,  # Include source code context
            include_local_variables=True,  # Capture local variables for debugging
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
            before_send_transaction=lambda event, hint: (
                _scrub_deployment_telemetry_event(event)
            ),
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
