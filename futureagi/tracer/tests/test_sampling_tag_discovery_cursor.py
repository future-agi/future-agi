"""Bounded assignment sweeps on primary PG, including late lower-key commits."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.db import connection

from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.tests.test_entity_tag_assignment_postgres import assignment_schema  # noqa: F401
from tracer.tests.test_entity_tag_target import PROJECT
from tracer.tests.test_entity_tag_targets_ch25 import OTHER_PROJECT


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture
def discovery(request, monkeypatch):
    from tracer.services.eval_tasks.sampling import tag_discovery

    request.getfixturevalue("assignment_schema")
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM tracer_entity_tag_assignment")
    monkeypatch.setattr(tag_discovery, "ASSIGNMENT_PAGE_SIZE", 2)
    return tag_discovery


def task(project=PROJECT, column="tag", col_type="SYSTEM_METRIC"):
    return SimpleNamespace(
        project_id=project,
        row_type="spans",
        filters={
            "filters": [{"column_id": column, "filter_config": {"col_type": col_type}}]
        },
    )


def insert(trace, *, project=PROJECT, **kwargs):
    return EntityTagAssignment.objects.using("default").create(
        project_id=project,
        entity_kind="trace",
        trace_id=trace,
        span_id="",
        start_hour=datetime(1970, 1, 1, tzinfo=UTC),
        observation_type="",
        service_name="",
        manual_tags=[],
        **kwargs,
    )


def poll(discovery, cohort, selected_task=None):
    page = discovery.assignment_page(selected_task or task(), {"mode": "live"}, cohort)
    assert page is not None
    cohort.selection_context[discovery.STATE_KEY] = page.cursor
    return page


def test_pages_are_bounded_tenant_scoped_and_eventually_wrap(discovery):
    for name in ("a", "b", "c"):
        insert(name)
    insert("other-tenant", project=OTHER_PROJECT)
    cohort = SimpleNamespace(selection_context={"algorithm": "unchanged"})
    first = poll(discovery, cohort)
    assert first.trace_ids == ("a", "b")
    assert poll(discovery, cohort).trace_ids == ("c",)
    assert poll(discovery, cohort).trace_ids == ("a", "b")
    assert cohort.selection_context["algorithm"] == "unchanged"


def test_new_writes_cannot_extend_active_sweep_forever(discovery):
    for name in ("a", "b", "c"):
        insert(name)
    cohort = SimpleNamespace(selection_context={})
    assert poll(discovery, cohort).trace_ids == ("a", "b")
    insert("d")
    assert poll(discovery, cohort).trace_ids == ("c",)
    insert("e")
    assert poll(discovery, cohort).trace_ids == ("a", "b")
    assert poll(discovery, cohort).trace_ids == ("c", "d")
    assert poll(discovery, cohort).trace_ids == ("e",)


def test_late_commit_below_cursor_and_updates_are_revisited(discovery):
    # A concurrent writer's lower key becomes visible only after the first
    # page has already advanced beyond it; timestamps/commit order do not matter.
    for name in ("a", "b", "c"):
        insert(name)
    cohort = SimpleNamespace(selection_context={})
    first = poll(discovery, cohort)
    assert first.trace_ids == ("a", "b")
    insert("0-late-commit")
    EntityTagAssignment.objects.filter(trace_id="a").update(
        manual_tags=[{"name": "changed-after-page"}], revision=2
    )
    assert poll(discovery, cohort).trace_ids == ("c",)
    assert poll(discovery, cohort).trace_ids == ("0-late-commit", "a")


def test_uncommitted_lower_key_is_seen_on_next_sweep(discovery, request):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import connections, transaction

    for name in ("a", "b", "c"):
        insert(name)
    ready, commit = Event(), Event()
    schema = request.getfixturevalue("assignment_schema")

    def delayed_writer():
        db = connections["default"]
        try:
            with db.cursor() as cursor:
                cursor.execute("SET search_path TO " + db.ops.quote_name(schema))
            with transaction.atomic(using="default"):
                insert("0-late-commit")
                ready.set()
                assert commit.wait(timeout=10)
        finally:
            db.close()

    cohort = SimpleNamespace(selection_context={})
    with ThreadPoolExecutor(max_workers=1) as executor:
        writer = executor.submit(delayed_writer)
        try:
            assert ready.wait(timeout=10)
            assert poll(discovery, cohort).trace_ids == ("a", "b")
        finally:
            commit.set()
        writer.result(timeout=10)
    assert poll(discovery, cohort).trace_ids == ("c",)
    assert poll(discovery, cohort).trace_ids == ("0-late-commit", "a")


def test_unacknowledged_or_interrupted_page_is_retried(discovery):
    insert("a")
    insert("b")
    insert("c")
    cohort = SimpleNamespace(selection_context={})
    first = discovery.assignment_page(task(), {"mode": "live"}, cohort)
    again = discovery.assignment_page(task(), {"mode": "live"}, cohort)
    assert first == again
    assert cohort.selection_context == {}


def test_deleted_tail_does_not_strand_cursor(discovery):
    for name in ("a", "b", "c"):
        insert(name)
    cohort = SimpleNamespace(selection_context={})
    poll(discovery, cohort)
    EntityTagAssignment.objects.filter(trace_id="c").delete()
    assert poll(discovery, cohort).trace_ids == ()
    assert poll(discovery, cohort).trace_ids == ("a", "b")


@pytest.mark.parametrize(
    "column,col_type,mode",
    [
        ("tag", "SPAN_ATTRIBUTE", "live"),
        ("status", "SYSTEM_METRIC", "live"),
        ("tag", "SYSTEM_METRIC", "closed"),
    ],
)
def test_unrelated_policies_do_not_scan_assignments(discovery, column, col_type, mode):
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as queries:
        page = discovery.assignment_page(
            task(column=column, col_type=col_type),
            {"mode": mode},
            SimpleNamespace(selection_context={}),
        )
    assert page is None
    assert len(queries) == 0


def test_assignment_replay_is_candidate_scoped_without_arrival_floor():
    from datetime import timedelta

    from tracer.services.eval_tasks.sampling.policy import validate_policy
    from tracer.services.eval_tasks.sampling.query import SamplingQuery

    now = datetime.now(UTC)
    start = now - timedelta(days=2)
    policy = validate_policy(
        {
            "version": 1,
            "sampling_unit": "trace",
            "mode": "live",
            "window": {"start": start.isoformat(), "end": None},
            "default": {"rate": 100},
        },
        row_type="spans",
        now=now,
    )
    selected = task()
    selected.filters = {}
    sql, params = SamplingQuery(selected, policy, "v", now).live_assignment_rows(
        ["a", "b"]
    )
    assert params["sp_tag_traces"] == ("a", "b")
    assert params["candidate_trace_ids"] == ("a", "b")
    assert params["sp_start"] == start
    assert params["sp_end"] == now
    assert "trace_id IN %(sp_tag_traces)s" in sql
    assert "sp_arrival_floor_ns" not in params
    assert "sp_floor" not in params
    assert "source_arrival < %(sp_snapshot)s" in sql
    assert "s.start_time >= %(sp_start)s" in sql


@pytest.mark.parametrize(
    "payload_bytes,rejected", [(300_000, False), (2_097_152, True)]
)
def test_stream_tag_budget_checks_native_values_and_existing_parser_cap(
    monkeypatch, payload_bytes, rejected
):
    from contextlib import contextmanager

    from tracer.services.clickhouse.v2 import effective_tags, query_service
    from tracer.services.eval_tasks.sampling.errors import SamplingTagFilterTooBroad
    from tracer.services.eval_tasks.sampling.query import READ_SETTINGS, stream_query

    captured = {}
    real_bind = effective_tags.bind_effective_tag_query

    def bind(sql, params, **kwargs):
        captured["bound_timestamp"] = params["timestamp"]
        captured["cap"] = kwargs["max_query_size"]
        return real_bind(sql, params, **kwargs)

    @contextmanager
    def execute(sql, params, **kwargs):
        captured["executed"] = True
        assert kwargs["settings"]["max_query_size"] == captured["cap"]
        yield [[("ok",)]]

    monkeypatch.setattr(effective_tags, "bind_effective_tag_query", bind)
    monkeypatch.setattr(
        query_service,
        "get_v2_query_client",
        lambda: SimpleNamespace(execute_read_block_stream=execute),
    )
    statement = "SELECT %(timestamp)s, %(payload)s, %(sample_assigned_keys)s"
    params = {
        "timestamp": datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        "payload": "x" * payload_bytes,
        "sample_assigned_keys": (("project", "trace"),),
    }
    if rejected:
        with pytest.raises(SamplingTagFilterTooBroad):
            list(stream_query(statement, params))
        assert "executed" not in captured
    else:
        assert list(stream_query(statement, params)) == [[("ok",)]]
    assert captured["bound_timestamp"] == "2026-01-02 03:04:05.123456"
    assert captured["cap"] == READ_SETTINGS["max_query_size"] == 2_097_152
