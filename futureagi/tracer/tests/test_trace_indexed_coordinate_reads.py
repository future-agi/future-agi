"""Immutable primary-index coordinates may prune I/O, never latest membership."""

from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse.query_builders.trace_list import (
    LatestFilterPredicate,
    TraceListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
    _replays_native_any_span_column,
    _replays_typed_attributes,
)
from tracer.tests.test_bounded_trace_filter_reads import (
    _attribute_filter,
    _render_driver_sql,
    _time_filter,
)
from tracer.tests.test_trace_root_physical_replay import assert_coherent_classifier

pytestmark = pytest.mark.unit
PROJECT = "00000000-0000-4000-8000-000000000001"
END = datetime(2026, 9, 5)


def builder(
    width=1,
    *,
    kind="text",
    operation="equals",
    value="long ' value",
    cls=TraceListQueryBuilderV2,
    **kwargs,
):
    return cls(
        project_id=PROJECT,
        filters=[
            _time_filter(END - timedelta(days=365), END),
            *[
                _attribute_filter(
                    f"attribute_{index}", value, filter_type=kind, operation=operation
                )
                for index in range(width)
            ],
        ],
        page_size=25,
        **kwargs,
    )


@pytest.mark.parametrize("width", [1, 2, 5, 10])
@pytest.mark.parametrize(
    "kind,operation,value",
    [
        ("text", "equals", "001234"),
        ("text", "contains", "long" * 600),
        ("text", "not_equals", "absent"),
        ("text", "is_null", None),
        ("number", "greater_than", 0.01),
        ("number", "equals", 0),
        ("boolean", "equals", False),
    ],
)
def test_coordinate_seed_cannot_filter_versions_values_roots_or_child_time(
    width, kind, operation, value
):
    subject = builder(width, kind=kind, operation=operation, value=value)
    assert subject._uses_scalar_coordinate_replay()
    # The short exact-string lane sizes its classifier to the ordered prefix
    # its own page can publish plus a quarter of precision headroom, clamped to
    # [64, 200] - so 64 for this twenty-five-row page. Every other
    # coordinate-replay shape keeps the 200 that equals the seed limit, and the
    # SEED limit is 200 on all of them: an extra classifier chunk is cheap, a
    # re-seed is not.
    assert subject.recommended_filter_classify_batch_size() == (
        64 if subject._uses_short_text_candidate_seed() else 200
    )
    assert subject.recommended_filter_cursor_seed_batch_size() == 200
    assert not subject.allow_filter_anchor_probe_for_initial_continuation()
    assert (
        subject.recommended_filter_candidate_witness_fallback_classify_batch_size()
        == 200
    )
    assert not subject.prefer_filter_candidate_witness_probe_first()
    coordinates = subject._filter_classifier_coordinate_predicate()
    for forbidden in (
        "is_deleted",
        "parent_span_id",
        "attrs_",
        "span_attr",
        "LIMIT",
        "FINAL",
        "_version",
        "start_date",
        "end_date",
        "latest_filter",
    ):
        assert forbidden not in coordinates
    assert "SELECT DISTINCT observation_type, service_name," in coordinates
    assert "toStartOfHour(start_time), trace_id" in coordinates
    assert "project_id = %(project_id)s" in coordinates
    assert "trace_id IN %(candidate_trace_ids)s" in coordinates
    sql, params = subject.build_filter_identity_match_query_from_seed_rows(
        [
            {"trace_id": "one", "root_span_id": "stale-root"},
            {"trace_id": "two", "root_span_id": "other-root"},
        ]
    )
    assert "SELECT DISTINCT observation_type, service_name," in sql
    assert (
        "GROUP BY observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        in sql
    )
    assert_coherent_classifier(sql)
    assert "WHERE latest_is_deleted = 0" in sql
    assert "latest_attr_exists_0" in sql
    assert params["candidate_trace_ids"] == ("one", "two")
    assert "stale-root" not in str(params)
    _render_driver_sql(sql, params)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bounded_internal_scan": True},
        {"bounded_identity_only": True},
        {"bounded_sampling_salt": "test", "bounded_sampling_rate": 10},
        {"search": "query"},
    ],
)
def test_other_execution_modes_keep_existing_path(kwargs):
    subject = builder(**kwargs)
    assert not subject._uses_scalar_coordinate_replay()
    assert subject._filter_classifier_coordinate_predicate() == ""


def test_legacy_schema_never_receives_v2_primary_coordinates():
    subject = builder(cls=TraceListQueryBuilder)
    assert subject._filter_classifier_coordinate_predicate() == ""
    assert subject.recommended_filter_classify_batch_size() == 10


def test_project_list_keeps_existing_path_and_json_never_uses_scalar_witness():
    subject = builder()
    subject.project_ids = [PROJECT]
    assert not subject._uses_scalar_coordinate_replay()
    subject = builder(kind="map", value={"path": "value"})
    assert not subject._uses_scalar_coordinate_replay()
    assert subject._uses_attribute_coordinate_replay()


@pytest.mark.parametrize("width", [1, 2, 5, 10])
@pytest.mark.parametrize(
    "kind,operation,value",
    [
        ("map", "contains", {"tier": "VIP"}),
        ("map", "not_equals", {"n": 1}),
        ("map", "is_null", None),
        ("array", "contains", ["0012", 12, True]),
        ("array", "not_contains", ["x"]),
        ("array", "is_not_null", None),
    ],
)
def test_json_uses_same_complete_prefix_and_exact_typed_classifier(
    width, kind, operation, value
):
    subject = builder(width, kind=kind, operation=operation, value=value)
    assert subject._uses_attribute_coordinate_replay()
    assert not subject._uses_scalar_coordinate_replay()
    assert not subject.supports_filter_windowed_candidate_seed_page()
    coordinates = subject._filter_classifier_coordinate_predicate()
    for forbidden in (
        "JSON",
        "LIMIT",
        "is_deleted",
        "parent_span_id",
        "start_date",
        "end_date",
    ):
        assert forbidden not in coordinates
    assert "trace_id IN %(candidate_trace_ids)s" in coordinates
    sql, params = subject.build_filter_identity_match_query_from_seed_rows(
        [{"trace_id": "one", "root_span_id": "not-authoritative"}]
    )
    assert "JSON" in sql
    assert "SELECT DISTINCT observation_type, service_name," in sql
    assert (
        "GROUP BY observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        in sql
    )
    assert "WHERE latest_is_deleted = 0" in sql
    assert "not-authoritative" not in str(params)
    _render_driver_sql(sql, params)


@pytest.mark.parametrize("days", [7, 30, 365])
def test_numeric_seed_limits_root_population_not_child_history(days):
    subject = builder(kind="number", operation="greater_than", value=0.01)
    start = END - timedelta(days=days)
    sql, params = subject.build_filter_windowed_candidate_seed_page(
        slice_start=start, slice_end=END, limit=50
    )
    population = subject._scalar_candidate_root_population_predicate()
    assert subject.supports_filter_windowed_candidate_seed_page()
    assert "start_time >= fromUnixTimestamp64Micro" in population
    assert "start_time < fromUnixTimestamp64Micro" in population
    assert "parent_span_id IS NULL OR parent_span_id = ''" in population
    for forbidden in ("is_deleted", "LIMIT", "attrs_", "filter_before", "FINAL"):
        assert forbidden not in population
    # Time belongs only to the necessary raw-root subquery, not to the
    # all-history numeric witness query wrapped around that subquery.
    seed, _ = sql.split("SELECT trace_id, id AS root_span_id, start_time", 1)
    assert seed.count("start_time >=") == 1
    assert seed.count("start_time <") == 1
    assert "attrs_number" in seed
    assert "LIMIT" not in seed
    assert "is_deleted" not in seed
    assert params["filter_slice_start"] == start
    _render_driver_sql(sql, params)


def test_non_numeric_and_internal_shapes_do_not_opt_into_primary_seed_budget():
    assert not builder().supports_filter_windowed_candidate_seed_page()
    subject = builder(
        kind="number", operation="greater_than", value=0.01, bounded_internal_scan=True
    )
    assert not subject.supports_filter_windowed_candidate_seed_page()
    assert subject._scalar_candidate_root_population_predicate() == ""


def _native_column_filter(key: str, value: str = "gpt-4o") -> dict:
    """A native any-span column leaf, as the grid emits it beside an attribute."""

    return {
        "column_id": key,
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _conjunction(*leaves, **kwargs):
    return TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=[_time_filter(END - timedelta(days=365), END), *leaves],
        page_size=25,
        **kwargs,
    )


@pytest.mark.parametrize(
    "native",
    [
        "model",
        "provider",
        "status",
        "span_name",
        "service_name",
        "span_id",
        "span_kind",
        "node_type",
        "observation_type",
    ],
)
@pytest.mark.parametrize(
    "kind,operation,value,cursor_seed",
    [
        ("text", "equals", "001234", 200),
        ("text", "contains", "long" * 600, 200),
        # A negative and a Boolean have no scalar candidate-witness predicate,
        # so the base builder mints no cursor seed batch for them. The widening
        # must not invent one: cursor seed batching is an acquisition decision
        # and reads the base gate, not the widened one.
        ("text", "not_equals", "absent", None),
        ("number", "greater_than", 0.01, 200),
        ("boolean", "equals", False, None),
    ],
)
def test_a_native_span_column_leaf_keeps_the_prefix_prune_and_the_batch(
    native, kind, operation, value, cursor_seed
):
    subject = _conjunction(
        _attribute_filter("attribute_0", value, filter_type=kind, operation=operation),
        _native_column_filter(native),
    )
    assert subject._uses_attribute_coordinate_replay()
    assert not subject._replays_only_typed_attribute_coordinates()
    assert subject.recommended_filter_classify_batch_size() == 200
    assert subject.recommended_filter_cursor_seed_batch_size() == cursor_seed
    assert subject.recommended_filter_cursor_seed_batch_size() == (
        TraceListQueryBuilder.recommended_filter_cursor_seed_batch_size(subject)
    )
    assert (
        subject.recommended_filter_candidate_witness_fallback_classify_batch_size()
        == 200
    )
    assert not subject.allow_filter_anchor_probe_for_initial_continuation()
    assert not subject.prefer_filter_candidate_witness_probe_first()
    coordinates = subject._filter_classifier_coordinate_predicate()
    assert "SELECT DISTINCT observation_type, service_name," in coordinates
    assert "trace_id IN %(candidate_trace_ids)s" in coordinates
    # The harvest carries no leaf of the request - not the attribute, and not
    # the native column - so it cannot drop a span either leaf would match.
    for forbidden in (
        "is_deleted",
        "parent_span_id",
        "attrs_",
        "span_attr",
        "latest_column_value",
        "latest_filter",
        "gpt-4o",
        "LIMIT",
        "FINAL",
        "_version",
    ):
        assert forbidden not in coordinates
    # Seed lane selection is NOT relaxed: every text/Boolean lane and every
    # bounded-witness hook below reads ``_uses_scalar_coordinate_replay``.
    assert not subject._uses_scalar_coordinate_replay()
    assert not subject._uses_short_text_candidate_seed()
    assert subject._public_long_text_candidate_seed_plan() is None
    assert subject._public_boolean_candidate_seed_plan() is None
    assert subject.filter_seed_width_policy() is None
    assert not subject.supports_filter_seed_density_probe()
    assert subject.filter_seed_witness_slack_hours() is None
    assert subject._scalar_candidate_root_population_predicate() == ""


def test_the_numeric_leaf_is_the_only_one_that_still_seeds_beside_a_native_column():
    subject = _conjunction(
        _attribute_filter(
            "attribute_0", 0.01, filter_type="number", operation="greater_than"
        ),
        _native_column_filter("model"),
    )
    assert TraceListQueryBuilder._public_scalar_candidate_seed_plan(subject) is not None
    assert subject.supports_filter_candidate_seed_page()
    # Unchanged, and the reason the relaxation cannot be argued as a seed win:
    # an optional seed is abandoned wherever speculative reads are unsupported.
    assert subject.filter_candidate_seed_is_optional()
    sql, _ = subject.build_filter_candidate_seed_page(
        slice_start=END - timedelta(days=2), slice_end=END, limit=50
    )
    cte = sql.split("SELECT trace_id, id AS root_span_id", 1)[0]
    assert "attrs_number" in cte
    assert "filter_witness_start_us" not in sql


@pytest.mark.parametrize(
    "residual",
    [
        {
            "column_id": "has_eval",
            "filter_config": {
                "filter_type": "boolean",
                "filter_op": "equals",
                "filter_value": True,
            },
        },
        {
            "column_id": "has_annotation",
            "filter_config": {
                "filter_type": "boolean",
                "filter_op": "equals",
                "filter_value": True,
            },
        },
        {
            "column_id": "tags",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "x",
            },
        },
    ],
)
def test_a_residual_leaf_still_demotes_the_whole_read(residual):
    subject = _conjunction(_attribute_filter("attribute_0", "001234"), residual)
    assert not subject._uses_attribute_coordinate_replay()
    assert not subject._uses_scalar_coordinate_replay()
    assert subject._filter_classifier_coordinate_predicate() == ""
    assert subject.recommended_filter_classify_batch_size() == 10


@pytest.mark.parametrize(
    "kwargs", [{"search": "query"}, {"sort_params": [("model", "asc")]}]
)
def test_modifiers_still_demote_a_native_column_conjunction(kwargs):
    subject = _conjunction(
        _attribute_filter("attribute_0", "001234"),
        _native_column_filter("model"),
        **kwargs,
    )
    assert not subject._uses_attribute_coordinate_replay()
    assert subject._filter_classifier_coordinate_predicate() == ""


def test_native_columns_without_any_attribute_leaf_keep_the_existing_path():
    subject = _conjunction(
        _native_column_filter("model"), _native_column_filter("status")
    )
    assert not subject._uses_attribute_coordinate_replay()
    assert not subject._uses_scalar_coordinate_replay()
    assert subject._filter_classifier_coordinate_predicate() == ""


@pytest.mark.parametrize(
    "aggregate,expected",
    [
        ("argMax(model, _peerdb_version) AS latest_column_value_0", True),
        ("argMax(tuple(model), _peerdb_version).1 AS latest_column_value_1", True),
        ("argMax(observation_type, _peerdb_version) AS latest_column_value_0", True),
        # Root-only columns are not any-span columns, and a spelling this
        # matcher does not know must fall back to the ordered walk.
        (
            "argMax(tuple(latency_ms), _peerdb_version).1 AS latest_column_value_0",
            False,
        ),
        (
            "argMax(tuple(trace_session_id), _peerdb_version).1 AS latest_column_value_0",
            False,
        ),
        ("argMax(model, _version) AS latest_column_value_0", False),
        ("argMax(lower(model), _peerdb_version) AS latest_column_value_0", False),
        ("argMax(span_attr_str[%(k)s], _peerdb_version) AS latest_attr_value_0", False),
    ],
)
def test_only_a_bare_any_span_column_aggregate_is_read_as_a_native_plan(
    aggregate, expected
):
    plan = LatestFilterPredicate(
        aggregates=(aggregate,),
        predicate="1 = 1",
        seed_predicate="1 = 1",
        params={},
        scope="any",
    )
    assert _replays_native_any_span_column(plan) is expected
    assert not _replays_typed_attributes(plan) or "span_attr" in aggregate


def test_two_aggregates_are_never_a_native_column_plan():
    plan = LatestFilterPredicate(
        aggregates=(
            "argMax(model, _peerdb_version) AS latest_column_value_0",
            "argMax(status, _peerdb_version) AS latest_column_value_1",
        ),
        predicate="1 = 1",
        seed_predicate="1 = 1",
        params={},
        scope="any",
    )
    assert not _replays_native_any_span_column(plan)


# --- The hook matrix, generated rather than written --------------------------
#
# Every routing hook the widened coordinate-replay regime can touch, for every
# named grid shape, at this head AND at the branch point. ``_BASE_HOOKS`` was
# produced by running ``_render_hooks`` against a ``git archive`` of the base
# commit 4954a80c4 in a detached tree under the same guard env, NOT by reading
# the diff: a claim about what the previous revision did has to come from that
# revision. ``_HEAD_HOOKS`` is pinned here, so the published matrix cannot
# drift from the code, and ``test_the_widening_never_moves_an_anchored_shape``
# turns the one invariant that matters into an assertion instead of a table.

_LONG_EXACT = "a" * 40
_LONG_BODY = "long" * 600


def _window(days=None, hours=None):
    span = timedelta(days=days) if days else timedelta(hours=hours)
    return _time_filter(END - span, END)


_MATRIX_SHAPES = {
    "short exact attr alone": lambda: [
        _window(days=365),
        _attribute_filter("attribute_0", "001234"),
    ],
    "long exact text attr alone": lambda: [
        _window(days=365),
        _attribute_filter("attribute_0", _LONG_EXACT),
    ],
    "long contains attr alone": lambda: [
        _window(days=365),
        _attribute_filter("attribute_0", _LONG_BODY, operation="contains"),
    ],
    "numeric attr alone": lambda: [
        _window(days=365),
        _attribute_filter(
            "attribute_0", 0.01, filter_type="number", operation="greater_than"
        ),
    ],
    "boolean attr alone": lambda: [
        _window(days=365),
        _attribute_filter("attribute_0", False, filter_type="boolean"),
    ],
    "short exact attr AND model": lambda: [
        _window(days=365),
        _attribute_filter("attribute_0", "001234"),
        _native_column_filter("model"),
    ],
    "long exact text attr AND model": lambda: [
        _window(days=365),
        _attribute_filter("attribute_0", _LONG_EXACT),
        _native_column_filter("model"),
    ],
    "long contains attr AND model": lambda: [
        _window(days=365),
        _attribute_filter("attribute_0", _LONG_BODY, operation="contains"),
        _native_column_filter("model"),
    ],
    "numeric attr AND model": lambda: [
        _window(days=365),
        _attribute_filter(
            "attribute_0", 0.01, filter_type="number", operation="greater_than"
        ),
        _native_column_filter("model"),
    ],
    "boolean attr AND model": lambda: [
        _window(days=365),
        _attribute_filter("attribute_0", False, filter_type="boolean"),
        _native_column_filter("model"),
    ],
    "status=error AND short exact attr (30d)": lambda: [
        _window(days=30),
        _attribute_filter("attribute_0", "001234"),
        _native_column_filter("status", "error"),
    ],
    "status=error AND short exact attr (1h)": lambda: [
        _window(hours=1),
        _attribute_filter("attribute_0", "001234"),
        _native_column_filter("status", "error"),
    ],
    "status=error AND long contains attr (30d)": lambda: [
        _window(days=30),
        _attribute_filter("attribute_0", _LONG_BODY, operation="contains"),
        _native_column_filter("status", "error"),
    ],
    "status=error AND numeric attr (30d)": lambda: [
        _window(days=30),
        _attribute_filter(
            "attribute_0", 0.01, filter_type="number", operation="greater_than"
        ),
        _native_column_filter("status", "error"),
    ],
    "long exact text attr AND status=error (30d)": lambda: [
        _window(days=30),
        _attribute_filter("attribute_0", _LONG_EXACT),
        _native_column_filter("status", "error"),
    ],
    "status=error alone (30d)": lambda: [
        _window(days=30),
        _native_column_filter("status", "error"),
    ],
    "model alone": lambda: [_window(days=365), _native_column_filter("model")],
    "model AND status=error (30d)": lambda: [
        _window(days=30),
        _native_column_filter("model"),
        _native_column_filter("status", "error"),
    ],
    "short exact attr AND has_eval": lambda: [
        _window(days=365),
        _attribute_filter("attribute_0", "001234"),
        {
            "column_id": "has_eval",
            "filter_config": {
                "filter_type": "boolean",
                "filter_op": "equals",
                "filter_value": True,
            },
        },
    ],
}

_MATRIX_HOOKS = (
    "attr_coord",
    "classify",
    "fallback_classify",
    "cursor_seed",
    "anchor_probe_initial",
    "witness_probe_first",
    "prune",
)

_BASE_HOOKS = {
    "short exact attr alone": {
        "attr_coord": True,
        "classify": 64,
        "fallback_classify": 200,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": True,
    },
    "long exact text attr alone": {
        "attr_coord": True,
        "classify": 200,
        "fallback_classify": 200,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": True,
    },
    "long contains attr alone": {
        "attr_coord": True,
        "classify": 200,
        "fallback_classify": 200,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": True,
    },
    "numeric attr alone": {
        "attr_coord": True,
        "classify": 200,
        "fallback_classify": 200,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": True,
    },
    "boolean attr alone": {
        "attr_coord": True,
        "classify": 200,
        "fallback_classify": 200,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": True,
    },
    "short exact attr AND model": {
        "attr_coord": False,
        "classify": 10,
        "fallback_classify": 10,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": True,
        "prune": False,
    },
    "long exact text attr AND model": {
        "attr_coord": False,
        "classify": 10,
        "fallback_classify": 10,
        "cursor_seed": 200,
        "anchor_probe_initial": True,
        "witness_probe_first": True,
        "prune": False,
    },
    "long contains attr AND model": {
        "attr_coord": False,
        "classify": 10,
        "fallback_classify": 10,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": True,
        "prune": False,
    },
    "numeric attr AND model": {
        "attr_coord": False,
        "classify": 10,
        "fallback_classify": 10,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": True,
        "prune": False,
    },
    "boolean attr AND model": {
        "attr_coord": False,
        "classify": 10,
        "fallback_classify": 10,
        "cursor_seed": None,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": False,
    },
    "status=error AND short exact attr (30d)": {
        "attr_coord": False,
        "classify": 10,
        "fallback_classify": 10,
        "cursor_seed": 200,
        "anchor_probe_initial": True,
        "witness_probe_first": True,
        "prune": False,
    },
    "status=error AND short exact attr (1h)": {
        "attr_coord": False,
        "classify": 10,
        "fallback_classify": 10,
        "cursor_seed": None,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": False,
    },
    "status=error AND long contains attr (30d)": {
        "attr_coord": False,
        "classify": 10,
        "fallback_classify": 10,
        "cursor_seed": 200,
        "anchor_probe_initial": True,
        "witness_probe_first": True,
        "prune": False,
    },
    "status=error AND numeric attr (30d)": {
        "attr_coord": False,
        "classify": 10,
        "fallback_classify": 10,
        "cursor_seed": 200,
        "anchor_probe_initial": True,
        "witness_probe_first": True,
        "prune": False,
    },
    "long exact text attr AND status=error (30d)": {
        "attr_coord": False,
        "classify": 10,
        "fallback_classify": 10,
        "cursor_seed": 200,
        "anchor_probe_initial": True,
        "witness_probe_first": True,
        "prune": False,
    },
    "status=error alone (30d)": {
        "attr_coord": False,
        "classify": 80,
        "fallback_classify": 80,
        "cursor_seed": None,
        "anchor_probe_initial": True,
        "witness_probe_first": False,
        "prune": False,
    },
    "model alone": {
        "attr_coord": False,
        "classify": 80,
        "fallback_classify": 80,
        "cursor_seed": None,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": False,
    },
    "model AND status=error (30d)": {
        "attr_coord": False,
        "classify": 80,
        "fallback_classify": 80,
        "cursor_seed": None,
        "anchor_probe_initial": True,
        "witness_probe_first": False,
        "prune": False,
    },
    "short exact attr AND has_eval": {
        "attr_coord": False,
        "classify": 10,
        "fallback_classify": 10,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": True,
        "prune": False,
    },
}

_HEAD_HOOKS = {
    **_BASE_HOOKS,
    # The widened regime, and the only rows that move: one typed-attribute
    # leaf beside a native any-span column, with no global anchor to lose.
    "short exact attr AND model": {
        "attr_coord": True,
        "classify": 200,
        "fallback_classify": 200,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": True,
    },
    "long contains attr AND model": {
        "attr_coord": True,
        "classify": 200,
        "fallback_classify": 200,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": True,
    },
    "numeric attr AND model": {
        "attr_coord": True,
        "classify": 200,
        "fallback_classify": 200,
        "cursor_seed": 200,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": True,
    },
    "boolean attr AND model": {
        "attr_coord": True,
        "classify": 200,
        "fallback_classify": 200,
        "cursor_seed": None,
        "anchor_probe_initial": False,
        "witness_probe_first": False,
        "prune": True,
    },
}


def _render_hooks(subject) -> dict:
    return {
        "attr_coord": subject._uses_attribute_coordinate_replay(),
        "classify": subject.recommended_filter_classify_batch_size(),
        "fallback_classify": (
            subject.recommended_filter_candidate_witness_fallback_classify_batch_size()
        ),
        "cursor_seed": subject.recommended_filter_cursor_seed_batch_size(),
        "anchor_probe_initial": (
            subject.allow_filter_anchor_probe_for_initial_continuation()
        ),
        "witness_probe_first": subject.prefer_filter_candidate_witness_probe_first(),
        "prune": bool(subject._filter_classifier_coordinate_predicate()),
    }


def render_hook_matrix() -> str:
    """The published before/after table, rendered from the code that decides it."""

    header = "| shape | " + " | ".join(_MATRIX_HOOKS) + " |"
    rule = "|---" * (len(_MATRIX_HOOKS) + 1) + "|"
    rows = [header, rule]
    for name, leaves in _MATRIX_SHAPES.items():
        head = _render_hooks(
            TraceListQueryBuilderV2(project_id=PROJECT, filters=leaves(), page_size=25)
        )
        cells = []
        for hook in _MATRIX_HOOKS:
            before, after = _BASE_HOOKS[name][hook], head[hook]
            cell = str(after) if before == after else f"**{before} -> {after}**"
            cells.append(cell)
        rows.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


@pytest.mark.parametrize("shape", sorted(_MATRIX_SHAPES))
def test_the_hook_matrix_is_pinned_for_every_named_shape(shape):
    subject = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=_MATRIX_SHAPES[shape](), page_size=25
    )
    assert _render_hooks(subject) == _HEAD_HOOKS[shape]


@pytest.mark.parametrize("shape", sorted(_MATRIX_SHAPES))
def test_the_widening_never_moves_an_anchored_shape(shape):
    """A global anchor is an indexed read the base builder chose deliberately.

    The widened regime turns ``allow_filter_anchor_probe_for_initial_
    continuation`` off, so a shape that carries one must stay on the base
    routing entirely - every hook, not just that one - until somebody measures
    the trade. Anchor PLAN existence is the gate, so the regime cannot change
    part-way through a request the way the window-and-probe-state predicates
    ``_uses_global_*_anchor`` would.
    """

    subject = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=_MATRIX_SHAPES[shape](), page_size=25
    )
    anchored = (
        subject._selective_error_status_anchor_plan() is not None
        or subject._selective_exact_text_anchor_plan() is not None
    )
    if not anchored:
        return
    assert subject._attribute_coordinate_replay_regime() in ("", "typed")
    assert _render_hooks(subject) == _BASE_HOOKS[shape]
