"""Replay runtime load evidence in bounded UTC-day windows, never during boot.

Example (end is exclusive)::

    python manage.py ch25_backfill_skill_trace_links \
        --start-date 2026-11-01 --end-date 2026-11-02 --project-id <UUID>

Both projections are read from the immutable 028 migration, so backfills and
live inserts cannot silently disagree. Original _version and tombstones are
kept: retries, overlap with live traffic and a partially completed run are safe
for readers using FINAL (or argMax followed by the deletion predicate). No
MATERIALIZE/OPTIMIZE, source mutation or automatic scheduling occurs here.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.v2 import get_v2_config

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "services/clickhouse/v2/schema/028_skill_attribution.sql"
)
TARGETS = ("skill_trace_links", "skill_load_failures")
MAX_DAYS = 31
QUERY_SETTINGS = {"max_execution_time": 120, "max_memory_usage": 536_870_912}


def load_projections() -> dict[str, str]:
    """Read only the two known SELECTs, not executable migration DDL."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    projections = {}
    for target in TARGETS:
        match = re.search(
            rf"CREATE MATERIALIZED VIEW IF NOT EXISTS {target}_mv\s+"
            rf"TO {target}\s+AS\s+(SELECT\b[^;]+);",
            sql,
        )
        if match is None:
            raise CommandError(f"Missing {target} projection in migration 028")
        projections[target] = match.group(1).strip()
    return projections


def day_windows(start: str, end: str):
    try:
        first, stop = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError) as exc:
        raise CommandError("Dates must be ISO dates (YYYY-MM-DD).") from exc
    if not 0 < (stop - first).days <= MAX_DAYS:
        raise CommandError(f"Select between 1 and {MAX_DAYS} UTC days; end is exclusive.")
    while first < stop:
        following = first + timedelta(days=1)
        yield (
            datetime.combine(first, time.min, tzinfo=timezone.utc),
            datetime.combine(following, time.min, tzinfo=timezone.utc),
        )
        first = following


def backfill_sql(target: str, projection: str, *, project_scoped: bool) -> str:
    if target not in TARGETS:
        raise ValueError("Unknown load evidence table")
    # Do not remove old versions or tombstones before replay. The target uses
    # the original versions to resolve concurrent inserts and repeated runs.
    sql = (
        f"INSERT INTO {target}\n{projection}\n"
        "AND start_time >= %(start)s AND start_time < %(end)s"
    )
    if project_scoped:
        sql += "\nAND project_id = %(project_id)s"
    return sql


class Command(BaseCommand):
    help = "Backfill load and failed-load evidence in bounded UTC-day windows."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--start-date", required=True, help="Inclusive UTC date (YYYY-MM-DD).")
        parser.add_argument("--end-date", required=True, help="Exclusive UTC date (at most 31 days).")
        parser.add_argument("--project-id", help="Optional project UUID; default is all projects.")
        parser.add_argument("--dry-run", action="store_true", help="Show windows without connecting or writing.")

    def _client(self):
        import clickhouse_connect

        cfg = get_v2_config()
        return clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
            send_receive_timeout=150,
        )

    def handle(self, *args, **options):
        # Validate the complete request before connecting or making any writes.
        windows = list(day_windows(options["start_date"], options["end_date"]))
        project = options.get("project_id")
        if project is not None:
            try:
                project = str(UUID(project))
            except (TypeError, ValueError, AttributeError) as exc:
                raise CommandError("--project-id must be a UUID.") from exc
        projections = load_projections()
        if options["dry_run"]:
            for start, end in windows:
                self.stdout.write(f"DRY RUN: both load tables, {start.date()} to {end.date()} (exclusive).")
            return

        client = self._client()
        try:
            for start, end in windows:
                params = {"start": start, "end": end}
                if project is not None:
                    params["project_id"] = project
                for target, projection in projections.items():
                    client.command(
                        backfill_sql(target, projection, project_scoped=project is not None),
                        parameters=params,
                        settings=QUERY_SETTINGS,
                    )
                    self.stdout.write(f"Replayed {target}: {start.date()} to {end.date()} (exclusive).")
        finally:
            client.close()
