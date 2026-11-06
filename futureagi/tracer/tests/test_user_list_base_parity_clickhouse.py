# ruff: noqa: F811 -- shared isolated ClickHouse/Postgres fixtures
"""Actual old/new Users entry points on replacement/remap-aware synthetic data.

Static pins below assert fixture assumptions only; BL01-Q live qualification and
M16 performance remain integration gates. No production authority is installed.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from tracer.models.derived_metric import MetricProjectRefresh
from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.derived_metrics.base_list_current_clickhouse import freeze_changes
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.storage import LIST_USER_ACTIVITY
from tracer.tests._base_list_parity import (
    ApplicationQueries,
    filters,
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
from tracer.tests.test_derived_metric_clickhouse import SCHEMA
from tracer.tests.test_user_list_base_statements import (
    old_user_builder,
    user_fixture_pin,
)

START = datetime(2026, 10, 1, 12, tzinfo=UTC)
END = START + timedelta(days=30)
USERS = [UUID(int=value) for value in range(101, 110)]
SESSIONS = [UUID(int=value) for value in range(201, 205)]


def query(client, statement):
    sql, params = statement
    rows, columns = client.execute(
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


@pytest.fixture
def user_facts(current_writer, settings):
    settings.METRICS_ENABLED = 1
    writer = current_writer
    client = writer.store.client
    client.execute(split_statements((SCHEMA / "017_end_users.sql").read_text())[0])
    for index, user in enumerate(USERS):
        client.execute(
            "INSERT INTO end_users (project_id, organization_id, end_user_id, user_id, first_seen, version, is_deleted) VALUES",
            [
                (
                    writer.lease.project_id,
                    writer.org_id,
                    user,
                    "" if index == 6 else f"synthetic-user-{index}",
                    START,
                    START,
                    int(index == 4),
                )
            ],
        )
    # Merge user aliases and session aliases using the real survivor map rule.
    client.execute(
        "INSERT INTO end_user_id_remap (old_id, new_id) VALUES", [(USERS[0], USERS[1])]
    )
    client.execute(
        "INSERT INTO trace_session_id_remap (old_id, new_id) VALUES",
        [(SESSIONS[0], SESSIONS[1])],
    )
    keys = set()

    def row(span, user, at, **payload):
        trace = payload.pop("trace", span)
        session = payload.pop("session", SESSIONS[0])
        source_row(
            writer,
            span=span,
            trace=trace,
            session=session,
            at=at,
            end_user_id=user,
            cost=0.125,
            total_tokens=8,
            prompt_tokens=5,
            completion_tokens=3,
            **payload,
        )
        keys.add((trace, span))

    row(
        "first", USERS[0], START, trace="shared", end_time=START + timedelta(seconds=10)
    )
    row(
        "child",
        USERS[1],
        START + timedelta(seconds=1),
        trace="shared",
        session=SESSIONS[1],
        parent_span_id="first",
        attrs_string={"b": "2", "a": "1"},
    )
    row("late", USERS[1], START + timedelta(days=2), session=SESSIONS[2])
    row("null-a", USERS[2], START + timedelta(days=3), end_time=None)
    row("null-b", USERS[3], START + timedelta(days=3), end_time=None)
    row("deleted-user", USERS[4], START + timedelta(days=4))
    row("empty-label", USERS[6], START + timedelta(days=4))
    row("not-curated", uuid4(), START + timedelta(days=5))
    row("foreign-project", USERS[0], START, project_id=uuid4())
    row("moved", USERS[7], START + timedelta(minutes=1), version=1)
    row("moved", USERS[8], START + timedelta(minutes=2), version=2)
    row("deleted", USERS[7], START + timedelta(minutes=3), version=1)
    row("deleted", USERS[7], START + timedelta(minutes=3), version=2, is_deleted=1)
    row("upper-bound", USERS[7], END)
    # A correction within the physical hour moves a row outside a partial window.
    row("time-corrected", USERS[7], START + timedelta(minutes=5), version=1)
    row("time-corrected", USERS[7], START + timedelta(minutes=55), version=2)
    head = publish_current(writer, sorted(keys), as_of=END + timedelta(days=1))
    return writer, head


def builders(writer, **kwargs):
    options = {
        "organization_id": str(writer.org_id),
        "project_id": str(writer.lease.project_id),
        "filters": filters(START, END),
        "limit": 2,
        "offset": 0,
        **kwargs,
    }
    return old_user_builder()(**options), UserListQueryBuilderV2(**options)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("offset", [0, 2, 4, 100])
def test_old_new_numbered_rows_counts_and_hashes(user_facts, offset):
    writer, _ = user_facts
    old, new = builders(writer, offset=offset)
    with user_fixture_pin(writer, authority) as read:
        new.bind_current_facts(read)
        a = query(writer.store.client, old.build_candidate_page_query())
        b = query(writer.store.client, old.build_candidate_page_query())
        c = query(writer.store.client, new.build_candidate_page_query())
        assert parity_hashes(a) == parity_hashes(b) == parity_hashes(c)
        assert a == c
        assert old.format_rows(a) == new.format_rows(c)


@pytest.mark.django_db(transaction=True)
def test_cursor_walk_through_null_activity_and_uuid_ties(user_facts):
    writer, _ = user_facts
    old, new = builders(writer, limit=2)
    before = None
    all_ids = []
    with user_fixture_pin(writer, authority) as read:
        new.bind_current_facts(read)
        for _ in range(20):
            a = query(
                writer.store.client,
                old.build_candidate_page_query(cursor_mode=True, cursor_before=before),
            )
            b = query(
                writer.store.client,
                new.build_candidate_page_query(cursor_mode=True, cursor_before=before),
            )
            assert a == b
            if not a:
                break
            # One public row plus one sentinel; never publish the sentinel twice.
            row = a[0]
            all_ids.append(row["end_user_id"])
            before = row["last_active"], str(row["end_user_id"])
        else:
            pytest.fail("cursor failed to exhaust")
    assert all_ids[-2:] == [USERS[3], USERS[2]]
    assert len(all_ids) == len(set(all_ids))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "start,end",
    [
        (START, START + timedelta(minutes=30)),
        (START + timedelta(microseconds=1), END),
        (START, END + timedelta(microseconds=1)),
    ],
)
def test_half_open_window_after_replacement_collapse(user_facts, start, end):
    writer, _ = user_facts
    old, new = builders(writer, limit=25, filters=filters(start, end))
    with user_fixture_pin(writer, authority) as read:
        new.bind_current_facts(read)
        assert query(writer.store.client, old.build_candidate_page_query()) == query(
            writer.store.client, new.build_candidate_page_query()
        )


@pytest.mark.django_db(transaction=True)
def test_user_key_moves_retractions_remap_only_and_compaction(user_facts):
    writer, first = user_facts
    source_row(
        writer,
        span="late",
        trace="late",
        session=SESSIONS[2],
        at=START + timedelta(days=2),
        end_user_id=USERS[8],
        version=3,
    )
    second = publish_current(writer, [("late", "late")], as_of=END + timedelta(days=1))
    sql, params = committed_rows_select(
        LIST_USER_ACTIVITY,
        second,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    rows = writer.store.execute(
        f"SELECT canonical_user_id, is_deleted FROM ({sql}) WHERE span_id = 'late' ORDER BY canonical_user_id",
        params,
    )
    assert rows == [(str(USERS[0]), 1), (str(USERS[8]), 0)]
    # Both sides must be written by THIS publication, not a stale live old-user
    # row plus a new-user insertion from another attempt/generation.
    assert writer.store.execute(
        f"SELECT canonical_user_id, is_deleted, generation, attempt_id FROM ({sql}) "
        "WHERE span_id = 'late' ORDER BY canonical_user_id",
        params,
    ) == [
        (str(USERS[0]), 1, second.generation, second.published_by_id),
        (str(USERS[8]), 0, second.generation, second.published_by_id),
    ]
    client = writer.store.client
    client.execute(
        "INSERT INTO end_user_id_remap (old_id,new_id) VALUES", [(USERS[2], USERS[3])]
    )
    third = publish_current(
        writer,
        [("null-a", "null-a"), ("null-b", "null-b")],
        as_of=END + timedelta(days=1),
    )
    assert third.id != second.id != first.id
    remapped_sql, remapped_params = committed_rows_select(
        LIST_USER_ACTIVITY,
        third,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    # A remap-only move uses the same old-key/new-key publication contract.
    # The canonical survivor is the smaller UUID, even when it is the old_id.
    assert writer.store.execute(
        f"SELECT canonical_user_id, is_deleted, generation, attempt_id FROM ({remapped_sql}) "
        "WHERE span_id = 'null-b' ORDER BY canonical_user_id",
        remapped_params,
    ) == [
        (str(USERS[2]), 0, third.generation, third.published_by_id),
        (str(USERS[3]), 1, third.generation, third.published_by_id),
    ]
    # Native and current fold the remap identically; no insert-only user state.
    old, new = builders(writer, limit=25)
    with user_fixture_pin(writer, authority) as read:
        new.bind_current_facts(read)
        assert query(client, old.build_candidate_page_query()) == query(
            client, new.build_candidate_page_query()
        )
    before = query(
        client,
        committed_rows_select(
            LIST_USER_ACTIVITY,
            third,
            org_id=writer.org_id,
            project_id=writer.lease.project_id,
        ),
    )
    compacted = writer.compact()
    after = query(
        client,
        committed_rows_select(
            LIST_USER_ACTIVITY,
            compacted,
            org_id=writer.org_id,
            project_id=writer.lease.project_id,
        ),
    )

    def payload(rows):
        return sorted(
            (
                str(row["canonical_user_id"]),
                row["span_id"],
                row["is_deleted"],
                row["cost"],
            )
            for row in rows
        )

    assert payload(before) == payload(after)


@pytest.mark.django_db(transaction=True)
def test_equal_version_ambiguity_never_publishes_activity(user_facts):
    writer, head = user_facts
    writer.store.client.execute("SYSTEM STOP MERGES spans")
    source_row(
        writer,
        span="tie",
        trace="tie",
        session=SESSIONS[0],
        at=START,
        end_user_id=USERS[0],
        version=5,
    )
    source_row(
        writer,
        span="tie",
        trace="tie",
        session=SESSIONS[0],
        at=START,
        end_user_id=USERS[8],
        version=5,
    )
    # Disable merges only on this test's isolated source, preserving both tie rows.
    # Native argMax can choose either; no fabricated stable tie-break is allowed.
    attempt = writer.begin(ceilings={"as_of": END})
    assert not freeze_changes(writer, attempt, head, keys=[("tie", "tie")], as_of=END)
    assert not attempt.batches.exists()
    assert (
        MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest_id
        == head.id
    )


@pytest.mark.django_db(transaction=True)
def test_real_manager_numbered_and_cursor_parity_with_exact_session_counts(
    user_facts, monkeypatch
):
    from tracer.services import users_list_manager as module

    writer, _ = user_facts
    api = ApplicationQueries(writer.store.client)
    monkeypatch.setattr(module, "V2AnalyticsQueryService", lambda: api)
    kwargs = {
        "organization_id": str(writer.org_id),
        "project_id": str(writer.lease.project_id),
        "allowed_project_ids": [str(writer.lease.project_id)],
        "filters": filters(START, END),
        "requested_columns": ["num_sessions"],
    }
    with monkeypatch.context() as historical:
        historical.setattr(module, "UserListQueryBuilderV2", old_user_builder())
        old_manager = module.UsersListManager(**kwargs)
        a = old_manager.list_payload(page_size=25, current_page=0)
        cursor_a = module.UsersListManager(**kwargs).list_cursor_payload(
            page_size=2, page_wall=False
        )
    with user_fixture_pin(writer, authority) as read:
        current = module.UsersListManager(**kwargs)
        current.bind_current_facts(read)
        api.statements.clear()
        b = current.list_payload(page_size=25, current_page=0)
        assert a == b
        assert any("list_current_user_activity" in sql for sql in api.statements)
        assert any("trace_session_id_remap" in sql for sql in api.statements)
        canonical = next(
            row for row in b["table"] if row["end_user_id"] == str(USERS[0])
        )
        assert canonical["num_sessions"] == 2
        cursor_b = module.UsersListManager(**kwargs)
        cursor_b.bind_current_facts(read)
        result = cursor_b.list_cursor_payload(page_size=2, page_wall=False)
        assert cursor_a == result


@pytest.mark.django_db(transaction=True)
def test_authenticated_users_http_parity_and_exact_failure(
    user_facts, monkeypatch, auth_client
):
    import json

    from tracer.services import users_list_manager as module
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
    from tracer.views import trace as views

    writer, _ = user_facts
    api = ApplicationQueries(writer.store.client)
    monkeypatch.setattr(module, "V2AnalyticsQueryService", lambda: api)
    params = {
        "project_id": str(writer.lease.project_id),
        "filters": json.dumps(filters(START, END)),
        "requested_columns": json.dumps(["num_sessions"]),
        "page_size": 25,
    }
    with monkeypatch.context() as historical:
        historical.setattr(module, "UserListQueryBuilderV2", old_user_builder())
        before = auth_client.get("/tracer/users/", params)
        repeated = auth_client.get("/tracer/users/", params)
    assert before.status_code == repeated.status_code == 200
    assert before.json() == repeated.json()
    with user_fixture_pin(writer, authority) as read:

        def manager(**kwargs):
            result = module.UsersListManager(**kwargs)
            result.bind_current_facts(read)
            return result

        monkeypatch.setattr(views, "UsersListManager", manager)
        # The route's static request-column parser remains unchanged.
        after = auth_client.get("/tracer/users/", params)
        assert after.status_code == 200
        assert after.json() == before.json()
        assert parity_hashes(after.json()) == parity_hashes(before.json())
        execute = api.execute_ch_query

        def timeout_count(sql, params, **kwargs):
            if "count() OVER() AS total_count" in sql:
                raise ReadDeadlineExceeded("synthetic-private-count-timeout")
            return execute(sql, params, **kwargs)

        monkeypatch.setattr(api, "execute_ch_query", timeout_count)
        failed = auth_client.get("/tracer/users/", params)
        assert failed.status_code == 503
        assert b"synthetic-private-count-timeout" not in failed.content
        assert "table" not in failed.json().get("result", {})
