"""SELECT-only producer of the shared Go/Python write-admission descriptor.

Admission freezes identity, not permission to skip live proof. Writers must
reattest topology/Keeper and reconcile uncertain INSERT attempts independently.
Connections must be infrastructure-selected direct members, never service/LB
samples. This module neither installs schemas nor initializes runtime identity.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import http.client
import json
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from .installation_identity import IDENTITY_FILENAME, InstallationIdentity
from .keeper_membership import KeeperMember, attest_keeper_membership
from .write_endpoint import CatalogHTTPRouteCandidate, discover_catalog_http_route

WRITE_ADMISSION_FILENAME = "write-admission-v1.json"
MAX_ADMISSION_BYTES = 128 << 10
_MAX_PROBE_BYTES = 512 << 10
_FORMAT = "futureagi.property-catalog-write-admission"
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,253}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ENGINES = {
    "property_catalog_activation_control_events": "MergeTree",
    "property_catalog_activations": "ReplacingMergeTree",
    "property_catalog_checkpoints": "ReplacingMergeTree",
    "property_catalog_deliveries": "MergeTree",
    "property_catalog_source_streams": "ReplacingMergeTree",
    "property_definition_catalog": "MergeTree",
    "span_attribute_value_catalog": "AggregatingMergeTree",
}
_INVENTORY_SQL = """SELECT hostName() AS hostname,
toString(serverUUID()) AS server_uuid, currentDatabase() AS database,
(SELECT toString(uuid) FROM system.databases WHERE name=currentDatabase()) AS database_uuid,
(SELECT engine FROM system.databases WHERE name=currentDatabase()) AS database_engine,
currentUser() AS username, t.name AS name, toString(t.uuid) AS uuid,
t.engine AS engine, t.create_table_query AS create_table_query,
lower(hex(SHA256(t.create_table_query))) AS create_sha256,
ifNull(r.zookeeper_path,'') AS keeper_path,
ifNull(r.zookeeper_name,'') AS keeper_name,
ifNull(r.replica_name,'') AS replica_name,
arraySort(mapKeys(ifNull(r.replica_is_active,map()))) AS replica_names,
ifNull(r.total_replicas,0) AS total_replicas,
ifNull(r.active_replicas,0) AS active_replicas,
ifNull(r.is_readonly,0) AS readonly,
ifNull(r.is_session_expired,0) AS session_expired
FROM system.tables AS t LEFT JOIN system.replicas AS r
ON t.database=r.database AND t.name=r.table
WHERE t.database=currentDatabase() ORDER BY t.name LIMIT 8"""
# Only an already-validated standalone admission may omit replica metadata.
# Table engines, incarnations, and CREATE text remain live observations.
_STANDALONE_INVENTORY_SQL = """SELECT hostName() AS hostname,
toString(serverUUID()) AS server_uuid, currentDatabase() AS database,
(SELECT toString(uuid) FROM system.databases WHERE name=currentDatabase()) AS database_uuid,
(SELECT engine FROM system.databases WHERE name=currentDatabase()) AS database_engine,
currentUser() AS username, t.name AS name, toString(t.uuid) AS uuid,
t.engine AS engine, t.create_table_query AS create_table_query,
lower(hex(SHA256(t.create_table_query))) AS create_sha256,
'' AS keeper_path,
'' AS keeper_name,
'' AS replica_name,
CAST([], 'Array(String)') AS replica_names,
toUInt32(0) AS total_replicas,
toUInt32(0) AS active_replicas,
toUInt8(0) AS readonly,
toUInt8(0) AS session_expired
FROM system.tables AS t
WHERE t.database=currentDatabase() ORDER BY t.name LIMIT 8"""
_INVENTORY_FIELDS = frozenset(
    "hostname server_uuid database database_uuid database_engine username name uuid "
    "engine create_table_query create_sha256 keeper_path keeper_name replica_name "
    "replica_names total_replicas active_replicas readonly session_expired".split()
)


class WriteAdmissionError(ValueError):
    """Admission is missing, uncertain, malformed, or conflicts with installed state."""


def _canonical(document: Any) -> bytes:
    # Go encoding/json escapes HTML and these two separators, but not other UTF-8.
    value = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    for literal, escaped in (
        ("&", "\\u0026"),
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        value = value.replace(literal, escaped)
    return value.encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _uuid(value: str) -> None:
    try:
        parsed = UUID(value)
        if not parsed.int or str(parsed) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise WriteAdmissionError("admission requires canonical nonzero UUIDs") from exc


def _origin(value: str):
    try:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or not url.port
            or any(c.isspace() for c in value)
        ):
            raise ValueError
        return url
    except (ValueError, TypeError, AttributeError) as exc:
        raise WriteAdmissionError(
            "admission requires a direct origin with an explicit listener port"
        ) from exc


@dataclass(frozen=True, slots=True)
class WriteTable:
    create_sha256: str
    engine: str
    keeper_path: str
    name: str
    replica_name: str
    replica_names: tuple[str, ...]
    uuid: str


@dataclass(frozen=True, slots=True)
class WriteMember:
    database_uuid: str
    hostname: str
    name: str
    server_uuid: str
    tables: tuple[WriteTable, ...]
    url: str


def _keeper_digest(members: tuple[WriteMember, ...]) -> str:
    return _sha(
        {
            "format": "futureagi.catalog-keeper-live-membership",
            "version": 1,
            "members": [
                {"name": m.name, "server_uuid": m.server_uuid} for m in members
            ],
            "tables": [
                {"name": t.name, "keeper_path": t.keeper_path}
                for t in members[0].tables
            ],
        }
    )


@dataclass(frozen=True, slots=True)
class WriteAdmission:
    database: str
    environment: str
    family: str
    installation_sha256: str
    keeper_identity_sha256: str
    members: tuple[WriteMember, ...]
    topology_sha256: str
    format: str = _FORMAT
    version: int = 1

    def __post_init__(self) -> None:
        if (
            self.format != _FORMAT
            or type(self.version) is not int
            or self.version != 1
            or not isinstance(self.database, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", self.database)
            or self.database in {"default", "system", "information_schema"}
            or self.environment not in {"development", "production"}
            or not isinstance(self.installation_sha256, str)
            or not _SHA.fullmatch(self.installation_sha256)
            or not isinstance(self.topology_sha256, str)
            or not _SHA.fullmatch(self.topology_sha256)
            or type(self.members) is not tuple
            or not self.members
            or any(type(m) is not WriteMember for m in self.members)
        ):
            raise WriteAdmissionError("invalid write admission identity")
        n = len(self.members)
        if self.family == "standalone":
            if (
                n != 1
                or self.keeper_identity_sha256 != ""
                or self.environment == "production"
            ):
                raise WriteAdmissionError(
                    "standalone requires one non-production server"
                )
        elif self.family == "replicated":
            if n < 2 or (self.environment == "production" and n != 3):
                raise WriteAdmissionError(
                    "replicated admission requires full serving membership"
                )
        else:
            raise WriteAdmissionError("unknown admission family")
        for key in ("name", "hostname", "server_uuid", "url"):
            values = [getattr(m, key) for m in self.members]
            if any(not isinstance(v, str) for v in values) or len(set(values)) != n:
                raise WriteAdmissionError("admission aliases a serving member")
        names = tuple(m.name for m in self.members)
        if names != tuple(sorted(names)):
            raise WriteAdmissionError("admission members must be sorted")
        for member in self.members:
            if not _NAME.fullmatch(member.name) or not _NAME.fullmatch(member.hostname):
                raise WriteAdmissionError("invalid admission member name")
            _origin(member.url)
            _uuid(member.database_uuid)
            _uuid(member.server_uuid)
            if (
                type(member.tables) is not tuple
                or any(type(t) is not WriteTable for t in member.tables)
                or tuple(t.name for t in member.tables) != tuple(sorted(_ENGINES))
            ):
                raise WriteAdmissionError(
                    "admission requires exactly seven sorted catalog tables"
                )
            for table in member.tables:
                _uuid(table.uuid)
                if (
                    any(
                        not isinstance(getattr(table, key), str)
                        for key in (
                            "create_sha256",
                            "engine",
                            "keeper_path",
                            "replica_name",
                        )
                    )
                    or not _SHA.fullmatch(table.create_sha256)
                    or type(table.replica_names) is not tuple
                ):
                    raise WriteAdmissionError("invalid admitted table identity")
                engine = _ENGINES[table.name]
                if self.family == "replicated":
                    engine = "Replicated" + engine
                    if (
                        table.replica_name != member.name
                        or table.replica_names != names
                    ):
                        raise WriteAdmissionError(
                            "table membership differs from serving membership"
                        )
                elif (table.keeper_path, table.replica_name, table.replica_names) != (
                    "",
                    "",
                    (),
                ):
                    raise WriteAdmissionError(
                        "standalone requires explicit empty replica membership"
                    )
                if table.engine != engine:
                    raise WriteAdmissionError(
                        "table engine differs from admitted family"
                    )
        if self.family == "replicated":
            # Structural validation is NOT live attestation. No sessions in digest.
            keeper = _keeper_members(self.members)
            if (
                any(m.table_paths != keeper[0].table_paths for m in keeper)
                or len({p for _, p in keeper[0].table_paths}) != 7
                or self.keeper_identity_sha256 != _keeper_digest(self.members)
            ):
                raise WriteAdmissionError(
                    "Keeper identity does not bind exact servers and table paths"
                )
        document = asdict(self)
        del document["topology_sha256"]
        if (
            _sha(document) != self.topology_sha256
            or len(self.encode()) > MAX_ADMISSION_BYTES
        ):
            raise WriteAdmissionError("admission digest or size is invalid")

    def encode(self) -> bytes:
        return _canonical(asdict(self)) + b"\n"

    @classmethod
    def decode(cls, raw: bytes) -> WriteAdmission:
        try:
            if not raw or len(raw) > MAX_ADMISSION_BYTES or not raw.endswith(b"\n"):
                raise ValueError
            document = json.loads(raw)
            if _canonical(document) + b"\n" != raw:
                raise ValueError
            members = document["members"]
            if type(members) is not list:
                raise ValueError
            for member in members:
                if type(member["tables"]) is not list:
                    raise ValueError
                for table in member["tables"]:
                    if type(table["replica_names"]) is not list:
                        raise ValueError
                    table["replica_names"] = tuple(table["replica_names"])
                member["tables"] = tuple(WriteTable(**t) for t in member["tables"])
            document["members"] = tuple(WriteMember(**m) for m in members)
            result = cls(**document)
            if result.encode() != raw:  # Missing defaulted fields are not canonical.
                raise ValueError
            return result
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
            raise WriteAdmissionError("invalid canonical write admission") from exc


@dataclass(frozen=True, slots=True)
class DirectCatalogConnection:
    name: str
    native_member_host: str
    expected_hostname: str
    driver: Any = field(repr=False, compare=False)
    http_scheme: str = "http"
    proof_username: str = field(default="", repr=False)
    proof_password: str = field(default="", repr=False)


def _settings(limit: int, timeout_ms: int) -> dict:
    return {
        "readonly": 2,
        "max_threads": 1,
        "max_result_rows": limit,
        "max_result_bytes": _MAX_PROBE_BYTES,
        "max_memory_usage": 128 << 20,
        "max_execution_time": max(1, (timeout_ms + 999) // 1000),
        "result_overflow_mode": "throw",
        "timeout_overflow_mode": "throw",
        "use_query_cache": 0,
    }


def _native_read(
    connection: DirectCatalogConnection,
    sql: str,
    params: Mapping,
    limit: int,
    timeout_ms: int,
) -> tuple[dict, ...]:
    try:
        rows, columns, _ = connection.driver.execute_read(
            sql,
            dict(params),
            timeout_ms=timeout_ms,
            settings=_settings(limit, timeout_ms),
        )
        names = tuple(c[0] if isinstance(c, tuple) else c for c in columns)
        if len(names) != len(set(names)) or len(rows) > limit:
            raise ValueError
        result = tuple(dict(zip(names, row, strict=True)) for row in rows)
        if len(_canonical(result)) > _MAX_PROBE_BYTES:
            raise ValueError
        return result
    except Exception as exc:
        raise WriteAdmissionError("direct native metadata observation failed") from exc


def read_catalog_http(
    connection: DirectCatalogConnection,
    route: CatalogHTTPRouteCandidate,
    sql: str,
    timeout_ms: int,
    limit: int,
) -> tuple[dict, ...]:
    """One complete bounded SELECT; no proxies, redirects, retries, or default ports."""
    url = _origin(route.origin)
    client_class = (
        http.client.HTTPSConnection
        if url.scheme == "https"
        else http.client.HTTPConnection
    )
    client = client_class(url.hostname, url.port, timeout=timeout_ms / 1000)
    try:
        settings = {
            **_settings(limit, timeout_ms),
            "database": route.database,
            "wait_end_of_query": 1,
            "output_format_json_quote_64bit_integers": 0,
        }
        credentials = base64.b64encode(
            f"{connection.proof_username}:{connection.proof_password}".encode()
        ).decode("ascii")
        client.request(
            "POST",
            "/?" + urlencode(settings),
            body=(sql + " FORMAT JSONEachRow").encode(),
            headers={
                "Authorization": "Basic " + credentials,
                "Content-Type": "text/plain; charset=utf-8",
            },
        )
        response = client.getresponse()
        raw = response.read(_MAX_PROBE_BYTES + 1)
        length = response.getheader("Content-Length")
        if (
            response.status != 200
            or response.getheader("X-ClickHouse-Exception-Code")
            or len(raw) > _MAX_PROBE_BYTES
            or not raw.endswith(b"\n")
            or (length is not None and int(length) != len(raw))
        ):
            raise ValueError

        def unique(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError
            return result

        rows = tuple(
            json.loads(line, object_pairs_hook=unique) for line in raw.splitlines()
        )
        if len(rows) > limit or any(type(row) is not dict for row in rows):
            raise ValueError
        return rows
    except Exception:
        # Do not propagate credentials, server SQL errors, or response bodies.
        raise WriteAdmissionError("direct HTTP metadata observation failed") from None
    finally:
        client.close()


def verify_catalog_schema_snapshot(database: str, rows: Sequence[Mapping]) -> None:
    """Use the existing pinned exact CREATE verifier on the observed rows themselves."""
    from tracer.services.clickhouse.v2 import catalog_prod_schema as schema

    statements = (
        *schema._load_canonical_statements(),
        schema._activation_control_statement(),
    )
    specs = (*schema._TABLE_SPECS, schema._ACTIVATION_CONTROL_SPEC)
    pinned = {s.table: (s, spec) for s, spec in zip(statements, specs, strict=True)}
    if tuple(row["name"] for row in rows) != tuple(sorted(pinned)):
        raise WriteAdmissionError("schema proof requires all seven catalog tables")
    for row in rows:
        statement, spec = pinned[row["name"]]
        expected = statement.sql
        if row["engine"] == spec.replicated_engine:
            expected = schema._render_table(
                statement,
                spec,
                target_database=database,
                cluster="runtime_schema_verification",
                keeper_path_prefix="/clickhouse/tables",
            ).sql
        elif row["engine"] != statement.engine:
            raise WriteAdmissionError("unreviewed catalog engine")
        options = {
            "target_database": database,
            "expected_table": row["name"],
            "cluster": "runtime_schema_verification",
        }
        if schema._canonical_create_tokens(
            row["create_table_query"], **options
        ) != schema._canonical_create_tokens(expected, **options):
            raise WriteAdmissionError("catalog CREATE differs from pinned schema")


def _keeper_members(members: tuple[WriteMember, ...]) -> tuple[KeeperMember, ...]:
    return tuple(
        KeeperMember(
            m.name,
            m.hostname,
            m.server_uuid,
            tuple((t.name, t.keeper_path) for t in m.tables),
        )
        for m in members
    )


def _observe(connection, route, rows, names, *, http: bool) -> WriteMember:
    if len(rows) != 7 or any(set(row) != _INVENTORY_FIELDS for row in rows):
        raise WriteAdmissionError(
            "physical inventory must contain exactly seven complete rows"
        )
    tables = []
    identity = None
    for row in rows:
        route.require_same_http_identity(
            hostname=row["hostname"],
            database=row["database"],
            database_uuid=row["database_uuid"],
        )
        current = (row["hostname"], row["server_uuid"], row["database_uuid"])
        if identity is not None and current != identity:
            raise WriteAdmissionError("mixed node identity in catalog inventory")
        identity = current
        if (
            row["database_engine"] != "Atomic"
            or not isinstance(row["username"], str)
            or not row["username"]
            or (http and row["username"] != connection.proof_username)
        ):
            raise WriteAdmissionError("database engine or proof account differs")
        if (
            not isinstance(row["create_table_query"], str)
            or hashlib.sha256(row["create_table_query"].encode()).hexdigest()
            != row["create_sha256"]
        ):
            raise WriteAdmissionError("CREATE fingerprint differs from observed schema")
        replicated = isinstance(row["engine"], str) and row["engine"].startswith(
            "Replicated"
        )
        n = len(names) if replicated else 0
        for key, expected in (
            ("total_replicas", n),
            ("active_replicas", n),
            ("readonly", 0),
            ("session_expired", 0),
        ):
            if type(row[key]) is not int or row[key] != expected:
                raise WriteAdmissionError(
                    "not every admitted replica is registered, active and writable"
                )
        if row["keeper_name"] != ("default" if replicated else "") or type(
            row["replica_names"]
        ) not in (list, tuple):
            raise WriteAdmissionError(
                "table must bind the attested default Keeper only"
            )
        tables.append(
            WriteTable(
                *(
                    row[key]
                    for key in (
                        "create_sha256",
                        "engine",
                        "keeper_path",
                        "name",
                        "replica_name",
                    )
                ),
                tuple(row["replica_names"]),
                row["uuid"],
            )
        )
    return WriteMember(
        identity[2],
        identity[0],
        connection.name,
        identity[1],
        tuple(tables),
        route.origin,
    )


def _private(fd: int, *, directory: bool = False) -> None:
    info = os.fstat(fd)
    if (
        not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
        or (not directory and info.st_nlink != 1)
    ):
        raise WriteAdmissionError(
            "admission requires private owned directories and single-link regular files"
        )


def _read_file(directory: int, name: str, limit: int) -> bytes:
    fd = os.open(
        name,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=directory,
    )
    with os.fdopen(fd, "rb") as source:
        _private(source.fileno())
        return source.read(limit + 1)


def reattest_catalog_writes(
    directory: Path,
    *,
    identity: InstallationIdentity,
    admission: WriteAdmission,
    connections: Sequence[DirectCatalogConnection],
    timeout_ms: int,
    http_read: Callable = read_catalog_http,
    route_resolver: Callable[[DirectCatalogConnection, CatalogHTTPRouteCandidate], str]
    | None = None,
) -> None:
    """Re-prove an installed admission; never create, replace or repair it.

    Every call reads fresh native/HTTP inventories and verifies the pinned
    schema. Native inventories bracket HTTP observation and, for replicated
    installations, the concrete cross-node Keeper/session proof. Reusing an
    admitted direct URL is not reusing a prior observation: a replaced node,
    table, database, schema or inactive member still fails immediately.

    Initial admission's extra publication checks and directory fsync belong
    to startup, not to every already-admitted catalog SELECT or INSERT proof.
    """
    directory = Path(directory)
    connections = tuple(connections)
    if (
        not directory.is_absolute()
        or type(identity) is not InstallationIdentity
        or type(admission) is not WriteAdmission
        or type(timeout_ms) is not int
        or not 1 <= timeout_ms <= 30_000
        or any(type(c) is not DirectCatalogConnection for c in connections)
        or not callable(http_read)
        or (route_resolver is not None and not callable(route_resolver))
    ):
        raise WriteAdmissionError("invalid bounded direct reattestation request")
    admission.__post_init__()
    connections = tuple(sorted(connections, key=lambda c: c.name))
    names = tuple(m.name for m in admission.members)
    if (
        tuple(c.name for c in connections) != names
        or admission.database != identity.target_database
        or admission.environment != identity.environment
        or admission.installation_sha256
        != json.loads(identity.encode())["identity_sha256"]
        or any(
            c.expected_hostname != m.hostname
            or getattr(c.driver, "database", None) != admission.database
            or c.http_scheme != _origin(m.url).scheme
            or not isinstance(c.proof_username, str)
            or not c.proof_username
            or ":" in c.proof_username
            or not isinstance(c.proof_password, str)
            for c, m in zip(connections, admission.members, strict=True)
        )
    ):
        raise WriteAdmissionError("reattestation requires exact admitted members")
    inventory_sql = (
        _STANDALONE_INVENTORY_SQL
        if admission.family == "standalone"
        else _INVENTORY_SQL
    )
    deadline = monotonic() + timeout_ms / 1000

    def remaining():
        value = int((deadline - monotonic()) * 1000)
        if value <= 0:
            raise WriteAdmissionError("write reattestation timed out")
        return value

    directory_fd = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:

        def check_installed():
            _private(directory_fd, directory=True)
            original = os.fstat(directory_fd)
            current = os.stat(directory, follow_symlinks=False)
            if (
                (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino)
                or _read_file(directory_fd, IDENTITY_FILENAME, 4096)
                != identity.encode()
                or _read_file(
                    directory_fd, WRITE_ADMISSION_FILENAME, MAX_ADMISSION_BYTES
                )
                != admission.encode()
            ):
                raise WriteAdmissionError(
                    "installed runtime identity or admission changed"
                )

        check_installed()
        routes, snapshots = [], []
        for connection, member in zip(connections, admission.members, strict=True):
            route = CatalogHTTPRouteCandidate(
                member.url, member.hostname, admission.database, member.database_uuid
            )
            if route_resolver is not None:
                discovered = discover_catalog_http_route(
                    connection.driver,
                    native_member_host=connection.native_member_host,
                    expected_hostname=connection.expected_hostname,
                    database=identity.target_database,
                    scheme=connection.http_scheme,
                    timeout_ms=remaining(),
                )
                if route_resolver(connection, discovered) != route.origin:
                    raise WriteAdmissionError("admitted HTTP route changed")
            before = _native_read(connection, inventory_sql, {}, 8, remaining())
            if _observe(connection, route, before, names, http=False) != member:
                raise WriteAdmissionError("native admitted inventory changed")
            verify_catalog_schema_snapshot(admission.database, before)
            http_rows = http_read(connection, route, inventory_sql, remaining(), 8)
            if _observe(connection, route, http_rows, names, http=True) != member:
                raise WriteAdmissionError("HTTP admitted inventory changed")
            routes.append(route)
            snapshots.append(before)

        if admission.family == "replicated":
            direct = {c.name: c for c in connections}

            def read(name, sql, params, limit, timeout):
                return _native_read(
                    direct[name], sql, params, limit, min(timeout, remaining())
                )

            observed = attest_keeper_membership(
                _keeper_members(admission.members), read, timeout_ms=remaining()
            )
            if observed != admission.keeper_identity_sha256:
                raise WriteAdmissionError("live Keeper membership digest differs")

        for connection, member, route, before in zip(
            connections, admission.members, routes, snapshots, strict=True
        ):
            if _native_read(connection, inventory_sql, {}, 8, remaining()) != before:
                raise WriteAdmissionError(
                    "native inventory changed during reattestation"
                )
            if admission.family == "replicated":
                http_rows = http_read(connection, route, inventory_sql, remaining(), 8)
                if _observe(connection, route, http_rows, names, http=True) != member:
                    raise WriteAdmissionError(
                        "HTTP inventory changed during Keeper proof"
                    )
        check_installed()
        remaining()
    finally:
        os.close(directory_fd)


def admit_catalog_writes(
    directory: Path,
    *,
    identity: InstallationIdentity,
    connections: Sequence[DirectCatalogConnection],
    timeout_ms: int = 30_000,
    verify_schema: Callable[
        [str, Sequence[Mapping]], None
    ] = verify_catalog_schema_snapshot,
    http_read: Callable = read_catalog_http,
    route_resolver: Callable[[DirectCatalogConnection, CatalogHTTPRouteCandidate], str]
    | None = None,
) -> WriteAdmission:
    """Prove and install immutable admission; an existing descriptor is reattested.

    ``verify_schema`` is the exact schema verifier, not a claimed schema hash.
    Keeper attestation is always the concrete helper, never caller-supplied proof.
    The private directory and its ancestors must be the installed shared volume.
    A changed topology requires a separately reviewed attempt/recovery migration;
    this producer deliberately cannot overwrite it, even after a successful probe.
    ``route_resolver(connection, discovered)`` may supply an infrastructure-owned
    NAT origin. It cannot change any expected identity or bypass direct HTTP proof.
    """
    directory = Path(directory)
    connections = tuple(connections)
    if (
        not directory.is_absolute()
        or type(timeout_ms) is not int
        or not 1 <= timeout_ms <= 30_000
        or not 1 <= len(connections) <= 32
        or any(type(c) is not DirectCatalogConnection for c in connections)
        or any(
            not isinstance(c.name, str)
            or not _NAME.fullmatch(c.name)
            or not isinstance(c.expected_hostname, str)
            or not _NAME.fullmatch(c.expected_hostname)
            or not isinstance(c.proof_username, str)
            or not c.proof_username
            or ":" in c.proof_username
            or not isinstance(c.proof_password, str)
            for c in connections
        )
        or len({c.name for c in connections}) != len(connections)
        or not callable(verify_schema)
        or not callable(http_read)
        or (route_resolver is not None and not callable(route_resolver))
    ):
        raise WriteAdmissionError("invalid bounded direct admission request")
    connections = tuple(sorted(connections, key=lambda c: c.name))
    deadline = monotonic() + timeout_ms / 1000

    def remaining():
        value = int((deadline - monotonic()) * 1000)
        if value <= 0:
            raise WriteAdmissionError("write admission timed out")
        return value

    directory_fd = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        _private(directory_fd, directory=True)

        def check_identity():
            installed = InstallationIdentity.decode(
                _read_file(directory_fd, IDENTITY_FILENAME, 4096)
            )
            if installed != identity:
                raise WriteAdmissionError("installed runtime identity changed")

        check_identity()
        routes, members, snapshots = [], [], []
        names = tuple(c.name for c in connections)
        for connection in connections:
            route = discover_catalog_http_route(
                connection.driver,
                native_member_host=connection.native_member_host,
                expected_hostname=connection.expected_hostname,
                database=identity.target_database,
                scheme=connection.http_scheme,
                timeout_ms=remaining(),
            )
            if route_resolver is not None:
                origin = route_resolver(connection, route)
                # Mapping may change host/port, never silently downgrade TLS.
                if _origin(origin).scheme != connection.http_scheme:
                    raise WriteAdmissionError(
                        "mapped HTTP route changed configured protocol"
                    )
                route = CatalogHTTPRouteCandidate(
                    origin, route.hostname, route.database, route.database_uuid
                )
            before = _native_read(connection, _INVENTORY_SQL, {}, 8, remaining())
            member = _observe(connection, route, before, names, http=False)
            http_rows = http_read(connection, route, _INVENTORY_SQL, remaining(), 8)
            if _observe(connection, route, http_rows, names, http=True) != member:
                raise WriteAdmissionError(
                    "HTTP and native table/server identities differ"
                )
            verify_schema(identity.target_database, before)
            if _native_read(connection, _INVENTORY_SQL, {}, 8, remaining()) != before:
                raise WriteAdmissionError("native schema changed during admission")
            routes.append(route)
            members.append(member)
            snapshots.append(before)
        members = tuple(members)
        family = (
            "replicated"
            if members[0].tables[0].engine.startswith("Replicated")
            else "standalone"
        )
        digest = _keeper_digest(members) if family == "replicated" else ""
        document = {
            "database": identity.target_database,
            "environment": identity.environment,
            "family": family,
            "format": _FORMAT,
            "version": 1,
            "installation_sha256": json.loads(identity.encode())["identity_sha256"],
            "keeper_identity_sha256": digest,
            "members": [asdict(m) for m in members],
        }
        result = WriteAdmission.decode(
            _canonical({**document, "topology_sha256": _sha(document)}) + b"\n"
        )

        def recheck_inventory():
            for connection, route, snapshot in zip(
                connections, routes, snapshots, strict=True
            ):
                if (
                    _native_read(connection, _INVENTORY_SQL, {}, 8, remaining())
                    != snapshot
                ):
                    raise WriteAdmissionError(
                        "native inventory changed before publication"
                    )
                current = http_read(connection, route, _INVENTORY_SQL, remaining(), 8)
                if (
                    _observe(connection, route, current, names, http=True)
                    != result.members[names.index(connection.name)]
                ):
                    raise WriteAdmissionError(
                        "HTTP inventory changed before publication"
                    )

        def reattest():
            recheck_inventory()
            if family == "replicated":
                direct = {c.name: c for c in connections}

                def read(name, sql, params, limit, timeout):
                    return _native_read(
                        direct[name], sql, params, limit, min(timeout, remaining())
                    )

                observed = attest_keeper_membership(
                    _keeper_members(members), read, timeout_ms=remaining()
                )
                if observed != digest:
                    raise WriteAdmissionError("live Keeper membership digest differs")
                # Keeper is metadata, not a schema lock. A DDL change during its
                # cross-node proof must not install the earlier inventory.
                recheck_inventory()
            remaining()

        lock = os.open(
            ".write-admission-v1.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            _private(lock)
            # Busy admission is retryable by the lifecycle caller, never an unbounded wait.
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            check_identity()
            try:
                existing = _read_file(
                    directory_fd, WRITE_ADMISSION_FILENAME, MAX_ADMISSION_BYTES
                )
            except FileNotFoundError:
                existing = None
            if existing is not None and WriteAdmission.decode(existing) != result:
                raise WriteAdmissionError("installed write admission is immutable")
            reattest()
            check_identity()
            if existing is None:
                temporary = ".write-admission-" + uuid4().hex + ".tmp"
                fd = os.open(
                    temporary,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory_fd,
                )
                try:
                    with os.fdopen(fd, "wb") as target:
                        target.write(result.encode())
                        target.flush()
                        os.fsync(target.fileno())
                    # All producers hold this private lock. Rename avoids a hardlink
                    # crash window that the Go single-link reader correctly rejects.
                    os.rename(
                        temporary,
                        WRITE_ADMISSION_FILENAME,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                    )
                finally:
                    try:
                        os.unlink(temporary, dir_fd=directory_fd)
                    except FileNotFoundError:
                        pass
            # Also repair an uncertain previous directory-fsync after exact reattestation.
            os.fsync(directory_fd)
            return result
        finally:
            os.close(lock)
    finally:
        os.close(directory_fd)
