"""
PII Scrubber — Presidio-powered server-side PII redaction.

Replaces detected PII with <ENTITY_TYPE> tokens (e.g., <EMAIL_ADDRESS>).
Fail-open: any exception is caught and logged, original value preserved.
"""

import json
import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Lazy singleton initialisation — engines are created on first use so that
# import-time failures don't block startup when Presidio/spaCy isn't installed.
# ---------------------------------------------------------------------------
_analyzer = None
_anonymizer = None
_INIT_FAILED_AT: float | None = None
_RETRY_INTERVAL = 300  # seconds


# Retry initialization after a transient Presidio failure.
def _ensure_engines() -> bool:
    """Lazily initialise Presidio engines. Returns True if ready."""
    global _analyzer, _anonymizer, _INIT_FAILED_AT  # noqa: PLW0603
    if _analyzer is not None and _anonymizer is not None:
        return True
    if _INIT_FAILED_AT is not None:
        if time.monotonic() - _INIT_FAILED_AT < _RETRY_INTERVAL:
            return False
    try:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_anonymizer import AnonymizerEngine
        from presidio_anonymizer.entities import OperatorConfig

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
        _analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        _analyzer.registry.add_recognizer(api_key_recognizer)
        _anonymizer = AnonymizerEngine()
        _INIT_FAILED_AT = None

        logger.info("pii_scrubber_engines_initialized")
        return True
    except Exception:
        _INIT_FAILED_AT = time.monotonic()
        logger.warning("pii_scrubber_init_failed", exc_info=True)
        return False


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


def scrub_pii_in_string(text: str) -> str:
    """Analyse and anonymize PII in *text* using Presidio."""
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
    except Exception:
        logger.warning("pii_scrub_string_failed", exc_info=True)
        return text


_MEDIA_PREFIXES = (
    "data:image/",
    "data:audio/",
    "data:video/",
    "data:application/octet-stream",
)


def scrub_pii_in_value(value: Any) -> Any:
    """Recursively scrub PII from *value*.

    Handles str (including JSON-encoded strings), dict, list.
    Skips binary/media data (base64 images, audio, video).
    """
    if isinstance(value, str):
        # Skip base64-encoded media data
        if value.startswith(_MEDIA_PREFIXES):
            return value
        # Attempt JSON parse — attribute values are often JSON-encoded.
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                scrubbed = scrub_pii_in_value(parsed)
                return json.dumps(scrubbed)
        except (json.JSONDecodeError, TypeError):
            pass
        return scrub_pii_in_string(value)
    if isinstance(value, dict):
        return {k: scrub_pii_in_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_pii_in_value(item) for item in value]
    return value


def scrub_span_attributes(attributes: dict) -> dict:
    """Walk *attributes* dict and scrub content-bearing keys."""
    scrubbed = {}
    for key, value in attributes.items():
        if _is_content_key(key):
            scrubbed[key] = scrub_pii_in_value(value)
        else:
            scrubbed[key] = value
    return scrubbed


def scrub_pii_in_span_batch(
    otel_data_list: list[dict],
    project_pii_settings: dict[str, bool],
) -> None:
    """Scrub PII in-place across a batch of span dicts.

    Only scrubs spans whose project has PII redaction enabled.
    """
    for span_data in otel_data_list:
        project_name = span_data.get("project_name")
        if not project_name or not project_pii_settings.get(project_name, False):
            continue
        # Scrub attributes
        attrs = span_data.get("attributes")
        if isinstance(attrs, dict):
            span_data["attributes"] = scrub_span_attributes(attrs)
        # Scrub resource attributes (may also contain content)
        res_attrs = span_data.get("resource_attributes")
        if isinstance(res_attrs, dict):
            span_data["resource_attributes"] = scrub_span_attributes(res_attrs)
