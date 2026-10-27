"""drop_legacy_observation_span — retire the PG observation-span table.

The PG ``tracer_observation_span`` table is the predecessor to v2 CH
``spans`` (now written directly by fi-collector). Before it can be
dropped, four PG readers must migrate to CH:

    1. ``tracer/socket.py``                          (graph data WS)
    2. ``tracer/utils/sql_queries.py``
    3. ``model_hub/utils/SQL_queries.py``
    4. ``ee/usage/management/commands/backfill_usage_summary.py``

This command's default mode is a **dry-run that audits readiness**:
it grep-checks the four readers for active ``tracer_observation_span``
queries and prints a checklist. ``--force-drop`` actually drops the
table, but only when ``CH25_DROP_LEGACY_CDC_CHAIN`` is set, the readers
have been migrated, and the operator passed ``--yes`` to confirm.

Dev / local docker compose: the readers haven't all migrated yet — so
even there, the command currently exits early. Run it after migrating
the four readers to see a green checklist.

In prod the drop stays manual until ops decides. See
``docs/CH25_MIGRATION.md``.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import connection

# Paths checked for residual PG reads of tracer_observation_span. These
# are resolved at runtime relative to the repo root so the command stays
# robust against working-directory changes.
_PG_READERS = (
    "tracer/socket.py",
    "tracer/utils/sql_queries.py",
    "model_hub/utils/SQL_queries.py",
    "ee/usage/management/commands/backfill_usage_summary.py",
)

# Match `FROM tracer_observation_span` or `tracer_observation_span,` /
# `... s`. We're looking for SQL clauses, not comments / docstrings.
# The regex is intentionally case-insensitive and tolerant of whitespace.
_QUERY_RE = re.compile(
    r"\bFROM\s+tracer_observation_span\b",
    re.IGNORECASE,
)

_FLAG_ENV = "CH25_DROP_LEGACY_CDC_CHAIN"


def _flag_set() -> bool:
    """Return whether the CH25 cutover env flag is enabled."""
    val = os.getenv(_FLAG_ENV, "false").lower()
    return val in {"1", "true", "yes", "on"}


def _readers_status(repo_root: Path) -> list[tuple[str, int]]:
    """Return one ``(path, n_matches)`` tuple per PG reader.

    ``n_matches`` is the count of ``FROM tracer_observation_span``
    occurrences. We don't strip docstrings — embedded SQL in these
    files lives inside triple-quoted f-strings and a naive triple-quote
    stripper would eat them. Over-counting from a stray comment is the
    safer failure mode for an audit (refuse to drop until proven clean).
    """
    statuses: list[tuple[str, int]] = []
    for rel in _PG_READERS:
        path = repo_root / rel
        if not path.exists():
            statuses.append((rel, -1))  # missing file — flag, don't drop
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # Strip only `#` line comments — these never contain real SQL.
        text_clean = re.sub(r"(?m)^\s*#.*$", "", text)
        statuses.append((rel, len(_QUERY_RE.findall(text_clean))))
    return statuses


class Command(BaseCommand):
    help = "Audit / drop the legacy PG tracer_observation_span table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force-drop",
            action="store_true",
            help="Actually DROP the PG table. Default is dry-run audit.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the interactive confirmation prompt.",
        )

    def handle(self, *args, force_drop: bool = False, yes: bool = False, **opts):
        repo_root = Path(__file__).resolve().parents[4]
        statuses = _readers_status(repo_root)

        # ----- audit pass -----
        self.stdout.write(self.style.MIGRATE_HEADING("PG reader audit"))
        clean = True
        for rel, count in statuses:
            if count < 0:
                self.stdout.write(self.style.WARNING(f"  ?  {rel}: missing"))
                clean = False
            elif count == 0:
                self.stdout.write(self.style.SUCCESS(f"  ✓  {rel}: migrated"))
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"  ✗  {rel}: {count} active reads of tracer_observation_span"
                    )
                )
                clean = False

        flag_on = _flag_set()
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"{_FLAG_ENV}: {'enabled' if flag_on else 'disabled'}"
            )
        )

        if not force_drop:
            self.stdout.write("")
            self.stdout.write(
                "Dry-run complete. Pass --force-drop to actually drop "
                "the PG table once the audit is clean."
            )
            return

        # ----- drop pass -----
        if not flag_on:
            self.stderr.write(
                self.style.ERROR(
                    f"Refusing to drop: {_FLAG_ENV} is not set. "
                    "Enable it in compose / .env first."
                )
            )
            sys.exit(2)

        if not clean:
            self.stderr.write(
                self.style.ERROR(
                    "Refusing to drop: PG readers still query "
                    "tracer_observation_span. Migrate them first."
                )
            )
            sys.exit(2)

        if not yes:
            confirm = input(
                "About to DROP TABLE tracer_observation_span CASCADE. "
                "Type 'drop' to confirm: "
            )
            if confirm.strip().lower() != "drop":
                self.stdout.write("Aborted.")
                sys.exit(1)

        with connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS tracer_observation_span CASCADE")
        self.stdout.write(
            self.style.SUCCESS(
                "Dropped PG table tracer_observation_span. Remove the "
                "Django model class in a follow-up migration."
            )
        )
