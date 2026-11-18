"""Backfill ``QueueItem.project`` for pre-0116 rows, resolving from ClickHouse.

Migration 0116 added the denormalized ``project`` FK (schema only). New writes
stamp it, but rows created before it are NULL and still take the unscoped
multi-tenant ``spans`` wide scan the denormalization removes — the exact reads
that timed the queue detail page out. This runs the backfill on ``migrate`` so
self-hosted / OSS deploys get it with no manual step.

Tracer kinds only: ``trace`` → root-span project (``root_ids_by_trace_ids``),
``observation_span`` → span project (``scope_by_ids``), ``trace_session`` →
session project (``resolve_session_fields``). All three are the LEAN id→project
readers, so a backfill over fat voice spans cannot OOM the shared cluster
(code 241). ``dataset_row`` is never CH-read on the render path (skipped);
``call_execution`` has no owning project (left NULL).

Fail-open on ClickHouse: a CH outage during ``migrate`` yields no resolutions
for the affected batch (rows keep the correct-but-unpruned fallback read) and
must never block the schema deploy. Fail-CLOSED on a wrong stamp: a project is
written only when it maps to a ``Project`` in the item's own org — a wrong
project would make the scoped read *miss* the source and render ``deleted``,
worse than leaving it unscoped.

Idempotent (``project__isnull=True``) + keyset-paginated + ``atomic = False`` so
each batch commits independently and a re-run resumes past what is already done.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)

_BATCH = 500
# Raw QueueItemSourceType values — pinned here so the migration stays
# self-contained (never re-derived from the evolving enum).
_TRACER_KINDS = ("trace", "observation_span", "trace_session")


def _resolve_projects_via_ch(span_items, trace_items, session_items):
    """Return ``{item.id: project_id (str)}`` resolved from ClickHouse.

    ``*_items`` map a soft source id to the list of QueueItems carrying it.
    Fail-open: a CH error resolves nothing for the affected kind (its rows stay
    NULL and are picked up by a later run) rather than aborting the migration.
    """
    resolved = {}
    if span_items or trace_items:
        try:
            from tracer.services.clickhouse.v2 import get_reader

            with get_reader() as reader:
                if span_items:
                    for span_id, scope in reader.scope_by_ids(
                        list(span_items)
                    ).items():
                        if scope.project_id:
                            for item in span_items.get(str(span_id), []):
                                resolved[item.id] = scope.project_id
                if trace_items:
                    for trace_id, (_root, project_id) in reader.root_ids_by_trace_ids(
                        list(trace_items)
                    ).items():
                        if project_id:
                            for item in trace_items.get(str(trace_id), []):
                                resolved[item.id] = project_id
        except Exception:
            logger.warning(
                "queueitem_project_backfill: ClickHouse span/trace resolve failed",
                exc_info=True,
            )
    if session_items:
        try:
            from tracer.services.clickhouse.v2.trace_session_dict_reader import (
                resolve_session_fields,
            )

            for session_id, fields in resolve_session_fields(
                list(session_items)
            ).items():
                project_id = fields.get("project_id")
                if project_id:
                    for item in session_items.get(str(session_id), []):
                        resolved[item.id] = str(project_id)
        except Exception:
            logger.warning(
                "queueitem_project_backfill: ClickHouse session resolve failed",
                exc_info=True,
            )
    return resolved


def _process_batch(QueueItem, Project, batch, stats):  # noqa: N803
    span_items, trace_items, session_items = {}, {}, {}
    for item in batch:
        source_type = item.source_type
        if source_type == "observation_span" and item.observation_span_id:
            span_items.setdefault(str(item.observation_span_id), []).append(item)
        elif source_type == "trace" and item.trace_id:
            trace_items.setdefault(str(item.trace_id), []).append(item)
        elif source_type == "trace_session" and item.trace_session_id:
            session_items.setdefault(str(item.trace_session_id), []).append(item)

    resolved = _resolve_projects_via_ch(span_items, trace_items, session_items)

    # Org guard: only stamp a project that belongs to the item's own org, so a
    # bad resolve can never point a scoped read at the wrong tenant's granules.
    resolved_pids = set(resolved.values())
    project_org = (
        {
            str(pid): org_id
            for pid, org_id in Project.objects.filter(
                id__in=resolved_pids
            ).values_list("id", "organization_id")
        }
        if resolved_pids
        else {}
    )

    to_update = []
    for item in batch:
        project_id = resolved.get(item.id)
        if not project_id:
            stats["unresolvable"] += 1
            continue
        if project_org.get(str(project_id)) != item.organization_id:
            stats["org_mismatch"] += 1
            continue
        item.project_id = project_id
        to_update.append(item)

    if to_update:
        QueueItem.objects.bulk_update(to_update, ["project"], batch_size=len(to_update))
    stats["stamped"] += len(to_update)


def backfill_queueitem_project(apps, schema_editor):
    QueueItem = apps.get_model("model_hub", "QueueItem")  # noqa: N806
    Project = apps.get_model("tracer", "Project")  # noqa: N806

    base_qs = QueueItem.objects.filter(
        project__isnull=True, source_type__in=_TRACER_KINDS
    ).only(
        "id",
        "source_type",
        "organization",
        "project",
        "trace",
        "observation_span",
        "trace_session",
    )

    stats = {"stamped": 0, "unresolvable": 0, "org_mismatch": 0}

    # Keyset pagination by pk: each batch is a fresh query and writes commit
    # between queries (atomic = False), so nothing writes into an open cursor.
    # Unresolvable / mismatch rows stay NULL, and id__gt steps past them.
    last_id = None
    while True:
        chunk_qs = base_qs.order_by("id")
        if last_id is not None:
            chunk_qs = chunk_qs.filter(id__gt=last_id)
        batch = list(chunk_qs[:_BATCH])
        if not batch:
            break
        last_id = batch[-1].id
        _process_batch(QueueItem, Project, batch, stats)

    if any(stats.values()):
        logger.info(
            "queueitem_project_backfill done: stamped=%s unresolvable=%s "
            "org_mismatch=%s",
            stats["stamped"],
            stats["unresolvable"],
            stats["org_mismatch"],
        )


class Migration(migrations.Migration):
    # Outside a transaction so each bulk_update batch commits independently — a
    # large backfill must not hold one long write lock, and a re-run resumes
    # from what already committed.
    atomic = False

    dependencies = [
        ("model_hub", "0116_queueitem_project"),
    ]

    operations = [
        migrations.RunPython(
            backfill_queueitem_project,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
