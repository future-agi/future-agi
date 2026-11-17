"""
PII Scrubber — Presidio-powered server-side PII redaction.

Replaces detected PII with <ENTITY_TYPE> tokens (e.g., <EMAIL_ADDRESS>).
Low-level helpers preserve their historical fail-open behavior.  Batch
ingestion fails closed when a project explicitly enables PII redaction but
the configured Presidio engines cannot be initialized.
"""

import json
from typing import Any

import structlog

from tracer.utils.semantic_conventions import AttributeAliases

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

        from presidio_analyzer.predefined_recognizers import EmailRecognizer
        from tldextract import TLDExtract

        # Presidio 2.2's EmailRecognizer has no extractor injection option.
        # Keep its patterns, context and fqdn validation, but use tldextract's
        # bundled PSL snapshot without network fetches or a shared disk cache.
        # Replace only this analyzer's recognizer, never the global extractor.
        offline_extract = TLDExtract(suffix_list_urls=(), cache_dir=None)

        class OfflineEmailRecognizer(EmailRecognizer):
            def validate_result(self, pattern_text: str) -> bool:
                return offline_extract(pattern_text).fqdn != ""

        analyzer.registry.remove_recognizer("EmailRecognizer", language="en")
        analyzer.registry.add_recognizer(OfflineEmailRecognizer(name="EmailRecognizer"))
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
    """Identify inputs that cannot be scanned even after a retry."""
    if isinstance(exc, UnicodeError):
        return "undecodable"
    if isinstance(exc, ValueError) and _SPACY_TEXT_TOO_LONG_CODE in str(exc):
        return "too_long"
    return None


def scrub_pii_in_string(text: str, *, strict: bool = False) -> str:
    """Scrub text, preserving best-effort defaults for explicit legacy callers.

    In strict batch mode, permanently unscannable text becomes a marker and
    other scrub failures raise without carrying the original content.
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
                logger.warning("pii_scrub_value_masked", reason=reason)
                return UNSCANNABLE_VALUE
            # Exception text may contain the value; do not chain it into logs.
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


# hide_io is intentionally independent of the historical mask_pii heuristic.
# Only non-content fields consumed by otel.py, the adapters and normalization
# may retain strings. No prefix/suffix exemptions: a new payload alias is hidden
# by default, including names such as metadata.query or custom.model.
_HIDE_IO_STRING_KEYS = frozenset(
    alias
    for aliases in (
        AttributeAliases.MODEL_NAME, AttributeAliases.PROVIDER,
        AttributeAliases.SPAN_KIND, AttributeAliases.OPERATION_NAME,
        AttributeAliases.INPUT_TOKENS, AttributeAliases.OUTPUT_TOKENS,
        AttributeAliases.TOTAL_TOKENS, AttributeAliases.CACHE_READ_TOKENS,
        AttributeAliases.CACHE_WRITE_TOKENS, AttributeAliases.COST_INPUT,
        AttributeAliases.COST_OUTPUT, AttributeAliases.COST_TOTAL,
        AttributeAliases.COST_CACHE_READ, AttributeAliases.COST_CACHE_WRITE,
        AttributeAliases.SESSION_ID, AttributeAliases.USER_ID,
        AttributeAliases.TOOL_NAME, AttributeAliases.TOOL_TYPE,
        AttributeAliases.TOOL_CALL_ID, AttributeAliases.RESPONSE_ID,
        AttributeAliases.FINISH_REASONS, AttributeAliases.OUTPUT_TYPE,
        AttributeAliases.AGENT_ID, AttributeAliases.AGENT_NAME,
        AttributeAliases.AGENT_GRAPH_NODE_ID, AttributeAliases.AGENT_GRAPH_NODE_NAME,
        AttributeAliases.AGENT_GRAPH_PARENT_NODE_ID,
        AttributeAliases.PROMPT_TEMPLATE_NAME, AttributeAliases.PROMPT_TEMPLATE_VERSION,
        AttributeAliases.PROMPT_TEMPLATE_LABEL, AttributeAliases.TOKEN_TYPE,
        AttributeAliases.INPUT_MIME_TYPE, AttributeAliases.OUTPUT_MIME_TYPE,
        AttributeAliases.ERROR_TYPE, AttributeAliases.CLIENT_OPERATION_DURATION,
        AttributeAliases.TIME_TO_FIRST_TOKEN, AttributeAliases.TIME_PER_OUTPUT_TOKEN,
        AttributeAliases.SERVER_QUEUE_TIME, AttributeAliases.RERANKER_MODEL,
        AttributeAliases.VOICE_CALL_DURATION_SECS,
        AttributeAliases.VOICE_LATENCY_MODEL_AVG_MS, AttributeAliases.VOICE_LATENCY_VOICE_AVG_MS,
        AttributeAliases.VOICE_LATENCY_TRANSCRIBER_AVG_MS, AttributeAliases.VOICE_LATENCY_TURN_AVG_MS,
        AttributeAliases.VOICE_LATENCY_TTFB_MS, AttributeAliases.VOICE_COST_TOTAL,
        AttributeAliases.VOICE_COST_STT, AttributeAliases.VOICE_COST_TTS,
        AttributeAliases.VOICE_COST_LLM, AttributeAliases.VOICE_COST_TELEPHONY,
        AttributeAliases.PROMPT_ID, AttributeAliases.PROMPT_VENDOR,
        AttributeAliases.EVAL_NAME, AttributeAliases.EVAL_SCORE,
        AttributeAliases.EVAL_LABEL, AttributeAliases.EVAL_TARGET_SPAN_ID,
    )
    for alias in aliases
) | frozenset({
    # Identity and converter routing.
    "trace_id", "span_id", "parent_id", "parent_span_id", "session_id",
    "trace.id", "span.id", "span.name", "span.kind", "span.type",
    "project_name", "project_type", "project_id", "project_version_name",
    "project_version_id", "session_name", "user.id.type", "user.id.hash",
    "workflow.id", "workflow.name", "gen_ai.trace.source",
    "tag.tags",  # Converter tags and tag-based filtering labels.
    "gen_ai.data_source.id", "gen_ai.prompt.name", "gen_ai.prompt.id",
    "gen_ai.prompt.vendor", "llm.prompt_template.id", "llm.prompt_template.version",
    "llm.provider", "embedding.model_name", "traceloop.span.kind",
    "traceloop.entity.name", "traceloop.entity.version",
    "traceloop.prompt.key", "traceloop.prompt.version",
    "langfuse.observation.type", "langfuse.observation.model.name",
    "langfuse.observation.prompt.name", "langfuse.observation.prompt.version",
    "langfuse.user.id", "langfuse.session.id",
    # Accounting/enum fields must survive a second normalize_span in conversion.
    "fi.normalization.profile", "fi.source.operation", "fi.observation.category",
    "fi.usage.contribution", "fi.usage.scope", "fi.cost.kind",
    "gen_ai.usage.prompt_tokens", "gen_ai.usage.completion_tokens",
    "status.code", "otel.status_code", "http.status_code", "http.response.status_code",
    "http.method", "http.request.method", "exception.type",
    "latency", "latency_ms", "start_time", "end_time", "duration",
    "gen_ai.voice.call_id", "gen_ai.voice.provider", "gen_ai.voice.channel_type",
    "gen_ai.voice.stt.model", "gen_ai.voice.stt.provider", "gen_ai.voice.stt.language",
    "gen_ai.voice.tts.model", "gen_ai.voice.tts.provider", "gen_ai.voice.tts.voice_id",
    # Resource identity is enumerated, not a blanket host/process/k8s exemption.
    "service.name", "service.namespace", "service.version", "service.instance.id",
    "telemetry.sdk.name", "telemetry.sdk.language", "telemetry.sdk.version",
    "telemetry.distro.name", "telemetry.distro.version",
    "host.id", "host.name", "host.type", "host.arch", "host.image.id", "host.image.name",
    "host.image.version", "process.pid", "process.parent_pid", "process.executable.name",
    "process.runtime.name", "process.runtime.version",
    "container.id", "container.name", "container.image.name", "container.image.tag",
    "container.image.id", "container.image.tags", "k8s.cluster.name", "k8s.cluster.uid",
    "k8s.namespace.name", "k8s.node.name", "k8s.node.uid", "k8s.pod.name", "k8s.pod.uid",
    "k8s.container.name", "k8s.deployment.name", "k8s.deployment.uid",
    "k8s.statefulset.name", "k8s.statefulset.uid", "k8s.daemonset.name", "k8s.daemonset.uid",
    "k8s.replicaset.name", "k8s.replicaset.uid", "k8s.job.name", "k8s.job.uid",
    "k8s.cronjob.name", "k8s.cronjob.uid",
    # Indexed fields generated by adapters/base.py; '*' matches an index ONLY.
    "message.role", "message.tool_call_id", "message.function_call_name",
    "llm.input_messages.*.message.role", "llm.output_messages.*.message.role",
    "llm.input_messages.*.message.tool_call_id", "llm.output_messages.*.message.tool_call_id",
    "llm.input_messages.*.message.contents.*.message_content.type",
    "llm.output_messages.*.message.contents.*.message_content.type",
    "llm.input_messages.*.message.tool_calls.*.tool_call.id",
    "llm.output_messages.*.message.tool_calls.*.tool_call.id",
    "llm.input_messages.*.message.tool_calls.*.tool_call.function.name",
    "llm.output_messages.*.message.tool_calls.*.tool_call.function.name",
    "gen_ai.prompt.*.role", "gen_ai.completion.*.role",
    "gen_ai.input.messages.*.role", "gen_ai.output.messages.*.role",
    "gen_ai.output.messages.*.finish_reason",
    "retrieval.documents.*.document.id",
})

_HIDE_IO_EVENT_NAMES = frozenset({
    "exception", "message", "gen_ai.system.message", "gen_ai.user.message",
    "gen_ai.assistant.message", "gen_ai.tool.message", "gen_ai.choice",
    "gen_ai.client.inference.operation.details", "gen_ai.evaluation.result",
})


def _hide_io_value(value: Any, path: str = "", key: str = "") -> Any:
    """Keep container shape and numbers/bools; allow strings by exact semantic key.

    JSON containers are inspected even under an allowed key. An allowed parent
    never exempts arbitrary children. Lists carry their index in the semantic
    path, while scalar lists can retain allowed enum values (finish_reasons).
    """
    if isinstance(value, dict):
        return {
            child: _hide_io_value(item, f"{path}.{child}" if path else child, child)
            for child, item in value.items()
        }
    if isinstance(value, list):
        return [
            _hide_io_value(item, f"{path}.{index}", key)
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(_hide_io_value(parsed, path, key))
        normalized = ".".join("*" if part.isdigit() else part for part in path.split("."))
        return value if key in _HIDE_IO_STRING_KEYS or normalized in _HIDE_IO_STRING_KEYS else "[hidden]"
    return value


def hide_span_io(attributes: dict) -> dict:
    return _hide_io_value(attributes)


def _redaction_mode(setting: bool | dict) -> str | None:
    if isinstance(setting, dict):
        if not setting.get("enabled", False):
            return None
        return "hide_io" if setting.get("mode") == "hide_io" else "mask_pii"
    return "mask_pii" if setting else None


def scrub_pii_in_span_batch(
    otel_data_list: list[dict],
    project_pii_settings: dict[str | tuple[str, str | None], bool | dict],
) -> None:
    """Scrub PII in-place across a batch of span dicts.

    Only scrubs spans whose project has PII redaction enabled.
    hide_io replaces content with a fixed marker without invoking Presidio;
    mask_pii uses strict scrubbing: unscannable values become a fixed marker,
    and unavailable engines or transient failures reject the batch before
    storage. Disabled projects remain unchanged.
    """
    project_modes = {
        name: _redaction_mode(setting)
        for name, setting in project_pii_settings.items()
    }
    def span_mode(span_data):
        name = span_data.get("project_name")
        # Ingestion uses (name, trace type); legacy explicit callers may still
        # supply name-only settings. Never fall back across two trace types.
        identity = (name, span_data.get("project_type"))
        return project_modes.get(identity, project_modes.get(name))

    needs_presidio = any(span_mode(span) == "mask_pii" for span in otel_data_list)
    if needs_presidio and not _ensure_engines():
        raise PIIScrubError(
            "PII redaction is enabled, but its engines are unavailable. "
            "Rebuild the backend image with EXTRAS=pii."
        )

    for span_data in otel_data_list:
        mode = span_mode(span_data)
        if mode is None:
            continue
        for field in ("attributes", "resource_attributes"):
            attrs = span_data.get(field)
            if isinstance(attrs, dict):
                span_data[field] = (
                    hide_span_io(attrs)
                    if mode == "hide_io"
                    else scrub_span_attributes(attrs, strict=True)
                )
        if mode == "hide_io":
            # The OTLP parser nests resource attributes here. Retain support for
            # the flat legacy field above without changing mask_pii behavior.
            resource = span_data.get("resource")
            if isinstance(resource, dict) and isinstance(resource.get("attributes"), dict):
                resource["attributes"] = hide_span_io(resource["attributes"])
            if isinstance(span_data.get("status_message"), str):
                span_data["status_message"] = "[hidden]"
            if "metadata" in span_data:
                span_data["metadata"] = _hide_io_value(span_data["metadata"])
            for event in span_data.get("events") or []:
                if not isinstance(event, dict):
                    continue
                if event.get("name") not in _HIDE_IO_EVENT_NAMES:
                    event["name"] = "[hidden]"
                if isinstance(event.get("attributes"), dict):
                    event["attributes"] = hide_span_io(event["attributes"])
