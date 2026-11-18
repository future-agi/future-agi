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

import contextlib
import contextvars
import os
import re
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote_plus

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
# from becoming an event. ignore_logger() (below) and the before_send prefix
# check are the only reliable levers.
NOISY_LOGGER_PREFIXES = (
    "opentelemetry",  # OTLP exporter/instrumentation export failures
    "saml2",
    "xmlschema",
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

# Exception class names that are expected/handled and not actionable as issues.
IGNORED_EXCEPTIONS = frozenset(
    {
        "DisallowedHost",  # Django - invalid host header (scanner/bot traffic)
        "SuspiciousOperation",  # Django - security-related, handled
        "ConnectionResetError",  # Client/peer disconnected mid-request
        "BrokenPipeError",  # Client disconnected mid-response
    }
)

# Substrings of structlog/event messages that represent expected, non-actionable
# conditions. Kept deliberately specific so real bugs are never hidden.
IGNORED_MESSAGE_SUBSTRINGS = (
    "trace_payload_not_found_in_redis",  # expected ingestion race (payload TTL)
    "Failed to export traces",  # OTel exporter (backstop for logger check)
    "Exception while exporting metrics",  # OTel exporter (backstop)
    # litellm's internal 'LiteLLM' logger emits this at ERROR when a user
    # supplies an invalid/partial Vertex AI service-account key during model
    # validation. Expected user-input error, not actionable. CORE-BACKEND-119Y.
    "Failed to load vertex credentials",
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
    "api-key",
    "api key",
    "x-api-key",
    "access_key",
    "access-key",
    "access key",
    "accesskey",
    "private_key",
    "private-key",
    "private key",
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
    # Cloud storage connection strings and their account keys.
    "connection_string",
    "connectionstring",
    "accountkey",
    "sharedaccesskey",
    "sharedaccesssignature",
    # A Slack webhook URL is itself the credential.
    "webhook_url",
    "slack_webhook",
)
_REDACTED = "[Filtered]"

# Count fields that name tokens but carry no secret. They are removed from the
# lowered text before the substring test, so `max_tokens` is kept while a text
# that also names `api_key` or `secret` is still caught. A name is removed only
# as a whole word, so it cannot eat part of a neighbouring one.
_COUNT_FIELD_NAMES = (
    "max_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "token_count",
    "tokens_used",
    "num_tokens",
)
_COUNT_FIELD = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(_COUNT_FIELD_NAMES) + r")(?![a-z0-9])"
)
# Counts quoted in LLM errors: "130000 tokens", "128000 input tokens", and the
# "tokens per min" of a rate limit. Linear: a number is only tried at its first
# digit.
_COUNT_PHRASE = re.compile(
    r"(?<![\d,])\d[\d,]*\s+(?:(?:input|output|prompt|completion)\s+)?tokens\b"
    r"|(?<![a-z0-9_])tokens per min(?:ute)?\b"
)

# Cache keys that embed a one-time token or code after a neutral prefix, so the
# key can be sent as the prefix alone.
_TOKEN_KEY_PREFIX = re.compile(
    r"\b(aws_onboard|gcp_onboard(?:ing)?|mcp_oauth_(?:code|approve|access|refresh)):"
    r"[^\s'\"]+"
)

# Slack incoming-webhook URLs are the credential themselves.
_SECRET_URL_MARKERS = ("hooks.slack.com/services/",)

# Query parameters that carry a credential under a name that does not say so.
_SENSITIVE_QUERY_PARAMS = frozenset(
    {
        "key",
        "sig",
        "signature",
        "x-amz-signature",
        "x-goog-signature",
        "code",
        "state",
    }
)

# Routes with a token in the path (tracer/urls.py shared links, accounts/urls.py
# activation, password reset and invitation, simulate/urls.py harness ingress).
_URL_TOKEN_SEGMENT = re.compile(
    r"(?P<one>/(?:shared|api/harness/ingress)/)[^/?#\s]+"
    r"|(?P<two>/(?:activate|password-reset-confirm|accept-invitation)/)"
    r"[^/?#\s]+/[^/?#\s]+"
)

# A URL that carries a password, such as a database or broker connection string.
# Linear: no nested quantifiers, and each class excludes the next delimiter.
_URL_PASSWORD = re.compile(r"://[^/\s:@]*:[^/\s@]+@")

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
    return _names_sensitive_key(key.lower())


def _names_sensitive_key(lowered: str) -> bool:
    # The count removals only take text out, so a text that names no key before
    # them names none after. Every count name holds "token".
    if not any(s in lowered for s in SENSITIVE_KEY_SUBSTRINGS):
        return False
    if "token" in lowered:
        lowered = _COUNT_FIELD.sub(" ", lowered)
        lowered = _COUNT_PHRASE.sub(" ", lowered)
        return any(s in lowered for s in SENSITIVE_KEY_SUBSTRINGS)
    return True


# Each regex below runs only when a plain `in` check says it can match: on a
# long string with no hit that is the whole saving.
_TOKEN_KEY_LITERALS = ("aws_onboard", "gcp_onboard", "mcp_oauth_")


def _redact_token_keys(text: str) -> str:
    """The text with the token of each cache key that embeds one taken out."""
    if not any(literal in text for literal in _TOKEN_KEY_LITERALS):
        return text
    return _TOKEN_KEY_PREFIX.sub(r"\1:" + _REDACTED, text)


def _has_url_password(text: str) -> bool:
    return "://" in text and _URL_PASSWORD.search(text) is not None


_SAML_VALUE_MARKERS = ("<saml:", "<samlp:", "SAMLResponse=", "fai_saml_b_")
# Azure connection strings carry their key as `Name=value` pairs.
_AZURE_VALUE_MARKERS = ("accountkey=", "sharedaccesssignature=", "sharedaccesskey=")


def _is_secret_value(value: str) -> bool:
    """True if a string value is a credential itself, whatever key holds it: a
    Slack webhook, a URL with a password, an Azure connection string."""
    lowered = value.lower()
    return (
        any(marker in value for marker in _SECRET_URL_MARKERS)
        or _has_url_password(value)
        or any(marker in lowered for marker in _AZURE_VALUE_MARKERS)
    )


def _mentions_sensitive_key(text: str) -> bool:
    """True if the text names a sensitive key anywhere, in any format."""
    return (
        any(marker in text for marker in _SAML_VALUE_MARKERS)
        or any(marker in text for marker in _SECRET_URL_MARKERS)
        or _names_sensitive_key(text.lower())
        or _has_url_password(text)
    )


# The most string data the scrub reads for one event, in characters. Past it,
# each string is replaced unread, so an event cannot hold the raising thread for
# longer than this much text costs. Direct calls outside a hook have no budget.
_SCRUB_BUDGET_CHARS = 8 * 1024 * 1024
_scrub_budget: contextvars.ContextVar = contextvars.ContextVar(
    "sentry_scrub_budget", default=None
)


@contextlib.contextmanager
def _budgeted():
    """The scope of one event's scrub, with its own budget."""
    token = _scrub_budget.set([_SCRUB_BUDGET_CHARS])
    try:
        yield
    finally:
        _scrub_budget.reset(token)


def _within_budget(text: str) -> bool:
    """Charge the string to the event's budget; False once it is spent. A string
    read twice is charged twice."""
    budget = _scrub_budget.get()
    if budget is None:
        return True
    budget[0] -= len(text)
    return budget[0] >= 0


def _scrub_text(text: str) -> str:
    """Free text: redacted whole when it names a sensitive key, else with any
    token embedded in a cache key taken out."""
    if not _within_budget(text):
        return _REDACTED
    return _scrub_text_read(text)


def _scrub_text_read(text: str) -> str:
    if _mentions_sensitive_key(text):
        return _REDACTED
    return _redact_token_keys(text)


def _scrub_query(query: str) -> str:
    """A query string with the value of each credential parameter replaced."""
    if not _within_budget(query):
        return _REDACTED
    return _scrub_query_read(query)


def _scrub_query_read(query: str) -> str:
    # Names are tested as decoded: `api%5Fkey` and `api+key` are `api_key` and
    # `api key` to the server.
    if _mentions_sensitive_key(unquote_plus(query)):
        return _REDACTED
    parts = []
    for part in query.split("&"):
        name, sep, _value = part.partition("=")
        decoded = unquote_plus(name).lower()
        if sep and (
            decoded in _SENSITIVE_QUERY_PARAMS or _names_sensitive_key(decoded)
        ):
            part = f"{name}={_REDACTED}"
        parts.append(part)
    return "&".join(parts)


def _scrub_url(url: str) -> str:
    """A URL with token path segments and credential query values replaced."""
    if not _within_budget(url):
        return _REDACTED
    return _scrub_url_read(url)


def _scrub_url_read(url: str) -> str:
    if _has_url_password(url) or any(m in url for m in _SECRET_URL_MARKERS):
        return _REDACTED

    def path_token(match: re.Match) -> str:
        if match.group("one"):
            return f"{match.group('one')}{_REDACTED}"
        return f"{match.group('two')}{_REDACTED}/{_REDACTED}"

    url = _URL_TOKEN_SEGMENT.sub(path_token, url)
    base, mark, rest = url.partition("?")
    if mark:
        query, hash_mark, fragment = rest.partition("#")
        url = base + "?" + _scrub_query_read(query) + (hash_mark + fragment)
    return url


# Data keys that hold a URL, or its query, as recorded by the HTTP integrations.
_URL_DATA_KEYS = ("url", "http.url")
_QUERY_DATA_KEYS = ("http.query", "http.fragment")


def _scrub_url_fields(data: Any) -> Any:
    """Scrub the URL and query fields of span or breadcrumb data by their shape;
    everything else is left for `_scrub_strings`."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for key in _URL_DATA_KEYS:
        if isinstance(out.get(key), str):
            out[key] = _scrub_url(out[key])
    for key in _QUERY_DATA_KEYS:
        if isinstance(out.get(key), str):
            out[key] = _scrub_query(out[key])
    return out


def _scrub(value: Any, depth: int = 0) -> Any:
    """Recursively redact values under sensitive keys (defensive, bounded)."""
    if depth > 6:
        return value
    if isinstance(value, str) and not _within_budget(value):
        return _REDACTED
    if isinstance(value, str) and any(
        marker in value for marker in _SAML_VALUE_MARKERS
    ):
        return _REDACTED
    if isinstance(value, str) and _is_secret_value(value):
        return _REDACTED
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_sensitive_key(k) else _scrub(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(v, depth + 1) for v in value]
    return value


def _scrub_strings(value: Any, depth: int = 0) -> Any:
    """Like `_scrub`, but also redacts any string that names a sensitive key.

    Stack-frame locals and breadcrumb data hold reprs, such as a dataclass or
    a dict printed as text, where the key is part of the string and not a dict
    key. A string that mentions one is redacted whole, so a harmless value that
    shares the name (a `prompt_tokens` count) is lost too.
    """
    if depth > 6:
        # Too deep to check: drop it rather than send it unread.
        return _REDACTED
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_sensitive_key(k) else _scrub_strings(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub_strings(v, depth + 1) for v in value]
    return value


# The longest string an event may carry when it leaves the process. The hooks
# cut every string to this after scrubbing (`_cap_strings`), and a rebuilt log
# line is cut to it too.
_MAX_FORMATTED = 16_384

# What init_sentry hands the SDK as `max_value_length`. The SDK cuts the strings
# it serializes (extra, breadcrumbs, frame variables, messages) to this before
# `before_send` runs, so a limit near the final cap would send the front of a
# long repr whose key name comes later (a system prompt, then
# `user_api_key=...`) with nothing in view to match. 256 KB keeps such a name in
# view for any realistic repr while the scrub's cost per string stays bounded
# (every check on a string is linear). Span descriptions are the exception: the
# SDK passes them on whole (serializer.py), so `_scrub_span_description` cuts
# them to this itself.
_SDK_MAX_VALUE_LENGTH = 262_144

# A `%` conversion in a log template, with its optional `(name)` mapping key.
# It runs on user-controlled text (a logging frame's `msg`) for every log call,
# so no two parts may match the same character: the width starts at 1-9 (a `0`
# is a flag) and the mapping key stops at the next `%`, or `%0000...` and
# `%(%(%(...` would each be quadratic.
_FORMAT_SPEC = re.compile(
    r"%(?:\([^)%]*\))?[#0\- +]*(?:[1-9]\d*|\*)?(?:\.(?:\d+|\*))?[hlL]?[diouxXeEfFgGcrsa]"
)
_KEY_MENTION = re.compile(
    "|".join(
        re.escape(k) for k in sorted(SENSITIVE_KEY_SUBSTRINGS, key=len, reverse=True)
    )
)
# What may stand between a key name and a `%` conversion that is its value:
# the rest of the name, then separators such as `=`, `: ` or `='`.
_LABEL_THEN_SPEC = re.compile("[\\w-]*[\\s:=\"'(\\[{,]*\x00")


def _template_text(template: str) -> str:
    """A log template with each `%` conversion made a NUL, and `%%` dropped, so
    a mapping key inside a conversion (`%(api_key)s`) is not read as a name."""
    return _FORMAT_SPEC.sub("\x00", template.replace("%%", ""))


def _names_key_in_template(template: str) -> bool:
    if not _within_budget(template):
        return True
    return _mentions_sensitive_key(_template_text(template))


def _template_leaks(template: str) -> bool:
    """True if a log template carries a value of its own: a key name followed by
    anything but a `%` conversion (`token abc for %s` is an f-string's secret,
    where `api_key=%s` is a label for an argument), or a credential by shape."""
    text = _template_text(template)
    if (
        any(marker in text for marker in _SAML_VALUE_MARKERS)
        or any(marker in text for marker in _SECRET_URL_MARKERS)
        or _has_url_password(text)
    ):
        return True
    lowered = text.lower()
    if "token" in lowered:
        lowered = _COUNT_PHRASE.sub(" ", _COUNT_FIELD.sub(" ", lowered))
    # Each mention is read once, and the scan resumes after its label, so a text
    # made of key names cannot make this quadratic.
    position = 0
    while (mention := _KEY_MENTION.search(lowered, position)) is not None:
        label = _LABEL_THEN_SPEC.match(lowered, mention.end())
        if label is None:
            return True
        position = label.end()
    return False


def _scrub_template(template: str) -> str:
    """The template of a log call that has arguments. It is kept whole so events
    group, unless the text itself holds a secret."""
    if not _within_budget(template) or _template_leaks(template):
        return _REDACTED
    return _redact_token_keys(template)


def _redact_all(params: Any) -> Any:
    """Every argument replaced, keeping the shape `%` formatting expects."""
    if isinstance(params, dict):
        return dict.fromkeys(params, _REDACTED)
    if isinstance(params, (list, tuple)):
        return [_REDACTED for _ in params]
    return _REDACTED


def _cap_string(value: str) -> str:
    # Like the SDK: the limit is in UTF-8 bytes, and a cut ends in "...".
    if len(value) * 4 <= _MAX_FORMATTED:
        return value
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= _MAX_FORMATTED:
        return value
    return raw[: _MAX_FORMATTED - 3].decode("utf-8", errors="ignore") + "..."


def _cap_strings(value: Any) -> Any:
    """The event with every string cut to `_MAX_FORMATTED`; the last step of the
    hooks, once the scrub has read the strings whole."""
    if isinstance(value, str):
        return _cap_string(value)
    if isinstance(value, dict):
        return {k: _cap_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_cap_strings(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_cap_strings(v) for v in value)
    return value


def _scrub_frame_vars(frame: dict) -> None:
    """Scrub a frame's local variables. In the logging module's frames `args`
    holds the call's arguments as one repr, and `msg` its template: when the
    template names a key, the arguments go."""
    local_vars = frame["vars"]
    scrubbed = _scrub_strings(local_vars)
    module = frame.get("module")
    if (
        isinstance(module, str)
        and (module == "logging" or module.startswith("logging."))
        and "args" in local_vars
        and isinstance(local_vars.get("msg"), str)
        and _names_key_in_template(local_vars["msg"])
    ):
        scrubbed["args"] = _REDACTED
    frame["vars"] = scrubbed


def _formatted(message: Any, params: Any) -> str:
    """A log line formatted from its scrubbed arguments; the template itself when
    they don't format."""
    try:
        line = str(message) % (tuple(params) if isinstance(params, list) else params)
    except (TypeError, ValueError, KeyError):
        line = str(message)
    return line[:_MAX_FORMATTED]


def _scrub_event(event: dict) -> None:
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
        # A body that is a list or a string has no key to match on: read it as text.
        if isinstance(request.get("data"), (list, str)):
            request["data"] = _scrub_strings(request["data"])
        # A query string that names a credential goes whole; in the rest, the
        # value of a parameter that is one by its name alone is replaced.
        if isinstance(request.get("query_string"), str):
            request["query_string"] = _scrub_query(request["query_string"])
        if isinstance(request.get("url"), str):
            request["url"] = _scrub_url(request["url"])

    # A transaction named by its URL (no route matched) carries the raw path.
    url_named = (
        isinstance(event.get("transaction_info"), dict)
        and event["transaction_info"].get("source") == "url"
    )
    if url_named and isinstance(event.get("transaction"), str):
        event["transaction"] = _scrub_url(event["transaction"])

    # The message and each exception's value are free text, often `str(exc)`.
    if isinstance(event.get("message"), str):
        event["message"] = _scrub_text(event["message"])
    exception = event.get("exception")
    if isinstance(exception, dict) and isinstance(exception.get("values"), list):
        for exc in exception["values"]:
            if isinstance(exc, dict) and isinstance(exc.get("value"), str):
                exc["value"] = _scrub_text(exc["value"])

    if isinstance(event.get("extra"), dict):
        event["extra"] = _scrub_strings(event["extra"])
    contexts = event.get("contexts")
    if isinstance(contexts, dict):
        # The trace's dynamic sampling context is the SDK's own (the transaction
        # name, release, public key) and a later hop reads it back as it was.
        trace = contexts.get("trace")
        sampling = (
            trace.pop("dynamic_sampling_context", None)
            if isinstance(trace, dict)
            else None
        )
        event["contexts"] = _scrub_strings(contexts)
        if sampling is not None:
            if (
                url_named
                and isinstance(sampling, dict)
                and isinstance(sampling.get("transaction"), str)
            ):
                sampling = {
                    **sampling,
                    "transaction": _scrub_url(sampling["transaction"]),
                }
            trace["dynamic_sampling_context"] = sampling
            if isinstance(event["contexts"].get("trace"), dict):
                event["contexts"]["trace"]["dynamic_sampling_context"] = sampling
    # A log call's arguments, such as an activity input passed to `%s`, and the
    # message formatted from them, which is rebuilt from the scrubbed arguments.
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        params = logentry.get("params")
        template = logentry.get("message")
        if params is not None:
            # A template that names a key (`api_key=%s`) says what its bare
            # arguments are, so none of them is kept.
            if (
                params
                and isinstance(template, str)
                and _names_key_in_template(template)
            ):
                logentry["params"] = _redact_all(params)
            else:
                logentry["params"] = _scrub_strings(params)
            # Rebuilt only when an argument was scrubbed: the line as logged is
            # kept otherwise.
            if logentry["params"] != params and logentry.get("formatted") is not None:
                logentry["formatted"] = _formatted(
                    logentry.get("message"), logentry["params"]
                )
        formatted = logentry.get("formatted")
        if isinstance(formatted, str):
            logentry["formatted"] = _scrub_text(formatted)
        # With no arguments the message is the logged text itself (an f-string,
        # or a structlog line with its fields as JSON), not a template. With
        # them it is kept so events group, unless it holds a secret of its own,
        # as when an f-string was logged with a `%s` left in it.
        message = logentry.get("message")
        if isinstance(message, str):
            if logentry.get("params"):
                logentry["message"] = _scrub_template(message)
            else:
                logentry["message"] = _scrub_text(message)

    # Breadcrumbs carry whatever the caller logged, such as a Temporal
    # activity's input. The shape is a list or a dict with a "values" list.
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        breadcrumbs = breadcrumbs.get("values")
    if isinstance(breadcrumbs, list):
        for crumb in breadcrumbs:
            if not isinstance(crumb, dict):
                continue
            is_redis = crumb.get("category") == "redis"
            if "data" in crumb:
                data = _redact_db_values(_scrub_url_fields(crumb["data"]))
                if is_redis:
                    data = _cut_redis_commands(data)
                crumb["data"] = _scrub_strings(data)
            message = crumb.get("message")
            if isinstance(message, str):
                # A redis crumb's message is the span description, with every
                # argument of the command in it.
                if is_redis:
                    message = _redis_command_and_key(message)
                crumb["message"] = _scrub_text(message)

    # Sentry captures local variables (include_local_variables=True), as reprs
    # of the objects. Redact them in every stack trace the event holds
    # (exception, threads and a top-level stacktrace). This also covers the
    # ACS candidate-store path, which leaves the raw assertion in a view frame.
    for frame in _event_frames(event):
        if isinstance(frame.get("vars"), dict):
            _scrub_frame_vars(frame)


def _event_frames(event: dict) -> list:
    """Every stack frame the event holds, tolerating odd shapes.

    The SDK's `iter_event_frames` assumes the shapes it builds itself; a hook
    must not raise on any other, so each level is checked here.
    """
    from sentry_sdk.utils import AnnotatedValue

    stacktraces = [event.get("stacktrace")]
    for key in ("threads", "exception"):
        container = event.get(key)
        if isinstance(container, dict) and isinstance(container.get("values"), list):
            stacktraces.extend(
                item.get("stacktrace")
                for item in container["values"]
                if isinstance(item, dict)
            )
    frames = []
    for stacktrace in stacktraces:
        if isinstance(stacktrace, AnnotatedValue):
            stacktrace = stacktrace.value
        if isinstance(stacktrace, dict) and isinstance(stacktrace.get("frames"), list):
            frames.extend(f for f in stacktrace["frames"] if isinstance(f, dict))
    return frames


def _event_scrubber() -> Any:
    """The denylist scrubber installed by init_sentry.

    It redacts values under denylisted names in the request, extras,
    breadcrumbs, spans and stack-frame local variables, but it matches whole
    names only (case-insensitively), not the substrings `_scrub` matches.
    """
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

    denylist = list(DEFAULT_DENYLIST) + [
        k for k in SENSITIVE_KEY_SUBSTRINGS if k not in DEFAULT_DENYLIST
    ]
    return EventScrubber(denylist=denylist, recursive=True)


def _event_message(event: dict) -> str:
    """Best-effort extraction of an event's human-readable message."""
    msg = event.get("message")
    if isinstance(msg, dict):
        msg = msg.get("formatted") or msg.get("message") or ""
    logentry = event.get("logentry")
    logentry_msg = logentry.get("message", "") if isinstance(logentry, dict) else ""
    parts = [str(msg or ""), str(logentry_msg or "")]
    return " ".join(p for p in parts if p)


def _is_telemetry_url(url: object) -> bool:
    """True when ``url`` matches a path prefix whose body must never reach Sentry.

    Reuses ``SENSITIVE_BODY_PATH_PREFIXES`` so the OTel middleware and the
    Sentry scrubber agree on which paths are sensitive; previously the prefix
    was hardcoded here and could silently drift from the middleware copy.
    """
    if not url:
        return False
    from tfc.telemetry.middleware import SENSITIVE_BODY_PATH_PREFIXES

    return any(prefix in str(url) for prefix in SENSITIVE_BODY_PATH_PREFIXES)


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
    if not _is_telemetry_url(request.get("url")):
        return event

    request.pop("data", None)
    for exception in (event.get("exception") or {}).get("values", []):
        for frame in (exception.get("stacktrace") or {}).get("frames", []):
            frame.pop("vars", None)
    return event


def _report_scrub_failure(exc: Exception) -> None:
    """Say that a scrub failed, by exception class only. It does not go through
    the logging module: Python 3.13.4+ drops a record logged while another record
    on the same thread is being handled, and a hook runs inside the handler of
    the record that made the event. It goes to the process's own stderr: in a
    Celery worker `sys.stderr` is a proxy that logs what it is given.

    Only the ways the write can fail are quiet: a closed pipe (OSError), a closed
    file (ValueError), or no stream at all (AttributeError on None). Anything else
    is a bug here and surfaces."""
    with contextlib.suppress(OSError, ValueError, AttributeError):
        stream = sys.__stderr__ if sys.__stderr__ is not None else sys.stderr
        stream.write(f"sentry_scrub_failed: {type(exc).__name__}\n")
        stream.flush()


def _get_before_send() -> Callable:
    """
    Create the before_send hook that runs on every error event.

    Responsibilities, in order:
    1. Drop pure-infrastructure logger noise (OTel exporter, etc.).
    2. Drop expected/handled exceptions and expected 4xx responses.
    3. Drop known expected log conditions matched by message substring.
    4. Scrub secrets/PII from whatever remains.
    """

    def before_send(event: dict, hint: dict) -> dict | None:
        # Fail closed: if anything raises, the event is dropped, never sent raw.
        try:
            return _before_send(event, hint)
        except Exception as exc:  # noqa: BLE001 - fail closed: drop the event, never send it raw
            _report_scrub_failure(exc)
            return None

    def _before_send(event: dict, hint: dict) -> dict | None:
        event = _scrub_deployment_telemetry_event(event)

        # 1. Infrastructure logger noise - never actionable as an issue.
        logger_name = event.get("logger") or ""
        # Match on a dot boundary: "saml2" must not swallow "saml2_auth".
        if isinstance(logger_name, str) and any(
            logger_name == p or logger_name.startswith(f"{p}.")
            for p in NOISY_LOGGER_PREFIXES
        ):
            return None

        # 2. Expected/handled exceptions and expected client (4xx) errors.
        if "exc_info" in hint:
            exc_type, exc_value, _tb = hint["exc_info"]
            exc_name = exc_type.__name__ if exc_type else ""
            if exc_name in IGNORED_EXCEPTIONS:
                return None

            status_code = getattr(exc_value, "status_code", None)
            if (
                isinstance(status_code, int)
                and 400 <= status_code < 500
                and status_code not in (401, 403)
            ):
                # Drop 400/404/405/429/etc. (client behaviour); keep 401/403
                # for security monitoring.
                return None

        # 3. Known expected conditions identified by message text.
        message = _event_message(event)
        if message and any(s in message for s in IGNORED_MESSAGE_SUBSTRINGS):
            return None

        # 4. Scrub secrets/PII from the surviving event.
        with _budgeted():
            _scrub_event(event)
        return _cap_strings(event)

    return before_send


_REDIS_COMMAND_AND_KEY = re.compile(
    r"\s*(\S+)(?:\s+(b?'(?:[^'\\]|\\.)*'|b?\"(?:[^\"\\]|\\.)*\"|\S+))?"
)


def _redis_command_and_key(description: str) -> str:
    """A redis span description cut back to the command and its key.

    With default PII on, the SDK writes every argument into the description,
    including the (pickled) value a cache write stores.
    """
    match = _REDIS_COMMAND_AND_KEY.match(description)
    if match is None:
        return _REDACTED
    return " ".join(part for part in match.groups() if part)


def _cut_redis_commands(data: Any) -> Any:
    """Span or breadcrumb data with its `redis.commands` cut back to the
    command and key of each."""
    if isinstance(data, dict) and isinstance(data.get("redis.commands"), dict):
        data = dict(data)
        commands = dict(data["redis.commands"])
        first = commands.get("first_ten")
        if isinstance(first, list):
            commands["first_ten"] = [
                _redis_command_and_key(c) if isinstance(c, str) else _REDACTED
                for c in first
            ]
        data["redis.commands"] = commands
    return data


# Span and breadcrumb data keys that hold the rows of a query: the
# clickhouse_driver integration records a query's parameters and its result with
# PII on, and customer data has no key name to match.
_DB_VALUE_KEYS = ("db.params", "db.result")


def _redact_db_values(data: Any) -> Any:
    """Span or breadcrumb data with the values of `db.params` and `db.result`
    replaced; the keys stay, so a span still shows that it had them."""
    if not isinstance(data, dict) or not any(k in data for k in _DB_VALUE_KEYS):
        return data
    return {k: (_REDACTED if k in _DB_VALUE_KEYS else v) for k, v in data.items()}


def _scrub_span_data(data: Any) -> Any:
    """Span data scrubbed as strings, except the `code.*` keys (`code.function`,
    `code.filepath`, `code.namespace`): they name code, not values, and a
    function called `get_user_from_token` would otherwise match."""
    data = _cut_redis_commands(_scrub_url_fields(_redact_db_values(data)))
    if not isinstance(data, dict):
        return _scrub_strings(data)
    scrubbed = _scrub_strings({k: v for k, v in data.items() if not _is_code_key(k)})
    return {k: data[k] if _is_code_key(k) else scrubbed[k] for k in data}


def _is_code_key(key: Any) -> bool:
    return isinstance(key, str) and key.startswith("code.")


_QUERY_OR_FRAGMENT = re.compile(r"[?#]")
# Path segments that name a credential, not a resource: the long names match as
# a substring (`access_token`), the short ones only as a whole word, so
# `/candidates/42` and `/authors/7` are kept.
_PATH_KEY_SUBSTRINGS = (
    "token",
    "secret",
    "password",
    "passwd",
    "apikey",
    "api_key",
    "credential",
    "signature",
)
_PATH_KEY_WORDS = frozenset({"key", "auth", "sig", "code"})
_PATH_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def _names_credential_segment(segment: str) -> bool:
    lowered = _COUNT_FIELD.sub(" ", segment.lower())
    return any(name in lowered for name in _PATH_KEY_SUBSTRINGS) or any(
        word in _PATH_KEY_WORDS for word in _PATH_WORD_SPLIT.split(lowered)
    )


def _scrub_path_values(url: str) -> str:
    """A URL, or a `GET <url>` description, with the path segment that follows a
    segment naming a sensitive key replaced: `/token/<value>`."""
    cut = _QUERY_OR_FRAGMENT.search(url)
    head, tail = (url[: cut.start()], url[cut.start() :]) if cut else (url, "")
    scheme = head.find("://")
    start = head.find("/", scheme + 3 if scheme != -1 else 0)
    if start == -1:
        return url
    segments = head[start:].split("/")
    after_key = False
    for index, segment in enumerate(segments):
        if not segment:
            continue
        if after_key:
            segments[index] = _REDACTED
            after_key = False
        elif _names_credential_segment(segment):
            after_key = True
    return head[:start] + "/".join(segments) + tail


def _scrub_cache_description(op: str, description: str) -> str:
    """A redis or cache span description cut to its command and key, with the key
    read as text: a key that names a sensitive one (`password_reset:<tok>`) is
    replaced, an ordinary key is kept."""
    match = _REDIS_COMMAND_AND_KEY.match(description)
    if match is None:
        return _REDACTED
    command, key = match.groups()
    # A redis span is a command and its key, and only the key is a value. A cache
    # span has no command: Django's description is the key itself, or the keys
    # joined by ", ", so each part is read.
    if op != "db.redis" or key is None:
        command = _scrub_text_read(command)
    if key is not None:
        key = _scrub_text_read(key)
    return " ".join(part for part in (command, key) if part)


def _scrub_span_description(op: Any, description: str) -> str:
    """A span description. Those of db, http, middleware and function spans are
    code paths and parametrized SQL, not values, so they are not matched by key
    name (a query on a `password` column, the CSRF middleware, an OAuth `/token`
    call). The ones that can carry a value are cut by their shape."""
    op = op if isinstance(op, str) else ""
    # The SDK does not cut span descriptions, so this does; what is cut is not
    # sent, as the final cap is far shorter.
    description = description[:_SDK_MAX_VALUE_LENGTH]
    if not _within_budget(description):
        return _REDACTED
    if op == "db.redis" or op.startswith("cache."):
        description = _scrub_cache_description(op, description)
    elif op.startswith("http"):
        description = _scrub_path_values(_scrub_url_read(description))
    elif op == "subprocess":
        # An argv can hold values, so a name in it is read as one.
        description = _scrub_text_read(description)
    if _is_secret_value(description) or any(
        marker in description for marker in _SAML_VALUE_MARKERS
    ):
        return _REDACTED
    return _redact_token_keys(description)


def _scrub_span(span: dict) -> None:
    """Scrub one transaction span: its data, its tags and its description."""
    if "data" in span:
        span["data"] = _scrub_span_data(span["data"])
    if "tags" in span:
        span["tags"] = _scrub_strings(span["tags"])
    description = span.get("description")
    if isinstance(description, str):
        span["description"] = _scrub_span_description(span.get("op"), description)


def _get_before_send_transaction() -> Callable:
    """The before_send_transaction hook: the same scrub as error events, plus
    every span's data (an outbound call's `http.query` lands there)."""

    def before_send_transaction(event: dict, hint: dict) -> dict | None:
        try:
            event = _scrub_deployment_telemetry_event(event)
            with _budgeted():
                _scrub_event(event)
                for span in event.get("spans") or []:
                    if isinstance(span, dict):
                        _scrub_span(span)
            return _cap_strings(event)
        except Exception as exc:  # noqa: BLE001 - fail closed: drop the event, never send it raw
            _report_scrub_failure(exc)
            return None

    return before_send_transaction


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
            # "medium" caps bodies at ~10KB: enough to debug, small enough that
            # large uploads/payloads don't dominate the event volume or leak.
            max_request_body_size="medium",
            max_breadcrumbs=100,
            # Bounds each captured string, and so the cost of scrubbing it. Kept
            # far above _MAX_FORMATTED: the hooks scrub strings whole, then cut
            # each to _MAX_FORMATTED.
            max_value_length=_SDK_MAX_VALUE_LENGTH,
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
            before_send_transaction=_get_before_send_transaction(),
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
