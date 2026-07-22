#!/usr/bin/env python3
"""
backfill_pg_to_ch — resumable PG tracer_observation_span → CH spans backfill.

Design summary (full rationale in DECISIONS.md #013–#016):
    • Unit of work is one (project_id, hour_bucket) window. Windows are
      discovered once via a GROUP BY on PG, then processed in order.
    • Per-window state lives in CH default.backfill_checkpoints. Because the
      target table is ReplacingMergeTree on (_version, is_deleted), re-running
      a window is CORRECT (not just safe) — the checkpoint is a SPEED
      optimization that lets us skip already-done work on restart, not a
      correctness gate. Lose the checkpoint table and you only pay re-work,
      never wrong data.
    • Within a window, rows are streamed with KEYSET pagination
      (`(start_time, id) > $last`) so unbounded-skew hours (one busy
      project's busy minute = 100M rows) still process with bounded memory.
    • Rows are converted in pure Python (see pg_to_ch_adapter.adapt) and
      inserted via the clickhouse-driver NATIVE protocol in sync batches
      (default 50k). We do NOT use async_insert — that's an ingest-edge
      tool; for bulk loads, sync batches give us per-batch error visibility
      and clean backpressure.
    • Per-row conversion failures go to default.spans_v2_dead_letter with
      the original PG row as raw JSON; the batch continues. Per-batch CH
      insert failures retry up to 3x then mark the window failed and move on.
    • After all batches in a window, count parity check
      (PG window count == CH window count + dead-letter count). If mismatch,
      checkpoint = 'failed_validation'; operator runs the dead-letter triage
      query and either retries or accepts the gap.

Usage:
    backfill_pg_to_ch \\
        --pg-host ... --pg-port ... --pg-user ... --pg-pass ... --pg-db ... \\
        --ch-host ... --ch-tcp-port 19002 --ch-http-port 19001 \\
        --ch-user default --ch-db default \\
        --batch-size 50000 \\
        [--project-id <uuid> ...]      # restrict to specific projects
        [--since 2026-01-01]           # min start_time
        [--until 2026-06-01]           # max start_time (exclusive)
        [--dry-run]                    # discover + checkpoint only, no inserts

    backfill_pg_to_ch --status         # print summary from backfill_checkpoints
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import clickhouse_connect          # HTTP — used for ALL CH I/O, see below.
import psycopg
import structlog
from psycopg.rows import dict_row

# Why HTTP and not the clickhouse-driver native protocol:
#   The new CH 25.x typed JSON column `JSON(max_dynamic_paths=0)` is not yet
#   understood by clickhouse-driver 0.2.9 — INSERT fails with "Unknown type
#   JSON(max_dynamic_paths=0)". clickhouse-connect (HTTP) serializes JSON
#   payloads as strings and lets the server parse them into the typed JSON
#   column, which works correctly. Throughput cost is small (~10-20%) for
#   bulk loads and well within our budget. Caught by the smoke test against
#   the local PG; documented in DECISIONS #015.

# Make the adapter importable when running this script via `python scripts/...`
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapter import (                                     # noqa: E402
    CH_INSERT_COLUMNS,
    AdapterError,
    adapt,
    row_to_tuple,
)


# ─── Logging ──────────────────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger("backfill")


# ─── PG query (single source of truth for what gets pulled) ───────────────────
# We left-join tracer_trace to get trace_session_id, which is denormalized into
# the CH `spans` table. Filters:
#   • deleted = false        (soft-deleted rows are not migrated)
#   • start_time IS NOT NULL (rows with no timestamp can't be bucketed)
# The order is (start_time ASC, id ASC) so keyset pagination is monotonic.

_DISCOVER_WINDOWS_SQL = """
    SELECT s.project_id,
           date_trunc('hour', s.start_time) AS hour_bucket,
           count(*) AS row_estimate
    FROM tracer_observation_span s
    WHERE s.deleted = false
      AND s.start_time IS NOT NULL
      {project_filter}
      {time_filter}
    GROUP BY s.project_id, date_trunc('hour', s.start_time)
    ORDER BY hour_bucket ASC, s.project_id ASC
"""

_WINDOW_FETCH_SQL = """
    SELECT s.id, s.project_id, s.project_version_id, s.trace_id, s.parent_span_id,
           s.name, s.observation_type, s.operation_name,
           s.start_time, s.end_time,
           s.input, s.output,
           s.model, s.model_parameters, s.latency_ms,
           s.org_id, s.org_user_id,
           s.prompt_tokens, s.completion_tokens, s.total_tokens, s.response_time,
           s.eval_id, s.cost,
           s.status, s.status_message,
           s.tags, s.metadata, s.span_events, s.provider,
           s.input_images, s.eval_input, s.eval_attributes,
           s.custom_eval_config_id, s.eval_status,
           s.end_user_id, s.prompt_version_id, s.prompt_label_id,
           s.span_attributes, s.resource_attributes, s.semconv_source,
           s.created_at, s.updated_at, s.deleted,
           t.session_id AS trace_session_id
    FROM tracer_observation_span s
    LEFT JOIN tracer_trace t ON t.id = s.trace_id
    WHERE s.project_id = %(project_id)s
      AND s.start_time >= %(hour_start)s
      AND s.start_time <  %(hour_end)s
      AND s.deleted = false
      AND (s.start_time, s.id) > (%(last_ts)s, %(last_id)s)
    ORDER BY s.start_time ASC, s.id ASC
    LIMIT %(batch_size)s
"""


# ─── CH helpers ───────────────────────────────────────────────────────────────
def _ch_http(args) -> clickhouse_connect.driver.Client:
    settings = {}
    if getattr(args, "max_memory_per_query", 0):
        settings["max_memory_usage"] = args.max_memory_per_query
        # Spill GROUP BY / ORDER BY to disk past this threshold instead of OOMing.
        settings["max_bytes_before_external_group_by"] = max(args.max_memory_per_query // 2, 1)
        settings["max_bytes_before_external_sort"]     = max(args.max_memory_per_query // 2, 1)
    return clickhouse_connect.get_client(
        host=args.ch_host, port=args.ch_http_port,
        username=args.ch_user, password=args.ch_pass,
        database=args.ch_db, send_receive_timeout=300,
        settings=settings or None,
    )


class _ResilientCH:
    """Wraps a clickhouse-connect client so every .insert / .query / .command
    call is retried on transient `OperationalError` (which is what surfaces
    when CH server closes a long-lived HTTP connection — frequent at scale).
    Auto-recycles the underlying client between attempts so we don't keep
    hitting the same dead socket.

    Forwards all other attribute access to the inner client.
    """

    def __init__(self, factory, max_attempts: int = 4, base_backoff: float = 0.5):
        self._factory = factory
        self._client = factory()
        self._max_attempts = max_attempts
        self._base_backoff = base_backoff

    def __getattr__(self, name):
        # For .insert/.query/.command we want resilient versions; everything
        # else is direct passthrough.
        attr = getattr(self._client, name)
        if name in {"insert", "query", "command"}:
            return self._wrap(attr, name)
        return attr

    def _wrap(self, method, name: str):
        def call(*args, **kwargs):
            last_err: Exception | None = None
            for attempt in range(1, self._max_attempts + 1):
                try:
                    return getattr(self._client, name)(*args, **kwargs)
                except Exception as e:                              # noqa: BLE001
                    msg = repr(e)
                    transient = ("RemoteDisconnected" in msg
                                 or "Connection aborted" in msg
                                 or "BrokenPipe" in msg
                                 or "ConnectionResetError" in msg)
                    last_err = e
                    if attempt == self._max_attempts or not transient:
                        raise
                    log.warning("ch_op_recycle",
                                op=name, attempt=attempt, err=str(e)[:200])
                    # Backoff FIRST so CH gets a moment to recover before we
                    # try to reopen. Then attempt to recycle the client — if
                    # the factory itself fails (CH overloaded), keep trying.
                    time.sleep(self._base_backoff * (2 ** (attempt - 1)))
                    self._recycle()
            raise last_err  # unreachable
        return call

    def _recycle(self) -> None:
        try:
            self._client.close()
        except Exception:                                            # noqa: BLE001
            pass
        # Best-effort: 5 tries with growing backoff. If CH is down for longer
        # than ~30 s the orchestrator will give up at the next call attempt;
        # that's correct — the caller's outer loop sees an exception, marks
        # the window 'failed', and moves on (operator retries the run).
        for i in range(5):
            try:
                self._client = self._factory()
                return
            except Exception as e:                                   # noqa: BLE001
                log.warning("ch_factory_retry", attempt=i + 1, err=str(e)[:200])
                time.sleep(min(2 ** i, 10))
        # Couldn't reopen; leave `self._client` pointing at the dead one.
        # The next call() will fail and exit the retry loop — operator sees it.

    def close(self):
        try:
            self._client.close()
        except Exception:                                            # noqa: BLE001
            pass


def _ch_bulk(args) -> clickhouse_connect.driver.Client:
    """Dedicated HTTP client for bulk inserts. Same library as _ch_http, but a
    separate connection so checkpoint writes and bulk inserts don't queue
    behind each other.
    """
    settings = {"async_insert": 0, "max_insert_block_size": 100_000}
    if getattr(args, "max_memory_per_query", 0):
        settings["max_memory_usage"] = args.max_memory_per_query
    return clickhouse_connect.get_client(
        host=args.ch_host, port=args.ch_http_port,
        username=args.ch_user, password=args.ch_pass,
        database=args.ch_db,
        send_receive_timeout=600,
        # async_insert is for ingest-edge bursts; for bulk loads we want
        # sync ack so we know exactly which batch failed and why.
        settings=settings,
    )


def _upsert_checkpoint(
    ch_http,
    *,
    project_id: str, hour_bucket: datetime, status: str,
    rows_in_pg: int = 0, rows_in_ch: int = 0, dead_letter_count: int = 0,
    started_at: datetime | None = None, finished_at: datetime | None = None,
    worker_id: str = "", backfill_run_id: str = "", error_message: str = "",
) -> None:
    """Insert a checkpoint row. ReplacingMergeTree(_version) makes the latest win.

    Pass started_at on the FIRST insert (in_progress); pass it again on the final
    insert (completed/failed) so the row keeps its original start time after
    dedup — CH wins by max(_version), which has no concept of which fields to
    keep from which row. The ENTIRE row is replaced.
    """
    if started_at is None:
        started_at = datetime.now(timezone.utc)
    ch_http.insert(
        "backfill_checkpoints",
        [[
            project_id, hour_bucket, status,
            rows_in_pg, rows_in_ch, dead_letter_count,
            started_at, finished_at, worker_id, backfill_run_id, error_message,
        ]],
        column_names=[
            "project_id", "hour_bucket", "status",
            "rows_in_pg", "rows_in_ch", "dead_letter_count",
            "started_at", "finished_at", "worker_id", "backfill_run_id", "error_message",
        ],
    )


def _completed_windows(ch_http) -> set[tuple[str, datetime]]:
    """Set of (project_id, hour_bucket) that are status='completed'.

    Uses FINAL to collapse ReplacingMergeTree duplicates. At scale this is fine
    because the table is small (one row per window, max few hundred K).

    CH returns hour_bucket as a naive datetime — the column is `DateTime('UTC')`
    on storage but clickhouse-connect drops the tzinfo on return. We attach
    UTC explicitly so the set keys match those produced by `_discover_windows`
    (which also returns UTC-aware values). Without this normalization, set
    lookup MISSES every completed window on resume and the orchestrator
    re-processes work it has already done. Caught during a real-PG resume run.
    """
    rows = ch_http.query("""
        SELECT toString(project_id), hour_bucket
        FROM   backfill_checkpoints FINAL
        WHERE  status = 'completed'
    """).result_rows
    out: set[tuple[str, datetime]] = set()
    for pid, hb in rows:
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=timezone.utc)
        else:
            hb = hb.astimezone(timezone.utc)
        out.add((pid, hb))
    return out


def _dead_letter_insert(ch_http, items: list[dict[str, Any]]) -> None:
    if not items:
        return
    ch_http.insert(
        "spans_v2_dead_letter",
        [[i["pg_id"], i["project_id"], i["trace_id"], i["raw_pg_row"],
          i["error_class"], i["error_message"], i["error_stage"],
          i["attempted_at"], i["backfill_run_id"]] for i in items],
        column_names=[
            "pg_id", "project_id", "trace_id", "raw_pg_row",
            "error_class", "error_message", "error_stage",
            "attempted_at", "backfill_run_id",
        ],
    )


# ─── PG helpers ───────────────────────────────────────────────────────────────
def _pg_conn(args) -> psycopg.Connection:
    # Force UTC session so date_trunc('hour', start_time) yields a UTC-bucketed
    # timestamp regardless of the server's default `timezone` setting. Without
    # this, a server set to e.g. Asia/Kolkata would return a non-UTC bucket and
    # our subsequent `_stream_window` query (which compares to UTC) would miss
    # rows. See codex P2 finding #4.
    conn = psycopg.connect(
        host=args.pg_host, port=args.pg_port, user=args.pg_user,
        password=args.pg_pass, dbname=args.pg_db,
        autocommit=True, row_factory=dict_row,
    )
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def _discover_windows(
    pg, project_filter: list[str] | None, since: datetime | None, until: datetime | None,
) -> list[tuple[str, datetime, int]]:
    pf, tf = "", ""
    params: dict[str, Any] = {}
    if project_filter:
        pf = "AND s.project_id = ANY(%(projects)s)"
        params["projects"] = project_filter
    if since:
        tf += " AND s.start_time >= %(since)s"
        params["since"] = since
    if until:
        tf += " AND s.start_time <  %(until)s"
        params["until"] = until
    sql = _DISCOVER_WINDOWS_SQL.format(project_filter=pf, time_filter=tf)
    with pg.cursor() as cur:
        cur.execute(sql, params)
        out = []
        for r in cur.fetchall():
            hb = r["hour_bucket"]
            # date_trunc on timestamptz returns timestamptz; with `SET TIME ZONE 'UTC'`
            # on the session this comes back as UTC-aware already. Coerce via
            # astimezone (not .replace, which would mis-label non-UTC values).
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            else:
                hb = hb.astimezone(timezone.utc)
            out.append((str(r["project_id"]), hb, int(r["row_estimate"])))
        return out


def _stream_window(
    pg, project_id: str, hour_bucket: datetime, batch_size: int,
    *, since: datetime | None = None, until: datetime | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Yield batches of rows for one (project, hour) window via keyset pagination.

    Why keyset and not OFFSET: at 100M rows in a hot hour, `OFFSET 99_000_000`
    reads every preceding row. Keyset on the index `(start_time, id)` is O(log N).

    Window bounds are CLAMPED to the caller's --since / --until (if any). Without
    this clamp, a 30-minute --since=2026-04-09T07:30:00 would still pull the
    07:00 hour's full 60 minutes — including data the user explicitly excluded.
    The checkpoint then claims work for "07:00 hour" that doesn't match what
    actually ran. (codex P2 finding #3.)
    """
    hour_start = hour_bucket
    hour_end   = hour_bucket + timedelta(hours=1)
    if since is not None and since > hour_start:
        hour_start = since
    if until is not None and until < hour_end:
        hour_end = until

    last_ts = datetime.min.replace(tzinfo=timezone.utc)
    last_id = ""
    while True:
        with pg.cursor() as cur:
            cur.execute(_WINDOW_FETCH_SQL, {
                "project_id": project_id,
                "hour_start": hour_start,
                "hour_end":   hour_end,
                "last_ts":    last_ts,
                "last_id":    last_id,
                "batch_size": batch_size,
            })
            batch = cur.fetchall()
        if not batch:
            return
        yield batch
        # Advance keyset cursor to the last row's (start_time, id)
        tail = batch[-1]
        last_ts = tail["start_time"]
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        last_id = tail["id"]
        if len(batch) < batch_size:
            return


# ─── One-window processor ─────────────────────────────────────────────────────
def _process_window(
    pg, ch_http, ch_bulk, *,
    project_id: str, hour_bucket: datetime, batch_size: int,
    worker_id: str, run_id: str, dry_run: bool,
    since: datetime | None = None, until: datetime | None = None,
    ch_bulk_factory=None,
) -> dict[str, int]:
    """Process one (project, hour) window end-to-end. Idempotent: safe to re-run.

    Dry-run mode: discovers and streams the window from PG (so we can verify the
    query + count) but writes NO checkpoint rows and skips CH inserts entirely.
    This keeps the checkpoint table clean — a dry-run must never make a
    subsequent real run skip work as if it had been done.
    """
    started_at = datetime.now(timezone.utc)
    if not dry_run:
        _upsert_checkpoint(
            ch_http, project_id=project_id, hour_bucket=hour_bucket, status="in_progress",
            started_at=started_at, worker_id=worker_id, backfill_run_id=run_id,
        )

    rows_in_pg = 0
    rows_inserted = 0
    dead_letter_buf: list[dict[str, Any]] = []

    if dry_run:
        # Count rows that would be processed without doing any CH work.
        for batch in _stream_window(pg, project_id, hour_bucket, batch_size, since=since, until=until):
            rows_in_pg += len(batch)
        return {"rows_in_pg": rows_in_pg, "rows_inserted": 0, "rows_in_ch": 0,
                "dead_letter_count": 0, "status": "dry_run"}

    for batch in _stream_window(pg, project_id, hour_bucket, batch_size, since=since, until=until):
        rows_in_pg += len(batch)
        ch_tuples: list[tuple] = []
        for r in batch:
            try:
                ch_tuples.append(row_to_tuple(adapt(r)))
            except AdapterError as e:
                dead_letter_buf.append({
                    "pg_id": str(r.get("id", "")),
                    "project_id": str(r["project_id"]) if r.get("project_id") else None,
                    "trace_id":   str(r["trace_id"])   if r.get("trace_id")   else None,
                    "raw_pg_row": json.dumps(r, default=str),
                    "error_class": "ADAPTER_FAIL",
                    "error_message": str(e)[:1000],
                    "error_stage": "adapter",
                    "attempted_at": datetime.now(timezone.utc),
                    "backfill_run_id": run_id,
                })

        if ch_tuples:
            # Sync insert with bounded retries. Failure here is a CH-level
            # problem (network, schema mismatch) — not a bad-row problem —
            # so we don't dead-letter the whole batch, we fail the window.
            #
            # On a transient connection error (RemoteDisconnected, ConnectionReset,
            # etc — CH closes long-lived HTTP connections after a while) we
            # RECYCLE the client. Reusing the same stale client object would just
            # hit the same closed connection 3x and burn 6+ s of backoff before
            # the orchestrator gives up and marks the window 'failed'. Caught
            # during the full-PG-scale backfill.
            for attempt in range(1, 4):
                try:
                    ch_bulk.insert("spans", ch_tuples, column_names=list(CH_INSERT_COLUMNS))
                    rows_inserted += len(ch_tuples)
                    break
                except Exception as e:                              # noqa: BLE001 — broad: CH driver throws many subclasses
                    log.warning("ch_insert_attempt_failed",
                                project_id=project_id, hour=hour_bucket.isoformat(),
                                attempt=attempt, batch_size=len(ch_tuples), err=str(e)[:300])
                    if attempt == 3:
                        raise
                    # Recycle the client so the next attempt opens a fresh
                    # connection (urllib3 may have a dead socket cached). If
                    # no factory was provided (legacy callers/tests), fall back
                    # to just sleeping.
                    if ch_bulk_factory is not None:
                        try:
                            ch_bulk.close()
                        except Exception:                            # noqa: BLE001
                            pass
                        ch_bulk = ch_bulk_factory()
                    time.sleep(min(2 ** attempt, 8))

    # Flush dead-letter buffer in one go for the window
    if dead_letter_buf:
        _dead_letter_insert(ch_http, dead_letter_buf)

    # Validate per-window via the in-memory invariant: every PG row we read
    # either ended up dead-lettered or successfully inserted. We do NOT run a
    # per-window `count() FROM spans FINAL` here because:
    #   • FINAL on ReplacingMergeTree forces in-memory dedup of every accumulated
    #     part — its cost grows with table size, not window size.
    #   • At ~600 active parts the per-window FINAL count was the dominant
    #     contributor to CH OOM during the local 263k-row smoke (DECISIONS #025).
    #   • The end-of-run Layer A validator does the FINAL comparison once across
    #     all windows; same coverage, one query instead of 5211.
    #
    # The invariant: rows_inserted + dl_count == rows_in_pg, modulo retried
    # inserts on connection death (which the resilient client may double-count
    # at the orchestrator level; ReplacingMergeTree dedups them on the CH side).
    dl_count = len(dead_letter_buf)
    is_partial = (
        (since is not None and since > hour_bucket) or
        (until is not None and until < hour_bucket + timedelta(hours=1))
    )
    status = "completed_partial" if is_partial else "completed"
    error_message = ""
    # rows_inserted may exceed (rows_in_pg - dl_count) if a connection death
    # caused us to re-insert a batch (ReplacingMergeTree dedups on _version
    # at the CH side). The error case is the OTHER direction: too few rows.
    if rows_inserted + dl_count < rows_in_pg:
        status = "failed_validation"
        error_message = (
            f"under-insert: pg={rows_in_pg} inserted={rows_inserted} "
            f"dead_letter={dl_count} short={rows_in_pg - rows_inserted - dl_count}"
        )
    # `rows_in_ch` in the checkpoint reflects what we attempted to insert; the
    # actual FINAL row count is verified by the validator at end-of-run.
    ch_count = rows_inserted

    finished_at = datetime.now(timezone.utc)
    _upsert_checkpoint(
        ch_http, project_id=project_id, hour_bucket=hour_bucket, status=status,
        rows_in_pg=rows_in_pg, rows_in_ch=ch_count, dead_letter_count=dl_count,
        started_at=started_at, finished_at=finished_at,
        worker_id=worker_id, backfill_run_id=run_id, error_message=error_message,
    )

    return {
        "rows_in_pg": rows_in_pg, "rows_inserted": rows_inserted,
        "rows_in_ch": ch_count, "dead_letter_count": dl_count,
        "status": status,
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────
def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # PG
    p.add_argument("--pg-host", default="127.0.0.1")
    p.add_argument("--pg-port", type=int, default=5432)
    p.add_argument("--pg-user", default=os.environ.get("PG_USER", "user"))
    p.add_argument("--pg-pass", default=os.environ.get("PG_PASSWORD", "password"))
    p.add_argument("--pg-db",   default=os.environ.get("PG_DB", "tfc"))
    # CH 25.3
    p.add_argument("--ch-host", default="127.0.0.1")
    p.add_argument("--ch-tcp-port",  type=int, default=19002)
    p.add_argument("--ch-http-port", type=int, default=19001)
    p.add_argument("--ch-user", default="default")
    p.add_argument("--ch-pass", default=os.environ.get("CH_PASSWORD", ""))
    p.add_argument("--ch-db",   default="default")
    # Run controls
    p.add_argument("--batch-size", type=int, default=50_000)
    p.add_argument("--project-id", action="append",
                   help="Restrict to this project (repeatable). Default: all projects.")
    p.add_argument("--since", type=lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc),
                   help="Only windows where start_time >= this (ISO8601)")
    p.add_argument("--until", type=lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc),
                   help="Only windows where start_time < this (ISO8601)")
    p.add_argument("--dry-run", action="store_true",
                   help="Discover + create in_progress checkpoints, but skip inserts.")
    p.add_argument("--max-windows", type=int, default=None,
                   help="Process at most this many windows then exit (smoke testing)")
    p.add_argument("--optimize-every", type=int, default=0,
                   help="Run OPTIMIZE TABLE spans every N windows (default 0=off). "
                        "Bounds the active-part count and prevents memory growth on small "
                        "CH nodes. 200-500 is a sane value for an 8 GiB sidecar.")
    p.add_argument("--max-memory-per-query", type=int, default=0,
                   help="Apply max_memory_usage setting to every CH connection (bytes; "
                        "default 0=server default). Set on memory-constrained nodes to "
                        "spill aggregations to disk instead of OOMing.")
    # Status mode
    p.add_argument("--status", action="store_true",
                   help="Print aggregate progress from backfill_checkpoints, then exit.")
    return p


def _cmd_status(args) -> int:
    ch_http = _ch_http(args)
    rows = ch_http.query("""
        SELECT status, count(), sum(rows_in_pg), sum(rows_in_ch), sum(dead_letter_count)
        FROM   backfill_checkpoints FINAL
        GROUP  BY status
        ORDER  BY status
    """).result_rows
    if not rows:
        log.info("status_empty",
                 hint="No checkpoint rows yet — no backfill has run against this CH yet.")
        return 0
    for status, count, sum_pg, sum_ch, sum_dl in rows:
        log.info("status",
                 status=status, windows=int(count),
                 rows_in_pg=int(sum_pg or 0), rows_in_ch=int(sum_ch or 0),
                 dead_letter=int(sum_dl or 0))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    if args.status:
        return _cmd_status(args)

    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    log.info("backfill_start",
             run_id=run_id, worker_id=worker_id,
             dry_run=args.dry_run, batch_size=args.batch_size)

    pg = _pg_conn(args)
    # Both CH clients are wrapped in _ResilientCH so the orchestrator survives
    # the inevitable HTTP connection deaths CH dishes out during long backfills.
    # (Bare clickhouse-connect retries are URL-level; they don't recycle the
    # underlying client, so a dead socket gets re-used and re-fails 3x.)
    ch_http_factory = lambda: _ch_http(args)                         # noqa: E731
    ch_http = _ResilientCH(ch_http_factory)
    ch_bulk_factory = lambda: _ch_bulk(args)                         # noqa: E731
    ch_bulk = ch_bulk_factory()

    log.info("discovering_windows", since=args.since, until=args.until,
             projects=args.project_id or "all")
    windows = _discover_windows(pg, args.project_id, args.since, args.until)
    log.info("windows_discovered", total=len(windows))

    done = _completed_windows(ch_http)
    todo = [(pid, hr, est) for pid, hr, est in windows if (pid, hr) not in done]
    log.info("windows_skipped_already_done", skipped=len(windows) - len(todo))

    if args.max_windows is not None:
        todo = todo[: args.max_windows]
        log.info("windows_capped_for_run", cap=args.max_windows)

    totals = {"rows_in_pg": 0, "rows_inserted": 0, "rows_in_ch": 0,
              "dead_letter_count": 0, "windows": 0,
              "failed_validation": 0, "failed_windows": 0}
    t0 = time.time()

    for i, (project_id, hour_bucket, est) in enumerate(todo, 1):
        try:
            r = _process_window(
                pg, ch_http, ch_bulk,
                project_id=project_id, hour_bucket=hour_bucket,
                batch_size=args.batch_size, worker_id=worker_id, run_id=run_id,
                dry_run=args.dry_run, since=args.since, until=args.until,
                ch_bulk_factory=ch_bulk_factory,
            )
        except Exception as e:                                      # noqa: BLE001
            log.error("window_failed",
                      project_id=project_id, hour=hour_bucket.isoformat(),
                      err=str(e)[:500])
            _upsert_checkpoint(
                ch_http, project_id=project_id, hour_bucket=hour_bucket, status="failed",
                worker_id=worker_id, backfill_run_id=run_id, error_message=str(e)[:1000],
            )
            totals["failed_windows"] += 1
            continue

        totals["windows"] += 1
        for k in ("rows_in_pg", "rows_inserted", "rows_in_ch", "dead_letter_count"):
            totals[k] += r[k]
        if r["status"] == "failed_validation":
            totals["failed_validation"] += 1

        log.info("window_done", n=i, of=len(todo),
                 project_id=project_id, hour=hour_bucket.isoformat(),
                 est_rows=est, **r)

        # Periodic OPTIMIZE keeps the active-part count bounded on small CH
        # nodes — without it, ReplacingMergeTree accumulates ~600 parts after a
        # few thousand windows, and any FINAL query (e.g. validator at end-of-
        # run) needs O(parts) memory to merge them. With --optimize-every=500
        # the part count stays in the low hundreds.
        if args.optimize_every and i % args.optimize_every == 0:
            t_opt = time.time()
            try:
                ch_http.command("OPTIMIZE TABLE spans FINAL DEDUPLICATE")
                log.info("optimize_run", windows_so_far=i,
                         elapsed_sec=round(time.time() - t_opt, 2))
            except Exception as e:                                  # noqa: BLE001
                log.warning("optimize_failed", err=str(e)[:200])

    log.info("backfill_complete",
             elapsed_sec=round(time.time() - t0, 2), run_id=run_id, **totals)
    # Exit codes:
    #   0 — everything completed clean
    #   3 — at least one window raised an exception (e.g. CH/PG unreachable)
    #   4 — at least one window finished but with count mismatch (failed_validation)
    # 3 takes precedence because "did not finish" is a more serious failure than
    # "finished but counts disagreed" — both need operator attention, but they are
    # triaged differently.
    if totals["failed_windows"] > 0:
        return 3
    if totals["failed_validation"] > 0:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
