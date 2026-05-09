import base64
import json
import math
import re
import traceback
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

import structlog
from django.db import connection, models, transaction
from django.utils import timezone

logger = structlog.get_logger(__name__)
from model_hub.models.prompt_label import PromptLabel
from model_hub.models.run_prompt import PromptVersion
from tfc.temporal import temporal_activity
from tfc.utils.payload_storage import payload_storage
from tracer.models.observation_span import EndUser, ObservationSpan, Trace, UserIdType


def _norm_uid_type(raw: str | None) -> str:
    """Normalise a caller-supplied user_id_type: None or empty → "custom"."""
    return raw if raw else UserIdType.CUSTOM
from tracer.models.project import Project
from tracer.models.trace_session import TraceSession
from tracer.tasks.trace_scanner import scan_traces_task
from tracer.utils.adapters import normalize_span_attributes
from tracer.utils.otel import bulk_convert_otel_spans_to_observation_spans
from tracer.utils.parsers import deserialize_trace_payload
from tracer.utils.pii_scrubber import scrub_pii_in_span_batch
from tracer.utils.pii_settings import get_pii_settings_for_projects

OTLP_STATUS_MAP = {
    "STATUS_CODE_UNSET": "UNSET",
    "STATUS_CODE_OK": "OK",
    "STATUS_CODE_ERROR": "ERROR",
}


# --- Helper Functions for Data Transformation ---


def _convert_attributes(attributes: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert a list of OTLP key-value pairs to a Python dictionary."""
    if not attributes:
        return {}
    return {
        item["key"]: item["value"].get(list(item["value"].keys())[0])
        for item in attributes
        if "key" in item and "value" in item and item["value"]
    }


def _format_id(id_str: str) -> str:
    """Convert a base64 encoded ID to its hex representation."""
    if not id_str:
        return None
    return base64.b64decode(id_str).hex()


def _is_hex(s: str) -> bool:
    """Check if a string is a valid hex string."""
    return re.fullmatch(r"^[0-9a-fA-F]+$", s or "") is not None


def _format_if_needed(raw: str) -> str | None:
    """Format an ID to hex if it's not already in that format."""
    if not raw:
        return None
    return raw if _is_hex(raw) else _format_id(raw)


# --- Helper Functions for Database Interaction ---


def _serialize_json_field_value(val: Any) -> str | None:
    """
    Serialize a value for PostgreSQL JSONField in COPY operations.

    Args:
        val: The value to serialize

    Returns:
        JSON string representation or None
    """
    if val is None:
        return None

    if isinstance(val, str):
        try:
            json.loads(val)
            return val
        except (json.JSONDecodeError, TypeError):
            return json.dumps(val)

    return json.dumps(val)


def _bulk_create_with_copy(model: models.Model, objects: list[models.Model]):
    """Bulk creates model instances using PostgreSQL's fast COPY command."""
    if not objects:
        return

    with connection.cursor() as cursor:
        fields = list(model._meta.concrete_fields)
        columns = [f.column for f in fields]

        values_list = []
        for obj in objects:
            row = []
            for field in fields:
                val = getattr(obj, field.attname)

                # Handle JSONField values
                if isinstance(field, models.JSONField):
                    val = _serialize_json_field_value(val)

                row.append(val)
            values_list.append(tuple(row))

        copy_sql = f"COPY {model._meta.db_table} ({', '.join(columns)}) FROM STDIN"
        with cursor.copy(copy_sql) as copy:
            for row in values_list:
                copy.write_row(row)


def _fetch_or_create_traces(
    parsed_data_list: list[dict[str, Any]],
) -> dict[uuid.UUID, Trace]:
    """
    Fetches existing traces or creates new ones for the given spans,
    returning a dictionary of all relevant traces.
    """
    trace_ids = {uuid.UUID(d["trace"]) for d in parsed_data_list if d.get("trace")}
    if not trace_ids:
        return {}

    existing_traces = {t.id: t for t in Trace.objects.filter(id__in=trace_ids)}

    unique_new_traces = {}
    for d in parsed_data_list:
        trace_id_str = d.get("trace")
        if not trace_id_str:
            continue
        try:
            trace_id = uuid.UUID(trace_id_str)
            if trace_id not in existing_traces and trace_id not in unique_new_traces:
                unique_new_traces[trace_id] = {
                    "project": d["project"],
                    "project_version": d.get("project_version"),
                }
        except (ValueError, TypeError):
            continue

    if unique_new_traces:
        now = timezone.now()
        new_traces_to_create = [
            Trace(
                id=trace_id,
                project=trace_info["project"],
                project_version=trace_info.get("project_version"),
                created_at=now,
                updated_at=now,
            )
            for trace_id, trace_info in unique_new_traces.items()
        ]
        try:
            _bulk_create_with_copy(Trace, new_traces_to_create)
            for trace in new_traces_to_create:
                existing_traces[trace.id] = trace
        except Exception as e:
            from django.db import DatabaseError
            from psycopg.errors import UniqueViolation

            if isinstance(getattr(e, "__cause__", None), UniqueViolation) or isinstance(
                e, DatabaseError
            ):
                # Race condition: another worker inserted the same trace(s) — re-fetch
                refetched = {
                    t.id: t
                    for t in Trace.objects.filter(
                        id__in=[t.id for t in new_traces_to_create]
                    )
                }
                existing_traces.update(refetched)
            else:
                raise

    return existing_traces


def _fetch_or_create_sessions(
    parsed_data_list: list[dict[str, Any]],
) -> dict[tuple, TraceSession]:
    """
    Fetches existing sessions or creates new ones, returning all relevant sessions.
    """
    session_keys = {
        (d["session_name"], d["project"].id)
        for d in parsed_data_list
        if d.get("session_name") and d.get("project")
    }
    if not session_keys:
        return {}

    session_names = {key[0] for key in session_keys}
    project_ids = {key[1] for key in session_keys}

    existing_sessions = {
        (s.name, s.project_id): s
        for s in TraceSession.objects.filter(
            name__in=session_names, project_id__in=project_ids
        )
    }

    unique_new_sessions = {}

    for d in parsed_data_list:
        session_name = d.get("session_name")
        project = d.get("project")
        if session_name and project:
            key = (session_name, project.id)

            if key in existing_sessions:
                continue
            if key in unique_new_sessions:
                continue

            unique_new_sessions[key] = TraceSession(
                name=session_name,
                project=project,
            )

    if unique_new_sessions:
        TraceSession.objects.bulk_create(
            list(unique_new_sessions.values()), ignore_conflicts=True
        )

        return {
            (s.name, s.project_id): s
            for s in TraceSession.objects.filter(
                name__in=session_names, project_id__in=project_ids
            )
        }

    return existing_sessions


def _fetch_or_create_end_users(
    parsed_data_list: list[dict[str, Any]], organization_id: str
) -> dict[tuple, EndUser]:
    """
    Fetches existing end users or creates new ones.

    Supports:
    - Profile fields (display_name, email, avatar_url)
    - Analytics tracking (first_seen, last_seen)
    - Flexible attributes from span data
    """
    end_user_keys = set()
    end_user_data = {}  # Store full user data for creation

    for d in parsed_data_list:
        end_user = d.get("end_user")
        if end_user and end_user.get("user_id"):
            key = (
                str(end_user["user_id"]),
                str(organization_id),
                str(end_user["project"].id),
                _norm_uid_type(end_user.get("user_id_type")),
            )
            end_user_keys.add(key)

            if key not in end_user_data:
                end_user_data[key] = {
                    "end_user": end_user,
                }

    if not end_user_keys:
        return {}

    user_ids = {k[0] for k in end_user_keys}
    project_ids = {k[2] for k in end_user_keys}

    existing_end_users = {
        (eu.user_id, str(eu.organization_id), str(eu.project_id), eu.user_id_type): eu
        for eu in EndUser.objects.filter(
            user_id__in=user_ids,
            organization_id=organization_id,
            project_id__in=project_ids,
        )
    }

    unique_new_end_users = {}
    users_to_update = []

    for key, data in end_user_data.items():
        end_user = data["end_user"]

        if key in existing_end_users:
            existing_user = existing_end_users[key]
            users_to_update.append(existing_user)
        elif key not in unique_new_end_users:
            unique_new_end_users[key] = EndUser(
                user_id=str(end_user["user_id"]),
                organization_id=organization_id,
                project=end_user["project"],
                user_id_type=_norm_uid_type(end_user.get("user_id_type")),
                user_id_hash=end_user.get("user_id_hash"),
                metadata=end_user.get("metadata", {}),
            )

    if unique_new_end_users:
        EndUser.objects.bulk_create(
            list(unique_new_end_users.values()), ignore_conflicts=True
        )
        # Re-fetch all users to get IDs of newly created ones
        return {
            (
                eu.user_id,
                str(eu.organization_id),
                str(eu.project_id),
                eu.user_id_type,
            ): eu
            for eu in EndUser.objects.filter(
                user_id__in=user_ids,
                organization_id=organization_id,
                project_id__in=project_ids,
            )
        }

    return existing_end_users


def _fetch_prompt_versions(
    parsed_data_list: List[Dict[str, Any]], organization_id: str
) -> Dict[tuple, Dict]:
    """Fetches all required prompt versions."""
    prompt_version_filters = []
    for d in parsed_data_list:
        prompt_details = d.get("prompt_details")
        span_details = d.get("observation_span", {})
        span_type = span_details.get("observation_type", None)

        if prompt_details is not None and span_type == "llm":
            prompt_template_name = prompt_details.get("prompt_template_name", None)
            prompt_template_version = prompt_details.get(
                "prompt_template_version", None
            )
            prompt_template_label = prompt_details.get("prompt_template_label", None)

            if prompt_template_name and prompt_template_label:
                filters = {
                    "original_template__name": prompt_template_name,
                    "original_template__organization": organization_id,
                    "labels__name": prompt_template_label,
                }

                if prompt_template_version:
                    filters["template_version"] = prompt_template_version

                prompt_version_filters.append(filters)

    if not prompt_version_filters or len(prompt_version_filters) == 0:
        return {}

    # Use a cache to avoid redundant queries for the same filter set
    prompt_versions_cache = {}
    for filters in prompt_version_filters:
        key = tuple(sorted(filters.items()))
        if key not in prompt_versions_cache:
            prompt_version = PromptVersion.objects.filter(**filters).first()
            if prompt_version:
                # Fetch the required prompt_label with the specific name
                label_name = filters.get("labels__name")
                prompt_labels_ids = prompt_version.labels.through.objects.filter(
                    promptversion_id=prompt_version,
                ).values_list("promptlabel_id", flat=True)

                req_label = PromptLabel.no_workspace_objects.filter(
                    id__in=prompt_labels_ids, name=label_name
                ).first()

                prompt_versions_cache[key] = {
                    "prompt_version_id": str(prompt_version.id),
                    "prompt_label_id": str(req_label.id),
                }

    return prompt_versions_cache


# --- Core Logic for Span Ingestion Pipeline ---


def _parse_otel_request(request_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parses a dict from an OTLP request and extracts a flat list of span data."""
    resource_spans = request_data.get("resource_spans", [])
    otel_data_list = []

    for resource_span in resource_spans:
        resource_attributes = _convert_attributes(
            resource_span.get("resource", {}).get("attributes", [])
        )
        scope_spans = resource_span.get("scope_spans", [])
        for scope_span in scope_spans:
            for span in scope_span.get("spans", []):
                start_time_unix_nano = int(span.get("start_time_unix_nano", 0))
                end_time_unix_nano = int(span.get("end_time_unix_nano", 0))
                span_data = {
                    "trace_id": _format_if_needed(span.get("trace_id")),
                    "span_id": _format_if_needed(span.get("span_id")),
                    "name": span.get("name"),
                    "start_time": start_time_unix_nano,
                    "end_time": end_time_unix_nano,
                    "attributes": _convert_attributes(span.get("attributes")),
                    "events": [
                        {
                            "name": event.get("name"),
                            "attributes": _convert_attributes(event.get("attributes")),
                            "timestamp": (
                                datetime.fromtimestamp(
                                    int(event.get("time_unix_nano")) / 1e9
                                ).isoformat()
                                if event.get("time_unix_nano")
                                else None
                            ),
                        }
                        for event in span.get("events", [])
                    ],
                    "status": OTLP_STATUS_MAP.get(
                        span.get("status", {}).get("code"), "UNSET"
                    ),
                    "status_message": span.get("status", {}).get("message"),
                    "parent_id": _format_if_needed(span.get("parent_span_id")),
                    "project_name": resource_attributes.get("project_name"),
                    "project_type": resource_attributes.get("project_type"),
                    "project_version_name": resource_attributes.get(
                        "project_version_name"
                    ),
                    "project_version_id": resource_attributes.get("project_version_id"),
                    "latency": (
                        math.floor(
                            (end_time_unix_nano - start_time_unix_nano) / 1000000
                        )
                        if end_time_unix_nano and start_time_unix_nano
                        else 0
                    ),
                    "eval_tags": resource_attributes.get("eval_tags"),
                    "metadata": resource_attributes.get("metadata"),
                    "session_name": resource_attributes.get("session_name"),
                }
                otel_data_list.append(span_data)
    return otel_data_list


def _link_end_user(observation_span_data, parsed_data, all_end_users, organization_id):
    """Links the correct EndUser object to the observation span data."""
    if not (parsed_data.get("end_user") and parsed_data["end_user"].get("user_id")):
        return

    end_user_info = parsed_data["end_user"]
    end_user_key = (
        str(end_user_info["user_id"]),
        str(organization_id),
        str(parsed_data["project"].id),
        _norm_uid_type(end_user_info.get("user_id_type")),
    )
    if end_user_key in all_end_users:
        observation_span_data["end_user"] = all_end_users[end_user_key]
    else:
        logger.warning(f"End user not found for key: {end_user_key}. Skipping link.")


def _link_prompt_version(
    observation_span_data, parsed_data, all_prompt_versions, organization_id
):
    """Links the correct PromptVersion object to the observation span data."""
    prompt_details = parsed_data.get("prompt_details")

    span_details = parsed_data.get("observation_span", {})
    span_type = span_details.get("observation_type", None)

    if prompt_details is not None and span_type == "llm":
        prompt_template_name = prompt_details.get("prompt_template_name", None)
        prompt_template_version = prompt_details.get("prompt_template_version", None)
        prompt_template_label = prompt_details.get("prompt_template_label", None)

        if prompt_template_name and prompt_template_label:
            filters = {
                "original_template__name": prompt_template_name,
                "original_template__organization": organization_id,
                "labels__name": prompt_template_label,
            }

            if prompt_template_version:
                filters["template_version"] = prompt_template_version

            key = tuple(sorted(filters.items()))
            if (
                key in all_prompt_versions
                and all_prompt_versions[key] is not None
                and len(all_prompt_versions[key]) > 0
            ):
                observation_span_data["prompt_version_id"] = all_prompt_versions[key][
                    "prompt_version_id"
                ]
                observation_span_data["prompt_label_id"] = all_prompt_versions[key][
                    "prompt_label_id"
                ]

            else:
                logger.warning(
                    f"Prompt version not found for key: {key}. Skipping link."
                )


def _prepare_trace_update_data(
    traces_to_update, parsed_data, observation_span_data, all_sessions
):
    """Prepares the dictionary used to bulk update Trace objects later."""
    trace_id_str = parsed_data.get("trace")
    parent_span_id = observation_span_data.get("parent_span_id")

    if not parent_span_id:
        if observation_span_data.get("input"):
            traces_to_update[trace_id_str]["input"] = observation_span_data["input"]
        if observation_span_data.get("output"):
            traces_to_update[trace_id_str]["output"] = observation_span_data["output"]

    if parsed_data.get("session_name"):
        session_name = parsed_data["session_name"]
        project_id = parsed_data["project"].id
        session_key = (session_name, project_id)
        if session_key in all_sessions:
            traces_to_update[trace_id_str]["session"] = all_sessions[session_key]


def _prepare_observation_spans_and_trace_updates(
    parsed_data_list: list[dict[str, Any]],
    all_traces: dict[uuid.UUID, Trace],
    all_sessions: dict[tuple, TraceSession],
    all_end_users: dict[tuple, EndUser],
    all_prompt_versions: dict[tuple, PromptVersion],
    organization_id: str,
) -> (list[ObservationSpan], dict[str, dict[str, Any]]):
    """Links related models to observation spans and prepares data for trace updates."""
    spans_to_create = []
    traces_to_update = defaultdict(dict)

    for parsed_data in parsed_data_list:
        trace_id_str = parsed_data.get("trace")
        if not trace_id_str:
            raise Exception("Trace ID missing for a span.")
        try:
            trace_id = uuid.UUID(trace_id_str)
        except (ValueError, TypeError):
            raise Exception(f"Invalid trace ID format: {trace_id_str}.")  # noqa: B904

        if trace_id not in all_traces:
            raise Exception(f"Trace not found for trace ID: {trace_id_str}.")

        observation_span_data = parsed_data["observation_span"]
        observation_span_data["trace"] = all_traces[trace_id]

        if "trace_id" in observation_span_data:
            del observation_span_data["trace_id"]

        _link_end_user(
            observation_span_data, parsed_data, all_end_users, organization_id
        )
        _link_prompt_version(
            observation_span_data, parsed_data, all_prompt_versions, organization_id
        )

        spans_to_create.append(ObservationSpan(**observation_span_data))

        # Prepare data for the eventual bulk update of Trace objects
        _prepare_trace_update_data(
            traces_to_update, parsed_data, observation_span_data, all_sessions
        )

    return spans_to_create, traces_to_update


def _bulk_insert_observation_spans(spans_to_create: list[ObservationSpan]):
    """Sets timestamps and bulk inserts observation spans using the COPY command."""
    if not spans_to_create:
        return

    # Manually set timestamps because we are bypassing the ORM's auto-field handling.
    now = timezone.now()
    for span in spans_to_create:
        if not span.created_at:
            span.created_at = now
        if not span.updated_at:
            span.updated_at = now

    _bulk_create_with_copy(ObservationSpan, spans_to_create)


def _bulk_update_traces(
    traces_to_update: dict[str, dict[str, Any]], all_traces: dict[uuid.UUID, Trace]
):
    """Bulk updates trace fields like input, output, and session."""
    traces_to_bulk_update = []
    update_fields = set()

    for trace_id_str, updates in traces_to_update.items():
        try:
            trace_id = uuid.UUID(trace_id_str)
            trace = all_traces.get(trace_id)
            if trace:
                for field, value in updates.items():
                    setattr(trace, field, value)
                    update_fields.add(field)
                traces_to_bulk_update.append(trace)
        except (ValueError, TypeError):
            continue

    if traces_to_bulk_update:
        Trace.objects.bulk_update(traces_to_bulk_update, list(update_fields))


def _trigger_trace_scanner(spans: list[ObservationSpan]):
    """
    Detect completed traces (root span with end_time) and trigger the scanner.

    Root span = parent_span_id is None. end_time set = trace is complete.
    Groups by project_id since scanner activity runs per-project.
    Only "observe" projects are scanned — experiment projects are throwaway
    evaluation runs and shouldn't burn scanner LLM tokens or surface in the feed.
    """
    complete_traces_by_project: dict[str, set[str]] = defaultdict(set)
    for span in spans:
        if span.parent_span_id is None and span.end_time is not None:
            complete_traces_by_project[str(span.project_id)].add(str(span.trace_id))

    if not complete_traces_by_project:
        return

    observe_project_ids = set(
        str(pid)
        for pid in Project.objects.filter(
            id__in=complete_traces_by_project.keys(),
            trace_type="observe",
        ).values_list("id", flat=True)
    )

    for project_id, trace_ids in complete_traces_by_project.items():
        if project_id not in observe_project_ids:
            continue
        scan_traces_task.apply_async(args=(list(trace_ids), project_id))


@temporal_activity(max_retries=0, time_limit=3600, queue="trace_ingestion")
def bulk_create_observation_span_task(
    payload_key: str, organization_id, user_id, workspace_id=None, payload_format="json"
):
    """
    Temporal activity to create ObservationSpans from a batch of OTEL data using bulk operations.

    Args:
        payload_key: Redis key containing the trace data (instead of passing large JSON directly)
        organization_id: Organization ID
        user_id: User ID
        workspace_id: Optional workspace ID
        payload_format: Format of the stored payload — "json" or "protobuf".
            Defaults to "json" for backward compatibility with in-flight tasks.
    """
    try:
        payload_bytes = payload_storage.retrieve(payload_key)

        if payload_bytes is None:
            logger.error(
                "trace_payload_not_found_in_redis",
                payload_key=payload_key,
            )
            raise ValueError(f"Trace payload not found in Redis: {payload_key}")

        logger.info(
            "trace_payload_retrieved_from_redis",
            payload_key=payload_key,
            payload_size=len(payload_bytes),
            payload_format=payload_format,
        )

        request_data = deserialize_trace_payload(payload_bytes, payload_format)

        # Pre-check: enforce free tier limits on trace ingestion
        try:
            try:
                from ee.usage.deployment import DeploymentMode
            except ImportError:
                DeploymentMode = None

            if not DeploymentMode.is_oss():
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.services.metering import check_usage
                except ImportError:
                    check_usage = None

                usage_check = check_usage(
                    str(organization_id), BillingEventType.TRACING_EVENT
                )
                if not usage_check.allowed:
                    logger.warning(
                        "trace_ingestion_blocked_free_tier",
                        org_id=str(organization_id),
                        reason=usage_check.reason,
                    )
                    return
        except Exception:
            pass  # Fail open — don't break ingestion on metering errors

        with transaction.atomic():
            # 1. Parse and transform the raw request data
            otel_data_list = _parse_otel_request(request_data)
            if not otel_data_list:
                return

            # 1.5. Normalize foreign attribute formats (OpenInference, etc.) to fi.*
            normalize_span_attributes(otel_data_list)

            # 1.6. PII scrubbing (per-project, after normalization)

            project_names = {
                s.get("project_name") for s in otel_data_list if s.get("project_name")
            }
            if project_names:
                pii_settings = get_pii_settings_for_projects(
                    project_names, str(organization_id)
                )
                scrub_pii_in_span_batch(otel_data_list, pii_settings)

            parsed_data_list = bulk_convert_otel_spans_to_observation_spans(
                otel_data_list, organization_id, user_id, workspace_id
            )
            if not parsed_data_list:
                return

            # 2. Fetch or create all related objects in bulk
            all_traces = _fetch_or_create_traces(parsed_data_list)

            # Create end users first so we can associate them with sessions
            all_end_users = _fetch_or_create_end_users(
                parsed_data_list, organization_id
            )

            all_sessions = _fetch_or_create_sessions(parsed_data_list)

            all_prompt_versions = _fetch_prompt_versions(
                parsed_data_list, organization_id
            )

            # 3. Prepare final objects by linking related models
            (
                observation_spans_to_create,
                traces_to_update,
            ) = _prepare_observation_spans_and_trace_updates(
                parsed_data_list,
                all_traces,
                all_sessions,
                all_end_users,
                all_prompt_versions,
                organization_id,
            )

            # 4. Perform bulk database writes
            _bulk_insert_observation_spans(observation_spans_to_create)
            _bulk_update_traces(traces_to_update, all_traces)

            # 5. Trigger scanner for completed traces (root span with end_time)
            _trigger_trace_scanner(observation_spans_to_create)

        # 6. Usage metering (after commit, outside transaction)
        try:
            try:
                from ee.usage.deployment import DeploymentMode
            except ImportError:
                DeploymentMode = None

            if not DeploymentMode.is_oss():
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                num_traces = len(
                    set(p.get("trace") for p in parsed_data_list if p.get("trace"))
                )
                num_spans = len(observation_spans_to_create)
                if num_traces:
                    emit(
                        UsageEvent(
                            org_id=str(organization_id),
                            event_type=BillingEventType.TRACING_EVENT,
                            amount=num_traces,
                            properties={"traces": num_traces},
                        )
                    )
                if num_spans:
                    emit(
                        UsageEvent(
                            org_id=str(organization_id),
                            event_type=BillingEventType.OBSERVE_ADD,
                            amount=len(payload_bytes) if payload_bytes else 0,
                            properties={"source": "trace_span", "spans": num_spans},
                        )
                    )
        except Exception:
            logger.debug("usage_metering_skipped", exc_info=True)

    except Exception as exc:
        logger.exception(
            f"Error processing spans in bulk: {exc}\n{traceback.format_exc()}"
        )
        raise
