"""
PII Scrubber — Presidio-powered server-side PII redaction.

Replaces detected PII with <ENTITY_TYPE> tokens (e.g., <EMAIL_ADDRESS>).
Low-level helpers preserve their historical fail-open behavior.  Batch
ingestion fails closed when a project explicitly enables PII redaction but
the configured Presidio engines cannot be initialized.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
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


# Names and places are personal data once a conversation becomes test text,
# though ingestion's opt-in scrubbing leaves them in the trace.
_FINDABLE_ENTITIES = (*_ENTITY_OPERATORS, "PERSON", "LOCATION")

# What find_pii_basic recognises when Presidio is not installed: values with a
# fixed shape, earlier patterns winning an overlap. Names and addresses have no
# fixed shape, so it cannot find them.
_BASIC_PATTERNS = (
    ("EMAIL_ADDRESS", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    (
        "API_KEY",
        re.compile(r"\b(?:sk|pk)[-_](?:live|test|prod)[-_][A-Za-z0-9]{20,}\b"),
    ),
    ("CREDIT_CARD", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")),
    ("US_SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    (
        "IP_ADDRESS",
        re.compile(
            r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])"
        ),
    ),
    (
        "PHONE_NUMBER",
        re.compile(
            r"(?<![\w+])(?:\+\d{1,3}[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}(?!\w)"
        ),
    ),
)


@dataclass(frozen=True)
class PiiFinding:
    """One value redaction would replace: where it sits in the text, what kind
    of value it is, and the token that would replace it."""

    entity: str
    start: int
    end: int
    value: str
    replacement: str


def find_pii(texts: Sequence[str]) -> list[list[PiiFinding]] | None:
    """The personal data Presidio finds in each of *texts*, in order and
    without overlaps (the likelier reading of a span wins), analysed in one
    batch. ``None`` when nothing could look: the engines are missing or the
    analysis failed. Unlike the scrubbers, this does not fail open, so a caller
    can tell "nothing found" from "not looked at"."""
    if not any(texts):
        return [[] for _ in texts]
    if not _ensure_engines():
        return None
    try:
        from presidio_analyzer import BatchAnalyzerEngine

        results = list(
            BatchAnalyzerEngine(analyzer_engine=_analyzer).analyze_iterator(
                list(texts),
                language="en",
                entities=list(_FINDABLE_ENTITIES),
                batch_size=64,
            )
        )
    except Exception:
        logger.exception(
            "pii_find_failed", values=len(texts), chars=sum(len(t) for t in texts)
        )
        return None
    return [
        _without_overlaps(
            text, ((r.entity_type, r.start, r.end, r.score) for r in found)
        )
        for text, found in zip(texts, results, strict=True)
    ]


def find_pii_basic(texts: Sequence[str]) -> list[list[PiiFinding]]:
    """What a fixed-shape match finds in each of *texts*: emails, API keys,
    card numbers (that pass the Luhn check), US SSNs, IP addresses and phone
    numbers. For a server without Presidio; it finds no names or addresses."""
    found = []
    for text in texts:
        hits = []
        for rank, (entity, pattern) in enumerate(reversed(_BASIC_PATTERNS)):
            for match in pattern.finditer(text or ""):
                if entity == "CREDIT_CARD" and not _luhn(match.group()):
                    continue
                hits.append((entity, match.start(), match.end(), float(rank)))
        found.append(_without_overlaps(text or "", hits))
    return found


def redact(text: str, findings: Sequence[PiiFinding]) -> str:
    """``text`` with each finding replaced by its token."""
    for finding in sorted(findings, key=lambda f: f.start, reverse=True):
        text = text[: finding.start] + finding.replacement + text[finding.end :]
    return text


def _luhn(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    checksum = sum(digits[-1::-2]) + sum(sum(divmod(2 * d, 10)) for d in digits[-2::-2])
    return checksum % 10 == 0


def _without_overlaps(text: str, hits) -> list[PiiFinding]:
    """``(entity, start, end, score)`` hits as findings, the higher score
    winning where two overlap."""
    kept: list[PiiFinding] = []
    for entity, start, end, _ in sorted(hits, key=lambda h: (-h[3], h[1], h[2])):
        if any(start < f.end and f.start < end for f in kept):
            continue
        kept.append(
            PiiFinding(
                entity=entity,
                start=start,
                end=end,
                value=text[start:end],
                replacement=_TOKEN_NAMES.get(entity, f"<{entity}>"),
            )
        )
    return sorted(kept, key=lambda f: f.start)


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
    pii_enabled = any(
        span_data.get("project_name")
        and project_pii_settings.get(span_data["project_name"], False)
        for span_data in otel_data_list
    )
    if pii_enabled and not _ensure_engines():
        raise RuntimeError(
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
            span_data["attributes"] = scrub_span_attributes(attrs)
        # Scrub resource attributes (may also contain content)
        res_attrs = span_data.get("resource_attributes")
        if isinstance(res_attrs, dict):
            span_data["resource_attributes"] = scrub_span_attributes(res_attrs)
