from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_bounded_trace_filter_reads import (
    END,
    PROJECT_ID,
    _attribute_filter,
    _CandidateWitnessHydrationFakeBuilder,
    _CandidateWitnessHydrationFakeExecutor,
    _FakeBuilder,
    _FakeExecutor,
    _render_driver_sql,
    _time_filter,
)
from tracer.tests.test_trace_root_physical_replay import (
    assert_coherent_classifier,
    complete_root_row,
)

pytestmark = pytest.mark.unit


class _WitnessFallbackV2(TraceListQueryBuilderV2):
    """Exercise the retained optional-witness fallback independently of the
    new indexed-coordinate lane, whose routing has its own regression suite.
    """

    def _uses_attribute_coordinate_replay(self):
        return False


def _reported_filter(kind="numeric"):
    if kind == "numeric":
        return _attribute_filter(
            "agent.duration_s", 1, filter_type="number", operation="greater_than"
        )
    item = _attribute_filter("company_id", ["10000001"], operation="in")
    item["filter_config"]["attribute_value_types"] = ["string"]
    return item


def _builder(item=None, builder_cls=_WitnessFallbackV2, **kwargs):
    return builder_cls(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(END - timedelta(days=7), END),
            item or _reported_filter(),
        ],
        page_size=25,
        **kwargs,
    )


@pytest.mark.parametrize(
    "builder_cls", [TraceListQueryBuilder, TraceListQueryBuilderV2]
)
@pytest.mark.parametrize("kind", ["numeric", "company"])
def test_reported_scalar_witness_is_finite_indexed_and_all_child_time(
    builder_cls, kind
):
    builder = _builder(_reported_filter(kind), builder_cls)
    sql, params = builder.build_filter_candidate_witness_probe(
        [{"trace_id": "first"}, {"trace_id": "second"}, {"trace_id": "first"}],
        slice_start=END - timedelta(hours=1),
        slice_end=END,
    )
    rendered = _render_driver_sql(sql, params)
    indexed = builder_cls is TraceListQueryBuilderV2
    assert builder.prefer_filter_candidate_witness_probe_first() is not indexed
    assert builder.recommended_filter_cursor_seed_batch_size() == 200
    assert builder.recommended_filter_classify_batch_size() == (200 if indexed else 10)
    assert builder.filter_candidate_witness_replays_global_membership() is True
    assert params["filter_candidate_trace_ids"] == ("first", "second")
    assert params["filter_candidate_witness_limit"] == 2
    assert "project_id = %(project_id)s" in sql
    assert "trace_id IN %(filter_candidate_trace_ids)s" in sql
    assert "GROUP BY trace_id" in sql
    assert "indexHint(has(mapKeys(" in sql
    assert "is_deleted" not in sql  # Retain raw tombstones for exact replay.
    assert "argMax" not in sql
    assert "start_time" not in sql  # A matching child can predate or outlive its root.
    assert "FINAL" not in sql
    if kind == "numeric":
        assert "] > 1.0" in rendered
    else:
        assert "lowerUTF8" in sql
        assert "IN ('10000001')" in rendered
    classifier, _ = builder.build_filter_identity_match_query_from_seed_rows(
        [{"trace_id": "first"}]
    )
    if indexed:
        assert_coherent_classifier(classifier)
    else:
        assert "argMax(is_deleted," in classifier
    assert "WHERE latest_is_deleted = 0" in classifier
    assert "latest_attr_exists_0" in classifier
    assert "latest_attr_value_0" in classifier
    assert "FROM spans" in classifier
    assert "FINAL" not in classifier


@pytest.mark.parametrize(
    "operation,value",
    [
        ("greater_than", 1),
        ("greater_than_or_equal", 1),
        ("less_than", -1),
        ("less_than_or_equal", -1),
        ("between", [1, 2]),
    ],
)
def test_numeric_range_witness_uses_only_default_safe_comparisons(operation, value):
    builder = _builder(
        _attribute_filter("duration", value, filter_type="number", operation=operation)
    )
    sql, params = builder.build_filter_candidate_witness_probe([{"trace_id": "a"}])
    assert builder.prefer_filter_candidate_witness_probe_first() is True
    assert "attrs_number[%(latest_filter_key_0)s]" in sql
    assert "argMax" not in sql
    _render_driver_sql(sql, params)


@pytest.mark.parametrize(
    "operation,value",
    [
        ("not_equals", 1),
        ("not_in", [1]),
        ("is_null", None),
        ("equals", 0),
        ("greater_than", -1),
        ("less_than", 1),
        ("between", [-1, 1]),
    ],
)
def test_negative_and_missing_default_shapes_never_use_raw_pruning(operation, value):
    builder = _builder(
        _attribute_filter("duration", value, filter_type="number", operation=operation)
    )
    sql, _ = builder.build_filter_candidate_witness_probe([{"trace_id": "a"}])
    assert builder.prefer_filter_candidate_witness_probe_first() is False
    assert builder.recommended_filter_cursor_seed_batch_size() is None
    assert builder.supports_filter_candidate_seed_page() is False
    assert not sql or "argMax" in sql


def test_scalar_candidate_seed_bounds_roots_to_12m_but_keeps_all_child_history():
    start, end = datetime(2025, 9, 4), datetime(2026, 9, 4)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(start, end), _reported_filter()],
    )
    assert builder.supports_filter_candidate_seed_page() is True
    assert builder.supports_filter_anchor_probe() is False
    assert builder.filter_candidate_seed_proves_result_order() is True
    assert builder.recommended_filter_initial_slice_width() == end - start
    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=start,
        slice_end=end,
        limit=200,
        before_start_time=datetime(2026, 8, 1),
        before_id="previous-root",
    )
    child, root = sql.split("SELECT trace_id, id AS root_span_id, start_time", 1)
    assert "matching_scalar_trace_identities" in child
    assert "indexHint(has(mapKeys(" in child
    assert "project_id = %(project_id)s" in child
    assert "start_time" not in child
    assert "is_deleted" not in child
    assert "LIMIT" not in child  # A truncated membership set cannot prove absence.
    assert "trace_id IN" in root
    assert "FROM matching_scalar_trace_identities" in root
    assert "%(filter_slice_start_us)s" in root
    assert "%(filter_slice_end_us)s" in root
    assert "%(filter_before_start_us)s" in root
    assert "ORDER BY start_time DESC, trace_id DESC" in root
    assert params["filter_seed_limit"] == 200
    assert params["filter_slice_start"] == start
    assert "FINAL" not in sql
    _render_driver_sql(sql, params)


@pytest.mark.parametrize(
    "key,value,days",
    [("agent.duration_s", 1, 365), ("interruption_latency_s", 0.01, 7)],
)
@pytest.mark.parametrize("has_match", [False, True])
def test_real_numeric_builder_selector_keeps_membership_seed_and_exact_replay(
    key,
    value,
    days,
    has_match,
):
    start = END - timedelta(days=days)
    filters = [
        _time_filter(start, END),
        _attribute_filter(
            key,
            value,
            filter_type="number",
            operation="greater_than",
        ),
    ]
    builder = _WitnessFallbackV2(project_id=PROJECT_ID, filters=filters)
    row = complete_root_row(
        {
            "trace_id": "old-match",
            "root_span_id": "old-root",
            "start_time": start + timedelta(days=1),
        },
        project_id=PROJECT_ID,
    )
    calls = []

    class Transport:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            calls.append(query)
            assert len(calls) <= 4
            if len(calls) == 1:
                assert "matching_scalar_trace_identities" in query
                assert params["filter_slice_start"] == start
                assert params["filter_slice_end"] == END
            elif len(calls) == 2:
                assert "filter_candidate_trace_ids" in query
                assert "argMax" not in query
            elif len(calls) == 3:
                assert_coherent_classifier(query)
                assert "WHERE latest_is_deleted = 0" in query
                assert params["candidate_trace_ids"] == ("old-match",)
            rows = [row] if has_match else []
            return QueryResult(
                data=rows,
                row_count=len(rows),
                backend_used="clickhouse",
                query_time_ms=1,
            )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=Transport(),
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=9500,
        max_query_count=8,
        max_seed_attempts=1,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )
    assert page.complete is True
    assert page.has_more is False
    assert page.rows == ([row] if has_match else [])
    assert len(calls) == (4 if has_match else 1)


def test_dense_company_keeps_finite_roots_instead_of_all_history_trace_set():
    builder = _builder(_reported_filter("company"))
    assert builder.supports_filter_candidate_seed_page() is False
    assert builder.recommended_filter_initial_slice_width() == timedelta(hours=1)
    sql, params = builder.build_filter_ordered_seed_page(
        slice_start=END - timedelta(hours=1),
        slice_end=END,
        limit=200,
    )
    assert "matching_scalar_trace_identities" not in sql
    assert "attrs_string" not in sql
    assert params["filter_seed_limit"] == 200
    _render_driver_sql(sql, params)


@pytest.mark.parametrize("org_scope", [False, True])
@pytest.mark.parametrize("user_first", [False, True])
def test_user_detail_company_seeds_native_user_and_replays_both_filters(
    org_scope,
    user_first,
):
    project_b = "00000000-0000-4000-8000-000000000002"
    user = {
        "column_id": "user_id",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "10000004",
        },
    }
    company = _reported_filter("company")
    start = END - timedelta(days=365)
    builder = TraceListQueryBuilderV2(
        **(
            {"project_ids": [PROJECT_ID, project_b]}
            if org_scope
            else {"project_id": PROJECT_ID}
        ),
        filters=[
            _time_filter(start, END),
            *([user, company] if user_first else [company, user]),
        ],
    )
    assert builder.supports_filter_candidate_seed_page() is True
    assert builder.recommended_filter_initial_slice_width() == END - start
    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=start,
        slice_end=END,
        limit=26,
    )
    assert "matching_user_trace_identities" in sql
    assert "matching_scalar_trace_identities" not in sql
    child = sql.split(
        "SELECT "
        + ("project_id, " if org_scope else "")
        + "trace_id, id AS root_span_id",
        1,
    )[0]
    assert "start_time" not in child
    assert "LIMIT" not in child
    assert "attrs_string" not in sql
    assert "(project_id, trace_id) IN" in sql
    assert "FROM end_user_id_remap AS remap_match FINAL" in sql
    assert "FROM spans FINAL" not in sql
    assert params["col_1"] == "10000004"
    if org_scope:
        assert "eu.project_id IN %(project_ids)s" in sql
        assert params["project_ids"] == [PROJECT_ID, project_b] or params[
            "project_ids"
        ] == (PROJECT_ID, project_b)
    _render_driver_sql(sql, params)
    seed = {
        "project_id": PROJECT_ID,
        "trace_id": "same-trace",
        "root_span_id": "root",
        "start_time": END - timedelta(days=1),
    }
    exact, exact_params = builder.build_filter_identity_match_query_from_seed_rows(
        [seed]
    )
    rendered = _render_driver_sql(exact, exact_params)
    assert "latest_is_deleted = 0" in exact
    assert "latest_attr_value_0_string" in exact
    assert "10000004" in rendered and "10000001" in rendered
    if org_scope:
        assert "(project_id, trace_id) IN" in exact
        assert "project_id = toUUID(" in rendered


@pytest.mark.parametrize("column", ["user", "user_id", "end_user_id"])
def test_raw_user_shaped_attribute_with_company_never_enables_native_seed(column):
    builder = TraceListQueryBuilderV2(
        project_ids=[PROJECT_ID],
        filters=[
            _time_filter(END - timedelta(days=365), END),
            _attribute_filter(column, "10000004"),
            _reported_filter("company"),
        ],
    )
    assert builder.supports_filter_candidate_seed_page() is False


def test_mixed_typed_or_witness_preserves_missing_default_branch():
    item = _attribute_filter("company_id", ["10000001", 0, True], operation="in")
    item["filter_config"]["attribute_value_types"] = ["string", "number", "boolean"]
    builder = _builder(item)
    assert builder.supports_filter_candidate_seed_page() is False
    sql, params = builder.build_filter_candidate_witness_probe([{"trace_id": "a"}])
    assert builder.prefer_filter_candidate_witness_probe_first() is True
    assert " OR " in sql
    assert "attrs_string[%(latest_filter_key_0)s]" in sql
    assert "attrs_bool[%(latest_filter_key_0)s]" in sql
    # Independently reduced equal-version Map fields may obtain numeric zero
    # from a missing-key row. This OR branch must retain key presence alone.
    assert "has(attrs_number.keys, %(latest_filter_key_0)s)" in sql
    assert "attrs_number[" not in sql
    assert "latest_filter_param_0_number" not in sql
    _render_driver_sql(sql, params)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"search": "text"},
        {"bounded_identity_only": True},
        {"bounded_internal_scan": True},
        {"bounded_sampling_salt": "test", "bounded_sampling_rate": 10.0},
    ],
)
def test_non_public_or_sampled_modes_keep_existing_cursor_batch(kwargs):
    builder = _builder(**kwargs)
    assert builder.recommended_filter_cursor_seed_batch_size() is None


class _PublicScalarWitnessBuilder(_CandidateWitnessHydrationFakeBuilder):
    """Exercise the real scalar SQL/capabilities with deterministic transport."""

    def __init__(self, rows, *, match_rows, kind="numeric"):
        super().__init__(
            rows,
            start=END - timedelta(days=7),
            end=END,
            match_rows=match_rows,
            recommended_batch_size=10,
            recommended_seed_batch_size=200,
        )
        self.actual = _builder(_reported_filter(kind))
        self.probe_sql = []

    def recommended_filter_cursor_seed_batch_size(self):
        return self.actual.recommended_filter_cursor_seed_batch_size()

    def filter_candidate_witness_is_optional(self):
        return self.actual.filter_candidate_witness_is_optional()

    def prefer_filter_candidate_witness_probe_first(self):
        return self.actual.prefer_filter_candidate_witness_probe_first()

    def filter_candidate_witness_replays_global_membership(self):
        return self.actual.filter_candidate_witness_replays_global_membership()

    def recommended_filter_candidate_witness_probe_timeout_ms(self):
        return self.actual.recommended_filter_candidate_witness_probe_timeout_ms()

    def recommended_filter_candidate_witness_probe_total_ms(self):
        return self.actual.recommended_filter_candidate_witness_probe_total_ms()

    def recommended_filter_candidate_witness_probe_max_bytes(self):
        return self.actual.recommended_filter_candidate_witness_probe_max_bytes()

    def recommended_filter_candidate_witness_fallback_classify_batch_size(self):
        return self.actual.recommended_filter_candidate_witness_fallback_classify_batch_size()

    def build_filter_candidate_witness_probe(self, rows):
        sql, params = self.actual.build_filter_candidate_witness_probe(
            [{"trace_id": row["id"]} for row in rows]
        )
        assert sql and "argMax" not in sql
        self.probe_sql.append((sql, params))
        return "prefilter", {"candidate_ids": params["filter_candidate_trace_ids"]}


def _rows(count=230):
    return [
        {
            "id": f"trace-{index:03d}",
            "root_span_id": f"root-{index:03d}",
            "start_time": END - timedelta(seconds=index + 1),
        }
        for index in range(count)
    ]


def _page(builder, executor, **kwargs):
    return read_bounded_filter_page(
        builder=builder,
        analytics=executor,
        filters=[_time_filter(builder.start, builder.end)],
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=8_000,
        max_query_count=64,
        include_incomplete_rows=True,
        bounded_continuation=True,
        **kwargs,
    )


@pytest.mark.parametrize("kind", ["numeric", "company"])
def test_sparse_scalar_cursor_finds_witnesses_past_26_and_rechecks_stale_rows(kind):
    rows = _rows()
    builder = _PublicScalarWitnessBuilder(rows, match_rows=rows[90:117], kind=kind)
    # Old values / subsequently deleted spans have raw witnesses but fail the
    # latest classifier. They must never enter hydration or the public page.
    executor = _CandidateWitnessHydrationFakeExecutor(
        builder, witness_ids={row["id"] for row in [rows[30], rows[51], *rows[90:117]]}
    )
    page = _page(builder, executor, max_seed_attempts=1)
    assert executor.calls[0][1]["limit"] == 200
    assert len(builder.probe_sql[0][1]["filter_candidate_trace_ids"]) == 200
    assert page.complete is True
    assert page.has_more is True
    assert [row["id"] for row in page.rows] == [row["id"] for row in rows[90:115]]
    classified = [
        p["candidate_ids"] for q, p in executor.calls if q == "match_identity"
    ]
    assert all(len(batch) <= 10 for batch in classified)
    assert {rows[30]["id"], rows[51]["id"]}.issubset(set().union(*map(set, classified)))
    assert executor.prefilter_settings[0]["max_bytes_to_read"] == 256 * 1024 * 1024
    assert next(t for q, t in executor.timeouts if q == "prefilter") <= 1_500


@pytest.mark.parametrize("fail_probe", [False, True])
def test_large_scalar_cursor_failure_resumes_every_unconsumed_candidate(fail_probe):
    rows = _rows(180)
    builder = _PublicScalarWitnessBuilder(rows, match_rows=rows[30:60])
    witnesses = {row["id"] for row in rows[30:60]}

    class FailSecondClassifier(_CandidateWitnessHydrationFakeExecutor):
        classifier_calls = 0

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if query == "match_identity":
                self.classifier_calls += 1
                if self.classifier_calls == 2:
                    self.calls.append((query, params))
                    raise ReadDeadlineExceeded("bounded classifier test failure")
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )

    first_executor = FailSecondClassifier(
        builder, witness_ids=witnesses, fail_prefilter=fail_probe
    )
    first = _page(builder, first_executor)
    assert first.complete is False
    consumed = 9 if fail_probe else 39
    assert first.continuation_before_id == rows[consumed]["id"]
    assert first.continuation_before_start_time == rows[consumed]["start_time"]
    assert [row["id"] for row in first.rows] == (
        [] if fail_probe else [row["id"] for row in rows[30:40]]
    )

    collected = list(first.rows)
    previous = first
    for _ in range(4):
        resumed_executor = _CandidateWitnessHydrationFakeExecutor(
            builder, witness_ids=witnesses
        )
        last = collected[-1] if collected else None
        resumed = _page(
            builder,
            resumed_executor,
            cursor_start_time=last["start_time"] if last else None,
            cursor_order_token=last["id"] if last else None,
            continuation_slice_start=previous.continuation_slice_start,
            continuation_slice_end=previous.continuation_slice_end,
            continuation_before_start_time=previous.continuation_before_start_time,
            continuation_before_id=previous.continuation_before_id,
        )
        collected.extend(resumed.rows)
        if resumed.complete and not resumed.has_more:
            break
        previous = resumed
    else:
        pytest.fail("scalar cursor did not finish")
    assert [row["id"] for row in collected] == [row["id"] for row in rows[30:60]]


def test_empty_scalar_witness_commits_only_the_consumed_200_root_batch():
    rows = _rows()
    builder = _PublicScalarWitnessBuilder(rows, match_rows=[rows[220]])
    executor = _CandidateWitnessHydrationFakeExecutor(
        builder, witness_ids={rows[220]["id"]}
    )
    first = _page(builder, executor, max_seed_attempts=1)
    assert first.complete is False
    assert first.rows == []
    assert first.continuation_before_id == rows[199]["id"]
    resumed = _page(
        builder,
        _CandidateWitnessHydrationFakeExecutor(builder, witness_ids={rows[220]["id"]}),
        continuation_slice_start=first.continuation_slice_start,
        continuation_slice_end=first.continuation_slice_end,
        continuation_before_start_time=first.continuation_before_start_time,
        continuation_before_id=first.continuation_before_id,
    )
    assert resumed.rows == [rows[220]]
    # A sparse partial page may checkpoint the exhausted slice before proving
    # the remaining six days empty. Its published match is still exact.
    assert resumed.complete or resumed.continuation_slice_end <= rows[220]["start_time"]


@pytest.mark.parametrize("fail_first_seed", [False, True])
def test_scalar_candidate_seed_jumps_past_many_empty_root_batches_to_march(
    fail_first_seed,
):
    start, end = datetime(2025, 9, 4), datetime(2026, 9, 4)
    recent = [
        {
            "id": f"recent-{i:05d}",
            "root_span_id": f"root-{i:05d}",
            "start_time": end - timedelta(seconds=i + 1),
        }
        for i in range(20_000)
    ]
    march = {
        "id": "march-match",
        "root_span_id": "march-root",
        "start_time": datetime(2026, 3, 4),
    }

    class MarchSeedBuilder(_PublicScalarWitnessBuilder):
        def __init__(self):
            super().__init__([*recent, march], match_rows=[march])
            self.start, self.end = start, end
            self.actual = TraceListQueryBuilderV2(
                project_id=PROJECT_ID,
                filters=[_time_filter(start, end), _reported_filter()],
            )

        def supports_filter_candidate_seed_page(self):
            return self.actual.supports_filter_candidate_seed_page()

        def filter_candidate_seed_proves_result_order(self):
            return self.actual.filter_candidate_seed_proves_result_order()

        def recommended_filter_initial_slice_width(self):
            return self.actual.recommended_filter_initial_slice_width()

        def recommended_filter_max_slice_width(self):
            return self.actual.recommended_filter_max_slice_width()

        def build_filter_candidate_seed_page(self, **kwargs):
            sql, _ = self.actual.build_filter_candidate_seed_page(**kwargs)
            assert "FROM matching_scalar_trace_identities" in sql
            return "scalar_seed", kwargs

    builder = MarchSeedBuilder()

    class MembershipExecutor(_CandidateWitnessHydrationFakeExecutor):
        failed = False

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if query == "scalar_seed":
                self.calls.append((query, params))
                assert settings["read_overflow_mode"] == "throw"
                assert settings["result_overflow_mode"] == "throw"
                if fail_first_seed and not self.failed:
                    self.failed = True
                    raise ReadDeadlineExceeded("bounded scalar seed failure")
                # The sole witness may be outside even the root's 12M window.
                # The generated child CTE above has no timestamp restriction.
                witnesses = [("march-match", datetime(2024, 1, 1))]
                identities = {trace_id for trace_id, _ in witnesses}
                selected = [row for row in builder.rows if row["id"] in identities]
                return _FakeExecutor(_FakeBuilder(selected)).execute_ch_query(
                    "seed",
                    params,
                    timeout_ms=timeout_ms,
                    settings=settings,
                )
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )

    executor = MembershipExecutor(builder, witness_ids={march["id"]})
    page = _page(builder, executor, max_seed_attempts=1)
    if fail_first_seed:
        # A failed required membership read is inconclusive, not an exhausted
        # window. No raw identities are published and no checkpoint advances.
        assert page.complete is False
        assert page.error_code == "read_budget_exceeded"
        assert page.rows == []
        assert page.continuation_before_id is None
        assert page.continuation_before_start_time is None
        assert page.continuation_slice_start is None
        assert page.continuation_slice_end is None
        page = _page(builder, executor, max_seed_attempts=1)
    assert page.complete is True
    assert page.has_more is False
    assert page.rows == [march]
    assert sum(query == "scalar_seed" for query, _ in executor.calls) == (
        2 if fail_first_seed else 1
    )
    assert not any(query == "seed" for query, _ in executor.calls)
    assert executor.calls[0][1]["slice_start"] == start
    assert executor.calls[0][1]["slice_end"] == end


def test_scalar_probe_total_wall_expiry_falls_back_without_skipping(monkeypatch):
    from tracer.selectors import trace_filter_reads

    rows = _rows(450)
    for index, row in enumerate(rows):
        row["start_time"] = END - timedelta(microseconds=index + 1)
    builder = _PublicScalarWitnessBuilder(rows, match_rows=[rows[410]])
    clock = [0.0]
    monkeypatch.setattr(trace_filter_reads, "monotonic", lambda: clock[0])

    class ExpireProbeWindow(_CandidateWitnessHydrationFakeExecutor):
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            result = super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )
            # Root acquisition consumes the optional probe's elapsed wall;
            # enough request time remains for the ordinary exact fallback.
            if query == "seed" and builder.probe_sql:
                clock[0] += 4.6
            return result

    executor = ExpireProbeWindow(builder, witness_ids={rows[410]["id"]})
    page = _page(builder, executor)
    assert sum(q == "prefilter" for q, _ in executor.calls) == 1
    assert any(q == "match_identity" for q, _ in executor.calls)
    # The request may stop after a safe prefix; its checkpoint cannot pass the
    # unreturned match even after the optional budget is exhausted.
    assert page.rows == [rows[410]] or (
        not page.complete
        and page.continuation_before_start_time >= rows[410]["start_time"]
    )


class _OptionalScalarSeedBuilder(_PublicScalarWitnessBuilder):
    def supports_filter_candidate_seed_page(self):
        return self.actual.supports_filter_candidate_seed_page()

    def filter_candidate_seed_is_optional(self):
        return self.actual.filter_candidate_seed_is_optional()

    def filter_candidate_seed_proves_result_order(self):
        return self.actual.filter_candidate_seed_proves_result_order()

    def recommended_filter_initial_slice_width(self):
        return self.actual.recommended_filter_initial_slice_width()

    def recommended_filter_max_slice_width(self):
        return self.actual.recommended_filter_max_slice_width()

    def build_filter_candidate_seed_page(self, **kwargs):
        sql, _ = self.actual.build_filter_candidate_seed_page(**kwargs)
        assert "matching_scalar_trace_identities" in sql
        return "scalar_seed", kwargs

    def build_filter_ordered_seed_page(self, **kwargs):
        sql, _ = self.actual.build_filter_ordered_seed_page(**kwargs)
        assert "matching_scalar_trace_identities" not in sql
        return self.build_filter_seed_page(**kwargs)


class _OptionalSeedExecutor(_CandidateWitnessHydrationFakeExecutor):
    def __init__(self, builder, *, seed_error=None, required_error=None, **kwargs):
        super().__init__(builder, **kwargs)
        self.seed_error = seed_error
        self.required_error = required_error

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if query == "scalar_seed":
            self.calls.append((query, params))
            self.timeouts.append((query, timeout_ms))
            self.settings_by_query.append((query, dict(settings)))
            if self.seed_error is not None:
                raise self.seed_error
            return _FakeExecutor(self.builder).execute_ch_query(
                "seed",
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )
        if query == self.required_error:
            self.calls.append((query, params))
            raise ReadDeadlineExceeded("required read failure")
        return super().execute_ch_query(
            query, params, timeout_ms=timeout_ms, settings=settings
        )


@pytest.mark.parametrize(
    "seed_fails,window_fails", [(False, False), (True, False), (True, True)]
)
def test_window_population_seed_keeps_fast_path_budget_and_one_shot_fallback(
    seed_fails, window_fails
):
    from tracer.selectors.trace_filter_reads import _READ_SETTINGS

    class PrimarySeedBuilder(_OptionalScalarSeedBuilder):
        def supports_filter_windowed_candidate_seed_page(self):
            return True

        def build_filter_windowed_candidate_seed_page(self, **kwargs):
            return "window_seed", kwargs

    class WindowExecutor(_OptionalSeedExecutor):
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if query == "window_seed":
                self.calls.append((query, params))
                self.timeouts.append((query, timeout_ms))
                self.settings_by_query.append((query, dict(settings)))
                if window_fails:
                    raise ReadDeadlineExceeded("window seed cap")
                return _FakeExecutor(self.builder).execute_ch_query(
                    "seed", params, timeout_ms=timeout_ms, settings=settings
                )
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )

    rows = _rows(40)
    builder = PrimarySeedBuilder(rows, match_rows=rows)
    executor = WindowExecutor(
        builder,
        seed_error=ReadDeadlineExceeded("seed cap") if seed_fails else None,
        witness_ids={row["id"] for row in rows},
    )
    page = _page(builder, executor, query_timeout_ms=2500)
    assert page.complete
    assert page.rows == rows[:25]
    assert sum(q == "scalar_seed" for q, _ in executor.calls) == 1
    assert next(t for q, t in executor.timeouts if q == "scalar_seed") <= 1500
    assert sum(q == "window_seed" for q, _ in executor.calls) == int(seed_fails)
    if seed_fails:
        assert page.attempts[0].error_code == "read_budget_exceeded"
        assert next(t for q, t in executor.timeouts if q == "window_seed") == 2500
        seed_settings = next(
            s for q, s in executor.settings_by_query if q == "window_seed"
        )
        for key in (
            "max_bytes_to_read",
            "max_threads",
            "max_memory_usage",
            "read_overflow_mode",
        ):
            assert seed_settings[key] == _READ_SETTINGS[key]
        if window_fails:
            assert any(q == "seed" for q, _ in executor.calls)


@pytest.mark.parametrize("seed_fails", [False, True])
def test_optional_seed_is_one_shot_and_finite_fallback_finds_later_batch(seed_fails):
    rows = _rows(600)
    builder = _OptionalScalarSeedBuilder(rows, match_rows=rows[410:437])
    executor = _OptionalSeedExecutor(
        builder,
        seed_error=ReadDeadlineExceeded("seed cap") if seed_fails else None,
        witness_ids={row["id"] for row in rows},
    )
    page = _page(builder, executor)
    assert page.complete is True
    assert page.rows == rows[410:435]
    assert sum(q == "scalar_seed" for q, _ in executor.calls) == 1
    finite = [params for q, params in executor.calls if q == "seed"]
    assert finite and finite[0]["slice_end"] - finite[0]["slice_start"] == timedelta(
        minutes=5
    )
    assert all(
        len(p["candidate_ids"]) <= 10
        for q, p in executor.calls
        if q == "match_identity"
    )
    optional_settings = next(
        s for q, s in executor.settings_by_query if q == "scalar_seed"
    )
    assert optional_settings["max_bytes_to_read"] <= 1024**3
    assert next(t for q, t in executor.timeouts if q == "scalar_seed") <= 1500
    if seed_fails:
        # Global Set failure does not disable the independently finite probe.
        # This dense probe is abandoned after one successful broad result.
        assert sum(q == "prefilter" for q, _ in executor.calls) == 1
        assert page.attempts[0].kind == "candidate_seed"
        assert page.attempts[0].error_code == "read_budget_exceeded"


@pytest.mark.parametrize("finite_probe_fails", [False, True])
def test_global_seed_failure_keeps_independent_finite_witness_and_exact_replay(
    finite_probe_fails,
):
    rows = _rows(600)
    matches = rows[410:437]
    stale_witness = rows[391]
    builder = _OptionalScalarSeedBuilder(rows, match_rows=matches)
    executor = _OptionalSeedExecutor(
        builder,
        seed_error=ReadDeadlineExceeded("global Set cap"),
        witness_ids={row["id"] for row in [stale_witness, *matches]},
        fail_prefilter=finite_probe_fails,
    )
    page = _page(builder, executor)
    assert page.complete is True
    assert page.rows == matches[:25]
    assert sum(q == "scalar_seed" for q, _ in executor.calls) == 1
    probes = [p["candidate_ids"] for q, p in executor.calls if q == "prefilter"]
    assert probes and all(len(ids) <= 200 for ids in probes)
    classified = [
        p["candidate_ids"] for q, p in executor.calls if q == "match_identity"
    ]
    assert classified and all(len(ids) <= 10 for ids in classified)
    if finite_probe_fails:
        assert len(probes) == 1
        assert any(rows[0]["id"] in ids for ids in classified)
    else:
        # Five-minute seed boundaries can split the 200-ID prefix; all
        # successful finite probes retain exact ordered coverage.
        assert len(probes) == 4
        assert all(rows[0]["id"] not in ids for ids in classified)
        assert any(stale_witness["id"] in ids for ids in classified)
    assert stale_witness not in page.rows


def test_failed_optional_seed_then_empty_tail_never_proves_whole_window_empty():
    builder = _OptionalScalarSeedBuilder([], match_rows=[])
    page = _page(
        builder,
        _OptionalSeedExecutor(builder, seed_error=ReadDeadlineExceeded("seed cap")),
        max_seed_attempts=1,
    )
    assert page.complete is False
    assert page.rows == []
    assert page.continuation_slice_end == END - timedelta(minutes=5)
    assert page.continuation_before_id is None


@pytest.mark.parametrize("required_error", ["seed", "match_identity", "hydrate"])
def test_optional_seed_failure_does_not_hide_required_failure_or_skip_match(
    required_error,
):
    rows = _rows(40)
    builder = _OptionalScalarSeedBuilder(rows, match_rows=rows)
    executor = _OptionalSeedExecutor(
        builder,
        seed_error=ReadDeadlineExceeded("seed cap"),
        required_error=required_error,
        witness_ids={row["id"] for row in rows},
    )
    first = _page(builder, executor, max_seed_attempts=1)
    assert first.complete is False
    assert first.rows == []
    assert first.error_code == "read_budget_exceeded"
    assert first.continuation_before_id is None
    assert first.continuation_before_start_time is None
    second = _page(
        builder,
        _OptionalSeedExecutor(
            builder,
            seed_error=ReadDeadlineExceeded("seed cap"),
            witness_ids={row["id"] for row in rows},
        ),
    )
    assert second.complete is True
    assert second.rows == rows[:25]


def test_optional_seed_fallback_retains_deep_checkpoint_and_same_time_ties():
    when = END - timedelta(days=3)
    rows = sorted(
        [{**row, "start_time": when} for row in _rows(60)],
        key=lambda row: row["id"],
        reverse=True,
    )
    builder = _OptionalScalarSeedBuilder(rows, match_rows=rows)
    checkpoint = {
        "continuation_slice_start": builder.start,
        "continuation_slice_end": builder.end,
        "continuation_before_start_time": when,
        "continuation_before_id": rows[20]["id"],
    }
    failed = _OptionalSeedExecutor(
        builder, seed_error=ReadDeadlineExceeded("seed cap"), required_error="seed"
    )
    first = _page(builder, failed, **checkpoint)
    assert first.complete is False and first.rows == []
    assert (
        first.continuation_before_id is None
    )  # No replacement checkpoint was committed.
    fallback = next(p for q, p in failed.calls if q == "seed")
    assert fallback["before_id"] == rows[20]["id"]
    assert fallback["before_start_time"] == when
    assert fallback["slice_end"] == when + timedelta(microseconds=1)
    assert fallback["slice_start"] <= when < fallback["slice_end"]
    # Retrying the original signed tuple must neither restart newer rows nor
    # skip the unconsumed identities at the same physical timestamp.
    second = _page(
        builder,
        _OptionalSeedExecutor(
            builder,
            seed_error=ReadDeadlineExceeded("seed cap"),
            witness_ids={row["id"] for row in rows},
        ),
        **checkpoint,
    )
    assert second.complete is True
    assert second.rows == rows[21:46]


def test_optional_seed_expired_wall_never_runs_fallback_query(monkeypatch):
    from tracer.selectors import trace_filter_reads

    clock = [0.0]
    monkeypatch.setattr(trace_filter_reads, "monotonic", lambda: clock[0])
    rows = _rows(40)
    builder = _OptionalScalarSeedBuilder(rows, match_rows=rows)

    class Expired(_OptionalSeedExecutor):
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            if query == "scalar_seed":
                clock[0] = 9.0
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )

    executor = Expired(builder, seed_error=ReadDeadlineExceeded("expired seed"))
    page = _page(builder, executor)
    assert page.complete is False and page.rows == []
    assert page.error_code == "deadline_exceeded"
    assert [q for q, _ in executor.calls] == ["scalar_seed"]
    assert page.continuation_before_id is None


def test_company_witness_cap_falls_back_once_and_can_publish_exact_page():
    rows = _rows(450)
    builder = _PublicScalarWitnessBuilder(
        rows, match_rows=rows[410:437], kind="company"
    )
    executor = _CandidateWitnessHydrationFakeExecutor(builder, fail_prefilter=True)
    page = _page(builder, executor)
    assert page.complete is True
    assert page.rows == rows[410:435]
    assert sum(q == "prefilter" for q, _ in executor.calls) == 1
    assert all(
        len(p["candidate_ids"]) <= 10
        for q, p in executor.calls
        if q == "match_identity"
    )
    assert any(a.kind == "prefilter" and a.error_code for a in page.attempts)


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_optional_seed_does_not_hide_programming_errors(error_type):
    builder = _OptionalScalarSeedBuilder([], match_rows=[])
    executor = _OptionalSeedExecutor(
        builder, seed_error=error_type("invalid SQL builder")
    )
    with pytest.raises(error_type, match="invalid SQL builder"):
        _page(builder, executor)
    assert [q for q, _ in executor.calls] == ["scalar_seed"]
