# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Actual pinned old/new builder entry points on adversarial native source rows.

These are local correctness receipts, not M15 or HTTP end-to-end measurements.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.selectors.session_candidate_slice import read_candidate_slice_page
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests._base_list_parity import (
    filters,
    fixture_current_facts,
    old_session_builder,
    parity_hashes,
    source_row,
)
from tracer.tests.test_base_list_current_publication_clickhouse import (  # noqa: F401
    authority,
    current_writer,
    live_writer,
    metric_ch,
    publication_store,
    publish_current,
)

FIXTURE = json.loads(
    (
        Path(__file__).parent / "fixtures/base_list_parity/sessions_old_builder.json"
    ).read_text()
)


@pytest.fixture
def list_facts(current_writer):
    writer = current_writer
    start, end = (
        datetime.fromisoformat(FIXTURE[key]) for key in ("window_start", "window_end")
    )
    keys = set()
    for item in FIXTURE["rows"]:
        row = dict(item)
        at = start + timedelta(seconds=row.pop("offset_seconds"))
        session = FIXTURE["sessions"][row.pop("session_index")]
        if row["span"] == "a-first":
            row.update(
                attrs_string={"key": "value"},
                attrs_number={"quality": 0.5},
                attrs_bool={"ok": 1},
                end_time=None,
            )
        source_row(writer, session=session, at=at, **row)
        keys.add((row["trace"], row["span"]))
    # The same public UUID in another project never contaminates this project.
    source_row(
        writer,
        session=FIXTURE["sessions"][1],
        at=start,
        span="foreign",
        project_id=uuid4(),
    )
    head = publish_current(writer, sorted(keys), as_of=end + timedelta(days=1))
    return writer, head, start, end


def run(writer, query):
    sql, params = query
    rows, columns = writer.store.client.execute(
        sql,
        params,
        with_column_types=True,
        settings={
            "max_threads": 1,
            "max_memory_usage": 512 * 1024 * 1024,
            "max_block_size": 500,
            "max_joined_block_size_rows": 500,
            "use_query_cache": 0,
        },
    )
    return [dict(zip((name for name, _ in columns), row, strict=True)) for row in rows]


def builders(writer, start, end, *, page_number=0, page_size=25):
    kwargs = {
        "project_id": str(writer.lease.project_id),
        "filters": filters(start, end),
        "page_size": page_size,
        "page_number": page_number,
    }
    return old_session_builder()(**kwargs), SessionListQueryBuilderV2(**kwargs)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("page_number", [0, 1, 100])
def test_real_old_new_offset_count_and_hydration_hash_parity(list_facts, page_number):
    writer, head, start, end = list_facts
    old, new = builders(writer, start, end, page_number=page_number, page_size=1)
    with fixture_current_facts(
        writer.store,
        project_id=writer.lease.project_id,
        org_id=writer.org_id,
        authority=authority,
    ) as read:
        assert read.manifest.id == head.id
        new.bind_current_facts(read)
        before, after = (
            run(writer, old.build_candidate_page_query()),
            run(writer, new.build_candidate_page_query()),
        )
        assert before == after
        assert parity_hashes(before) == parity_hashes(after)
        assert run(writer, old.build_candidate_count_query()) == run(
            writer, new.build_candidate_count_query()
        )
        ids = [str(row["session_id"]) for row in before]
        if ids:
            a, b = (
                run(writer, old.build_page_hydration_query(ids)),
                run(writer, new.build_page_hydration_query(ids)),
            )
            # The public flow restores candidate order after unordered GROUP BY.
            a = sorted(a, key=lambda row: ids.index(str(row["session_id"])))
            b = sorted(b, key=lambda row: ids.index(str(row["session_id"])))
            assert a == b
            assert parity_hashes(a) == parity_hashes(b)
            assert old.expand_page_attribute_rows(a) == new.expand_page_attribute_rows(
                b
            )
        if page_number == 0:
            assert parity_hashes(before) == FIXTURE["expected_page_zero_hashes"]
            assert [str(row["session_id"]) for row in after] == [
                FIXTURE["sessions"][i] for i in FIXTURE["expected_session_indices"]
            ]


@pytest.mark.django_db(transaction=True)
def test_exact_cursor_pages_and_no_raw_density_probe_for_current_facts(list_facts):
    writer, _, start, end = list_facts
    old, new = builders(writer, start, end, page_size=1)

    class Analytics:
        statements = 0

        def execute_ch_query(self, sql, params, **kwargs):
            self.statements += 1
            assert not sql.lstrip().startswith("EXPLAIN")
            return SimpleNamespace(data=run(writer, (sql, params)))

    analytics = Analytics()
    with fixture_current_facts(
        writer.store,
        project_id=writer.lease.project_id,
        org_id=writer.org_id,
        authority=authority,
    ) as read:
        new.bind_current_facts(read)
        page = read_candidate_slice_page(
            builder=new,
            analytics=analytics,
            deadline=ReadDeadline.start(30000),
            read_settings=lambda n: {},
            query_timeout_ms=5000,
        )
        expected = run(writer, old.build_candidate_cursor_page_query())
        assert page.rows == expected[:1]
        assert page.has_more is True and page.remaining_count == 2
        assert page.slice_start is None and analytics.statements == 1
        cursor = {
            "before_start_time": page.rows[-1]["session_start"],
            "before_session_id": str(page.rows[-1]["session_id"]),
        }
        second = read_candidate_slice_page(
            builder=new,
            analytics=analytics,
            deadline=ReadDeadline.start(30000),
            read_settings=lambda n: {},
            query_timeout_ms=5000,
            **cursor,
        )
        assert second.rows == run(
            writer, old.build_candidate_cursor_page_query(**cursor)
        )
        assert second.has_more is False and second.remaining_count == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "start_delta,end_delta", [(0, 0), (1, 0), (-172800, 0), (0, 1), (86400, 1)]
)
def test_arbitrary_boundaries_minimum_root_is_request_relative(
    list_facts, start_delta, end_delta
):
    writer, _, start, end = list_facts
    old, new = builders(
        writer,
        start + timedelta(microseconds=start_delta),
        end + timedelta(microseconds=end_delta),
    )
    with fixture_current_facts(
        writer.store,
        project_id=writer.lease.project_id,
        org_id=writer.org_id,
        authority=authority,
    ) as read:
        new.bind_current_facts(read)
        assert run(writer, old.build_candidate_page_query()) == run(
            writer, new.build_candidate_page_query()
        )
        assert run(writer, old.build_candidate_count_query()) == run(
            writer, new.build_candidate_count_query()
        )


@pytest.fixture
def application_facts(current_writer, monkeypatch):
    """Deterministic expected application rows, independent of either builder."""
    from datetime import UTC
    from uuid import UUID

    from tracer.models.trace_session import TraceSessionOverlay
    from tracer.services.clickhouse.v2 import (
        end_user_dict_reader,
        trace_session_dict_reader,
    )
    from tracer.tests._base_list_parity import ApplicationQueries, native_label_schema

    writer = current_writer
    start = datetime(2026, 10, 20, 12, tzinfo=UTC)
    end = start + timedelta(days=1)
    session, alias, other, old_user, new_user, early_user = [
        UUID(int=i) for i in range(101, 107)
    ]
    writer.store.execute(
        "INSERT INTO trace_session_id_remap (old_id, new_id, version) VALUES",
        [(session, alias, start)],
    )
    writer.store.execute(
        "INSERT INTO end_user_id_remap (old_id, new_id, version) VALUES",
        [(old_user, new_user, start)],
    )
    rows = [
        # Different maps must stay associated per root, not independently sorted.
        {
            "span": "root-a",
            "trace": "trace-a",
            "session": session,
            "at": start,
            "input": "",
            "end_user_id": early_user,
            "version": 2,
            "attrs_string": {"region": "east"},
            "attrs_number": {"score": 0.25},
            "attrs_bool": {"valid": 1},
        },
        {
            "span": "llm-a",
            "trace": "trace-a",
            "session": alias,
            "at": start + timedelta(seconds=1),
            "parent_span_id": "root-a",
            "observation_type": "llm",
            "input": "fallback input",
            "cost": 0.25,
            "total_tokens": 5,
            "prompt_tokens": 2,
            "completion_tokens": 3,
        },
        {
            "span": "root-b",
            "trace": "trace-b",
            "session": alias,
            "at": start + timedelta(seconds=10),
            "input": "final input",
            "attrs_string": {"region": "west"},
            "attrs_number": {"score": 0.5},
            "attrs_bool": {"valid": 0},
        },
        # A changing owner/root retracts both old membership and old root facts.
        {
            "span": "moved",
            "trace": "trace-moved",
            "session": session,
            "at": start + timedelta(seconds=20),
            "version": 1,
            "input": "obsolete",
        },
        {
            "span": "moved",
            "trace": "trace-moved",
            "session": other,
            "at": start + timedelta(seconds=20),
            "version": 2,
            "parent_span_id": "parent",
            "input": "child now",
        },
        {
            "span": "root-other",
            "trace": "trace-other",
            "session": other,
            "at": start + timedelta(seconds=30),
            "input": "other input",
            "end_time": None,
        },
        # Latest user is intentionally OUTSIDE the list window and on a child.
        {
            "span": "later-user",
            "trace": "trace-later",
            "session": alias,
            "at": end + timedelta(seconds=1),
            "parent_span_id": "parent",
            "end_user_id": new_user,
        },
    ]
    # A physical replacement changes the in-window root user. The later
    # out-of-window child must still win the whole-retention label lookup.
    source_row(writer, **{**rows[0], "version": 1, "end_user_id": old_user})
    for row in rows:
        source_row(writer, **row)
    head = publish_current(
        writer,
        [(r["trace"], r["span"]) for r in rows],
        as_of=end + timedelta(days=1),
    )
    native_label_schema(
        writer.store.client,
        sessions=[(session, "external session"), (other, "external other")],
        users=[
            (old_user, "latest user", "synthetic", "synthetic-hash"),
            (early_user, "early user", None, "early-hash"),
        ],
    )
    TraceSessionOverlay.objects.create(
        trace_session_id=session,
        project_id=writer.lease.project_id,
        display_name="PG display name",
    )
    analytics = ApplicationQueries(writer.store.client)
    monkeypatch.setattr(trace_session_dict_reader, "_get_client", lambda: analytics)
    monkeypatch.setattr(end_user_dict_reader, "_get_client", lambda: analytics)
    return writer, head, start, end, session, other, analytics


def application_response(
    writer, analytics, start, end, builder_type, monkeypatch, **options
):
    from tracer.tests.test_session_list_bounded_view import _view_and_request
    from tracer.views import trace_session

    view, request = _view_and_request()
    request.organization.id = writer.org_id
    project = SimpleNamespace(id=writer.lease.project_id, session_config=None)
    if builder_type is not None:
        monkeypatch.setattr(trace_session, "SessionListQueryBuilderV2", builder_type)
    else:
        # Production-route regressions must not replace or bind the builder.
        assert trace_session.SessionListQueryBuilderV2 is SessionListQueryBuilderV2
    response = view._list_sessions_clickhouse(
        request,
        writer.lease.project_id,
        project,
        analytics,
        {"filters": filters(start, end), "page_size": 25, "page_number": 0, **options},
        read_deadline=ReadDeadline.start(60000),
    )
    assert response[0] == "ok", response
    return response[1]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("page_number", [0, 100])
def test_complete_application_helpers_and_pg_overlay_parity(
    application_facts, monkeypatch, page_number
):
    writer, _, start, end, session, other, analytics = application_facts
    before = application_response(
        writer,
        analytics,
        start,
        end,
        old_session_builder(),
        monkeypatch,
        page_number=page_number,
    )
    old_statements = list(analytics.statements)
    analytics.statements.clear()
    # This is a read-index integration test, not a production coverage receipt.
    # The source-qualification provider remains a separate release requirement.
    with fixture_current_facts(
        writer.store,
        project_id=writer.lease.project_id,
        org_id=writer.org_id,
        authority=authority,
    ) as read:

        class BoundBuilder(SessionListQueryBuilderV2):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.bind_current_facts(read)

        after = application_response(
            writer,
            analytics,
            start,
            end,
            BoundBuilder,
            monkeypatch,
            page_number=page_number,
        )
    assert before == after
    assert parity_hashes(before) == parity_hashes(after)
    assert after["metadata"]["total_rows"] == 2
    if page_number:
        assert after["table"] == []
        assert any(
            "list_current_root_facts" in sql and "count()" in sql
            for sql in analytics.statements
        )
        return
    # Six REAL CH helpers: page, hydration, name dict, session remap,
    # whole-retention latest user, user-remap+dict. Plus the real PG overlay.
    assert len(old_statements) == len(analytics.statements) == 6
    assert any("list_current_members" in sql for sql in analytics.statements)
    hydrated = next(sql for sql in analytics.statements if "root_page AS" in sql)
    assert "FROM spans" not in hydrated
    assert "candidate_member_identities" not in hydrated
    rows = after["table"]
    assert [r["session_id"] for r in rows] == [str(other), str(session)]
    assert [r["session_name"] for r in rows] == ["external other", "PG display name"]
    assert rows[0]["first_message"] == rows[0]["last_message"] == "other input"
    assert rows[0]["user_id"] is None
    assert rows[1]["first_message"] == "fallback input"
    assert rows[1]["last_message"] == "final input"
    assert rows[1]["total_cost"] == 0.25
    assert rows[1]["total_tokens"] == 5
    assert rows[1]["total_traces_count"] == 2
    assert rows[1]["user_id"] == "latest user"
    assert rows[1]["user_id_type"] == "synthetic"
    assert rows[1]["user_id_hash"] == "synthetic-hash"
    assert rows[1]["region"] == ["east", "west"]
    assert rows[1]["score"] == [0.25, 0.5]
    assert set(rows[1]["valid"]) == {False, True}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("shape", ["ascending", "session-filter", "inside-window"])
def test_complete_application_filter_sort_and_window_contract(
    application_facts, monkeypatch, shape
):
    writer, _, start, end, session, other, analytics = application_facts
    options = {}
    if shape == "ascending":
        options["sort_params"] = [{"column_id": "created_at", "direction": "asc"}]
    elif shape == "session-filter":
        options["filters"] = filters(start, end) + [
            {
                "column_id": "trace_session_id",
                "filter_config": {
                    "col_type": "SYSTEM",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [str(session)],
                },
            }
        ]
    else:
        start += timedelta(seconds=2)
    before = application_response(
        writer, analytics, start, end, old_session_builder(), monkeypatch, **options
    )
    analytics.statements.clear()
    with fixture_current_facts(
        writer.store,
        project_id=writer.lease.project_id,
        org_id=writer.org_id,
        authority=authority,
    ) as read:

        class BoundBuilder(SessionListQueryBuilderV2):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.bind_current_facts(read)

        after = application_response(
            writer, analytics, start, end, BoundBuilder, monkeypatch, **options
        )
    assert before == after
    assert parity_hashes(before) == parity_hashes(after)
    rows = after["table"]
    if shape == "ascending":
        assert [r["session_id"] for r in rows] == [str(session), str(other)]
    elif shape == "session-filter":
        assert [r["session_id"] for r in rows] == [str(session)]
        assert after["metadata"]["total_rows"] == 1
    else:
        assert rows[1]["start_time"] == (start + timedelta(seconds=8)).isoformat()
        assert rows[1]["first_message"] == "final input"
        assert rows[1]["total_cost"] == 0.0
        assert rows[1]["user_id"] == "latest user"
    if shape != "inside-window":
        assert all("list_current_" not in sql for sql in analytics.statements)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize(
    "page_number,cursor_mode", [(0, False), (100, False), (0, True)]
)
def test_production_route_always_uses_native_exact_path(
    application_facts, monkeypatch, stale, page_number, cursor_mode
):
    writer, _, start, end, session, other, analytics = application_facts
    # Neither fresh-looking receipts nor an actually stale index grant authority.
    # The production route uses its real builder, not the test-only BoundBuilder.
    if stale:
        source_row(
            writer,
            session=other,
            trace="trace-other",
            span="root-other",
            at=start + timedelta(seconds=30),
            version=2,
            is_deleted=1,
        )

    def reject_binding(*args, **kwargs):
        raise AssertionError("production must not bind unqualified current facts")

    monkeypatch.setattr(SessionListQueryBuilderV2, "bind_current_facts", reject_binding)
    result = application_response(
        writer,
        analytics,
        start,
        end,
        None,
        monkeypatch,
        page_number=page_number,
        cursor_mode=cursor_mode,
    )
    assert result["metadata"]["total_rows"] == (1 if stale else 2)
    expected = (
        [] if page_number else ([str(session)] if stale else [str(other), str(session)])
    )
    assert [row["session_id"] for row in result["table"]] == expected
    assert all("list_current_" not in sql for sql in analytics.statements)
    assert any("FROM spans" in sql for sql in analytics.statements)
    if page_number:
        assert any("count()" in sql for sql in analytics.statements)
