"""ch25_backfill_session_overlay — seed the PG ``trace_session_overlay`` from the
legacy PG ``trace_session`` UI state.

Part of the TraceSession three-way split (CH-derived dimensions, DESIGN §5 / §5.2).
The session's *external identity* (``external_session_id``, ``first_seen``) moves
to the CH ``trace_sessions`` RMT (backfilled by ``ch25_backfill_curated_dimensions``);
the *user overlay* — ``bookmarked`` and an optional ``display_name`` rename
override — moves to the small PG ``trace_session_overlay`` table written ONLY by
the UI. This command does the one-time history load of that overlay from the
legacy ``trace_session`` rows, so the bookmark state survives the reads cutover
(and, eventually, the PG ``trace_session`` drop at P4).

WHAT IT COPIES — bookmarks only; NOT a rename.
    Source state in PG ``trace_session`` is ``bookmarked`` (a real UI boolean)
    and ``name``. ``name`` is the DUAL-ROLE column (DESIGN §2.5): it is BOTH the
    external session id AND, after a UI rename, the display label — the two are
    indistinguishable in the legacy schema. So this backfill canNOT tell a
    renamed session from a never-renamed one, and copying ``name`` into the
    overlay ``display_name`` would falsely mark EVERY bookmarked session as
    "renamed" and pin its display name, breaking the go-forward identity.

    Therefore the backfill writes ``display_name = NULL`` and relies on the read
    COALESCE: ``COALESCE(overlay.display_name, external_session_id)``. In P3a the
    CH ``external_session_id`` == the PG ``name`` (straight mirror), so a
    NULL ``display_name`` yields the SAME display name the old read showed
    (``name``) — exact parity — while leaving the rename carve-out to P3b (where
    the deterministic re-key and the recoverable-vs-unrecoverable external-id
    split are handled). Only sessions a user actually bookmarked get an overlay
    row, keeping the table tiny (the intended tier-3 dimension).

    ``--include-display-name`` opts into ALSO copying ``name`` → ``display_name``
    for bookmarked sessions (off by default). It exists only for harness/parity
    setups that need a non-NULL override present; production backfills must NOT
    use it (it would pin display names as described above).

IDEMPOTENT: ``update_or_create`` keyed on the overlay's unique ``trace_session_id``
— re-running reconciles ``bookmarked``/``display_name`` to the current PG state
without duplicating rows. Uses ``all_objects`` on TraceSession so a soft-deleted
bookmarked session is still reflected (its overlay row carries ``deleted`` too).

    python manage.py ch25_backfill_session_overlay                      # all projects
    python manage.py ch25_backfill_session_overlay --project-id <UUID>  # one project (repeatable)
    python manage.py ch25_backfill_session_overlay --dry-run            # count only, no writes
    python manage.py ch25_backfill_session_overlay --include-display-name  # also copy name (harness only)

NOTE: this is a PURE PG command (no ClickHouse) — the overlay is a PG table. It
needs no ``CH25_DATABASE`` and touches no CH object.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Seed PG trace_session_overlay (bookmarked / display_name) from the "
        "legacy PG trace_session UI state (idempotent)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=5_000)
        parser.add_argument(
            "--project-id",
            action="append",
            default=None,
            help="Limit to this project (repeatable). Default: all.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count only; no overlay writes.",
        )
        parser.add_argument(
            "--include-display-name",
            action="store_true",
            help=(
                "Also copy TraceSession.name into the overlay display_name for "
                "bookmarked sessions. HARNESS/PARITY ONLY — do not use in prod "
                "(name is the dual-role external id; copying it pins display "
                "names and falsely marks every bookmarked session as renamed)."
            ),
        )

    def handle(self, *args, **opts):
        from tracer.models.trace_session import TraceSession, TraceSessionOverlay

        manager = getattr(TraceSession, "all_objects", TraceSession.objects)
        # Only bookmarked sessions get an overlay row — that's the sole UI-overlay
        # state recoverable from the legacy schema (a rename is indistinguishable
        # from the external id; see the module docstring).
        qs = manager.filter(bookmarked=True).order_by("created_at", "id")
        if opts["project_id"]:
            qs = qs.filter(project_id__in=opts["project_id"])

        total = qs.count()
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"trace_session_overlay — {total} bookmarked session(s) to seed "
                f"(dry_run={opts['dry_run']}, include_display_name="
                f"{opts['include_display_name']})"
            )
        )
        if opts["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(f"DRY RUN — would seed {total} overlay row(s).")
            )
            return
        if total == 0:
            self.stdout.write("  nothing to backfill for trace_session_overlay.")
            return

        include_name = opts["include_display_name"]
        written = 0
        # Read only local cols + the raw FK id project_id (never s.project) so
        # this stays a pure SELECT with no per-row related fetch.
        rows = qs.only("id", "project_id", "name", "bookmarked")
        for s in rows.iterator(chunk_size=opts["batch_size"]):
            TraceSessionOverlay.objects.update_or_create(
                trace_session_id=s.id,
                defaults={
                    "project_id": s.project_id,
                    "bookmarked": True,
                    # display_name stays NULL unless explicitly opted in (harness).
                    "display_name": (s.name if include_name else None),
                },
            )
            written += 1
            if written % opts["batch_size"] == 0:
                self.stdout.write(f"  …{written}/{total}")

        self.stdout.write(
            self.style.SUCCESS(
                f"✓ seeded {written} row(s) into PG `trace_session_overlay`."
            )
        )
