"""ch25_backfill_curated_dimensions — load PG curated dimensions into CH.

Backfills the two CH-native CURATED dimension RMTs introduced in P3a of the
CH-derived-dimensions migration (DESIGN §4 / §5):

    PG ``tracer_enduser``  → CH ``end_users``      (+ end_users_dict)
    PG ``trace_session``   → CH ``trace_sessions`` (+ trace_sessions_dict)

These tables hold the *curated* fields that are NOT derivable from spans
(``016_span_user_rollup`` / ``008_per_session_rollup`` cover the derived
analytics). In the legacy world they reached ClickHouse via PeerDB CDC
(``tracer_enduser`` / ``trace_session`` landing tables → ``enduser_dict`` /
``trace_session_dict``); v2 removes CDC, so the history is loaded here once and
then kept fresh by the collector dual-write
(``curated_writer.mirror_curated_dimensions_to_clickhouse``). The PG-row → CH-row
mapping is SHARED with that live mirror via ``curated_writer.end_user_to_row`` /
``trace_session_to_row`` (one definition; this backfill passes
``version_from_updated_at=True``).

P3a IDENTITY — STRAIGHT MIRROR, NO RE-KEY. ``end_user_id`` / ``trace_session_id``
are copied verbatim from the PG ``id`` (the random PG-minted uuid4). The
deterministic UUIDv5 re-keying and the consolidation it implies (DESIGN §3 /
§3.1) are P3b and are intentionally NOT done here, so the backfilled rows stay
keyed exactly like the ids already denormalized onto every span.

  • ``end_users``:      end_user_id = PG id, version = updated_at,
                        first_seen = created_at, is_deleted = 1 if soft-deleted.
                        NULL ``user_id_hash``/``metadata`` coerce to '' / '{}'
                        (non-null String columns); ``user_id_type`` stays NULL
                        (the column / dict attr is Nullable).
  • ``trace_sessions``: trace_session_id = PG id, external_session_id = PG name,
                        version = updated_at, first_seen = created_at,
                        is_deleted = 1 if soft-deleted.

Idempotent: both targets are ReplacingMergeTree(version) keyed on the entity id,
with ``version`` derived from the row's own ``updated_at`` — re-running is a
latest-wins no-op and never outranks a live collector dual-write's now()-based
version. Uses ``all_objects`` so soft-deleted rows are mirrored too (is_deleted
= 1), matching the raw PG table count.

After the inserts the command issues ``SYSTEM RELOAD DICTIONARY`` for both dicts
so ``dictGet`` resolves the freshly loaded rows immediately (the dicts otherwise
only re-read after their 60-120s LIFETIME).

CH target DB: ``get_v2_config()`` (env ``CH25_DATABASE`` / ``CH25_HOST`` …
override; falls back to the legacy CLICKHOUSE host). Point ``CH25_DATABASE`` at
``ch_test`` to backfill the parity harness.

Operator UX:
    python manage.py ch25_backfill_curated_dimensions                      # both entities
    python manage.py ch25_backfill_curated_dimensions --project-id <UUID>  # one project (repeatable)
    python manage.py ch25_backfill_curated_dimensions --only end_users     # one entity
    python manage.py ch25_backfill_curated_dimensions --dry-run            # count only, no writes
    python manage.py ch25_backfill_curated_dimensions --no-reload-dict     # skip dict reload
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from tracer.services.clickhouse.v2 import get_v2_config

# Shared row-mapping contract with the LIVE collector dual-write. Both the
# backfill (here) and ``curated_writer.mirror_curated_dimensions_to_clickhouse``
# map a PG model instance → CH row through these, so the column order and the
# id/version/first_seen/coercion rules have ONE definition (DRY). The backfill
# passes ``version_from_updated_at=True`` so a re-run is an idempotent latest-
# wins no-op that never out-versions a live now()-based mirror.
from tracer.services.clickhouse.v2.curated_writer import (
    _END_USER_COLUMNS,
    _TRACE_SESSION_COLUMNS,
    end_user_to_row,
    trace_session_to_row,
)


class Command(BaseCommand):
    help = (
        "Backfill PG tracer_enduser → CH end_users and PG trace_session → "
        "CH trace_sessions (idempotent, ReplacingMergeTree)."
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
            help="Backfill only this entity. Default: both.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Count only; no CH writes."
        )
        parser.add_argument(
            "--no-reload-dict",
            action="store_true",
            help="Skip SYSTEM RELOAD DICTIONARY after load.",
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

    # ── end_users ────────────────────────────────────────────────────────────
    def _backfill_end_users(self, client, opts) -> None:
        from tracer.models.observation_span import EndUser

        manager = getattr(EndUser, "all_objects", EndUser.objects)
        qs = manager.all().order_by("created_at", "id")
        if opts["project_id"]:
            qs = qs.filter(project_id__in=opts["project_id"])

        total = qs.count()
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"end_users — {total} PG enduser(s) to mirror (dry_run={opts['dry_run']})"
            )
        )
        if opts["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(f"DRY RUN — would mirror {total} enduser(s).")
            )
            return
        if total == 0:
            self.stdout.write("  nothing to backfill for end_users.")
            return

        bs = opts["batch_size"]
        written = 0
        batch: list[list] = []
        # Iterate model INSTANCES (not .values()) so the SHARED mapper
        # ``end_user_to_row`` is the single row definition with the live mirror.
        # ``.only()`` keeps the read a pure SELECT of local cols + the raw FK ids
        # (project_id/organization_id) — the mapper reads those *_id attrs, never
        # eu.project/eu.organization, so no related fetch fires.
        rows = qs.only(
            "id",
            "project_id",
            "organization_id",
            "user_id",
            "user_id_type",
            "user_id_hash",
            "metadata",
            "created_at",
            "updated_at",
            "deleted",
        )
        for eu in rows.iterator(chunk_size=bs):
            batch.append(end_user_to_row(eu, version_from_updated_at=True))
            if len(batch) >= bs:
                client.insert("end_users", batch, column_names=list(_END_USER_COLUMNS))
                written += len(batch)
                self.stdout.write(f"  …{written}/{total}")
                batch = []
        if batch:
            client.insert("end_users", batch, column_names=list(_END_USER_COLUMNS))
            written += len(batch)

        self.stdout.write(
            self.style.SUCCESS(f"✓ inserted {written} row(s) into CH `end_users`.")
        )

    # ── trace_sessions ───────────────────────────────────────────────────────
    def _backfill_trace_sessions(self, client, opts) -> None:
        from tracer.models.trace_session import TraceSession

        manager = getattr(TraceSession, "all_objects", TraceSession.objects)
        qs = manager.all().order_by("created_at", "id")
        if opts["project_id"]:
            qs = qs.filter(project_id__in=opts["project_id"])

        total = qs.count()
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"trace_sessions — {total} PG session(s) to mirror (dry_run={opts['dry_run']})"
            )
        )
        if opts["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(f"DRY RUN — would mirror {total} session(s).")
            )
            return
        if total == 0:
            self.stdout.write("  nothing to backfill for trace_sessions.")
            return

        bs = opts["batch_size"]
        written = 0
        batch: list[list] = []
        # Iterate INSTANCES + SHARED mapper ``trace_session_to_row`` (see the
        # end_users note). ``.only()`` reads local cols + the raw FK id project_id
        # (the mapper reads s.project_id, never s.project) → pure SELECT.
        rows = qs.only(
            "id", "project_id", "name", "created_at", "updated_at", "deleted"
        )
        for s in rows.iterator(chunk_size=bs):
            batch.append(trace_session_to_row(s, version_from_updated_at=True))
            if len(batch) >= bs:
                client.insert(
                    "trace_sessions", batch, column_names=list(_TRACE_SESSION_COLUMNS)
                )
                written += len(batch)
                self.stdout.write(f"  …{written}/{total}")
                batch = []
        if batch:
            client.insert(
                "trace_sessions", batch, column_names=list(_TRACE_SESSION_COLUMNS)
            )
            written += len(batch)

        self.stdout.write(
            self.style.SUCCESS(f"✓ inserted {written} row(s) into CH `trace_sessions`.")
        )

    def handle(self, *args, **opts):
        client = self._client()
        do_users = opts["only"] in (None, "end_users")
        do_sessions = opts["only"] in (None, "trace_sessions")

        if do_users:
            self._backfill_end_users(client, opts)
        if do_sessions:
            self._backfill_trace_sessions(client, opts)

        if opts["dry_run"]:
            return

        # Reload the dicts so dictGet resolves the freshly loaded rows now (they
        # otherwise only re-read after their 60-120s LIFETIME). RMT FINAL in the
        # dict source collapses to latest-version-wins at reload time.
        if not opts["no_reload_dict"]:
            if do_users:
                client.command("SYSTEM RELOAD DICTIONARY end_users_dict")
                self.stdout.write("  reloaded end_users_dict.")
            if do_sessions:
                client.command("SYSTEM RELOAD DICTIONARY trace_sessions_dict")
                self.stdout.write("  reloaded trace_sessions_dict.")

        # Quick parity readout (operator sanity check, not the full validator).
        if do_users:
            n = client.query(
                "SELECT count() FROM end_users FINAL WHERE is_deleted = 0"
            ).result_rows[0][0]
            self.stdout.write(f"CH end_users (FINAL, not deleted): {n}")
        if do_sessions:
            n = client.query(
                "SELECT count() FROM trace_sessions FINAL WHERE is_deleted = 0"
            ).result_rows[0][0]
            self.stdout.write(f"CH trace_sessions (FINAL, not deleted): {n}")
