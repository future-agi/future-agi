from __future__ import annotations

import inspect
from datetime import timedelta

import pytest
from django.core.management.base import CommandError
from django.utils import timezone

from model_hub.management.commands import backfill_score_tracer_project as subject


class _Blocks:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return iter((self.rows,))

    def __exit__(self, *_args):
        return None


class _ManagedClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute_read_block_stream(
        self, query, params, *, timeout_ms=None, block_size=None
    ):
        self.calls.append(
            {
                "query": query,
                "params": params,
                "timeout_ms": timeout_ms,
                "block_size": block_size,
            }
        )
        return _Blocks(self.rows)


def _score(*, created_at=None):
    return subject._PendingScoreRef(
        score_id="score-1",
        organization_id="org-1",
        workspace_id="workspace-1",
        trace_id="trace-1",
        span_id="span-1",
        created_at=created_at or timezone.now() - timedelta(days=3),
    )


def test_project_lookup_is_candidate_bounded_latest_state_and_managed():
    client = _ManagedClient((("trace", "trace-1"), ("span", "span-1")))

    matches = subject._read_project_matches(
        "00000000-0000-0000-0000-000000000001",
        trace_ids=("trace-1",),
        span_ids=("span-1",),
        client=client,
    )

    assert matches == {("trace", "trace-1"), ("span", "span-1")}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["timeout_ms"] == 9_500
    assert call["timeout_ms"] < 10_000
    assert call["block_size"] <= subject.DEFAULT_CHUNK_SIZE
    assert call["params"]["score_trace_ids"] == ("trace-1",)
    assert call["params"]["score_span_ids"] == ("span-1",)
    compact_sql = " ".join(call["query"].split())
    assert compact_sql.startswith("SELECT")
    assert compact_sql.count("PREWHERE project_id = toUUID(") == 2
    assert "trace_id IN %(score_trace_ids)s" in compact_sql
    assert "id IN %(score_span_ids)s" in compact_sql
    assert compact_sql.count("argMax(is_deleted, _version)") == 2
    assert "FINAL" not in compact_sql


def test_project_lookup_rejects_rows_outside_finite_request():
    client = _ManagedClient((("trace", "not-requested"),))

    with pytest.raises(subject.ScoreTracerProjectInvariantError):
        subject._read_project_matches(
            "00000000-0000-0000-0000-000000000001",
            trace_ids=("trace-1",),
            span_ids=(),
            client=client,
        )


def test_score_classification_distinguishes_resolvable_ambiguous_and_orphan():
    score = _score()
    cutoff = timezone.now() - timedelta(days=1)
    candidates = ("project-1", "project-2")

    resolved = subject._classify_score(
        score,
        candidate_project_ids=candidates,
        matches={
            ("trace", "trace-1"): {"project-1"},
            ("span", "span-1"): {"project-1"},
        },
        failed_project_ids=set(),
        orphan_before=cutoff,
        full_project_scope=True,
    )
    assert resolved == subject._ScoreClassification("resolvable", "project-1")

    ambiguous = subject._classify_score(
        score,
        candidate_project_ids=candidates,
        matches={
            ("trace", "trace-1"): {"project-1"},
            ("span", "span-1"): {"project-2"},
        },
        failed_project_ids=set(),
        orphan_before=cutoff,
        full_project_scope=True,
    )
    assert ambiguous.status == "ambiguous"

    orphan = subject._classify_score(
        score,
        candidate_project_ids=candidates,
        matches={},
        failed_project_ids=set(),
        orphan_before=cutoff,
        full_project_scope=True,
    )
    assert orphan.status == "verified_orphan"


def test_recent_project_scoped_and_failed_misses_are_never_called_orphans():
    cutoff = timezone.now() - timedelta(days=1)
    recent = _score(created_at=timezone.now())
    old = _score()
    kwargs = {
        "candidate_project_ids": ("project-1", "project-2"),
        "matches": {},
        "orphan_before": cutoff,
    }

    assert (
        subject._classify_score(
            recent,
            failed_project_ids=set(),
            full_project_scope=True,
            **kwargs,
        ).status
        == "recent_miss"
    )
    assert (
        subject._classify_score(
            old,
            failed_project_ids=set(),
            full_project_scope=False,
            **kwargs,
        ).status
        == "unscoped_miss"
    )
    assert (
        subject._classify_score(
            old,
            failed_project_ids={"project-2"},
            full_project_scope=True,
            **kwargs,
        ).status
        == "unreadable"
    )


def test_candidate_projects_are_tenant_scoped():
    projects = (
        subject._ProjectRef("same", "org-1", "workspace-1"),
        subject._ProjectRef("legacy", "org-1", None),
        subject._ProjectRef("other-workspace", "org-1", "workspace-2"),
        subject._ProjectRef("other-org", "org-2", "workspace-1"),
    )

    assert subject._candidate_project_ids(_score(), projects) == ("same", "legacy")


def test_strict_project_failure_raises_before_partial_match_can_be_used(monkeypatch):
    score = _score()
    projects = (
        subject._ProjectRef("project-1", "org-1", "workspace-1"),
        subject._ProjectRef("project-2", "org-1", "workspace-1"),
    )

    def read(project_id, **_kwargs):
        if project_id == "project-2":
            raise TimeoutError("bounded read expired")
        return {("trace", "trace-1")}

    monkeypatch.setattr(subject, "_read_project_matches", read)

    with pytest.raises(subject.ScoreTracerProjectReadError) as exc_info:
        subject._read_batch_matches(
            (score,),
            projects=projects,
            allow_partial=False,
        )
    assert exc_info.value.project_id == "project-2"


def test_allow_partial_marks_whole_candidate_scope_unreadable(monkeypatch):
    score = _score()
    projects = (
        subject._ProjectRef("project-1", "org-1", "workspace-1"),
        subject._ProjectRef("project-2", "org-1", "workspace-1"),
    )

    def read(project_id, **_kwargs):
        if project_id == "project-2":
            raise TimeoutError("bounded read expired")
        return {("trace", "trace-1")}

    monkeypatch.setattr(subject, "_read_project_matches", read)
    matches, failures, candidates = subject._read_batch_matches(
        (score,),
        projects=projects,
        allow_partial=True,
    )

    assert failures == {"project-2"}
    assert matches[("trace", "trace-1")] == {"project-1"}
    classification = subject._classify_score(
        score,
        candidate_project_ids=candidates[score.score_id],
        matches=matches,
        failed_project_ids=failures,
        orphan_before=timezone.now() - timedelta(days=1),
        full_project_scope=True,
    )
    assert classification.status == "unreadable"


def test_gate_rejects_partial_scope_before_running():
    command = subject.Command()
    with pytest.raises(CommandError, match="complete global scan"):
        command.handle(
            gate=True,
            audit_only=True,
            allow_partial=True,
            project_id=None,
            max_scores=None,
            start_after=None,
            dry_run=False,
        )


def test_gate_requires_explicit_read_only_audit_mode():
    command = subject.Command()
    with pytest.raises(CommandError, match="requires --audit-only"):
        command.handle(
            gate=True,
            audit_only=False,
            allow_partial=False,
            project_id=None,
            max_scores=None,
            start_after=None,
            dry_run=False,
        )


def test_gate_fails_when_a_resolvable_null_score_remains(monkeypatch):
    result = {
        "ready": False,
        "resolvable": 1,
        "ambiguous": 0,
        "recent_misses": 0,
        "unreadable": 0,
        "remaining": 1,
        "last_score_id": "score-1",
        "failed_projects": (),
    }
    monkeypatch.setattr(subject, "backfill_tracer_project_ids", lambda **_kw: result)

    with pytest.raises(CommandError, match="readiness gate failed"):
        subject.Command().handle(
            gate=True,
            audit_only=True,
            allow_partial=False,
            project_id=None,
            max_scores=None,
            start_after=None,
            dry_run=False,
            chunk_size=250,
            sleep=0.0,
            orphan_grace_hours=24.0,
        )


def test_original_migration_now_inherits_strict_default_without_rewrite():
    signature = inspect.signature(subject.backfill_tracer_project_ids)
    assert signature.parameters["allow_partial"].default is False
    assert subject.CLICKHOUSE_READ_TIMEOUT_MS <= 10_000
