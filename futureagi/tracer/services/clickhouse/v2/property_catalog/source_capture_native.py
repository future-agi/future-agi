"""Closed native operations for a source-local, derived span capture.

No arbitrary SQL or source mutation is exposed to the lifecycle. DDL uses the
same exclusive, identity-verified native connection as catalog delivery; the
journal, not this transport, owns whether an operation may be attempted. The
runtime factory must supply an admitted direct member and pre-provisioned narrow
grants. This module neither discovers credentials nor creates databases.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .codec import canonical_json, canonical_json_sha256
from .source_capture import (
    SourceCaptureError,
    SourceCaptureObservation,
    SourceCaptureSpec,
)
from .source_capture_reservations import SourceCaptureBackpressure
from .write_admission import WriteMember

_NAME = re.compile(r"[A-Za-z0-9_-]{1,256}\Z", re.ASCII)
_HASH = re.compile(r"[a-f0-9]{1,32}\Z", re.ASCII)
_SOURCE_ENGINES = frozenset(
    {
        "MergeTree",
        "ReplacingMergeTree",
        "ReplicatedMergeTree",
        "ReplicatedReplacingMergeTree",
    }
)
REQUIRED_CAPTURE_COLUMNS = frozenset(
    {
        "project_id",
        "observation_type",
        "service_name",
        "trace_id",
        "id",
        "start_time",
        "_version",
        "is_deleted",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "attributes_extra",
        "model",
    }
)


class QualifiedCaptureSchema(Protocol):
    """A checked, immutable DDL transform, not caller-provided SQL."""

    create_sql: str
    source_sha256: str
    capture_sha256: str
    source_database: str
    source_table: str
    source_uuid: str | None
    target_database: str
    target_table: str
    target_uuid: str
    required_stored_columns: tuple[str, ...]

    def verify_source(self, observed_create: str) -> None: ...

    def verify_capture(self, observed_create: str) -> None: ...


@dataclass(frozen=True)
class CaptureResourceBudget:
    """Factory-derived capacity; unrelated to catalog revision or workspace count.

    Retained bytes conservatively count hard-linked bytes in each capture. The
    factory must reserve these bounds against real source-local disk capacity;
    metadata checks alone are not a free-space reservation.
    """

    max_tables: int
    max_parts: int
    max_retained_bytes: int

    def __post_init__(self):
        for name, ceiling in (
            ("max_tables", 2),
            ("max_parts", 10_000),
            ("max_retained_bytes", 2**64 - 1),
        ):
            if (
                type(getattr(self, name)) is not int
                or not 1 <= getattr(self, name) <= ceiling
            ):
                raise ValueError("invalid source capture resource budget")


class NativeSourceCaptureBackend:
    """One exact spec and direct source member, with no reconnecting DDL retry."""

    def __init__(
        self,
        driver: Any,
        *,
        source_reader: Any,
        member: WriteMember,
        spec: SourceCaptureSpec,
        schema: QualifiedCaptureSchema,
        budget: CaptureResourceBudget,
        capacity_reservation: Callable[[SourceCaptureSpec, int], None],
        timeout_ms: int = 30_000,
    ):
        if type(spec) is not SourceCaptureSpec or type(member) is not WriteMember:
            raise TypeError(
                "capture requires a validated spec and admitted direct member"
            )
        spec.__post_init__()
        if type(budget) is not CaptureResourceBudget:
            raise TypeError("capture requires an explicit resource reservation")
        budget.__post_init__()
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30_000:
            raise ValueError("capture native timeout must be in [1, 30000] ms")
        if not callable(capacity_reservation):
            raise TypeError("source-local capacity must be reserved before capture")
        self._driver, self._member, self._spec, self._schema = (
            driver,
            member,
            spec,
            schema,
        )
        self._reader = source_reader
        self._budget, self._reserve, self._timeout = (
            budget,
            capacity_reservation,
            timeout_ms,
        )
        self._validate(spec)

    def _validate(self, spec):
        if type(spec) is not SourceCaptureSpec or spec != self._spec:
            raise SourceCaptureError("native capture cannot change its bound spec")
        spec.__post_init__()
        if (
            self._member.server_uuid != spec.source_server_uuid
            or getattr(self._driver, "database", None) != spec.source_database
            or getattr(self._driver, "server_enforced_readonly", None) is not False
            or not isinstance(getattr(self._driver, "user", None), str)
            or not self._driver.user
            or getattr(self._reader, "database", None) != spec.source_database
            or getattr(self._reader, "server_enforced_readonly", None) is not True
            or not isinstance(getattr(self._reader, "user", None), str)
            or not self._reader.user
            or self._reader.user == self._driver.user
            or self._schema.source_sha256 != spec.source_schema_sha256
            or self._schema.capture_sha256 != spec.capture_schema_sha256
            or self._schema.source_database != spec.source_database
            or self._schema.source_table != "spans"
            or self._schema.source_uuid not in {None, spec.source_table_uuid}
            or self._schema.target_database != spec.capture_database
            or self._schema.target_table != spec.capture_table
            or self._schema.target_uuid != spec.capture_table_uuid
        ):
            raise SourceCaptureError(
                "capture source-local capability or schema changed"
            )
        columns = self._schema.required_stored_columns
        if (
            not isinstance(columns, tuple)
            or not 1 <= len(columns) <= 256
            or any(not isinstance(c, str) or not c for c in columns)
            or len(set(columns)) != len(columns)
        ):
            raise SourceCaptureError(
                "capture schema has no complete resolved input-column contract"
            )

    def _read(self, sql, params, *, fields, max_rows):
        self._validate(self._spec)
        # Aggregate the bounded metadata result with its server identity in ONE
        # statement. Even zero returned objects must carry the actual identity;
        # a separate identity probe could borrow another connection/member.
        selected = ", ".join(f"`{name}`" for name in fields)
        wrapped = (
            "SELECT toString(serverUUID()) AS capture_server_uuid, "
            f"groupArray(tuple({selected})) AS capture_rows FROM ({sql})"
        )
        rows, columns, _ = self._reader.execute_read(
            wrapped,
            params,
            timeout_ms=self._timeout,
            settings={
                "readonly": 2,
                "max_threads": 1,
                "max_execution_time": self._timeout / 1000,
                "max_result_rows": 1,
                "max_result_bytes": 8 << 20,
                "result_overflow_mode": "throw",
                "timeout_overflow_mode": "throw",
                "use_query_cache": 0,
            },
        )
        names = tuple(c[0] if isinstance(c, tuple) else c for c in columns)
        if (
            names != ("capture_server_uuid", "capture_rows")
            or len(rows) != 1
            or len(rows[0]) != 2
        ):
            raise SourceCaptureError("capture metadata result shape or bound changed")
        observed_server, observed_rows = rows[0]
        if observed_server != self._spec.source_server_uuid:
            raise SourceCaptureError("capture metadata reached another source member")
        if (
            not isinstance(observed_rows, (list, tuple))
            or len(observed_rows) > max_rows
        ):
            raise SourceCaptureError("capture metadata exceeded its row bound")
        try:
            return tuple(dict(zip(fields, row, strict=True)) for row in observed_rows)
        except (TypeError, ValueError) as exc:
            raise SourceCaptureError("capture metadata row is malformed") from exc

    def _table(self, database, table):
        rows = self._read(
            "SELECT toString(serverUUID()) AS server_uuid, toString(uuid) AS table_uuid, "
            "engine, create_table_query FROM system.tables "
            "WHERE database=%(database)s AND name=%(table)s LIMIT 2",
            {"database": database, "table": table},
            fields=("server_uuid", "table_uuid", "engine", "create_table_query"),
            max_rows=2,
        )
        if len(rows) > 1:
            raise SourceCaptureError("capture table identity is ambiguous")
        if not rows:
            return None
        row = rows[0]
        if row["server_uuid"] != self._spec.source_server_uuid:
            raise SourceCaptureError("capture metadata reached another source member")
        if (
            not isinstance(row["create_table_query"], str)
            or not row["create_table_query"]
        ):
            raise SourceCaptureError(
                "capture table has no authoritative CREATE metadata"
            )
        return row

    def verify_source(self, spec):
        self._validate(spec)
        table = self._table(spec.source_database, "spans")
        if (
            table is None
            or table["table_uuid"] != spec.source_table_uuid
            or table["engine"] not in _SOURCE_ENGINES
        ):
            raise SourceCaptureError(
                "canonical source table incarnation or engine changed"
            )
        self._schema.verify_source(table["create_table_query"])

    @staticmethod
    def _uint(value):
        if type(value) is not int or not 0 <= value < 2**64:
            raise SourceCaptureError("capture metadata size is not UInt64")
        return value

    def _parts(self, database, table):
        rows = self._read(
            "SELECT name, toString(hash_of_all_files) AS checksum, rows, bytes_on_disk "
            "FROM system.parts WHERE database=%(database)s AND table=%(table)s AND active "
            "ORDER BY name LIMIT %(limit)s",
            {"database": database, "table": table, "limit": self._budget.max_parts + 1},
            fields=("name", "checksum", "rows", "bytes_on_disk"),
            max_rows=self._budget.max_parts + 1,
        )
        if len(rows) > self._budget.max_parts:
            raise SourceCaptureError("capture exceeds its reserved part bound")
        names = []
        for row in rows:
            if not isinstance(row["name"], str) or not _NAME.fullmatch(row["name"]):
                raise SourceCaptureError("capture has an invalid part name")
            if (
                not isinstance(row["checksum"], str)
                or not _HASH.fullmatch(row["checksum"])
                or int(row["checksum"], 16) == 0
            ):
                raise SourceCaptureError("capture has no complete part checksum")
            names.append(row["name"])
            self._uint(row["rows"])
            self._uint(row["bytes_on_disk"])
        if names != sorted(set(names)):
            raise SourceCaptureError(
                "capture part inventory is unordered or duplicated"
            )
        return rows

    def check_capacity(self, spec):
        self._validate(spec)
        tables = self._read(
            "SELECT name FROM system.tables WHERE database=%(database)s ORDER BY name LIMIT %(limit)s",
            {"database": spec.capture_database, "limit": self._budget.max_tables + 1},
            fields=("name",),
            max_rows=self._budget.max_tables + 1,
        )
        names = []
        for table in tables:
            name = table["name"]
            if (
                not isinstance(name, str)
                or re.fullmatch(r"spans_[0-9a-f]{32}", name) is None
            ):
                raise SourceCaptureError(
                    "capture namespace contains an unexpected object"
                )
            names.append(name)
        if len(set(names)) != len(names):
            raise SourceCaptureError("capture namespace has duplicate table metadata")
        if len(tables) >= self._budget.max_tables:
            raise SourceCaptureBackpressure("source capture slots are still in use")
        total = 0
        for name in names:
            total += sum(
                p["bytes_on_disk"] for p in self._parts(spec.capture_database, name)
            )
        source = self._parts(spec.source_database, "spans")
        proposed = sum(p["bytes_on_disk"] for p in source)
        if total + proposed > self._budget.max_retained_bytes:
            raise SourceCaptureBackpressure(
                "capture would exceed reserved retained bytes"
            )
        self._reserve(spec, proposed)

    def _command(self, spec, sql, query_id, *, before_send=None):
        # ordinary_once owns the connected exchange/watchdog. The durable
        # manager saved its intent before reaching here, including uncertain DDL.
        from .native_write_transport import ordinary_once

        self._validate(spec)

        def checked_send():
            self._validate(spec)
            if before_send is not None:
                before_send()

        return ordinary_once(
            self._driver,
            member=self._member,
            database=spec.source_database,
            user=self._driver.user,
            sql=sql,
            query_id=query_id,
            settings={
                "max_execution_time": self._timeout / 1000,
                "max_threads": 1,
                "data_type_default_nullable": 0,
                "lock_acquire_timeout": self._timeout / 1000,
            },
            timeout_ms=self._timeout,
            before_send=checked_send,
        )

    def create_empty_once(self, spec, *, before_send):
        self.verify_source(spec)
        if self._table(spec.capture_database, spec.capture_table) is not None:
            raise SourceCaptureError("new capture CREATE target already exists")
        if not callable(before_send):
            raise TypeError("CREATE needs a durable dispatch callback")
        self._command(
            spec,
            self._schema.create_sql,
            spec.query_id + "-create",
            before_send=before_send,
        )
        observed = self.inspect(spec)
        if (
            observed is None
            or observed.parts
            or observed.rows
            or observed.bytes_on_disk
        ):
            raise SourceCaptureError("expected exact empty capture before ATTACH")

    def inspect(self, spec):
        self._validate(spec)
        table = self._table(spec.capture_database, spec.capture_table)
        if table is None:
            return None
        if (
            table["table_uuid"] != spec.capture_table_uuid
            or table["engine"] != "MergeTree"
        ):
            raise SourceCaptureError("derived capture incarnation or engine changed")
        self._schema.verify_capture(table["create_table_query"])
        parts = self._parts(spec.capture_database, spec.capture_table)
        physical = self._read(
            "SELECT name, arraySort(groupUniqArray(column)) AS columns "
            "FROM system.parts_columns WHERE database=%(database)s AND table=%(table)s AND active AND rows>0 "
            "AND column IN %(columns)s GROUP BY name ORDER BY name LIMIT %(limit)s",
            {
                "database": spec.capture_database,
                "table": spec.capture_table,
                "columns": tuple(sorted(self._schema.required_stored_columns)),
                "limit": self._budget.max_parts + 1,
            },
            fields=("name", "columns"),
            max_rows=self._budget.max_parts + 1,
        )
        expected = tuple(
            {"name": p["name"], "columns": sorted(self._schema.required_stored_columns)}
            for p in parts
            if p["rows"]
        )
        if physical != expected:
            raise SourceCaptureError(
                "captured parts do not physically store every catalog input"
            )
        if (
            self._parts(spec.capture_database, spec.capture_table) != parts
            or self._table(spec.capture_database, spec.capture_table) != table
        ):
            raise SourceCaptureError("capture changed during physical verification")
        size = sum(p["bytes_on_disk"] for p in parts)
        if size > self._budget.max_retained_bytes:
            raise SourceCaptureError("capture exceeds reserved retained bytes")
        return SourceCaptureObservation(
            spec.source_server_uuid,
            spec.capture_database,
            spec.capture_table,
            spec.capture_table_uuid,
            spec.capture_schema_sha256,
            canonical_json_sha256(
                canonical_json({"parts": list(parts)}, max_bytes=8 << 20)
            ),
            len(parts),
            sum(p["rows"] for p in parts),
            size,
        )

    def attach_once(self, spec, *, query_id):
        self.verify_source(spec)
        if query_id != spec.query_id:
            raise SourceCaptureError("capture ATTACH query identity changed")
        before = self.inspect(spec)
        if before is None or before.parts or before.rows or before.bytes_on_disk:
            raise SourceCaptureError("capture ATTACH target is not exactly empty")
        self._command(
            spec,
            f"ALTER TABLE `{spec.capture_database}`.`{spec.capture_table}` "
            f"ATTACH PARTITION ALL FROM `{spec.source_database}`.`spans`",
            query_id,
        )

    def drop_owned(self, spec, *, require_empty=False):
        self._validate(spec)
        table = self._table(spec.capture_database, spec.capture_table)
        if table is None:
            return
        if (
            table["table_uuid"] != spec.capture_table_uuid
            or table["engine"] != "MergeTree"
        ):
            raise SourceCaptureError("cannot discard another capture incarnation")
        # Ownership/schema, not successful contents, authorizes derived cleanup.
        # A failed ATTACH or missing physical input column is exactly why this
        # unpublished table may need to be discarded.
        self._schema.verify_capture(table["create_table_query"])
        if require_empty and self._parts(spec.capture_database, spec.capture_table):
            raise SourceCaptureError(
                "unacknowledged CREATE unexpectedly contains parts"
            )
        self._command(
            spec,
            f"DROP TABLE `{spec.capture_database}`.`{spec.capture_table}` SYNC",
            spec.query_id + "-drop",
        )
