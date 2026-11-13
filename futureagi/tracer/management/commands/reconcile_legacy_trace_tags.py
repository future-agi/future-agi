"""Copy PG-only editor tags into the curated CH traces.tags read replica.

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

        scanned = different = written = missing = 0
        last_id = None
        try:
            while True:
                page = list((qs.filter(id__gt=last_id) if last_id else qs)[:size])
                if not page:
                    break
                last_id = page[-1].id
                scanned += len(page)
                result = client.query(
                    """SELECT * FROM traces
                    WHERE project_id IN %(projects)s AND id IN %(ids)s
                    ORDER BY _version DESC
                    LIMIT 1 BY project_id, id""",
                    parameters={
                        "projects": tuple({str(t.project_id) for t in page}),
                        "ids": tuple(str(t.id) for t in page),
                    },
                )
                columns = list(result.column_names)
                latest = {}
                for values in result.result_rows:
                    row = dict(zip(columns, values, strict=True))
                    latest[(str(row["project_id"]), str(row["id"]))] = row
                changes = []
                for trace in page:
                    prior = latest.get((str(trace.project_id), str(trace.id)))
                    if prior is None or prior.get("is_deleted") or trace.deleted:
                        # Not a tag difference: no live CH trace row to correct.
                        # Never backfill or resurrect a trace from here.
                        missing += prior is None
                        continue
                    pg_tags = trace.tags if trace.tags is not None else []
                    try:
                        equal = json.loads(prior["tags"]) == pg_tags
                    except (ValueError, TypeError):
                        equal = False
                    if equal:
                        continue
                    different += 1
                    if not opts["dry_run"]:
                        # Only the tags change: every other column keeps the
                        # latest CH value, which can be newer than PG's copy.
                        row = {
                            **prior,
                            "tags": json.dumps(
                                pg_tags, default=str, ensure_ascii=False
                            ),
                            "_version": prior["_version"] + 1,
                        }
                        changes.append([row[column] for column in columns])
                if changes:
                    client.insert("traces", changes, column_names=columns)
                    written += len(changes)
                self.stdout.write(
                    f"  scanned {scanned} trace(s); differing {different}; "
                    f"no CH trace row {missing}"
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
