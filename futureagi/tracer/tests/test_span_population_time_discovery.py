"""Raw span gap proofs must never substitute for latest typed membership."""

from datetime import timedelta

import pytest

from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_trace_root_time_discovery import (
    END,
    _ProbeBuilder,
    _ProbeExecutor,
    _read,
    _us,
)
from tracer.tests.test_trace_root_time_discovery import (
    START as WINDOW_START,
)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


class PopulationBuilder(_ProbeBuilder):
    def supports_filter_population_time_discovery(self):
        return True

    def build_filter_population_time_discovery_query(self, *, slice_start, slice_end):
        return "population_probe", {"slice_start": slice_start, "slice_end": slice_end}


class PopulationExecutor(_ProbeExecutor):
    supports_bounded_speculative_reads = False

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if query != "population_probe":
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )
        result = super().execute_ch_query(
            "root_probe", params, timeout_ms=timeout_ms, settings=settings
        )
        self.calls[-1] = (query, params)
        return QueryResult(
            [{"newest_raw_time_us": row["newest_raw_root_us"]} for row in result.data],
            result.row_count,
            "clickhouse",
            result.query_time_ms,
        )


def fake(rows=(), **kwargs):
    return PopulationBuilder(
        list(rows), start=WINDOW_START, end=END, recommended_seed_batch_size=4, **kwargs
    )


def run(builder, executor=None, **kwargs):
    return _read(
        builder,
        executor or PopulationExecutor(builder),
        root_time_discovery=False,
        **kwargs,
    )


@pytest.mark.unit
def test_uncapped_span_discovery_waits_for_empty_seed_then_skips_only_proven_tail():
    row = {"id": "span", "start_time": END - timedelta(days=2, minutes=20)}
    subject = fake([row])
    executor = PopulationExecutor(subject)
    result = run(subject, executor)
    assert result.complete and result.rows == [row]
    assert executor.calls[0][0] == "seed"
    probes = [p for q, p in executor.calls if q == "population_probe"]
    assert len(probes) == 2
    assert all(p["slice_end"] - p["slice_start"] <= timedelta(days=1) for p in probes)
    assert sum(a.kind == "population_time_discovery" for a in result.attempts) == 2
    # No raw time value is ever published as a result.
    assert any(a.kind == "classify" for a in result.attempts)


@pytest.mark.unit
def test_active_recent_full_page_does_not_pay_for_discovery():
    subject = fake(
        [{"id": key, "start_time": END - timedelta(minutes=1)} for key in ["a", "b"]]
    )
    executor = PopulationExecutor(subject)
    assert run(subject, executor).complete
    assert not any(q == "population_probe" for q, _ in executor.calls)


@pytest.mark.unit
@pytest.mark.parametrize("width_days", [1, 7])
@pytest.mark.parametrize(
    "caller_workers,expected_broad", [(None, 2), (1, 1), (2, 2), (4, 2)]
)
def test_broad_key_discovery_uses_finite_workers_and_preserves_explicit_caps(
    monkeypatch, width_days, caller_workers, expected_broad
):
    from tracer.selectors import trace_filter_reads as selector

    monkeypatch.setattr(selector, "_POPULATION_TIME_DISCOVERY_MAX_THREADS", 2)
    subject = fake()
    subject.recommended_filter_population_time_discovery_windows = lambda: (
        timedelta(days=width_days),
    )
    executor = PopulationExecutor(subject)
    read_settings = {"max_memory_usage": 64 * 1024**2}
    if caller_workers is not None:
        read_settings["max_threads"] = caller_workers
    result = run(subject, executor, read_settings=read_settings)
    assert result.complete
    probes = [
        settings for query, _, settings in executor.envelopes if query == "root_probe"
    ]
    assert probes
    assert all(
        settings["max_threads"] == (expected_broad if width_days > 1 else 1)
        for settings in probes
    )
    assert all(settings["max_memory_usage"] == 64 * 1024**2 for settings in probes)
    assert all(settings["result_overflow_mode"] == "throw" for settings in probes)


@pytest.mark.unit
@pytest.mark.parametrize("fail_second_probe", [False, True])
def test_rearmed_gap_after_rejected_hour_keeps_older_matches(fail_second_probe):
    rejected = {"id": "rejected", "start_time": END - timedelta(days=2)}
    older = {"id": "older", "start_time": END - timedelta(days=6)}
    subject = fake([rejected, older], match_rows=[older])
    subject.recommended_filter_population_time_discovery_windows = lambda: (
        timedelta(days=7),
    )

    class RearmExecutor(PopulationExecutor):
        probes = 0

        def execute_ch_query(self, query, params, **kwargs):
            if query == "population_probe":
                self.probes += 1
                if fail_second_probe and self.probes == 2:
                    self.fail_probe = ReadDeadlineExceeded("diagnostic")
            return super().execute_ch_query(query, params, **kwargs)

    executor = RearmExecutor(subject)
    result = run(subject, executor, page_size=3, max_seed_attempts=64)
    assert result.complete and result.rows == [older]
    probes = [
        (i, params)
        for i, (query, params) in enumerate(executor.calls)
        if query in {"population_probe", "root_probe"}
    ]
    assert len(probes) >= 2
    first, second = probes[:2]
    assert second[1]["slice_end"] <= rejected["start_time"].replace(
        minute=0, second=0, microsecond=0
    )
    assert any(
        query == "match" for query, _ in executor.calls[first[0] + 1 : second[0]]
    )
    assert not any(row["id"] == "rejected" for row in result.rows)


@pytest.mark.unit
def test_failed_metadata_query_does_not_skip_unread_slice():
    row = {"id": "span", "start_time": END - timedelta(hours=2)}
    subject = fake([row])
    executor = PopulationExecutor(
        subject, fail_probe=ReadDeadlineExceeded("diagnostic")
    )
    result = run(subject, executor)
    assert result.complete and result.rows == [row]
    assert sum(a.kind == "population_time_discovery" for a in result.attempts) == 1
    assert result.attempts[1].error_code is not None


@pytest.mark.unit
def test_raw_hit_is_still_rejected_by_exact_classifier():
    subject = fake(
        [{"id": "deleted", "start_time": END - timedelta(hours=2)}], match_rows=[]
    )
    result = run(subject)
    assert result.rows == []


@pytest.mark.unit
def test_unfinished_timestamp_keyset_is_not_jumped():
    stamp = END - timedelta(hours=3)
    subject = fake([{"id": name, "start_time": stamp} for name in ["a", "b", "c"]])
    executor = PopulationExecutor(subject)
    result = run(subject, executor, cursor_start_time=stamp, cursor_order_token="c")
    assert result.rows[0]["id"] == "b"
    assert not any(q == "population_probe" for q, _ in executor.calls)


@pytest.mark.unit
@pytest.mark.parametrize("period", [7, 30, 365])
@pytest.mark.parametrize("operation", ["not_in", "is_null"])
def test_query_uses_only_scope_time_and_necessary_typed_presence(period, operation):
    subject = SpanListQueryBuilderV2(
        project_id=PROJECT,
        project_version_id="mutable-version",
        bounded_internal_scan=True,
        filters=[
            time_filter(
                start=START - timedelta(days=period), end=START + timedelta(days=1)
            ),
            {
                "column_id": "company_id",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": operation,
                    "filter_value": ["a", "b"] if operation == "not_in" else None,
                },
            },
        ],
    )
    sql, params = subject.build_filter_population_time_discovery_query(
        slice_start=START, slice_end=START + timedelta(days=1)
    )
    assert params["project_id"] == PROJECT
    assert params["population_start_us"] == _us(START)
    assert "maxOrNull" in sql
    for forbidden in [
        "company_id",
        "project_version_id",
        "is_deleted",
        "FINAL",
        "parent_span_id",
        "LIMIT",
        "SAMPLE",
    ]:
        assert forbidden not in sql
    if operation == "not_in":
        assert "attrs_string.keys" in sql
        assert "latest_filter_key_0" in sql
        assert "latest_filter_param_0" not in sql
        assert "NOT IN" not in sql
    else:
        # Missing keys can satisfy is_null, so narrowing this raw population
        # to key-present rows would silently lose valid results.
        assert "attrs_" not in sql


@pytest.mark.integration
def test_real_raw_metadata_keeps_versions_tombstones_and_scope(request):
    execute, insert = request.getfixturevalue("engine")
    subject = SpanListQueryBuilderV2(
        project_id=PROJECT,
        bounded_internal_scan=True,
        filters=[time_filter(START - timedelta(days=2), START + timedelta(days=2))],
    )
    insert(
        id="changed",
        start_time=START + timedelta(minutes=20),
        project_version_id=OTHER_PROJECT,
    )
    insert(id="changed", start_time=START + timedelta(minutes=10), _version=2)
    insert(id="deleted", start_time=START + timedelta(minutes=50), is_deleted=1)
    insert(
        id="foreign", project_id=OTHER_PROJECT, start_time=START + timedelta(minutes=59)
    )
    sql, params = subject.build_filter_population_time_discovery_query(
        slice_start=START, slice_end=START + timedelta(days=1)
    )
    assert execute(sql, params) == [
        {"newest_raw_time_us": _us(START + timedelta(minutes=50))}
    ]
    sql, params = subject.build_filter_population_time_discovery_query(
        slice_start=START + timedelta(days=1), slice_end=START + timedelta(days=2)
    )
    assert execute(sql, params) == [{"newest_raw_time_us": None}]


@pytest.mark.integration
@pytest.mark.parametrize(
    "data_type,column,yes,no",
    [
        ("number", "attrs_number", 1, 0),
        ("text", "attrs_string", "match", "other"),
        ("boolean", "attrs_bool", 1, 0),
    ],
)
def test_engine_selector_preserves_latest_typed_membership(
    request, data_type, column, yes, no
):
    from tracer.selectors.trace_filter_reads import read_bounded_filter_page

    execute, insert = request.getfixturevalue("engine")
    if (
        data_type == "text"
        and not execute(
            "SELECT count() AS n FROM system.functions WHERE name = 'lowerUTF8'"
        )[0]["n"]
    ):
        pytest.skip("Full Unicode CH25 engine required; reduced chdb lacks lowerUTF8")
    insert(id="good", **{column: {"wanted": yes}})
    insert(id="changed", **{column: {"wanted": yes}})
    insert(
        id="changed",
        _version=2,
        start_time=START + timedelta(minutes=10),
        **{column: {"wanted": no}},
    )
    insert(id="deleted", **{column: {"wanted": yes}})
    insert(id="deleted", _version=2, is_deleted=1, **{column: {"wanted": yes}})
    insert(id="foreign", project_id=OTHER_PROJECT, **{column: {"wanted": yes}})

    class Executor:
        supports_bounded_speculative_reads = False

        def execute_ch_query(self, sql, params, **_kwargs):
            rows = execute(sql, params)
            return QueryResult(rows, len(rows), "clickhouse", 0)

    filters = [
        time_filter(START - timedelta(days=1), START + timedelta(days=2)),
        {
            "column_id": "wanted",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": data_type,
                "filter_op": "equals",
                "filter_value": bool(yes) if data_type == "boolean" else yes,
            },
        },
    ]
    subject = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=filters, bounded_internal_scan=True
    )
    payload = read_bounded_filter_page(
        builder=subject,
        analytics=Executor(),
        filters=filters,
        key_field="id",
        page_number=0,
        page_size=25,
        deadline_ms=30000,
        include_incomplete_rows=True,
        bounded_continuation=True,
        max_seed_attempts=64,
        max_query_count=128,
    )
    assert payload.complete
    assert [row["id"] for row in payload.rows] == ["good"]
    assert any(a.kind == "population_time_discovery" for a in payload.attempts)


@pytest.fixture
def population_clock(monkeypatch):
    import tracer.selectors.trace_filter_reads as selector

    clock = [0.0]
    monkeypatch.setattr(selector, "monotonic", lambda: clock[0])
    monkeypatch.setattr(selector, "_BOUNDED_CONTINUATION_MIN_QUERY_HEADROOM_MS", 3000)
    return clock


@pytest.mark.unit
@pytest.mark.parametrize("failed_query", ["seed", "match"])
def test_population_jump_failure_resumes_full_hour_and_all_tied_ids(
    population_clock, failed_query
):
    stamp = END - timedelta(hours=20)
    hour = stamp.replace(minute=0, second=0, microsecond=0)
    rows = [{"id": name, "start_time": stamp} for name in ["a", "b", "c"]]
    subject = fake(rows)

    class FailAfterJump(PopulationExecutor):
        def execute_ch_query(self, query, params, **kwargs):
            if query == failed_query and any(
                previous == "population_probe" for previous, _ in self.calls
            ):
                self.calls.append((query, params))
                raise ReadDeadlineExceeded("required read after proven jump")
            return super().execute_ch_query(query, params, **kwargs)

    executor = FailAfterJump(subject)
    interrupted = run(subject, executor)
    assert not interrupted.complete and interrupted.rows == []
    assert interrupted.error_code == "read_budget_exceeded"
    assert interrupted.continuation_slice_start == hour
    assert interrupted.continuation_slice_end == hour + timedelta(hours=1)
    assert interrupted.continuation_before_start_time is None
    assert interrupted.continuation_before_id is None
    assert sum(a.kind == "population_time_discovery" for a in interrupted.attempts) == 1

    resumed_executor = PopulationExecutor(subject)
    resumed = run(
        subject,
        resumed_executor,
        carry_continuation_slice_width=True,
        continuation_slice_start=interrupted.continuation_slice_start,
        continuation_slice_end=interrupted.continuation_slice_end,
    )
    first_seed = resumed_executor.calls[0]
    assert first_seed[0] == "seed"
    assert first_seed[1]["slice_start"] == hour
    assert first_seed[1]["slice_end"] == hour + timedelta(hours=1)
    assert first_seed[1]["before_start_time"] is None
    assert resumed.complete and resumed.has_more
    assert [row["id"] for row in resumed.rows] == ["c"]

    visible = list(resumed.rows)
    for previous_id, expected_id in [("c", "b"), ("b", "a")]:
        next_executor = PopulationExecutor(subject)
        page = run(
            subject,
            next_executor,
            cursor_start_time=stamp,
            cursor_order_token=previous_id,
        )
        assert page.complete and [row["id"] for row in page.rows] == [expected_id]
        assert next_executor.calls[0][1]["before_id"] == previous_id
        visible.extend(page.rows)
    assert [row["id"] for row in visible] == ["c", "b", "a"]


@pytest.mark.unit
@pytest.mark.parametrize("hit", [False, True], ids=["null", "hit"])
def test_population_probe_returned_after_deadline_keeps_pre_probe_checkpoint(
    population_clock, hit
):
    rows = [{"id": "old", "start_time": END - timedelta(hours=20)}] if hit else []
    subject = fake(rows)
    executor = PopulationExecutor(
        subject, clock=population_clock, delays={"root_probe": 5100}
    )
    result = run(subject, executor)
    assert not result.complete and result.error_code == "deadline_exceeded"
    assert result.rows == []
    # The initial empty seed is proven. The late NULL/hit is not committed.
    assert result.continuation_slice_end == END - timedelta(hours=1)
    assert result.continuation_slice_start is None
    assert result.continuation_before_start_time is None
    assert result.continuation_before_id is None
    assert [kind for kind, _ in executor.calls] == ["seed", "population_probe"]
    assert result.attempts[-1].error_code is None  # completed SQL, expired request


@pytest.mark.unit
@pytest.mark.parametrize("hit", [False, True], ids=["null", "hit"])
def test_population_completed_proof_survives_lost_next_query_headroom(
    population_clock, hit
):
    stamp = END - timedelta(hours=20)
    subject = fake([{"id": "old", "start_time": stamp}] if hit else [])
    executor = PopulationExecutor(
        subject, clock=population_clock, delays={"root_probe": 2100}
    )
    result = run(subject, executor)
    assert not result.complete and result.error_code == "deadline_exceeded"
    assert result.rows == []
    assert [kind for kind, _ in executor.calls] == ["seed", "population_probe"]
    if hit:
        hour = stamp.replace(minute=0, second=0, microsecond=0)
        assert result.continuation_slice_start == hour
        assert result.continuation_slice_end == hour + timedelta(hours=1)
    else:
        assert result.continuation_slice_end == END - timedelta(hours=25)
    assert result.continuation_before_start_time is None
    assert result.continuation_before_id is None


def _engine_population_page(execute, subject, filters, **overrides):
    from tracer.selectors.trace_filter_reads import read_bounded_filter_page

    class Executor:
        supports_bounded_speculative_reads = False

        def execute_ch_query(self, sql, params, **_kwargs):
            rows = execute(sql, params)
            return QueryResult(rows, len(rows), "clickhouse", 0)

    options = {
        "builder": subject,
        "analytics": Executor(),
        "filters": filters,
        "key_field": "id",
        "page_number": 0,
        "page_size": 25,
        "deadline_ms": 30000,
        "include_incomplete_rows": True,
        "bounded_continuation": True,
        "max_seed_attempts": 64,
        "max_query_count": 128,
    }
    options.update(overrides)
    return read_bounded_filter_page(**options)


@pytest.mark.integration
def test_adaptive_population_batches_preserve_physical_ties_and_latest_winners(
    request,
):
    execute, insert = request.getfixturevalue("engine")
    stamp = START + timedelta(minutes=20)
    # One local INSERT creates a rejection-heavy same-timestamp population.
    # This fixture never connects to a database server.
    execute(
        """INSERT INTO spans (
            project_id, observation_type, service_name, start_time, trace_id, id,
            attrs_number, cost, _version
        ) SELECT toUUID(%(project)s), 'span', 'service-a',
                 fromUnixTimestamp64Micro(%(stamp)s), 'trace',
                 concat('reject-', leftPad(toString(number), 4, '0')),
                 map('tag', toFloat64(0)), toFloat64(1), toUInt64(1)
          FROM numbers(260)""",
        {"project": PROJECT, "stamp": _us(stamp)},
    )
    insert(id="match-tie", start_time=stamp, service_name="service-a")
    insert(id="match-tie", start_time=stamp, service_name="service-b")
    insert(id="match-older", start_time=stamp)
    insert(id="match-older", start_time=stamp - timedelta(minutes=5), _version=2)
    insert(id="stale-removed", start_time=stamp)
    insert(id="stale-removed", start_time=stamp, _version=2, attrs_number={})
    insert(id="stale-deleted", start_time=stamp)
    insert(id="stale-deleted", start_time=stamp, _version=2, is_deleted=1)
    insert(id="stale-value", start_time=stamp)
    insert(
        id="stale-value", start_time=stamp, _version=2, attrs_number={"tag": 0}, cost=1
    )
    insert(id="match-tie", start_time=stamp, project_id=OTHER_PROJECT)
    filters = [
        time_filter(START - timedelta(days=1), START + timedelta(days=1)),
        {
            "column_id": "tag",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "number",
                "filter_op": "is_not_null",
                "filter_value": None,
            },
        },
        # This native negative remains classifier-only. Scalar attributes now
        # filter after FINAL in ordinary seeds; use an actual residual so the
        # engine still crosses every adaptive batch transition.
        {
            "column_id": "cost",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "number",
                "filter_op": "not_equals",
                "filter_value": 1,
            },
        },
    ]
    subject = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=filters, bounded_internal_scan=True
    )
    assert subject.recommended_filter_cursor_seed_batch_size() == 32
    assert subject.recommended_filter_cursor_adaptive_seed_batch_size() == 200
    seed_limits = []

    def record(sql, params):
        if "filter_seed_limit" in params:
            seed_limits.append(params["filter_seed_limit"])
        return execute(sql, params)

    first = _engine_population_page(record, subject, filters, page_size=2)
    assert first.complete and first.has_more
    assert max(seed_limits) == 200
    assert {32, 64, 128, 200} <= set(seed_limits)
    second = _engine_population_page(
        record,
        subject,
        filters,
        page_size=2,
        cursor_start_time=first.rows[-1]["start_time"],
        cursor_order_token=subject.bounded_filter_row_order_token(first.rows[-1]),
    )
    assert second.complete and not second.has_more
    expected = execute(
        """SELECT project_id, trace_id, id, start_time, observation_type,
                  service_name, _version
           FROM spans FINAL
           WHERE project_id = toUUID(%(project)s) AND is_deleted = 0
             AND mapContains(attrs_number, 'tag')
             AND cost != 1
           ORDER BY start_time DESC, project_id DESC, trace_id DESC, id DESC,
                    observation_type DESC, service_name DESC""",
        {"project": PROJECT},
    )

    def identity_and_winner(row):
        return (
            subject.bounded_filter_row_identity(row),
            row["start_time"],
            int(row["_version"]),
        )

    actual = first.rows + second.rows
    assert len(actual) == 3
    assert list(map(identity_and_winner, actual)) == list(
        map(identity_and_winner, expected)
    )
    assert [row["service_name"] for row in first.rows] == [
        "service-b",
        "service-a",
    ]
    assert second.rows[0]["id"] == "match-older"
    assert int(second.rows[0]["_version"]) == 2


@pytest.mark.integration
@pytest.mark.parametrize("boundary", ["lower", "upper"])
def test_population_hit_replays_replacements_across_non_hour_window_boundary(
    request, boundary
):
    execute, insert = request.getfixturevalue("engine")
    edge = START + timedelta(minutes=30, microseconds=123456)
    if boundary == "lower":
        start, end = edge, START + timedelta(days=2)
        outside = edge - timedelta(microseconds=1)
        inside = edge
        original = START + timedelta(minutes=45)
        good = START + timedelta(minutes=40)
    else:
        start, end = START - timedelta(days=2), edge
        outside = edge  # upper bound is exclusive
        inside = START + timedelta(minutes=10)
        original = START + timedelta(minutes=20)
        good = START + timedelta(minutes=5)
    insert(id="good", start_time=good)
    insert(id="enters", start_time=outside, attrs_number={"tag": 0})
    insert(id="enters", start_time=inside, _version=2)
    insert(id="leaves", start_time=original)
    insert(id="leaves", start_time=outside, _version=2)
    insert(id="deleted", start_time=original)
    insert(id="deleted", start_time=outside, _version=2, is_deleted=1)
    filters = [
        time_filter(start, end),
        {
            "column_id": "tag",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "number",
                "filter_op": "equals",
                "filter_value": 1,
            },
        },
    ]
    subject = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=filters, bounded_internal_scan=True
    )
    payload = _engine_population_page(execute, subject, filters)
    assert payload.complete
    expected = ["good", "enters"] if boundary == "lower" else ["enters", "good"]
    assert [row["id"] for row in payload.rows] == expected
    current = {row["id"]: row for row in payload.rows}
    assert current["enters"]["start_time"] == inside
    assert int(current["enters"]["_version"]) == 2
    assert any(a.kind == "population_time_discovery" for a in payload.attempts)


@pytest.mark.integration
@pytest.mark.parametrize("projects", [(PROJECT,), (PROJECT, OTHER_PROJECT)])
def test_population_multi_project_scope_preserves_colliding_physical_ids(
    request, projects
):
    execute, insert = request.getfixturevalue("engine")
    foreign = "33333333-3333-3333-3333-333333333333"
    stamp = START + timedelta(minutes=10)
    insert(id="shared", start_time=stamp)
    insert(id="shared", project_id=OTHER_PROJECT, start_time=stamp, _version=2)
    insert(
        id="shared",
        project_id=foreign,
        start_time=START + timedelta(minutes=55),
        _version=99,
    )
    filters = [time_filter(START - timedelta(days=1), START + timedelta(days=2))]
    subject = SpanListQueryBuilderV2(
        project_ids=list(projects), filters=filters, bounded_internal_scan=True
    )
    sql, params = subject.build_filter_population_time_discovery_query(
        slice_start=START, slice_end=START + timedelta(days=1)
    )
    assert params["project_ids"] == projects
    assert execute(sql, params) == [{"newest_raw_time_us": _us(stamp)}]
    payload = _engine_population_page(execute, subject, filters)
    assert payload.complete
    assert [(str(row["project_id"]), row["id"]) for row in payload.rows] == [
        (project, "shared") for project in sorted(projects, reverse=True)
    ]
    assert any(a.kind == "population_time_discovery" for a in payload.attempts)


@pytest.mark.unit
@pytest.mark.parametrize(
    "mode", ["anchor", "sample_zero", "sample_half", "sort", "score_seed", "one_hour"]
)
def test_population_builder_rejects_unqualified_modes(mode):
    kwargs = {"bounded_internal_scan": True}
    start, end = WINDOW_START, END
    additional_filters = []
    if mode == "anchor":
        kwargs["bounded_anchor_probe"] = True
    elif mode.startswith("sample"):
        kwargs.update(
            bounded_sampling_salt="population-mode-test",
            bounded_sampling_rate=0 if mode == "sample_zero" else 50,
        )
    elif mode == "sort":
        kwargs["sort_params"] = [{"column_id": "cost", "direction": "asc"}]
    elif mode == "score_seed":
        label = "44444444-4444-4444-4444-444444444444"
        kwargs["annotation_label_ids"] = [label]
        additional_filters = [
            {
                "column_id": label,
                "filter_config": {
                    "col_type": "ANNOTATION",
                    "filter_type": "number",
                    "filter_op": "equals",
                    "filter_value": 1,
                },
            }
        ]
    else:
        start = end - timedelta(hours=1)
    subject = SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[time_filter(start, end), *additional_filters],
        **kwargs,
    )
    if mode == "score_seed":
        assert subject.supports_filter_candidate_seed_page()
    assert not subject.supports_filter_population_time_discovery()
    with pytest.raises(ValueError, match="unavailable"):
        subject.build_filter_population_time_discovery_query(
            slice_start=end - timedelta(hours=1), slice_end=end
        )


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["noncursor", "other_entity", "deferred", "unordered"])
def test_population_selector_mode_guards_keep_original_acquisition(
    population_clock, mode
):
    subject = fake(seed_proves_order=mode != "unordered")
    executor = PopulationExecutor(subject)
    kwargs = {}
    if mode == "noncursor":
        kwargs.update(bounded_continuation=False, include_incomplete_rows=False)
    elif mode == "other_entity":
        kwargs["key_field"] = "trace_id"
    elif mode == "deferred":
        kwargs.update(defer_classification=True, max_seed_attempts=1)
    page = run(subject, executor, **kwargs)
    assert page.rows == []
    assert any(query == "seed" for query, _ in executor.calls)
    assert not any(query == "population_probe" for query, _ in executor.calls)
    if mode == "deferred":
        assert page.classification_deferred


@pytest.mark.unit
@pytest.mark.parametrize("probe_error", [None, "budget", "programming"])
def test_population_real_application_wrapper_clears_caps_and_restores_context(
    population_clock, monkeypatch, probe_error
):
    from tracer.services.clickhouse.application_read_policy import (
        UNLIMITED_STATEMENT_SETTINGS,
        is_application_read,
    )
    from tracer.services.clickhouse.v2 import query_service

    subject = fake([{"id": "old", "start_time": END - timedelta(hours=20)}])
    error = (
        ReadDeadlineExceeded("inconclusive probe")
        if probe_error == "budget"
        else RuntimeError("compiler defect")
        if probe_error == "programming"
        else None
    )
    transport = PopulationExecutor(subject, fail_probe=error)
    envelopes = []

    class LocalClient:
        server_enforced_readonly = False
        server_profile_locked = False

        def execute_read(self, query, params, *, timeout_ms, settings):
            assert is_application_read()
            assert timeout_ms is None
            assert all(settings[key] == 0 for key in UNLIMITED_STATEMENT_SETTINGS)
            assert settings["max_memory_usage"] == 64 * 1024**2
            assert all(
                settings[key] == "throw"
                for key in (
                    "read_overflow_mode",
                    "result_overflow_mode",
                    "timeout_overflow_mode",
                )
            )
            envelopes.append((query, dict(settings)))
            result = transport.execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )
            columns = list(result.data[0]) if result.data else []
            return (
                [tuple(row[key] for key in columns) for row in result.data],
                [(key, "String") for key in columns],
                0.0,
            )

    monkeypatch.setattr(query_service, "get_v2_query_client", lambda: LocalClient())
    service = query_service.V2AnalyticsQueryService()
    assert service.supports_bounded_speculative_reads is False
    assert not is_application_read()
    options = {"read_settings": {"max_memory_usage": 64 * 1024**2, "max_threads": 4}}
    if probe_error == "programming":
        with pytest.raises(RuntimeError, match="compiler defect"):
            run(subject, service, **options)
    else:
        page = run(subject, service, **options)
        assert page.complete and [row["id"] for row in page.rows] == ["old"]
        assert sum(a.kind == "population_time_discovery" for a in page.attempts) == 1
        if probe_error == "budget":
            assert any(a.error_code for a in page.attempts)
    assert not is_application_read()
    probes = [settings for query, settings in envelopes if query == "population_probe"]
    assert len(probes) == 1 and probes[0]["max_threads"] == 1
