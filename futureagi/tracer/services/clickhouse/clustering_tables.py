"""Shared DDL for the clustering ClickHouse tables that live outside the managed
v2 schema (``cluster_centroids``, ``trace_input_embeddings``, ``error_embeddings``).

These tables are created lazily by the scanner / error / eval clustering query
paths. The DDL was duplicated across three query modules (``cluster_centroids``
alone had three identical copies); it lives here once so the engine-selection
rule and the column layout have a single source of truth, and so the
default-to-replicated migration command can create the same tables in a target
database without re-declaring their schema.

Engine selection mirrors ``ClickHouseVectorDB.create_table`` via
``build_replicated_engine``: a multi-replica cluster gets the ``Replicated*``
form ``ON CLUSTER`` with a Keeper path; single-node CH keeps the plain engine.
Each table preserves its own sub-family — the ``cluster_centroids`` /
``trace_input_embeddings`` ``ReplacingMergeTree`` dedup and the
``error_embeddings`` plain ``MergeTree`` (soft-delete via ``ALTER ... UPDATE``,
no version column) are intentional and must not be flattened to one engine.
"""
from __future__ import annotations

from agentic_eval.core.database.ch_vector import (
    ClickHouseVectorDB,
    build_replicated_engine,
)

CENTROIDS_TABLE = "cluster_centroids"
TRACE_INPUTS_TABLE = "trace_input_embeddings"
ERROR_EMBEDDINGS_TABLE = "error_embeddings"


def _qualified(table: str, database: str | None) -> str:
    return f"{database}.{table}" if database else table


def ensure_centroid_table(
    db: ClickHouseVectorDB, *, database: str | None = None, cluster: str | None = None
) -> None:
    """Create the shared ``cluster_centroids`` table (scanner + error + eval)."""
    engine, on_cluster = build_replicated_engine(
        "ReplacingMergeTree(last_updated)",
        CENTROIDS_TABLE,
        clustered=db._is_clustered(),
        database=database,
        cluster=cluster,
    )
    # Array(...) can't sit inside Nullable; override server profiles that set
    # data_type_default_nullable=1 so unmodified types aren't auto-wrapped.
    db.client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_qualified(CENTROIDS_TABLE, database)}{on_cluster} (
            cluster_id String,
            project_id UUID,
            centroid Array(Float32),
            member_count UInt32,
            family String,
            last_updated DateTime DEFAULT now(),
            PRIMARY KEY (cluster_id)
        ) ENGINE = {engine}
        ORDER BY (cluster_id)
        """,
        settings={"data_type_default_nullable": 0},
    )


def ensure_trace_inputs_table(
    db: ClickHouseVectorDB, *, database: str | None = None, cluster: str | None = None
) -> None:
    """Create the ``trace_input_embeddings`` table (scanner success-trace KNN)."""
    engine, on_cluster = build_replicated_engine(
        "ReplacingMergeTree(created_at)",
        TRACE_INPUTS_TABLE,
        clustered=db._is_clustered(),
        database=database,
        cluster=cluster,
    )
    db.client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_qualified(TRACE_INPUTS_TABLE, database)}{on_cluster} (
            trace_id UUID,
            project_id UUID,
            embedding Array(Float32),
            has_issues Bool,
            created_at DateTime DEFAULT now()
        ) ENGINE = {engine}
        ORDER BY (project_id, trace_id)
        """,
        settings={"data_type_default_nullable": 0},
    )


def ensure_error_embeddings_table(
    db: ClickHouseVectorDB, *, database: str | None = None, cluster: str | None = None
) -> None:
    """Create the ``error_embeddings`` table (error-feed cluster embeddings).

    Plain ``MergeTree`` -> ``ReplicatedMergeTree``: rows are soft-deleted with
    ``ALTER ... UPDATE deleted = 1`` and read with ``deleted = 0``, so a
    ``Replacing`` engine would change the dedup semantics. Mutations on a
    ``ReplicatedMergeTree`` propagate through the replication log on their own.
    """
    engine, on_cluster = build_replicated_engine(
        "MergeTree()",
        ERROR_EMBEDDINGS_TABLE,
        clustered=db._is_clustered(),
        database=database,
        cluster=cluster,
    )
    db.client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_qualified(ERROR_EMBEDDINGS_TABLE, database)}{on_cluster} (
            id UUID,
            eval_id UUID,
            vector Array(Float32),
            metadata Nested (
                key String,
                value Nullable(String)
            ),
            deleted UInt8 DEFAULT 0
        ) ENGINE = {engine}
        ORDER BY (eval_id, id)
        """,
        settings={"data_type_default_nullable": 0},
    )
