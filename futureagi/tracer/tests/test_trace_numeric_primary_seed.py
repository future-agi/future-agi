"""Numeric primary acquisition under the real uncapped application policy.

Only local chdb RMT fixtures execute SQL. No socket/ORM, candidate-ID oracle,
schema mutation outside chdb, or statement abort cap is involved.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.test import override_settings

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.application_read_policy import (
    UNLIMITED_STATEMENT_SETTINGS,
    application_read_settings,
)
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_trace_primary_prefix import trace_engine as trace_engine
from tracer.tests.test_trace_root_physical_replay import assert_coherent_classifier


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


END = START.replace(tzinfo=UTC) + timedelta(days=1)
ROOT_TIME = END - timedelta(hours=1, microseconds=123456)


def number_filter(operation="greater_than", value=1, key="agent.duration_s", **config):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "number",
            "filter_op": operation,
            "filter_value": value,
            **config,
        },
    }


def subject(*, days=7, leaves=None, **kwargs):
    scope = {} if "project_ids" in kwargs else {"project_id": PROJECT}
    return TraceListQueryBuilderV2(
        **scope,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [END - timedelta(days=days), END],
                },
            },
            *(leaves if leaves is not None else [number_filter()]),
        ],
        **kwargs,
    )


@pytest.mark.parametrize(
    "operation,value",
    [
        ("greater_than", 1),
        ("greater_than", 0.01),
        ("greater_than_or_equal", 1),
        ("less_than", -1),
        ("less_than_or_equal", -1),
        ("between", [1, 2]),
        ("equals", 2),
        ("in", [2, 3]),
    ],
)
@pytest.mark.parametrize("days", [7, 30, 365])
def test_only_compiler_proven_numeric_value_plan_is_primary(operation, value, days):
    builder = subject(days=days, leaves=[number_filter(operation, value)])
    assert builder.supports_filter_candidate_seed_page()
    assert builder.filter_candidate_seed_is_optional() is False
    assert (
        builder._public_scalar_candidate_seed_plan().raw_graph_value_witness_predicate
    )


@pytest.mark.parametrize(
    "operation,value",
    [
        ("equals", 0),
        ("in", [0, 2]),
        ("greater_than", -1),
        ("greater_than_or_equal", 0),
        ("less_than", 1),
        ("between", [-1, 2]),
        ("not_equals", 2),
        ("not_in", [2, 3]),
        ("not_between", [1, 2]),
        ("is_null", None),
        ("is_not_null", None),
    ],
)
def test_negative_or_default_ambiguous_numeric_route_is_unchanged(operation, value):
    builder = subject(leaves=[number_filter(operation, value)])
    assert builder._public_scalar_candidate_seed_plan() is None
    assert not builder.supports_filter_candidate_seed_page()
    assert (
        builder.filter_candidate_seed_is_optional()
        == TraceListQueryBuilder.filter_candidate_seed_is_optional(builder)
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"project_version_id": "33333333-3333-3333-3333-333333333333"},
        {"project_ids": [PROJECT, OTHER_PROJECT]},
        {"bounded_internal_scan": True},
        {"bounded_identity_only": True},
        {"bounded_identity_only": True, "bounded_bulk_scan": True},
        {
            "bounded_identity_only": True,
            "bounded_internal_scan": True,
            "bounded_bulk_scan": True,
            "bounded_population_proof": True,
        },
        {
            "bounded_identity_only": True,
            "bounded_internal_scan": True,
            "bounded_bulk_scan": True,
            "bounded_include_filter_witnesses": False,
            "bounded_global_span_witnesses": True,
        },
        {"bounded_sampling_salt": "test", "bounded_sampling_rate": 10},
        {"search": "query"},
        {"sort_params": [{"column_id": "cost", "sort_order": "asc"}]},
    ],
)
@pytest.mark.parametrize("mixed", [False, True])
def test_other_modes_retain_original_numeric_routing(kwargs, mixed):
    builder = subject(leaves=mixed_conjunction(5) if mixed else None, **kwargs)
    assert (
        builder.filter_candidate_seed_is_optional()
        == TraceListQueryBuilder.filter_candidate_seed_is_optional(builder)
    )


def test_membership_window_mode_retains_original_numeric_routing():
    builder = subject(bounded_membership_filters=subject().filters)
    assert (
        builder.filter_candidate_seed_is_optional()
        == TraceListQueryBuilder.filter_candidate_seed_is_optional(builder)
    )


@pytest.mark.parametrize(
    "extra",
    [
        {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "ok",
            },
        },
        {
            "column_id": "user_id",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "user",
            },
        },
        {
            "column_id": "has_eval",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "boolean",
                "filter_op": "equals",
                "filter_value": True,
            },
        },
        {
            "column_id": "payload",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "map",
                "filter_op": "contains",
                "filter_value": {"x": 1},
            },
        },
        number_filter(
            key="44444444-4444-4444-4444-444444444444", col_type="EVAL_METRIC"
        ),
        number_filter(
            key="44444444-4444-4444-4444-444444444444", col_type="ANNOTATION"
        ),
    ],
)
def test_root_relation_user_or_structured_leaves_do_not_gain_numeric_primary(extra):
    builder = subject(leaves=[number_filter(), extra])
    assert (
        builder.filter_candidate_seed_is_optional()
        == TraceListQueryBuilder.filter_candidate_seed_is_optional(builder)
    )


def test_long_text_primary_contract_and_short_window_remain_unchanged():
    text = number_filter(
        "equals", "resolved customer response " * 30, filter_type="text"
    )
    builder = subject(leaves=[text])
    assert builder._public_long_text_candidate_seed_plan() is not None
    assert builder.filter_candidate_seed_is_optional() is False
    short = subject(days=1 / 24)
    assert short._public_scalar_candidate_seed_plan() is None
    assert not short.supports_filter_candidate_seed_page()


class UncappedTransport:
    supports_per_query_read_settings = True
    supports_bounded_speculative_reads = False

    def __init__(self, run=None, *, fail_at=None):
        self.run, self.fail_at, self.calls = run, fail_at, []

    def execute_ch_query(self, query, params, **kwargs):
        settings = application_read_settings(kwargs.get("settings"))
        assert all(settings[name] == 0 for name in UNLIMITED_STATEMENT_SETTINGS)
        self.calls.append((query, params, settings))
        if len(self.calls) == self.fail_at:
            raise ReadDeadlineExceeded("simulated incomplete required read")
        rows = self.run(query, params) if self.run else []
        for row in rows:
            for key in ("start_time", "end_time", "_root_start_hour"):
                value = row.get(key)
                if isinstance(value, str):
                    value = datetime.fromisoformat(value)
                if isinstance(value, datetime):
                    row[key] = (
                        value.replace(tzinfo=UTC)
                        if value.tzinfo is None
                        else value.astimezone(UTC)
                    )
        return QueryResult(rows, len(rows), "clickhouse", 1.0)


def page(builder, transport, **kwargs):
    return read_bounded_filter_page(
        builder=builder,
        analytics=transport,
        filters=builder.filters,
        key_field="trace_id",
        page_number=0,
        page_size=builder.page_size,
        deadline_ms=20_000,
        max_query_count=64,
        bounded_continuation=True,
        include_incomplete_rows=True,
        root_time_discovery=True,
        **kwargs,
    )


@pytest.mark.parametrize("width", [1, 2, 5, 10])
def test_uncapped_selector_reaches_numeric_seed_without_five_minute_fallback(width):
    builder = subject(leaves=mixed_conjunction(width))
    transport = UncappedTransport()
    result = page(builder, transport)
    assert result.complete and not result.rows and not result.has_more
    assert len(transport.calls) == 1
    query, params, _ = transport.calls[0]
    child, root = query.split("SELECT trace_id, id AS root_span_id, start_time", 1)
    assert "matching_scalar_trace_identities" in child and "attrs_number" in child
    assert "indexHint(has(mapKeys(" in child
    for forbidden in (
        "start_time",
        "LIMIT",
        "is_deleted",
        "project_version_id",
        "FINAL",
    ):
        assert forbidden not in child
    assert "ORDER BY start_time DESC, trace_id DESC" in root
    # BaseQueryBuilder normalizes query bounds to UTC-naive datetimes.
    assert params["filter_slice_start"] == (END - timedelta(days=7)).replace(
        tzinfo=None
    )
    assert params["filter_slice_end"] == END.replace(tzinfo=None)


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("width", [2, 10])
def test_failed_primary_seed_never_advances_checkpoint_or_falls_back(width, mixed):
    builder = subject(
        leaves=mixed_conjunction(width) if mixed else conjunction(width, "missing")
    )
    cursor = {
        "continuation_slice_start": END - timedelta(days=7),
        "continuation_slice_end": END,
        "continuation_before_start_time": ROOT_TIME,
        "continuation_before_id": "tie-previous",
    }
    failed = UncappedTransport(fail_at=1)
    result = page(builder, failed, **cursor)
    assert not result.complete and result.error_code == "read_budget_exceeded"
    assert not result.rows and not result.has_more
    assert len(failed.calls) == 1
    # No proven progress means no replacement cursor. The caller retries its
    # original signed checkpoint, not a fabricated exhausted window.
    assert all(getattr(result, key) is None for key in cursor)
    assert failed.calls[0][1]["filter_before_id"] == cursor["continuation_before_id"]
    retry = UncappedTransport()
    resumed = page(builder, retry, **cursor)
    assert resumed.complete and not resumed.rows
    assert failed.calls[0][:2] == retry.calls[0][:2]


def insert_root(insert, trace="target", **kwargs):
    insert(
        **{
            "trace_id": trace,
            "id": "root",
            "parent_span_id": "",
            "start_time": ROOT_TIME,
            "attrs_number": {},
            "_version": 1,
            **kwargs,
        }
    )


def insert_child(insert, trace="target", **kwargs):
    insert(
        **{
            "trace_id": trace,
            "id": "child",
            "parent_span_id": "root",
            "start_time": ROOT_TIME - timedelta(days=400),
            "attrs_number": {"agent.duration_s": 2},
            "_version": 1,
            **kwargs,
        }
    )


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("after", [False, True])
def test_rmt_child_outside_root_window_is_still_a_witness(request, days, after):
    run, insert = request.getfixturevalue("trace_engine")
    assert run("SELECT version() AS version")[0]["version"].startswith("25.")
    insert_root(insert)
    insert_child(
        insert,
        start_time=END + timedelta(days=2)
        if after
        else ROOT_TIME - timedelta(days=400),
    )
    transport = UncappedTransport(run)
    result = page(subject(days=days), transport)
    assert result.complete, result.error_code
    assert [row["trace_id"] for row in result.rows] == ["target"]
    assert (
        len(transport.calls) == 3
    )  # Required seed, latest classifier, exact root hydration.
    assert_coherent_classifier(transport.calls[1][0])
    row = result.rows[0]
    assert row["_root_version"] == 1 and row["_root_service_name"] == "service-a"
    assert row["_root_observation_type"] == "span" and row["start_time"] == ROOT_TIME


@pytest.mark.parametrize(
    "latest,expected",
    [
        ({"attrs_number": {}}, False),
        ({"attrs_number": {"agent.duration_s": 0}}, False),
        ({"attrs_number": {}, "attrs_string": {"agent.duration_s": "2"}}, False),
        ({"is_deleted": 1}, False),
        ({"is_deleted": 0, "attrs_number": {"agent.duration_s": 3}}, True),
    ],
)
def test_rmt_full_six_key_latest_correction_removal_and_deletion(
    request, latest, expected
):
    run, insert = request.getfixturevalue("trace_engine")
    insert_root(insert)
    child_time = ROOT_TIME - timedelta(days=400)
    insert_child(insert, start_time=child_time)
    # Timestamp changes within the physical hour must not preserve the old value.
    insert_child(
        insert,
        **{"start_time": child_time - timedelta(minutes=10), "_version": 2, **latest},
    )
    result = page(subject(), UncappedTransport(run))
    assert result.complete, result.error_code
    assert [row["trace_id"] for row in result.rows] == (["target"] if expected else [])


@pytest.mark.parametrize(
    "collision",
    [
        {"service_name": "other-service"},
        {"observation_type": "other-type"},
        {"start_time": ROOT_TIME - timedelta(days=400, hours=1)},
        {"project_id": OTHER_PROJECT},
        {"trace_id": "foreign-trace"},
    ],
)
def test_rmt_other_physical_key_tombstone_cannot_delete_matching_child(
    request, collision
):
    run, insert = request.getfixturevalue("trace_engine")
    insert_root(insert)
    insert_child(insert)
    insert_child(insert, **{"_version": 9, "is_deleted": 1, **collision})
    result = page(subject(), UncappedTransport(run))
    assert result.complete and [row["trace_id"] for row in result.rows] == ["target"]


def test_rmt_empty_population_finishes_in_one_seed_not_many_root_classifiers(request):
    run, insert = request.getfixturevalue("trace_engine")
    for index in range(40):
        insert_root(insert, trace=f"unmatched-{index:03}")
    transport = UncappedTransport(run)
    result = page(subject(), transport)
    assert result.complete and not result.rows and not result.has_more
    assert len(transport.calls) == 1
    assert "matching_scalar_trace_identities" in transport.calls[0][0]


@pytest.mark.parametrize("fail_at", [2, 3])
@pytest.mark.parametrize("width", [1, 10])
def test_rmt_failed_classifier_or_hydration_cannot_commit_candidate(
    request, fail_at, width
):
    run, insert = request.getfixturevalue("trace_engine")
    insert_root(insert)
    insert_child(insert)
    builder = subject(leaves=conjunction(width, "missing"))
    result = page(builder, UncappedTransport(run, fail_at=fail_at))
    assert not result.complete and not result.rows and not result.has_more
    assert result.error_code == "read_budget_exceeded"
    assert (
        result.continuation_before_id is None and result.continuation_slice_end is None
    )
    retry = page(builder, UncappedTransport(run))
    assert retry.complete and [row["trace_id"] for row in retry.rows] == ["target"]


@pytest.mark.parametrize("width", [1, 10])
def test_rmt_corrected_root_order_and_same_time_pagination_have_no_skips(
    request, width
):
    run, insert = request.getfixturevalue("trace_engine")
    for trace in ("tie-a", "tie-b", "tie-c", "corrected"):
        insert_root(insert, trace=trace)
        insert_child(insert, trace=trace)
    corrected = ROOT_TIME - timedelta(minutes=10)
    insert_root(insert, trace="corrected", start_time=corrected, _version=2)
    # A stale candidate whose latest root is a tombstone must not be published.
    insert_root(insert, trace="deleted")
    insert_child(insert, trace="deleted")
    insert_root(insert, trace="deleted", start_time=corrected, _version=2, is_deleted=1)
    transport, cursor, rows = UncappedTransport(run), {}, []
    for _ in range(4):
        result = page(
            subject(page_size=2, leaves=conjunction(width, "missing")),
            transport,
            **cursor,
        )
        assert result.complete, result.error_code
        rows.extend(result.rows)
        if not result.has_more:
            break
        cursor = {
            "cursor_start_time": result.rows[-1]["start_time"],
            "cursor_order_token": result.rows[-1]["trace_id"],
        }
    assert [row["trace_id"] for row in rows] == ["tie-c", "tie-b", "tie-a", "corrected"]
    assert rows[-1]["start_time"] == corrected and rows[-1]["_root_version"] == 2
    assert rows[-1]["_root_start_hour"] == corrected.replace(
        minute=0, second=0, microsecond=0
    )


def conjunction(width, family):
    # Anchor last: negatives/absence/default comparisons must not disrupt
    # compiler plan indexing or turn the seed into a same-child conjunction.
    operations = {
        "positive": [("between", [1, 3])],
        "negative": [
            ("not_equals", 99),
            ("not_in", [99, 100]),
            ("not_between", [90, 100]),
        ],
        "missing": [("is_null", None)],
        "zero": [("equals", 0)],
    }[family]
    return [
        number_filter(*operations[i % len(operations)], key=f"numeric_{i}")
        for i in range(width - 1)
    ] + [number_filter()]


@pytest.mark.parametrize("width", [2, 5, 10])
@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("family", ["positive", "negative", "missing", "zero"])
def test_rmt_numeric_and_uses_one_witness_and_all_latest_leaf_predicates(
    request, width, days, family
):
    run, insert = request.getfixturevalue("trace_engine")
    leaves = conjunction(width, family)
    builder = subject(days=days, leaves=leaves)
    assert builder.supports_filter_candidate_seed_page()
    assert builder.filter_candidate_seed_is_optional() is False
    for trace, bad in [("z-pass", False), ("y-fail", True)]:
        insert_root(insert, trace=trace)
        insert_child(insert, trace=trace)  # Positive anchor, outside every root window.
        for index, item in enumerate(leaves[:-1]):
            key = item["column_id"]
            old = 99 if family == "negative" else 2
            insert_child(
                insert, trace=trace, id=f"leaf-{index}", attrs_number={key: old}
            )
            fails_leaf = bad and index == 0
            if family == "missing":
                attrs = {key: 0} if fails_leaf else {}
            elif family == "zero":
                attrs = {} if fails_leaf else {key: 0}
            else:
                attrs = {key: (99 if family == "negative" else 0) if fails_leaf else 2}
            insert_child(
                insert,
                trace=trace,
                id=f"leaf-{index}",
                _version=2,
                start_time=ROOT_TIME - timedelta(days=400, minutes=10),
                attrs_number=attrs,
                # Cleared numeric keys can coexist with wrong-type keys;
                # alternate missing witnesses are deleted latest rows.
                attrs_string={key: "2"}
                if family == "missing" and not fails_leaf
                else {},
                is_deleted=int(
                    family == "missing" and not fails_leaf and index % 2 == 0
                ),
            )
    corrected = ROOT_TIME - timedelta(minutes=10)
    insert_root(insert, trace="z-pass", _version=2, start_time=corrected)
    transport = UncappedTransport(run)
    result = page(builder, transport)
    assert result.complete, result.error_code
    assert [row["trace_id"] for row in result.rows] == ["z-pass"]
    assert (
        result.rows[0]["start_time"] == corrected
        and result.rows[0]["_root_version"] == 2
    )
    assert len(transport.calls) == 3
    seed_sql, seed_params, _ = transport.calls[0]
    child = seed_sql.split("SELECT trace_id, id AS root_span_id", 1)[0]
    selected = builder._public_scalar_candidate_seed_plan()
    selected_keys = [
        name for name in selected.params if name.startswith("latest_filter_key_")
    ]
    assert len(selected_keys) == 1
    assert seed_params[selected_keys[0]] in {leaf["column_id"] for leaf in leaves}
    assert [
        name for name in seed_params if name.startswith("latest_filter_key_")
    ] == selected_keys
    for forbidden in ("start_time", "LIMIT", "is_deleted", "FINAL"):
        assert forbidden not in child
    classifier = transport.calls[1][0]
    assert_coherent_classifier(classifier)
    plans, residual = builder._partition_trace_filter_plans(builder._bounded_filters())
    assert not residual and len(plans) == width
    assert all(plan.grouped_match_predicate() in classifier for plan in plans)


@pytest.mark.parametrize("width", [2, 5, 10])
@pytest.mark.parametrize(
    "operation,value", [("not_equals", 99), ("is_null", None), ("equals", 0)]
)
def test_numeric_conjunction_without_safe_positive_anchor_keeps_fallback(
    width, operation, value
):
    builder = subject(
        leaves=[
            number_filter(operation, value, key=f"numeric_{i}") for i in range(width)
        ]
    )
    assert builder._public_scalar_candidate_seed_plan() is None
    assert not builder.supports_filter_candidate_seed_page()


@pytest.mark.parametrize("mixed", [False, True])
def test_more_than_ten_scalar_leaves_fall_back_without_failing(mixed):
    leaves = (
        [*mixed_conjunction(10), number_filter(key="eleventh")]
        if mixed
        else conjunction(11, "positive")
    )
    builder = subject(leaves=leaves)
    assert builder.supports_filter_candidate_seed_page()
    assert builder.filter_candidate_seed_is_optional() is True
    transport = UncappedTransport()
    result = page(builder, transport)
    assert result.complete and not result.rows and result.error_code is None
    assert transport.calls and all(
        "matching_scalar_trace_identities" not in sql for sql, _, _ in transport.calls
    )


@pytest.mark.parametrize(
    "other",
    [
        number_filter("equals", "text", filter_type="text"),
        number_filter("equals", True, filter_type="boolean"),
        number_filter(
            "in",
            [2, "text"],
            filter_type="text",
            attribute_value_types=["number", "string"],
        ),
    ],
)
def test_mixed_type_companion_uses_numeric_primary_and_exact_classifier(other):
    builder = subject(leaves=[number_filter(key="anchor"), other])
    assert builder.filter_candidate_seed_is_optional() is False


@pytest.mark.parametrize("operation", ["in", "not_in"])
def test_numeric_only_picker_companion_uses_existing_typed_provenance(operation):
    builder = subject(
        leaves=[
            number_filter(
                operation,
                [2, 3],
                filter_type="text",
                attribute_value_types=["number", "number"],
            ),
            number_filter(key="anchor"),
        ]
    )
    assert builder.supports_filter_candidate_seed_page()
    assert builder.filter_candidate_seed_is_optional() is False


def test_rmt_opposing_ranges_on_same_key_can_match_different_children(request):
    run, insert = request.getfixturevalue("trace_engine")
    insert_root(insert)
    insert_child(insert, id="high", attrs_number={"metric": 2})
    insert_child(insert, id="low", attrs_number={"metric": -2})
    builder = subject(
        leaves=[
            number_filter("greater_than", 1, key="metric"),
            number_filter("less_than", -1, key="metric"),
        ]
    )
    result = page(builder, UncappedTransport(run))
    assert result.complete and [row["trace_id"] for row in result.rows] == ["target"]
    insert_child(
        insert, id="low", _version=2, is_deleted=1, attrs_number={"metric": -2}
    )
    result = page(builder, UncappedTransport(run))
    assert result.complete and not result.rows


def mixed_leaf_cases():
    # Same-named native fields remain explicit raw attributes. Each tuple is
    # (public filter, stale typed maps, independently expected matching maps).
    return [
        (
            number_filter("equals", "ready", key="user_id", filter_type="text"),
            {"attrs_string": {"user_id": "blocked"}},
            {"attrs_string": {"user_id": "READY"}},
        ),
        (
            number_filter("equals", False, key="has_eval", filter_type="boolean"),
            {"attrs_bool": {"has_eval": 1}},
            {"attrs_bool": {"has_eval": 0}},
        ),
        (
            number_filter("equals", 0, key="zero"),
            {"attrs_number": {"zero": 9}},
            {"attrs_number": {"zero": 0}},
        ),
        (
            number_filter("not_in", ["blocked"], key="created_at", filter_type="text"),
            {"attrs_string": {"created_at": "blocked"}},
            {"attrs_string": {"created_at": "ready"}},
        ),
        (
            number_filter("not_equals", True, key="flag", filter_type="boolean"),
            {"attrs_bool": {"flag": 1}},
            {"attrs_bool": {"flag": 0}},
        ),
        (
            number_filter("not_between", [90, 100], key="range"),
            {"attrs_number": {"range": 99}},
            {"attrs_number": {"range": 2}},
        ),
        (
            number_filter("is_null", None, key="status", filter_type="text"),
            {"attrs_string": {"status": "present"}},
            {"attrs_number": {"status": 0}},
        ),
        (
            number_filter("is_null", None, key="has_annotation", filter_type="boolean"),
            {"attrs_bool": {"has_annotation": 0}},
            {"attrs_string": {"has_annotation": "false"}},
        ),
        (
            number_filter(
                "not_in",
                [9, "blocked", True],
                key="picker",
                filter_type="text",
                attribute_value_types=["number", "string", "boolean"],
            ),
            {"attrs_number": {"picker": 9}},
            {"attrs_number": {"picker": 0}},
        ),
    ]


def mixed_conjunction(width):
    return [case[0] for case in mixed_leaf_cases()[: width - 1]] + [number_filter()]


def insert_mixed_children(insert, trace, width, *, bad_leaf=None):
    insert_child(insert, trace=trace)
    for index, (_, stale, latest) in enumerate(mixed_leaf_cases()[: width - 1]):
        timestamp = (
            ROOT_TIME - timedelta(days=400)
            if index % 2
            else END + timedelta(days=2, minutes=40)
        )
        corrected = timestamp - timedelta(minutes=10)
        assert timestamp.replace(
            minute=0, second=0, microsecond=0
        ) == corrected.replace(minute=0, second=0, microsecond=0)
        identity = {"id": f"leaf-{index}", "start_time": timestamp}
        empty = {"attrs_number": {}, "attrs_string": {}, "attrs_bool": {}}
        insert_child(insert, trace=trace, **identity, **(empty | stale))
        insert_child(
            insert,
            trace=trace,
            **(identity | {"start_time": corrected}),
            _version=2,
            **(empty | (stale if index == bad_leaf else latest)),
        )


@pytest.fixture
def unicode_trace_engine(request):
    run, insert = request.getfixturevalue("trace_engine")
    if not run("SELECT name FROM system.functions WHERE name = 'lowerUTF8'"):
        pytest.skip("CH25 reduced engine lacks lowerUTF8; no ASCII substitution")
    return run, insert


@pytest.mark.parametrize("width", [2, 5, 10])
@pytest.mark.parametrize("days", [7, 30, 365])
def test_rmt_mixed_and_distinct_latest_children_and_root_order(request, width, days):
    run, insert = request.getfixturevalue("unicode_trace_engine")
    for trace in ("tie-a", "tie-b", "tie-c", "corrected", "deleted", "bad-child"):
        insert_root(insert, trace=trace)
        insert_mixed_children(
            insert, trace, width, bad_leaf=0 if trace == "bad-child" else None
        )
    corrected = ROOT_TIME - timedelta(minutes=10)
    insert_root(insert, trace="corrected", _version=2, start_time=corrected)
    insert_root(insert, trace="deleted", _version=2, is_deleted=1)
    builder = subject(days=days, leaves=mixed_conjunction(width), page_size=2)
    assert builder.filter_candidate_seed_is_optional() is False
    transport, cursor, rows = UncappedTransport(run), {}, []
    for _ in range(4):
        result = page(builder, transport, **cursor)
        assert result.complete, result.error_code
        rows.extend(result.rows)
        if not result.has_more:
            break
        cursor = {
            "cursor_start_time": result.rows[-1]["start_time"],
            "cursor_order_token": result.rows[-1]["trace_id"],
        }
    assert not result.has_more
    assert [row["trace_id"] for row in rows] == ["tie-c", "tie-b", "tie-a", "corrected"]
    assert rows[-1]["start_time"] == corrected and rows[-1]["_root_version"] == 2
    seed, params, _ = transport.calls[0]
    raw = seed.split("SELECT trace_id, id AS root_span_id", 1)[0]
    assert "matching_scalar_trace_identities" in raw
    for forbidden in (
        "start_time",
        "LIMIT",
        "FINAL",
        "is_deleted",
        "attrs_string",
        "attrs_bool",
    ):
        assert forbidden not in raw
    assert [v for k, v in params.items() if k.startswith("latest_filter_key_")] == [
        "agent.duration_s"
    ]
    plans, residual = builder._partition_trace_filter_plans(builder._bounded_filters())
    assert not residual and len(plans) == width
    classifier = transport.calls[1][0]
    assert_coherent_classifier(classifier)
    assert all(plan.grouped_match_predicate() in classifier for plan in plans)


@pytest.mark.parametrize("bad_leaf", range(9))
def test_rmt_each_mixed_sibling_remains_authoritative(request, bad_leaf):
    run, insert = request.getfixturevalue("unicode_trace_engine")
    insert_root(insert)
    insert_mixed_children(insert, "target", 10, bad_leaf=bad_leaf)
    result = page(subject(leaves=mixed_conjunction(10)), UncappedTransport(run))
    assert result.complete and not result.rows and not result.has_more


@pytest.mark.parametrize("operation", ["in", "not_in"])
@pytest.mark.parametrize(
    "maps,positive,negative",
    [
        ({"attrs_string": {"picker": "ready"}}, True, False),
        ({"attrs_number": {"picker": 2}}, True, False),
        ({"attrs_bool": {"picker": 1}}, True, False),
        ({"attrs_number": {"picker": 0}}, False, True),
        ({"attrs_bool": {"picker": 0}}, False, True),
        ({"attrs_string": {"picker": "other"}}, False, True),
        ({}, False, False),
        ({"attributes_extra": '{"picker": 2}'}, False, False),
        (
            {"attrs_number": {"picker": 0}, "attrs_string": {"picker": "ready"}},
            True,
            False,
        ),
    ],
)
def test_rmt_mixed_picker_union_presence_and_negation(
    request, operation, maps, positive, negative
):
    run, insert = request.getfixturevalue("unicode_trace_engine")
    insert_root(insert)
    insert_child(insert)
    insert_child(insert, id="picker", **({"attrs_number": {}} | maps))
    picker = number_filter(
        operation,
        ["ready", 2, True],
        key="picker",
        filter_type="text",
        attribute_value_types=["string", "number", "boolean"],
    )
    builder = subject(leaves=[picker, number_filter()])
    assert builder.filter_candidate_seed_is_optional() is False
    result = page(builder, UncappedTransport(run))
    assert result.complete, result.error_code
    assert [row["trace_id"] for row in result.rows] == (
        ["target"] if (positive if operation == "in" else negative) else []
    )


@pytest.mark.parametrize(
    "collision",
    [
        {"service_name": "other-service"},
        {"observation_type": "other-type"},
        {"start_time": ROOT_TIME - timedelta(days=400, hours=1)},
        {"project_id": OTHER_PROJECT},
        {"trace_id": "foreign-trace"},
    ],
)
def test_rmt_mixed_sibling_physical_collision_and_own_tombstone(request, collision):
    run, insert = request.getfixturevalue("trace_engine")
    insert_root(insert)
    insert_child(insert)
    sibling = {"id": "flag", "attrs_number": {}, "attrs_bool": {"flag": 0}}
    insert_child(insert, **sibling)
    insert_child(insert, **(sibling | collision), _version=9, is_deleted=1)
    builder = subject(
        leaves=[
            number_filter(),
            number_filter("not_equals", True, key="flag", filter_type="boolean"),
        ]
    )
    result = page(builder, UncappedTransport(run))
    assert result.complete and [row["trace_id"] for row in result.rows] == ["target"]
    insert_child(insert, **sibling, _version=10, is_deleted=1)
    result = page(builder, UncappedTransport(run))
    assert result.complete and not result.rows


@pytest.mark.parametrize(
    "anchor", [number_filter("equals", 0), number_filter("not_in", [2])]
)
def test_mixed_scalar_siblings_cannot_create_numeric_anchor(anchor):
    builder = subject(
        leaves=[mixed_leaf_cases()[0][0], mixed_leaf_cases()[1][0], anchor]
    )
    assert builder._public_scalar_candidate_seed_plan() is None
    assert not builder.supports_filter_candidate_seed_page()


@pytest.mark.parametrize("width", [2, 5, 10])
@pytest.mark.parametrize("days", [7, 30, 365])
def test_rmt_number_boolean_siblings_clear_wrong_type_and_latest_tombstone(
    request, width, days
):
    run, insert = request.getfixturevalue("trace_engine")
    insert_root(insert)
    insert_child(insert)
    leaves = []
    for index in range(width - 1):
        key = f"bool-{index}"
        missing = index % 2 == 1
        leaves.append(
            number_filter(
                "is_null" if missing else "not_equals",
                None if missing else True,
                key=key,
                filter_type="boolean",
            )
        )
        insert_child(insert, id=key, attrs_number={}, attrs_bool={key: 1})
        insert_child(
            insert,
            id=key,
            _version=2,
            start_time=ROOT_TIME - timedelta(days=400, minutes=10),
            attrs_number={key: 0} if missing else {},
            attrs_bool={} if missing else {key: 0},
            is_deleted=int(missing),
        )
    builder = subject(days=days, leaves=[*leaves, number_filter()])
    assert builder.filter_candidate_seed_is_optional() is False
    result = page(builder, UncappedTransport(run))
    assert result.complete and [row["trace_id"] for row in result.rows] == ["target"]
    # A deleted false witness cannot be supplied by numeric zero or by a
    # missing-map default, even though the necessary numeric anchor survives.
    insert_child(
        insert,
        id="bool-0",
        _version=3,
        attrs_number={},
        attrs_bool={"bool-0": 0},
        is_deleted=1,
    )
    result = page(builder, UncappedTransport(run))
    assert result.complete and not result.rows


def test_rmt_unicode_text_sibling_uses_unmodified_compiler(unicode_trace_engine):
    run, insert = unicode_trace_engine
    insert_root(insert)
    insert_child(insert)
    insert_child(insert, id="text", attrs_number={}, attrs_string={"text": "ÄPFEL"})
    builder = subject(
        leaves=[
            number_filter(),
            number_filter("equals", "äpfel", key="text", filter_type="text"),
        ]
    )
    result = page(builder, UncappedTransport(run))
    assert result.complete and [row["trace_id"] for row in result.rows] == ["target"]


@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_native_relation_seed_keeps_priority_over_numeric_anchor():
    builder = subject(
        leaves=[
            number_filter(),
            number_filter(
                "equals",
                True,
                key="has_eval",
                col_type="SYSTEM_METRIC",
                filter_type="boolean",
            ),
        ],
        eval_config_ids=["44444444-4444-4444-4444-444444444444"],
    )
    assert builder._positive_relational_seed_filter() is not None
    assert builder._public_scalar_candidate_seed_plan() is not None
    assert builder.filter_candidate_seed_is_optional() is False
    start, end = builder._bounded_request_window
    sql, _ = builder.build_filter_candidate_seed_page(
        slice_start=start, slice_end=end, limit=26
    )
    assert "tracer_eval_logger_v2" in sql
    assert "matching_scalar_trace_identities" not in sql
