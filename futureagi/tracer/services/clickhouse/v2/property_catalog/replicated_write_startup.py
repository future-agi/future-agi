"""SELECT-only direct-member discovery for managed replicated catalog startup.

Call after installation identity resolution and keep this context open for the
writer/proof lifetime. The configured WRITE_CH route is a discovery seed only.
No schema, source, control-ledger, or catalog row is written here; the existing
admission producer alone persists its proven local admission descriptor.

``prefix=None`` selects DEV for a development installation and LIFECYCLE for
production. The production controller's existing DEV settings overlay may pass
``prefix="PROPERTY_CATALOG_DEV_"`` explicitly. ``cluster`` may name an already
configured infrastructure cluster; omission requires one unambiguous complete
local cluster in system.clusters. No catalog version or replica-count env knobs
are read. Native TLS/NAT listener translation is not guessed: unsupported or
unreachable direct routes fail closed.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from .dev_rollout import is_dev_control_plane_cloud_allowed
from .installation_identity import (
    IDENTITY_FILENAME,
    InstallationIdentity,
    load_identity,
)
from .keeper_membership import PROPERTY_CATALOG_TABLES
from .production_rollout import PRODUCTION_CLOUD_DEPLOYMENTS
from .publisher import require_dev_catalog_database, require_prod_catalog_database
from .write_admission import (
    DirectCatalogConnection,
    WriteAdmission,
    WriteAdmissionError,
    admit_catalog_writes,
)
from .write_endpoint import _uri_host

_DEV = "PROPERTY_CATALOG_DEV_"
_PROD = "PROPERTY_CATALOG_LIFECYCLE_"
_LEDGER = "FI_PROPERTY_CATALOG_LEDGER_CH_"
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,253}\Z", re.ASCII)
_MAX_MEMBERS = 16  # Same bound as runtime infrastructure hostname expectations.
_MAX_CLUSTER_ROWS = 256
_METADATA_TABLES = frozenset(
    {
        "clusters",
        "databases",
        "tables",
        "replicas",
        "query_log",
        "zookeeper",
        "zookeeper_connection",
    }
)
# Already reviewed for the existing ledger/source-part proof deployment, but
# not needed by direct startup discovery or native write completion itself.
_OPTIONAL_METADATA_TABLES = frozenset({"parts"})
_IDENTITY_SQL = """SELECT hostName() AS hostname,
toString(serverUUID()) AS server_uuid, currentDatabase() AS database,
currentUser() AS username, toString(uuid) AS database_uuid,
getServerPort('tcp_port') AS native_port
FROM system.databases WHERE name=currentDatabase() LIMIT 2"""
_REPLICAS_SQL = """SELECT table, replica_name,
arraySort(mapKeys(replica_is_active)) AS replica_names,
total_replicas, active_replicas, is_readonly, is_session_expired
FROM system.replicas WHERE database=currentDatabase() ORDER BY table LIMIT 8"""
_CLUSTERS_SQL = """SELECT cluster, shard_num, replica_num, host_name, port, is_local
FROM system.clusters
WHERE cluster IN (SELECT cluster FROM system.clusters WHERE is_local)
AND (%(cluster)s = '' OR cluster = %(cluster)s)
ORDER BY cluster, shard_num, replica_num, host_name, port LIMIT 257"""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise WriteAdmissionError(f"replicated startup requires explicit {label}")
    return value


def _name(value: Any, label: str) -> str:
    if not _NAME.fullmatch(_text(value, label)):
        raise WriteAdmissionError(f"invalid replicated startup {label}")
    return value


def _port(value: Any, *, configured=False) -> int:
    if configured and isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not 1 <= value <= 65535:
        raise WriteAdmissionError(
            "replicated startup needs an explicit actual listener port"
        )
    return value


def _uuid(value: Any) -> str:
    try:
        parsed = UUID(value)
        if not parsed.int or str(parsed) != value:
            raise ValueError
    except (TypeError, ValueError, AttributeError) as exc:
        raise WriteAdmissionError("replicated startup incarnation is invalid") from exc
    return value


def _expected_hosts(settings_object, prefix):
    plural = getattr(settings_object, prefix + "EXPECTED_WRITE_CH_HOSTNAMES", ())
    singular = getattr(settings_object, prefix + "EXPECTED_WRITE_CH_HOSTNAME", "")
    if not isinstance(plural, (tuple, list)):
        raise WriteAdmissionError(
            "replicated hostname expectations must be an explicit list"
        )
    values = tuple(_name(v, "expected hostname") for v in plural)
    if singular:
        singular = _name(singular, "expected hostname")
        if values and singular not in values:
            raise WriteAdmissionError(
                "conflicting infrastructure hostname expectations"
            )
        if not values:
            values = (singular,)
    if len(set(values)) != len(values) or len(values) > _MAX_MEMBERS:
        raise WriteAdmissionError("ambiguous infrastructure hostname expectations")
    return tuple(sorted(values))


def _scheme(environ):
    origin = environ.get(_LEDGER + "URL")
    if origin is None:
        return "http"
    try:
        url = urlsplit(origin)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or not url.port
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or any(c.isspace() for c in origin)
        ):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise WriteAdmissionError("invalid configured ledger HTTP origin") from exc
    # Its service/NAT authority is NOT used for any member. Discover actual
    # listeners on each direct node, preserving the configured TLS protocol.
    return url.scheme


@dataclass(frozen=True)
class _Node:
    hostname: str
    server_uuid: str
    database_uuid: str
    native_port: int


@dataclass(frozen=True)
class _Route:
    cluster: str
    replica: int
    host: str
    port: int


def _routes(rows, *, count, cluster):
    if len(rows) > _MAX_CLUSTER_ROWS:
        raise WriteAdmissionError("cluster discovery exceeded its metadata bound")
    groups = {}
    for row in rows:
        if set(row) != {
            "cluster",
            "shard_num",
            "replica_num",
            "host_name",
            "port",
            "is_local",
        }:
            raise WriteAdmissionError("cluster discovery columns changed")
        name = _name(row["cluster"], "cluster")
        host = _text(row["host_name"], "cluster host")
        _uri_host(host)
        for key in ("shard_num", "replica_num"):
            if type(row[key]) is not int or row[key] < 1:
                raise WriteAdmissionError("invalid cluster shard/replica number")
        if type(row["is_local"]) is not int or row["is_local"] not in (0, 1):
            raise WriteAdmissionError("invalid cluster local membership")
        port = _port(row["port"])
        groups.setdefault(name, []).append(
            (row, _Route(name, row["replica_num"], host, port))
        )
    candidates = []
    for name, group in groups.items():
        if cluster is not None and name != cluster:
            raise WriteAdmissionError("cluster query returned foreign infrastructure")
        if (
            len(group) != count
            or any(row["shard_num"] != 1 for row, _ in group)
            or sorted(route.replica for _, route in group) != list(range(1, count + 1))
            or sum(row["is_local"] for row, _ in group) != 1
            or len({(route.host, route.port) for _, route in group}) != count
        ):
            continue
        routes = tuple(sorted((route for _, route in group), key=lambda r: r.replica))
        local = next(route for row, route in group if row["is_local"])
        candidates.append((routes, local))
    if len(candidates) != 1:
        raise WriteAdmissionError(
            "no unique complete single-shard direct cluster routes"
        )
    return candidates[0]


@contextmanager
def replicated_write_admission_context(
    settings_object: Any,
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
    prefix: str | None = None,
    cluster: str | None = None,
    timeout_ms: int = 30_000,
) -> Iterator[
    tuple[InstallationIdentity, WriteAdmission, tuple[DirectCatalogConnection, ...]]
]:
    """Discover, fully attest, and retain actual direct native proof clients.

    No clients are constructed until persisted identity, destination, scope,
    and separate credentials have passed local checks. All remote operations
    are bounded SELECT/SHOW reads under one shrinking startup budget (at most
    30 seconds). Automatic DEV dispatch passes its remaining budget here.
    A configured load balancer is never returned as a direct connection.
    """
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30_000:
        raise WriteAdmissionError(
            "replicated startup needs a bounded remaining deadline"
        )
    deadline = monotonic() + timeout_ms / 1000
    environ = os.environ if environ is None else environ
    environment = getattr(settings_object, "ENV_TYPE", "")
    cloud = getattr(settings_object, "CLOUD_DEPLOYMENT", "")
    production = environment in {"prod", "production"}
    if production:
        if cloud not in PRODUCTION_CLOUD_DEPLOYMENTS:
            raise WriteAdmissionError(
                "production replicated startup requires an exact cloud identity"
            )
    elif not is_dev_control_plane_cloud_allowed(
        environment=environment, cloud_deployment=cloud
    ):
        raise WriteAdmissionError("unsupported replicated startup environment")
    prefix = prefix or (_PROD if production else _DEV)
    if prefix not in {_DEV, _PROD} or (not production and prefix != _DEV):
        raise WriteAdmissionError(
            "replicated startup settings prefix crosses environments"
        )
    if cluster is not None:
        _name(cluster, "configured cluster")
    fence = Path(
        _text(
            getattr(settings_object, prefix + "REVISION_FENCE_FILE", None),
            "revision fence file",
        )
    )
    if not fence.is_absolute():
        raise WriteAdmissionError(
            "replicated startup needs the absolute shared fence directory"
        )
    identity = load_identity(fence.parent / IDENTITY_FILENAME)
    # Production owns TARGET_DATABASE and maps it to DEV_WRITE_CH_DATABASE in
    # workspace_settings_overlay; there is no production WRITE_CH_DATABASE
    # setting in the existing configuration contract.
    target = _text(
        getattr(
            settings_object,
            prefix + ("TARGET_DATABASE" if prefix == _PROD else "WRITE_CH_DATABASE"),
            None,
        ),
        "writer database",
    )
    explicit_writer_database = getattr(
        settings_object, prefix + "WRITE_CH_DATABASE", target
    )
    if explicit_writer_database != target:
        raise WriteAdmissionError("configured writer database differs from target")
    (require_prod_catalog_database if production else require_dev_catalog_database)(
        target
    )
    # DEV_TARGET_DATABASE on a production shim belongs to the unused DEV
    # command, not that shim's explicitly mapped production WRITE_CH_DATABASE.
    target_prefix = _PROD if production else _DEV
    declared = getattr(settings_object, target_prefix + "TARGET_DATABASE", target)
    if declared != target or environ.get(_LEDGER + "DATABASE", target) != target:
        raise WriteAdmissionError(
            "writer/ledger destination differs from managed target"
        )
    identity.require_destination(
        environment="production" if production else "development",
        target_database=target,
        candidate_topic=_text(
            getattr(settings_object, "PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC", None),
            "candidate topic",
        ),
        ordered_topic=_text(
            getattr(settings_object, "PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC", None),
            "ordered topic",
        ),
    )
    host = _text(
        getattr(settings_object, prefix + "WRITE_CH_HOST", None), "discovery seed"
    )
    _uri_host(host)
    port = _port(
        getattr(settings_object, prefix + "WRITE_CH_PORT", None), configured=True
    )
    writer_user = _name(
        getattr(settings_object, prefix + "WRITE_CH_USER", None), "writer user"
    )
    proof_user = _name(environ.get(_LEDGER + "USERNAME"), "ledger proof user")
    proof_password = environ.get(_LEDGER + "PASSWORD")
    if (
        proof_user == writer_user
        or proof_user.casefold() in {"admin", "root", "default"}
        or writer_user.casefold() in {"admin", "root", "default"}
        or not isinstance(proof_password, str)
    ):
        raise WriteAdmissionError(
            "replicated startup requires a separate non-admin ledger principal"
        )
    expected_hosts = _expected_hosts(settings_object, prefix)
    if production and len(expected_hosts) != 3:
        raise WriteAdmissionError(
            "production replicated startup requires exactly three infrastructure hostnames"
        )
    if expected_hosts and not 2 <= len(expected_hosts) <= _MAX_MEMBERS:
        raise WriteAdmissionError(
            "replicated startup requires full member expectations"
        )
    scheme = _scheme(environ)

    def remaining():
        value = int((deadline - monotonic()) * 1000)
        if value <= 0:
            raise WriteAdmissionError("replicated startup admission timed out")
        return value

    def query(client, sql, params, limit):
        try:
            rows, columns, _ = client.execute_read(
                sql,
                params,
                timeout_ms=remaining(),
                settings={
                    "readonly": 2,
                    "max_threads": 1,
                    "max_result_rows": limit,
                    "max_result_bytes": 128 << 10,
                    "max_memory_usage": 128 << 20,
                    "max_execution_time": max(1, min(5000, remaining()) // 1000),
                    "result_overflow_mode": "throw",
                    "timeout_overflow_mode": "throw",
                    "use_query_cache": 0,
                },
            )
            remaining()
            names = tuple(c[0] if isinstance(c, tuple) else c for c in columns)
            if len(rows) > limit or len(names) != len(set(names)):
                raise ValueError
            return tuple(dict(zip(names, row, strict=True)) for row in rows)
        except Exception:
            raise WriteAdmissionError(
                "replicated startup metadata unavailable or incomplete"
            ) from None

    def node(client):
        rows = query(client, _IDENTITY_SQL, {}, 2)
        if len(rows) != 1 or set(rows[0]) != {
            "hostname",
            "server_uuid",
            "database",
            "username",
            "database_uuid",
            "native_port",
        }:
            raise WriteAdmissionError("replicated node identity is incomplete")
        row = rows[0]
        if (row["database"], row["username"]) != (target, proof_user):
            raise WriteAdmissionError(
                "replicated proof connection changed database or principal"
            )
        result = _Node(
            _name(row["hostname"], "observed hostname"),
            _uuid(row["server_uuid"]),
            _uuid(row["database_uuid"]),
            _port(row["native_port"]),
        )
        if expected_hosts and result.hostname not in expected_hosts:
            raise WriteAdmissionError(
                "replicated node is outside infrastructure expectations"
            )
        return result

    def membership(client):
        rows = query(client, _REPLICAS_SQL, {}, 8)
        if tuple(row.get("table") for row in rows) != tuple(
            sorted(PROPERTY_CATALOG_TABLES)
        ):
            raise WriteAdmissionError(
                "replicated startup requires exactly seven replicated catalog tables"
            )
        expected = None
        for row in rows:
            if set(row) != {
                "table",
                "replica_name",
                "replica_names",
                "total_replicas",
                "active_replicas",
                "is_readonly",
                "is_session_expired",
            }:
                raise WriteAdmissionError("replica discovery columns changed")
            name = _name(row["replica_name"], "replica name")
            if not isinstance(row["replica_names"], (tuple, list)) or not (
                2 <= len(row["replica_names"]) <= _MAX_MEMBERS
            ):
                raise WriteAdmissionError("replica membership is not explicit")
            names = tuple(_name(v, "replica name") for v in row["replica_names"])
            if (
                names != tuple(sorted(set(names)))
                or name not in names
                or not 2 <= len(names) <= _MAX_MEMBERS
                or (production and len(names) != 3)
                or (expected_hosts and len(names) != len(expected_hosts))
            ):
                raise WriteAdmissionError(
                    "replica discovery did not return exact full membership"
                )
            for key, value in (
                ("total_replicas", len(names)),
                ("active_replicas", len(names)),
                ("is_readonly", 0),
                ("is_session_expired", 0),
            ):
                if type(row[key]) is not int or row[key] != value:
                    raise WriteAdmissionError(
                        "replica membership is inactive or incomplete"
                    )
            if expected is not None and (name, names) != expected:
                raise WriteAdmissionError(
                    "catalog tables disagree on serving membership"
                )
            expected = name, names
        return expected

    def grants(client):
        required = {f"{target}.{table}" for table in PROPERTY_CATALOG_TABLES} | {
            f"system.{table}" for table in _METADATA_TABLES
        }
        allowed = required | {f"system.{table}" for table in _OPTIONAL_METADATA_TABLES}
        seen = set()
        for row in query(client, "SHOW GRANTS", {}, 32):
            if len(row) != 1 or not isinstance(next(iter(row.values())), str):
                raise WriteAdmissionError("invalid replicated ledger grant metadata")
            grant = next(iter(row.values())).replace("`", "")
            start, end = "GRANT SELECT ON ", " TO " + proof_user
            if not grant.startswith(start) or not grant.endswith(end):
                raise WriteAdmissionError(
                    "ledger proof principal has non-SELECT or delegated grants"
                )
            table = grant[len(start) : -len(end)]
            if table not in allowed:
                raise WriteAdmissionError(
                    "ledger proof principal has unreviewed table grants"
                )
            seen.add(table)
        if not required.issubset(seen):
            raise WriteAdmissionError(
                "ledger proof principal lacks exact seven-table/metadata SELECT grants"
            )

    if client_factory is None:
        from tracer.services.clickhouse.client import ClickHouseClient

        client_factory = ClickHouseClient
    with ExitStack() as stack:

        def connect(route_host, route_port):
            remaining()
            client = client_factory(
                host=route_host,
                port=route_port,
                user=proof_user,
                password=proof_password,
                database=target,
                server_enforced_readonly=True,
                allow_query_settings_with_server_readonly=True,
                connect_timeout=min(5, remaining() / 1000),
                send_timeout=remaining() / 1000,
                receive_timeout=remaining() / 1000,
                pool_size=1,
                read_timeout_ceiling_ms=30_000,
            )
            stack.callback(client.close)
            if (
                getattr(client, "host", None) != route_host
                or getattr(client, "port", None) != route_port
                or getattr(client, "database", None) != target
                or getattr(client, "server_enforced_readonly", None) is not True
            ):
                raise WriteAdmissionError(
                    "direct proof client changed its configured route"
                )
            return client

        seed = connect(host, port)
        seed_node = node(seed)
        grants(seed)
        seed_name, names = membership(seed)
        routes, local = _routes(
            query(seed, _CLUSTERS_SQL, {"cluster": cluster or ""}, 257),
            count=len(names),
            cluster=cluster,
        )
        selected_cluster = routes[0].cluster
        connections, observations = [], []
        for route in routes:
            client = connect(route.host, route.port)
            observed = node(client)
            if observed.native_port != route.port:
                raise WriteAdmissionError(
                    "cluster route differs from the actual native listener"
                )
            name, members = membership(client)
            if members != names:
                raise WriteAdmissionError(
                    "direct node is outside the complete catalog membership"
                )
            if route == local and (observed, name) != (seed_node, seed_name):
                raise WriteAdmissionError(
                    "discovery seed does not match its configured local member"
                )
            grants(client)
            connections.append(
                DirectCatalogConnection(
                    name=name,
                    native_member_host=route.host,
                    expected_hostname=observed.hostname,
                    driver=client,
                    http_scheme=scheme,
                    proof_username=proof_user,
                    proof_password=proof_password,
                )
            )
            observations.append((route, observed, name, client))
        connections = tuple(sorted(connections, key=lambda c: c.name))
        if (
            tuple(c.name for c in connections) != names
            or any(
                len({getattr(n, field) for _, n, _, _ in observations}) != len(names)
                for field in ("hostname", "server_uuid")
            )
            or (
                expected_hosts
                and tuple(sorted(n.hostname for _, n, _, _ in observations))
                != expected_hosts
            )
        ):
            raise WriteAdmissionError(
                "discovered routes alias or omit actual serving members"
            )

        def recheck():
            if node(seed) != seed_node or membership(seed) != (seed_name, names):
                raise WriteAdmissionError("discovery seed changed during admission")
            current, current_local = _routes(
                query(seed, _CLUSTERS_SQL, {"cluster": cluster or ""}, 257),
                count=len(names),
                cluster=cluster,
            )
            if (current, current_local) != (routes, local):
                raise WriteAdmissionError("configured discovery topology changed")
            for route, observed, name, client in observations:
                current, current_local = _routes(
                    query(client, _CLUSTERS_SQL, {"cluster": selected_cluster}, 257),
                    count=len(names),
                    cluster=selected_cluster,
                )
                if (
                    current != routes
                    or current_local != route
                    or node(client) != observed
                    or membership(client) != (name, names)
                ):
                    raise WriteAdmissionError(
                        "direct member changed identity or cluster routes"
                    )
                grants(client)
            remaining()

        recheck()
        admission = admit_catalog_writes(
            fence.parent,
            identity=identity,
            connections=connections,
            timeout_ms=remaining(),
        )
        if admission.family != "replicated" or tuple(
            (m.name, m.hostname, m.server_uuid, m.database_uuid)
            for m in admission.members
        ) != tuple(
            sorted(
                (name, n.hostname, n.server_uuid, n.database_uuid)
                for _, n, name, _ in observations
            )
        ):
            raise WriteAdmissionError(
                "admission producer returned another discovered membership"
            )
        recheck()
        yield identity, admission, connections


def prepare_replicated_write_admission(
    settings_object: Any, **kwargs
) -> WriteAdmission:
    """Short-lived admission wrapper; writers should hold the context instead."""
    with replicated_write_admission_context(settings_object, **kwargs) as (
        _,
        admission,
        _,
    ):
        return admission
