"""Read-only database evidence for initializing the shared runtime identity.

Only control metadata is used for adopting a running installation. Conflicting
identities require the reviewed separate-database migration, never choosing a
maximum version or rewriting rows. Every workspace still undergoes the normal
schema, provenance, reservation and activation checks before its first write.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from .activation import ManifestStreamRole, RevisionBuildPlan
from .installation_identity import (
    InstallationIdentity,
    InstallationIdentityError,
    identity_path,
    load_identity,
    load_or_initialize_identity,
)
from .publisher import PROPERTY_CATALOG_TABLES, require_catalog_database

_READ_TIMEOUT_MS = 30_000
_INSPECTION_TIMEOUT_SECONDS = 60
_READ_SETTINGS = {
    "readonly": 2,
    "max_threads": 2,
    "max_bytes_to_read": 512 << 20,
    "max_result_bytes": 256 << 10,
    "max_result_rows": 32,
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}


def _rows(
    client: Any, sql: str, params: dict | None = None, *, timeout_ms: int
) -> Sequence[tuple]:
    result, _, _ = client.execute_read(
        sql,
        params or {},
        timeout_ms=timeout_ms,
        settings=dict(_READ_SETTINGS),
    )
    return result


def inspect_installation(
    client: Any,
    *,
    environment: str,
    target_database: str,
    candidate_topic: str,
    ordered_topic: str,
    monotonic: Callable[[], float] = time.monotonic,
) -> InstallationIdentity:
    """Derive an exact existing identity or prove all six catalog tables empty."""
    require_catalog_database(target_database)
    if getattr(client, "database", None) != target_database:
        raise InstallationIdentityError(
            "identity probe database differs from the target"
        )
    catalog_tables = set(PROPERTY_CATALOG_TABLES)
    deadline = monotonic() + _INSPECTION_TIMEOUT_SECONDS

    def read(sql: str, params: dict | None = None) -> Sequence[tuple]:
        remaining_ms = int((deadline - monotonic()) * 1_000)
        if remaining_ms <= 0:
            raise InstallationIdentityError("catalog identity inspection timed out")
        rows = _rows(
            client, sql, params, timeout_ms=min(_READ_TIMEOUT_MS, remaining_ms)
        )
        if monotonic() >= deadline:
            raise InstallationIdentityError("catalog identity inspection timed out")
        return rows

    table_scope = {"database": target_database, "tables": tuple(sorted(catalog_tables))}
    metadata = read(
        "SELECT name, engine FROM system.tables WHERE database = %(database)s "
        "AND name IN %(tables)s",
        table_scope,
    )
    engines = {str(name): str(engine) for name, engine in metadata}
    if not catalog_tables.issubset(engines):
        raise InstallationIdentityError(
            "catalog schema is incomplete; identity was not initialized"
        )
    if any(
        not engine.endswith("MergeTree")
        for name, engine in engines.items()
        if name in catalog_tables
    ):
        raise InstallationIdentityError(
            "catalog identity probe requires physical MergeTree tables"
        )
    # A caught-up local replica is necessary but does not replace the normal
    # all-replica durability proof used before activating catalog data.
    if any(engines[name].startswith("Replicated") for name in catalog_tables):
        replicas = read(
            "SELECT table, is_readonly, is_session_expired, queue_size, "
            "active_replicas, total_replicas FROM system.replicas "
            "WHERE database = %(database)s AND table IN %(tables)s",
            table_scope,
        )
        expected = {
            name for name in catalog_tables if engines[name].startswith("Replicated")
        }
        if {str(row[0]) for row in replicas} != expected or any(
            readonly or expired or queued or active != total or not total
            for _, readonly, expired, queued, active, total in replicas
        ):
            raise InstallationIdentityError("catalog replica evidence is not caught up")
    # Identity allocation is not qualification. The representative full plan
    # is checked below; each workspace independently checks all its own plans.
    rows = read(
        "SELECT catalog_epoch, projection_version, "
        "JSONExtractString(stream, 'producer_stream_id') AS hot_stream, "
        "any(tuple(build_plan_json, build_lease_sha256)) AS evidence "
        f"FROM {target_database}.property_catalog_source_streams "
        "ARRAY JOIN JSONExtractArrayRaw(build_plan_json, 'streams') AS stream "
        "WHERE source_adapter = 'system_manifest' AND producer_stream_id = build_token "
        "AND JSONExtractString(stream, 'role') = 'hot_values' "
        "GROUP BY catalog_epoch, projection_version, hot_stream LIMIT 2",
    )
    if len(rows) > 1:
        raise InstallationIdentityError(
            "catalog contains multiple runtime identities; use a validated migration, not a version override"
        )
    if rows:
        epoch, projection, stream, evidence = rows[0]
        plan = RevisionBuildPlan.from_json(evidence[0])
        hot = tuple(
            item for item in plan.streams if item.role is ManifestStreamRole.HOT_VALUES
        )
        if (
            plan.sha256 != evidence[1]
            or plan.catalog_epoch != epoch
            or plan.projection_version != projection
            or len(hot) != 1
            or hot[0].producer_stream_id != stream
        ):
            raise InstallationIdentityError(
                "catalog identity build-plan evidence is inconsistent"
            )
        # Do not discard historical conflicting identity namespaces that happen
        # not to have appeared in the representative hot-stream selection.
        other = read(
            f"SELECT 1 FROM {target_database}.property_catalog_source_streams "
            "WHERE catalog_epoch != %(epoch)s OR projection_version != %(projection)s LIMIT 1",
            {"epoch": epoch, "projection": projection},
        )
        if other:
            raise InstallationIdentityError(
                "catalog control history contains conflicting identities"
            )
        return InstallationIdentity(
            environment=environment,
            target_database=target_database,
            candidate_topic=candidate_topic,
            ordered_topic=ordered_topic,
            catalog_epoch=epoch,
            projection_version=projection,
            producer_stream_id=str(stream),
        )
    for table in sorted(catalog_tables):
        if read(f"SELECT 1 FROM {target_database}.{table} LIMIT 1"):
            raise InstallationIdentityError(
                "non-empty catalog has no unambiguous runtime identity"
            )
    return InstallationIdentity.fresh(
        environment=environment,
        target_database=target_database,
        candidate_topic=candidate_topic,
        ordered_topic=ordered_topic,
    )


def resolve_installation(
    *,
    settings_object: Any,
    prefix: str,
    environment: str,
    target_database: str,
    revision_fence_file: str,
    candidate_topic: str,
    ordered_topic: str,
    client_factory: Callable[..., Any] | None = None,
    readonly: bool = False,
) -> InstallationIdentity:
    """Resolve before workspace discovery; no synthetic workspace may allocate it."""
    require_catalog_database(target_database)
    path = identity_path(revision_fence_file)

    def initialize() -> InstallationIdentity:
        if readonly:
            raise InstallationIdentityError(
                "status-only requires an existing runtime identity"
            )
        if client_factory is None:
            from tracer.services.clickhouse.client import ClickHouseClient

            factory = ClickHouseClient
        else:
            factory = client_factory
        client = factory(
            host=getattr(settings_object, prefix + "WRITE_CH_HOST"),
            port=getattr(settings_object, prefix + "WRITE_CH_PORT"),
            user=getattr(settings_object, prefix + "WRITE_CH_USER"),
            password=getattr(settings_object, prefix + "WRITE_CH_PASSWORD"),
            database=target_database,
            server_enforced_readonly=True,
            allow_query_settings_with_server_readonly=True,
            connect_timeout=5,
            send_timeout=35,
            receive_timeout=35,
            pool_size=1,
        )
        try:
            return inspect_installation(
                client,
                environment=environment,
                target_database=target_database,
                candidate_topic=candidate_topic,
                ordered_topic=ordered_topic,
            )
        finally:
            client.close()

    identity = (
        load_identity(path)
        if readonly
        else load_or_initialize_identity(path, initialize=initialize)
    )
    identity.require_destination(
        environment=environment,
        target_database=target_database,
        candidate_topic=candidate_topic,
        ordered_topic=ordered_topic,
    )
    return identity


def require_legacy_identity(
    identity: InstallationIdentity,
    *,
    epoch: Any,
    projection: Any,
    producer: Any,
) -> None:
    """Deprecated explicit values may confirm persisted identity, never override it."""
    if (epoch, projection, producer) != (
        identity.catalog_epoch,
        identity.projection_version,
        identity.producer_stream_id,
    ):
        raise InstallationIdentityError(
            "legacy version settings conflict with the persisted identity"
        )
