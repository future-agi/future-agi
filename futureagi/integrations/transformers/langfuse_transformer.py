import logging
import uuid
from typing import Any

from integrations.transformers.base import BaseTraceTransformer, register_transformer
from tracer.utils.adapters.base import (
    extract_query,
    flatten_input_messages,
    flatten_output_messages,
    set_io_value,
)
from tracer.utils.adapters.base import guess_provider as _guess_provider
from tracer.utils.langfuse_upsert import parse_langfuse_timestamp
from tracer.utils.otel import SpanAttributes

# Langfuse observation type -> FutureAGI observation_type
OBSERVATION_TYPE_MAP = {
    "SPAN": "chain",
    "GENERATION": "llm",
    "EVENT": "unknown",
}

# Langfuse observation type -> FutureAGI fi.span.kind
SPAN_KIND_MAP = {
    "GENERATION": "LLM",
    "SPAN": "CHAIN",
    "EVENT": "UNKNOWN",
}


class LangfuseTransformer(BaseTraceTransformer):
    """Transforms Langfuse data into FutureAGI model field dicts."""

    def transform_trace(
        self,
        raw_trace: dict[str, Any],
        project_id: str,
    ) -> dict[str, Any]:
        """Transform a Langfuse trace into FutureAGI Trace fields."""
        if "id" not in raw_trace:
            raise ValueError("Trace missing required 'id' field")

        metadata = raw_trace.get("metadata") or {}
        if isinstance(metadata, dict):
            metadata = {**metadata, "integration_source": "langfuse"}
        else:
            metadata = {"integration_source": "langfuse", "original_metadata": metadata}

        metadata.pop("fi.external.langfuse", None)
        tags = list(raw_trace.get("tags") or [])
        if "langfuse" not in tags:
            tags.append("langfuse")

        # Trace name: Langfuse OTEL integration sometimes absorbs the root
        # span into the trace record, leaving the trace name empty.
        # Fall back to: root observation name (no parent) → earliest observation.
        trace_name = raw_trace.get("name") or ""
        if not trace_name:
            observations = raw_trace.get("observations") or []
            # Try root observation first (no parentObservationId)
            for obs in observations:
                if not obs.get("parentObservationId"):
                    trace_name = obs.get("name") or ""
                    if trace_name:
                        break
            # If still empty, use earliest observation by startTime
            if not trace_name and observations:
                sorted_obs = sorted(
                    observations,
                    key=lambda o: o.get("startTime") or "",
                )
                trace_name = sorted_obs[0].get("name") or ""

        return {
            "external_id": raw_trace["id"],
            "project_id": project_id,
            "name": trace_name,
            "input": raw_trace.get("input"),
            "output": raw_trace.get("output"),
            "metadata": metadata,
            "tags": tags,
            "user_id": raw_trace.get("userId"),
            "session_id": raw_trace.get("sessionId"),
        }

    def transform_observations(
        self,
        raw_trace: dict[str, Any],
        trace_id: str,
        project_id: str,
        *,
        source_origin: str | None = None,
        source_project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Transform observations; only connector-owned inputs attest external lineage."""
        observations = raw_trace.get("observations") or []
        result = []

        for obs in observations:
            if "id" not in obs:
                continue  # Skip observations without an ID

            obs_type = OBSERVATION_TYPE_MAP.get(obs.get("type", "").upper(), "unknown")

            # Provider detection from model name
            model_name = obs.get("model") or ""
            provider = _guess_provider(model_name) if model_name else ""

            # Parse timestamps
            start_time = self._parse_timestamp(obs.get("startTime"))
            end_time = self._parse_timestamp(obs.get("endTime"))

            # Token counts from usageDetails
            # Note: use `is None` checks because 0 is a valid token count
            usage = obs.get("usageDetails") or {}
            prompt_tokens = usage.get("input")
            if prompt_tokens is None:
                prompt_tokens = usage.get("promptTokens")
            if prompt_tokens is None:
                prompt_tokens = 0

            completion_tokens = usage.get("output")
            if completion_tokens is None:
                completion_tokens = usage.get("completionTokens")
            if completion_tokens is None:
                completion_tokens = 0

            total_tokens = usage.get("total")
            if total_tokens is None:
                total_tokens = usage.get("totalTokens")
            if total_tokens is None:
                total_tokens = prompt_tokens + completion_tokens

            # Latency: Langfuse uses seconds, FutureAGI uses milliseconds
            latency_seconds = obs.get("latency") or 0
            latency_ms = int(latency_seconds * 1000) if latency_seconds else 0

            # Cost: Langfuse returns calculatedTotalCost or costDetails.total
            cost = obs.get("calculatedTotalCost")
            if cost is None:
                cost_details = obs.get("costDetails") or {}
                cost = cost_details.get("total", 0)
            cost = cost or 0

            # Build eval_attributes matching traceAI SDK convention
            eval_attrs = self._build_eval_attributes(
                obs,
                obs_type,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                trace_metadata=raw_trace.get("metadata"),
                trace_tags=raw_trace.get("tags"),
                trace_user_id=raw_trace.get("userId"),
                trace_session_id=raw_trace.get("sessionId"),
            )

            raw_metadata = obs.get("metadata") or {}
            metadata = (
                dict(raw_metadata)
                if isinstance(raw_metadata, dict)
                else {"original_metadata": raw_metadata}
            )
            # A posted metadata field is not a connector attestation. The real
            # connector supplies its configured host and provider-returned project ID.
            metadata.pop("fi.external.langfuse", None)
            if source_origin is not None and source_project_id is not None:
                from model_hub.services.skills_hub.r3.external_trace_capture import (
                    retained_external_lineage,
                )

                try:
                    metadata["fi.external.langfuse"] = retained_external_lineage(
                        origin=source_origin,
                        project_id=source_project_id,
                        trace_id=raw_trace["id"],
                        observation_id=obs["id"],
                    )
                except ValueError:
                    # Fail closed on provenance, not on existing trace ingestion.
                    logging.getLogger(__name__).warning(
                        "Langfuse source identity cannot supply a navigation link",
                        exc_info=True,
                    )
                    metadata["fi.external.langfuse.status"] = (
                        "unsupported_source_identity"
                    )

            span_dict = {
                "id": obs["id"],
                "trace_id": trace_id,
                "project_id": project_id,
                "parent_span_id": obs.get("parentObservationId") or None,
                "observation_type": obs_type,
                "name": obs.get("name") or "",
                "start_time": start_time,
                "end_time": end_time,
                "input": obs.get("input"),
                "output": obs.get("output"),
                "metadata": metadata,
                "model": model_name,
                "provider": provider,
                "model_parameters": obs.get("modelParameters") or {},
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "latency_ms": latency_ms,
                "cost": cost,
                "status": self._map_level_to_status(obs.get("level")),
                "span_attributes": eval_attrs,
                "eval_attributes": eval_attrs,
            }
            result.append(span_dict)

        return result

    def transform_scores(
        self,
        raw_trace: dict[str, Any],
        trace_id: str,
    ) -> list[dict[str, Any]]:
        """Transform Langfuse scores into FutureAGI EvalLogger dicts."""
        scores = raw_trace.get("scores") or []
        result = []

        for score in scores:
            data_type = (score.get("dataType") or "").upper()

            eval_dict = {
                "langfuse_score_id": score.get("id", str(uuid.uuid4())),
                "trace_id": trace_id,
                "observation_id": score.get("observationId"),
                "eval_type_id": score.get("name") or "",
                "output_float": score.get("value"),
                "output_str": score.get("stringValue") or "",
                "eval_explanation": score.get("comment") or "",
                "output_bool": (
                    score.get("value") == 1 if data_type == "BOOLEAN" else None
                ),
            }
            result.append(eval_dict)

        return result

    @staticmethod
    def _build_eval_attributes(
        obs: dict,
        obs_type: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        *,
        trace_metadata: dict | None = None,
        trace_tags: list | None = None,
        trace_user_id: str | None = None,
        trace_session_id: str | None = None,
    ) -> dict:
        """Build eval_attributes matching traceAI SDK convention.

        Uses shared utilities from tracer.utils.adapters.base for message
        flattening, IO value setting, and query extraction (DRY).
        """
        attrs: dict[str, Any] = {}
        raw_type = obs.get("type", "").upper()
        obs_input = obs.get("input")
        obs_output = obs.get("output")

        # -- fi.span.kind (all span types) --
        attrs[SpanAttributes.FI_SPAN_KIND] = SPAN_KIND_MAP.get(raw_type, "UNKNOWN")

        # -- Context attributes (all span types) --
        if trace_session_id:
            attrs[SpanAttributes.SESSION_ID] = trace_session_id
        if trace_user_id:
            attrs[SpanAttributes.USER_ID] = trace_user_id
        if trace_tags:
            attrs[SpanAttributes.TAG_TAGS] = trace_tags
        if trace_metadata:
            attrs[SpanAttributes.METADATA] = trace_metadata

        # -- input.value / output.value + mime types (all span types) --
        set_io_value(attrs, "input", obs_input)
        set_io_value(attrs, "output", obs_output)

        # -- LLM-specific attributes --
        if obs_type == "llm":
            model = obs.get("model") or ""

            if model:
                attrs[SpanAttributes.LLM_MODEL_NAME] = model
                provider = _guess_provider(model)
                if provider:
                    attrs[SpanAttributes.LLM_PROVIDER] = provider
                    attrs[SpanAttributes.LLM_SYSTEM] = provider

            attrs[SpanAttributes.LLM_TOKEN_COUNT_PROMPT] = prompt_tokens
            attrs[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION] = completion_tokens
            attrs[SpanAttributes.LLM_TOKEN_COUNT_TOTAL] = total_tokens

            model_params = obs.get("modelParameters") or {}
            if model_params:
                attrs[SpanAttributes.LLM_INVOCATION_PARAMETERS] = model_params

            # Flatten messages using shared utilities
            flatten_input_messages(obs_input, attrs)
            flatten_output_messages(obs_output, attrs)
            extract_query(obs_input, attrs)

            if obs_input is not None:
                attrs[SpanAttributes.RAW_INPUT] = obs_input
            if obs_output is not None:
                attrs[SpanAttributes.RAW_OUTPUT] = obs_output

        return attrs

    @staticmethod
    def _parse_timestamp(ts):
        """Parse an ISO 8601 timestamp string.

        Delegates to the shared ``parse_langfuse_timestamp`` utility.
        """
        return parse_langfuse_timestamp(ts)

    @staticmethod
    def _map_level_to_status(level: str | None) -> str:
        """Map Langfuse observation level to FutureAGI status.

        Langfuse observations that complete without errors may omit the
        ``level`` field entirely.  Default to ``OK`` since their presence
        in the trace response implies successful completion.
        """
        if not level:
            return "OK"
        level = level.upper()
        if level == "ERROR":
            return "ERROR"
        if level in ("DEFAULT", "DEBUG", "WARNING"):
            return "OK"
        return "UNSET"


# Self-register on module import
_langfuse_transformer = LangfuseTransformer()
register_transformer("langfuse", _langfuse_transformer)
