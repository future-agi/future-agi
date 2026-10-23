"""Dedicated bounded native connection, never the application's admin pool."""

import json
import math
import os
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.conf import settings

from .compiler import MAX_COMPILED_BYTES, MAX_RESULT_BYTES, MAX_RESULT_ROWS

MAX_SECONDS = 30
# Server profiles must enforce ceilings too; request settings can only tighten.
QUERY_SETTINGS = {
    "readonly": 1,
    "max_execution_time": MAX_SECONDS,
    "max_execution_time_leaf": MAX_SECONDS,
    "timeout_overflow_mode": "throw",
    "max_rows_to_read": 10_000_000,
    "max_bytes_to_read": 1_000_000_000,
    "max_rows_to_read_leaf": 10_000_000,
    "max_bytes_to_read_leaf": 1_000_000_000,
    "read_overflow_mode_leaf": "throw",
    "read_overflow_mode": "throw",
    "max_memory_usage": 268_435_456,
    "max_bytes_in_join": 67_108_864,
    "max_rows_in_join": 1_000_000,
    "max_rows_in_set": 1_000_000,
    "max_bytes_in_set": 67_108_864,
    "set_overflow_mode": "throw",
    "join_overflow_mode": "throw",
    "max_bytes_before_external_group_by": 0,
    "max_bytes_before_external_sort": 0,
    "max_result_rows": MAX_RESULT_ROWS + 1,
    "max_result_bytes": MAX_RESULT_BYTES,
    "result_overflow_mode": "throw",
    "max_threads": 2,
    "max_query_size": MAX_COMPILED_BYTES,
    "max_ast_depth": 100,
    "max_ast_elements": 30_000,
    "max_block_size": 1000,
    "use_query_cache": 0,
    "join_use_nulls": 1,
    "prefer_column_name_to_alias": 1,
    "optimize_distributed_group_by_sharding_key": 0,
    "optimize_skip_unused_shards": 0,
    "distributed_group_by_no_merge": 0,
    "prefer_global_in_and_join": 1,
    "distributed_product_mode": "global",
    "max_parallel_replicas": 1,
    "skip_unavailable_shards": 0,
}


class QueryUnavailable(Exception):
    pass


class QueryCancelled(Exception):
    pass


# The shared service principal runs at most max_concurrent_queries_for_user
# statements at once. ClickHouse turns a statement away with this code before
# it reads anything, so a run retries its opening statements for a short,
# bounded time instead of failing when another run's brief profile check
# holds a slot for a moment. Estimates use a separate principal.
TOO_MANY_SIMULTANEOUS_QUERIES = 202
START_RETRY_SECONDS = 3.0


def connection_config():
    username = os.environ.get("SQL_WORKBENCH_CH_USER", "")
    password = os.environ.get("SQL_WORKBENCH_CH_PASSWORD", "")
    config = settings.CLICKHOUSE_V2
    if not username or not password or username in {"default", config.get("CH25_USER")}:
        raise QueryUnavailable(
            "Configure the dedicated read-only SQL connection first."
        )
    return dict(
        host=config["CH25_HOST"],
        port=int(config.get("CH25_TCP_PORT") or 9000),
        database=config["CH25_DATABASE"],
        user=username,
        password=password,
        secure=os.environ.get("SQL_WORKBENCH_CH_SECURE", "false").lower() == "true",
        verify=True,
        connect_timeout=3,
        send_receive_timeout=MAX_SECONDS + 2,
        # readonly=1 profiles reject client-supplied setting assignments; the
        # provisioned profile exempts only additional_table_filters. Verify the
        # server-side ceilings below, then send reads carrying that one filter.
        client_name="futureagi-sql-workbench",
    )


PROFILE_MESSAGE = (
    "The SQL service account needs the documented locked read-only resource profile."
)


def verify_profile(client, *, estimate=False):
    limits = ESTIMATE_SETTINGS if estimate else QUERY_SETTINGS
    concurrency = 1 if estimate else 2
    keys = tuple(limits) + ("max_concurrent_queries_for_user",)
    values = client.execute(
        "SELECT " + ", ".join(f"getSetting('{key}')" for key in keys)
    )[0]
    for key, actual in zip(keys, values, strict=True):
        expected = limits.get(key, concurrency)
        if isinstance(expected, str):
            valid = str(actual) == expected
        elif key in {
            "readonly",
            "max_bytes_before_external_group_by",
            "max_bytes_before_external_sort",
            "use_query_cache",
            "join_use_nulls",
            "prefer_column_name_to_alias",
            "optimize_distributed_group_by_sharding_key",
            "optimize_skip_unused_shards",
            "distributed_group_by_no_merge",
            "prefer_global_in_and_join",
            "max_parallel_replicas",
            "skip_unavailable_shards",
        }:
            valid = int(actual) == expected
        else:
            valid = 0 < int(actual) <= expected
        if not valid:
            raise QueryUnavailable(PROFILE_MESSAGE)


def cell(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value if abs(value) <= 2**53 - 1 else str(value)
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return json.dumps(value, default=str, ensure_ascii=False)


def run(compiled_sql, query_id, cancelled, *, table_filters):
    """Execute one compiled query on the dedicated locked connection.

    ``table_filters`` is the scope's ``additional_table_filters`` map. It is the
    only per-query setting sent; the provisioned profile marks just that setting
    CHANGEABLE_IN_READONLY, so ClickHouse applies the scope's ownership filter
    to every read of each physical source, independently of the AST rewrite.
    """
    from clickhouse_driver import Client

    if (
        not isinstance(table_filters, str)
        or not table_filters.startswith("{'")
        or not table_filters.endswith("'}")
    ):
        # Fail closed: never run a user query without the server-side barrier.
        raise QueryUnavailable("SQL scope filters are missing.")
    client = Client(**connection_config())
    stop = threading.Event()
    aborted = threading.Event()
    started = time.monotonic()

    def monitor():
        from django.db import close_old_connections

        try:
            while not stop.wait(0.5):
                # Fail closed on a lost database/control-plane connection.
                try:
                    should_cancel = (
                        time.monotonic() - started > MAX_SECONDS or cancelled()
                    )
                except Exception:
                    should_cancel = True
                if should_cancel:
                    aborted.set()
                    try:
                        client.connection.send_cancel()
                    except Exception:
                        pass
                    return
        finally:
            close_old_connections()

    thread = threading.Thread(target=monitor, daemon=True)
    try:
        retry_until = time.monotonic() + START_RETRY_SECONDS
        delay = 0.05
        while True:
            if aborted.is_set() or cancelled():
                raise QueryCancelled()
            try:
                verify_profile(client)
                # Native cancellation targets THIS connection, not KILL
                # QUERY/admin SQL. Server/socket limits bound the read
                # independently of Temporal timeouts.
                stream = client.execute_iter(
                    compiled_sql,
                    query_id=str(query_id),
                    with_column_types=True,
                    settings={"additional_table_filters": table_filters},
                )
                # execute_iter has sent the query; only the main thread reads
                # packets. Client.cancel() is intentionally forbidden: it
                # drains receive_result().
                if thread.ident is None:
                    thread.start()
                header = next(stream)
                break
            except Exception as exc:
                if (
                    getattr(exc, "code", None) != TOO_MANY_SIMULTANEOUS_QUERIES
                    or time.monotonic() + delay > retry_until
                ):
                    raise
                # The rejected statement read nothing; reconnect and retry.
                client.disconnect()
                time.sleep(delay)
                delay = min(delay * 2, 0.5)
        columns = [{"name": name, "type": kind} for name, kind in header]
        if len(columns) > 100 or len({c["name"] for c in columns}) != len(columns):
            raise QueryUnavailable("Use at most 100 uniquely named output columns.")
        rows = []
        size = len(json.dumps(columns).encode())
        truncated = False
        for record in stream:
            if aborted.is_set():
                raise QueryCancelled()
            if len(rows) == MAX_RESULT_ROWS:
                truncated = True
                client.connection.send_cancel()
                break
            row = [cell(value) for value in record]
            size += len(json.dumps(row, ensure_ascii=False).encode())
            if size > MAX_RESULT_BYTES:
                raise QueryUnavailable(
                    "Result exceeds 8 MB. Aggregate or narrow the query."
                )
            rows.append(row)
        if aborted.is_set() or cancelled():
            raise QueryCancelled()
        return {
            "columns": columns,
            "rows": rows,
            "truncated": truncated,
            "row_count": len(rows),
            "result_bytes": size,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        if getattr(exc, "code", None) in {60, 81, 497}:
            raise QueryUnavailable(
                "SQL catalog source unavailable. Apply its schema and read-only grants."
            ) from exc
        if getattr(exc, "code", None) == 164:
            # READONLY: the profile does not allow the per-query scope filter.
            raise QueryUnavailable(PROFILE_MESSAGE) from exc
        raise
    finally:
        stop.set()
        if thread.is_alive():
            thread.join(timeout=2)
        client.disconnect()


# An estimate is advisory: it must answer quickly or not at all.
ESTIMATE_SECONDS = 5
ESTIMATE_SETTINGS = {
    **QUERY_SETTINGS,
    "max_execution_time": ESTIMATE_SECONDS,
    "max_execution_time_leaf": ESTIMATE_SECONDS,
    "max_threads": 1,
}


def estimate_connection_config():
    """Never consume a run's per-user slots for advisory work.

    Missing/invalid estimator credentials disable estimates, not execution.
    The operator must provision this distinct account on each coordinator.
    """
    config = connection_config()
    username = os.environ.get("SQL_WORKBENCH_ESTIMATE_CH_USER", "")
    password = os.environ.get("SQL_WORKBENCH_ESTIMATE_CH_PASSWORD", "")
    if (
        not username
        or not password
        or username
        in {"default", config.get("user"), settings.CLICKHOUSE_V2.get("CH25_USER")}
    ):
        raise QueryUnavailable(
            "Configure a separate read-only SQL estimate connection."
        )
    config.update(
        user=username,
        password=password,
        connect_timeout=2,
        send_receive_timeout=ESTIMATE_SECONDS,
        client_name="futureagi-sql-estimate",
    )
    return config


def estimate(compiled_sql, *, table_filters, database):
    """Index analysis of one compiled query; reads no table data rows.

    ``EXPLAIN ESTIMATE`` runs on a separate locked estimate principal with the
    same server-side ``additional_table_filters`` as a real run. It cannot use
    execution's per-user slots. It returns, per physical table, the parts,
    index granules (marks) and rows selected by index analysis. The actual
    primary key and granularity come from ``system.tables`` so the caller can
    withhold numbers for tables whose index is shared across projects.
    """
    from clickhouse_driver import Client

    if (
        not isinstance(table_filters, str)
        or not table_filters.startswith("{'")
        or not table_filters.endswith("'}")
    ):
        raise QueryUnavailable("SQL scope filters are missing.")
    client = Client(**estimate_connection_config())
    try:
        verify_profile(client, estimate=True)
        rows = client.execute(
            f"EXPLAIN ESTIMATE {compiled_sql}",
            settings={"additional_table_filters": table_filters},
        )
        tables = []
        for row in rows:
            if len(row) != 5:
                raise QueryUnavailable("Unexpected estimate shape.")
            source_database, table, parts, read_rows, marks = row
            tables.append(
                {
                    "database": str(source_database),
                    "table": str(table),
                    "parts": int(parts),
                    "rows": int(read_rows),
                    "marks": int(marks),
                }
            )
        names = sorted({item["table"] for item in tables})
        layout = {}
        if names:
            for name, primary_key, engine_full in client.execute(
                "SELECT name, primary_key, engine_full FROM system.tables "
                "WHERE database = %(database)s AND name IN %(names)s",
                {"database": database, "names": tuple(names)},
            ):
                layout[str(name)] = (str(primary_key), str(engine_full))
        return {"tables": tables, "layout": layout}
    finally:
        client.disconnect()


def derived_metrics_availability():
    if (
        os.environ.get("SQL_WORKBENCH_DERIVED_METRICS_ENABLED", "false").lower()
        != "true"
    ):
        return False, "Derived metrics schema is not enabled."
    import re
    from clickhouse_driver import Client
    from .catalog import COLUMNS

    client = None
    try:
        config = connection_config()
        database = config["database"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", database):
            raise QueryUnavailable("Invalid SQL source database.")
        config.update(connect_timeout=2, send_receive_timeout=2)
        client = Client(**config)
        verify_profile(client)
        # LIMIT 0 checks the actual view/grants/schema without a metadata catalog
        # privilege, returning no user data and not scanning stored rollups.
        client.execute(
            f"SELECT {', '.join(COLUMNS['derived_metrics'])} FROM {database}.derived_metrics LIMIT 0"
        )
        return True, ""
    except Exception:
        return (
            False,
            "Derived metrics source unavailable: apply the metrics schema and read-only grants.",
        )
    finally:
        if client is not None:
            client.disconnect()
