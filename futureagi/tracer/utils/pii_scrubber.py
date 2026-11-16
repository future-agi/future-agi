"""
PII Scrubber — Presidio-powered server-side PII redaction.

Replaces detected PII with <ENTITY_TYPE> tokens (e.g., <EMAIL_ADDRESS>).
Low-level helpers preserve their historical fail-open behavior for explicit
callers.  Batch ingestion never stores a raw value of a project that enables PII
redaction: a value that can never be scanned (too long, undecodable) is stored
as ``UNSCANNABLE_VALUE``, and when the engines are unavailable or fail for any
other reason, the batch is rejected before anything is stored. Trace ingestion
then retries it a bounded number of times.
"""

import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Lazy singleton initialisation — engines are created on first use so that
# import-time failures don't block startup when Presidio/spaCy isn't installed.
#
# ``_INIT_FAILED`` is a process-lifetime latch.  Because batch ingestion fails
# closed when the engines are unavailable, the latch is only set for failures
# that cannot resolve without a rebuild/restart (see ``_is_permanent_init_error``).
# Anything else (OOM during model load, transient file contention, ...) is
# retried on the next call instead of turning into a per-worker outage.
# ---------------------------------------------------------------------------
_analyzer = None
_anonymizer = None
_INIT_FAILED = False

# spaCy's error code for "model package not installed / not a valid path".
_SPACY_MISSING_MODEL_CODE = "E050"
# spaCy's error code for text longer than the model's ``max_length``.
_SPACY_TEXT_TOO_LONG_CODE = "E088"
# What a value of a PII-enabled project becomes when it can never be scanned.
UNSCANNABLE_VALUE = "[REDACTED: could not be scanned]"


def _is_permanent_init_error(exc: BaseException) -> bool:
    """Return True for engine-init failures that will never succeed in this process.

    * ``ImportError`` — presidio / spaCy not installed (``pii`` extra missing).
    * spaCy ``OSError`` E050 — the ``en_core_web_sm`` model package is absent.
    """
    if isinstance(exc, ImportError):
        return True
    if isinstance(exc, OSError) and _SPACY_MISSING_MODEL_CODE in str(exc):
        return True
    return False


def _ensure_engines() -> bool:
    """Lazily initialise Presidio engines. Returns True if ready."""
    global _analyzer, _anonymizer, _INIT_FAILED  # noqa: PLW0603
    if _INIT_FAILED:
        return False
    if _analyzer is not None and _anonymizer is not None:
        return True
    try:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_anonymizer import AnonymizerEngine

        # Custom recognizer for API keys (sk-live-..., pk_test-..., etc.)
        api_key_recognizer = PatternRecognizer(
            supported_entity="API_KEY",
            name="api_key_recognizer",
            patterns=[
                Pattern(
                    name="api_key",
                    regex=r"\b(?:sk|pk)[-_](?:live|test|prod)[-_][A-Za-z0-9]{20,}\b",
                    score=0.9,
                ),
            ],
        )

        from presidio_analyzer.nlp_engine import NlpEngineProvider

        nlp_config = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
        nlp_engine = NlpEngineProvider(nlp_configuration=nlp_config).create_engine()
        analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        analyzer.registry.add_recognizer(api_key_recognizer)
        anonymizer = AnonymizerEngine()
    except Exception as exc:
        permanent = _is_permanent_init_error(exc)
        if permanent:
            _INIT_FAILED = True
        logger.warning(
            "pii_scrubber_init_failed",
            permanent=permanent,
            will_retry=not permanent,
            exc_info=True,
        )
        return False

    # Only publish both engines once both initialised, so a failure between
    # the two can never leave a half-initialised singleton behind.
    _analyzer = analyzer
    _anonymizer = anonymizer
    logger.info("pii_scrubber_engines_initialized")
    return True


# ---------------------------------------------------------------------------
# Entity → replacement-token mapping.
# Presidio built-in entity types → our <TOKEN> convention.
# ---------------------------------------------------------------------------
_ENTITY_OPERATORS = {
    "EMAIL_ADDRESS": "replace",
    "PHONE_NUMBER": "replace",
    "CREDIT_CARD": "replace",
    "US_SSN": "replace",
    "IP_ADDRESS": "replace",
    "IBAN_CODE": "replace",
    "CRYPTO": "replace",
    "API_KEY": "replace",
}

# Map Presidio entity types to our token names
_TOKEN_NAMES = {
    "EMAIL_ADDRESS": "<EMAIL_ADDRESS>",
    "PHONE_NUMBER": "<PHONE_NUMBER>",
    "CREDIT_CARD": "<CREDIT_CARD>",
    "US_SSN": "<SSN>",
    "IP_ADDRESS": "<IP_ADDRESS>",
    "IBAN_CODE": "<IBAN_CODE>",
    "CRYPTO": "<CRYPTO>",
    "API_KEY": "<API_KEY>",
}


def _get_operator_config() -> dict:
    """Build Presidio operator config dict."""
    from presidio_anonymizer.entities import OperatorConfig

    return {
        entity: OperatorConfig(
            "replace", {"new_value": _TOKEN_NAMES.get(entity, f"<{entity}>")}
        )
        for entity in _ENTITY_OPERATORS
    }


# ---------------------------------------------------------------------------
# Content-key heuristic — only scan attribute values that are likely to
# contain user content (messages, input/output values).  Skip structural
# keys like model names, span kinds, roles, etc.
# ---------------------------------------------------------------------------
_CONTENT_KEY_SUBSTRINGS = (
    "input.value",
    "output.value",
    ".content",
    ".text",
    ".body",
    "message",
    "user.query",
    "completion",
    "prompt",
)

_STRUCTURAL_KEY_SUFFIXES = (
    ".role",
    ".kind",
    ".model",
    ".name",
    ".type",
    ".status",
    ".finish_reason",
    ".tool_call_id",
    ".function.name",
    ".function.arguments",
    ".embedding.model",
)


def _is_content_key(key: str) -> bool:
    """Return True if *key* is likely to contain user-generated text."""
    lower = key.lower()
    # Fast reject — structural keys never contain PII worth scanning.
    if any(lower.endswith(suffix) for suffix in _STRUCTURAL_KEY_SUFFIXES):
        return False
    return any(sub in lower for sub in _CONTENT_KEY_SUBSTRINGS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class PIIScrubError(RuntimeError):
    """A batch of a PII-enabled project could not be scrubbed. Never carries a value."""


def _unscannable_reason(exc: BaseException) -> str | None:
    """Why this input fails on every attempt, or None for a failure a retry may clear.

    * spaCy ``ValueError`` E088: the text is longer than the model's limit.
    * ``UnicodeError``: the value cannot be encoded or decoded.
    """
    if isinstance(exc, UnicodeError):
        return "undecodable"
    if isinstance(exc, ValueError) and _SPACY_TEXT_TOO_LONG_CODE in str(exc):
        return "too_long"
    return None


def scrub_pii_in_string(text: str, *, strict: bool = False) -> str:
    """Analyse and anonymize PII in *text* using Presidio.

    Best effort by default: a failure returns *text* unchanged. With *strict*,
    no failure returns *text*: input that fails on every attempt (too long,
    undecodable) becomes ``UNSCANNABLE_VALUE``, and any other failure raises
    ``PIIScrubError`` so the caller can retry.
    """
    if not text or not _ensure_engines():
        return text
    try:
        results = _analyzer.analyze(
            text=text,
            language="en",
            entities=list(_ENTITY_OPERATORS.keys()),
        )
        if not results:
            return text
        anonymized = _anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators=_get_operator_config(),
        )
        return anonymized.text
    except Exception as exc:
        if strict:
            reason = _unscannable_reason(exc)
            if reason is not None:
                # A retry would fail the same way and drop the rest of the batch with
                # it: mask this value and keep the others. Neither the log nor the
                # error ever carries the value.
                logger.warning("pii_scrub_value_masked", reason=reason)
                return UNSCANNABLE_VALUE
            # `from None`: the original exception's text can quote the value, and a
            # chained cause travels with this error into logs and task failures.
            raise PIIScrubError(
                f"PII redaction failed ({type(exc).__name__}); the value is not stored."
            ) from None
        logger.warning("pii_scrub_string_failed", exc_info=True)
        return text


_MEDIA_PREFIXES = (
    "data:image/",
    "data:audio/",
    "data:video/",
    "data:application/octet-stream",
)


def scrub_pii_in_value(value: Any, *, strict: bool = False) -> Any:
    """Recursively scrub PII from *value*.

    Handles str (including JSON-encoded strings), dict, list.
    Skips binary/media data (base64 images, audio, video).
    *strict* is passed to ``scrub_pii_in_string``.
    """
    if isinstance(value, str):
        # Skip base64-encoded media data
        if value.startswith(_MEDIA_PREFIXES):
            return value
        # Attempt JSON parse — attribute values are often JSON-encoded.
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                scrubbed = scrub_pii_in_value(parsed, strict=strict)
                return json.dumps(scrubbed)
        except (json.JSONDecodeError, TypeError):
            pass
        return scrub_pii_in_string(value, strict=strict)
    if isinstance(value, dict):
        return {k: scrub_pii_in_value(v, strict=strict) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_pii_in_value(item, strict=strict) for item in value]
    return value


def scrub_span_attributes(attributes: dict, *, strict: bool = False) -> dict:
    """Walk *attributes* dict and scrub content-bearing keys."""
    scrubbed = {}
    for key, value in attributes.items():
        if _is_content_key(key):
            scrubbed[key] = scrub_pii_in_value(value, strict=strict)
        else:
            scrubbed[key] = value
    return scrubbed


def scrub_pii_in_span_batch(
    otel_data_list: list[dict],
    project_pii_settings: dict[str, bool],
) -> None:
    """Scrub PII in-place across a batch of span dicts.

    Only scrubs spans whose project has PII redaction enabled, and never keeps
    one of their raw values. A value that can never be scanned is replaced with
    ``UNSCANNABLE_VALUE`` and the batch goes on. Unavailable engines and any
    other scrub failure raise ``PIIScrubError``, so the caller stores nothing of
    the batch and can retry it.
    """
    pii_enabled = any(
        span_data.get("project_name")
        and project_pii_settings.get(span_data["project_name"], False)
        for span_data in otel_data_list
    )
    if pii_enabled and not _ensure_engines():
        raise PIIScrubError(
            "PII redaction is enabled, but its engines are unavailable. "
            "Rebuild the backend image with EXTRAS=pii."
        )

    for span_data in otel_data_list:
        project_name = span_data.get("project_name")
        if not project_name or not project_pii_settings.get(project_name, False):
            continue
        # Scrub attributes
        attrs = span_data.get("attributes")
        if isinstance(attrs, dict):
            span_data["attributes"] = scrub_span_attributes(attrs, strict=True)
        # Scrub resource attributes (may also contain content)
        res_attrs = span_data.get("resource_attributes")
        if isinstance(res_attrs, dict):
            span_data["resource_attributes"] = scrub_span_attributes(
                res_attrs, strict=True
            )
