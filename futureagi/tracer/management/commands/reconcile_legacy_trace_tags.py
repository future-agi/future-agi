"""Re-mirror PG-only editor tags into the curated CH traces.tags read replica.

The tag grid, picker and filters read legacy tags from CH traces.tags, with a
saved EntityTagAssignment (including an explicit clear) taking precedence.
Keeping this copy in sync preserves that precedence without manufacturing an
assignment for a user who has never edited tags in the new grid.
"""

from __future__ import annotations

import json
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.clickhouse.v2.trace_writer import _TRACE_COLUMNS, _trace_to_row


class Command(BaseCommand):
    help = "Reconcile PG-only trace editor tags with the CH curated trace replica."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--project-id", type=UUID, help="Limit to one project.")
        parser.add_argument(
            "--organization-id", type=UUID, help="Limit to one organization."
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Compare only; do not write."
        )

    def handle(self, *args, **opts):
        from tracer.models.trace import Trace

        size = opts["batch_size"]
        if not 1 <= size <= 1000:
            raise CommandError("--batch-size must be between 1 and 1000")

        import clickhouse_connect

        cfg = get_v2_config()
        client = clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
            send_receive_timeout=120,
        )
        manager = getattr(Trace, "all_objects", Trace.objects)
        qs = manager.all().order_by("id")
        if opts["project_id"]:
            qs = qs.filter(project_id=opts["project_id"])
        if opts["organization_id"]:
            qs = qs.filter(project__organization_id=opts["organization_id"])

        scanned = different = written = 0
        last_id = None
        try:
            while True:
                page = list((qs.filter(id__gt=last_id) if last_id else qs)[:size])
                if not page:
                    break
                last_id = page[-1].id
                scanned += len(page)
                rows = client.query(
                    """SELECT toString(project_id), toString(id),
                        argMax(tuple(tags, _version), _version) AS latest
                    FROM traces
                    WHERE project_id IN %(projects)s AND id IN %(ids)s
                    GROUP BY project_id, id""",
                    parameters={
                        "projects": tuple({str(t.project_id) for t in page}),
                        "ids": tuple(str(t.id) for t in page),
                    },
                ).result_rows
                latest = {
                    (project, trace): (tags, version)
                    for project, trace, (tags, version) in rows
                }
                changes = []
                for trace in page:
                    prior = latest.get((str(trace.project_id), str(trace.id)))
                    pg_tags = trace.tags if trace.tags is not None else []
                    try:
                        equal = prior is not None and json.loads(prior[0]) == pg_tags
                    except (ValueError, TypeError):
                        equal = False
                    if equal:
                        continue
                    different += 1
                    if not opts["dry_run"]:
                        # This is a reconciliation of the *current* PG source of
                        # truth, not a historical backfill: beat the latest CH
                        # version even if a previous writer used a future clock.
                        row = _trace_to_row(trace)
                        row[-1] = max(row[-1], prior[1] + 1 if prior else 0)
                        changes.append(row)
                if changes:
                    client.insert("traces", changes, column_names=list(_TRACE_COLUMNS))
                    written += len(changes)
                self.stdout.write(
                    f"  scanned {scanned} trace(s); differing {different}"
                )
        finally:
            client.close()
        if opts["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"DRY RUN — scanned {scanned} trace(s); would mirror {different} trace(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"scanned {scanned} trace(s); mirrored {written} trace(s)."
                )
            )
