"""Backfill the additive dashboard root-span fact table in bounded windows.

The command is dry-run by default. ``--execute`` is the only write path, and
that path inserts into ``dashboard_root_spans`` exclusively; it never mutates,
truncates, or alters ``spans``. ReplacingMergeTree versions make each batch
idempotent, while a count + content fingerprint proves the destination agrees
with the current active root rows before the coverage flag can be enabled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.v2 import get_v2_config

_ROOT_COLUMNS = (
    "project_id",
    "observation_type",
    "service_name",
    "start_time",
    "trace_id",
    "id",
    "parent_span_id",
    "name",
    "latency_ms",
    "end_user_id",
    "trace_session_id",
    "prompt_version_id",
    "prompt_label_id",
    "status",
    "model",
    "provider",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "attrs_string",
    "attrs_number",
    "attrs_bool",
    "attributes_extra",
    "tags",
    "is_deleted",
    "_version",
)


@dataclass(frozen=True)
class _Window:
    start: datetime
    end: datetime


def _parse_utc(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CommandError(f"invalid ISO-8601 timestamp: {raw}") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _windows(start: datetime, end: datetime, batch_hours: int) -> list[_Window]:
    step = timedelta(hours=batch_hours)
    count = math.ceil((end - start) / step)
    return [
        _Window(start + index * step, min(end, start + (index + 1) * step))
        for index in range(count)
    ]


def _literal_timestamp(value: datetime) -> str:
    rendered = value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    return f"toDateTime64('{rendered}', 6, 'UTC')"


def _scope_predicate(window: _Window, project_ids: tuple[str, ...]) -> str:
    clauses = [
        f"start_time >= {_literal_timestamp(window.start)}",
        f"start_time < {_literal_timestamp(window.end)}",
        "parent_span_id = ''",
        "is_deleted = 0",
    ]
    if project_ids:
        projects = ", ".join(f"toUUID('{project_id}')" for project_id in project_ids)
        clauses.append(f"project_id IN ({projects})")
    return "\n  AND ".join(clauses)


def _fingerprint_query(
    table: str, window: _Window, project_ids: tuple[str, ...]
) -> str:
    # Hash every copied field, not only identity/count, so coverage cannot be
    # declared over rows whose attribute payload was truncated or transformed.
    payload = f"toString(tuple({', '.join(_ROOT_COLUMNS)}))"
    return (
        "SELECT count() AS rows,\n"
        f"       sumWithOverflow(cityHash64({payload})) AS city_fingerprint,\n"
        f"       sumWithOverflow(sipHash64({payload})) AS sip_fingerprint\n"
        f"FROM {table} FINAL\n"
        f"WHERE {_scope_predicate(window, project_ids)}"
    )


def _insert_query(window: _Window, project_ids: tuple[str, ...]) -> str:
    columns = ",\n    ".join(_ROOT_COLUMNS)
    return (
        "INSERT INTO dashboard_root_spans\n"
        f"(\n    {columns}\n)\n"
        "SELECT\n"
        f"    {columns}\n"
        "FROM spans FINAL\n"
        f"WHERE {_scope_predicate(window, project_ids)}"
    )


class Command(BaseCommand):
    help = (
        "Backfill dashboard_root_spans from spans FINAL in bounded time windows; "
        "dry-run unless --execute is supplied."
    )

    def add_arguments(self, parser):
        parser.add_argument("--start", required=True, help="Inclusive ISO-8601 start")
        parser.add_argument("--end", required=True, help="Exclusive ISO-8601 end")
        parser.add_argument(
            "--project-id",
            action="append",
            default=[],
            help="Limit to one project UUID (repeatable)",
        )
        parser.add_argument(
            "--all-projects",
            action="store_true",
            help="Explicitly acknowledge an all-project backfill",
        )
        parser.add_argument(
            "--batch-hours",
            type=int,
            default=settings.DASHBOARD_ROOT_SPANS_BACKFILL_BATCH_HOURS,
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Perform additive inserts; default only prints the plan",
        )

    def _client(self):
        import clickhouse_connect

        cfg = get_v2_config()
        return clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
            send_receive_timeout=(
                settings.DASHBOARD_ROOT_SPANS_BACKFILL_WALL_SECONDS
                + settings.DASHBOARD_ROOT_SPANS_BACKFILL_TRANSPORT_GRACE_SECONDS
            ),
        )

    @staticmethod
    def _fingerprint(client, table, window, project_ids, query_settings):
        result = client.query(
            _fingerprint_query(table, window, project_ids),
            settings=query_settings,
        ).result_rows[0]
        return int(result[0]), int(result[1] or 0), int(result[2] or 0)

    def handle(self, *args, **options):
        start = _parse_utc(options["start"])
        end = _parse_utc(options["end"])
        if start >= end:
            raise CommandError("start must be before end")

        try:
            project_ids = tuple(
                sorted({str(UUID(value)) for value in options["project_id"]})
            )
        except ValueError as exc:
            raise CommandError("project-id values must be UUIDs") from exc
        if bool(project_ids) == bool(options["all_projects"]):
            raise CommandError(
                "choose either one or more --project-id values or --all-projects"
            )

        batch_hours = options["batch_hours"]
        maximum_batch_hours = settings.DASHBOARD_ROOT_SPANS_BACKFILL_MAX_BATCH_HOURS
        if not 1 <= batch_hours <= maximum_batch_hours:
            raise CommandError(
                f"batch-hours must be between 1 and {maximum_batch_hours}"
            )
        batches = _windows(start, end, batch_hours)
        self.stdout.write(
            f"dashboard_root_spans: {len(batches)} batch(es), "
            f"{start.isoformat()} to {end.isoformat()}, "
            f"scope={'all projects' if not project_ids else len(project_ids)}"
        )
        if not options["execute"]:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN — no connection or write performed; add --execute to run."
                )
            )
            return

        query_settings = {
            "max_threads": settings.DASHBOARD_ROOT_SPANS_BACKFILL_MAX_THREADS,
            "max_memory_usage": settings.DASHBOARD_TRACE_READ_MAX_MEMORY_BYTES,
            "max_bytes_to_read": settings.DASHBOARD_TRACE_READ_MAX_BYTES,
            "max_execution_time": settings.DASHBOARD_ROOT_SPANS_BACKFILL_WALL_SECONDS,
            "read_overflow_mode": "throw",
            "timeout_overflow_mode": "throw",
        }
        client = self._client()
        try:
            for index, window in enumerate(batches, start=1):
                source = self._fingerprint(
                    client, "spans", window, project_ids, query_settings
                )
                target = self._fingerprint(
                    client,
                    "dashboard_root_spans",
                    window,
                    project_ids,
                    query_settings,
                )
                if target != source:
                    client.command(
                        _insert_query(window, project_ids),
                        settings=query_settings,
                    )
                    target = self._fingerprint(
                        client,
                        "dashboard_root_spans",
                        window,
                        project_ids,
                        query_settings,
                    )
                if target != source:
                    raise CommandError(
                        f"batch {index} verification failed: source={source}, "
                        f"target={target}"
                    )
                self.stdout.write(
                    f"  {index}/{len(batches)} verified {source[0]} root row(s): "
                    f"{window.start.isoformat()} to {window.end.isoformat()}"
                )
        finally:
            client.close()

        activation_scope = (
            "set DASHBOARD_ROOT_SPANS_ALL_PROJECTS_COVERED=true"
            if options["all_projects"]
            else "add the verified project UUIDs to "
            "DASHBOARD_ROOT_SPANS_PROJECT_ALLOWLIST"
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Backfill verified. Set DASHBOARD_ROOT_SPANS_COVERED_SINCE "
                f"no later than {start.isoformat()} and {activation_scope}."
            )
        )
