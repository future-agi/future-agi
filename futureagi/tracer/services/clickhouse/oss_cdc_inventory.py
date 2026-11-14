"""Read-only PeerDB mapping inspection for the OSS installer.

The injected JSON transport owns authentication, response-size and whole-job
deadlines. It must not retry writes or turn failed responses into empty objects.
Only the two GET routes and the read-only mirror-status POST below are used.
Protocol: the flow-api shipped in Compose, verified against PeerDB v0.36.9.
This is an inventory, NOT a historical-copy, CDC catch-up or schema witness.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

# Optional defaults recorded in PeerDB v0.36.9 mirror config.env, not operator
# epoch/revision knobs. Accept only these exact values, never arbitrary settings.
_RECORDED_ENV_DEFAULTS = {
    "PEERDB_CLICKHOUSE_CLIENT_NAME": "peerdb",
    "PEERDB_CLICKHOUSE_INITIAL_LOAD_ALLOW_NON_EMPTY_TABLES": "false",
    "PEERDB_FORCE_INTERNAL_VERSION": "4",
    "PEERDB_POSTGRES_WAL_SENDER_TIMEOUT": "120s",
    "PEERDB_SOURCE_SCHEMA_AS_DESTINATION_COLUMN": "false",
}


class InventoryError(ValueError):
    """An incomplete or incompatible inventory must never authorize bootstrap."""


class InventoryPending(InventoryError):
    """A known initial setup/snapshot state may be polled without any writes."""


@dataclass(frozen=True)
class PeerTarget:
    name: str
    host: str
    port: int
    database: str

    def __post_init__(self):
        if (
            any(
                not isinstance(s, str) or not s
                for s in (self.name, self.host, self.database)
            )
            or type(self.port) is not int
            or not 0 < self.port < 65536
        ):
            raise InventoryError("explicit peer name, host, port and database required")

    @property
    def endpoint(self):
        return self.host, self.port, self.database


def inspect_mappings(
    request: Callable[[str, str, dict | None], dict],
    *,
    source: PeerTarget,
    destination: PeerTarget,
) -> tuple[tuple[str, str], ...]:
    """Return every effective mapping into the exact configured CH endpoint.

    Peer names alone are not identity: inspect same-target aliases too. Endpoint
    comparison is exact; the caller must use PeerDB's configured network addresses,
    not a host-side forwarding address. An exclusive installer window is still
    required: this API supplies no transactional snapshot or distributed lock.
    No mirror is created, paused, resynced, deleted or restarted by this function.
    """

    def read(method, path, payload=None):
        try:
            result = request(method, path, payload)
        except Exception:
            # Peer configs and transport errors may contain credentials/SQL.
            raise InventoryError(
                "PeerDB inspection failed; no inventory produced"
            ) from None
        if not isinstance(result, dict):
            raise InventoryError("invalid PeerDB response envelope")
        return result

    peers = {}

    def peer(name, kind):
        if not isinstance(name, str) or not name:
            raise InventoryError("invalid mirror peer identity")
        if name not in peers:
            peers[name] = read("GET", f"/v1/peers/info/{quote(name, safe='')}").get(
                "peer"
            )
        record = peers[name]
        if (
            not isinstance(record, dict)
            or record.get("name") != name
            or record.get("type") != kind
        ):
            raise InventoryError("peer name/type differs from requested target")
        config = record.get(
            "postgresConfig" if kind == "POSTGRES" else "clickhouseConfig"
        )
        if not isinstance(config, dict):
            raise InventoryError("missing peer configuration")
        target = PeerTarget(
            name, config.get("host"), config.get("port"), config.get("database")
        )
        if config.get("sshConfig"):
            raise InventoryError(
                "SSH-routed peer identity is not qualified for bootstrap"
            )
        return target.endpoint

    if (
        peer(source.name, "POSTGRES") != source.endpoint
        or peer(destination.name, "CLICKHOUSE") != destination.endpoint
    ):
        raise InventoryError("configured peer endpoint does not match installer target")
    mirrors = read("GET", "/v1/mirrors/list").get("mirrors")
    if not isinstance(mirrors, list):
        raise InventoryError("complete mirrors list required")
    names, destinations, result = set(), set(), []
    for mirror in mirrors:
        if not isinstance(mirror, dict):
            raise InventoryError("invalid mirror list entry")
        name = mirror.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise InventoryError("missing/duplicate mirror name")
        names.add(name)
        dst = mirror.get("destinationName")
        if not isinstance(dst, str) or not dst:
            raise InventoryError("missing mirror destination identity")
        kind = mirror.get("destinationType")
        if kind not in ("POSTGRES", "CLICKHOUSE"):
            raise InventoryError("missing or unqualified mirror destination type")
        # Verify the peer before excluding a writer. A contradictory list entry
        # must not hide an alias that actually writes into this database.
        endpoint = peer(dst, kind)
        if kind != "CLICKHOUSE" or endpoint != destination.endpoint:
            continue  # inspected unrelated endpoint; never touch its mappings
        if mirror.get("isCdc") is not True or mirror.get("sourceType") != "POSTGRES":
            raise InventoryError(
                "non-Postgres CDC writer targets this ClickHouse database"
            )
        src = mirror.get("sourceName")
        if peer(src, "POSTGRES") != source.endpoint:
            raise InventoryError(
                "foreign source writer targets this ClickHouse database"
            )
        status = read(
            "POST",
            "/v1/mirrors/status",
            {
                "flowJobName": name,
                "includeFlowInfo": True,
                "excludeBatches": True,
            },
        )
        if status.get("flowJobName") != name:
            raise InventoryError("mirror status identity differs from inventory")
        states = (status.get("currentFlowState"), mirror.get("status"))
        if states != ("STATUS_RUNNING", "STATUS_RUNNING"):
            # Paused, failed, resyncing, modifying, unknown and malformed states
            # are not an initial copy in progress. Never repair/restart them.
            if all(
                state in ("STATUS_SETUP", "STATUS_SNAPSHOT", "STATUS_RUNNING")
                for state in states
            ):
                raise InventoryPending("mirror is not in a stable running state")
            raise InventoryError("mirror is not in a stable running state")
        cdc = status.get("cdcStatus")
        config = cdc.get("config") if isinstance(cdc, dict) else None
        if not isinstance(config, dict) or any(
            type(config.get(k)) is not type(v) or config.get(k) != v
            for k, v in {
                "flowJobName": name,
                "sourceName": src,
                "destinationName": dst,
                "softDeleteColName": "_peerdb_is_deleted",
                "syncedAtColName": "_peerdb_synced_at",
                "resync": False,
                "initialSnapshotOnly": False,
                "script": "",
                "system": "Q",
                "flags": [],
            }.items()
        ):
            raise InventoryError(
                "incompatible or incomplete effective CDC configuration"
            )
        env = config.get("env")
        if not isinstance(env, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or (
                value not in ("true", "false")
                if key == "PEERDB_NULLABLE"
                else key not in _RECORDED_ENV_DEFAULTS
                or value != _RECORDED_ENV_DEFAULTS[key]
            )
            for key, value in env.items()
        ):
            raise InventoryError("incompatible or incomplete CDC settings")
        # Empty means inherited, not false. This mapping inventory does not
        # establish effective nullability or compatibility of retained tables.
        # doInitialSnapshot is historical creation intent, not current readiness.
        # config.version is not an operator revision and is not compared here.
        mappings = config.get("tableMappings")
        if not isinstance(mappings, list) or not mappings:
            raise InventoryError("complete effective table mappings required")
        for mapping in mappings:
            if not isinstance(mapping, dict) or any(
                mapping.get(k) != v
                for k, v in {
                    "exclude": [],
                    "columns": [],
                    "partitionKey": "",
                    "shardingKey": "",
                    "policyName": "",
                    "partitionByExpr": "",
                    "engine": "CH_ENGINE_REPLACING_MERGE_TREE",
                }.items()
            ):
                raise InventoryError("table mapping transformations are not qualified")
            table = mapping.get("destinationTableIdentifier")
            if (
                not isinstance(table, str)
                or not table.isascii()
                or not table.isidentifier()
                or mapping.get("sourceTableIdentifier") != f"public.{table}"
                or table in destinations
            ):
                raise InventoryError(
                    "unknown, foreign or duplicate effective destination"
                )
            destinations.add(table)
            result.append((name, table))
    return tuple(result)
