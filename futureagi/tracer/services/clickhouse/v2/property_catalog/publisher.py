"""Bounded, allowlisted ClickHouse publisher for unified v1 envelopes.

The publisher deliberately accepts a tiny client surface instead of a raw
``clickhouse-connect`` object.  The adapter must expose the database identity
it is connected to and must preserve the fully-qualified table name supplied
here.  That makes a configuration mix-up fail before the first write.

Data chunks are append-only logical states.  Replaying identical definition
rows is harmless because readers resolve the same ``state_sha256`` and
replaying identical aggregate value rows preserves ``min``/``max``/``anyLast``.
We additionally pass a stable insert-deduplication token for every chunk and
the ledger row.  The logical identity remains the safety boundary on local
MergeTree tables where server-side insert deduplication may be disabled.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Protocol

from .activation import ManifestStreamRole, RevisionBuildPlan
from .codec import canonical_uuid, require_sha256
from .database import (
    PRODUCTION_PROPERTY_CATALOG_DATABASE,
    configured_production_property_catalog_database,
)
from .models import PropertyCatalogEnvelope, SourceAdapter
from .runtime_limits import RUNTIME_LIMITS
from .wire import encode_envelope

PROPERTY_CATALOG_TABLES = frozenset(
    {
        "property_definition_catalog",
        "span_attribute_value_catalog",
        "property_catalog_checkpoints",
        "property_catalog_activations",
        "property_catalog_deliveries",
        "property_catalog_source_streams",
    }
)
CATALOG_DATABASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Backwards-compatible export for publisher callers. The canonical identity is
# shared with the read path so production admission cannot drift by module.
PROD_CATALOG_DATABASE = PRODUCTION_PROPERTY_CATALOG_DATABASE
RESERVED_CATALOG_DATABASES = frozenset(
    {"default", "futureagi", "information_schema", "system"}
)
_ENVELOPE_WRITE_TABLES = frozenset(
    {
        "property_definition_catalog",
        "span_attribute_value_catalog",
        "property_catalog_deliveries",
    }
)
_MAX_SOURCE_STREAM_STATES = 16
_DELIVERY_COLUMNS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "source_adapter",
    "producer_stream_id",
    "sequence",
    "envelope_format",
    "envelope_version",
    "envelope_id",
    "payload_sha256",
    "previous_payload_sha256",
    "source_batch_digest",
    "outcome",
    "terminal",
    "gap_reasons",
    "source_rows",
    "definition_rows",
    "value_rows",
    "tombstone_rows",
    "transport",
    "kafka_partition",
    "kafka_offset",
    "delivered_at",
    "_version",
)
_NATIVE_DATETIME64_COLUMNS = {
    "property_definition_catalog": {
        "first_seen": True,
        "last_seen": True,
        "deleted_at": True,
        "emitted_at": False,
    },
    "span_attribute_value_catalog": {
        "first_seen": False,
        "last_seen": False,
    },
    "property_catalog_deliveries": {"delivered_at": False},
}
_WIRE_DATETIME64_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


class PropertyCatalogPublishError(RuntimeError):
    pass


class CatalogPublishClient(Protocol):
    catalog_database: str

    def query(
        self, sql: str, params: Mapping[str, Any], *, timeout_ms: int
    ) -> Sequence[Mapping[str, Any]]: ...

    def insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class CatalogWriteLease:
    """Exact fresh revision assignment required before any catalog write."""

    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    source_adapter: SourceAdapter
    producer_stream_id: str
    build_plan_json: str
    build_lease_sha256: str
    expires_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("organization_id", "workspace_id", "build_token"):
            object.__setattr__(
                self,
                field_name,
                canonical_uuid(getattr(self, field_name), field=field_name),
            )
        if type(self.catalog_epoch) is not int or not 1 <= self.catalog_epoch < (
            1 << 16
        ):
            raise ValueError("catalog_epoch must be a positive UInt16")
        if type(self.catalog_revision) is not int or not 1 <= self.catalog_revision < (
            1 << 64
        ):
            raise ValueError("catalog_revision must be a positive UInt64")
        if type(
            self.projection_version
        ) is not int or not 1 <= self.projection_version < (1 << 16):
            raise ValueError("projection_version must be a positive UInt16")
        if not isinstance(self.source_adapter, SourceAdapter):
            raise TypeError("source_adapter must be a SourceAdapter")
        object.__setattr__(
            self,
            "producer_stream_id",
            canonical_uuid(self.producer_stream_id, field="producer_stream_id"),
        )
        require_sha256(self.build_lease_sha256, field="build_lease_sha256")
        if (
            self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() != UTC.utcoffset(self.expires_at)
        ):
            raise ValueError("expires_at must be timezone-aware UTC")
        plan = RevisionBuildPlan.from_json(self.build_plan_json)
        if (
            plan.organization_id != self.organization_id
            or plan.workspace_id != self.workspace_id
            or plan.catalog_epoch != self.catalog_epoch
            or plan.catalog_revision != self.catalog_revision
            or plan.build_token != self.build_token
            or plan.projection_version != self.projection_version
            or plan.sha256 != self.build_lease_sha256
            or (self.source_adapter, self.producer_stream_id)
            not in {stream.key for stream in plan.streams}
        ):
            raise ValueError("catalog write lease does not match its build plan")


@dataclass(slots=True)
class SharedCatalogDeadline:
    """One shrinking wall shared by source reads and all catalog operations."""

    wall_ms: int = RUNTIME_LIMITS.publisher_wall_ms
    clock: Callable[[], float] = monotonic
    cancelled: Callable[[], bool] = lambda: False
    _deadline: float = 0.0

    def __post_init__(self) -> None:
        if (
            type(self.wall_ms) is not int
            or not 1 <= self.wall_ms <= RUNTIME_LIMITS.deadline_max_wall_ms
        ):
            raise ValueError(
                f"catalog wall_ms must be in [1, {RUNTIME_LIMITS.deadline_max_wall_ms}]"
            )
        if not callable(self.cancelled):
            raise TypeError("catalog cancellation probe must be callable")
        self._deadline = self.clock() + self.wall_ms / 1_000

    def remaining_ms(self, *, cap_ms: int = RUNTIME_LIMITS.publisher_wall_ms) -> int:
        if type(cap_ms) is not int or cap_ms < 1:
            raise ValueError("catalog deadline cap_ms must be positive")
        if self.cancelled():
            raise PropertyCatalogPublishError("catalog operation was cancelled")
        remaining = int((self._deadline - self.clock()) * 1_000)
        if remaining < 1:
            raise PropertyCatalogPublishError("catalog operation deadline exceeded")
        return min(remaining, cap_ms)


@dataclass(slots=True)
class ClickHouseEnvelopePublisher:
    """Idempotently commit data rows then their delivery ledger evidence."""

    client: CatalogPublishClient
    database: str
    lease: CatalogWriteLease
    deadline: SharedCatalogDeadline | None = None
    clock: Any = monotonic
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    wall_ms: int = RUNTIME_LIMITS.publisher_wall_ms

    def __post_init__(self) -> None:
        require_catalog_database(self.database)
        if not 1 <= self.wall_ms <= RUNTIME_LIMITS.publisher_wall_ms:
            raise PropertyCatalogPublishError(
                f"publisher wall must be in (0, {RUNTIME_LIMITS.publisher_wall_ms}] ms"
            )
        self._validate_target()
        if self.now() >= self.lease.expires_at:
            raise PropertyCatalogPublishError("catalog write lease is expired")

    def publish(
        self,
        envelope: PropertyCatalogEnvelope,
        *,
        value_rows: Sequence[Mapping[str, Any]] = (),
    ) -> str:
        started = self.clock()
        self._validate_target()
        self._validate_envelope_lease(envelope)
        wire = encode_envelope(envelope, value_rows=value_rows)
        self._validate_role_payload(envelope=envelope, document=wire.document)
        self._validate_target()
        existing = self.client.query(
            "SELECT any(delivery.envelope_id) AS envelope_id, "
            "any(delivery.payload_sha256) AS payload_sha256, "
            "uniqExact(tuple(delivery.projection_version, delivery.envelope_format, "
            "delivery.envelope_version, delivery.envelope_id, delivery.payload_sha256, "
            "delivery.previous_payload_sha256, delivery.source_batch_digest, "
            "delivery.outcome, delivery.terminal, delivery.gap_reasons, "
            "delivery.source_rows, delivery.definition_rows, delivery.value_rows, "
            "delivery.tombstone_rows)) AS identity_variants "
            f"FROM `{self.database}`.`property_catalog_deliveries` AS delivery "
            "WHERE delivery.organization_id = %(organization_id)s "
            "AND delivery.workspace_id = %(workspace_id)s "
            "AND delivery.catalog_epoch = %(catalog_epoch)s "
            "AND delivery.catalog_revision = %(catalog_revision)s "
            "AND delivery.build_token = %(build_token)s "
            "AND delivery.source_adapter = %(source_adapter)s "
            "AND delivery.producer_stream_id = %(producer_stream_id)s "
            "AND delivery.sequence = %(sequence)s",
            {
                "organization_id": envelope.organization_id,
                "workspace_id": envelope.workspace_id,
                "catalog_epoch": envelope.catalog_epoch,
                "catalog_revision": envelope.catalog_revision,
                "build_token": envelope.build_token,
                "source_adapter": str(envelope.source_adapter),
                "producer_stream_id": envelope.producer_stream_id,
                "sequence": envelope.sequence,
            },
            timeout_ms=self._remaining(started),
        )
        if self._is_exact_delivery_replay(
            existing,
            envelope_id=wire.envelope_id,
            payload_sha256=wire.payload_sha256,
        ):
            return wire.payload_sha256
        self._assert_stream_writable(envelope=envelope, started=started)
        payload = wire.document["payload"]
        for chunk in payload["chunks"]:
            table = str(chunk["table"])
            if table not in _ENVELOPE_WRITE_TABLES:
                raise PropertyCatalogPublishError(
                    "wire chunk targets a forbidden table"
                )
            # The exact JSONEachRow bytes were validated/hashed by the wire codec.
            # Decode only after that check to feed the normal typed client insert API.
            rows = [
                json.loads(line)
                for line in base64.b64decode(chunk["json_each_row"]).splitlines()
            ]
            rows = list(_native_insert_rows(table, rows))
            columns = tuple(rows[0]) if rows else ()
            qualified_table = _qualified(self.database, table)
            # Re-check immediately before every irreversible write.  The
            # client identity is deliberately read late so a recycled or
            # reconfigured client fails closed instead of crossing databases.
            self._validate_target()
            self._assert_stream_writable(envelope=envelope, started=started)
            self.client.insert(
                qualified_table,
                rows,
                columns=columns,
                timeout_ms=self._remaining(started),
                deduplication_token=(
                    f"property-catalog-v1:{wire.envelope_id}:chunk:{chunk['index']}"
                ),
            )
        now = datetime.now(UTC)
        delivery = {
            "organization_id": envelope.organization_id,
            "workspace_id": envelope.workspace_id,
            "catalog_epoch": envelope.catalog_epoch,
            "catalog_revision": envelope.catalog_revision,
            "build_token": envelope.build_token,
            "projection_version": envelope.projection_version,
            "source_adapter": str(envelope.source_adapter),
            "producer_stream_id": envelope.producer_stream_id,
            "sequence": envelope.sequence,
            "envelope_format": wire.document["format"],
            "envelope_version": wire.document["version"],
            "envelope_id": wire.envelope_id,
            "payload_sha256": wire.payload_sha256,
            "previous_payload_sha256": envelope.previous_payload_sha256,
            "source_batch_digest": envelope.source_batch_digest,
            "outcome": str(envelope.outcome),
            "terminal": int(envelope.terminal),
            "gap_reasons": list(envelope.gap_reasons),
            "source_rows": envelope.counts.source_count,
            "definition_rows": envelope.counts.definition_count,
            "value_rows": envelope.counts.value_count,
            "tombstone_rows": envelope.counts.tombstone_count,
            "transport": "reconcile",
            "kafka_partition": -1,
            "kafka_offset": -1,
            "delivered_at": now,
            "_version": envelope.sequence,
        }
        delivery = _native_insert_rows("property_catalog_deliveries", (delivery,))[0]
        self._validate_target()
        self._assert_stream_writable(envelope=envelope, started=started)
        self.client.insert(
            _qualified(self.database, "property_catalog_deliveries"),
            (delivery,),
            columns=_DELIVERY_COLUMNS,
            timeout_ms=self._remaining(started),
            deduplication_token=f"property-catalog-v1:{wire.envelope_id}:delivery",
        )
        self._validate_target()
        committed = self.client.query(
            "SELECT any(delivery.envelope_id) AS envelope_id, "
            "any(delivery.payload_sha256) AS payload_sha256, "
            "uniqExact(tuple(delivery.projection_version, delivery.envelope_format, "
            "delivery.envelope_version, delivery.envelope_id, delivery.payload_sha256, "
            "delivery.previous_payload_sha256, delivery.source_batch_digest, "
            "delivery.outcome, delivery.terminal, delivery.gap_reasons, "
            "delivery.source_rows, delivery.definition_rows, delivery.value_rows, "
            "delivery.tombstone_rows)) AS identity_variants "
            f"FROM `{self.database}`.`property_catalog_deliveries` AS delivery "
            "WHERE delivery.organization_id = %(organization_id)s "
            "AND delivery.workspace_id = %(workspace_id)s "
            "AND delivery.catalog_epoch = %(catalog_epoch)s "
            "AND delivery.catalog_revision = %(catalog_revision)s "
            "AND delivery.build_token = %(build_token)s "
            "AND delivery.source_adapter = %(source_adapter)s "
            "AND delivery.producer_stream_id = %(producer_stream_id)s "
            "AND delivery.sequence = %(sequence)s",
            {
                "organization_id": envelope.organization_id,
                "workspace_id": envelope.workspace_id,
                "catalog_epoch": envelope.catalog_epoch,
                "catalog_revision": envelope.catalog_revision,
                "build_token": envelope.build_token,
                "source_adapter": str(envelope.source_adapter),
                "producer_stream_id": envelope.producer_stream_id,
                "sequence": envelope.sequence,
            },
            timeout_ms=self._remaining(started),
        )
        if not self._is_exact_delivery_replay(
            committed,
            envelope_id=wire.envelope_id,
            payload_sha256=wire.payload_sha256,
        ):
            raise PropertyCatalogPublishError(
                "delivery ledger append is not durably visible"
            )
        return wire.payload_sha256

    @staticmethod
    def _is_exact_delivery_replay(
        rows: Sequence[Mapping[str, Any]],
        *,
        envelope_id: str,
        payload_sha256: str,
    ) -> bool:
        if not rows:
            return False
        if len(rows) != 1:
            raise PropertyCatalogPublishError(
                "delivery identity aggregate returned multiple rows"
            )
        variants = rows[0].get("identity_variants")
        if type(variants) is not int or not 0 <= variants < (1 << 64):
            raise PropertyCatalogPublishError("delivery identity aggregate is invalid")
        if variants == 0:
            return False
        if (
            variants != 1
            or _text(rows[0].get("envelope_id")) != envelope_id
            or _text(rows[0].get("payload_sha256")) != payload_sha256
        ):
            raise PropertyCatalogPublishError(
                "delivery sequence is already committed with other bytes"
            )
        return True

    def _validate_target(self) -> None:
        require_catalog_database(self.database)
        if getattr(self.client, "catalog_database", None) != self.database:
            raise PropertyCatalogPublishError(
                "publisher client database does not match the isolated DEV target"
            )

    def _validate_envelope_lease(self, envelope: PropertyCatalogEnvelope) -> None:
        lease = self.lease
        if self.now() >= lease.expires_at:
            raise PropertyCatalogPublishError("catalog write lease is expired")
        if (
            envelope.organization_id != lease.organization_id
            or envelope.workspace_id != lease.workspace_id
            or envelope.catalog_epoch != lease.catalog_epoch
            or envelope.catalog_revision != lease.catalog_revision
            or envelope.build_token != lease.build_token
            or envelope.projection_version != lease.projection_version
            or envelope.source_adapter is not lease.source_adapter
            or envelope.producer_stream_id != lease.producer_stream_id
        ):
            raise PropertyCatalogPublishError(
                "envelope scope does not match the exact catalog write lease"
            )

    def _validate_role_payload(
        self,
        *,
        envelope: PropertyCatalogEnvelope,
        document: Mapping[str, Any],
    ) -> None:
        plan = RevisionBuildPlan.from_json(self.lease.build_plan_json)
        planned = tuple(
            stream
            for stream in plan.streams
            if stream.key == (envelope.source_adapter, envelope.producer_stream_id)
        )
        if len(planned) != 1:
            raise PropertyCatalogPublishError(
                "envelope stream has no unique build-plan role"
            )
        payload = document["payload"]
        chunk_tables = {str(chunk["table"]) for chunk in payload["chunks"]}
        role = planned[0].role
        if envelope.terminal:
            if chunk_tables or any(
                (
                    envelope.counts.source_count,
                    envelope.counts.definition_count,
                    envelope.counts.value_count,
                    envelope.counts.tombstone_count,
                )
            ):
                raise PropertyCatalogPublishError(
                    "terminal envelope must be an explicit empty delivery"
                )
            return
        if role is ManifestStreamRole.DEFINITIONS:
            valid = envelope.counts.value_count == 0 and chunk_tables <= {
                "property_definition_catalog"
            }
        elif role in {ManifestStreamRole.VALUES, ManifestStreamRole.HOT_VALUES}:
            valid = (
                envelope.counts.definition_count == 0
                and envelope.counts.tombstone_count == 0
                and chunk_tables <= {"span_attribute_value_catalog"}
            )
        else:
            valid = (
                role is ManifestStreamRole.SOURCE_AUDIT
                and envelope.counts.definition_count == 0
                and envelope.counts.value_count == 0
                and envelope.counts.tombstone_count == 0
                and not chunk_tables
            )
        if not valid:
            raise PropertyCatalogPublishError(
                "envelope payload violates its immutable build-plan role"
            )

    def _assert_stream_writable(
        self, *, envelope: PropertyCatalogEnvelope, started: float
    ) -> None:
        """Fence late direct writes once drain has started.

        An already-ledgered identical replay returns before this check.  Any
        data chunk whose ledger was never committed must observe the exact,
        unexpired open-stream lease immediately before writing.
        """

        self._validate_target()

        def read_fence(*, reservation: bool) -> tuple[Mapping[str, Any], ...]:
            self._validate_target()
            rows = tuple(
                self.client.query(
                    "SELECT projection_version, envelope_version, build_plan_json, "
                    "build_lease_sha256, status, drain_deadline, fenced_at, _version "
                    f"FROM `{self.database}`."
                    "`property_catalog_source_streams` "
                    "WHERE organization_id=%(organization_id)s "
                    "AND workspace_id=%(workspace_id)s "
                    "AND catalog_epoch=%(catalog_epoch)s "
                    "AND catalog_revision=%(catalog_revision)s "
                    "AND build_token=%(build_token)s "
                    "AND source_adapter=%(source_adapter)s "
                    "AND producer_stream_id=%(producer_stream_id)s "
                    "ORDER BY _version DESC LIMIT %(row_limit)s",
                    {
                        "organization_id": envelope.organization_id,
                        "workspace_id": envelope.workspace_id,
                        "catalog_epoch": envelope.catalog_epoch,
                        "catalog_revision": envelope.catalog_revision,
                        "build_token": envelope.build_token,
                        "source_adapter": str(
                            SourceAdapter.SYSTEM_MANIFEST
                            if reservation
                            else envelope.source_adapter
                        ),
                        "producer_stream_id": (
                            envelope.build_token
                            if reservation
                            else envelope.producer_stream_id
                        ),
                        "row_limit": _MAX_SOURCE_STREAM_STATES + 1,
                    },
                    timeout_ms=self._remaining(started),
                )
            )
            if len(rows) > _MAX_SOURCE_STREAM_STATES:
                label = "reservation" if reservation else "stream"
                raise PropertyCatalogPublishError(
                    f"{label} lease exceeded its conflict-proof row cap"
                )
            return rows

        stream = _unique_latest_fence(read_fence(reservation=False), label="stream")
        reservation = _unique_latest_fence(
            read_fence(reservation=True), label="reservation"
        )
        for label, row, envelope_version in (
            ("stream", stream, 1),
            ("reservation", reservation, 0),
        ):
            drain_deadline = _utc_datetime(
                row.get("drain_deadline"), label=f"{label} drain_deadline"
            )
            if (
                row.get("projection_version") != envelope.projection_version
                or row.get("envelope_version") != envelope_version
                or _text(row.get("build_plan_json")) != self.lease.build_plan_json
                or _text(row.get("build_lease_sha256")) != self.lease.build_lease_sha256
                or str(row.get("status")) != "open"
                or row.get("fenced_at") is not None
                or drain_deadline != self.lease.expires_at
                or self.now() >= drain_deadline
            ):
                raise PropertyCatalogPublishError(
                    f"{label} lease is stale, mismatched, draining, or fenced"
                )

    def _remaining(self, started: float) -> int:
        local_remaining = int(self.wall_ms - (self.clock() - started) * 1000)
        remaining = (
            min(local_remaining, self.deadline.remaining_ms(cap_ms=self.wall_ms))
            if self.deadline is not None
            else local_remaining
        )
        if remaining < 1:
            raise PropertyCatalogPublishError("catalog publish deadline exceeded")
        return remaining


def _qualified(database: str, table: str) -> str:
    require_catalog_database(database)
    if table not in PROPERTY_CATALOG_TABLES:
        raise PropertyCatalogPublishError("forbidden property catalog table")
    return f"`{database}`.`{table}`"


def _native_insert_rows(
    table: str, rows: Sequence[Mapping[str, Any]]
) -> tuple[Mapping[str, Any], ...]:
    """Rehydrate canonical wire timestamps for clickhouse-driver VALUES.

    JSONEachRow timestamps must remain canonical strings while their bytes are
    hashed and transported.  ``clickhouse-driver``'s native DateTime64 encoder,
    however, accepts ``datetime`` (or integer epoch values), not those strings.
    Keep that representation change at the final typed-insert boundary.
    """

    datetime_columns = _NATIVE_DATETIME64_COLUMNS.get(table)
    if datetime_columns is None:
        raise PropertyCatalogPublishError(
            "native insert timestamp contract has no table definition"
        )
    encoded: list[Mapping[str, Any]] = []
    for row in rows:
        missing = set(datetime_columns).difference(row)
        if missing:
            raise PropertyCatalogPublishError(
                "native insert row is missing its DateTime64 columns"
            )
        typed = dict(row)
        for column, nullable in datetime_columns.items():
            value = typed[column]
            if value is None:
                if not nullable:
                    raise PropertyCatalogPublishError(
                        f"{table}.{column} must be a DateTime64 value"
                    )
                continue
            if isinstance(value, str):
                try:
                    value = datetime.strptime(value, _WIRE_DATETIME64_FORMAT).replace(
                        tzinfo=UTC
                    )
                except ValueError as exc:
                    raise PropertyCatalogPublishError(
                        f"{table}.{column} is not canonical DateTime64(6)"
                    ) from exc
                if value.strftime(_WIRE_DATETIME64_FORMAT) != typed[column]:
                    raise PropertyCatalogPublishError(
                        f"{table}.{column} is not canonical DateTime64(6)"
                    )
            elif isinstance(value, datetime):
                if value.tzinfo is None or value.utcoffset() is None:
                    raise PropertyCatalogPublishError(
                        f"{table}.{column} must be timezone-aware"
                    )
                value = value.astimezone(UTC)
            else:
                raise PropertyCatalogPublishError(
                    f"{table}.{column} must be a DateTime64 value"
                )
            typed[column] = value
        encoded.append(typed)
    return tuple(encoded)


def _unique_latest_fence(
    rows: Sequence[Mapping[str, Any]], *, label: str
) -> Mapping[str, Any]:
    if not rows:
        raise PropertyCatalogPublishError(f"{label} has no mandatory lease evidence")
    try:
        maximum = max(int(row["_version"]) for row in rows)
    except (KeyError, TypeError, ValueError) as exc:
        raise PropertyCatalogPublishError(
            f"{label} lease contains an invalid version"
        ) from exc
    latest = tuple(row for row in rows if int(row["_version"]) == maximum)
    identities = {
        (
            row.get("projection_version"),
            row.get("envelope_version"),
            _text(row.get("build_plan_json")),
            _text(row.get("build_lease_sha256")),
            _text(row.get("status")),
            _utc_datetime(row.get("drain_deadline"), label="drain_deadline"),
            row.get("fenced_at"),
        )
        for row in latest
    }
    if len(identities) != 1:
        raise PropertyCatalogPublishError(
            f"{label} lease has conflicting latest states"
        )
    return latest[0]


def _utc_datetime(value: Any, *, label: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PropertyCatalogPublishError(f"{label} is invalid") from exc
    if not isinstance(value, datetime):
        raise PropertyCatalogPublishError(f"{label} is invalid")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def require_dev_catalog_database(database: str) -> str:
    """Return one safe non-production DEV identifier or fail closed."""

    if (
        not isinstance(database, str)
        or len(database.encode("utf-8")) > 128
        or CATALOG_DATABASE_RE.fullmatch(database) is None
        or database == configured_production_property_catalog_database()
        or database in RESERVED_CATALOG_DATABASES
    ):
        raise PropertyCatalogPublishError(
            "development catalog database must be a safe lowercase ClickHouse "
            "identifier isolated from production and source databases"
        )
    return database


def require_prod_catalog_database(database: str) -> str:
    """Return the configured production catalog identifier or fail closed."""

    configured_database = configured_production_property_catalog_database()
    if database != configured_database:
        raise PropertyCatalogPublishError(
            "production catalog database must match the configured production "
            f"database {configured_database!r}"
        )
    return database


def require_catalog_database(database: str) -> str:
    """Return an explicitly isolated DEV or production catalog identifier."""

    if database == configured_production_property_catalog_database():
        return database
    return require_dev_catalog_database(database)


__all__ = [
    "PROPERTY_CATALOG_TABLES",
    "CATALOG_DATABASE_RE",
    "PROD_CATALOG_DATABASE",
    "RESERVED_CATALOG_DATABASES",
    "CatalogPublishClient",
    "CatalogWriteLease",
    "ClickHouseEnvelopePublisher",
    "PropertyCatalogPublishError",
    "SharedCatalogDeadline",
    "require_dev_catalog_database",
    "require_prod_catalog_database",
    "require_catalog_database",
]
