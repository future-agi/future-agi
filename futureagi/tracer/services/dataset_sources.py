"""Bounded source previews for Observe-to-dataset imports."""

from collections import Counter
from datetime import UTC, timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from tracer.models.observation_span import EvalLogger
from tracer.serializers.filters import BoundedFilterListField
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.eval_tiles import _verdict_value
from tracer.services.filter_principal_context import (
    bind_request_my_annotations_principal,
)

MAX_SOURCES = 200


class DatasetSourcePreviewSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    source_kind = serializers.ChoiceField(choices=["traces", "spans", "sessions"])
    failures_only = serializers.BooleanField(default=True)
    filters = BoundedFilterListField(required=False)
    exclude_ids = serializers.ListField(
        child=serializers.CharField(max_length=128), required=False, max_length=200
    )
    trace_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=200
    )
    span_ids = serializers.ListField(
        child=serializers.CharField(max_length=128), required=False, max_length=200
    )
    session_ids = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False, max_length=200
    )

    def validate_filters(self, filters):
        if not any(BaseQueryBuilder.is_datetime_filter(item) for item in filters):
            raise serializers.ValidationError(
                "An Observe selection must include its date window."
            )
        return filters

    def validate(self, attrs):
        if "filters" in attrs and attrs["source_kind"] == "sessions":
            raise serializers.ValidationError(
                {"filters": "Observe filter selections must use traces or spans."}
            )
        if "exclude_ids" in attrs and "filters" not in attrs:
            raise serializers.ValidationError(
                {"exclude_ids": "Exclusions require an Observe filter selection."}
            )
        return attrs


class DatasetSourceItemSerializer(serializers.Serializer):
    span_id = serializers.CharField()
    trace_id = serializers.CharField()
    session_id = serializers.CharField(allow_null=True)
    name = serializers.CharField()
    start_time = serializers.DateTimeField()


class DatasetSourceResultSerializer(serializers.Serializer):
    items = DatasetSourceItemSerializer(many=True)
    limited = serializers.BooleanField()
    limit = serializers.IntegerField()


class DatasetSourceResponseSerializer(serializers.Serializer):
    result = DatasetSourceResultSerializer()


class DatasetImportResultSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    dataset_name = serializers.CharField(required=False)
    status = serializers.CharField()
    message = serializers.CharField()


class DatasetImportResponseSerializer(serializers.Serializer):
    result = DatasetImportResultSerializer()


def preview_sources(project, options, *, request=None):
    """Preview a scoped Observe selection, or the newest week for standalone use."""
    kind = options["source_kind"]
    now = timezone.now()
    since = now - timedelta(days=7)
    selection_limited = False
    scoped = "filters" in options
    if scoped:
        from model_hub.services.bulk_selection import (
            resolve_filtered_span_ids,
            resolve_filtered_trace_ids,
        )

        # These are the same bounded resolvers used by all-matching queue
        # actions: predicates and exclusions apply BEFORE the preview cap.
        resolver = (
            resolve_filtered_span_ids if kind == "spans" else resolve_filtered_trace_ids
        )
        try:
            # Match the grid's authenticated annotation cohort without mutating
            # the validated payload or trusting a previously bound principal.
            filters = bind_request_my_annotations_principal(request, options["filters"])
            selection = resolver(
                project_id=project.id,
                organization=project.organization,
                filters=filters,
                exclude_ids=options.get("exclude_ids", []),
                cap=MAX_SOURCES,
                user=getattr(request, "user", None),
            )
        except ValueError as exc:
            # User-relative filters without request context must not broaden
            # into a project-wide preview. The sheet displays this refusal.
            raise serializers.ValidationError({"filters": str(exc)}) from exc
        selection_limited = selection.truncated
        if not selection.ids:
            return {"items": [], "limited": selection_limited, "limit": MAX_SOURCES}
        key = "span_ids" if kind == "spans" else "trace_ids"
        options = {**options, key: selection.ids}
        since, now = BaseQueryBuilder.parse_time_range(options["filters"], strict=True)
        since, now = since.replace(tzinfo=UTC), now.replace(tzinfo=UTC)
    where = [
        "project_id = %(project)s",
        "org_id = %(org)s",
        "is_deleted = 0",
        "start_time >= %(since)s",
        "start_time < %(now)s" if scoped else "start_time <= %(now)s",
    ]
    params = {
        "project": str(project.id),
        "org": str(project.organization_id),
        "since": since,
        "now": now,
    }
    if kind != "spans":
        where.append("parent_span_id = ''")
    if kind == "sessions":
        where.append("trace_session_id IS NOT NULL")
    for key, column in [
        ("trace_ids", "trace_id"),
        ("span_ids", "id"),
        ("session_ids", "trace_session_id"),
    ]:
        if key in options:
            if not options[key]:
                return {"items": [], "limited": False, "limit": MAX_SOURCES}
            where.append(f"{column} IN %({key})s")
            params[key] = tuple(str(value) for value in options[key])
    # Read identifiers and labels only. The worker reads payloads after confirmation.
    with get_reader() as reader:
        rows = reader._client.query(
            "SELECT id, trace_id, trace_session_id, name, start_time FROM spans FINAL WHERE "
            + " AND ".join(where)
            + " ORDER BY start_time DESC, id LIMIT 201",
            parameters=params,
        ).result_rows
    if scoped and kind == "spans":
        # The resolver returns bare span IDs. Another span in this window can
        # carry the same ID (for example under a different trace) without
        # matching the filters. The sheet and the import select by span ID
        # alone, so refuse rather than offer a span outside the selection.
        counts = Counter(str(row[0]) for row in rows)
        shared = sorted(span_id for span_id, count in counts.items() if count > 1)
        if shared:
            raise serializers.ValidationError(
                f"Span {shared[0]} shares its ID with another span, so this "
                "selection can't be imported safely. Uncheck that span in "
                "Observe or narrow the filters, then try again."
            )
    limited = selection_limited or len(rows) > MAX_SOURCES
    rows = rows[:MAX_SOURCES]
    if options["failures_only"] and rows:
        traces = [str(row[1]) for row in rows]
        sessions = [str(row[2]) for row in rows if row[2]]
        # Bind evaluation provenance as well as source IDs: caller-supplied
        # telemetry identifiers can collide across projects.
        failed = EvalLogger.no_workspace_objects.filter(
            Q(custom_eval_config__project=project)
            | Q(custom_eval_config__isnull=True, trace__project=project)
            | Q(custom_eval_config__isnull=True, trace_session__project=project),
            Q(trace_id__in=traces) | Q(trace_session_id__in=sessions),
            status="completed",
            error=False,
            deleted=False,
            created_at__gte=since,
        ).values(
            "trace_id",
            "observation_span_id",
            "trace_session_id",
            "target_type",
            "output_bool",
            "output_str",
            "output_float",
        )
        failed_traces, failed_spans, failed_sessions = set(), set(), set()
        for result in failed.iterator(chunk_size=500):
            if _verdict_value(result) != "fail":
                continue
            if kind != "spans" or result["target_type"] == "trace":
                failed_traces.add(str(result["trace_id"]))
            failed_spans.add(str(result["observation_span_id"]))
            failed_sessions.add(str(result["trace_session_id"]))
        if kind == "sessions":
            failed_sessions.update(
                str(row[2]) for row in rows if row[2] and str(row[1]) in failed_traces
            )
        rows = [
            row
            for row in rows
            if str(row[0]) in failed_spans
            or str(row[1]) in failed_traces
            or (row[2] and str(row[2]) in failed_sessions)
        ]
    return {
        "items": [
            {
                "span_id": str(row[0]),
                "trace_id": str(row[1]),
                "session_id": str(row[2]) if row[2] else None,
                "name": row[3],
                "start_time": row[4].isoformat(),
            }
            for row in rows
        ],
        "limited": limited,
        "limit": MAX_SOURCES,
    }


def validate_import_mapping(mapping, *, new_columns=True):
    """Validate the complete mapping before creating destination state."""
    from model_hub.models.choices import DataTypeChoices
    from tracer.services.clickhouse.v2.span_reader import _EXPORT_FIELD_COLUMNS

    supported_fields = set(_EXPORT_FIELD_COLUMNS) | {
        "child_spans",
        "eval_metrics",
        "annotation_metrics",
    }
    names = []
    for item in mapping:
        name = item.get("col_name")
        if not isinstance(name, str) or not name.strip() or len(name) > 255:
            raise ValueError(
                "A nonempty column name of at most 255 characters is required"
            )
        if new_columns and item.get("data_type") not in {
            choice.value for choice in DataTypeChoices
        }:
            raise ValueError("Unsupported column data type")
        field = item.get("span_field") or name
        if not isinstance(field, str) or field not in supported_fields:
            raise ValueError("Invalid source field")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError("Column names must be unique")
