"""Choose the stable admitted source-local capture route before live baselines.

Only metadata SELECTs are performed. The caller owns every driver (including
failure cleanup) and creates the selected driver with the original SOURCE
credentials, never catalog writer/proof credentials. Existing write admission
is required; this helper does not install or refresh write authority.
"""

from __future__ import annotations

from uuid import UUID

from .source_capture import SourceCaptureError
from .source_capture_schema import (
    SourceCaptureSchemaError,
    _header,
    _identifier_argument,
    _lex,
    _nesting,
    _split,
    normalized_schema_sha256,
)
from .write_admission import DirectCatalogConnection, WriteAdmission

_ENGINES = frozenset(
    {
        "MergeTree",
        "ReplacingMergeTree",
        "ReplicatedMergeTree",
        "ReplicatedReplacingMergeTree",
    }
)
# Each subquery is independently bounded so absent/duplicate table or replica
# metadata cannot disappear behind a JOIN or lose the same-query server UUID.
_METADATA_SQL = """SELECT toString(serverUUID()) AS capture_server_uuid,
(SELECT groupArray(tuple(toString(uuid), engine, create_table_query)) FROM
 (SELECT uuid, engine, create_table_query FROM system.tables
  WHERE database=%(database)s AND name='spans' LIMIT 2)) AS capture_tables,
(SELECT groupArray(tuple(zookeeper_path, zookeeper_name)) FROM
 (SELECT zookeeper_path, zookeeper_name FROM system.replicas
  WHERE database=%(database)s AND table='spans' LIMIT 2)) AS capture_replicas"""


def _uuid(value):
    try:
        parsed = UUID(value)
        if str(parsed) != value or not parsed.int:
            raise ValueError
    except (TypeError, ValueError, AttributeError) as exc:
        raise SourceCaptureError("source route metadata has an invalid UUID") from exc
    return value


def _credentials(driver, database):
    if (
        getattr(driver, "database", None) != database
        or getattr(driver, "server_enforced_readonly", None) is not True
        or not isinstance(getattr(driver, "user", None), str)
        or not driver.user
        or not isinstance(getattr(driver, "password", None), str)
        or not callable(getattr(driver, "execute_read", None))
    ):
        raise SourceCaptureError(
            "source route requires the original SOURCE read-only credentials"
        )
    return driver.user, driver.password


def _schema(sql, database, table_uuid, engine, *, replica_route=False):
    """Reuse the existing bounded tokenizer/hash, not a second CREATE parser.

    Only cross-replica comparisons omit the first two explicit string engine
    arguments: actual Keeper identity is checked separately. All version/delete
    arguments, TTL, defaults, keys, projections and settings remain in the hash.
    """
    try:
        tokens = _lex(sql)
        depths = _nesting(tokens)
        observed_database, table, observed_uuid, opening = _header(tokens)
        if (observed_database or database, table) != (database, "spans") or (
            observed_uuid is not None and observed_uuid != table_uuid
        ):
            raise SourceCaptureError("source CREATE changed its exact table binding")
        closing = next(
            i
            for i in range(opening + 1, len(tokens))
            if tokens[i].text == ")" and depths[i] == 1
        )
        start = closing + 3
        if [t.word or t.text for t in tokens[closing + 1 : start]] != [
            "ENGINE",
            "=",
        ] or tokens[start].text != engine:
            raise SourceCaptureError("source CREATE engine differs from table metadata")
        if replica_route and start + 1 < len(tokens) and tokens[start + 1].text == "(":
            end = next(
                i
                for i in range(start + 2, len(tokens))
                if tokens[i].text == ")" and depths[i] == 1
            )
            args = (
                list(_split(tokens, depths, start + 2, end, 1))
                if end > start + 2
                else []
            )
            if args and tokens[args[0][0]].text.startswith("'"):
                if len(args) < 2 or any(
                    right - left != 1 or not tokens[left].text.startswith("'")
                    for left, right in args[:2]
                ):
                    raise SourceCaptureError(
                        "source replica engine arguments are ambiguous"
                    )
                # Preserve remaining engine arguments exactly; source hashing
                # itself excludes header UUID and normalizes only formatting.
                remaining = (
                    sql[tokens[args[2][0]].start : tokens[end].start]
                    if len(args) > 2
                    else ""
                )
                sql = (
                    sql[: tokens[start + 1].end] + remaining + sql[tokens[end].start :]
                )
        return normalized_schema_sha256(sql)
    except (SourceCaptureSchemaError, IndexError, StopIteration) as exc:
        raise SourceCaptureError("source route CREATE metadata is invalid") from exc


def _metadata(driver, database, *, admitted_servers):
    # No settings overrides: normal SOURCE is locked at readonly=1 with finite
    # server limits. Its same statement carries identity even for zero tables.
    result = driver.execute_read(
        _METADATA_SQL, {"database": database}, timeout_ms=30_000
    )
    try:
        rows, columns, _ = result
        names = tuple(c[0] if isinstance(c, tuple) else c for c in columns)
        if (
            names != ("capture_server_uuid", "capture_tables", "capture_replicas")
            or len(rows) != 1
        ):
            raise ValueError
        server, tables, replicas = rows[0]
        _uuid(server)
        if server not in admitted_servers:
            raise SourceCaptureError(
                "source route reached a server outside write admission"
            )
        if not isinstance(tables, (tuple, list)) or len(tables) != 1:
            raise SourceCaptureError(
                "source route needs exactly one canonical spans table"
            )
        if not isinstance(tables[0], (tuple, list)) or len(tables[0]) != 3:
            raise ValueError
        table_uuid, engine, create = tables[0]
        _uuid(table_uuid)
        if not isinstance(engine, str) or engine not in _ENGINES:
            raise SourceCaptureError("source route engine is not a supported MergeTree")
        if not isinstance(replicas, (tuple, list)):
            raise ValueError
        if engine.startswith("Replicated"):
            if (
                len(replicas) != 1
                or not isinstance(replicas[0], (tuple, list))
                or len(replicas[0]) != 2
            ):
                raise SourceCaptureError(
                    "source route has missing or ambiguous Keeper identity"
                )
            path, keeper = replicas[0]
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or len(path) > 4096
                or not isinstance(keeper, str)
                or not keeper
                or len(keeper) > 256
                or any(c in path + keeper for c in "\x00\r\n")
            ):
                raise SourceCaptureError(
                    "source route has no exact nonempty Keeper identity"
                )
        elif replicas:
            raise SourceCaptureError(
                "nonreplicated source unexpectedly has Keeper metadata"
            )
    except (TypeError, ValueError, IndexError) as exc:
        raise SourceCaptureError("source route metadata result is malformed") from exc
    _schema(create, database, table_uuid, engine)
    return (
        {"server_uuid": server, "table_uuid": table_uuid, "create_table_query": create},
        engine,
        tuple(replicas[0]) if replicas else None,
    )


def resolve_capture_source(
    source_driver, *, source_database, admission, connections, driver_factory
):
    """Return (WriteMember, selected SOURCE driver, exact three-field metadata).

    driver_factory(connection) receives the selected DirectCatalogConnection
    and must create AND own a SOURCE-credential driver at connection.driver's
    host/port, including error cleanup.
    Selection is always minimum admitted connection.name, never replica affinity
    to the load-balanced source probe, and must precede any parts baseline.
    """
    _identifier_argument(source_database, "source_database")
    credentials = _credentials(source_driver, source_database)
    if type(admission) is not WriteAdmission or not callable(driver_factory):
        raise SourceCaptureError("source route requires existing typed write admission")
    admission.__post_init__()
    if (
        not isinstance(connections, (tuple, list))
        or not 1 <= len(connections) <= 32
        or any(
            type(c) is not DirectCatalogConnection or not isinstance(c.name, str)
            for c in connections
        )
    ):
        raise SourceCaptureError(
            "source route requires bounded admitted direct connections"
        )
    connections = tuple(sorted(connections, key=lambda c: c.name))
    if tuple(c.name for c in connections) != tuple(
        m.name for m in admission.members
    ) or any(
        c.expected_hostname != m.hostname
        or getattr(c.driver, "database", None) != admission.database
        for c, m in zip(connections, admission.members, strict=True)
    ):
        raise SourceCaptureError("source route connections differ from write admission")
    connection, member = connections[0], admission.members[0]
    host, port = (
        getattr(connection.driver, "host", None),
        getattr(connection.driver, "port", None),
    )
    if (
        not isinstance(host, str)
        or not host
        or host != host.strip()
        or any(c in host for c in "\x00\r\n")
        or type(port) is not int
        or not 1 <= port <= 65535
    ):
        raise SourceCaptureError("source route has no exact admitted native endpoint")
    original, original_engine, original_keeper = _metadata(
        source_driver,
        source_database,
        admitted_servers={m.server_uuid for m in admission.members},
    )
    selected = driver_factory(connection)
    if (
        any(selected is c.driver for c in connections)
        or _credentials(selected, source_database) != credentials
        or _credentials(source_driver, source_database) != credentials
        or getattr(selected, "host", None) != host
        or getattr(selected, "port", None) != port
    ):
        raise SourceCaptureError(
            "selected route did not preserve the SOURCE identity and endpoint"
        )
    observed, engine, keeper = _metadata(
        selected, source_database, admitted_servers={member.server_uuid}
    )
    cross_node = original["server_uuid"] != observed["server_uuid"]
    if engine != original_engine:
        raise SourceCaptureError("selected source engine differs from original source")
    if cross_node:
        if (
            not engine.startswith("Replicated")
            or keeper != original_keeper
            or keeper is None
            or keeper[1] != "default"
            or admission.family != "replicated"
        ):
            # Admission proves common default Keeper identity, not arbitrary
            # auxiliary ensembles that happen to share a configured name.
            raise SourceCaptureError(
                "selected replica has no common admitted source Keeper identity"
            )
    elif observed["table_uuid"] != original["table_uuid"] or keeper != original_keeper:
        raise SourceCaptureError(
            "same source member changed its table UUID or Keeper identity"
        )
    if _schema(
        original["create_table_query"],
        source_database,
        original["table_uuid"],
        engine,
        replica_route=cross_node,
    ) != _schema(
        observed["create_table_query"],
        source_database,
        observed["table_uuid"],
        engine,
        replica_route=cross_node,
    ):
        raise SourceCaptureError(
            "selected source normalized schema differs from original source"
        )
    return member, selected, observed
