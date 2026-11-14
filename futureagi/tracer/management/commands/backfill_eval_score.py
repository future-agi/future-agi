"""Re-materialize ``usage_apicalllog.eval_score`` on rows already on disk.

Sequence: DROP INDEX, MODIFY, ADD INDEX, MATERIALIZE COLUMN, MATERIALIZE INDEX.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

TABLE = "usage_apicalllog"
COLUMN = "eval_score"
INDEX = "idx_eval_score"
INDEX_DEF = f"{INDEX} {COLUMN} TYPE minmax GRANULARITY 1"

# Structured-output rows whose stored eval_score disagrees with the expression.
_AFFECTED_COUNT = """
SELECT count()
FROM {table}
WHERE _peerdb_is_deleted = 0
  AND {predicate}
  AND {column} != {expr}
"""

# system.parts spans every database on the server, so scope it to this one.
_PARTITIONS = """
SELECT DISTINCT partition
FROM system.parts
WHERE table = '{table}' AND database = currentDatabase() AND active
ORDER BY partition DESC
"""

# position() rather than LIKE: the driver runs printf substitution over every
# query, so a literal % here is read as a format spec and raises.
_IN_FLIGHT = """
SELECT count()
FROM system.mutations
WHERE table = '{table}' AND NOT is_done AND position(command, '{column}') > 0
"""


def rebuild_statements(table: str = TABLE) -> list[str]:
    """DDL carrying a new eval_score expression onto an existing table."""
    from tracer.services.clickhouse.schema import CH_EVAL_SCORE_EXPR

    return [
        f"ALTER TABLE {table} DROP INDEX IF EXISTS {INDEX}",
        f"ALTER TABLE {table} MODIFY COLUMN {COLUMN} Float64 MATERIALIZED {CH_EVAL_SCORE_EXPR}",
        f"ALTER TABLE {table} ADD INDEX IF NOT EXISTS {INDEX_DEF}",
    ]


def materialize_statements(table: str = TABLE, partition: str | None = None) -> list[str]:
    """Mutations that rewrite the stored column and rebuild its index."""
    scope = f" IN PARTITION {partition}" if partition is not None else ""
    return [
        f"ALTER TABLE {table} MATERIALIZE COLUMN {COLUMN}{scope}",
        f"ALTER TABLE {table} MATERIALIZE INDEX {INDEX}{scope}",
    ]


class Command(BaseCommand):
    help = "Re-materialize usage_apicalllog.eval_score so existing rows pick up the current extraction."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the affected row count without submitting a mutation.",
        )
        parser.add_argument(
            "--no-confirm",
            action="store_true",
            help="Skip the interactive prompt (for CI/CD and deploy automation).",
        )
        parser.add_argument(
            "--whole-table",
            action="store_true",
            help="Materialize the whole table in one mutation instead of per partition.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Rebuild and re-materialize unconditionally. The stale count "
                "only looks at structured outputs, so use this to repair a "
                "stale skip index or drift on any other output shape."
            ),
        )

    def handle(self, *args, **opts):
        from tracer.services.clickhouse.client import get_clickhouse_client
        from tracer.services.clickhouse.eval_expressions import (
            eval_has_structured_score,
        )
        from tracer.services.clickhouse.schema import (
            CH_EVAL_SCORE_EXPR,
            EVAL_OUTPUT_JSON_ARGS,
        )

        ch = get_clickhouse_client()

        predicate = eval_has_structured_score(EVAL_OUTPUT_JSON_ARGS)
        affected = self._scalar(
            ch,
            _AFFECTED_COUNT.format(
                table=TABLE, column=COLUMN, expr=CH_EVAL_SCORE_EXPR, predicate=predicate
            ),
        )
        self.stdout.write(f"rows with a stale {COLUMN}: {affected}")

        if affected == 0 and not opts["force"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{COLUMN} is already up to date, nothing to do. "
                    "Re-run with --force to rebuild anyway."
                )
            )
            return

        if opts["dry_run"]:
            self.stdout.write("--dry-run: no mutation submitted.")
            return

        in_flight = self._scalar(ch, _IN_FLIGHT.format(table=TABLE, column=COLUMN))
        if in_flight:
            raise CommandError(
                f"{in_flight} {COLUMN} mutation(s) already running on {TABLE}. "
                "Wait for them to finish (see system.mutations) before re-running."
            )

        if not opts["no_confirm"]:
            scope = f"{affected} rows" if affected else "every row (forced)"
            answer = input(f"Re-materialize {COLUMN} for {scope}? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                self.stdout.write("aborted.")
                return

        for sql in rebuild_statements():
            ch.execute(sql)
            self.stdout.write(f"  {sql}")

        if opts["whole_table"]:
            targets = [None]
        else:
            targets = [row[0] for row in ch.execute(_PARTITIONS.format(table=TABLE))]
            self.stdout.write(f"materializing across {len(targets)} partition(s)")

        for target in targets:
            for sql in materialize_statements(partition=target):
                ch.execute(sql)
                self.stdout.write(f"  submitted: {sql}")

        self.stdout.write(
            self.style.SUCCESS(
                "mutation(s) submitted. They run asynchronously, so poll "
                f"system.mutations for table='{TABLE}', then re-run with "
                "--dry-run to confirm the affected count is 0."
            )
        )

    @staticmethod
    def _scalar(ch, sql) -> int:
        rows = ch.execute(sql)
        if not rows:
            return 0
        first = rows[0]
        return int(first[0] if isinstance(first, (list, tuple)) else first)
