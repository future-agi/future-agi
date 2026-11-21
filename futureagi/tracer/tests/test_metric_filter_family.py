"""Metric family validation (no services), PG scope, and owned CH SQL checks."""

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from rest_framework import serializers

from tracer.models.derived_metric import MetricReadManifest
from tracer.serializers.filters import (
    EvalTaskFiltersField,
    FilterListField,
    FilterListQueryParamField,
    SessionBoundedFilterListQueryParamField,
    validate_persisted_filter_list,
)
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.derived_metrics import list_filter_capabilities
from tracer.services.derived_metrics.predicates import (
    METRIC_FIELD_STATUSES,
    STATUS_NO_VALUE,
    STATUS_OK,
    STATUS_OPEN,
    MetricFieldMetadata,
    MetricFilterCandidates,
    MetricFilterError,
    MetricReadScope,
    decode_metric_field_id,
    metric_field_id,
    resolve_metric_field,
    validate_metric_filter,
    validate_metric_filter_structure,
)
from tracer.utils.filter_operators import FILTER_TYPE_ALLOWED_OPS


@pytest.fixture
def metadata():
    return MetricFieldMetadata(
        metric_id=str(uuid4()),
        project_id=str(uuid4()),
        organization_id=str(uuid4()),
        workspace_id=str(uuid4()),
        version_id=str(uuid4()),
        version=1,
        level="conversation",
        value_type="boolean",
        kind="share",
        value_labels={"match": "Resolved", "other": "Not resolved"},
    )


def chip(meta, value_type="boolean", op="equals", value=True):
    return {
        "column_id": meta.identity,
        "property_id": meta.identity,
        "display_name": "Resolution rate",
        "source": "sessions",
        "filter_config": {
            "col_type": "METRIC_FIELD",
            "filter_type": value_type,
            "filter_op": op,
            "filter_value": value,
        },
    }


def sample(value_type, op):
    if op in {"is_null", "is_not_null"}:
        return None
    if op in {"between", "not_between"}:
        return [0, 10]
    if op in {"in", "not_in"}:
        return ["a", "b"]
    return {"boolean": True, "number": 2, "text": "a"}[value_type]


def validate_in_serializer(meta, rows, context="sessions", field_class=FilterListField):
    class Request(serializers.Serializer):
        filters = field_class()

    serializer = Request(
        data={"filters": rows},
        context={
            "project_id": meta.project_id,
            "organization_id": meta.organization_id,
            "workspace_id": meta.workspace_id,
            "metric_filter_context": context,
            "metric_field_resolver": lambda *args, **kwargs: meta,
        },
    )
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data["filters"]


@pytest.mark.parametrize(
    "context,level",
    [
        ("sessions", "conversation"),
        ("spans", "span"),
        ("task_sessions", "conversation"),
        ("task_spans", "span"),
    ],
)
@pytest.mark.parametrize(
    "value_type,op",
    [
        (t, op)
        for t in ("number", "boolean", "text")
        for op in sorted(FILTER_TYPE_ALLOWED_OPS[t])
    ],
)
def test_native_context_operator_matrix(metadata, context, level, value_type, op):
    meta = replace(metadata, level=level, value_type=value_type)
    item = chip(meta, value_type, op, sample(value_type, op))
    assert validate_in_serializer(meta, [item], context) == [item]


@pytest.mark.parametrize("op", sorted(FILTER_TYPE_ALLOWED_OPS["number"]))
def test_users_conversation_projection_uses_numbers_not_boolean_labels(metadata, op):
    item = chip(metadata, "number", op, sample("number", op))
    assert validate_in_serializer(metadata, [item], "users") == [item]
    with pytest.raises(serializers.ValidationError, match="active field"):
        validate_in_serializer(metadata, [chip(metadata)], "users")


@pytest.mark.parametrize(
    "context",
    [
        "sessions",
        "traces",
        "spans",
        "task",
        "task_sessions",
        "task_traces",
        "task_spans",
    ],
)
@pytest.mark.parametrize(
    "kind,rule", [("share", "any"), ("count", "every"), ("average", "average")]
)
def test_user_metric_wrong_grain_errors_precede_capability_gate(
    metadata, context, kind, rule
):
    meta = replace(metadata, level="user", kind=kind, user_rule=rule)
    with pytest.raises(MetricFilterError) as error:
        validate_metric_filter(chip(meta), meta, context=context)
    assert error.value.code == "metric_filter_wrong_grain"
    if context.startswith("task"):
        assert "can't filter tasks yet" in str(error.value)


@pytest.mark.parametrize(
    "kind,rule,value_type,value",
    [
        ("share", "any", "boolean", "Resolved"),
        ("count", "every", "boolean", "Not resolved"),
        ("average", "total", "number", 7),
    ],
)
@pytest.mark.parametrize("op", ["equals", "not_equals"])
def test_user_metric_gate_and_enabled_types(
    metadata, monkeypatch, kind, rule, value_type, value, op
):
    meta = replace(metadata, level="user", kind=kind, user_rule=rule)
    item = chip(meta, value_type, op, value)
    assert list_filter_capabilities.USER_LEVEL_METRIC_FILTER_ON_USERS is False
    with pytest.raises(serializers.ValidationError) as error:
        validate_in_serializer(meta, [item], "users")
    assert error.value.get_codes()["filters"][0][0] == "user_metric_filter_unavailable"
    monkeypatch.setattr(
        list_filter_capabilities, "USER_LEVEL_METRIC_FILTER_ON_USERS", True
    )
    assert validate_in_serializer(meta, [item], "users") == [item]


@pytest.mark.parametrize("op", sorted(FILTER_TYPE_ALLOWED_OPS["number"]))
def test_enabled_user_average_accepts_every_numeric_operator(metadata, monkeypatch, op):
    monkeypatch.setattr(
        list_filter_capabilities, "USER_LEVEL_METRIC_FILTER_ON_USERS", True
    )
    meta = replace(metadata, level="user", kind="average", user_rule="average")
    validate_metric_filter(
        chip(meta, "number", op, sample("number", op)), meta, context="users"
    )


@pytest.mark.parametrize(
    "value_type,op,value",
    [
        ("number", "gt", 2),
        ("number", "greater_than", True),
        ("number", "equals", float("inf")),
        ("number", "equals", None),
        ("number", "between", [3, 1]),
        ("boolean", "equals", "obsolete label"),
        ("boolean", "equals", 1),
        ("text", "in", []),
        ("text", "equals", None),
    ],
)
def test_bad_metric_values_and_undocumented_aliases_rejected(
    metadata, value_type, op, value
):
    meta = replace(metadata, value_type=value_type)
    with pytest.raises(MetricFilterError):
        validate_metric_filter(
            chip(meta, value_type, op, value), meta, context="sessions"
        )


@pytest.mark.parametrize(
    "field_class",
    [
        FilterListField,
        FilterListQueryParamField,
        SessionBoundedFilterListQueryParamField,
    ],
)
def test_persisted_metric_filter_identity_round_trip(metadata, field_class):
    item = chip(metadata, value="Resolved")
    stored = json.loads(json.dumps([item]))
    # A fresh copy each time: validation must not pass by mutating its input.
    payload = json.dumps(stored)
    if field_class is FilterListField:
        payload = json.loads(payload)
    assert validate_in_serializer(metadata, payload, field_class=field_class) == stored
    assert decode_metric_field_id(item["column_id"]) == metadata.metric_id


@pytest.mark.parametrize(
    "change", ["property", "family", "foreign", "scope", "identity"]
)
def test_identity_and_scope_cannot_be_spoofed(metadata, change):
    item = chip(metadata)
    meta = metadata
    if change == "property":
        item["property_id"] = metric_field_id(uuid4())
    elif change == "family":
        item["filter_config"]["col_type"] = "SPAN_ATTRIBUTE"
    elif change == "foreign":
        meta = replace(metadata, project_id=str(uuid4()))
    elif change == "scope":
        meta = replace(metadata, organization_id=str(uuid4()))
    else:
        item["column_id"] = "metric_field:malformed"

    class Request(serializers.Serializer):
        filters = FilterListField()

    request = Request(
        data={"filters": [item]},
        context={
            "project_id": metadata.project_id,
            "organization_id": metadata.organization_id,
            "metric_filter_context": "sessions",
            "metric_field_resolver": lambda *a, **kw: meta,
        },
    )
    assert not request.is_valid()


def test_missing_authorized_context_fails_closed(metadata):
    with pytest.raises(serializers.ValidationError, match="project scope"):
        FilterListField().run_validation([chip(metadata)])


def test_tasks_never_accept_user_metric_even_with_users_context(metadata, monkeypatch):
    monkeypatch.setattr(
        list_filter_capabilities, "USER_LEVEL_METRIC_FILTER_ON_USERS", True
    )
    meta = replace(metadata, level="user", user_rule="any")

    class Request(serializers.Serializer):
        filters = EvalTaskFiltersField()

    request = Request(
        data={"filters": {"filters": [chip(meta)]}},
        context={
            "project_id": meta.project_id,
            "metric_filter_context": "users",
            "metric_field_resolver": lambda *a, **kw: meta,
        },
    )
    assert not request.is_valid()
    assert "can't filter tasks yet" in str(request.errors)


@pytest.mark.django_db
def test_active_metadata_not_draft_and_foreign_deleted_rejected(
    workspace, observe_project
):
    from tracer.models.derived_metric import DerivedMetric, DerivedMetricVersion

    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="Scoped field",
    )

    def version(number, **kwargs):
        return DerivedMetricVersion.objects.create(
            metric=metric,
            version=number,
            formula="x",
            inputs=[],
            selection_unit="session",
            weighting="none",
            definition_hash=str(number) * 64,
            **kwargs,
        )

    active = version(
        1, value_type="boolean", value_labels={"match": "Active", "other": "Other"}
    )
    DerivedMetric.objects.filter(pk=metric.id).update(active_version=active)
    version(2, value_type="number", level="user", kind="average", user_rule="total")
    identity = metric_field_id(metric.id)
    meta = resolve_metric_field(identity, project_id=observe_project.id)
    assert (meta.version, meta.level, meta.value_type, meta.value_labels["match"]) == (
        1,
        "conversation",
        "boolean",
        "Active",
    )
    with pytest.raises(MetricFilterError, match="unavailable"):
        resolve_metric_field(identity, project_id=uuid4())
    DerivedMetric.objects.filter(pk=metric.id).update(deleted=True)
    with pytest.raises(MetricFilterError, match="unavailable"):
        resolve_metric_field(identity, project_id=observe_project.id)


def scope_for(
    meta, *, checkpoint=None, deltas=None, manifest=None, retention_start=None
):
    # An unsaved real manifest, so its allowed_attempts() reader contract is
    # what the SQL uses. Pins stay fakes: is_usable() reads durable PG state.
    if manifest is None:
        manifest = MetricReadManifest(
            project_id=meta.project_id,
            generation=max([1, *(g for g, _ in deltas or [])]),
            checkpoint_attempt_id=checkpoint,
            checkpoint_generation=1 if checkpoint else None,
            delta_generations=[g for g, _ in deltas or []],
            delta_attempt_ids=[a for _, a in deltas or []],
        )
    pin = SimpleNamespace(
        project_id=meta.project_id,
        definition_version_id=meta.version_id,
        manifest=manifest,
        manifest_id=manifest.id,
        is_usable=lambda: True,
    )
    return MetricReadScope(metadata=meta, pin=pin, retention_start=retention_start)


def translate(
    meta,
    scope,
    *,
    value_type=None,
    op="equals",
    value=True,
    context="sessions",
    outer_column="session_id",
    builder_class=ClickHouseFilterBuilderV2,
    candidates=None,
):
    builder = builder_class(
        project_id=meta.project_id,
        metric_filter_context=context,
        metric_read_scopes={meta.identity: scope},
        metric_candidates=candidates
        or MetricFilterCandidates(ids_param="metric_candidates"),
        metric_outer_column=outer_column,
    )
    return builder.translate([chip(meta, value_type or meta.value_type, op, value)])


def test_unintegrated_planners_fail_closed(metadata):
    with pytest.raises(MetricFilterError, match="pre-page"):
        ClickHouseFilterBuilder().translate([chip(metadata)])
    for name in ["bad; DROP TABLE x", "x.y", "x y"]:
        with pytest.raises(MetricFilterError):
            MetricFilterCandidates(relation=name)
    for column in ["unit_start; DROP TABLE x", "candidate.start", "start time"]:
        with pytest.raises(MetricFilterError):
            MetricFilterCandidates(relation="candidate_rows", unit_start_column=column)
    with pytest.raises(MetricFilterError):
        MetricFilterCandidates(
            ids_param="candidate_ids", unit_start_column="unit_start"
        )


def test_finite_retention_is_null_requires_canonical_candidate_starts(metadata):
    scope = scope_for(
        replace(metadata, value_type="number"),
        deltas=[(1, uuid4())],
        retention_start=datetime(2026, 9, 15, tzinfo=UTC),
    )
    with pytest.raises(MetricFilterError) as error:
        translate(
            replace(metadata, value_type="number"), scope, op="is_null", value=None
        )
    assert error.value.code == "metric_filter_planner_unavailable"


def test_sql_uses_exact_allowlist_then_tuple_collapse_no_list_date(metadata):
    meta = replace(metadata, value_type="number")
    base, committed = uuid4(), uuid4()
    scope = scope_for(meta, checkpoint=base, deltas=[(3, committed)])
    for builder in (ClickHouseFilterBuilder, ClickHouseFilterBuilderV2):
        sql, params = translate(meta, scope, value=5, builder_class=builder)
        assert "metric_field_values_checkpoint" in sql
        assert "argMax(tuple(is_deleted, status, unit_start" in sql
        assert sql.count("unit_id IN %(metric_candidates)s") == 2
        assert "unit_start >=" not in sql
        assert "start_date" not in sql and "FINAL" not in sql
        assert "generation <=" not in sql
        assert (1, base) in params.values() and ((3, committed),) in params.values()
        assert "LIMIT" not in sql
        assert sql.startswith("toString(session_id) IN (SELECT unit_id FROM")
        assert f"status = '{STATUS_OK}'" in sql
    sql, _ = translate(meta, scope, value=5, outer_column="s.trace_session_id")
    assert sql.startswith("toString(s.trace_session_id) IN (")
    for missing in (None, "", "toString(session_id)", "session_id OR 1", "a.b.c"):
        with pytest.raises(MetricFilterError) as error:
            translate(meta, scope, value=5, outer_column=missing)
        assert error.value.code == "metric_filter_planner_unavailable"
    scope.pin.is_usable = lambda: False
    with pytest.raises(MetricFilterError, match="expired"):
        translate(meta, scope, value=5)


def test_status_vocabulary_is_the_unit_writer_contract(metadata):
    # metric_field_values.status: ok | no_value | open (schema contract). A
    # never-calculated unit has no row; open units are neither value nor No value.
    assert (STATUS_OK, STATUS_NO_VALUE, STATUS_OPEN) == ("ok", "no_value", "open")
    assert METRIC_FIELD_STATUSES == {"ok", "no_value", "open"}
    meta = replace(metadata, value_type="number")
    scope = scope_for(meta, deltas=[(1, uuid4())])
    null_sql, _ = translate(meta, scope, op="is_null", value=None)
    present_sql, _ = translate(meta, scope, op="is_not_null", value=None)
    assert "status = 'no_value'" in null_sql and "status = 'ok'" not in null_sql
    assert "status = 'ok' AND isNotNull(value_number)" in present_sql
    for sql in (null_sql, present_sql):
        assert "'value'" not in sql and "in_progress" not in sql
        assert "not_calculated" not in sql


@pytest.mark.parametrize(
    "level,kind,rule,value_type,value",
    [
        ("conversation", "share", "", "number", 0.25),
        ("user", "share", "any", "boolean", "Resolved"),
        ("user", "average", "average", "number", 2),
    ],
)
def test_users_membership_waits_for_the_shared_per_user_planner(
    metadata, monkeypatch, level, kind, rule, value_type, value
):
    # Users values come only from the shared per-user rules in the Users list
    # planner; the generic hook validates and then fails closed.
    monkeypatch.setattr(
        list_filter_capabilities, "USER_LEVEL_METRIC_FILTER_ON_USERS", True
    )
    meta = replace(metadata, level=level, kind=kind, user_rule=rule)
    validate_metric_filter(
        chip(meta, value_type, "equals", value), meta, context="users"
    )
    scope = scope_for(meta, deltas=[(1, uuid4())])
    with pytest.raises(MetricFilterError) as error:
        translate(
            meta,
            scope,
            context="users",
            value_type=value_type,
            value=value,
            outer_column="end_user_id",
        )
    assert error.value.code == "metric_filter_planner_unavailable"


@pytest.mark.parametrize("same_manifest", [True, False])
def test_one_request_reads_one_committed_manifest(metadata, same_manifest):
    first_meta = replace(metadata, value_type="number")
    second_meta = replace(
        metadata, metric_id=str(uuid4()), version_id=str(uuid4()), value_type="number"
    )
    first = scope_for(first_meta, deltas=[(1, uuid4())])
    second = scope_for(
        second_meta,
        manifest=first.pin.manifest if same_manifest else None,
        deltas=None if same_manifest else [(2, uuid4())],
    )
    builder = ClickHouseFilterBuilderV2(
        project_id=metadata.project_id,
        metric_filter_context="sessions",
        metric_read_scopes={first_meta.identity: first, second_meta.identity: second},
        metric_candidates=MetricFilterCandidates(ids_param="metric_candidates"),
        metric_outer_column="session_id",
    )
    rows = [
        chip(first_meta, "number", "equals", 1),
        chip(second_meta, "number", "equals", 2),
    ]
    if same_manifest:
        sql, _ = builder.translate(rows)
        assert sql.count("toString(session_id) IN (") == 2
    else:
        with pytest.raises(MetricFilterError) as error:
            builder.translate(rows)
        assert error.value.code == "metric_filter_pin_mismatch"


@pytest.mark.parametrize(
    "level,context,code",
    [
        ("span", "sessions", "metric_filter_wrong_grain"),
        ("conversation", "spans", "metric_filter_wrong_grain"),
        ("trace", "users", "metric_filter_wrong_grain"),
        ("trace", "traces", "trace_metric_unavailable"),
        ("conversation", "", "metric_filter_wrong_grain"),
    ],
)
def test_other_wrong_grains_and_trace_gate(metadata, level, context, code):
    meta = replace(metadata, level=level)
    with pytest.raises(MetricFilterError) as error:
        validate_metric_filter(chip(meta), meta, context=context)
    assert error.value.code == code


def test_multi_project_planners_must_not_use_unqualified_id_membership(metadata):
    scope = scope_for(metadata, deltas=[(1, uuid4())])
    builder = ClickHouseFilterBuilderV2(
        project_ids=[metadata.project_id, str(uuid4())],
        metric_filter_context="sessions",
        metric_read_scopes={metadata.identity: scope},
        metric_candidates=MetricFilterCandidates(ids_param="candidate_ids"),
        metric_outer_column="session_id",
    )
    with pytest.raises(MetricFilterError, match="single-project"):
        builder.translate([chip(metadata)])


@pytest.fixture(scope="module")
def live_store():
    port = os.environ.get("FI_METRIC_SCHEMA_TEST_HTTP_PORT")
    if not port:
        pytest.skip("owned ClickHouse sidecar not requested")
    import clickhouse_connect

    from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements

    client = clickhouse_connect.get_client(
        host="127.0.0.1", port=int(port), username="default", password=""
    )
    database = "test_metric_filters_" + uuid4().hex
    client.command(f"CREATE DATABASE {database}")
    scoped = clickhouse_connect.get_client(
        host="127.0.0.1",
        port=int(port),
        username="default",
        password="",
        database=database,
    )
    schema = (
        Path(__file__).resolve().parents[1]
        / "services/clickhouse/v2/schema/031_metric_field_values.sql"
    )
    try:
        for statement in split_statements(schema.read_text()):
            scoped.command(statement)
        yield scoped
    finally:
        scoped.close()
        client.command(f"DROP DATABASE {database}")
        client.close()


def insert_field(
    store, meta, attempt, generation, unit_id, *, table="metric_field_values", **changes
):
    old = datetime(2026, 9, 1, tzinfo=UTC)
    row = {
        "org_id": UUID(meta.organization_id),
        "workspace_id": UUID(meta.workspace_id),
        "project_id": UUID(meta.project_id),
        "field_id": UUID(meta.metric_id),
        "field_version": meta.version,
        "level": "conversation",
        "unit_id": unit_id,
        "session_id": unit_id,
        "unit_start": old,
        "bucket_start": old,
        "settled_at": old,
        "status": STATUS_OK,
        "value_bool": 1,
        "value_number": 5.0,
        "generation": generation,
        "attempt_id": attempt,
        "fencing_epoch": 1,
        "row_sequence": generation,
        "_version": generation,
        "is_deleted": 0,
    }
    row.update(changes)
    # The unit writer's row shape: a deleted unit is No value with no settle
    # time, an open unit has no settle time, and values exist only when ok.
    if row["is_deleted"]:
        row["status"] = STATUS_NO_VALUE
    if row["status"] not in METRIC_FIELD_STATUSES:
        raise AssertionError(f"not a writer status: {row['status']}")
    if row["is_deleted"] or row["status"] == STATUS_OPEN:
        row["settled_at"] = None
    if row["status"] != STATUS_OK:
        row.update(value_bool=None, value_number=None, value_text=None)
    store.insert(table, [list(row.values())], column_names=list(row))


@pytest.mark.parametrize(
    "op,expected",
    [
        ("equals", {"old-start"}),
        ("is_null", {"missing"}),
        ("is_not_null", {"old-start", "negative"}),
        ("not_equals", {"negative"}),
    ],
)
def test_live_committed_membership_before_page_count_sampling(
    live_store, metadata, op, expected
):
    meta = replace(metadata, value_type="number")
    base, commit, abort = uuid4(), uuid4(), uuid4()
    scope = scope_for(meta, checkpoint=base, deltas=[(3, commit)])
    # "unwritten" is a listed candidate the writer never calculated: no row.
    for unit in ("old-start", "missing", "deleted", "negative", "open"):
        insert_field(
            live_store, meta, base, 1, unit, table="metric_field_values_checkpoint"
        )
    for unit, changes in [
        ("missing", {"status": STATUS_NO_VALUE}),
        ("deleted", {"is_deleted": 1}),
        ("negative", {"value_number": 9.0}),
        ("open", {"status": STATUS_OPEN}),
    ]:
        insert_field(live_store, meta, commit, 3, unit, **changes)
    insert_field(live_store, meta, abort, 2, "old-start", is_deleted=1)
    insert_field(live_store, meta, uuid4(), 4, "old-start", value_number=100.0)
    sql, params = translate(meta, scope, op=op, value=5)
    candidates = ("old-start", "missing", "deleted", "negative", "open", "unwritten")
    params["metric_candidates"] = candidates
    # Native membership keeps old-start because its current root is in range;
    # a global unit_start floor here would incorrectly lose it.
    insert_field(live_store, meta, commit, 3, "out-of-range")
    params["metric_candidates"] = (*candidates, "out-of-range")
    params["outer_candidates"] = [*candidates, "out-of-range"]
    params["start_date"] = datetime(2026, 10, 1)
    params["end_date"] = datetime(2026, 11, 1)
    # Root-based list membership remains separate from the field's September
    # anchor. This is the logical classifier seam, not a full list benchmark.
    outer = (
        "SELECT session_id FROM (SELECT arrayJoin(%(outer_candidates)s) AS session_id) "
        "WHERE if(session_id = 'out-of-range', toDateTime64('2026-09-01', 6), "
        "toDateTime64('2026-10-15', 6)) >= %(start_date)s "
        "AND if(session_id = 'out-of-range', toDateTime64('2026-09-01', 6), "
        "toDateTime64('2026-10-15', 6)) < %(end_date)s"
    )
    selected = f"SELECT session_id FROM ({outer}) WHERE {sql} ORDER BY session_id"
    assert {
        r[0] for r in live_store.query(selected, parameters=params).result_rows
    } == expected
    assert live_store.query(
        f"SELECT count() FROM ({selected})", parameters=params
    ).result_rows == [(len(expected),)]
    assert live_store.query(selected + " LIMIT 1", parameters=params).result_rows == [
        (sorted(expected)[0],)
    ]
    sampled = f"SELECT session_id FROM ({outer}) WHERE ({sql}) AND modulo(cityHash64(session_id), 2) = 0 ORDER BY session_id LIMIT 1"
    expected_sample = live_store.query(
        f"SELECT session_id FROM ({selected}) WHERE modulo(cityHash64(session_id), 2) = 0 ORDER BY session_id LIMIT 1",
        parameters=params,
    ).result_rows
    assert live_store.query(sampled, parameters=params).result_rows == expected_sample
    for table in ("metric_field_values", "metric_field_values_checkpoint"):
        live_store.command(f"OPTIMIZE TABLE {table} FINAL")
    assert {
        r[0] for r in live_store.query(selected, parameters=params).result_rows
    } == expected


@pytest.mark.parametrize(
    "op,value,expected",
    [
        ("equals", 5, {"b"}),
        ("not_equals", 5, {"a", "c"}),
        ("greater_than", 5, {"c"}),
        ("greater_than_or_equal", 5, {"b", "c"}),
        ("less_than", 5, {"a"}),
        ("less_than_or_equal", 5, {"a", "b"}),
        ("between", [0, 5], {"a", "b"}),
        ("not_between", [0, 5], {"c"}),
        ("is_null", None, {"missing"}),
        ("is_not_null", None, {"a", "b", "c"}),
    ],
)
def test_live_numeric_operator_matrix(live_store, metadata, op, value, expected):
    meta = replace(metadata, value_type="number")
    attempt = uuid4()
    for name, number in [("a", 0), ("b", 5), ("c", 10), ("missing", None)]:
        insert_field(
            live_store,
            meta,
            attempt,
            1,
            name,
            value_number=number,
            status=STATUS_NO_VALUE if number is None else STATUS_OK,
        )
    sql, params = translate(
        meta, scope_for(meta, deltas=[(1, attempt)]), op=op, value=value
    )
    params["metric_candidates"] = ("a", "b", "c", "missing")
    result = live_store.query(
        f"SELECT session_id FROM (SELECT arrayJoin(['a', 'b', 'c', 'missing']) AS session_id) WHERE {sql}",
        parameters=params,
    ).result_rows
    assert {r[0] for r in result} == expected


@pytest.mark.parametrize(
    "op,value,expected",
    [
        ("equals", 5, {"kept"}),
        (
            "is_null",
            None,
            {"missing", "expired", "expired-missing", "expired-purged"},
        ),
        ("is_not_null", None, {"kept"}),
        ("not_equals", 7, {"kept"}),
    ],
)
def test_live_retention_expired_units_remain_no_value_after_purge(
    live_store, metadata, op, value, expected
):
    # Expired cells are No value for is_null even after their field rows are
    # physically purged. Other operators cannot read or match expired values.
    meta = replace(metadata, value_type="number")
    attempt = uuid4()
    recent = datetime(2026, 10, 1, tzinfo=UTC)
    expired = datetime(2026, 9, 1, tzinfo=UTC)
    for unit, changes in [
        ("kept", {"unit_start": recent}),
        ("missing", {"unit_start": recent, "status": STATUS_NO_VALUE}),
        ("expired", {"unit_start": expired}),
        ("expired-missing", {"unit_start": expired, "status": STATUS_NO_VALUE}),
    ]:
        insert_field(live_store, meta, attempt, 1, unit, **changes)
    live_store.command(
        "CREATE TABLE IF NOT EXISTS metric_candidate_rows "
        "(unit_id String, unit_start DateTime64(6, 'UTC')) ENGINE=Memory"
    )
    live_store.command("TRUNCATE TABLE metric_candidate_rows")
    candidates = [
        ("kept", recent),
        ("missing", recent),
        ("expired", expired),
        ("expired-missing", expired),
        # This candidate has no field row, as after the retention purge.
        ("expired-purged", expired),
    ]
    live_store.insert(
        "metric_candidate_rows",
        candidates,
        column_names=["unit_id", "unit_start"],
    )
    scope = scope_for(
        meta,
        deltas=[(1, attempt)],
        retention_start=datetime(2026, 9, 15, tzinfo=UTC),
    )
    sql, params = translate(
        meta,
        scope,
        op=op,
        value=value,
        candidates=MetricFilterCandidates(
            relation="metric_candidate_rows", unit_start_column="unit_start"
        ),
    )
    units = [unit for unit, _ in candidates]
    params["outer_candidates"] = units
    query = (
        "SELECT session_id FROM (SELECT arrayJoin(%(outer_candidates)s) AS session_id) "
        f"WHERE {sql}"
    )
    assert {
        r[0] for r in live_store.query(query, parameters=params).result_rows
    } == expected


def test_metric_filters_never_carry_skill_groups_or_the_skill_family(metadata):
    # Skills correlation groups and the SKILL family stay Skills-only, both for
    # stored saved-view filters and for scoped list requests.
    grouped = {**chip(metadata), "group": 1}
    as_skill = chip(metadata)
    as_skill["filter_config"]["col_type"] = "SKILL"
    for item, message in (
        (grouped, "group is supported only for SKILL filters"),
        (as_skill, "SKILL filters cannot carry a metric field identity"),
    ):
        with pytest.raises(serializers.ValidationError, match=message):
            validate_persisted_filter_list([json.loads(json.dumps(item))])
        with pytest.raises(serializers.ValidationError, match=message):
            FilterListField().run_validation([json.loads(json.dumps(item))])


def test_persisted_filters_are_checked_structurally_when_opted_in(metadata):
    item = chip(metadata, value="Resolved")
    stored = json.loads(json.dumps([item]))
    assert validate_persisted_filter_list(json.loads(json.dumps(stored))) == stored
    number = chip(replace(metadata, value_type="number"), "number", "between", [1, 2])
    expected = json.loads(json.dumps([number]))
    assert validate_persisted_filter_list([number]) == expected
    assert validate_metric_filter_structure(chip(metadata, op="is_null", value=None))
    # Without the explicit opt-in a detached list still needs a list scope.
    with pytest.raises(serializers.ValidationError, match="project scope"):
        FilterListField().run_validation(stored)


@pytest.mark.parametrize(
    "change",
    [
        "identity",
        "property",
        "family",
        "type",
        "alias_op",
        "infinite",
        "int_bool",
        "empty_label",
        "provenance",
    ],
)
def test_persisted_metric_filters_reject_bad_structure(metadata, change):
    item = chip(metadata)
    config = item["filter_config"]
    if change == "identity":
        item["column_id"] = item["property_id"] = "metric_field:not-a-uuid"
    elif change == "property":
        item["property_id"] = metric_field_id(uuid4())
    elif change == "family":
        config["col_type"] = "SPAN_ATTRIBUTE"
    elif change == "type":
        config["filter_type"] = "array"
    elif change == "alias_op":
        config.update(filter_type="number", filter_op="gt", filter_value=1)
    elif change == "infinite":
        config.update(filter_type="number", filter_value=float("inf"))
    elif change == "int_bool":
        config["filter_value"] = 1
    elif change == "empty_label":
        config["filter_value"] = " "
    else:
        config["attribute_value_types"] = ["number"]
    with pytest.raises(serializers.ValidationError):
        validate_persisted_filter_list([item])
