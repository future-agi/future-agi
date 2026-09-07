"""All-member native write evidence over an already admitted installation.

This is SELECT-only. Neither matching counts, a local ACK, nor an expired
request authorizes replay. The caller owns its exact durable write attempt and
must retain it until settlement AND all-member coverage have been established.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import UUID

from tracer.services.clickhouse.server_readonly import (
    _top_level_tokens,
    ensure_read_statement,
)

from .activation_control import ACTIVATION_CONTROL_COLUMNS
from .coordinator import _SOURCE_STREAM_COLUMNS
from .publisher import _DELIVERY_COLUMNS
from .state_store import _ACTIVATION_COLUMNS, _CHECKPOINT_COLUMNS
from .wire import _DEFINITION_COLUMNS, _VALUE_COLUMNS
from .write_admission import (
    DirectCatalogConnection,
    WriteAdmission,
    _canonical,
    _native_read,
    reattest_catalog_writes,
)

_COLUMNS = {
    "property_definition_catalog": _DEFINITION_COLUMNS,
    "span_attribute_value_catalog": _VALUE_COLUMNS,
    "property_catalog_checkpoints": _CHECKPOINT_COLUMNS,
    "property_catalog_activations": _ACTIVATION_COLUMNS,
    "property_catalog_source_streams": _SOURCE_STREAM_COLUMNS,
    "property_catalog_deliveries": _DELIVERY_COLUMNS,
    "property_catalog_activation_control_events": ACTIVATION_CONTROL_COLUMNS,
}
_VALUE_AGGREGATES = frozenset(
    {"value_json", "value_search_text_folded", "first_seen", "last_seen"}
)
_MAX_ROWS = 16_384
_MAX_AGREEMENT_BYTES = 16 << 20
_CHUNK_ROWS = 32


class NativeWriteProofError(RuntimeError):
    """The exact native write has not been proved complete on every member."""


@dataclass(frozen=True)
class NativeReadAgreement:
    """Internal query-specific permission to ignore exact duplicate row order.

    Adapters, not user settings, classify reviewed state queries. Strict is the
    default. A cap-plus-one query must return fewer than its outer LIMIT on
    EVERY member, before deduplication. A complete-result query has no outer
    LIMIT and relies on the unchanged server throw/byte/row bounds. Nested
    selection windows are not interpreted as overflow sentinels here.
    """

    kind: str = "strict"
    limit_parameter: str | None = None
    limit: int | None = None

    def __post_init__(self):
        if type(self.kind) is not str or self.kind not in {
            "strict",
            "cap_plus_one",
            "complete_result",
        }:
            raise NativeWriteProofError("unknown native read agreement contract")
        if self.limit_parameter is not None and (
            type(self.limit_parameter) is not str
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.limit_parameter) is None
        ):
            raise NativeWriteProofError("invalid agreement limit parameter")
        if self.limit is not None:
            self._require_limit(self.limit)
        if self.kind == "cap_plus_one":
            if self.limit is None and self.limit_parameter is None:
                raise NativeWriteProofError("cap-plus-one agreement requires a limit")
        elif self.limit is not None or self.limit_parameter is not None:
            raise NativeWriteProofError("only cap-plus-one agreement accepts limits")

    @staticmethod
    def _require_limit(value):
        if type(value) is not int or not 2 <= value < 1 << 64:
            raise NativeWriteProofError(
                "agreement limit must be an integer in [2, 2^64)"
            )
        return value

    @classmethod
    def cap_plus_one(cls, *, limit_parameter=None, limit=None):
        """limit alone binds literal SQL; with a parameter it pins its value."""
        return cls("cap_plus_one", limit_parameter, limit)

    @classmethod
    def complete_result(cls):
        return cls("complete_result")

    def _overflow_at(self, sql, params):
        # Revalidate the contract at use, even if an object was deserialized or
        # mutated outside the frozen dataclass's normal construction path.
        self.__post_init__()
        if self.kind == "strict":
            return None
        limits = [
            end for token, _start, end in _top_level_tokens(sql) if token == "LIMIT"
        ]
        if self.kind == "complete_result":
            if limits:
                raise NativeWriteProofError(
                    "complete-result agreement forbids an outer LIMIT"
                )
            return None
        if self.limit_parameter is not None:
            value = self._require_limit(params.get(self.limit_parameter))
            if self.limit is not None and value != self.limit:
                raise NativeWriteProofError(
                    "agreement limit parameter differs from its contract"
                )
            expected = f"%({self.limit_parameter})s"
        else:
            value = self._require_limit(self.limit)
            expected = str(value)
        # Exact terminal syntax: no OFFSET, WITH TIES, LIMIT BY, comments,
        # nested/quoted lookalikes, or a different parameter can supply proof.
        if len(limits) != 1 or sql[limits[0] :].strip() != expected:
            raise NativeWriteProofError(
                "agreement requires the exact terminal outer LIMIT"
            )
        return value


STRICT_READ_AGREEMENT = NativeReadAgreement()


def _agreement_columns(columns):
    if not isinstance(columns, (list, tuple)) or not columns:
        raise NativeWriteProofError("catalog agreement columns are incomplete")
    signature = []
    for column in columns:
        if isinstance(column, str):
            name, native_type = column, None
        elif (
            isinstance(column, tuple)
            and len(column) == 2
            and isinstance(column[0], str)
            and isinstance(column[1], str)
        ):
            name, native_type = column
        else:
            raise NativeWriteProofError(
                "catalog agreement column metadata is malformed"
            )
        if not name:
            raise NativeWriteProofError("catalog agreement column name is empty")
        signature.append((name, native_type))
    names = tuple(name for name, _type in signature)
    if len(names) != len(set(names)):
        raise NativeWriteProofError("catalog agreement column names are duplicated")
    return names, _canonical(signature)


def _parameter(value: Any) -> Any:
    # clickhouse-driver's ordinary datetime parameter escaping drops fractional
    # seconds. Use exact UTC text for comparison with DateTime64(6), never float.
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise NativeWriteProofError("native write evidence requires UTC times")
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")
    if isinstance(value, UUID):
        return str(value)
    if value is None or isinstance(value, str):
        return value
    if type(value) is int and -(1 << 63) <= value < 1 << 64:
        return value
    if isinstance(value, (tuple, list)):
        return [_parameter(item) for item in value]
    raise NativeWriteProofError("unsupported native catalog proof parameter")


def coverage_query(
    database: str,
    table: str,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> tuple[str, dict[str, Any]]:
    """Check exact rows/aggregate coverage inside one immutable tenant prefix."""
    from .publisher import require_catalog_database

    require_catalog_database(database)
    expected = _COLUMNS.get(table)
    if (
        expected is None
        or tuple(columns) != expected
        or not 1 <= len(rows) <= _CHUNK_ROWS
        or any(set(row) != set(expected) for row in rows)
    ):
        raise NativeWriteProofError("incomplete native catalog coverage request")
    prefix = ("organization_id", "workspace_id")
    if table != "property_catalog_activation_control_events":
        prefix += ("catalog_epoch", "catalog_revision", "build_token")
    if any(any(row[key] != rows[0][key] for key in prefix) for row in rows):
        raise NativeWriteProofError("native proof cannot mix tenant/build scopes")
    params = {"scope_" + key: _parameter(rows[0][key]) for key in prefix}
    predicates = [f"`{key}` = %(scope_{key})s" for key in prefix]
    checks = []
    for index, row in enumerate(rows):
        terms = {}
        for key in columns:
            parameter = f"r{index}_{key}"
            if row[key] is None:
                terms[key] = f"isNull(`{key}`)"
            else:
                params[parameter] = _parameter(row[key])
                terms[key] = f"`{key}` = %({parameter})s"
        condition = " AND ".join(terms.values())
        check = f"countIf({condition}) > 0"
        if table == "span_attribute_value_catalog":
            identity = " AND ".join(
                term for key, term in terms.items() if key not in _VALUE_AGGREGATES
            )
            # Physical aggregate rows may merge or overlap. Prove a nonempty
            # logical key, no value disagreement, and enclosing min/max times.
            check = (
                f"countIf({identity}) > 0 AND "
                f"countIf(({identity}) AND NOT ("
                f"{terms['value_json']} AND {terms['value_search_text_folded']})) = 0 "
                f"AND minIf(first_seen, {identity}) <= %(r{index}_first_seen)s "
                f"AND maxIf(last_seen, {identity}) >= %(r{index}_last_seen)s"
            )
        checks.append(f"toUInt8({check}) AS r{index}")
    return (
        f"SELECT {', '.join(checks)} FROM `{database}`.`{table}` "
        f"WHERE {' AND '.join(predicates)}",
        params,
    )


def coverage_plan(database, table, rows, columns):
    """Prevalidate the WHOLE statement before any dispatch or proof read."""
    if not 1 <= len(rows) <= _MAX_ROWS:
        raise NativeWriteProofError("native coverage row bound exceeded")
    prefix = ("organization_id", "workspace_id")
    if table != "property_catalog_activation_control_events":
        prefix += ("catalog_epoch", "catalog_revision", "build_token")
    if any(
        any(key not in row or row[key] != rows[0].get(key) for key in prefix)
        for row in rows
    ):
        raise NativeWriteProofError("native coverage cannot mix tenant/build scopes")
    return [
        coverage_query(database, table, rows[start : start + _CHUNK_ROWS], columns)
        for start in range(0, len(rows), _CHUNK_ROWS)
    ]


class NativeWriteProof:
    """Actual proof transport; caller-selected connections must be direct nodes."""

    def __init__(
        self,
        *,
        directory,
        identity,
        admission: WriteAdmission,
        connections: Sequence[DirectCatalogConnection],
        route_resolver=None,
    ):
        if any(type(c) is not DirectCatalogConnection for c in connections):
            raise NativeWriteProofError("native proof requires direct connections")
        self.admission = admission
        self.connections = tuple(sorted(connections, key=lambda c: c.name))
        if (
            type(admission) is not WriteAdmission
            or tuple(c.name for c in self.connections)
            != tuple(m.name for m in admission.members)
            or admission.database != identity.target_database
            or admission.environment != identity.environment
        ):
            raise NativeWriteProofError("native proof requires exact admitted members")
        self.directory = directory
        self.identity = identity
        self.route_resolver = route_resolver

    @staticmethod
    def _budget(timeout_ms):
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30_000:
            raise NativeWriteProofError("native proof requires a bounded deadline")
        deadline = monotonic() + timeout_ms / 1000

        def remaining():
            result = int((deadline - monotonic()) * 1000)
            if result <= 0:
                raise NativeWriteProofError("native proof deadline exceeded")
            return result

        return remaining

    def attest(self, *, timeout_ms: int) -> None:
        # Fresh native/HTTP/CREATE/Keeper proof, without repeating the
        # installation producer's discovery, publication lock and fsync.
        reattest_catalog_writes(
            self.directory,
            identity=self.identity,
            admission=self.admission,
            connections=self.connections,
            route_resolver=self.route_resolver,
            timeout_ms=timeout_ms,
        )

    def cover(self, table, rows, *, columns, timeout_ms: int) -> None:
        remaining = self._budget(timeout_ms)
        # Validate all input before the first read, including later chunks.
        queries = coverage_plan(self.admission.database, table, rows, columns)
        for connection in self.connections:
            for index, (sql, params) in enumerate(queries):
                chunk = rows[index * _CHUNK_ROWS : (index + 1) * _CHUNK_ROWS]
                observed = _native_read(connection, sql, params, 1, remaining())
                expected_keys = {f"r{i}" for i in range(len(chunk))}
                if (
                    len(observed) != 1
                    or set(observed[0]) != expected_keys
                    or any(
                        type(value) is not int or value != 1
                        for value in observed[0].values()
                    )
                ):
                    raise NativeWriteProofError(
                        "native write coverage absent on an admitted member"
                    )
        remaining()

    def agreed_read(
        self,
        sql,
        params,
        *,
        timeout_ms: int,
        agreement: NativeReadAgreement = STRICT_READ_AGREEMENT,
    ):
        """Return bounded catalog state only when every serving member agrees.

        Metadata proof readers are deliberately separate from the INSERT
        principal. No source reads, system reads, or cross-database SQL are
        admitted through this state-read adapter.
        """
        ensure_read_statement(sql)
        references = set(re.findall(r"`([^`]+)`\.`([^`]+)`", sql))
        if not references or any(
            database != self.admission.database or table not in _COLUMNS
            for database, table in references
        ):
            raise NativeWriteProofError(
                "agreement reads require qualified catalog tables"
            )
        if type(agreement) is not NativeReadAgreement or not isinstance(
            params, Mapping
        ):
            raise NativeWriteProofError(
                "agreement reads require a typed contract and parameters"
            )
        query_params = dict(params)
        overflow_at = agreement._overflow_at(sql, query_params)
        connections = tuple(self.connections)
        if (
            not connections
            or any(type(c) is not DirectCatalogConnection for c in connections)
            or tuple(c.name for c in connections)
            != tuple(m.name for m in self.admission.members)
        ):
            raise NativeWriteProofError(
                "agreement requires every exact admitted member"
            )
        remaining = self._budget(timeout_ms)
        expected = None
        selected = None
        for connection in connections:
            rows, columns, _ = connection.driver.execute_read(
                sql,
                dict(query_params),
                timeout_ms=remaining(),
                settings={
                    "readonly": 2,
                    "max_threads": 1,
                    "max_result_rows": _MAX_ROWS,
                    "max_result_bytes": _MAX_AGREEMENT_BYTES,
                    "max_memory_usage": 128 << 20,
                    "max_execution_time": max(1, (remaining() + 999) // 1000),
                    "result_overflow_mode": "throw",
                    "timeout_overflow_mode": "throw",
                    "use_query_cache": 0,
                },
            )
            names, column_signature = _agreement_columns(columns)
            if not isinstance(rows, (list, tuple)) or len(rows) > _MAX_ROWS:
                raise NativeWriteProofError("catalog agreement result is incomplete")
            if overflow_at is not None and len(rows) >= overflow_at:
                raise NativeWriteProofError(
                    f"catalog agreement raw overflow on {connection.name}: "
                    f"{len(rows)} rows reached sentinel {overflow_at}"
                )
            if any(
                not isinstance(row, (tuple, list)) or len(row) != len(names)
                for row in rows
            ):
                raise NativeWriteProofError("catalog agreement row shape is incomplete")
            # Preserve typed rows for existing state parsers. Canonical comparison
            # normalizes UUID and UTC times without losing microseconds.
            # Charge every physical row, including duplicates, BEFORE making a
            # set. Size is the full canonical [columns,[rows...]] representation.
            raw_bytes = len(column_signature) + 5 + max(0, len(rows) - 1)
            encoded_rows = []
            for row in rows:
                encoded = _canonical([_parameter(value) for value in row])
                raw_bytes += len(encoded)
                if raw_bytes > _MAX_AGREEMENT_BYTES:
                    raise NativeWriteProofError(
                        "catalog agreement result exceeds byte bound"
                    )
                encoded_rows.append(encoded)
            if raw_bytes > _MAX_AGREEMENT_BYTES:
                raise NativeWriteProofError(
                    "catalog agreement result exceeds byte bound"
                )
            canonical = (
                column_signature,
                tuple(encoded_rows)
                if agreement.kind == "strict"
                else frozenset(encoded_rows),
            )
            if expected is not None and expected != canonical:
                raise NativeWriteProofError(
                    "catalog state disagrees across serving members"
                )
            if expected is None:
                expected = canonical
                selected = tuple(dict(zip(names, row, strict=True)) for row in rows)
        remaining()
        return selected

    def settled(self, attempt: Mapping, *, timeout_ms: int) -> bool:
        """Only positive original-query completion can settle an uncertain write.

        The write executor must freeze a fresh UUID, exact SQL, parameters digest
        in log_comment, user and settings before dispatching once. Missing logs,
        exceptions, multiple results or profile overrides return no success.
        This method never retries an INSERT or enables server query logging.
        """
        from .native_write_journal import native_insert_sql

        remaining = self._budget(timeout_ms)
        quorum = len(self.connections) if self.admission.family == "replicated" else 0
        try:
            expected_sql = native_insert_sql(
                self.admission.database,
                attempt["table"],
                attempt["columns"],
                quorum=quorum,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise NativeWriteProofError(
                "native settlement intent lacks exact SQL pins"
            ) from exc
        if attempt.get("sql") != expected_sql:
            raise NativeWriteProofError("native settlement intent lacks exact SQL pins")
        members = {c.name: c for c in self.connections}
        if attempt.get("member") not in members:
            raise NativeWriteProofError("native settlement member is not admitted")
        sql = """SELECT query_id, type, exception_code, current_database, user,
query, log_comment, written_rows,
Settings['async_insert'] AS async_insert,
Settings['insert_quorum'] AS insert_quorum,
Settings['insert_quorum_parallel'] AS insert_quorum_parallel
FROM system.query_log WHERE query_id = %(query_id)s AND is_initial_query = 1
AND type IN ('QueryFinish', 'ExceptionBeforeStart', 'ExceptionWhileProcessing')
ORDER BY event_time_microseconds LIMIT 2"""
        observed = _native_read(
            members[attempt["member"]],
            sql,
            {"query_id": attempt["query_id"]},
            2,
            remaining(),
        )
        expected = {
            "query_id": attempt["query_id"],
            "type": "QueryFinish",
            "exception_code": 0,
            "current_database": self.admission.database,
            "user": attempt["user"],
            "query": attempt["sql"],
            "log_comment": attempt["parameters_sha256"],
            "written_rows": attempt["row_count"],
        }
        remaining()
        # system.query_log.Settings is a changed-settings map, not a complete
        # effective profile. Exact completed SQL above pins all three settings;
        # an omitted entry is acceptable ONLY with that positive SQL witness.
        # Explicit contradictory values still invalidate the completion.
        pins = {
            "async_insert": "0",
            "insert_quorum": str(quorum),
            "insert_quorum_parallel": "1",
        }
        if len(observed) != 1 or set(observed[0]) != set(expected) | set(pins):
            return False
        row = observed[0]
        return all(
            row[key] == value and type(row[key]) is type(value)
            for key, value in expected.items()
        ) and all(
            type(row[key]) is str and row[key] in ("", value)
            for key, value in pins.items()
        )
