"""User pages seek through versioned selectors, rolling up only the page's users."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tracer.models.instant_eval import InstantEvalAnswer, InstantEvalRow
from tracer.services import instant_eval_summary as summary
from tracer.tests.test_instant_eval_scale import _question
from tracer.tests.test_instant_eval_users_rollup import _users_run

START = datetime(2026, 10, 6, tzinfo=UTC)


@pytest.fixture
def paging_run(observe_project, eval_template, request):
    config = _question(observe_project, eval_template, "Did the agent resolve it?")
    task, run = _users_run(observe_project, [config], [])
    # 100k sessions, including tied and missing times and confidences. Each
    # user's latest time and mean confidence must decide their position.
    users = getattr(request, "param", 203)
    rows = InstantEvalRow.objects.bulk_create(
        [
            InstantEvalRow(
                task_id=task.id,
                row_id=str(uuid.UUID(int=1 + n)),
                unit="sessions",
                end_user_id=uuid.UUID(int=1 + n // 10),
                user_label=f"user {n // 10}",
                time=(
                    START
                    + timedelta(
                        minutes=(n // 10) % 7,
                        seconds=n % 10,
                        microseconds=(n // 10) % 3,
                    )
                    if (n // 10) % 5
                    else None
                ),
                turns=1,
                excerpt=f"session {n}",
            )
            for n in range(users * 10)
        ],
        batch_size=5000,
    )
    InstantEvalAnswer.objects.bulk_create(
        [
            InstantEvalAnswer(
                task_id=task.id,
                row_id=row.id,
                config_id=config.id,
                label="No" if (n // 10) % 2 else "Yes",
                confidence=[None, 0.2, 0.5, 0.9][(n // 10) % 4],
            )
            for n, row in enumerate(rows)
        ],
        batch_size=5000,
    )
    # Autovacuum cannot analyze rows inserted inside this test's transaction.
    # Production's narrow tables are analyzed after large answer batches too.
    with connection.cursor() as cur:
        cur.execute("ANALYZE tracer_instant_eval_row")
        cur.execute("ANALYZE tracer_instant_eval_answer")
    view = summary.RunView(
        task_id=str(task.id),
        row_type="sessions",
        unit="users",
        config_ids=[str(config.id)],
        tz=ZoneInfo("UTC"),
        rows_version=run.answers_version,
    )
    return view, users


@pytest.mark.django_db
@pytest.mark.parametrize("paging_run", [10_003], indirect=True)
def test_user_deep_pages_are_complete_keysets_without_repeated_full_rollups(paging_run):
    view, users = paging_run
    cache.clear()
    for sort in ("time", "newest", "confidence"):
        expected = summary._rows(
            f"WITH {summary._user_rollup(view)} SELECT r.end_user_id::text FROM ur r "
            "ORDER BY "
            + {
                "time": "r.time ASC NULLS FIRST, r.end_user_id",
                "newest": "r.time DESC NULLS LAST, r.end_user_id",
                "confidence": "COALESCE(r.c0, 2) ASC, r.end_user_id",
            }[sort],
            summary._user_params(view, {"task": view.task_id}),
        )
        seen, cursor = [], None
        with CaptureQueriesContext(connection) as queries:
            while True:
                page = summary.rows_page(
                    view,
                    summary.RowFilter(),
                    cursor=cursor,
                    page_size=1499,
                    sort=sort,
                )
                assert page["total"] == users
                seen.extend(row["id"] for row in page["rows"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
                state = summary._cursor_decode(cursor)
                assert "o" not in state
                assert state["k"][-1] == seen[-1]
        assert seen == [row[0] for row in expected]
        assert len(set(seen)) == users
        statements = [q["sql"] for q in queries]
        assert all(
            " OFFSET " not in sql and "count(*) OVER ()" not in sql
            for sql in statements
        )
        full_rollups = [
            sql
            for sql in statements
            if "ur_s AS (" in sql and "r.end_user_id = ANY(" not in sql
        ]
        assert len(full_rollups) == 1
        page_rollups = [
            sql
            for sql in statements
            if "ur_s AS (" in sql and "r.end_user_id = ANY(" in sql
        ]
        assert len(page_rollups) == 7


@pytest.mark.django_db
def test_user_selectors_and_total_follow_filters_answers_and_cancellation(paging_run):
    view, users = paging_run
    cache.clear()

    def page(f=summary.RowFilter()):
        return summary.rows_page(view, f, cursor=None, page_size=3, sort="confidence")

    yes = summary.RowFilter(answers=[(view.config_ids[0], "Yes")])
    filtered = page(yes)
    assert filtered["total"] == (users + 1) // 2
    assert all(
        row["answers"][view.config_ids[0]]["answer"] == "Yes"
        for row in filtered["rows"]
    )
    assert page().get("total") == users
    # An answer update changes both the filter membership and its confidence
    # key. A new version must not reuse an earlier selector or total.
    first = filtered["rows"][0]["id"]
    changed = InstantEvalRow.objects.filter(task_id=view.task_id, end_user_id=first)
    InstantEvalAnswer.objects.filter(row__in=changed).update(label="No", confidence=0.1)
    view.rows_version += 1
    assert page(yes)["total"] == filtered["total"] - 1
    assert page()["rows"][0]["id"] == first
    # Cancellation changes pending users' rollup even without another answer.
    row = changed.first()
    InstantEvalAnswer.objects.filter(row_id=row.id).delete()
    view.rows_version += 1
    assert first not in [r["id"] for r in page()["rows"]]
    view.cancelled = True
    assert page()["rows"][0]["id"] == first


@pytest.mark.django_db
def test_user_confidence_pages_sort_by_the_selected_question(
    paging_run, observe_project, eval_template
):
    view, users = paging_run
    config = _question(observe_project, eval_template, "Was the customer satisfied?")
    InstantEvalAnswer.objects.bulk_create(
        [
            InstantEvalAnswer(
                task_id=view.task_id,
                row_id=row.id,
                config_id=config.id,
                label="Yes",
                confidence=(int(row.end_user_id) % 7) / 10,
            )
            for row in InstantEvalRow.objects.filter(task_id=view.task_id)
        ]
    )
    view.config_ids.append(str(config.id))
    cache.clear()
    for selected in (str(config.id), None):
        seen, cursor = [], None
        while True:
            page = summary.rows_page(
                view,
                summary.RowFilter(),
                cursor=cursor,
                page_size=37,
                sort="confidence",
                sort_config=selected,
            )
            seen.extend(page["rows"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len({row["id"] for row in seen}) == users
        cfg = selected or view.config_ids[0]
        order = [
            (
                2
                if row["answers"][cfg]["confidence"] is None
                else row["answers"][cfg]["confidence"],
                row["id"],
            )
            for row in seen
        ]
        assert order == sorted(order)


def test_empty_user_pages_do_not_build_offsets_or_window_counts(monkeypatch):
    cache.clear()
    view = summary.RunView(
        str(uuid.uuid4()), "sessions", "users", [str(uuid.uuid4())], ZoneInfo("UTC")
    )
    seen = []
    monkeypatch.setattr(summary, "_rows", lambda sql, params: seen.append(sql) or [])
    assert summary.rows_page(
        view, summary.RowFilter(), cursor=None, page_size=1500, sort="confidence"
    ) == {"rows": [], "next_cursor": None, "total": 0}
    assert all("OFFSET" not in sql and "count(*) OVER ()" not in sql for sql in seen)


def test_user_page_sql_has_neither_offsets_nor_window_counts(monkeypatch):
    view = summary.RunView(
        "task", "sessions", "users", [str(uuid.UUID(int=1))], ZoneInfo("UTC")
    )
    seen = []

    def read(sql, params):
        seen.append(sql)
        return []

    cache.clear()
    monkeypatch.setattr(summary, "_rows", read)
    result = summary.rows_page(
        view, summary.RowFilter(), cursor=None, page_size=1500, sort="time"
    )
    assert result == {"rows": [], "next_cursor": None, "total": 0}
    assert len(seen) == 1
    assert "OFFSET" not in seen[0]
    assert "count(*) OVER ()" not in seen[0]


def test_user_selector_cache_reads_only_chunks_after_the_keyset(monkeypatch):
    cache.clear()
    view = summary.RunView("task", "sessions", "users", [], ZoneInfo("UTC"))
    keys = [(0, n // 3, str(uuid.UUID(int=n + 1))) for n in range(100_003)]
    calls = []
    monkeypatch.setattr(summary, "_rows", lambda sql, params: calls.append(sql) or keys)
    params = {"task": "task"}
    first, total = summary._user_page_keys(view, "true", params, "time", {}, 1499)
    assert first == keys[:1500]
    assert total == len(keys)
    # Seek deep into a 100k-user selector; neither a full database rollup nor
    # a full cached selector needs to be read again.
    reads = []
    get = cache.get
    monkeypatch.setattr(
        cache, "get", lambda key, *a, **kw: reads.append(key) or get(key, *a, **kw)
    )
    deep, total = summary._user_page_keys(
        view, "true", params, "time", {"k": list(keys[90_000])}, 1499
    )
    assert deep == keys[90_001:91_501]
    assert total == len(keys)
    assert len(calls) == 1
    assert len(reads) <= 4
    # Evict a selector block. A cache miss rebuilds the same keyset rather
    # than silently truncating a page or changing its total.
    block = next(key for key in reads if ":chunk:" in key)
    cache.delete(block)
    again, total = summary._user_page_keys(
        view, "true", params, "time", {"k": list(keys[90_000])}, 1499
    )
    assert again == deep
    assert total == len(keys)
    assert len(calls) == 2
