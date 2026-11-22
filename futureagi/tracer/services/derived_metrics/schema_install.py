"""The ClickHouse schema Metrics needs: packaged files 009, 011 and 028-038.

``--apply`` is a step of every install path: bootstrap_install (the Helm
bootstrap Job and the Standalone bootstrap) and the Distributed stack's
metrics-schema-bootstrap job. It does nothing while METRICS_ENABLED is off, so
turning Metrics on later applies the files on the next bootstrap (Standalone:
``docker compose up -d`` recreates ``app``; Distributed: the job runs again
before the backend and workers start). The packaged runner
(tracer.services.clickhouse.v2.apply_schema) records a receipt per file and
skips files whose receipt matches, so every rerun is a no-op.

``--check`` only reads: with Metrics on, it exits 1 while the schema is
missing. Install paths pass ``--advisory``: a failure is reported and the exit
code is 0, so the rest of the product still starts. The API and the refresh worker run the same read-only probe
(:func:`ensure_installed`) and answer ``metric_schema_not_installed`` instead
of reporting "recalculating" indefinitely.

Hosted production applies its replicated schema through its own runbook; there
``--apply`` changes nothing.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Callable, Iterable

# Apply order. 028 adds columns to tracer_eval_logger_v2, which 011 creates;
# 011's materialized view writes the rollup 009 creates.
FILES = (
    "009_per_eval_rollup.sql",
    "011_eval_logger_v2.sql",
    "028_derived_metrics.sql",
    "029_derived_metric_uncertainty.sql",
    "030_derived_metric_view_refresh.sql",
    "031_metric_field_values.sql",
    "032_metric_bucket_totals.sql",
    "033_metric_unit_state.sql",
    "034_metric_source_dependencies.sql",
    "035_metric_bucket_retired_parts.sql",
    "036_list_current_root_facts.sql",
    "037_list_current_user_activity.sql",
    "038_metric_snapshot_sketches.sql",
)

# Every Metrics table carries project_id. The last two columns exist only once
# the later files ran: 029/030 re-project the derived_metrics view and 038 adds
# value_text. 009 and 011 are applied only because 028 alters 011's table.
# Metrics reads config_hash from whichever eval table CH25_EVAL_LOGGER_TABLE
# selects: the legacy table has it from its own DDL, and 028 adds it to
# tracer_eval_logger_v2 before creating its tables, so 028's markers imply it.
TABLES = (
    "derived_metric_inputs",
    "derived_metric_results",
    "derived_metric_commits",
    "metric_field_values",
    "metric_field_values_checkpoint",
    "metric_bucket_totals",
    "metric_bucket_totals_checkpoint",
    "metric_unit_state",
    "metric_source_dependencies",
    "metric_bucket_retired_parts",
    "list_current_root_facts",
    "list_current_members",
    "list_current_user_activity",
    "derived_metric_snapshot_units",
    "derived_metric_snapshot_buckets",
)
MARKERS = (
    *((table, "project_id") for table in TABLES),
    ("derived_metrics", "ci_interval"),
    ("derived_metric_inputs", "value_text"),
)

# Exit codes of --apply/--check (the runner's 2 and 3 pass through).
CAUSES = {
    1: "schema_missing",
    2: "drift_detected",
    3: "apply_aborted_or_clickhouse_unreachable",
}

MESSAGE = "Metrics schema not installed"
COMMAND = "python -m tracer.services.derived_metrics.schema_install --apply"
HINT = (
    f"Run `{COMMAND}` in the backend image (Standalone: `docker compose exec app "
    f"{COMMAND}`; Distributed: `docker compose run --rm metrics-schema-bootstrap "
    "--apply`), or set METRICS_ENABLED=0."
)
# A missing schema is checked again after this many seconds; a present one
# never is (nothing removes it while the process runs).
RECHECK_SECONDS = 30.0

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
_installed = False
_recheck_at = 0.0
_absent: tuple[str, ...] = ()


class MetricSchemaNotInstalled(Exception):
    code = "metric_schema_not_installed"

    def __init__(self, absent: Iterable[str] = ()):
        self.absent = tuple(absent)
        super().__init__(f"{MESSAGE}. {HINT}")


def probe_query() -> str:
    """One read of system.columns; names are constants, so no parameters."""
    tables = sorted({table for table, _ in MARKERS})
    columns = sorted({column for _, column in MARKERS})
    for name in (*tables, *columns):
        if not _IDENTIFIER.fullmatch(name):
            raise ValueError(f"not a plain identifier: {name!r}")
    return (
        "SELECT table, name FROM system.columns WHERE database = currentDatabase()"
        f" AND table IN ({', '.join(repr(t) for t in tables)})"
        f" AND name IN ({', '.join(repr(c) for c in columns)})"
    )


def missing(execute: Callable[[str], Iterable]) -> list[str]:
    """``table.column`` for every marker the current database lacks."""
    found = {(str(table), str(column)) for table, column in execute(probe_query())}
    return [
        f"{table}.{column}" for table, column in MARKERS if (table, column) not in found
    ]


def _connect():
    from clickhouse_driver import Client

    from tracer.services.clickhouse.v2 import get_v2_config

    config = get_v2_config()
    return Client(
        host=config["host"],
        port=config["tcp_port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        connect_timeout=5,
        send_receive_timeout=30,
    )


def _probe() -> list[str]:
    client = _connect()
    try:
        return missing(client.execute)
    finally:
        client.disconnect()


def ensure_installed(
    execute: Callable[[str], Iterable] | None = None,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Raise MetricSchemaNotInstalled while the Metrics schema is missing.

    ``execute`` runs the probe on an open connection; without one a short-lived
    connection from get_v2_config() is used. A probe that cannot reach
    ClickHouse raises its own error and caches nothing."""
    global _installed, _recheck_at, _absent
    if _installed:
        return
    if clock() < _recheck_at:
        raise MetricSchemaNotInstalled(_absent)
    absent = missing(execute) if execute is not None else _probe()
    if absent:
        _absent, _recheck_at = tuple(absent), clock() + RECHECK_SECONDS
        raise MetricSchemaNotInstalled(absent)
    _installed = True


def _say(message: str) -> None:
    print(f"[metrics-schema] {message}", flush=True)


def _apply() -> int:
    from tracer.services.clickhouse.v2 import apply_schema, get_v2_config

    config = get_v2_config()
    argv = [
        "--ch-host",
        str(config["host"]),
        "--ch-http-port",
        str(config["http_port"]),
        "--ch-user",
        str(config["user"]),
        "--ch-database",
        str(config["database"]),
        "--files",
        *FILES,
    ]
    _say(
        f"applying {FILES[0]}, {FILES[1]} and {FILES[2]} to {FILES[-1]} on "
        f"{config['host']}:{config['http_port']}/{config['database']}"
    )
    # The runner reads its password from CH_PASSWORD, never from argv.
    saved = {
        name: os.environ.get(name) for name in ("CH_PASSWORD", "FI_MIGRATION_USER")
    }
    os.environ["CH_PASSWORD"] = str(config["password"] or "")
    os.environ.setdefault("FI_MIGRATION_USER", "metrics-schema-install")
    try:
        code = apply_schema.main(argv)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    if code == 2:
        _say(
            "a Metrics schema file changed after it was applied (drift_detected "
            "above names it). Review that change with the ClickHouse owner; "
            "set METRICS_ENABLED=0 to start without Metrics meanwhile."
        )
    elif code == 3:
        _say(
            "ClickHouse rejected a Metrics schema statement (apply_aborted above "
            "names the file). Check that the ClickHouse user may change the "
            "schema of this database."
        )
    elif code:
        _say("the schema runner refused its options (see above)")
    return code


def _check() -> int:
    try:
        absent = _probe()
    except Exception as exc:
        # Driver messages can carry connection details; the type is enough.
        _say(f"could not read system.columns ({type(exc).__name__})")
        return 3
    if absent:
        _say(f"{MESSAGE}: missing {', '.join(absent)}. {HINT}")
        return 1
    _say("Metrics schema installed")
    return 0


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
    parser = argparse.ArgumentParser(
        prog="schema_install",
        description="Apply or check the ClickHouse schema Metrics needs.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true", help="apply missing files")
    mode.add_argument("--check", action="store_true", help="only verify (read-only)")
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="report a failure but exit 0, so startup continues without Metrics",
    )
    args = parser.parse_args(argv)
    try:
        code = _run(args)
    except Exception as exc:
        if not args.advisory:
            raise
        # Driver messages can carry connection details; the type is enough.
        _say(f"the Metrics schema step failed ({type(exc).__name__})")
        code = 3
    if code:
        # Never silent: one ERROR record names the step and the cause, whether
        # or not startup continues.
        import structlog

        structlog.get_logger(__name__).error(
            "metrics_schema_step_failed",
            step="apply" if args.apply else "check",
            cause=CAUSES.get(code, f"exit_{code}"),
            exit_code=code,
            advisory=args.advisory,
        )
    if code and args.advisory:
        _say(
            "continuing without Metrics: its pages and refreshes answer "
            f"metric_schema_not_installed until this is fixed (exit {code})"
        )
        return 0
    return code


def _run(args: argparse.Namespace) -> int:
    from tracer.services.clickhouse.v2.schema_topology import is_hosted_production
    from tracer.services.derived_metrics.feature_flag import metrics_enabled

    if not metrics_enabled():
        _say("METRICS_ENABLED is off; nothing to do")
        return 0
    if args.apply:
        if is_hosted_production(
            os.environ.get("ENV_TYPE", ""), os.environ.get("CLOUD_DEPLOYMENT", "")
        ):
            _say("hosted production applies its replicated schema separately; skipped")
            return 0
        code = _apply()
        if code:
            return code
    return _check()


if __name__ == "__main__":
    sys.exit(main())
