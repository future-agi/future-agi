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

from tfc.routers import force_primary
from tracer.services.clickhouse.v2 import get_v2_config


def _matches_tags(raw_tags, pg_tags):
    try:
        return json.loads(raw_tags) == pg_tags
    except (ValueError, TypeError):
        return False


class Command(BaseCommand):
    help = "Reconcile PG-only trace editor tags with the CH curated trace replica."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--after-id", type=UUID, help="Resume after this trace ID.")
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

        scanned = different = written = missing = changed = 0
        last_id = opts["after_id"]
        try:
            while True:
                with force_primary():
                    page = list(
                        (qs.filter(id__gt=last_id) if last_id else qs).values_list(
                            "id", "project_id", "deleted"
                        )[:size]
                    )
                if not page:
                    break
                last_id = page[-1][0]
                scanned += len(page)
                params = {
                    "projects": tuple({str(project_id) for _, project_id, _ in page}),
                    "ids": tuple(str(trace_id) for trace_id, _, _ in page),
                }
                # Read the narrow CH state before PG tags: a new editor write
                # between these reads must not be undone with stale PG values.
                states = client.query(
                    """SELECT project_id, id,
                    argMax(tuple(tags, is_deleted), _version) AS state,
                    max(_version) AS version
                    FROM traces
                    WHERE project_id IN %(projects)s AND id IN %(ids)s
                    GROUP BY project_id, id""",
                    parameters=params,
                )
                ch_states = {
                    (str(project_id), str(trace_id)): (state, version)
                    for project_id, trace_id, state, version in states.result_rows
                }
                live_ids = []
                for trace_id, project_id, deleted in page:
                    state = ch_states.get((str(project_id), str(trace_id)))
                    if state is None:
                        missing += 1
                    elif not deleted and not state[0][1]:
                        live_ids.append(trace_id)
                # PG tags are fetched only for IDs with a live CH candidate.
                with force_primary():
                    pg_tags_by_id = {
                        str(trace_id): tags if tags is not None else []
                        for trace_id, tags in qs.filter(
                            id__in=live_ids, deleted=False
                        ).values_list("id", "tags")
                    }
                candidates = {
                    (str(project_id), str(trace_id)): pg_tags_by_id[str(trace_id)]
                    for trace_id, project_id, _ in page
                    if str(trace_id) in pg_tags_by_id
                    and not _matches_tags(
                        ch_states[(str(project_id), str(trace_id))][0][0],
                        pg_tags_by_id[str(trace_id)],
                    )
                }
                if candidates:
                    result = client.query(
                        """SELECT * FROM traces
                        WHERE project_id IN %(projects)s AND id IN %(ids)s
                        ORDER BY _version DESC
                        LIMIT 1 BY project_id, id""",
                        parameters={
                            "projects": tuple({project for project, _ in candidates}),
                            "ids": tuple(trace_id for _, trace_id in candidates),
                        },
                    )
                    columns = list(result.column_names)
                    changes = []
                    for values in result.result_rows:
                        prior = dict(zip(columns, values, strict=True))
                        key = (str(prior["project_id"]), str(prior["id"]))
                        pg_tags = candidates.get(key)
                        if pg_tags is None:
                            continue
                        # A PG-then-CH write after our PG read can make that
                        # snapshot stale. Leave changed rows for the next pass.
                        if prior["_version"] != ch_states[key][1]:
                            changed += 1
                            continue
                        if prior["is_deleted"] or _matches_tags(prior["tags"], pg_tags):
                            continue
                        different += 1
                        if not opts["dry_run"]:
                            # Preserve every non-tag column from the latest CH row.
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
                    f"no CH trace row {missing}; changed during scan {changed}; "
                    f"resume with --after-id {last_id}"
                )
        finally:
            client.close()
        if opts["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"DRY RUN — scanned {scanned} trace(s); would mirror {different} trace(s); "
                    f"changed during scan {changed}."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"scanned {scanned} trace(s); mirrored {written} trace(s); "
                    f"changed during scan {changed}."
                )
            )
        if changed:
            self.stdout.write(
                "Rerun reconcile_legacy_trace_tags from the start: traces changed "
                "during the scan and were skipped."
            )
