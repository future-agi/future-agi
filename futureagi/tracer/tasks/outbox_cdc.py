"""Temporal activities for the in-app outbox CDC (``FI_CDC_MODE=outbox``).

Both activities connect to Postgres directly (PG_HOST/PG_PORT), never through
PgBouncer: transaction pooling would break the session advisory lock that
serializes CDC writers.

Rollback or a switch to PeerDB: never just flip FI_CDC_MODE. Capture triggers
without a drain grow ``fi_cdc_outbox`` without bound. Boot once in the new
mode (``oss_outbox_cdc.ensure_installed()`` then drops the triggers, the
outbox tables and both schedules) or run
``python -m tracer.services.clickhouse.oss_outbox_cdc uninstall --apply``.
"""

from __future__ import annotations

import functools
from contextlib import contextmanager

import structlog

from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)

DRAIN_BUDGET_S = 8.0
# The drain runs every 10 s; this much lag means it is stuck or failing.
LAG_ERROR_SECONDS = 600
BACKLOG_WARNING_ROWS = 50_000


@functools.cache
def _config():
    # Parsing validates the packaged DDL; do it once per process, not per tick.
    from tracer.services.clickhouse import oss_outbox_cdc as cdc

    return cdc.load_config()


@contextmanager
def _connections():
    from tracer.services.clickhouse import oss_cdc_bootstrap as core
    from tracer.services.clickhouse import oss_outbox_cdc as cdc

    config = _config()
    pg, ch = cdc.connect(config)
    try:
        yield (
            config,
            pg,
            ch,
            core.landing_tables(include_usage_schema=config.include_usage_schema),
        )
    finally:
        pg.close()
        ch.close()


def _enabled() -> bool:
    from tracer.services.clickhouse import oss_outbox_cdc as cdc

    return cdc.cdc_mode() == "outbox"


def _report(event: str, result: dict) -> None:
    problems = {
        key: result[key]
        for key in (
            "errors",
            "rearmed",
            "rearm_failed",
            "drift_errors",
            "added_columns",
        )
        if result.get(key)
    }
    parked = {k: v for k, v in result.items() if k.endswith(".parked")}
    if problems or parked:
        # Repairs (re-armed capture, added columns) and failures both need a look.
        logger.error("outbox_cdc_attention", activity=event, **problems, **parked)
    if result.get("lag_seconds", 0) > LAG_ERROR_SECONDS:
        logger.error(
            "outbox_cdc_lagging",
            lag_seconds=result["lag_seconds"],
            outbox_depth=result.get("outbox_depth"),
        )
    elif result.get("outbox_depth", 0) > BACKLOG_WARNING_ROWS:
        logger.warning("outbox_cdc_backlog", outbox_depth=result["outbox_depth"])


@temporal_activity(time_limit=60, queue="tasks_s", max_retries=0)
def drain_outbox_cdc():
    """Move captured PG changes into the ClickHouse landing tables (every 10 s).

    ``max_retries=0``: the next tick is the retry. The drain is idempotent,
    so a tick that fails midway is replayed with higher versions.
    """
    if not _enabled():
        return {"disabled": True}
    from tracer.services.clickhouse import oss_outbox_cdc as cdc

    with _connections() as (config, pg, ch, tables):
        result = cdc.drain(
            pg, ch, tables=tables, source=config.source, budget_s=DRAIN_BUDGET_S
        )
    _report("outbox_cdc_drain", result)
    return result


@temporal_activity(time_limit=3600, queue="tasks_l", max_retries=0)
def reconcile_outbox_cdc():
    """Id parity for requested tables and a daily sweep of every table.

    Covers TRUNCATE, restores, writes with triggers bypassed
    (``session_replication_role=replica``) and parked keys due a retry.
    """
    if not _enabled():
        return {"disabled": True}
    from tracer.services.clickhouse import oss_outbox_cdc as cdc

    with _connections() as (_, pg, ch, tables):
        result = cdc.reconcile(pg, ch, tables=tables)
    _report("outbox_cdc_reconcile", result)
    return result
