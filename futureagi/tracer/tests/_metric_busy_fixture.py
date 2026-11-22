"""Dense mixed workload and independent source oracle from the sizing run."""

from collections import defaultdict
from datetime import UTC, timedelta
from uuid import uuid4

from tracer.services.derived_metrics import changes
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.service import save_definition
from tracer.services.derived_metrics.storage import BUCKET_TOTALS, FIELD_VALUES


def _definitions(project, workspace, user):
    definitions = []
    for index in range(26):
        span = index == 25
        source = {
            "alias": "value",
            "source": "span",
            "field": "attribute_number",
            "attribute_key": f"value_{index}",
            "value_type": "number",
            "reducer": "latest",
            "ordering": "sentence",
        }
        metric = save_definition(
            {
                "name": f"Dense mixed value {index}",
                "description": "Opt-in dense mixed progress reproducer",
                "kind": "average",
                "level": "span" if span else "conversation",
                "selection_unit": "span" if span else "session",
                "weighting": "none",
                "inputs": [source],
                "field_spec": {"inputs": [source], "value_alias": "value"},
                "filters": [],
            },
            project,
            workspace,
            user,
        )
        definitions.append(metric.versions.get(version=metric.current_version))
    return definitions


def _seed(writer, scope, at, count, *, label):
    rows, dependencies, truth = [], [], {}
    for index in range(count):
        trace, session, span = str(uuid4()), str(uuid4()), f"{label}-{index}"
        values = {f"value_{field}": float(index % 7 + field) for field in range(26)}
        rows.append(
            (
                span,
                trace,
                scope.project,
                scope.organization,
                session,
                at,
                at + timedelta(seconds=1),
                at,
                at,
                changes.ns(at),
                values,
            )
        )
        dependencies.append(
            changes.Dependency(trace, span, session, session, "", "", at)
        )
        truth[session] = values
    writer.store.client.execute(
        "INSERT INTO spans (id, trace_id, project_id, org_id, trace_session_id, "
        "start_time, end_time, created_at, updated_at, _version, attrs_number) VALUES",
        rows,
    )
    return dependencies, truth


def _batch(dependencies, cursor):
    return changes.ChangeBatch(
        tuple(d.key for d in dependencies),
        tuple(
            changes.UnitKey("conversation", d.canonical_session_id)
            for d in dependencies
        ),
        (),
        tuple(dependencies),
        (),
        {"watermark_ns": cursor},
    )


def _truth_check(store, head, definitions, scope, seeded):
    """Independent latest physical rows + Python arithmetic; no metric compiler."""
    source = store.client.execute(
        "SELECT toString(trace_session_id), start_time, attrs_number FROM ("
        "SELECT * FROM spans WHERE project_id = %(project)s "
        "AND org_id = %(org)s ORDER BY _version DESC LIMIT 1 BY trace_id, id) "
        "WHERE is_deleted = 0",
        {"project": scope.project, "org": scope.organization},
    )
    assert {session: values for session, _, values in source} == seeded
    expected_fields = {
        (str(d.metric_id), session): values[f"value_{index}"]
        for index, d in enumerate(definitions)
        if d.level == "conversation"
        for session, _, values in source
    }
    expected_buckets = defaultdict(lambda: [0, 0, 0.0, 0.0, 0.0, 0.0])
    for _, at, values in source:
        bucket = at.replace(minute=at.minute // 15 * 15, second=0, microsecond=0)
        bucket = bucket.replace(tzinfo=UTC)
        totals = expected_buckets[bucket]
        totals[0] += 1
        totals[1] += 1
        totals[2] += values["value_25"]
        totals[3] += 1
        totals[4] += 1
        totals[5] += 1
    fields, params = committed_rows_select(
        FIELD_VALUES, head, org_id=scope.organization, project_id=scope.project
    )
    actual_fields = store.execute(
        "SELECT toString(field_id), session_id, status, value_number "
        f"FROM ({fields}) WHERE is_deleted = 0 AND level = 'conversation'",
        params,
    )
    buckets, params = committed_rows_select(
        BUCKET_TOTALS, head, org_id=scope.organization, project_id=scope.project
    )
    span_rows = store.execute(
        "SELECT bucket_start, sum(unit_count), sum(valid_count), sum(weighted_n), "
        "sum(weighted_d), sum(sum_w), sum(sum_w2), sum(missing_count), sum(invalid_count) "
        f"FROM ({buckets}) WHERE is_deleted = 0 AND metric_id = %(span_metric)s "
        "GROUP BY bucket_start ORDER BY bucket_start",
        {**params, "span_metric": definitions[-1].metric_id},
    )
    fields_match = (
        len(actual_fields) == len(expected_fields)
        and all(row[2] == "ok" for row in actual_fields)
        and {(field, session): value for field, session, _, value in actual_fields}
        == expected_fields
    )
    actual_buckets = {row[0].replace(tzinfo=UTC): list(row[1:7]) for row in span_rows}
    buckets_match = actual_buckets == dict(expected_buckets) and all(
        row[7:] == (0, 0) for row in span_rows
    )
    return {
        "conversation_fields_match": fields_match,
        "span_bucket_aggregates_match": buckets_match,
        "actual_conversation_fields": len(actual_fields),
        "expected_conversation_fields": len(expected_fields),
        "actual_span_units": sum(row[1] for row in span_rows),
        "expected_span_units": len(source),
        "all_match": fields_match and buckets_match,
    }
