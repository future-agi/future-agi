"""Span export boundary for credentials.

Model-library instrumentors record a call's own arguments on its span. For
example traceai_litellm sets ``raw.input`` to every keyword argument of
``litellm.completion`` (and of ``acompletion`` and ``completion_with_retries``)
and ``llm.invocation_parameters`` to every one but the model and the messages,
so a call's ``api_key=``, or an ``Authorization`` header in its
``extra_headers``, would be exported as given. Every finished span passes
through this processor before any exporting processor sees it, whichever
instrumentor made it:

- an attribute named like a credential (``is_credential_name``, read on the
  last dotted part of its name) keeps its name, and its value becomes
  ``MASKED``;
- text that holds a JSON object or array is parsed; the value of every name
  like a credential in it, at any depth, becomes ``MASKED``, and the text is
  written back as JSON;
- text that holds a call's arguments (``ARGUMENT_ATTRIBUTES``) and does not
  parse (cut short by an attribute length limit, or not JSON), and other
  JSON-like text that does not parse and names a credential, becomes
  ``UNCHECKED``: it is never exported as it was.

Span, event and link attributes are all checked. A span with nothing to mask
is passed on as it is (the same object). A credential in free text that is
not JSON (an exception message, an object's repr) is not found here.
"""

import json
import logging
import re

from opentelemetry.sdk.trace import Event, ReadableSpan, SpanProcessor
from opentelemetry.trace import Link

logger = logging.getLogger(__name__)

# What a credential's value becomes; its name is kept.
MASKED = "[masked credential]"
# What an attribute's text becomes when it cannot be checked for credentials.
UNCHECKED = "[withheld: could not be checked for credentials]"

# Attributes that the installed traceai instrumentors (litellm, openai,
# anthropic, bedrock, google_genai, vertexai) fill with a call's own
# arguments, as JSON: their text must parse, or it is withheld.
ARGUMENT_ATTRIBUTES = frozenset({"raw.input", "llm.invocation_parameters"})

# Names whose value is a credential, compared as ``_normal_name`` gives them.
_CREDENTIAL_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "api_secret",
        "apikey",
        "authorization",
        "azure_ad_token",
        "azure_api_key",
        "client_secret",
        "key",
        "password",
        "proxy_authorization",
        "refresh_token",
        "token",
        "vertex_ai_credentials",
        "vertex_credentials",
    }
)
_CREDENTIAL_PREFIXES = ("aws_",)
_CREDENTIAL_SUFFIXES = ("_key", "_secret", "_token")
# Text that holds none of these words (in any case) names no credential,
# unless it escapes characters: a JSON unicode escape can spell a name.
_CREDENTIAL_WORDS = (
    "authorization",
    "aws",
    "credentials",
    "key",
    "password",
    "secret",
    "token",
)
_UNICODE_ESCAPE = "\\u"
_JSON_START = re.compile(r"\s*[\[{]")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# A quoted name before a colon: how JSON, or a dict's repr, names a value.
_QUOTED_NAME = re.compile(r"[\"']([^\"'\\\n]{1,200})[\"']\s*:")


def _normal_name(name):
    """``name``'s last dotted part in snake case and lower case, with ``-``
    and spaces read as ``_``."""
    leaf = name.strip().rsplit(".", 1)[-1]
    snake = _CAMEL_BOUNDARY.sub("_", leaf).casefold()
    return snake.replace("-", "_").replace(" ", "_")


def is_credential_name(name) -> bool:
    """Whether ``name`` (an attribute's, or a name in JSON) names a
    credential: one of ``_CREDENTIAL_NAMES``, any name starting ``aws_``, or
    one ending in ``_key``, ``_secret`` or ``_token``. Read on its last
    dotted part, in any case, with camelCase and ``-`` read as snake case."""
    if not isinstance(name, str):
        return False
    normal = _normal_name(name)
    return (
        normal in _CREDENTIAL_NAMES
        or normal.startswith(_CREDENTIAL_PREFIXES)
        or normal.endswith(_CREDENTIAL_SUFFIXES)
    )


def _may_name_a_credential(text) -> bool:
    folded = text.casefold()
    return _UNICODE_ESCAPE in text or any(word in folded for word in _CREDENTIAL_WORDS)


def _names_a_credential(text) -> bool:
    """Whether ``text``, which did not parse, quotes a credential's name
    before a colon, or escapes characters (an escape can spell a name)."""
    return _UNICODE_ESCAPE in text or any(
        is_credential_name(match.group(1)) for match in _QUOTED_NAME.finditer(text)
    )


def _masked_json(value):
    """(``value``, parsed JSON, with every credential's value masked at any
    depth; whether anything changed). Text in it that holds JSON is checked
    the same way."""
    if isinstance(value, dict):
        result, changed = {}, False
        for name, item in value.items():
            if is_credential_name(name):
                result[name] = MASKED
                changed = changed or item != MASKED
            else:
                result[name], inner = _masked_json(item)
                changed = changed or inner
        return result, changed
    if isinstance(value, list):
        pairs = [_masked_json(item) for item in value]
        return [item for item, _ in pairs], any(inner for _, inner in pairs)
    if isinstance(value, str):
        return _masked_text(value)
    return value, False


def _masked_text(text, arguments=False):
    """(the text to export for ``text``; whether it changed). ``arguments``:
    the text holds a call's arguments, and must parse."""
    if not arguments and not (_JSON_START.match(text) and _may_name_a_credential(text)):
        return text, False
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        if arguments or _names_a_credential(text):
            return UNCHECKED, True
        return text, False
    masked, changed = _masked_json(parsed)
    if not changed:
        return text, False
    # ASCII output: text with an unpaired surrogate still encodes for export.
    return json.dumps(masked), True


def _masked_value(name, value):
    """(the value to export for the attribute ``name``; whether it
    changed)."""
    if is_credential_name(name):
        return MASKED, value != MASKED
    arguments = name in ARGUMENT_ATTRIBUTES
    if isinstance(value, str):
        return _masked_text(value, arguments)
    if isinstance(value, (list, tuple)):
        pairs = [
            _masked_text(item, arguments) if isinstance(item, str) else (item, False)
            for item in value
        ]
        if any(changed for _, changed in pairs):
            return tuple(item for item, _ in pairs), True
    return value, False


def _masked_attributes(attributes):
    """(the attributes to export; whether any changed). A changed set is a
    new dict: the live span's own attributes are never changed."""
    if not attributes:
        return attributes, False
    result, changed = {}, False
    for name, value in attributes.items():
        result[name], inner = _masked_value(name, value)
        changed = changed or inner
    return (result, True) if changed else (attributes, False)


def mask_span(span):
    """Return ``span``, or a copy with every credential masked in its own,
    its events' and its links' attributes."""
    attributes, changed = _masked_attributes(span.attributes)
    events = []
    for event in span.events:
        event_attributes, event_changed = _masked_attributes(event.attributes)
        changed = changed or event_changed
        events.append(
            Event(event.name, event_attributes, event.timestamp)
            if event_changed
            else event
        )
    links = []
    for link in span.links:
        link_attributes, link_changed = _masked_attributes(link.attributes)
        changed = changed or link_changed
        links.append(Link(link.context, link_attributes) if link_changed else link)
    if not changed:
        return span
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attributes,
        events=events,
        links=links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class CredentialMaskingSpanProcessor(SpanProcessor):
    """Deliver only spans with every credential masked (``mask_span``) to the
    wrapped processor: the exporting one, or another boundary in front of
    it."""

    def __init__(self, delegate):
        self._delegate = delegate

    def on_start(self, span, parent_context=None):
        self._delegate.on_start(span, parent_context=parent_context)

    def on_end(self, span):
        try:
            masked = mask_span(span)
            if masked is not span:
                # The checkout boundaries keep request values by the SDK's
                # own snapshot (checkout_output); the copy carries them on.
                from .checkout_output import remember_scope, scope_values

                remember_scope(masked, scope_values(span))
        except Exception:
            # Fail closed: drop the span rather than export it unmasked.
            logger.warning("credential_span_masking_failed")
            return
        self._delegate.on_end(masked)

    def shutdown(self):
        self._delegate.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self._delegate.force_flush(timeout_millis)
