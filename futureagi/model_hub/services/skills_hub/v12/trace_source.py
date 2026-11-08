"""Authorized physical-span reads and allowlisted request/tool-result extraction."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from django.utils import timezone

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.v12.limits import MAX_CAPTURE_BYTES
from tracer.models.project import Project
from tracer.services.analysis_retention import (
    AnalysisSourceExpired,
    require_retained_source,
)
from tracer.services.clickhouse.application_read_policy import application_read_settings
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService


def normalize_trace_ref(trace_ref: dict) -> dict:
    if not isinstance(trace_ref, dict) or set(trace_ref) != {
        "project_id",
        "trace_id",
        "span_id",
        "start_time",
    }:
        raise SkillsHubError(
            "capture_unsafe", "Use the complete physical trace reference.", status=422
        )
    try:
        project_id = str(UUID(str(trace_ref["project_id"])))
        stamp = trace_ref["start_time"]
        if isinstance(stamp, str):
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not isinstance(stamp, datetime) or not timezone.is_aware(stamp):
            raise ValueError
        for key in ("trace_id", "span_id"):
            value = trace_ref[key]
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 200
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError
    except (TypeError, ValueError):
        raise SkillsHubError(
            "capture_unsafe", "Invalid physical trace reference.", status=422
        ) from None
    return {
        "project_id": project_id,
        "trace_id": trace_ref["trace_id"],
        "span_id": trace_ref["span_id"],
        "start_time": stamp.astimezone(UTC).isoformat(),
    }


def authorize_trace_source(*, principal: SkillPrincipal, trace_ref: dict) -> Project:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=False)
    ref = normalize_trace_ref(trace_ref)
    # Simulation-owned projects are excluded by the existing project selector.
    # No shared-link token or default-workspace fallback confers capture authority.
    project = Project.no_workspace_objects.filter(
        pk=ref["project_id"],
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        deleted=False,
    ).first()
    if project is None:
        raise SkillsHubError(
            "resource_not_found", "Trace source not found.", status=404
        )
    try:
        require_retained_source(
            principal.scope.organization_id, datetime.fromisoformat(ref["start_time"])
        )
    except AnalysisSourceExpired as exc:
        raise SkillsHubError(
            "capture_expired", "The source trace is no longer retained.", status=410
        ) from exc
    return project


def _decode(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            return value
    return value


def _text_content(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        # Never copy reasoning/thinking blocks, image bodies or arbitrary objects.
        parts = [
            item["text"]
            for item in value
            if isinstance(item, dict)
            and item.get("type") in {"text", "input_text"}
            and isinstance(item.get("text"), str)
        ]
        return "\n".join(parts) if parts else None
    return None


def extract_segments(row: dict) -> list[dict]:
    value = _decode(row.get("input"))
    entries = []
    if isinstance(value, str):
        entries.append(("request", value))
    elif isinstance(value, (dict, list)):
        messages = value.get("messages") if isinstance(value, dict) else value
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict) or message.get("role") not in {
                    "user",
                    "tool",
                }:
                    continue
                text = _text_content(message.get("content"))
                if text:
                    entries.append(
                        (
                            "request" if message["role"] == "user" else "tool_result",
                            text,
                        )
                    )
        elif isinstance(value, dict) and isinstance(value.get("request"), str):
            entries.append(("request", value["request"]))
    if str(row.get("observation_type", "")).lower() == "tool":
        text = _text_content(_decode(row.get("output")))
        if text:
            entries.append(("tool_result", text))
    if (
        len(entries) > 200
        or sum(len(text.encode("utf-8")) for _, text in entries) > MAX_CAPTURE_BYTES
    ):
        raise SkillsHubError(
            "payload_too_large",
            "Selected source text exceeds the capture bound.",
            status=413,
        )
    return [
        {
            "id": canonical_digest(
                {"kind": kind, "index": index, "sha256": file_sha256(text)}
            ),
            "kind": kind,
            "text": text,
            "sha256": file_sha256(text),
        }
        for index, (kind, text) in enumerate(entries)
    ]


def read_trace_source(*, principal: SkillPrincipal, trace_ref: dict) -> dict:
    """Read FINAL winners before matching mutable start_time; ambiguity fails closed.

    The frozen four-field TraceRef cannot distinguish service/observation aliases.
    We resolve those physical keys only when exactly one winner exists, then retain
    the six-key identity and its version. Never pick the first same-name span.
    """
    ref = normalize_trace_ref(trace_ref)
    project = authorize_trace_source(principal=principal, trace_ref=ref)
    stamp = datetime.fromisoformat(ref["start_time"])
    result = V2AnalyticsQueryService().execute_ch_query(
        """SELECT toString(project_id) AS project_id, trace_id, id,
            observation_type, service_name, start_time, toString(_version) AS _version,
            is_deleted, input, output, attributes_extra AS span_attributes,
            toJSONString(metadata) AS metadata_json
        FROM spans FINAL
        PREWHERE project_id = %(project_id)s AND trace_id = %(trace_id)s
            AND id = %(span_id)s AND toStartOfHour(start_time) = %(start_hour)s
        LIMIT 2""",
        {
            "project_id": ref["project_id"],
            "trace_id": ref["trace_id"],
            "span_id": ref["span_id"],
            "start_hour": stamp.replace(minute=0, second=0, microsecond=0),
        },
        settings=application_read_settings(
            {
                "max_threads": 1,
                "optimize_move_to_prewhere": 0,
                "optimize_move_to_prewhere_if_final": 0,
                "enable_optimize_predicate_expression_to_final_subquery": 0,
            }
        ),
    )
    if (
        getattr(result, "complete", True) is not True
        or not isinstance(result.data, list)
        or len(result.data) != 1
    ):
        raise SkillsHubError(
            "capture_unsafe",
            "The source span is unavailable or physically ambiguous.",
            status=422,
        )
    row = result.data[0]
    actual_time = row.get("start_time")
    if isinstance(actual_time, datetime) and timezone.is_naive(actual_time):
        actual_time = actual_time.replace(tzinfo=UTC)
    if (
        any(
            str(row.get(field)) != ref[key]
            for field, key in [
                ("project_id", "project_id"),
                ("trace_id", "trace_id"),
                ("id", "span_id"),
            ]
        )
        or actual_time != stamp
        or row.get("is_deleted") != 0
    ):
        raise SkillsHubError(
            "resource_not_found", "The source span changed or was removed.", status=404
        )
    physical = {
        **ref,
        "service_name": row["service_name"],
        "observation_type": row["observation_type"],
        "version": str(row["_version"]),
    }
    segments = extract_segments(row)
    attributes = _decode(row.get("span_attributes"))
    attributes = attributes if isinstance(attributes, dict) else {}
    metadata = _decode(row.get("metadata_json"))
    metadata = metadata if isinstance(metadata, dict) else {}
    # Only exact scalar Hub identity can be used; human review still sees it as recorded.
    recorded = {
        key: attributes.get(f"fi.context.{key}", metadata.get(f"fi.context.{key}"))
        for key in ("id", "version_id", "digest")
    }
    if attributes.get("gen_ai.trace.source") == "langsmith":
        raise SkillsHubError(
            "capture_unsafe",
            "LangSmith-origin capture is not supported in this pilot.",
            status=422,
        )
    external = None
    raw_external = metadata.get("fi.external.langfuse")
    if isinstance(raw_external, dict):
        from model_hub.services.skills_hub.r3.external_trace_capture import (
            retained_external_lineage,
        )

        fields = ("origin", "project_id", "trace_id", "observation_id")
        if all(
            isinstance(raw_external.get(key), str) and len(raw_external[key]) <= 255
            for key in fields
        ):
            try:
                external = retained_external_lineage(
                    **{key: raw_external[key] for key in fields}
                )
                external["trust"] = "source_reported"
            except ValueError:
                external = None
    source_hashes = {segment["id"]: segment["sha256"] for segment in segments}
    return {
        "trace_ref": ref,
        "physical": physical,
        "segments": segments,
        "source_hashes": source_hashes,
        "source_fingerprint": canonical_digest(
            {
                "physical": physical,
                "hashes": source_hashes,
                "recorded": recorded,
                "external": external,
            }
        ),
        "recorded_context": recorded,
        "external": external,
        "project_generation": project.analysis_source_generation,
    }
