"""Backfill and audit ``Score.tracer_project_id`` for trace/span scores.

``Score.project_id`` belongs to the model-hub project namespace.  The tracer
project therefore has to be recovered from the authoritative ClickHouse
``spans`` table.  Readers intentionally use only the denormalized
``tracer_project_id``; silently skipping a failed ClickHouse read would make
valid annotation data disappear.

Safety contract
---------------

* Pending PostgreSQL rows are processed in finite, keyset-ordered batches.
* Every ClickHouse call is a finite point lookup through the managed native
  read API and has a hard 9.5 second wall (transport, admission and server
  cancellation), never the process-wide socket timeout.
* Matching is limited to tracer projects in the score's organization and,
  when present, workspace.  No value is written when an identity is ambiguous.
* Writes retain the ``tracer_project_id IS NULL`` guard, so interrupted runs
  and concurrent retries are idempotent.
* Strict mode is the default and raises on the first ClickHouse project-read
  failure.  ``--allow-partial`` is an explicit operational escape hatch; a
  failed candidate project makes affected scores unreadable for that run and
  they are never stamped from another candidate.
* A missing identity is called a verified orphan only after every eligible
  project read succeeded and the score is older than the CDC grace period.
  Recent misses, failed reads and project-scoped misses remain unclassified.

The original migration ``0120_backfill_score_tracer_project`` imports this
helper dynamically.  Its default is now strict for fresh installs.  Already
applied installations must run this command, followed by ``--audit-only
--gate``, before enabling readers that require the denormalized column.

Examples::

    python manage.py backfill_score_tracer_project
    python manage.py backfill_score_tracer_project --audit-only --gate
    python manage.py backfill_score_tracer_project --allow-partial
    python manage.py backfill_score_tracer_project --project-id <uuid>
    python manage.py backfill_score_tracer_project --max-scores 5000 \
        --start-after <score-uuid>
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta

import structlog
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

logger = structlog.get_logger(__name__)

CLICKHOUSE_READ_TIMEOUT_MS = 9_500
DEFAULT_CHUNK_SIZE = 250
MAX_CHUNK_SIZE = 2_000
DEFAULT_ORPHAN_GRACE_HOURS = 24.0


class ScoreTracerProjectReadError(RuntimeError):
    """A candidate project's authoritative span lookup did not complete."""

    def __init__(self, project_id: str):
        self.project_id = str(project_id)
        super().__init__(
            f"ClickHouse span lookup failed for tracer project {self.project_id}"
        )


class ScoreTracerProjectInvariantError(RuntimeError):
    """A bounded lookup returned data outside its requested identity set."""


class ScoreTracerProjectReadinessError(RuntimeError):
    """A complete strict backfill ended with non-orphan NULL rows."""

    def __init__(self, result: dict):
        self.result = result
        super().__init__(
            "Score.tracer_project_id strict backfill is incomplete: "
            f"remaining={result['remaining']} "
            f"ambiguous={result['ambiguous']} "
            f"recent_misses={result['recent_misses']} "
            f"unreadable={result['unreadable']}"
        )


@dataclass(frozen=True)
class _ProjectRef:
    project_id: str
    organization_id: str
    workspace_id: str | None


@dataclass(frozen=True)
class _PendingScoreRef:
    score_id: str
    organization_id: str
    workspace_id: str | None
    trace_id: str | None
    span_id: str | None
    created_at: object

    @property
    def identities(self) -> tuple[tuple[str, str], ...]:
        identities: list[tuple[str, str]] = []
        if self.trace_id:
            identities.append(("trace", self.trace_id))
        if self.span_id:
            identities.append(("span", self.span_id))
        return tuple(identities)


@dataclass(frozen=True)
class _ScoreClassification:
    status: str
    project_id: str | None = None


def _pending_scores():
    from model_hub.models.score import Score

    return Score.no_workspace_objects.filter(
        Q(trace_id__isnull=False) | Q(observation_span_id__isnull=False),
        tracer_project_id__isnull=True,
        deleted=False,
    )


def _normalize_chunk_size(chunk_size: int) -> int:
    size = int(chunk_size)
    if not 1 <= size <= MAX_CHUNK_SIZE:
        raise ValueError(f"chunk_size must be between 1 and {MAX_CHUNK_SIZE}")
    return size


def _eligible_project_ids(base, only_project: str | None) -> list:
    """Compatibility helper retained for callers of the original command."""
    from tracer.models.project import Project

    if only_project:
        return [only_project]
    org_ids = list(base.order_by().values_list("organization_id", flat=True).distinct())
    if not org_ids:
        return []
    return list(
        Project.objects.filter(organization_id__in=org_ids, deleted=False).values_list(
            "id", flat=True
        )
    )


def _tag(project_id, field: str, ids: list) -> int:
    """Compatibility helper: stamp matching NULL rows without overwriting."""
    from model_hub.models.score import Score

    return Score.no_workspace_objects.filter(
        tracer_project_id__isnull=True, **{f"{field}__in": ids}
    ).update(tracer_project_id=project_id)


def _tag_score_ids(project_id: str, score_ids: Iterable[str]) -> int:
    """Idempotently stamp an exact, already-audited score-id batch."""
    from model_hub.models.score import Score

    ids = tuple(score_ids)
    if not ids:
        return 0
    return Score.no_workspace_objects.filter(
        id__in=ids,
        tracer_project_id__isnull=True,
    ).update(tracer_project_id=project_id)


def _project_match_query(
    *, trace_ids: tuple[str, ...], span_ids: tuple[str, ...]
) -> tuple[str, dict]:
    """Build one finite latest-state lookup for a single tracer project.

    ``FINAL`` can force a large merge and needs query settings that are not
    accepted by every server-enforced read-only profile.  Candidate-filtered
    ``argMax`` resolves the ReplacingMergeTree state without that hazard.  The
    outer groups cap the result cardinality at the finite input identity set.
    """

    parts: list[str] = []
    params: dict = {}
    if trace_ids:
        params["score_trace_ids"] = trace_ids
        parts.append(
            """
            SELECT 'trace' AS source_kind, toString(trace_id) AS source_id
            FROM (
                SELECT
                    trace_id,
                    id,
                    start_time,
                    argMax(is_deleted, _version) AS latest_is_deleted
                FROM spans
                PREWHERE project_id = toUUID(%(score_project_id)s)
                WHERE trace_id IN %(score_trace_ids)s
                GROUP BY trace_id, id, start_time
            ) AS latest_trace_spans
            WHERE latest_is_deleted = 0
            GROUP BY trace_id
            """
        )
    if span_ids:
        params["score_span_ids"] = span_ids
        parts.append(
            """
            SELECT 'span' AS source_kind, toString(id) AS source_id
            FROM (
                SELECT
                    id,
                    trace_id,
                    start_time,
                    argMax(is_deleted, _version) AS latest_is_deleted
                FROM spans
                PREWHERE project_id = toUUID(%(score_project_id)s)
                WHERE id IN %(score_span_ids)s
                GROUP BY id, trace_id, start_time
            ) AS latest_score_spans
            WHERE latest_is_deleted = 0
            GROUP BY id
            """
        )
    if not parts:
        return "", {}
    params["score_project_id"] = ""  # Filled by the per-project reader.
    return "\nUNION ALL\n".join(parts), params


def _read_project_matches(
    project_id: str,
    *,
    trace_ids: Iterable[str],
    span_ids: Iterable[str],
    client=None,
) -> set[tuple[str, str]]:
    """Return finite ``(kind, id)`` matches for one project within 9.5s."""
    from tracer.services.clickhouse.client import get_clickhouse_client

    normalized_traces = tuple(dict.fromkeys(str(value) for value in trace_ids if value))
    normalized_spans = tuple(dict.fromkeys(str(value) for value in span_ids if value))
    query, params = _project_match_query(
        trace_ids=normalized_traces,
        span_ids=normalized_spans,
    )
    if not query:
        return set()
    params["score_project_id"] = str(project_id)
    expected = {
        *(("trace", value) for value in normalized_traces),
        *(("span", value) for value in normalized_spans),
    }
    ch = client or get_clickhouse_client()
    found: set[tuple[str, str]] = set()
    with ch.execute_read_block_stream(
        query,
        params,
        timeout_ms=CLICKHOUSE_READ_TIMEOUT_MS,
        block_size=min(max(len(expected), 1), DEFAULT_CHUNK_SIZE),
    ) as blocks:
        for block in blocks:
            for raw_kind, raw_source_id in block:
                identity = (str(raw_kind), str(raw_source_id))
                if identity not in expected:
                    raise ScoreTracerProjectInvariantError(
                        "ClickHouse returned an identity outside the requested batch"
                    )
                found.add(identity)
    return found


def _projects_for_pending_scores(
    base, only_project: str | None
) -> tuple[tuple[_ProjectRef, ...], object]:
    """Load the finite PG project catalog used as the tenancy boundary."""
    from tracer.models.project import Project

    if only_project:
        row = (
            Project.objects.filter(id=only_project, deleted=False)
            .order_by()
            .values("id", "organization_id", "workspace_id")
            .first()
        )
        if row is None:
            raise ValueError(f"unknown tracer project: {only_project}")
        base = base.filter(organization_id=row["organization_id"])
        projects = (row,)
    else:
        org_ids = tuple(
            base.order_by().values_list("organization_id", flat=True).distinct()
        )
        projects = tuple(
            Project.objects.filter(organization_id__in=org_ids, deleted=False)
            .order_by("id")
            .values("id", "organization_id", "workspace_id")
        )
    return (
        tuple(
            _ProjectRef(
                project_id=str(row["id"]),
                organization_id=str(row["organization_id"]),
                workspace_id=(
                    str(row["workspace_id"]) if row["workspace_id"] else None
                ),
            )
            for row in projects
        ),
        base,
    )


def _score_ref(row: dict) -> _PendingScoreRef:
    return _PendingScoreRef(
        score_id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        workspace_id=str(row["workspace_id"]) if row["workspace_id"] else None,
        trace_id=str(row["trace_id"]) if row["trace_id"] else None,
        span_id=(
            str(row["observation_span_id"]) if row["observation_span_id"] else None
        ),
        created_at=row["created_at"],
    )


def _candidate_project_ids(
    score: _PendingScoreRef, projects: tuple[_ProjectRef, ...]
) -> tuple[str, ...]:
    return tuple(
        project.project_id
        for project in projects
        if project.organization_id == score.organization_id
        and (
            score.workspace_id is None
            or project.workspace_id is None
            or project.workspace_id == score.workspace_id
        )
    )


def _classify_score(
    score: _PendingScoreRef,
    *,
    candidate_project_ids: tuple[str, ...],
    matches: dict[tuple[str, str], set[str]],
    failed_project_ids: set[str],
    orphan_before,
    full_project_scope: bool,
) -> _ScoreClassification:
    """Classify one score without mutating it (kept pure for audit tests)."""

    if failed_project_ids.intersection(candidate_project_ids):
        return _ScoreClassification("unreadable")
    matched_projects: set[str] = set()
    for identity in score.identities:
        matched_projects.update(matches.get(identity, set()))
    matched_projects.intersection_update(candidate_project_ids)
    if len(matched_projects) == 1:
        return _ScoreClassification("resolvable", matched_projects.pop())
    if len(matched_projects) > 1:
        return _ScoreClassification("ambiguous")
    if not full_project_scope:
        return _ScoreClassification("unscoped_miss")
    if score.created_at > orphan_before:
        return _ScoreClassification("recent_miss")
    return _ScoreClassification("verified_orphan")


def _read_batch_matches(
    scores: tuple[_PendingScoreRef, ...],
    *,
    projects: tuple[_ProjectRef, ...],
    allow_partial: bool,
) -> tuple[
    dict[tuple[str, str], set[str]],
    set[str],
    dict[str, tuple[str, ...]],
]:
    """Read all candidate projects before any score in the batch is written."""

    candidates_by_score = {
        score.score_id: _candidate_project_ids(score, projects) for score in scores
    }
    sources_by_project: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"trace": set(), "span": set()}
    )
    for score in scores:
        for project_id in candidates_by_score[score.score_id]:
            for kind, source_id in score.identities:
                sources_by_project[project_id][kind].add(source_id)

    matches: dict[tuple[str, str], set[str]] = defaultdict(set)
    failed_projects: set[str] = set()
    for project_id in sorted(sources_by_project):
        sources = sources_by_project[project_id]
        try:
            project_matches = _read_project_matches(
                project_id,
                trace_ids=tuple(sorted(sources["trace"])),
                span_ids=tuple(sorted(sources["span"])),
            )
        except Exception as exc:
            logger.exception(
                "backfill_score_tracer_project_read_failed",
                project_id=project_id,
            )
            if not allow_partial:
                raise ScoreTracerProjectReadError(project_id) from exc
            failed_projects.add(project_id)
            continue
        for identity in project_matches:
            matches[identity].add(project_id)
    return matches, failed_projects, candidates_by_score


def _backfill_project(project_id, chunk_size: int) -> int:
    """Compatibility wrapper for the former project-at-a-time implementation."""

    result = backfill_tracer_project_ids(
        chunk_size=chunk_size,
        only_project=str(project_id),
    )
    return int(result["updated"])


def backfill_tracer_project_ids(
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    sleep_s: float = 0.0,
    only_project: str | None = None,
    log: Callable[[str], None] | None = None,
    *,
    allow_partial: bool = False,
    audit_only: bool = False,
    orphan_grace_hours: float = DEFAULT_ORPHAN_GRACE_HOURS,
    max_scores: int | None = None,
    start_after: str | None = None,
) -> dict:
    """Run an idempotent backfill or read-only readiness audit.

    The result preserves the original ``updated``, ``remaining`` and ``total``
    keys and adds the classifications needed by the production gate.  Strict
    mode raises on project reads; partial mode reports them under
    ``failed_projects`` and never writes affected scores.
    """

    emit = log or (lambda _msg: None)
    size = _normalize_chunk_size(chunk_size)
    sleep_s = float(sleep_s)
    if sleep_s < 0:
        raise ValueError("sleep_s must be non-negative")
    orphan_grace_hours = float(orphan_grace_hours)
    if orphan_grace_hours < 0:
        raise ValueError("orphan_grace_hours must be non-negative")
    if max_scores is not None and int(max_scores) <= 0:
        raise ValueError("max_scores must be positive")
    max_scores = int(max_scores) if max_scores is not None else None

    base = _pending_scores()
    projects, base = _projects_for_pending_scores(base, only_project)
    total = base.count()
    emit(f"Scores missing tracer_project_id in scope: {total}")

    orphan_before = timezone.now() - timedelta(hours=orphan_grace_hours)
    cursor = str(start_after) if start_after else None
    scanned = updated = resolvable = 0
    verified_orphans = recent_misses = ambiguous = unreadable = unscoped_misses = 0
    failed_projects: set[str] = set()
    scan_exhausted = True

    while True:
        if max_scores is not None and scanned >= max_scores:
            scan_exhausted = (
                not base.filter(id__gt=cursor).exists() if cursor else False
            )
            break
        page_size = size
        if max_scores is not None:
            page_size = min(page_size, max_scores - scanned)
        page = base.order_by("id")
        if cursor:
            page = page.filter(id__gt=cursor)
        rows = tuple(
            _score_ref(row)
            for row in page.values(
                "id",
                "organization_id",
                "workspace_id",
                "trace_id",
                "observation_span_id",
                "created_at",
            )[:page_size]
        )
        if not rows:
            break

        matches, batch_failures, candidates_by_score = _read_batch_matches(
            rows,
            projects=projects,
            allow_partial=allow_partial,
        )
        failed_projects.update(batch_failures)
        resolved_by_project: dict[str, list[str]] = defaultdict(list)
        for score in rows:
            classification = _classify_score(
                score,
                candidate_project_ids=candidates_by_score[score.score_id],
                matches=matches,
                failed_project_ids=batch_failures,
                orphan_before=orphan_before,
                full_project_scope=only_project is None,
            )
            if classification.status == "resolvable":
                resolvable += 1
                resolved_by_project[str(classification.project_id)].append(
                    score.score_id
                )
            elif classification.status == "verified_orphan":
                verified_orphans += 1
            elif classification.status == "recent_miss":
                recent_misses += 1
            elif classification.status == "ambiguous":
                ambiguous += 1
            elif classification.status == "unreadable":
                unreadable += 1
            else:
                unscoped_misses += 1

        # All candidate-project reads and ambiguity checks complete before the
        # first PG write in a page.  A partial CH result can never be stamped.
        if not audit_only:
            for project_id, score_ids in resolved_by_project.items():
                updated += _tag_score_ids(project_id, score_ids)

        scanned += len(rows)
        cursor = rows[-1].score_id
        emit(
            f"  scanned={scanned} resolvable={resolvable} updated={updated} "
            f"orphans={verified_orphans} unreadable={unreadable}"
        )
        if sleep_s:
            time.sleep(sleep_s)

    remaining = base.count()
    global_scope = only_project is None and start_after is None and max_scores is None
    ready = bool(
        global_scope
        and scan_exhausted
        and not failed_projects
        and ambiguous == 0
        and recent_misses == 0
        and unreadable == 0
        and unscoped_misses == 0
        and remaining == verified_orphans
    )
    result = {
        "total": total,
        "scanned": scanned,
        "resolvable": resolvable,
        "updated": updated,
        "remaining": remaining,
        "verified_orphans": verified_orphans,
        "recent_misses": recent_misses,
        "ambiguous": ambiguous,
        "unreadable": unreadable,
        "unscoped_misses": unscoped_misses,
        "failed_projects": tuple(sorted(failed_projects)),
        "scan_exhausted": scan_exhausted,
        "last_score_id": cursor,
        "ready": ready,
        "audit_only": bool(audit_only),
    }
    emit(
        "Score tracer-project readiness: "
        f"updated={updated} remaining={remaining} "
        f"verified_orphans={verified_orphans} recent_misses={recent_misses} "
        f"ambiguous={ambiguous} unreadable={unreadable} ready={ready}"
    )
    # Migration 0120 ignores the return value.  Enforce completeness here so a
    # fresh migration cannot be recorded as applied after a partial result.
    # Bounded/project-scoped runs are explicitly resumable slices and audits
    # intentionally return their report to the caller/gate instead.
    if not allow_partial and not audit_only and global_scope and not result["ready"]:
        raise ScoreTracerProjectReadinessError(result)
    return result


class Command(BaseCommand):
    help = (
        "Backfill or audit Score.tracer_project_id with bounded strict "
        "ClickHouse reads."
    )

    def add_arguments(self, parser):
        parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
        parser.add_argument("--sleep", type=float, default=0.0)
        parser.add_argument("--project-id", type=str, default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--audit-only", action="store_true")
        parser.add_argument("--gate", action="store_true")
        parser.add_argument("--allow-partial", action="store_true")
        parser.add_argument(
            "--orphan-grace-hours",
            type=float,
            default=DEFAULT_ORPHAN_GRACE_HOURS,
        )
        parser.add_argument("--max-scores", type=int, default=None)
        parser.add_argument("--start-after", type=str, default=None)

    def handle(self, *args, **opts):
        if opts["gate"] and not opts["audit_only"]:
            raise CommandError("--gate is read-only and requires --audit-only")
        if opts["gate"] and (
            opts["allow_partial"]
            or opts["project_id"]
            or opts["max_scores"]
            or opts["start_after"]
        ):
            raise CommandError(
                "--gate requires a strict, complete global scan; remove "
                "--allow-partial/--project-id/--max-scores/--start-after"
            )
        if opts["dry_run"]:
            total = _pending_scores().count()
            self.stdout.write(f"Scores missing tracer_project_id: {total}")
            self.stdout.write("No ClickHouse audit performed; use --audit-only.")
            return

        try:
            result = backfill_tracer_project_ids(
                chunk_size=opts["chunk_size"],
                sleep_s=opts["sleep"],
                only_project=opts["project_id"],
                log=self.stdout.write,
                allow_partial=opts["allow_partial"],
                audit_only=opts["audit_only"],
                orphan_grace_hours=opts["orphan_grace_hours"],
                max_scores=opts["max_scores"],
                start_after=opts["start_after"],
            )
        except (
            ScoreTracerProjectReadError,
            ScoreTracerProjectReadinessError,
            ValueError,
        ) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            f"resume_cursor={result['last_score_id']} "
            f"failed_projects={len(result['failed_projects'])}"
        )
        if opts["gate"] and not result["ready"]:
            raise CommandError(
                "Score.tracer_project_id readiness gate failed: resolvable="
                f"{result['resolvable']} ambiguous={result['ambiguous']} "
                f"recent_misses={result['recent_misses']} "
                f"unreadable={result['unreadable']} remaining={result['remaining']}"
            )
        if not opts["allow_partial"] and (
            result["failed_projects"]
            or result["ambiguous"]
            or result["recent_misses"]
            or result["unreadable"]
        ):
            raise CommandError(
                "strict backfill is incomplete; inspect the readiness summary "
                "or rerun explicitly with --allow-partial"
            )
        self.stdout.write(
            self.style.SUCCESS("Score tracer-project operation complete.")
        )
