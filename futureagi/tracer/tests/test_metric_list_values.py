# ruff: noqa: F811 -- shared PG-only catalog fixture
"""B11 metadata is PG-only; cell policy/state tests need no ClickHouse."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from tracer.models.derived_metric import DerivedMetric, MetricStateConflict
from tracer.services.derived_metrics.list_values import (
    MetricPageUnit,
    _cell,
    _field_cells_select,
    hydrate_metric_list_values,
    page_lifecycle_select,
)
from tracer.services.derived_metrics.queries import Scope
from tracer.tests.test_property_catalog_metric_fields import (
    create_metric,
    create_version,
    page,
    tenant,  # noqa: F401
)
from tracer.utils.helper import update_column_config_based_on_metric_fields

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
START = NOW - timedelta(days=1)
ARRIVAL = int((NOW - timedelta(hours=1)).timestamp()) * 10**9


def definition(**kwargs):
    return SimpleNamespace(
        **{
            "metric_id": uuid4(),
            "version": 1,
            "value_type": "boolean",
            "value_format": "",
            "value_labels": {"match": "Resolved", "other": "Not resolved"},
            **kwargs,
        }
    )


def field(**kwargs):
    return {
        "unit_start": START,
        "last_arrival_ns": ARRIVAL,
        "settled_at": NOW - timedelta(minutes=30),
        "status": "ok",
        "no_value_reason": "",
        "value_bool": 0,
        "value_number": None,
        "value_text": None,
        "is_deleted": 0,
        **kwargs,
    }


def cell(row, *, lifecycle=None, page_unit=None, version=None, cutoff=None):
    return _cell(
        version or definition(),
        SimpleNamespace(id=uuid4()),
        page_unit or MetricPageUnit(str(uuid4())),
        row,
        lifecycle,
        now=NOW,
        cutoff=cutoff,
        settle_minutes=30,
    )


@pytest.mark.parametrize(
    "context,level,expected",
    [
        ("sessions", "conversation", 1),
        ("spans", "span", 1),
        ("traces", "trace", 0),
        ("sessions", "user", 0),
        ("spans", "conversation", 0),
        ("users", "conversation", 1),
        ("users", "user", 1),
    ],
)
def test_column_config_per_list_uses_scoped_catalog_without_ch(
    tenant, context, level, expected
):
    metric, _ = create_metric(
        tenant,
        level="conversation" if level == "trace" else level,
        user_rule="any" if level == "user" else "",
    )
    if level == "trace":
        # Simulate an existing trace pointer: the current activation API refuses
        # new ones while M12 is closed, and discovery must hide old ones too.
        legacy_trace = create_version(metric, version=2, level="trace")
        DerivedMetric.no_workspace_objects.filter(id=metric.id).update(
            active_version=legacy_trace
        )
    with patch(
        "tracer.services.derived_metrics.publication.ClickHousePublicationStore.execute",
        side_effect=AssertionError("metadata may not hydrate"),
    ):
        descriptors = list(page(tenant, context=context).metrics)
        result = update_column_config_based_on_metric_fields(
            [], descriptors, context=context
        )
    assert len(result) == expected
    if result:
        assert result[0]["hydration"] == "deferred"
        assert result[0]["group_by"] == "Metrics"
        assert not result[0]["is_visible"]
        assert (
            result[0]["id"] == result[0]["property_id"] == descriptors[0]["property_id"]
        )
        assert result[0]["version"] == 1
        assert "value" not in result[0]


@pytest.mark.parametrize(
    "kind,value_type,projection",
    [
        ("share", "number", "per_user"),
        ("count", "number", "per_user"),
        ("average", "number", "per_user"),
    ],
)
def test_users_projection_column_never_borrows_conversation_boolean_labels(
    tenant, kind, value_type, projection
):
    create_metric(tenant, kind=kind, unit="ms", value_format="decimal")
    item = update_column_config_based_on_metric_fields(
        [], list(page(tenant, context="users").metrics), context="users"
    )[0]
    assert item["value_type"] == item["output_type"] == value_type
    assert item["projection"] == projection and item["value_labels"] == {}
    assert item["format"] == (
        "percent" if kind == "share" else "number" if kind == "count" else "decimal"
    )


def test_configs_preserve_order_visibility_without_mutation_and_drop_unavailable_fields(
    tenant,
):
    metric, active = create_metric(tenant)
    descriptors = list(page(tenant, context="sessions").metrics)
    identity = descriptors[0]["property_id"]
    saved = [
        {"id": "session_id", "name": "Session"},
        {"id": identity, "is_visible": True, "version": 0},
        {"id": f"metric_field:{uuid4()}"},
    ]
    original = deepcopy(saved)
    before = update_column_config_based_on_metric_fields(
        saved, descriptors, context="sessions"
    )
    create_version(
        metric,
        version=2,
        value_labels={"match": "Draft", "other": "Draft other"},
        backfill_status="running",
    )
    assert (
        update_column_config_based_on_metric_fields(
            saved, list(page(tenant, context="sessions").metrics), context="sessions"
        )
        == before
    )
    assert saved == original
    assert before[1]["version"] == active.version and before[1]["is_visible"]
    assert before[1]["value_labels"]["match"] == "Resolved" and len(before) == 2
    assert before[0] == original[0] and before[0] is not original[0]


@pytest.mark.parametrize(
    "kwargs,state,reason",
    [
        ({}, "value", ""),
        ({"status": "open", "value_bool": None}, "not_calculated", ""),
        ({"last_arrival_ns": int(NOW.timestamp()) * 10**9}, "in_progress", ""),
        (
            {"status": "no_value", "no_value_reason": "not_evaluated"},
            "no_value",
            "not_evaluated",
        ),
        (
            {"status": "no_value", "no_value_reason": "zero_denominator"},
            "no_value",
            "zero_denominator",
        ),
        (
            {"status": "no_value", "no_value_reason": "invalid_probability"},
            "no_value",
            "invalid_probability",
        ),
        ({"is_deleted": 1}, "no_value", "deleted"),
        ({"value_bool": None}, "no_value", "attribute_absent"),
    ],
)
def test_every_stored_state_has_explicit_missing_semantics(kwargs, state, reason):
    result = cell(field(**kwargs))
    assert result["state"] == state and result["reason"] == reason
    if state != "value":
        assert result["value"] is None and result["label"] is None
    else:
        assert result["value"] is False and result["label"] == "Not resolved"


@pytest.mark.parametrize(
    "value_type,value", [("number", 0.0), ("text", ""), ("boolean", 0)]
)
def test_actual_zero_false_and_empty_text_are_values(value_type, value):
    column = {"number": "value_number", "text": "value_text", "boolean": "value_bool"}[
        value_type
    ]
    result = cell(field(**{column: value}), version=definition(value_type=value_type))
    assert result["state"] == "value" and result["value"] == value
    assert result["counts"] == {"eligible": 1, "evaluated": 1, "missing": 0}
    if value_type != "boolean":
        assert result["label"] is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_never_leaves_cell(value):
    result = cell(field(value_number=value), version=definition(value_type="number"))
    assert result["state"] == "no_value" and result["reason"] == "non_finite"
    assert result["value"] is None


def test_no_row_unknown_anchor_is_not_zero_or_an_invented_settle():
    assert cell(None)["reason"] == "unknown_anchor"
    page_unit = MetricPageUnit(str(uuid4()), START, ARRIVAL)
    assert cell(None, page_unit=page_unit)["state"] == "not_calculated"
    result = cell(None, lifecycle={"first_seen": START, "last_arrival_ns": ARRIVAL})
    assert result["state"] == "no_value" and result["reason"] == "unknown_anchor"
    assert result["value"] is None


@pytest.mark.parametrize("anchor_source", ["field", "page"])
def test_old_queue_first_seen_cannot_expire_an_authoritative_live_anchor(anchor_source):
    result = cell(
        field() if anchor_source == "field" else None,
        page_unit=MetricPageUnit(
            str(uuid4()), START if anchor_source == "page" else None
        ),
        lifecycle={"first_seen": NOW - timedelta(days=20), "last_arrival_ns": ARRIVAL},
        cutoff=NOW - timedelta(days=7),
    )
    assert result["state"] == (
        "value" if anchor_source == "field" else "not_calculated"
    )
    assert result["reason"] == ""


@pytest.mark.parametrize("has_field", [False, True])
@pytest.mark.parametrize("queue_age", [0, 20])
def test_queue_first_seen_never_proves_an_unknown_anchor(has_field, queue_age):
    result = cell(
        field(unit_start=datetime(1970, 1, 1, tzinfo=UTC)) if has_field else None,
        lifecycle={
            "first_seen": NOW - timedelta(days=queue_age),
            "last_arrival_ns": ARRIVAL,
        },
        cutoff=NOW - timedelta(days=7),
    )
    assert result["state"] == "no_value" and result["reason"] == "unknown_anchor"
    assert result["value"] is None
    assert result["counts"] == {"eligible": 0, "evaluated": 0, "missing": 0}


def test_reopened_arrival_floor_hides_old_value_then_waits_for_recalculation():
    recent = int(NOW.timestamp()) * 10**9
    assert (
        cell(field(), lifecycle={"first_seen": START, "last_arrival_ns": recent})[
            "state"
        ]
        == "in_progress"
    )
    older = ARRIVAL + 60 * 10**9
    assert (
        cell(field(), lifecycle={"first_seen": START, "last_arrival_ns": older})[
            "state"
        ]
        == "not_calculated"
    )


def test_retention_beats_open_and_old_generation_values_and_handles_purged_rows():
    cutoff = NOW - timedelta(hours=2)
    for row in (field(), field(status="open"), None):
        result = cell(
            row, page_unit=MetricPageUnit(str(uuid4()), START, ARRIVAL), cutoff=cutoff
        )
        assert result["reason"] == "retention_expired" and result["value"] is None
        assert result["counts"] == {"eligible": 0, "evaluated": 0, "missing": 0}


def test_sql_pushes_keys_and_exact_allowlist_inside_both_collapses():
    scope = Scope(uuid4(), uuid4(), uuid4())
    checkpoint, delta = uuid4(), uuid4()
    manifest = SimpleNamespace(
        project_id=scope.project,
        checkpoint_attempt_id=checkpoint,
        delta_attempt_ids=[delta],
        allowed_attempts=lambda: [(1, checkpoint), (2, delta)],
    )
    ids = (str(uuid4()), str(uuid4()))
    sql, params = _field_cells_select(scope, definition(), ids, manifest, NOW)
    assert params["field_read_units"] == ids
    # Pushed inside the collapse in every branch: each checkpoint bucket branch
    # (page reads seek only their own physical tuple) and the delta branch.
    from tracer.services.derived_metrics.storage import FIELD_CHECKPOINT_BUCKETS

    assert sql.count("unit_id IN %(field_read_units)s") == FIELD_CHECKPOINT_BUCKETS + 1
    assert (
        "attempt_id IN %(field_read_deltas)s" in sql
        and "attempt_id = %(field_read_checkpoint)s" in sql
    )
    assert sql.index("unit_start <") < sql.index("FROM (SELECT *")
    collapse = sql[sql.index("ORDER BY generation DESC") :]
    assert "WHERE status" not in collapse and "WHERE unit_start" not in collapse
    assert "generation <=" not in sql and " FINAL" not in sql
    assert "value_number) AS value_number" in sql
    assert "unit_start > toDateTime64(0, 6, 'UTC')" in sql
    sql, params = page_lifecycle_select(scope, ids, manifest)
    assert params["list_page_ids"] == ids
    assert "unit_id IN %(list_page_ids)s" in sql
    assert "WHERE (generation, attempt_id) IN %(list_attempts)s" in sql
    assert "is_deleted = 0" not in sql and "due_at <=" not in sql
    assert "first_seen" not in sql


@pytest.mark.parametrize(
    "pages",
    [
        ["raw-id"],
        [MetricPageUnit(str(uuid4()))] * 2,
        [MetricPageUnit(str(uuid4())) for _ in range(101)],
    ],
)
def test_bad_or_unbounded_pages_fail_before_any_io(pages):
    store = Mock()
    with pytest.raises(ValueError):
        hydrate_metric_list_values(
            store,
            Scope(uuid4(), uuid4(), uuid4()),
            uuid4(),
            pages,
            retention_days=lambda: 0,
        )
    store.execute.assert_not_called()


def test_empty_page_does_not_pin_or_read():
    store = Mock()
    assert (
        hydrate_metric_list_values(
            store,
            Scope(uuid4(), uuid4(), uuid4()),
            uuid4(),
            [],
            retention_days=lambda: 0,
        )
        == {}
    )
    store.execute.assert_not_called()


@pytest.mark.parametrize(
    "start,arrival",
    [
        (datetime(2026, 1, 1), None),
        (None, -1),
        (None, 0),
        (None, True),
        (None, 2**64),
    ],
)
def test_page_unit_requires_authoritative_timestamp_types(start, arrival):
    with pytest.raises(ValueError):
        MetricPageUnit(str(uuid4()), start, arrival)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"unit_start": datetime(1970, 1, 1, tzinfo=UTC)},
        {"last_arrival_ns": 0},
    ],
)
def test_unknown_stored_anchor_or_arrival_never_exposes_a_value(kwargs):
    result = cell(field(**kwargs))
    assert result["state"] == "no_value"
    assert result["reason"] == "unknown_anchor"
    assert result["value"] is None


def test_lifecycle_scope_mismatch_fails_before_sql():
    with pytest.raises(MetricStateConflict):
        page_lifecycle_select(
            Scope(uuid4(), uuid4(), uuid4()),
            (str(uuid4()),),
            SimpleNamespace(project_id=uuid4()),
        )
