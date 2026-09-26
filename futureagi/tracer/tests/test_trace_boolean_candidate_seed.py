"""Deferred Boolean acquisition: exact latest roots, lossless cursors and raw gaps.

Only local embedded RMT fixtures execute SQL. Existing numeric and physical
fixture helpers are repository-local; no snapshots, external hooks or QA oracle
choose expected matches.
"""

# ruff: noqa: F811 -- pytest fixture injection
import inspect
from copy import deepcopy
from datetime import timedelta

import pytest

from tracer.selectors import trace_filter_reads as selector
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2 as Actual,
)
from tracer.tests import test_trace_numeric_primary_seed as numeric
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_trace_primary_prefix import trace_engine as trace_engine


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def subject(value=False, *, leaves=None, **kwargs):
    return numeric.subject(
        leaves=[
            numeric.number_filter("equals", value, key="flag", filter_type="boolean")
        ]
        if leaves is None
        else leaves,
        **{"page_size": 25, **kwargs},
    )


def plain(at):
    return at.replace(tzinfo=None)


def seeds(transport):
    return [c for c in transport.calls if "filter_seed_limit" in c[1]]


def classifiers(transport):
    return [c for c in transport.calls if "candidate_trace_ids" in c[1]]


def globals_used(transport):
    return [c for c in seeds(transport) if "matching_scalar_trace_identities" in c[0]]


def ids(result):
    return [r["trace_id"] for r in result.rows]


def names(first, last):
    return [f"w-{i:05}" for i in range(first, last - 1, -1)]


def populate(run, count, at, *, first=1, value=False, reject_newest=0):
    run(
        """INSERT INTO spans
        (project_id, observation_type, service_name, start_time, trace_id, id,
         parent_span_id, attrs_bool, _version)
        SELECT toUUID(%(project)s), 'span', 'service-a',
          fromUnixTimestamp64Micro(%(time)s),
          concat('w-', leftPad(toString(%(first)s+number), 5, '0')), 'root', '',
          map('flag', toUInt8(if(number >= %(count)s-%(reject)s, 1-%(value)s, %(value)s))), 1
        FROM numbers(%(count)s)""",
        {
            "project": numeric.PROJECT,
            "time": int(at.timestamp() * 1_000_000),
            "first": first,
            "count": count,
            "reject": reject_newest,
            "value": int(value),
        },
    )


def correct(run, at, *, where, deleted=0):
    run(
        f"""INSERT INTO spans
        (project_id, observation_type, service_name, start_time, trace_id, id,
         parent_span_id, attrs_bool, is_deleted, _version)
        SELECT project_id, observation_type, service_name,
          fromUnixTimestamp64Micro(%(time)s), trace_id, id,
          parent_span_id, attrs_bool, %(deleted)s, toUInt64(2)
        FROM spans WHERE {where}""",
        {"time": int(at.timestamp() * 1_000_000), "deleted": deleted},
    )


def checkpoint(result):
    return {
        k: getattr(result, k)
        for k in (
            "continuation_slice_start",
            "continuation_slice_end",
            "continuation_before_start_time",
            "continuation_before_id",
        )
        if getattr(result, k) is not None
    }


def us(at):
    return int(at.timestamp() * 1_000_000)


def probes(transport):
    return [call for call in transport.calls if "root_discovery_start_us" in call[1]]


class Transport(numeric.UncappedTransport):
    def __init__(
        self,
        run=None,
        *,
        probe_error=None,
        fail_after_probe=False,
        probe_rows=None,
        clock=None,
        probe_seconds=0,
        **kwargs,
    ):
        super().__init__(self.dispatch, **kwargs)
        self.raw_run, self.probe_error, self.fail_after_probe = (
            run,
            probe_error,
            fail_after_probe,
        )
        self.probe_rows, self.clock, self.probe_seconds = (
            probe_rows,
            clock,
            probe_seconds,
        )
        self.envelopes, self.probe_seen = [], False

    def execute_ch_query(self, query, params, **kwargs):
        self.envelopes.append((query, params, deepcopy(kwargs)))
        return super().execute_ch_query(query, params, **kwargs)

    def dispatch(self, query, params):
        if "root_discovery_start_us" in params:
            self.probe_seen = True
            if self.clock is not None:
                self.clock[0] += self.probe_seconds
            if self.probe_error:
                raise self.probe_error
            if self.probe_rows is not None:
                return self.probe_rows
            if not self.raw_run:
                return [{"newest_raw_root_us": None}]
        elif self.probe_seen and self.fail_after_probe:
            raise ReadDeadlineExceeded("required read after probe failed")
        return self.raw_run(query, params) if self.raw_run else []


def test_probe_follows_empty_seed_and_removes_adjacent_round_trips():
    transport = Transport()
    result = numeric.page(subject(), transport)
    assert result.complete and not result.rows
    assert (
        len(probes(transport)) == 3
    )  # Existing finite attempt allowance, not history cutoff.
    assert "filter_seed_limit" in transport.calls[0][1]
    end = numeric.END - timedelta(hours=1)
    for index, (_, params, _) in enumerate(probes(transport)):
        assert params["root_discovery_end_us"] == us(end - timedelta(days=index))
        assert params["root_discovery_start_us"] == us(end - timedelta(days=index + 1))
    assert not globals_used(transport)
    assert seeds(transport)[1][1]["filter_slice_end"] == plain(end - timedelta(days=3))


@pytest.mark.parametrize("value", [False, True])
@pytest.mark.parametrize("hours", [2, 25, 49])
def test_rmt_hit_replays_whole_hour_after_adjacent_complete_nulls(
    trace_engine, value, hours
):
    run, _ = trace_engine
    at = numeric.END - timedelta(hours=hours, minutes=20, microseconds=123456)
    populate(run, 200, at, value=value)
    transport = Transport(run)
    result = numeric.page(subject(value), transport)
    assert result.complete and result.has_more and ids(result) == names(200, 176)
    assert len(probes(transport)) == (hours - 1) // 24 + 1
    hour = at.replace(minute=0, second=0, microsecond=0)
    replay = seeds(transport)[1][1]
    assert replay["filter_slice_start"] == plain(hour)
    assert replay["filter_slice_end"] == plain(hour + timedelta(hours=1))
    assert replay.get("filter_before_id") is None
    assert not globals_used(transport)
    assert [len(c[1]["candidate_trace_ids"]) for c in classifiers(transport)] == [50]


@pytest.mark.parametrize("hours", [2, 24, 25, 25.5, 26.5])
def test_null_finishes_only_complete_remaining_request(hours):
    filters = deepcopy(subject().filters)
    filters[0]["filter_config"]["filter_value"][0] = numeric.END - timedelta(
        hours=hours
    )
    builder = Actual(project_id=numeric.PROJECT, filters=filters, page_size=25)
    transport = Transport()
    result = numeric.page(builder, transport)
    assert result.complete and not result.rows and not result.has_more
    # Existing admission deliberately leaves <=1h to the ordinary seed.
    seed_finishes = hours == 2 or 25 < hours <= 26
    assert len(seeds(transport)) == (2 if seed_finishes else 1)
    assert len(probes(transport)) == (0 if hours == 2 else 2 if hours > 26 else 1)
    if not seed_finishes:
        assert probes(transport)[-1][1]["root_discovery_start_us"] == us(
            numeric.END - timedelta(hours=hours)
        )
    else:
        assert seeds(transport)[-1][1]["filter_slice_start"] == plain(
            numeric.END - timedelta(hours=hours)
        )


@pytest.mark.parametrize(
    "error",
    [ReadDeadlineExceeded("diagnostic budget"), TimeoutError("transport failure")],
)
def test_failed_probe_retains_boundary_and_disables_for_request(error):
    transport = Transport(probe_error=error, fail_after_probe=True)
    failed = numeric.page(subject(), transport)
    assert not failed.complete and not failed.rows
    assert len(probes(transport)) == 1
    assert [a.kind for a in failed.attempts] == ["seed", "root_time_discovery", "seed"]
    expected_end = plain(numeric.END - timedelta(hours=1))
    assert seeds(transport)[1][1]["filter_slice_end"] == expected_end
    state = checkpoint(failed)
    assert state["continuation_slice_end"] == expected_end
    assert "continuation_before_start_time" not in state
    retry = Transport()
    resumed = numeric.page(subject(), retry, **state)
    assert resumed.complete and not resumed.rows
    # Unkeyed legacy-width cursors may restart with a narrower hour. They must
    # retain the exact proven upper boundary and eventually cover all older time.
    assert retry.calls[0][0] == seeds(transport)[1][0]
    assert retry.calls[0][1]["filter_slice_end"] == expected_end
    assert (
        seeds(transport)[1][1]["filter_slice_start"]
        <= retry.calls[0][1]["filter_slice_start"]
        < expected_end
    )
    assert retry.calls[0][1].get("filter_before_id") is None


def test_failed_probe_never_recurs_after_later_empty_seeds():
    transport = Transport(probe_error=ReadDeadlineExceeded("diagnostic budget"))
    result = numeric.page(subject(), transport)
    assert result.complete and not result.rows
    assert len(probes(transport)) == 1 and len(seeds(transport)) > 3


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{}],
        [{"newest_raw_root_us": None}] * 2,
        [{"newest_raw_root_us": False}],
        [{"newest_raw_root_us": us(numeric.END)}],
    ],
)
def test_partial_or_malformed_discovery_cannot_prove_empty(rows):
    with pytest.raises(ValueError, match="root discovery"):
        numeric.page(subject(), Transport(probe_rows=rows))


@pytest.mark.parametrize("error", [ValueError("bad SQL"), RuntimeError("logic error")])
def test_arbitrary_errors_are_not_timeout_fallback(error):
    with pytest.raises(type(error), match=str(error)):
        numeric.page(subject(), Transport(probe_error=error))


@pytest.mark.parametrize(
    "rows",
    [
        [{"newest_raw_root_us": None}],
        [{"newest_raw_root_us": us(numeric.END - timedelta(hours=2))}],
    ],
)
def test_late_discovery_has_no_checkpoint_authority(monkeypatch, rows):
    clock = [0.0]
    monkeypatch.setattr(selector, "monotonic", lambda: clock[0])
    transport = Transport(clock=clock, probe_seconds=21, probe_rows=rows)
    result = numeric.page(subject(), transport)
    assert (
        not result.complete
        and not result.rows
        and result.error_code == "deadline_exceeded"
    )
    assert len(transport.calls) == 2
    assert checkpoint(result)["continuation_slice_end"] == plain(
        numeric.END - timedelta(hours=1)
    )


def test_uncapped_probe_no_discovery_statement_caps_and_shared_wall(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(selector, "monotonic", lambda: clock[0])
    transport = Transport(clock=clock, probe_seconds=1.1, fail_after_probe=True)
    result = numeric.page(subject(), transport, query_timeout_ms=8000)
    assert not result.complete
    envelopes = [c[2] for c in transport.envelopes if "root_discovery_start_us" in c[1]]
    assert (
        len(envelopes) == 3
    )  # No fresh 1-second optional wall grant or inherited 1-second stop.
    assert all(e["timeout_ms"] > 1000 for e in envelopes)
    ordinary = transport.envelopes[0][2]["settings"]
    assert all(
        e["settings"]["max_bytes_to_read"] == ordinary["max_bytes_to_read"]
        for e in envelopes
    )
    for _, _, cleared in probes(transport):
        assert all(cleared[key] == 0 for key in numeric.UNLIMITED_STATEMENT_SETTINGS)
        assert 0 < cleared["max_memory_usage"] and cleared["max_threads"] == 1
    # Explicit execute call contract is independent of transport normalization.
    source = inspect.getsource(selector.read_bounded_filter_page)
    assert source.count("if population_discovery or deferred_root_discovery") >= 2


@pytest.mark.parametrize("page_size", [25, 75])
def test_rmt_dense_first_seed_no_discovery_and_tied_pagination(trace_engine, page_size):
    run, _ = trace_engine
    at = numeric.END - timedelta(minutes=20, microseconds=123456)
    populate(run, 225, at)
    seen, state = [], {}
    for _ in range(10):
        transport = Transport(run)
        result = numeric.page(subject(page_size=page_size), transport, **state)
        assert result.complete and not probes(transport) and not globals_used(transport)
        seen += ids(result)
        if not result.has_more:
            break
        state = {
            "cursor_start_time": result.rows[-1]["start_time"],
            "cursor_order_token": result.rows[-1]["trace_id"],
        }
    assert seen == names(225, 1)


def test_page75_empty_seed_keeps_baseline_no_new_discovery():
    transport = Transport()
    result = numeric.page(subject(page_size=75), transport)
    assert result.complete and not result.rows and not probes(transport)


@pytest.mark.parametrize("value", [False, True])
def test_rmt_zero50_after_hit_reacquires_remaining150_and_old_child(
    trace_engine, value
):
    run, insert = trace_engine
    at = numeric.END - timedelta(hours=36, minutes=20)
    populate(run, 200, at, value=value, reject_newest=50)
    numeric.insert_root(
        insert, trace="old-child", start_time=numeric.END - timedelta(days=4)
    )
    numeric.insert_child(
        insert, trace="old-child", attrs_number={}, attrs_bool={"flag": int(value)}
    )
    transport = Transport(run)
    result = numeric.page(subject(value), transport)
    assert result.complete and ids(result) == names(150, 126)
    assert len(probes(transport)) == 2 and len(globals_used(transport)) == 1
    hour = at.replace(minute=0, second=0, microsecond=0)
    params = globals_used(transport)[0][1]
    assert params["filter_slice_start"] == plain(numeric.END - timedelta(days=7))
    assert params["filter_slice_end"] == plain(hour + timedelta(hours=1))
    assert params["filter_before_id"] == "w-00151"
    assert [len(c[1]["candidate_trace_ids"]) for c in classifiers(transport)] == [
        50,
        151,
    ]
    assert "old-child" in classifiers(transport)[1][1]["candidate_trace_ids"]


@pytest.mark.parametrize("failure", ["global", "remaining150", "hydrate"])
def test_rmt_hit_then_failed_stage_and_resume_never_skip_roots(trace_engine, failure):
    run, _ = trace_engine
    at = numeric.END - timedelta(hours=2, minutes=20)
    populate(run, 200, at, reject_newest=50 if failure == "global" else 0)
    if failure == "remaining150":
        correct(run, at - timedelta(minutes=10), where="trace_id > 'w-00150'")
    fired = []

    def flaky(sql, params):
        if (
            failure == "global"
            and "matching_scalar_trace_identities" in sql
            or failure == "remaining150"
            and len(params.get("candidate_trace_ids", [])) == 150
            or failure == "hydrate"
            and "page_hydration_physical_keys" in params
        ):
            fired.append(True)
            raise ReadDeadlineExceeded(failure)
        return run(sql, params)

    transport = Transport(flaky)
    result = numeric.page(subject(), transport)
    assert fired and not result.complete and not result.rows
    retry = Transport(run)
    resumed = numeric.page(subject(), retry, **checkpoint(result))
    assert resumed.complete and ids(resumed) == (
        names(200, 176) if failure == "hydrate" else names(150, 126)
    )
    assert not probes(
        retry
    )  # An unfinished hour/tie is replayed first, never rediscovered.


@pytest.mark.parametrize(
    "kind",
    [
        "deleted",
        "root_to_child",
        "cleared",
        "wrong_type",
        "corrected",
        "physical_collision",
    ],
)
def test_rmt_stale_raw_hit_is_not_latest_membership(trace_engine, kind):
    run, insert = trace_engine
    at = numeric.END - timedelta(hours=2, minutes=20)
    populate(run, 30, at)
    numeric.insert_root(
        insert,
        trace="zz-stale",
        start_time=at + timedelta(minutes=10),
        attrs_bool={"flag": 0},
    )
    changes = {
        "deleted": {"is_deleted": 1},
        "root_to_child": {"parent_span_id": "parent"},
        "cleared": {"attrs_bool": {}},
        "wrong_type": {"attrs_bool": {}, "attrs_number": {"flag": 0}},
        "corrected": {"start_time": at - timedelta(minutes=10)},
        "physical_collision": {"service_name": "other", "attrs_bool": {"flag": 1}},
    }[kind]
    numeric.insert_root(
        insert,
        trace="zz-stale",
        start_time=at + timedelta(minutes=10),
        **{"attrs_bool": {"flag": 0}, "_version": 2, **changes},
    ) if kind != "corrected" else numeric.insert_root(
        insert, trace="zz-stale", attrs_bool={"flag": 0}, _version=2, **changes
    )
    transport = Transport(run)
    result = numeric.page(subject(), transport)
    assert result.complete and len(probes(transport)) == 1
    expected = (
        ["zz-stale"] + names(30, 7) if kind == "physical_collision" else names(30, 6)
    )
    assert ids(result) == expected


def test_sql_probe_is_existing_thin_raw_necessary_population():
    builder = subject()
    end = plain(numeric.END - timedelta(hours=1))
    sql, params = builder.build_filter_root_time_discovery_query(
        slice_start=end - timedelta(days=1), slice_end=end
    )
    assert "maxOrNull(toUnixTimestamp64Micro(start_time))" in sql
    assert (
        "parent_span_id IS NULL OR parent_span_id = ''" in sql
        and "is_deleted = 0" in sql
    )
    for forbidden in (
        "FINAL",
        "attrs_",
        "arrayJoin",
        "LIMIT",
        "candidate_trace",
        "argMax",
    ):
        assert forbidden not in sql
    assert params["root_discovery_start_us"] == us(numeric.END - timedelta(hours=25))


@pytest.mark.parametrize("enabled", [False, True])
def test_public_discovery_flag_still_controls_opt_in(enabled):
    builder, transport = subject(), Transport()
    result = selector.read_bounded_filter_page(
        builder=builder,
        analytics=transport,
        filters=builder.filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        bounded_continuation=True,
        include_incomplete_rows=True,
        root_time_discovery=enabled,
        deadline_ms=20000,
        max_query_count=64,
    )
    assert result.complete and not result.rows
    assert bool(probes(transport)) is enabled


def test_rmt_partial_prefix_before_later_gap_does_not_arm_discovery(trace_engine):
    run, _ = trace_engine
    populate(run, 10, numeric.END - timedelta(minutes=20), first=201)
    populate(run, 200, numeric.END - timedelta(hours=10, minutes=20))
    transport = Transport(run)
    result = numeric.page(subject(), transport)
    assert result.complete and ids(result) == names(210, 201) + names(200, 186)
    assert not probes(transport) and not globals_used(transport)


@pytest.mark.parametrize("value", [False, True])
def test_rmt_pending_keyset_and_range_are_replayed_before_discovery(
    trace_engine, value
):
    run, _ = trace_engine
    at = numeric.END - timedelta(hours=36, minutes=20)
    populate(run, 200, at, value=value)
    state = {
        "continuation_slice_start": plain(at.replace(minute=0)),
        "continuation_slice_end": plain(at.replace(minute=0) + timedelta(hours=1)),
        "continuation_before_start_time": plain(at),
        "continuation_before_id": "w-00151",
    }
    transport = Transport(run)
    result = numeric.page(subject(value), transport, **state)
    assert result.complete and ids(result) == names(150, 126)
    assert not probes(transport)
    first = seeds(transport)[0][1]
    assert first["filter_slice_start"] == state["continuation_slice_start"]
    assert first["filter_slice_end"] == state["continuation_slice_end"]
    assert first["filter_before_id"] == state["continuation_before_id"]


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("child_after", [False, True])
def test_rmt_probe_does_not_clip_child_history_or_include_other_project(
    trace_engine, days, child_after
):
    run, insert = trace_engine
    at = numeric.END - timedelta(hours=36, minutes=20)
    numeric.insert_root(insert, trace="target", start_time=at)
    numeric.insert_child(
        insert,
        trace="target",
        attrs_number={},
        attrs_bool={"flag": 0},
        start_time=numeric.END + timedelta(days=3)
        if child_after
        else numeric.END - timedelta(days=400),
    )
    numeric.insert_root(
        insert,
        trace="foreign",
        project_id="22222222-2222-4222-8222-222222222222",
        start_time=numeric.END - timedelta(hours=2),
        attrs_bool={"flag": 0},
    )
    transport = Transport(run)
    result = numeric.page(subject(days=days), transport)
    assert result.complete and ids(result) == ["target"]
    assert len(probes(transport)) == 2
    assert probes(transport)[-1][1]["root_discovery_end_us"] == us(
        numeric.END - timedelta(hours=25)
    )
    assert result.rows[0]["start_time"] == at


@pytest.mark.parametrize("value", [False, True])
def test_boolean_plan_preserves_presence_all_history_and_existing_batch_sizes(value):
    builder = subject(value)
    saved = deepcopy(builder.filters)
    assert builder.filter_candidate_seed_requires_empty_prefix()
    assert not builder.filter_candidate_seed_is_optional()
    assert builder.recommended_filter_initial_classify_batch_size() == 50
    assert builder.recommended_filter_classify_batch_size() == 200
    assert builder.recommended_filter_cursor_seed_batch_size() == 200
    assert builder.recommended_filter_initial_slice_width() == timedelta(hours=1)
    start, end = builder._bounded_request_window
    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=start, slice_end=end, limit=200
    )
    witness, root = sql.split("SELECT trace_id, id AS root_span_id, start_time", 1)
    assert (
        "SELECT DISTINCT trace_id" in witness
        and "matching_scalar_trace_identities" in witness
    )
    assert "mapContains(attrs_bool, %(latest_filter_key_0)s)" in witness
    assert "attrs_bool[%(latest_filter_key_0)s] = %(latest_filter_param_0)s" in witness
    assert params["latest_filter_param_0"] == int(value)
    for forbidden in (
        "start_time",
        "is_deleted",
        "LIMIT",
        "FINAL",
        "project_version_id",
    ):
        assert forbidden not in witness
    assert "ORDER BY start_time DESC, trace_id DESC" in root
    assert params["filter_seed_limit"] == 200 and builder.filters == saved
    if not value:
        plan = builder._partition_trace_filter_plans(builder._bounded_filters())[0][0]
        assert (
            plan.raw_graph_value_witness_predicate is None
        )  # Shared graph guard unchanged.


@pytest.mark.parametrize(
    "kwargs",
    [
        {"project_ids": [numeric.PROJECT, numeric.OTHER_PROJECT]},
        {"project_version_id": "33333333-3333-3333-3333-333333333333"},
        {"bounded_identity_only": True},
        {"bounded_internal_scan": True},
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
            "bounded_global_span_witnesses": True,
            "bounded_include_filter_witnesses": False,
        },
        {"bounded_sampling_salt": "fixture", "bounded_sampling_rate": 10},
        {"search": "name"},
        {"sort_params": [{"column_id": "cost", "sort_order": "asc"}]},
        {"days": 1 / 24},
    ],
)
def test_unsupported_modes_cannot_enable_boolean_transition(kwargs):
    builder = subject(**kwargs)
    assert builder._public_boolean_candidate_seed_plan() is None
    assert not builder.filter_candidate_seed_requires_empty_prefix()
    assert builder.recommended_filter_initial_classify_batch_size() is None


@pytest.mark.parametrize(
    "config,error",
    [
        ({"col_type": None}, "unsupported trace filter"),
        ({"col_type": "SYSTEM_METRIC"}, None),
        ({"filter_op": "not_equals"}, None),
        ({"filter_op": "is_null", "filter_value": None}, None),
        ({"filter_op": "in", "filter_value": [False]}, None),
        ({"filter_value": 0}, "boolean values must be booleans"),
        ({"filter_value": "false"}, "boolean values must be booleans"),
        ({"filter_type": "text"}, "text values must be strings"),
        (
            {"filter_op": "contains", "filter_type": "array", "filter_value": [False]},
            None,
        ),
    ],
)
def test_unsupported_leaf_cannot_enable_boolean_transition(config, error):
    leaf = numeric.number_filter("equals", False, key="flag", filter_type="boolean")
    leaf["filter_config"].update(config)
    builder = subject(leaves=[leaf])
    if error:
        # Existing compiler validation still rejects malformed wire values.
        with pytest.raises(ValueError, match=error):
            builder._uses_scalar_coordinate_replay()
        with pytest.raises(ValueError, match=error):
            builder._public_boolean_candidate_seed_plan()
    else:
        assert builder._public_boolean_candidate_seed_plan() is None


@pytest.mark.parametrize("size", [2, 5, 10])
def test_multiple_booleans_remain_ineligible_without_new_necessary_leaf_proof(size):
    leaves = [
        numeric.number_filter("equals", True, key=f"flag{i}", filter_type="boolean")
        for i in range(size)
    ]
    assert subject(leaves=leaves)._public_boolean_candidate_seed_plan() is None


@pytest.mark.parametrize("value", [False, True])
@pytest.mark.parametrize(
    "latest,expected",
    [
        ({}, True),
        ({"attrs_bool": {}}, False),
        ({"attrs_bool": {}, "attrs_number": {"flag": 0}}, False),
        ({"attrs_bool": {}, "attrs_string": {"flag": "false"}}, False),
        ({"attrs_bool": {}, "attributes_extra": '{"flag":false}'}, False),
        ({"is_deleted": 1}, False),
        ({"_opposite": True}, False),
        ({"attrs_number": {"flag": 0}, "attrs_string": {"flag": "false"}}, True),
    ],
)
def test_rmt_missing_cleared_wrong_type_and_tombstone_are_exact(
    trace_engine, value, latest, expected
):
    run, insert = trace_engine
    numeric.insert_root(insert)
    numeric.insert_child(insert, attrs_number={}, attrs_bool={"flag": int(value)})
    changes = dict(latest)
    if changes.pop("_opposite", False):
        changes["attrs_bool"] = {"flag": int(not value)}
    numeric.insert_child(
        insert,
        **{
            "attrs_number": {},
            "attrs_bool": {"flag": int(value)},
            "_version": 2,
            "start_time": numeric.ROOT_TIME - timedelta(days=400, minutes=10),
            **changes,
        },
    )
    result = numeric.page(subject(value), Transport(run))
    assert result.complete and ids(result) == (["target"] if expected else [])
    assert not result.has_more


@pytest.mark.parametrize("value", [False, True])
@pytest.mark.parametrize(
    "collision",
    [
        {"service_name": "other"},
        {"observation_type": "other"},
        {"start_time": numeric.ROOT_TIME - timedelta(days=400, hours=1)},
        {"project_id": numeric.OTHER_PROJECT},
        {"trace_id": "foreign"},
        {"id": "other"},
    ],
)
def test_rmt_full_six_key_collision_tombstone_does_not_erase_witness(
    trace_engine, value, collision
):
    run, insert = trace_engine
    numeric.insert_root(insert)
    numeric.insert_child(insert, attrs_number={}, attrs_bool={"flag": int(value)})
    numeric.insert_child(
        insert,
        **{
            "attrs_number": {},
            "attrs_bool": {"flag": int(not value)},
            "_version": 99,
            "is_deleted": 1,
            **collision,
        },
    )
    result = numeric.page(subject(value), Transport(run))
    assert result.complete and ids(result) == ["target"]


@pytest.mark.parametrize("value", [False, True])
def test_rmt_tied_opposite_and_missing_cannot_synthesize_boolean(trace_engine, value):
    run, insert = trace_engine
    numeric.insert_root(insert)
    numeric.insert_child(
        insert, attrs_number={}, attrs_bool={"flag": int(not value)}, _version=9
    )
    numeric.insert_child(insert, attrs_number={}, attrs_bool={}, _version=9)
    builder = subject(value)
    sql, params = builder.build_filter_identity_match_query_from_seed_rows(
        [{"trace_id": "target"}]
    )
    assert run(sql, params) == []
    result = numeric.page(builder, Transport(run))
    assert result.complete and not result.rows and not result.has_more


@pytest.mark.parametrize("value", [False, True])
def test_rmt_cursor_filtered_positive50_is_not_zero_evidence(trace_engine, value):
    run, _ = trace_engine
    at = numeric.END - timedelta(minutes=20)
    populate(run, 200, at, value=value)
    correct(run, at + timedelta(seconds=2), where="1")
    transport = Transport(run)
    result = numeric.page(
        subject(value),
        transport,
        cursor_start_time=at + timedelta(seconds=1),
        cursor_order_token="zz",
    )
    assert result.complete and not result.rows and not globals_used(transport)
    assert [len(c[1]["candidate_trace_ids"]) for c in classifiers(transport)] == [
        50,
        150,
    ]


@pytest.mark.parametrize("drift", ["version", "timestamp", "deleted"])
def test_rmt_hydration_winner_drift_rolls_back_unpublished_page(trace_engine, drift):
    run, insert = trace_engine
    at = numeric.END - timedelta(minutes=20)
    populate(run, 40, at)

    def drifting(sql, params):
        if "page_hydration_physical_keys" in params:
            changes = {"attrs_bool": {"flag": 0}, "_version": 2, "start_time": at}
            if drift == "timestamp":
                changes["start_time"] = at - timedelta(minutes=10)
            if drift == "deleted":
                changes["is_deleted"] = 1
            numeric.insert_root(insert, trace="w-00040", **changes)
        return run(sql, params)

    result = numeric.page(subject(), Transport(drifting))
    assert not result.complete and not result.rows and not result.has_more
    assert result.error_code == "classification_drift"
    retry = numeric.page(subject(), Transport(run), **checkpoint(result))
    assert retry.complete and ids(retry) == (
        names(40, 16) if drift == "version" else names(39, 15)
    )
