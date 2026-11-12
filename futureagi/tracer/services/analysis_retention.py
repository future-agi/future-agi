"""Retire analysis copies with their source data, across all workspaces.

A report's quotations and categories cannot be safely separated from one
expired source, so the whole affected report is retired. Unrelated reports
remain intact. Work is bounded by runs/tasks; source data is never scanned.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from functools import partial
from uuid import UUID

import structlog
from django.db import connection, transaction
from django.db.models import BooleanField, Exists, F, OuterRef, Q
from django.db.models.expressions import RawSQL
from django.utils import timezone

from tfc.routers import primary_required
from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.insights import InsightConfig, InsightRun, InsightUnit
from tracer.models.instant_eval import InstantEvalAnswer, InstantEvalRow, InstantEvalRun

logger = structlog.get_logger(__name__)
BATCH_SIZE = 200
POLICY_UNREAD = object()
EXPIRED = "source_expired"
EXPIRED_MESSAGE = (
    "The report's source data expired or was deleted. Run it again over retained data."
)


class AnalysisSourceExpired(ValueError):
    pass


def source_time(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if timezone.is_naive(value) else value


def oldest_source(*values: datetime | None) -> datetime | None:
    known = [value for value in values if value is not None]
    return min(known) if known else None


def require_retained_source(
    organization_id, started_at: datetime | None, *, cutoff=POLICY_UNREAD
) -> None:
    """Fail closed before storing a copy under a finite Cloud retention policy."""
    from tfc.ee_gates import trace_retention_cutoff

    if cutoff is POLICY_UNREAD:
        cutoff = trace_retention_cutoff(organization_id)
    if cutoff is not None and (started_at is None or started_at < cutoff):
        raise AnalysisSourceExpired(EXPIRED_MESSAGE)


def _legacy_source_time(run) -> datetime | None:
    """Bounded backfill from copied source timestamps, never run.created_at.

    Only an explicit source arrival is valid; event time/first_seen and run
    creation time must not extend or prematurely shorten source retention.
    Unknown legacy provenance cannot establish finite-policy eligibility.
    """
    earliest = None
    found = False
    for row in (
        InsightUnit.objects.filter(run_id=run.id)
        .values("time", "metrics")
        .iterator(chunk_size=BATCH_SIZE)
    ):
        found = True
        stamp = source_time((row["metrics"] or {}).get("source_created_at"))
        if stamp is None:
            return None
        earliest = oldest_source(earliest, stamp)
    return earliest if found else None


@primary_required
def taxonomy_source_time(taxonomy: dict) -> datetime | None:
    source_id = (taxonomy or {}).get("source_run_id")
    if not source_id:
        return None
    origin = InsightRun._base_manager.using("default").filter(id=source_id).first()
    if origin is None or origin.deleted:
        raise AnalysisSourceExpired(EXPIRED_MESSAGE)
    return origin.source_created_at or _legacy_source_time(origin)


def _uuids(values) -> list[str]:
    valid = []
    for value in values:
        try:
            valid.append(str(UUID(str(value))))
        except (ValueError, TypeError, AttributeError):
            continue
    return list(dict.fromkeys(valid))


def source_generation(project_id) -> int:
    """Server-side project deletion epoch; persistence still requires a project."""
    from tracer.models.project import Project

    if not _uuids([project_id]):
        return 0
    return (
        Project._base_manager.using("default")
        .filter(pk=project_id)
        .values_list("analysis_source_generation", flat=True)
        .first()
        or 0
    )


def _session_aliases(session_ids) -> dict[str, str]:
    """Existing bounded remap reader; metadata only, never source span contents."""
    ids = _uuids(session_ids)
    if not ids:
        return {}
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        _, aliases = reader.resolve_ids("trace_session_id_remap", ids)
    return {**{value: value for value in ids}, **aliases}


def equivalent_session_ids(session_ids) -> set[str]:
    aliases = _session_aliases(session_ids)
    return {
        spelling
        for value in _uuids([*aliases, *aliases.values()])
        for spelling in (value, UUID(value).hex)
    }


def deleted_source_ids(
    project_id, trace_ids=(), session_ids=(), *, sessions_resolved=False
) -> tuple[set[str], set[str]]:
    """Check existing deletion markers, not absence of legacy PG telemetry rows."""
    from tracer.models.trace import Trace
    from tracer.models.trace_session import TraceSession, TraceSessionOverlay

    traces, sessions = set(), set()
    if not _uuids([project_id]):
        return traces, sessions
    with connection.cursor() as cursor:
        tables = [
            Trace._meta.db_table,
            TraceSession._meta.db_table,
            TraceSessionOverlay._meta.db_table,
        ]
        cursor.execute(
            "SELECT to_regclass(%s), to_regclass(%s), to_regclass(%s)", tables
        )
        present = {
            name
            for name, value in zip(tables, cursor.fetchone(), strict=True)
            if value is not None
        }
    requested_sessions = _uuids(session_ids)
    aliases = {}
    if (
        requested_sessions
        and not sessions_resolved
        and any(
            model._meta.db_table in present
            and model._base_manager.using("default")
            .filter(project_id=project_id, deleted=True)
            .exists()
            for model in (TraceSession, TraceSessionOverlay)
        )
    ):
        try:
            aliases = _session_aliases(requested_sessions)
        except Exception as exc:
            raise AnalysisSourceExpired(
                "Deleted source identities could not be checked. Please retry."
            ) from exc
    checked_sessions = _uuids([*requested_sessions, *aliases, *aliases.values()])
    for model, field, ids, target in (
        (Trace, "id", _uuids(trace_ids), traces),
        (TraceSession, "id", checked_sessions, sessions),
        (TraceSessionOverlay, "trace_session_id", checked_sessions, sessions),
    ):
        if model._meta.db_table not in present:
            continue
        for start in range(0, len(ids), BATCH_SIZE):
            target.update(
                spelling
                for value in model._base_manager.using("default")
                .filter(
                    project_id=project_id,
                    deleted=True,
                    **{field + "__in": ids[start : start + BATCH_SIZE]},
                )
                .values_list(field, flat=True)
                for spelling in (str(value), UUID(str(value)).hex)
            )
    deleted_groups = {aliases.get(value, value) for value in _uuids(sessions)}
    for value in requested_sessions:
        if aliases.get(value, value) in deleted_groups:
            sessions.update((value, UUID(value).hex))
    return traces, sessions


def retained_spans(project_id, spans):
    spans = list(spans)
    traces, sessions = deleted_source_ids(
        project_id,
        (span.trace_id for span in spans),
        (span.trace_session_id for span in spans),
    )
    return [
        span
        for span in spans
        if str(span.trace_id) not in traces
        and str(span.trace_session_id) not in sessions
    ]


def require_retained_lineage(run, *, result=None) -> None:
    ids = {
        str(value)
        for value in (
            ((run.settings or {}).get("taxonomy") or {}).get("source_run_id"),
            (
                ((run.result if result is None else result) or {}).get("changes") or {}
            ).get("previous_run_id"),
        )
        if value
    }
    if ids and InsightRun._base_manager.using("default").filter(
        id__in=ids, organization_id=run.organization_id, deleted=False
    ).count() != len(ids):
        raise AnalysisSourceExpired(EXPIRED_MESSAGE)


def _forget_evidence(run_id) -> None:
    from tfc.ee_gates import forget_analysis_evidence

    try:
        forget_analysis_evidence(run_id)
    except Exception:
        logger.warning(
            "analysis_retention_evidence_cleanup_failed",
            run_id=str(run_id),
            exc_info=True,
        )


def _retire_insight(run_id, organization_id) -> bool:
    config_id = (
        InsightRun._base_manager.using("default")
        .filter(id=run_id, organization_id=organization_id)
        .values_list("config_id", flat=True)
        .first()
    )
    if config_id is None:
        return False
    # All operations which lock both follow config -> run (including Run again).
    with transaction.atomic():
        config = (
            InsightConfig._base_manager.select_for_update(of=("self",))
            .filter(id=config_id)
            .first()
        )
        if config is None:
            return False
        run = (
            InsightRun._base_manager.select_for_update(of=("self",))
            .filter(id=run_id, organization_id=organization_id)
            .first()
        )
        if run is None:
            return False
        now = timezone.now()
        InsightUnit.objects.filter(run_id=run_id).delete()
        InsightRun._base_manager.using("default").filter(id=run_id).update(
            deleted=True,
            deleted_at=now,
            updated_at=now,
            status="failed",
            stage="expired",
            result={},
            settings={},
            progress={"stage": "expired", "error_code": EXPIRED},
            error=EXPIRED_MESSAGE,
            finished_at=now,
            analysed_count=0,
        )
        origin = (config.taxonomy or {}).get("source_run_id")
        if config.taxonomy and (not origin or str(origin) == str(run_id)):
            config.taxonomy = {}
            config.save(update_fields=["taxonomy", "updated_at"])
        transaction.on_commit(partial(_forget_evidence, str(run_id)))
    return True


@primary_required
def retire_insight_runs(run_ids, *, organization_id) -> int:
    """Retire affected runs and reports that copied their taxonomy/comparison."""
    pending = deque(str(run_id) for run_id in run_ids)
    seen = set()
    retired = 0
    while pending:
        run_id = pending.popleft()
        if run_id in seen:
            continue
        seen.add(run_id)
        # Retire under the config lock BEFORE discovering children: a concurrent
        # classify start either commits before this lock (and is found below),
        # or observes the cleared/retired taxonomy and cannot inherit it.
        retired += int(_retire_insight(run_id, organization_id))
        dependants = (
            InsightRun._base_manager.using("default")
            .filter(organization_id=organization_id, deleted=False)
            .filter(
                Q(settings__taxonomy__source_run_id=run_id)
                | Q(result__changes__previous_run_id=run_id)
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        for dependent_id in dependants.iterator(chunk_size=BATCH_SIZE):
            pending.append(str(dependent_id))
    return retired


@primary_required
def _retire_instant_task(task_id, *, organization_id) -> int:
    with transaction.atomic():
        run = (
            InstantEvalRun.objects.select_for_update(of=("self",))
            .filter(eval_task_id=task_id, organization_id=organization_id)
            .first()
        )
        task = EvalTask._base_manager.filter(
            pk=task_id, project__organization_id=organization_id
        )
        if not task.exists():
            return 0
        now = timezone.now()
        if run is not None:
            InstantEvalRun.objects.filter(pk=run.pk).update(
                cancelled_at=now, answers_version=F("answers_version") + 1
            )
        # Reads resolve the task before consulting a cache. Keeping a tombstone
        # also prevents a retry/resume from materializing the removed data again.
        EvalTask._base_manager.filter(pk=task_id).update(
            deleted=True,
            deleted_at=now,
            updated_at=now,
            status=EvalTaskStatus.DELETED,
        )
        InstantEvalAnswer.objects.filter(task_id=task_id).delete()
        InstantEvalRow.objects.filter(task_id=task_id).delete()
        from tracer.services.project_deletion import _pause_eval_task_workflows

        transaction.on_commit(partial(_pause_eval_task_workflows, [str(task_id)]))
    return 1


@primary_required
def expire_analysis_data(*, organization_id, cutoff: datetime | None) -> dict[str, int]:
    """Use the same source cutoff as trace retention; None preserves unlimited."""
    counts = {"instant_eval_runs": 0, "insight_runs": 0}
    if cutoff is None:
        return counts
    cutoff = source_time(cutoff)
    if cutoff is None:
        raise ValueError("A retention cutoff must be a datetime")
    rows = InstantEvalRow.objects.filter(
        task__project__organization_id=organization_id
    ).filter(Q(source_created_at__lt=cutoff) | Q(source_created_at__isnull=True))
    while True:
        tasks = list(
            rows.order_by("task_id")
            .values_list("task_id", flat=True)
            .distinct()[:BATCH_SIZE]
        )
        if not tasks:
            break
        for task_id in tasks:
            if not _retire_instant_task(task_id, organization_id=organization_id):
                # Legacy rows can precede their InstantEvalRun record. They are
                # still copied source data and must not survive its cutoff.
                InstantEvalAnswer.objects.filter(row__task_id=task_id).delete()
                InstantEvalRow.objects.filter(task_id=task_id).delete()
                EvalTask._base_manager.filter(
                    pk=task_id, project__organization_id=organization_id
                ).update(deleted=True, deleted_at=timezone.now())
            counts["instant_eval_runs"] += 1

    # Backfill legacy provenance in keyset batches without a migration that
    # rewrites the table. Empty pending runs are not materialized copies yet.
    cursor = None
    while True:
        candidates = (
            InsightRun._base_manager.using("default")
            .filter(organization_id=organization_id, source_created_at__isnull=True)
            .annotate(
                has_units=Exists(
                    InsightUnit.objects.filter(run_id=OuterRef("pk")).exclude(
                        content="",
                        excerpt="",
                        summary="",
                        lens_answer="",
                        label="",
                        facets={},
                    )
                )
            )
            .filter(
                Q(has_units=True)
                | ~Q(result={})
                | Q(settings__has_key="taxonomy")
                | Q(progress__has_key="recent")
            )
        )
        if cursor is not None:
            candidates = candidates.filter(pk__gt=cursor)
        batch = list(candidates.order_by("pk")[:BATCH_SIZE])
        if not batch:
            break
        cursor = batch[-1].pk
        for run in batch:
            stamp = _legacy_source_time(run)
            try:
                inherited = taxonomy_source_time(
                    (run.settings or {}).get("taxonomy") or {}
                )
            except AnalysisSourceExpired:
                stamp = None
            else:
                stamp = oldest_source(stamp, inherited) if stamp else None
            if stamp is None or stamp < cutoff:
                counts["insight_runs"] += retire_insight_runs(
                    [run.id], organization_id=organization_id
                )
            else:
                InsightRun._base_manager.using("default").filter(
                    id=run.id, source_created_at__isnull=True
                ).update(source_created_at=stamp)
    while True:
        expired = (
            InsightRun._base_manager.using("default")
            .filter(organization_id=organization_id, source_created_at__lt=cutoff)
            .annotate(
                has_units=Exists(
                    InsightUnit.objects.filter(run_id=OuterRef("pk")).exclude(
                        content="",
                        excerpt="",
                        summary="",
                        lens_answer="",
                        label="",
                        facets={},
                    )
                )
            )
            .filter(
                Q(deleted=False)
                | Q(has_units=True)
                | ~Q(result={})
                | Q(settings__has_key="taxonomy")
                | Q(progress__has_key="recent")
            )
        )
        ids = list(
            expired.order_by("source_created_at", "id").values_list("id", flat=True)[
                :BATCH_SIZE
            ]
        )
        if not ids:
            break
        counts["insight_runs"] += retire_insight_runs(
            ids, organization_id=organization_id
        )
    return counts


@primary_required
def retire_analysis_sources(
    *, project_id, trace_ids=(), session_ids=()
) -> dict[str, int]:
    """Forget copies after an explicitly deleted trace/session, including CH-only sessions.

    New Insights units carry source identities. Legacy user units cannot prove
    which source contributed, so deleting source data in that project retires
    those unproven user reports rather than retaining possible quotations.
    """
    from tracer.models.project import Project

    project = Project._base_manager.only("organization_id").get(id=project_id)
    trace_ids = list(dict.fromkeys(str(value) for value in trace_ids))
    session_ids = list(dict.fromkeys(str(value) for value in session_ids))
    # Source text may carry OTel hex ids while PG/API ids are hyphenated UUIDs.
    trace_ids = list(
        dict.fromkeys(
            [
                *trace_ids,
                *(
                    spelling
                    for value in _uuids(trace_ids)
                    for spelling in (value, UUID(value).hex)
                ),
            ]
        )
    )
    session_ids = list(
        dict.fromkeys(
            [
                *session_ids,
                *(
                    spelling
                    for value in _uuids(session_ids)
                    for spelling in (value, UUID(value).hex)
                ),
            ]
        )
    )
    if not trace_ids and not session_ids:
        return {"instant_eval_runs": 0, "insight_runs": 0}
    unproven_sessions = False
    if session_ids:
        try:
            session_ids = list(set(session_ids) | equivalent_session_ids(session_ids))
        except Exception:
            # The source delete still succeeds during CH outage. Without the
            # remap metadata no copied session lineage in this project is proven.
            unproven_sessions = True
            logger.warning(
                "analysis_delete_session_identity_unavailable",
                project_id=str(project_id),
                exc_info=True,
            )
    # Writers lock project before run and compare the epoch captured before
    # reading. This also fences the first write, when no copied rows exist.
    # Under an outer transaction this lock deliberately remains held until its
    # commit; no Insights path takes a project lock after config/run locks.
    Project._base_manager.using("default").filter(pk=project_id).update(
        analysis_source_generation=F("analysis_source_generation") + 1
    )
    active = InstantEvalRun.objects.filter(
        project_id=project_id,
        cancelled_at__isnull=True,
        eval_task__status__in=[EvalTaskStatus.PENDING, EvalTaskStatus.RUNNING],
    )
    instant_count = 0
    while True:
        task_ids = list(
            active.order_by("pk").values_list("eval_task_id", flat=True)[:BATCH_SIZE]
        )
        if not task_ids:
            break
        for task_id in task_ids:
            instant_count += _retire_instant_task(
                task_id, organization_id=project.organization_id
            )
    copied_sources = (
        Q(unit="traces", row_id__in=trace_ids)
        | Q(trace_id__in=trace_ids)
        | Q(unit="sessions", row_id__in=session_ids)
    )
    if session_ids:
        # Existing legacy rows have no owning-session proof; do not preserve
        # their excerpts merely because the source was ingested only into CH.
        copied_sources |= Q(source_session_deleted=True) | Q(
            source_session_ids__isnull=True
        )
    if unproven_sessions:
        copied_sources = Q()
    rows = (
        InstantEvalRow.objects.filter(task__project_id=project_id)
        .alias(
            source_session_deleted=RawSQL(
                "source_session_ids ?| %s", [session_ids], output_field=BooleanField()
            ),
        )
        .filter(copied_sources)
    )
    while True:
        task_ids = list(
            rows.order_by("task_id")
            .values_list("task_id", flat=True)
            .distinct()[:BATCH_SIZE]
        )
        if not task_ids:
            break
        for task_id in task_ids:
            instant_count += _retire_instant_task(
                task_id, organization_id=project.organization_id
            )
    predicate = (
        Q(run__unit="traces", unit_id__in=trace_ids)
        | Q(run__unit="sessions", unit_id__in=session_ids)
        | Q(session_id__in=session_ids)
        | Q(source_deleted=True)
        | Q(run__unit="users", metrics__source_trace_ids__isnull=True)
    )
    if unproven_sessions:
        predicate = Q()
    units = (
        InsightUnit.objects.filter(run__project_id=project_id)
        .alias(
            source_deleted=RawSQL(
                "(metrics -> 'source_trace_ids') ?| %s OR (metrics -> 'source_session_ids') ?| %s",
                [trace_ids, session_ids],
                output_field=BooleanField(),
            )
        )
        .filter(predicate)
    )
    retired = 0
    while True:
        run_ids = list(
            units.order_by("run_id")
            .values_list("run_id", flat=True)
            .distinct()[:BATCH_SIZE]
        )
        if not run_ids:
            break
        retired += retire_insight_runs(run_ids, organization_id=project.organization_id)
    return {"instant_eval_runs": instant_count, "insight_runs": retired}
