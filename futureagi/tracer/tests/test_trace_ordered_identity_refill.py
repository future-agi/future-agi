"""Portable subset of native420: real builders, existing local RMT fixtures.

No snapshot imports or callback replacement. New integration tests, not a
relabelling of the already-executed paired native420 evidence.
"""

# ruff: noqa: F811 -- pytest fixture injection
import inspect
from datetime import timedelta

import pytest

from tracer.selectors import trace_filter_reads as selector
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.tests import test_trace_boolean_candidate_seed as b
from tracer.tests import test_trace_numeric_primary_seed as n
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_trace_primary_prefix import trace_engine as trace_engine


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def subject(operation="in", values=(False,), page_size=25):
    return n.subject(
        page_size=page_size,
        leaves=[
            n.number_filter(
                operation,
                list(values),
                key="flag",
                filter_type="text",
                attribute_value_types=["boolean"] * len(values),
            )
        ],
    )


def sizes(transport):
    return [len(call[1]["candidate_trace_ids"]) for call in b.classifiers(transport)]


def populate(run, *, older=300, clear=False):
    recent = n.END - timedelta(minutes=20, microseconds=123456)
    old = n.END - timedelta(hours=2, minutes=20, microseconds=123456)
    b.populate(run, 50, recent, first=301, reject_newest=0 if clear else 30)
    if clear:
        run(
            """INSERT INTO spans
            (project_id, observation_type, service_name, start_time, trace_id, id,
             parent_span_id, attrs_bool, _version)
            SELECT project_id, observation_type, service_name, start_time,
                trace_id, id, parent_span_id, map(), toUInt64(2)
            FROM spans WHERE trace_id > 'w-00320'""",
            {},
        )
    if older:
        b.populate(run, older, old)
    return old


def assert_winners(result):
    assert result.complete
    for row in result.rows:
        assert str(row["project_id"]) == n.PROJECT
        assert row["root_span_id"] == "root"
        assert row["_root_observation_type"] == "span"
        assert row["_root_service_name"] == "service-a"
        assert int(row["_root_version"]) == 1
        assert row["_root_start_hour"] == row["start_time"].replace(
            minute=0,
            second=0,
            microsecond=0,
        )


@pytest.mark.parametrize(
    "operation,values,clear",
    [
        ("in", (False,), False),
        ("in", (False, True), True),
        ("not_in", (True,), False),
    ],
)
def test_partial50_with20_matches_refills30_not200(
    trace_engine, operation, values, clear
):
    run, _ = trace_engine
    populate(run, clear=clear)
    seed_results = []

    def recorded(sql, params):
        rows = run(sql, params)
        if "filter_seed_limit" in params:
            seed_results.append((params["filter_seed_limit"], len(rows)))
        return rows

    builder = subject(operation, values)
    assert builder.supports_filter_empty_seed_root_time_discovery() is (
        operation == "in"
    )
    transport = b.Transport(recorded)
    result = n.page(builder, transport)
    assert_winners(result)
    assert b.ids(result) == b.names(320, 301) + b.names(300, 296)
    assert result.has_more and sizes(transport) == [50, 30]
    # Native420 asserted populated acquisitions, not a fixed empty-gap count.
    # NOT IN intentionally does not use the positive-IN root-discovery route.
    assert [(limit, count) for limit, count in seed_results if count] == [
        (200, 50),
        (30, 30),
    ]
    populated = 0
    for limit, count in seed_results:
        assert limit == (200 if populated == 0 else 30)
        populated += bool(count)
    assert populated == 2
    if operation == "not_in":
        assert not b.probes(transport)
    assert not b.globals_used(transport)
    for _, _, settings in transport.calls:
        assert all(settings[key] == 0 for key in n.UNLIMITED_STATEMENT_SETTINGS)
        assert settings["max_threads"] == 1 and settings["max_memory_usage"] > 0


def test_zero_yield_refill_restores200_without_false_exhaustion(trace_engine):
    run, _ = trace_engine
    at = populate(run, older=0)
    b.populate(run, 300, at, reject_newest=30)
    transport = b.Transport(run)
    result = n.page(subject(), transport)
    assert_winners(result)
    assert b.ids(result) == b.names(320, 301) + b.names(270, 266)
    assert result.has_more and sizes(transport) == [50, 30, 200]
    seeds = b.seeds(transport)
    assert [c[1]["filter_seed_limit"] for c in seeds] == [200, 30, 200]
    assert seeds[2][1]["filter_before_id"] == "w-00271"
    assert seeds[1][1]["filter_slice_start"] == seeds[2][1]["filter_slice_start"]


@pytest.mark.parametrize("failure", ["seed", "classify", "hydrate"])
def test_failed_refill_preserves_checkpoint_and_unpublished_rows(trace_engine, failure):
    run, _ = trace_engine
    populate(run)
    completed_classes = []

    def flaky(sql, params):
        if (
            (failure == "seed" and "filter_seed_limit" in params and completed_classes)
            or (
                failure == "classify"
                and "candidate_trace_ids" in params
                and completed_classes
            )
            or (failure == "hydrate" and "page_hydration_physical_keys" in params)
        ):
            raise ReadDeadlineExceeded(failure)
        rows = run(sql, params)
        if "candidate_trace_ids" in params:
            completed_classes.append(True)
        return rows

    failed_transport = b.Transport(flaky)
    failed = n.page(subject(), failed_transport)
    assert not failed.complete and failed.error_code == "read_budget_exceeded"
    assert b.ids(failed) == ([] if failure == "hydrate" else b.names(320, 301))
    state = b.checkpoint(failed)
    if failure == "hydrate":
        # No prefix preceded this fixture's first match. Rollback restores the
        # original request, represented by NO continuation, as in native420.
        assert state == {} and not failed.has_more
        assert sizes(failed_transport) == [50, 30]
    else:
        assert state
    if failed.rows:
        state |= {
            "cursor_start_time": failed.rows[-1]["start_time"],
            "cursor_order_token": failed.rows[-1]["trace_id"],
        }
    retry = b.Transport(run)
    resumed = n.page(subject(page_size=25 - len(failed.rows)), retry, **state)
    fresh = n.page(subject(), b.Transport(run))
    assert_winners(resumed)
    assert failed.rows + resumed.rows == fresh.rows and resumed.has_more
    assert b.ids(failed) + b.ids(resumed) == b.names(320, 301) + b.names(300, 296)
    assert b.seeds(retry)[0][1]["filter_seed_limit"] == 200
    if failure == "hydrate":
        assert b.seeds(retry)[0][1] == b.seeds(failed_transport)[0][1]
        assert b.classifiers(retry)[0][1]["candidate_trace_ids"] == tuple(
            b.names(350, 301)
        )


@pytest.mark.parametrize("accepted", [20, 50])
def test_partial50_corrected_cutoff_resets_max_and_defers_publication(
    trace_engine, accepted
):
    run, _ = trace_engine
    recent = n.END - timedelta(minutes=20)
    corrected = recent - timedelta(minutes=20)
    b.populate(run, 50, recent, first=301, reject_newest=50 - accepted)
    b.correct(run, corrected, where="1")
    b.populate(run, 100, recent - timedelta(minutes=15))
    uncertain = []

    class Observed(b.Transport):
        def execute_ch_query(self, query, params, **kwargs):
            if "filter_seed_limit" in params and len(b.classifiers(self)) == 1:
                frame = inspect.currentframe()
                try:
                    while (
                        frame
                        and frame.f_code
                        is not selector.read_bounded_filter_page.__code__
                    ):
                        frame = frame.f_back
                    assert frame is not None
                    state = frame.f_locals
                    assert (
                        state["ordered_identity_refill"]
                        and state["seed_proves_result_order"]
                    )
                    assert not state["pending_identity_candidates"]
                    assert (
                        state["candidate_limit"]
                        == state["identity_refill_limit"]
                        == 200
                    )
                    matches = list(state["matched_by_id"].values())
                    assert len(matches) == accepted and not state["page_complete"]
                    assert {row["start_time"] for row in matches} == {corrected}
                    assert min(map(state["result_row_key"], matches)) < (
                        b.plain(recent),
                        "w-00301",
                    )
                    assert params["filter_seed_limit"] == 200
                    assert params["filter_slice_end"] == b.plain(
                        n.END - timedelta(minutes=30)
                    )
                    assert not any(
                        "page_hydration_physical_keys" in c[1] for c in self.calls
                    )
                    uncertain.append(accepted)
                finally:
                    del frame
            if "page_hydration_physical_keys" in params:
                assert sizes(self) == [50, 100]
            return super().execute_ch_query(query, params, **kwargs)

    transport = Observed(run)
    result = n.page(
        subject(),
        transport,
        continuation_slice_start=b.plain(n.END - timedelta(minutes=30)),
        continuation_slice_end=b.plain(n.END),
        carry_continuation_slice_width=True,
    )
    assert_winners(result)
    assert b.ids(result) == b.names(100, 76) and result.has_more
    assert uncertain == [accepted] and sizes(transport) == [50, 100]
    assert b.classifiers(transport)[0][1]["candidate_trace_ids"] == tuple(
        b.names(350, 301)
    )
    assert b.classifiers(transport)[1][1]["candidate_trace_ids"] == tuple(
        b.names(100, 1)
    )
    assert all(c[1]["filter_seed_limit"] == 200 for c in b.seeds(transport))


@pytest.mark.parametrize("page_size", [25, 75])
def test_tied_pages_preserve_unconsumed_suffix_and_large_page_exclusion(
    trace_engine, page_size
):
    run, _ = trace_engine
    populate(run, older=80)
    expected, seen, state = b.names(320, 301) + b.names(80, 1), [], {}
    for _ in range(6):
        transport = b.Transport(run)
        result = n.page(subject(page_size=page_size), transport, **state)
        assert_winners(result)
        assert b.ids(result) == expected[len(seen) : len(seen) + page_size]
        seen += b.ids(result)
        assert result.has_more == (len(seen) < len(expected))
        if page_size == 75:
            assert all(c[1]["filter_seed_limit"] == 200 for c in b.seeds(transport))
        if not result.has_more:
            assert not b.checkpoint(result)
            break
        state = {
            "cursor_start_time": result.rows[-1]["start_time"],
            "cursor_order_token": result.rows[-1]["trace_id"],
        }
    assert seen == expected and len(set(seen)) == 100


@pytest.mark.parametrize(
    "operation,expected", [("in", b.names(320, 301)), ("not_in", [])]
)
def test_short_or_empty_requires_full_history(trace_engine, operation, expected):
    run, _ = trace_engine
    populate(run, older=0, clear=True)
    result = n.page(subject(operation, (False, True)), b.Transport(run))
    assert_winners(result)
    assert (
        b.ids(result) == expected and not result.has_more and not b.checkpoint(result)
    )
    assert min(attempt.slice_start for attempt in result.attempts) == b.plain(
        n.END - timedelta(days=7)
    )
