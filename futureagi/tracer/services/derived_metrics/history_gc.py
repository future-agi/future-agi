"""Background reclaim of rows that history GC deleted lightweight.

RefreshWriter.collect_unreachable_rows deletes superseded copies with a
lightweight DELETE: readers stop seeing them at once, but the bytes (and the
rows each read still scans before the mask) stay until a merge rewrites the
part, and large parts may not merge for a long time. This scheduled pass,
outside every refresh pass, applies the deleted-row mask to at most a few
partitions per run, choosing those with the most masked rows. A partition is
shared by many projects (cityHash64(project_id) % 32), so the rewrite also
touches other projects' rows in the same parts; it changes none of them.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from tracer.services.derived_metrics.storage import TABLES

DEFAULT_MASK_PARTITIONS_PER_RUN = 4


def mask_partitions_per_run():
    """Partitions one cleanup run rewrites (METRICS_GC_MASK_PARTITIONS_PER_RUN)."""
    value = getattr(
        settings, "METRICS_GC_MASK_PARTITIONS_PER_RUN", DEFAULT_MASK_PARTITIONS_PER_RUN
    )
    if type(value) is not int or not 0 <= value <= 64:
        raise ImproperlyConfigured(
            "METRICS_GC_MASK_PARTITIONS_PER_RUN must be an integer from 0 to 64"
        )
    return value


def masked_partitions(execute, *, limit):
    """(table, partition_id, masked parts' rows) with the most masked rows first."""
    if limit <= 0:
        return []
    return execute(
        "SELECT table, partition_id, sum(rows) AS masked_rows FROM system.parts "
        "WHERE database = currentDatabase() AND active AND has_lightweight_delete "
        "AND table IN %(gc_tables)s GROUP BY table, partition_id "
        f"ORDER BY masked_rows DESC, table, partition_id LIMIT {int(limit)}",
        {"gc_tables": tuple(TABLES)},
    )


def apply_deleted_masks(execute, *, limit=None):
    """Rewrite up to ``limit`` partitions' masked parts; return what was done.

    ``execute(sql, params)`` runs one statement on the metric store's server.
    Each rewrite is synchronous and bounded to one partition. A failure leaves
    the rows masked (still invisible to readers) for the next run.
    """
    limit = mask_partitions_per_run() if limit is None else limit
    done = []
    for table, partition, rows in masked_partitions(execute, limit=limit):
        if table not in TABLES:
            continue
        execute(
            f"ALTER TABLE {table} APPLY DELETED MASK IN PARTITION ID %(gc_partition)s "
            "SETTINGS mutations_sync = 2",
            {"gc_partition": partition},
        )
        done.append((table, partition, rows))
    return done


def run_scheduled():
    """The scheduled metric cleanup's reclaim step, on its own connection."""
    from clickhouse_driver import Client

    from tracer.services.clickhouse.v2 import get_v2_config
    from tracer.services.derived_metrics.publication import WRITE_SETTINGS

    if not mask_partitions_per_run():
        return []
    config = get_v2_config()
    client = Client(
        host=config["host"],
        port=config["tcp_port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        connect_timeout=10,
        send_receive_timeout=300,
    )
    settings_ = {**WRITE_SETTINGS, "max_execution_time": 300}
    try:
        return apply_deleted_masks(
            lambda sql, params: client.execute(sql, params, settings=settings_)
        )
    finally:
        client.disconnect()
