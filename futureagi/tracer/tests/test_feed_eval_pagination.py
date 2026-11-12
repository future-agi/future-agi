"""W10-ERROR-FEED-L03: paginate membership units, not failing eval rows."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)
from tracer.queries import feed

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 6, 23, tzinfo=UTC)


@pytest.fixture
def eval_cluster(observe_project):
    return TraceErrorGroup.objects.create(
        project=observe_project,
        cluster_id="EVAL-PAGINATION",
        error_type="eval",
        issue_group="Failing eval",
        source=ClusterSource.EVAL,
    )


@pytest.fixture(autouse=True)
def trace_row_lookups(monkeypatch):
    # Keep the actual membership queryset/SQL; only external hydration is mocked.
    def resolve(trace_ids, project_id, *, include_unavailable=False):
        return {
            tid: SimpleNamespace(id=tid, created_at=NOW, input=None)
            for tid in trace_ids
        }

    monkeypatch.setattr(feed, "_resolve_member_traces", resolve)
    for name in (
        "_get_trace_totals_batch",
        "_get_trace_scores_batch",
        "_get_root_spans_batch",
        "_get_investigation_reports_batch",
        "_session_judges_batch",
        "_session_rep_trace_map",
    ):
        monkeypatch.setattr(feed, name, Mock(return_value={}))


def _save_members(cluster, units):
    """Persist (trace, session, occurrence) rows with controlled junction times."""
    rows = ErrorClusterTraces.objects.bulk_create(
        [
            ErrorClusterTraces(cluster=cluster, trace_id=tid, trace_session_id=sid)
            for tid, sid, _ in units
        ]
    )
    # auto_now_add assigns insertion times; set occurrence times afterwards.
    for row, (_, _, occurrence_at) in zip(rows, units, strict=True):
        row.created_at = occurrence_at
    ErrorClusterTraces.objects.bulk_update(rows, ["created_at"])


def _page(cluster, limit, offset):
    return feed._fetch_trace_rows(
        cluster.cluster_id, str(cluster.project_id), limit=limit, offset=offset
    )


@pytest.mark.parametrize("source", [ClusterSource.EVAL, ClusterSource.SCANNER])
def test_duplicate_memberships_fill_disjoint_pages_and_reach_oldest_trace(
    eval_cluster, source
):
    eval_cluster.source = source
    eval_cluster.save(update_fields=["source"])
    trace_ids = [UUID(int=i + 1) for i in range(201)]
    # The eval producer keeps one membership per failing result, including
    # repeated results on a trace. Four duplicates exceed the old 3x overfetch.
    _save_members(
        eval_cluster,
        [
            (tid, None, NOW - timedelta(minutes=i))
            for i, tid in enumerate(trace_ids)
            for _ in range(4)
        ],
    )

    first, first_total = _page(eval_cluster, limit=200, offset=0)
    second, second_total = _page(eval_cluster, limit=200, offset=200)
    after_end, after_total = _page(eval_cluster, limit=200, offset=400)

    assert first_total == second_total == after_total == 201
    assert [len(first), len(second)] == [200, 1]
    first_ids = [row.id for row in first]
    second_ids = [row.id for row in second]
    assert not set(first_ids) & set(second_ids)
    assert first_ids + second_ids == [str(tid) for tid in trace_ids]
    assert after_end == []


def test_distinct_units_order_by_latest_occurrence_then_stable_id(eval_cluster):
    first, second, third = [UUID(int=i) for i in (1, 2, 3)]
    _save_members(
        eval_cluster,
        [
            (first, None, NOW - timedelta(minutes=3)),
            (second, None, NOW),
            (third, None, NOW - timedelta(minutes=1)),
            (first, None, NOW),
        ],
    )

    pages = [_page(eval_cluster, limit=1, offset=i) for i in range(3)]

    assert [total for _, total in pages] == [3, 3, 3]
    assert [[row.id for row in rows] for rows, _ in pages] == [
        [str(first)],
        [str(second)],
        [str(third)],
    ]


def test_session_pages_ignore_provenance_only_rows_and_keep_judge_scores(
    eval_cluster, monkeypatch
):
    sessions = [UUID(int=101), UUID(int=102)]
    trace_ids = [str(UUID(int=1)), str(UUID(int=2))]
    # Repeated session evals create provenance-only rows (no target IDs).
    _save_members(
        eval_cluster,
        [(None, None, NOW + timedelta(minutes=1)) for _ in range(8)]
        + [(None, sid, NOW - timedelta(minutes=i)) for i, sid in enumerate(sessions)],
    )
    representatives = Mock(
        return_value={
            str(sid): tid for sid, tid in zip(sessions, trace_ids, strict=True)
        }
    )
    monkeypatch.setattr(feed, "_session_rep_trace_map", representatives)
    monkeypatch.setattr(
        feed,
        "_session_judges_batch",
        Mock(return_value={str(sid): ("failed", 0.25) for sid in sessions}),
    )

    for i, sid in enumerate(sessions):
        rows, total = _page(eval_cluster, limit=1, offset=i)
        assert total == 2
        assert [row.id for row in rows] == [trace_ids[i]]
        assert rows[0].score == 0.25
        representatives.assert_called_with([str(sid)], str(eval_cluster.project_id))

    rows, total = _page(eval_cluster, limit=1, offset=2)
    assert rows == []
    assert total == 2


def test_trace_id_takes_precedence_over_session_for_membership_unit(eval_cluster):
    trace_id, next_trace_id, session_id = [UUID(int=i) for i in (1, 2, 101)]
    _save_members(
        eval_cluster,
        [
            (trace_id, session_id, NOW),
            (trace_id, None, NOW - timedelta(minutes=1)),
            (next_trace_id, None, NOW - timedelta(minutes=2)),
        ],
    )

    first, total = _page(eval_cluster, limit=1, offset=0)
    second, second_total = _page(eval_cluster, limit=1, offset=1)

    assert total == second_total == 2
    assert [row.id for row in first] == [str(trace_id)]
    assert [row.id for row in second] == [str(next_trace_id)]
