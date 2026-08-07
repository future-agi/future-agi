"""ch25_build_id_remap — build the historical old→new surrogate-id remap.

P3b STEP1 — ADDITIVE. Populates the two CH ReplacingMergeTree remap tables added
in ``019_id_remap.sql`` (DESIGN §3 / §3.1):

    PG ``tracer_enduser``  → CH ``end_user_id_remap``      (old_id, new_id, version)
    PG ``trace_session``   → CH ``trace_session_id_remap``  (old_id, new_id, version)

For every historical PG entity it records ``(old_id = the random PG uuid4,
new_id = the deterministic UUIDv5 of the natural key)``. The ``new_id`` is computed
by ``deterministic_id.deterministic_end_user_id`` /
``deterministic_trace_session_id`` — the SAME functions the future ingestion path
(P3b step2) calls — so a historical id and a new ingest-time id for one identity
resolve to the same ``new_id``. The map lets a read spanning the cutover translate
a span's stored OLD id to its NEW id WITHOUT rewriting billions of span rows
(DESIGN §3, §10.1).

The map is MANY-TO-ONE by construction: the deterministic id consolidates the
NULL-``user_id_type`` enduser duplicates (the box dry-run collapsed 879 → 544) and
the rename-bug duplicate sessions (1405 → 1404). So ``rows in`` (one per PG row) is
>= ``distinct new ids out`` — the command prints both as the operator's headline
sanity check (a prod run prints 879→544 / 1405→1404).

SCOPE — STEP1 ONLY. This builds the UNIFORM map (the deterministic id applied to
EVERY row). It does NOT implement the §3.1 carve-out (renamed sessions keeping an
``old→old`` identity row), the survivor-curated-row pick, or the span/Score/
annotation rewrite — those are the P3b-step3 consolidation sweep. It touches NO
ingestion / ``get_or_create`` (step2) and NO read path (step1.5).

IDEMPOTENT. Both targets are ReplacingMergeTree(version) keyed on ``old_id`` with
``version = now()`` — a re-run re-inserts the identical ``(old_id, new_id)`` with a
fresher version; the merge keeps one row per ``old_id`` and the value is unchanged.
Uses ``all_objects`` so soft-deleted rows are remapped too (a TTL'd span of a
soft-deleted entity must still resolve), matching the raw PG table count.

CH target DB: ``get_v2_config()`` (env ``CH25_DATABASE`` / ``CH25_HOST`` …
override). Point ``CH25_DATABASE`` at ``ch_test`` (and ``PGBOUNCER_HOST`` at the
test PG) to build against the parity island — NEVER prod ``default``.

Operator UX:
    python manage.py ch25_build_id_remap                       # both entities
    python manage.py ch25_build_id_remap --project-id <UUID>   # one project (repeatable)
    python manage.py ch25_build_id_remap --only end_users      # one entity
    python manage.py ch25_build_id_remap --dry-run             # compute + count only, no CH writes
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.core.management.base import BaseCommand

from tracer.services.clickhouse.v2 import get_v2_config

# The frozen id formula — the SINGLE source of truth shared with the future
# ingestion path (P3b step2). The build computes new_id ONLY through these, so a
# historical remap row and a new ingest-time id can never drift.
from tracer.services.clickhouse.v2.deterministic_id import (
    deterministic_end_user_id,
    deterministic_trace_session_id,
)

# Column contract for the remap INSERTs — must match 019_id_remap.sql.
_REMAP_COLUMNS: tuple[str, ...] = ("old_id", "new_id", "version")


class Command(BaseCommand):
    help = (
        "Build the historical old→new id remap: PG tracer_enduser → CH "
        "end_user_id_remap and PG trace_session → CH trace_session_id_remap "
        "(deterministic UUIDv5, idempotent ReplacingMergeTree)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=10_000)
        parser.add_argument(
            "--project-id",
            action="append",
            default=None,
            help="Limit to this project (repeatable). Default: all.",
        )
        parser.add_argument(
            "--only",
            choices=["end_users", "trace_sessions"],
            default=None,
            help="Build only this entity's remap. Default: both.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute + count only; no CH writes.",
        )

    def _client(self):
        import clickhouse_connect

        cfg = get_v2_config()
        self.stdout.write(
            self.style.MIGRATE_LABEL(
                f"CH target: {cfg['host']}:{cfg['http_port']} db={cfg['database']}"
            )
        )
        return clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
            send_receive_timeout=120,
        )

    def _insert_batch(self, client, table: str, batch: list[list]) -> None:
        client.insert(table, batch, column_names=list(_REMAP_COLUMNS))

    # ── end_users ────────────────────────────────────────────────────────────
    def _build_end_users(self, client, opts) -> None:
        from tracer.models.observation_span import EndUser

        manager = getattr(EndUser, "all_objects", EndUser.objects)
        qs = manager.all().order_by("created_at", "id")
        if opts["project_id"]:
            qs = qs.filter(project_id__in=opts["project_id"])

        total = qs.count()
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"end_user_id_remap — {total} PG enduser(s) to remap "
                f"(dry_run={opts['dry_run']})"
            )
        )
        if total == 0:
            self.stdout.write("  nothing to remap for end_users.")
            return

        bs = opts["batch_size"]
        now = datetime.now(UTC)
        written = 0
        distinct_new: set[str] = set()
        batch: list[list] = []
        # .only() keeps the read a pure SELECT of the natural-key columns + the raw
        # FK ids (project_id/organization_id) — we read those *_id attrs directly,
        # never eu.project/eu.organization, so no related fetch fires on the hot
        # read. all_objects (above) includes soft-deleted rows.
        rows = qs.only(
            "id",
            "project_id",
            "organization_id",
            "user_id",
            "user_id_type",
        )
        for eu in rows.iterator(chunk_size=bs):
            new_id = deterministic_end_user_id(
                eu.project_id, eu.organization_id, eu.user_id, eu.user_id_type
            )
            distinct_new.add(str(new_id))
            batch.append([str(eu.id), str(new_id), now])
            if len(batch) >= bs:
                if not opts["dry_run"]:
                    self._insert_batch(client, "end_user_id_remap", batch)
                written += len(batch)
                self.stdout.write(f"  …{written}/{total}")
                batch = []
        if batch:
            if not opts["dry_run"]:
                self._insert_batch(client, "end_user_id_remap", batch)
            written += len(batch)

        verb = "would map" if opts["dry_run"] else "mapped"
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ end_user_id_remap: {verb} {written} old id(s) → "
                f"{len(distinct_new)} distinct new id(s)."
            )
        )

    # ── trace_sessions ───────────────────────────────────────────────────────
    def _build_trace_sessions(self, client, opts) -> None:
        from tracer.models.trace_session import TraceSession

        manager = getattr(TraceSession, "all_objects", TraceSession.objects)
        qs = manager.all().order_by("created_at", "id")
        if opts["project_id"]:
            qs = qs.filter(project_id__in=opts["project_id"])

        total = qs.count()
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"trace_session_id_remap — {total} PG session(s) to remap "
                f"(dry_run={opts['dry_run']})"
            )
        )
        if total == 0:
            self.stdout.write("  nothing to remap for trace_sessions.")
            return

        bs = opts["batch_size"]
        now = datetime.now(UTC)
        written = 0
        distinct_new: set[str] = set()
        batch: list[list] = []
        # .only() reads local cols + the raw FK id project_id (we read s.project_id,
        # never s.project) → pure SELECT. name = the external session id (the
        # deterministic key input). all_objects includes soft-deleted rows.
        rows = qs.only("id", "project_id", "name")
        for s in rows.iterator(chunk_size=bs):
            new_id = deterministic_trace_session_id(s.project_id, s.name)
            distinct_new.add(str(new_id))
            batch.append([str(s.id), str(new_id), now])
            if len(batch) >= bs:
                if not opts["dry_run"]:
                    self._insert_batch(client, "trace_session_id_remap", batch)
                written += len(batch)
                self.stdout.write(f"  …{written}/{total}")
                batch = []
        if batch:
            if not opts["dry_run"]:
                self._insert_batch(client, "trace_session_id_remap", batch)
            written += len(batch)

        verb = "would map" if opts["dry_run"] else "mapped"
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ trace_session_id_remap: {verb} {written} old id(s) → "
                f"{len(distinct_new)} distinct new id(s)."
            )
        )

    def handle(self, *args, **opts):
        # Build the client even on a dry-run so the CH target (host/db) is printed
        # and a misconfigured target fails fast — but never write when --dry-run.
        client = self._client()
        do_users = opts["only"] in (None, "end_users")
        do_sessions = opts["only"] in (None, "trace_sessions")

        if do_users:
            self._build_end_users(client, opts)
        if do_sessions:
            self._build_trace_sessions(client, opts)

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("DRY RUN — no CH writes performed."))
