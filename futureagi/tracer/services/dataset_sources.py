"""Bounded source previews for Observe-to-dataset imports."""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from tracer.models.observation_span import EvalLogger
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.eval_tiles import _verdict_value

MAX_SOURCES = 200


class DatasetSourcePreviewSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    source_kind = serializers.ChoiceField(choices=["traces", "spans", "sessions"])
    failures_only = serializers.BooleanField(default=True)
    trace_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=200
    )
    span_ids = serializers.ListField(
        child=serializers.CharField(max_length=128), required=False, max_length=200
    )
    session_ids = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False, max_length=200
    )


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


def preview_sources(project, options):
    """Read only the scoped project's newest week; never write during preview."""
    kind = options["source_kind"]
    now = timezone.now()
    since = now - timedelta(days=7)
    where = [
        "project_id = %(project)s",
        "org_id = %(org)s",
        "is_deleted = 0",
        "start_time >= %(since)s",
        "start_time <= %(now)s",
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
    limited = len(rows) > MAX_SOURCES
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
