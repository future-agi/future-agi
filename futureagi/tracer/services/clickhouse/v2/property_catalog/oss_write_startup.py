"""Automatic DEV topology dispatch, after managed identity resolution only.

No schema installation, source reads, lifecycle work, or writer policy is hidden
here. The configured HTTP route is only a candidate: the real producer must
prove it serves the same native writer node and exact seven-table installation.
Exactly seven observed engines choose standalone or complete replicated proof.
No failure in either admission path can select the other topology as fallback.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
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
from .replicated_write_startup import (
    _expected_hosts,
    replicated_write_admission_context,
)
from .write_admission import (
    _ENGINES,
    DirectCatalogConnection,
    WriteAdmission,
    WriteAdmissionError,
    admit_catalog_writes,
)
from .write_endpoint import _uri_host

_PREFIX = "PROPERTY_CATALOG_DEV_"
_LEDGER = "FI_PROPERTY_CATALOG_LEDGER_CH_"
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,253}\Z", re.ASCII)
_METADATA_TABLES = frozenset({"databases", "tables", "replicas"})
_OPTIONAL_PROOF_TABLES = frozenset(
    {"clusters", "parts", "zookeeper", "zookeeper_connection", "query_log"}
)
_IDENTITY_SQL = """SELECT hostName() AS hostname,
toString(serverUUID()) AS server_uuid, currentDatabase() AS database,
currentUser() AS username, toString(uuid) AS database_uuid
FROM system.databases WHERE name=currentDatabase() LIMIT 2"""
_TABLES_SQL = """SELECT name, engine FROM system.tables
WHERE database=currentDatabase() ORDER BY name LIMIT 8"""


def _catalog_family(tables, columns):
    if (
        columns != ("name", "engine")
        or len(tables) != 7
        or any(not isinstance(row, (tuple, list)) or len(row) != 2 for row in tables)
        or tuple(row[0] for row in tables) != tuple(sorted(PROPERTY_CATALOG_TABLES))
    ):
        raise WriteAdmissionError(
            "OSS startup requires exactly seven physical catalog tables"
        )
    for family, prefix in (("standalone", ""), ("replicated", "Replicated")):
        if all(engine == prefix + _ENGINES[name] for name, engine in tables):
            return family
    raise WriteAdmissionError(
        "catalog engines are mixed or outside the exact format contract"
    )


def _port(value: Any) -> int:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        result = int(value)
    else:
        raise WriteAdmissionError("OSS admission requires an explicit configured port")
    if not 1 <= result <= 65535:
        raise WriteAdmissionError("OSS admission port is outside the listener range")
    return result


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise WriteAdmissionError(f"OSS admission requires explicit {label}")
    return value


def _http_origin(settings_object: Any, environ: Mapping[str, str]) -> str | None:
    # Existing infrastructure settings, not a new admission override. An explicit
    # ledger URL wins; otherwise CH25's HTTP mapping is only a candidate until the
    # producer compares the native server/database/table incarnations.
    configured = environ.get(_LEDGER + "URL")
    if configured is not None:
        url = urlsplit(configured)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or not url.port
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or any(c.isspace() for c in configured)
        ):
            raise WriteAdmissionError("invalid configured ledger HTTP origin")
        return configured
    config = getattr(settings_object, "CLICKHOUSE_V2", {}) or {}
    host, port = (
        config.get(key) if config.get(key) not in (None, "") else environ.get(key)
        for key in ("CH25_HOST", "CH25_HTTP_PORT")
    )
    if host in (None, "") and port in (None, ""):
        return None  # The producer discovers the native member's actual listener.
    return f"http://{_uri_host(_text(host, 'CH25_HOST'))}:{_port(port)}"


@contextmanager
def oss_write_admission_context(
    settings_object: Any,
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> Iterator[
    tuple[InstallationIdentity, WriteAdmission, tuple[DirectCatalogConnection, ...]]
]:
    """Read/prove existing identity and install admission before the first cycle.

    The supervisor calls this only for managed startup, not explicit legacy or
    status paths. Credentials use the existing Go ledger contract and can never
    default to the lifecycle/consumer INSERT principal.
    """
    environ = os.environ if environ is None else environ
    if not is_dev_control_plane_cloud_allowed(
        environment=getattr(settings_object, "ENV_TYPE", ""),
        cloud_deployment=getattr(settings_object, "CLOUD_DEPLOYMENT", ""),
    ):
        raise WriteAdmissionError(
            "OSS write admission requires an exact DEV environment"
        )
    fence = Path(
        _text(
            getattr(settings_object, _PREFIX + "REVISION_FENCE_FILE", None),
            "revision fence file",
        )
    )
    if not fence.is_absolute():
        raise WriteAdmissionError(
            "OSS write admission requires the absolute shared fence directory"
        )
    identity = load_identity(fence.parent / IDENTITY_FILENAME)
    target = getattr(settings_object, _PREFIX + "TARGET_DATABASE", None)
    identity.require_destination(
        environment="development",
        target_database=target,
        candidate_topic=getattr(
            settings_object,
            "PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC",
            "futureagi.oss.property-catalog.candidates.v1",
        ),
        ordered_topic=getattr(
            settings_object,
            "PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC",
            "futureagi.oss.property-catalog.ordered.v1",
        ),
    )
    if (
        getattr(settings_object, _PREFIX + "WRITE_CH_DATABASE", None) != target
        or environ.get(_LEDGER + "DATABASE", target) != target
    ):
        raise WriteAdmissionError(
            "OSS writer/ledger database differs from installed identity"
        )
    host = _text(
        getattr(settings_object, _PREFIX + "WRITE_CH_HOST", None), "writer host"
    )
    _uri_host(host)
    port = _port(getattr(settings_object, _PREFIX + "WRITE_CH_PORT", None))
    writer_user = _text(
        getattr(settings_object, _PREFIX + "WRITE_CH_USER", None), "writer user"
    )
    writer_password = getattr(settings_object, _PREFIX + "WRITE_CH_PASSWORD", None)
    proof_user = _text(environ.get(_LEDGER + "USERNAME"), "ledger proof user")
    proof_password = environ.get(_LEDGER + "PASSWORD")
    if (
        proof_user == writer_user
        or not _NAME.fullmatch(proof_user)
        or not isinstance(writer_password, str)
        or not isinstance(proof_password, str)
    ):
        raise WriteAdmissionError(
            "OSS admission requires separate explicit ledger credentials"
        )
    deadline = monotonic() + 30

    def remaining():
        result = int((deadline - monotonic()) * 1000)
        if result <= 0:
            raise WriteAdmissionError("OSS startup admission timed out")
        return result

    def query(client, sql, limit):
        budget = remaining()
        rows, columns, _ = client.execute_read(
            sql,
            {},
            timeout_ms=budget,
            settings={
                "readonly": 2,
                "max_threads": 1,
                "max_result_rows": limit,
                "max_result_bytes": 32768,
                "max_execution_time": max(1, (min(5000, budget) + 999) // 1000),
                "result_overflow_mode": "throw",
                "timeout_overflow_mode": "throw",
                "use_query_cache": 0,
            },
        )
        remaining()
        names = tuple(c[0] if isinstance(c, tuple) else c for c in columns)
        if len(rows) > limit or len(names) != len(set(names)):
            raise WriteAdmissionError("ambiguous OSS startup metadata")
        return rows, names

    def node(client, expected_user):
        rows, names = query(client, _IDENTITY_SQL, 2)
        if (
            names
            != ("hostname", "server_uuid", "database", "username", "database_uuid")
            or len(rows) != 1
            or len(rows[0]) != 5
        ):
            raise WriteAdmissionError("writer/ledger identity is incomplete")
        hostname, server_uuid, database, username, database_uuid = rows[0]
        if (
            not isinstance(hostname, str)
            or not _NAME.fullmatch(hostname)
            or (database, username) != (target, expected_user)
        ):
            raise WriteAdmissionError("writer/ledger node identity differs")
        for value in (server_uuid, database_uuid):
            try:
                parsed = UUID(value)
                if not parsed.int or str(parsed) != value:
                    raise ValueError
            except (TypeError, ValueError, AttributeError) as exc:
                raise WriteAdmissionError(
                    "writer/ledger incarnation is invalid"
                ) from exc
        return hostname, server_uuid, database_uuid

    if client_factory is None:
        from tracer.services.clickhouse.client import ClickHouseClient

        client_factory = ClickHouseClient
    with ExitStack() as stack:

        def client(user, password):
            remaining()
            result = client_factory(
                host=host,
                port=port,
                user=user,
                password=password,
                database=target,
                server_enforced_readonly=True,
                allow_query_settings_with_server_readonly=True,
                connect_timeout=min(5, remaining() / 1000),
                send_timeout=remaining() / 1000,
                receive_timeout=remaining() / 1000,
                pool_size=1,
            )
            stack.callback(result.close)
            return result

        # The seed may balance between replicas. No writer is constructed or
        # compared with this sampled node until standalone engines are proven.
        proof = client(proof_user, proof_password)
        expected = node(proof, proof_user)
        tables, names = query(proof, _TABLES_SQL, 8)
        family = _catalog_family(tables, names)
        grants, _ = query(proof, "SHOW GRANTS", 32)
        required = {f"{target}.{table}" for table in PROPERTY_CATALOG_TABLES} | {
            f"system.{table}" for table in _METADATA_TABLES
        }
        allowed = required | {f"system.{table}" for table in _OPTIONAL_PROOF_TABLES}
        seen = set()
        for row in grants:
            if len(row) != 1 or not isinstance(row[0], str):
                raise WriteAdmissionError("invalid ledger proof grant")
            grant = row[0].replace("`", "")
            prefix, suffix = "GRANT SELECT ON ", " TO " + proof_user
            if not grant.startswith(prefix) or not grant.endswith(suffix):
                raise WriteAdmissionError(
                    "ledger proof principal has non-SELECT or unreviewed grants"
                )
            table = grant[len(prefix) : -len(suffix)]
            if table not in allowed:
                raise WriteAdmissionError(
                    "ledger proof principal has unreviewed table grants"
                )
            seen.add(table)
        if not required.issubset(seen):
            raise WriteAdmissionError(
                "ledger proof principal lacks exact seven-table/metadata SELECT grants"
            )
        if family == "replicated":
            # The child re-discovers/attests all actual direct members, not the
            # seed observation. Release preliminary resources before delegation
            # and carry the remaining wall budget; never catch and fall back.
            stack.close()
            with replicated_write_admission_context(
                settings_object,
                environ=environ,
                client_factory=client_factory,
                prefix=_PREFIX,
                timeout_ms=remaining(),
            ) as admitted:
                remaining()
                yield admitted
            return

        expected_hosts = _expected_hosts(settings_object, _PREFIX)
        if expected_hosts and expected_hosts != (expected[0],):
            raise WriteAdmissionError(
                "standalone catalog conflicts with infrastructure member expectations"
            )
        origin = _http_origin(settings_object, environ)
        writer = client(writer_user, writer_password)
        if node(writer, writer_user) != expected:
            raise WriteAdmissionError(
                "ledger connection does not serve the configured writer node"
            )
        connection = DirectCatalogConnection(
            name=expected[0],
            native_member_host=host,
            expected_hostname=expected[0],
            driver=proof,
            http_scheme=urlsplit(origin).scheme if origin is not None else "http",
            proof_username=proof_user,
            proof_password=proof_password,
        )
        # Admission re-probes raw CREATE, DB/table UUIDs, the actual native listener,
        # and the mapped HTTP node. No expected rows/hashes are injected here.
        result = admit_catalog_writes(
            fence.parent,
            identity=identity,
            connections=(connection,),
            timeout_ms=remaining(),
            route_resolver=(lambda _connection, _discovered: origin)
            if origin is not None
            else None,
        )
        if node(writer, writer_user) != expected or any(
            (m.hostname, m.server_uuid, m.database_uuid) != expected
            for m in result.members
        ):
            raise WriteAdmissionError(
                "configured writer changed during startup admission"
            )
        # Keep the exact selected native proof connections alive for a caller's
        # transport/completion barrier. Their real host/port are not inferred
        # from HTTP URLs, and exit closes every client on success or failure.
        yield identity, result, (connection,)


def prepare_oss_write_admission(
    settings_object: Any,
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> WriteAdmission:
    """Managed startup wrapper; status and explicit legacy never call this path."""
    with oss_write_admission_context(
        settings_object, environ=environ, client_factory=client_factory
    ) as (_, admission, _):
        return admission
