import base64
import json
import math
import re
import traceback
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

import structlog
from django.db import IntegrityError, connection, models, transaction
from django.utils import timezone

from model_hub.models.prompt_label import PromptLabel
from model_hub.models.run_prompt import PromptVersion
from tfc.temporal import temporal_activity
from tfc.utils.payload_storage import payload_storage
from tracer.models.observation_span import ObservationSpan, Trace
from tracer.models.project import Project
from tracer.services.clickhouse.v2.curated_writer import (
    CuratedEndUser,
    CuratedSession,
)
from tracer.services.clickhouse.v2.deterministic_id import (
    deterministic_end_user_id,
    deterministic_trace_session_id,
)
from tracer.tasks.trace_scanner import scan_traces_task
from tracer.utils.adapters import normalize_span_attributes
from tracer.utils.otel import bulk_convert_otel_spans_to_observation_spans
from tracer.utils.parsers import deserialize_trace_payload
from tracer.utils.pii_scrubber import scrub_pii_in_span_batch
from tracer.utils.pii_settings import get_pii_settings_for_projects
from tfc.billing.boundary import get_billing, BillingEventType
from tracer.utils.usage_emit import emit_span_ingestion_usage

logger = structlog.get_logger(__name__)

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


def _sanitize_nonfinite_floats(value: Any) -> Any:
    """Recursively replace NaN/+-Infinity floats with ``None``.

    Python's ``json.dumps`` emits the bare tokens ``NaN``/``Infinity``/
    ``-Infinity`` for non-finite floats, which PostgreSQL's json/jsonb type
    rejects during COPY (``invalid input syntax for type json``). User-supplied
    span attributes can carry these values, so scrub them before serialization.
    Mirrors ``tracer.views.trace._sanitize_nonfinite_floats`` on the read path.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _sanitize_nonfinite_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_nonfinite_floats(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_nonfinite_floats(v) for v in value)
    return value


def _strip_null_chars(value: Any) -> Any:
    """Recursively strip NUL (``\\x00``) bytes from string keys and values.

    PostgreSQL's text and json/jsonb types cannot store the NUL code point: a
    real ``\\x00`` inside a string is emitted by ``json.dumps`` as the escape
    ``\\u0000``, which jsonb rejects during COPY (``unsupported Unicode escape
    sequence ... \\u0000 cannot be converted to text``). User-supplied span
    attributes can carry NUL (e.g. extracted PDF/document text), so scrub them
    before serialization. Distinct from ``_sanitize_nonfinite_floats``: NUL is
    silently escaped by ``json.dumps`` rather than raising, so the strip must
    run unconditionally on the JSON path. Only ``\\x00`` is removed; other
    control characters (e.g. ``\\u0013``) are valid in jsonb and preserved.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {
            (k.replace("\x00", "") if isinstance(k, str) else k): _strip_null_chars(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_strip_null_chars(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_strip_null_chars(v) for v in value)
    return value


def _contains_null_char(value: Any) -> bool:
    """Return True if any string key/value in ``value`` contains a NUL byte."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(
            (isinstance(k, str) and "\x00" in k) or _contains_null_char(v)
            for k, v in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_null_char(v) for v in value)
    return False


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
            parsed = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            # Not JSON: COPY writes the raw string into the text-backed column,
            # so strip NUL directly off the byte stream.
            return json.dumps(val.replace("\x00", ""), allow_nan=False)
        try:
            # Fast path: already-valid JSON with no non-finite floats or NUL
            # bytes. A NUL survives json.loads as a real \x00 inside ``parsed``
            # (the source text held the backslash-u-0000 escape), so check ``parsed``.
            json.dumps(parsed, allow_nan=False)
            if not _contains_null_char(parsed):
                return val
            return json.dumps(_strip_null_chars(parsed), allow_nan=False)
        except ValueError:
            return json.dumps(
                _strip_null_chars(_sanitize_nonfinite_floats(parsed)), allow_nan=False
            )

    # Fast path: dump directly; only pay the recursive scrub when a non-finite
    # float or NUL byte is actually present (keeps the common clean-data path
    # allocation-free).
    try:
        dumped = json.dumps(val, allow_nan=False)
    except ValueError:
        return json.dumps(
            _strip_null_chars(_sanitize_nonfinite_floats(val)), allow_nan=False
        )
    # json.dumps does not raise on NUL; it silently emits the \u0000 escape,
    # so guard the dumped output explicitly.
    if "\\u0000" not in dumped:
        return dumped
    return json.dumps(_strip_null_chars(val), allow_nan=False)


def _is_pk_unique_violation(exc: BaseException, table_name: str) -> bool:
    """True iff exc represents a unique violation on ``{table_name}_pkey``.

    Handles both Django-wrapped IntegrityError (psycopg cause on __cause__)
    and the raw psycopg UniqueViolation that escapes from ``cursor.copy()``
    contexts (which bypass Django's exception translation).
    """
    from psycopg.errors import UniqueViolation as PgUniqueViolation

    pg_exc: Any = (
        exc if isinstance(exc, PgUniqueViolation) else getattr(exc, "__cause__", None)
    )
    if not isinstance(pg_exc, PgUniqueViolation):
        return False
    diag = getattr(pg_exc, "diag", None)
    constraint = getattr(diag, "constraint_name", None) if diag else None
    return constraint == f"{table_name}_pkey"


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
                elif isinstance(val, str):
                    # text/varchar columns cannot store NUL either; COPY writes
                    # the raw byte stream, so strip \x00 off plain strings too.
                    val = val.replace("\x00", "")

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


def _resolve_session_ids(
    parsed_data_list: list[dict[str, Any]],
) -> tuple[dict[tuple, "uuid.UUID"], list[CuratedSession]]:
    """Compute the DETERMINISTIC ``trace_session_id`` for each session in the batch
    (CH-derived-dimensions P3b flip — NO PG ``TraceSession`` create).

    Returns ``({(session_name, project_id): trace_session_id}, [CuratedSession])``:
    the id map drives the ``trace.session_id`` column stamp (``db_constraint=False``
    so no PG row is needed), and the ``CuratedSession`` list is the CH
    ``trace_sessions`` dual-write payload (keyed by the same deterministic id).
    """
    session_id_map: dict[tuple, uuid.UUID] = {}
    curated: list[CuratedSession] = []

    for d in parsed_data_list:
        session_name = d.get("session_name")
        project = d.get("project")
        if not (session_name and project):
            continue
        key = (session_name, project.id)
        if key in session_id_map:
            continue
        ts_id = deterministic_trace_session_id(project.id, session_name)
        session_id_map[key] = ts_id
        curated.append(
            CuratedSession(
                project_id=project.id,
                trace_session_id=ts_id,
                external_session_id=session_name,
            )
        )

    return session_id_map, curated


def _resolve_end_user_ids(
    parsed_data_list: list[dict[str, Any]], organization_id: str
) -> tuple[dict[tuple, "uuid.UUID"], list[CuratedEndUser]]:
    """Compute the DETERMINISTIC ``end_user_id`` for each end user in the batch
    (CH-derived-dimensions P3b flip — NO PG ``EndUser`` create).

    The key matches ``_link_end_user``'s lookup key
    ``(user_id, org_id, project_id, user_id_type)`` (all str-coerced except
    ``user_id_type``, which stays the normalized value / None). Returns
    ``({key: end_user_id}, [CuratedEndUser])``: the id map drives the span's
    ``end_user_id`` column stamp (``db_constraint=False``), the ``CuratedEndUser``
    list is the CH ``end_users`` dual-write payload (keyed by the same id).

    ``user_id_type`` MUST be the SAME normalized value fed to
    ``deterministic_end_user_id`` (§11.1a: None → '' sentinel) so the curated row's
    key matches its id and the read-side remap.
    """
    end_user_id_map: dict[tuple, uuid.UUID] = {}
    curated: list[CuratedEndUser] = []

    for d in parsed_data_list:
        end_user = d.get("end_user")
        if not (end_user and end_user.get("user_id")):
            continue
        project = end_user["project"]
        user_id_type = end_user.get("user_id_type")
        key = (
            str(end_user["user_id"]),
            str(organization_id),
            str(project.id),
            user_id_type,
        )
        if key in end_user_id_map:
            continue
        eu_id = deterministic_end_user_id(
            project.id,
            organization_id,
            end_user["user_id"],
            user_id_type,
        )
        end_user_id_map[key] = eu_id
        curated.append(
            CuratedEndUser(
                project_id=project.id,
                end_user_id=eu_id,
                organization_id=organization_id,
                user_id=str(end_user["user_id"]),
                user_id_type=user_id_type,
                user_id_hash=end_user.get("user_id_hash"),
                metadata=end_user.get("metadata", {}),
            )
        )

    return end_user_id_map, curated


def _fetch_prompt_versions(
    parsed_data_list: list[dict[str, Any]], organization_id: str
) -> dict[tuple, dict]:
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


def _link_end_user(
    observation_span_data, parsed_data, all_end_user_ids, organization_id
):
    """Stamp the DETERMINISTIC ``end_user_id`` onto the span's FK COLUMN.

    P3b flip: no PG ``EndUser`` object — ``all_end_user_ids`` maps the lookup key
    to the deterministic id (``_resolve_end_user_ids``). The column is
    ``db_constraint=False``, so the bare id needs no PG row; it carries to the CH
    span via the ``s.end_user_id`` materialization.
    """
    if not (parsed_data.get("end_user") and parsed_data["end_user"].get("user_id")):
        return

    end_user_info = parsed_data["end_user"]
    end_user_key = (
        str(end_user_info["user_id"]),
        str(organization_id),
        str(parsed_data["project"].id),
        end_user_info.get("user_id_type"),
    )
    if end_user_key in all_end_user_ids:
        observation_span_data["end_user_id"] = all_end_user_ids[end_user_key]
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
    traces_to_update, parsed_data, observation_span_data, all_session_ids
):
    """Prepares the dictionary used to bulk update Trace objects later.

    P3b flip: the session is stamped as the DETERMINISTIC ``session_id`` (a bare
    UUID under the ATTNAME key) — NOT a PG ``TraceSession`` object. ``setattr(trace,
    "session_id", uuid)`` is valid (assigning a bare UUID to the ``.session``
    descriptor would raise); ``_bulk_update_traces`` maps the attname back to the
    field name ``"session"`` for ``bulk_update``.
    """
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
        if session_key in all_session_ids:
            traces_to_update[trace_id_str]["session_id"] = all_session_ids[session_key]


def _prepare_observation_spans_and_trace_updates(
    parsed_data_list: list[dict[str, Any]],
    all_traces: dict[uuid.UUID, Trace],
    all_session_ids: dict[tuple, "uuid.UUID"],
    all_end_user_ids: dict[tuple, "uuid.UUID"],
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
            observation_span_data, parsed_data, all_end_user_ids, organization_id
        )
        _link_prompt_version(
            observation_span_data, parsed_data, all_prompt_versions, organization_id
        )

        spans_to_create.append(ObservationSpan(**observation_span_data))

        # Prepare data for the eventual bulk update of Trace objects
        _prepare_trace_update_data(
            traces_to_update, parsed_data, observation_span_data, all_session_ids
        )

    return spans_to_create, traces_to_update


def _bulk_insert_observation_spans(spans_to_create: list[ObservationSpan]):
    """Sets timestamps and bulk inserts observation spans.

    Fast path: PostgreSQL COPY (all-or-nothing). On unique-key collision (e.g.
    a client double-submitting an OTLP batch), the savepoint rolls back and we
    re-insert via bulk_create(ignore_conflicts=True), which emits
    INSERT ... ON CONFLICT DO NOTHING and skips only the duplicate rows.
    """
    if not spans_to_create:
        return

    # Manually set timestamps because we are bypassing the ORM's auto-field handling.
    now = timezone.now()
    for span in spans_to_create:
        if not span.created_at:
            span.created_at = now
        if not span.updated_at:
            span.updated_at = now

    from psycopg.errors import UniqueViolation as PgUniqueViolation

    try:
        with transaction.atomic():
            _bulk_create_with_copy(ObservationSpan, spans_to_create)
    except (IntegrityError, PgUniqueViolation) as e:
        if not _is_pk_unique_violation(e, "tracer_observation_span"):
            raise
        logger.warning(
            "observation_span_copy_pk_violation_falling_back",
            batch_size=len(spans_to_create),
        )
        ObservationSpan.objects.bulk_create(
            spans_to_create,
            ignore_conflicts=True,
            batch_size=500,
        )


def _bulk_update_traces(
    traces_to_update: dict[str, dict[str, Any]], all_traces: dict[uuid.UUID, Trace]
):
    """Bulk updates trace fields like input, output, and session.

    ``session`` is staged under its ATTNAME ``session_id`` (a bare deterministic
    UUID; see ``_prepare_trace_update_data``). ``setattr`` uses that attname, but
    ``bulk_update``'s ``update_fields`` needs the FIELD NAME — so map
    ``session_id`` → ``session`` for the update-field set.
    """
    # attname (used for setattr) → model field name (used by bulk_update).
    _UPDATE_FIELD_NAMES = {"session_id": "session"}
    traces_to_bulk_update = []
    update_fields = set()

    for trace_id_str, updates in traces_to_update.items():
        try:
            trace_id = uuid.UUID(trace_id_str)
            trace = all_traces.get(trace_id)
            if trace:
                for field, value in updates.items():
                    setattr(trace, field, value)
                    update_fields.add(_UPDATE_FIELD_NAMES.get(field, field))
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

    observe_project_ids = {
        str(pid)
        for pid in Project.objects.filter(
            id__in=complete_traces_by_project.keys(),
            trace_type="observe",
        ).values_list("id", flat=True)
    }

    # Bound each scan task to a small batch so it finishes well under the scan
    # activity's time_limit. One big batch at high sampling can exceed the limit
    # and time out before writing anything — so split into per-task chunks.
    scan_batch_size = 15
    for project_id, trace_ids in complete_traces_by_project.items():
        if project_id not in observe_project_ids:
            continue
        tid_list = list(trace_ids)
        for i in range(0, len(tid_list), scan_batch_size):
            scan_traces_task.apply_async(
                args=(tid_list[i : i + scan_batch_size], project_id)
            )


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
            # Expected race: the payload TTL'd out (or its writer hasn't landed)
            # before this task ran. The raised ValueError below is what Temporal
            # retries on, so this log is purely informational - WARNING avoids
            # double-reporting the same condition as a Sentry error.
            logger.warning(
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
            billing = get_billing()
            usage_check = billing.check_usage(
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

            # 2. Fetch or create all related objects in bulk. Traces still get a PG
            # row (trace_writer re-reads it). EndUser / TraceSession do NOT — the
            # P3b flip computes their DETERMINISTIC ids (no PG create) and the
            # curated rows go to CH only.
            all_traces = _fetch_or_create_traces(parsed_data_list)

            # Resolve deterministic end-user / session ids (id map drives the
            # column stamp; the CuratedEndUser/CuratedSession lists are the CH
            # dual-write payload).
            all_end_user_ids, ch_end_users = _resolve_end_user_ids(
                parsed_data_list, organization_id
            )

            all_session_ids, ch_sessions = _resolve_session_ids(parsed_data_list)

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
                all_session_ids,
                all_end_user_ids,
                all_prompt_versions,
                organization_id,
            )

            # 4. Perform bulk database writes
            _bulk_insert_observation_spans(observation_spans_to_create)
            _bulk_update_traces(traces_to_update, all_traces)

            # CH25: mirror this batch's traces into the CH `traces` table — the
            # app-level replacement for the removed PeerDB CDC path that fed
            # trace_dict (the source of every span's trace_name). One upsert
            # batch per ingest batch (not per span); post-commit + best-effort
            # so a CH hiccup never breaks PG ingestion.
            if all_traces:
                from tracer.services.clickhouse.v2.trace_writer import (
                    mirror_traces_to_clickhouse,
                )

                _ch_trace_ids = [str(tid) for tid in all_traces]
                transaction.on_commit(
                    lambda ids=_ch_trace_ids: mirror_traces_to_clickhouse(ids)
                )

            # CH25 (P3b flip): mirror this batch's curated EndUser / TraceSession
            # rows into CH `end_users` / `trace_sessions`, keyed by the DETERMINISTIC
            # ids stamped above. The PG get_or_create is GONE — `ch_end_users` /
            # `ch_sessions` are one-per-identity CuratedEndUser/CuratedSession lists
            # (the curated fields off the span), so this is one batched insert each;
            # post-commit + best-effort so a CH hiccup never breaks or slows ingestion.
            if ch_end_users or ch_sessions:
                from tracer.services.clickhouse.v2.curated_writer import (
                    mirror_curated_dimensions_to_clickhouse,
                )

                transaction.on_commit(
                    lambda eus=ch_end_users, ss=ch_sessions: (
                        mirror_curated_dimensions_to_clickhouse(eus, ss)
                    )
                )

            # 5. Trigger scanner for completed traces (root span with end_time)
            _trigger_trace_scanner(observation_spans_to_create)

        num_traces = len({p.get("trace") for p in parsed_data_list if p.get("trace")})
        emit_span_ingestion_usage(
            organization_id=organization_id,
            num_traces=num_traces,
            num_spans=len(observation_spans_to_create),
            payload_bytes=len(payload_bytes) if payload_bytes else 0,
            source="trace_span",
        )

    except Exception as exc:
        logger.exception(
            f"Error processing spans in bulk: {exc}\n{traceback.format_exc()}"
        )
        raise
