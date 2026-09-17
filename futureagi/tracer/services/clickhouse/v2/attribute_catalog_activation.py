"""Development-only qualification and activation of a frozen catalog epoch.

This controller is intentionally separate from backfill and ingestion.  It can
write only ``span_attribute_catalog_source_streams`` and
``span_attribute_catalog_activations`` after SELECT-only evidence proves one
project/window is a complete backfill-only epoch.  Production and live-writer
epochs are rejected before any insert.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

CATALOG_ACTIVATION_ENVIRONMENT = "development"
CATALOG_ACTIVATION_ACK = "FI_PROPERTY_CATALOG_DEV_ACTIVATE_FROZEN_EPOCH"
CATALOG_ACTIVATION_SUPERSESSION_ACK = "FI_PROPERTY_CATALOG_DEV_SUPERSEDE_FROZEN_V1_WITH_V2"
CATALOG_ACTIVATION_MAX_HOURS = 366 * 24
CATALOG_ACTIVATION_MAX_RESULT_ROWS = CATALOG_ACTIVATION_MAX_HOURS + 1
CATALOG_ACTIVATION_QUERY_TIMEOUT_MS = 8_000
CATALOG_PROJECTION_VERSION = 2

SOURCE_STREAM_TABLE = "span_attribute_catalog_source_streams"
ACTIVATION_TABLE = "span_attribute_catalog_activations"
CHECKPOINT_TABLE = "span_attribute_catalog_checkpoints"
DELIVERY_TABLE = "span_attribute_catalog_deliveries"
KEY_TABLE = "span_attribute_key_catalog"
VALUE_TABLE = "span_attribute_value_catalog"
CATALOG_ACTIVATION_WRITE_TABLES = frozenset((SOURCE_STREAM_TABLE, ACTIVATION_TABLE))

_DATABASE_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
_ACTIVATION_STREAM_NAMESPACE = uuid.UUID("84984e8b-efb0-4dc1-889e-c35a89025de5")

_CHECKPOINT_AUDIT_SQL = """
WITH checkpoint_rows AS
(
    SELECT
        *,
        max(_version) OVER
        (
            PARTITION BY project_id, catalog_epoch, window_start, window_end
        ) AS latest_version
    FROM {checkpoint_table}
    PREWHERE project_id = toUUID(%(catalog_project_id)s)
      AND catalog_epoch = %(catalog_epoch)s
)
SELECT
    window_start,
    window_end,
    tupleElement(state, 1) AS source_version_fence,
    toString(tupleElement(state, 2)) AS status,
    tupleElement(state, 3) AS source_rows,
    tupleElement(state, 4) AS processed_rows,
    tupleElement(state, 5) AS key_rows,
    tupleElement(state, 6) AS value_rows,
    tupleElement(state, 7) AS gap_count,
    tupleElement(state, 8) AS gap_reasons,
    tupleElement(state, 9) AS projection_version,
    state_version,
    latest_state_variants
FROM
(
    SELECT
        window_start,
        window_end,
        argMax(
            tuple(
                source_version_fence,
                status,
                source_rows,
                processed_rows,
                key_rows,
                value_rows,
                gap_count,
                gap_reasons,
                projection_version
            ),
            _version
        ) AS state,
        max(_version) AS state_version,
        uniqExactIf(
            tuple(
                source_version_fence,
                status,
                source_rows,
                processed_rows,
                key_rows,
                value_rows,
                gap_count,
                gap_reasons,
                projection_version
            ),
            _version = latest_version
        ) AS latest_state_variants
    FROM checkpoint_rows
    GROUP BY window_start, window_end
)
ORDER BY window_start ASC, window_end ASC
LIMIT %(catalog_checkpoint_limit)s
"""

_SOURCE_STREAM_AUDIT_SQL = """
WITH stream_rows AS
(
    SELECT
        *,
        max(_version) OVER (PARTITION BY producer_stream_id) AS latest_version
    FROM {source_stream_table}
    PREWHERE project_id = toUUID(%(catalog_project_id)s)
      AND catalog_epoch = %(catalog_epoch)s
)
SELECT
    toString(producer_stream_id) AS producer_stream_id,
    tupleElement(state, 1) AS envelope_version,
    tupleElement(state, 2) AS first_sequence,
    tupleElement(state, 3) AS last_sequence,
    tupleElement(state, 4) AS frozen_sequence,
    toString(tupleElement(state, 5)) AS terminal_payload_sha256,
    toString(tupleElement(state, 6)) AS source_fence_digest,
    toString(tupleElement(state, 7)) AS status,
    tupleElement(state, 8) AS gap_count,
    tupleElement(state, 9) AS gap_reasons,
    latest_state_variants
FROM
(
    SELECT
        producer_stream_id,
        argMax(
            tuple(
                envelope_version,
                first_sequence,
                last_sequence,
                frozen_sequence,
                terminal_payload_sha256,
                source_fence_digest,
                status,
                gap_count,
                gap_reasons
            ),
            _version
        ) AS state,
        uniqExactIf(
            tuple(
                envelope_version,
                first_sequence,
                last_sequence,
                frozen_sequence,
                terminal_payload_sha256,
                source_fence_digest,
                status,
                gap_count,
                gap_reasons
            ),
            _version = latest_version
        ) AS latest_state_variants
    FROM stream_rows
    GROUP BY producer_stream_id
)
ORDER BY producer_stream_id ASC
LIMIT %(catalog_stream_limit)s
"""

_DELIVERY_AUDIT_SQL = """
WITH delivery_rows AS
(
    SELECT
        *,
        max(_version) OVER
        (
            PARTITION BY producer_stream_id, sequence
        ) AS latest_version
    FROM {delivery_table}
    PREWHERE project_id = toUUID(%(catalog_project_id)s)
      AND catalog_epoch = %(catalog_epoch)s
), latest_deliveries AS
(
    SELECT
        producer_stream_id,
        sequence,
        argMax(tuple(outcome, gap_reasons), _version) AS state,
        uniqExactIf(
            tuple(outcome, gap_reasons, payload_sha256, previous_payload_sha256),
            _version = latest_version
        ) AS latest_state_variants
    FROM delivery_rows
    GROUP BY producer_stream_id, sequence
)
SELECT
    count() AS delivery_count,
    countIf(toString(tupleElement(state, 1)) != 'committed') AS gap_count,
    countIf(notEmpty(tupleElement(state, 2))) AS gap_reason_count,
    countIf(latest_state_variants != 1) AS version_conflict_count
FROM latest_deliveries
"""

_CATALOG_BOUNDS_AUDIT_SQL = """
SELECT
    count() AS row_count,
    countIf(
        first_seen < %(catalog_window_start)s
        OR first_seen >= %(catalog_window_end)s
        OR last_seen < %(catalog_window_start)s
        OR last_seen >= %(catalog_window_end)s
        OR first_seen > last_seen
    ) AS out_of_window_count
FROM {catalog_table}
PREWHERE project_id = toUUID(%(catalog_project_id)s)
  AND catalog_epoch = %(catalog_epoch)s
"""

_ACTIVATION_AUDIT_SQL = """
WITH activation_rows AS
(
    SELECT
        *,
        max(_version) OVER (PARTITION BY project_id) AS latest_version
    FROM {activation_table}
    PREWHERE project_id = toUUID(%(catalog_project_id)s)
)
SELECT
    tupleElement(state, 1) AS catalog_epoch,
    tupleElement(state, 2) AS projection_version,
    tupleElement(state, 3) AS handoff_start,
    tupleElement(state, 4) AS handoff_end,
    tupleElement(state, 5) AS writer_watermark,
    toString(tupleElement(state, 6)) AS status,
    state_version,
    latest_state_variants
FROM
(
    SELECT
        argMax(
            tuple(
                catalog_epoch,
                projection_version,
                handoff_start,
                handoff_end,
                writer_watermark,
                status
            ),
            _version
        ) AS state,
        max(_version) AS state_version,
        uniqExactIf(
            tuple(
                catalog_epoch,
                projection_version,
                handoff_start,
                handoff_end,
                writer_watermark,
                status
            ),
            _version = latest_version
        ) AS latest_state_variants
    FROM activation_rows
)
WHERE state_version > 0
LIMIT 2
"""


class CatalogActivationError(RuntimeError):
    pass


class CatalogActivationIO(Protocol):
    def select(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        settings: Mapping[str, Any],
    ) -> list[dict[str, Any]]: ...

    def insert(
        self,
        table: str,
        rows: Sequence[Sequence[Any]],
        columns: Sequence[str],
        *,
        settings: Mapping[str, Any],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class CatalogActivationConfig:
    environment: str
    acknowledgement: str
    project_id: str
    catalog_epoch: int
    since: datetime
    until: datetime
    target_database: str
    dry_run: bool = False
    allow_projection_supersession: bool = False
    supersession_acknowledgement: str = ""

    def validated(self) -> CatalogActivationConfig:
        if self.environment != CATALOG_ACTIVATION_ENVIRONMENT:
            raise CatalogActivationError("catalog activation is development-only")
        if self.acknowledgement != CATALOG_ACTIVATION_ACK:
            raise CatalogActivationError(
                "explicit frozen-epoch activation acknowledgement missing"
            )
        try:
            project_id = str(uuid.UUID(self.project_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise CatalogActivationError(
                "project_id must be one canonical UUID"
            ) from exc
        if project_id != self.project_id:
            raise CatalogActivationError("project_id must use canonical UUID form")
        if type(self.catalog_epoch) is not int or not 1 <= self.catalog_epoch <= 65_535:
            raise CatalogActivationError(
                "catalog_epoch must be in UInt16 range 1..65535"
            )
        since = _utc_hour(self.since, "since")
        until = _utc_hour(self.until, "until")
        if since >= until:
            raise CatalogActivationError("since must be before until")
        hours = int((until - since).total_seconds() // 3_600)
        if hours > CATALOG_ACTIVATION_MAX_HOURS:
            raise CatalogActivationError("activation range exceeds 12 months")
        _database(self.target_database)
        if type(self.allow_projection_supersession) is not bool:
            raise CatalogActivationError(
                "allow_projection_supersession must be an explicit boolean"
            )
        if self.allow_projection_supersession:
            if self.supersession_acknowledgement != CATALOG_ACTIVATION_SUPERSESSION_ACK:
                raise CatalogActivationError(
                    "explicit projection supersession acknowledgement missing"
                )
        elif self.supersession_acknowledgement:
            raise CatalogActivationError(
                "projection supersession acknowledgement requires its explicit flag"
            )
        return self


@dataclass(frozen=True, slots=True)
class CatalogActivationSummary:
    project_id: str
    catalog_epoch: int
    since: datetime
    until: datetime
    checkpoint_count: int
    source_rows: int
    key_rows: int
    value_rows: int
    producer_stream_id: str
    source_fence_digest: str
    dry_run: bool
    already_active: bool
    superseded_epoch: int | None
    rows_written: int


@dataclass(frozen=True, slots=True)
class _ActivationDecision:
    already_active: bool
    prior_state_version: int
    superseded_epoch: int | None


class CatalogFrozenEpochActivator:
    def __init__(
        self,
        io: CatalogActivationIO,
        config: CatalogActivationConfig,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.io = io
        self.config = config.validated()
        self._now = now or (lambda: datetime.now(UTC))
        database = _database(config.target_database)
        self._checkpoint_sql = _CHECKPOINT_AUDIT_SQL.format(
            checkpoint_table=_qualified(database, CHECKPOINT_TABLE)
        )
        self._source_stream_sql = _SOURCE_STREAM_AUDIT_SQL.format(
            source_stream_table=_qualified(database, SOURCE_STREAM_TABLE)
        )
        self._delivery_sql = _DELIVERY_AUDIT_SQL.format(
            delivery_table=_qualified(database, DELIVERY_TABLE)
        )
        self._key_bounds_sql = _CATALOG_BOUNDS_AUDIT_SQL.format(
            catalog_table=_qualified(database, KEY_TABLE)
        )
        self._value_bounds_sql = _CATALOG_BOUNDS_AUDIT_SQL.format(
            catalog_table=_qualified(database, VALUE_TABLE)
        )
        self._activation_sql = _ACTIVATION_AUDIT_SQL.format(
            activation_table=_qualified(database, ACTIVATION_TABLE)
        )

    def run(self) -> CatalogActivationSummary:
        config = self.config
        expected_hours = int((config.until - config.since).total_seconds() // 3_600)
        common = {
            "catalog_project_id": config.project_id,
            "catalog_epoch": config.catalog_epoch,
        }
        checkpoint_rows = self.io.select(
            self._checkpoint_sql,
            {
                **common,
                "catalog_checkpoint_limit": expected_hours + 1,
            },
            settings=_read_settings(expected_hours + 1),
        )
        checkpoints = _validate_checkpoints(
            checkpoint_rows,
            since=config.since,
            until=config.until,
        )

        bounds_params = {
            **common,
            "catalog_window_start": config.since,
            "catalog_window_end": config.until,
        }
        _validate_catalog_bounds(
            self.io.select(
                self._key_bounds_sql,
                bounds_params,
                settings=_read_settings(1),
            ),
            family="key",
        )
        _validate_catalog_bounds(
            self.io.select(
                self._value_bounds_sql,
                bounds_params,
                settings=_read_settings(1),
            ),
            family="value",
        )

        stream_rows = self.io.select(
            self._source_stream_sql,
            {**common, "catalog_stream_limit": 2},
            settings=_read_settings(2),
        )
        delivery_rows = self.io.select(
            self._delivery_sql,
            common,
            settings=_read_settings(1),
        )
        _require_backfill_only(delivery_rows)

        source_fence_digest = _checkpoint_digest(config, checkpoints)
        terminal_payload_sha256 = _terminal_digest(checkpoints[-1])
        producer_stream_id = str(
            uuid.uuid5(
                _ACTIVATION_STREAM_NAMESPACE,
                "\0".join(
                    (
                        config.project_id,
                        str(config.catalog_epoch),
                        config.since.isoformat(timespec="microseconds"),
                        config.until.isoformat(timespec="microseconds"),
                    )
                ),
            )
        )
        expected_stream = {
            "producer_stream_id": producer_stream_id,
            "envelope_version": 1,
            "first_sequence": 1,
            "last_sequence": len(checkpoints),
            "frozen_sequence": len(checkpoints),
            "terminal_payload_sha256": terminal_payload_sha256,
            "source_fence_digest": source_fence_digest,
            "status": "frozen",
            "gap_count": 0,
            "gap_reasons": [],
        }
        stream_exists = _validate_existing_streams(stream_rows, expected_stream)

        activation_rows = self.io.select(
            self._activation_sql,
            common,
            settings=_read_settings(2),
        )
        activation_decision = _validate_existing_activation(
            activation_rows,
            config=config,
        )
        activation_exists = activation_decision.already_active

        totals = {
            name: sum(_strict_uint(row[name], name) for row in checkpoints)
            for name in ("source_rows", "key_rows", "value_rows")
        }
        rows_written = 0
        if not config.dry_run:
            now = _aware_utc(self._now(), "now")
            version = max(1, int(now.timestamp() * 1_000_000))
            if not stream_exists:
                self.io.insert(
                    _qualified(config.target_database, SOURCE_STREAM_TABLE),
                    [
                        (
                            config.project_id,
                            config.catalog_epoch,
                            producer_stream_id,
                            1,
                            1,
                            len(checkpoints),
                            len(checkpoints),
                            terminal_payload_sha256,
                            source_fence_digest,
                            "frozen",
                            0,
                            [],
                            config.since,
                            now,
                            now,
                            version,
                        )
                    ],
                    (
                        "project_id",
                        "catalog_epoch",
                        "producer_stream_id",
                        "envelope_version",
                        "first_sequence",
                        "last_sequence",
                        "frozen_sequence",
                        "terminal_payload_sha256",
                        "source_fence_digest",
                        "status",
                        "gap_count",
                        "gap_reasons",
                        "started_at",
                        "updated_at",
                        "frozen_at",
                        "_version",
                    ),
                    settings=_write_settings("source-stream"),
                )
                rows_written += 1
            if not activation_exists:
                activation_version = max(
                    version + 1,
                    activation_decision.prior_state_version + 1,
                )
                self.io.insert(
                    _qualified(config.target_database, ACTIVATION_TABLE),
                    [
                        (
                            config.project_id,
                            config.catalog_epoch,
                            config.since,
                            config.until,
                            config.until,
                            "active",
                            now,
                            now,
                            activation_version,
                            CATALOG_PROJECTION_VERSION,
                        )
                    ],
                    (
                        "project_id",
                        "catalog_epoch",
                        "handoff_start",
                        "handoff_end",
                        "writer_watermark",
                        "status",
                        "qualified_at",
                        "updated_at",
                        "_version",
                        "projection_version",
                    ),
                    settings=_write_settings("activation"),
                )
                rows_written += 1

        return CatalogActivationSummary(
            project_id=config.project_id,
            catalog_epoch=config.catalog_epoch,
            since=config.since,
            until=config.until,
            checkpoint_count=len(checkpoints),
            source_rows=totals["source_rows"],
            key_rows=totals["key_rows"],
            value_rows=totals["value_rows"],
            producer_stream_id=producer_stream_id,
            source_fence_digest=source_fence_digest,
            dry_run=config.dry_run,
            already_active=stream_exists and activation_exists,
            superseded_epoch=activation_decision.superseded_epoch,
            rows_written=rows_written,
        )


def _validate_checkpoints(rows, *, since, until):
    expected = []
    cursor = since
    while cursor < until:
        expected.append((cursor, cursor + timedelta(hours=1)))
        cursor += timedelta(hours=1)
    if len(rows) != len(expected):
        raise CatalogActivationError("checkpoint coverage is incomplete")
    validated = []
    for index, (row, window) in enumerate(zip(rows, expected, strict=True)):
        if not isinstance(row, dict):
            raise CatalogActivationError("checkpoint row is invalid")
        start = _aware_utc(row.get("window_start"), "checkpoint window_start")
        end = _aware_utc(row.get("window_end"), "checkpoint window_end")
        if (start, end) != window:
            raise CatalogActivationError("checkpoint coverage has a gap or overlap")
        if row.get("status") != "complete":
            raise CatalogActivationError(f"checkpoint {index} is not complete")
        source_rows = _strict_uint(row.get("source_rows"), "source_rows")
        processed_rows = _strict_uint(row.get("processed_rows"), "processed_rows")
        if source_rows != processed_rows:
            raise CatalogActivationError("checkpoint source/processed rows disagree")
        if _strict_uint(row.get("source_version_fence"), "source_version_fence") <= 0:
            raise CatalogActivationError("checkpoint source fence is missing")
        if (
            _strict_uint(
                row.get("projection_version"),
                "projection_version",
            )
            != CATALOG_PROJECTION_VERSION
        ):
            raise CatalogActivationError("checkpoint projection is incompatible")
        if _strict_uint(row.get("state_version"), "state_version") <= 0:
            raise CatalogActivationError("checkpoint state version is missing")
        if _strict_uint(row.get("latest_state_variants"), "latest_state_variants") != 1:
            raise CatalogActivationError("checkpoint latest state conflicts")
        if _strict_uint(row.get("gap_count"), "gap_count") != 0:
            raise CatalogActivationError("checkpoint declares a gap")
        reasons = row.get("gap_reasons")
        if not isinstance(reasons, (tuple, list)) or reasons:
            raise CatalogActivationError("checkpoint gap reasons are invalid")
        _strict_uint(row.get("key_rows"), "key_rows")
        _strict_uint(row.get("value_rows"), "value_rows")
        validated.append(row)
    return validated


def _require_backfill_only(rows) -> None:
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise CatalogActivationError("delivery audit result is invalid")
    row = rows[0]
    for name in (
        "gap_count",
        "gap_reason_count",
        "version_conflict_count",
    ):
        if _strict_uint(row.get(name), name):
            raise CatalogActivationError("delivery ledger is not gap-free")
    if _strict_uint(row.get("delivery_count"), "delivery_count") != 0:
        raise CatalogActivationError(
            "activation accepts only a backfill-only epoch with no live deliveries"
        )


def _validate_catalog_bounds(rows, *, family: str) -> None:
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise CatalogActivationError(f"{family} catalog bounds audit is invalid")
    row = rows[0]
    _strict_uint(row.get("row_count"), f"{family}_row_count")
    if _strict_uint(row.get("out_of_window_count"), f"{family}_out_of_window_count"):
        raise CatalogActivationError(
            f"{family} catalog contains rows outside the activation window"
        )


def _validate_existing_streams(rows, expected: Mapping[str, Any]) -> bool:
    if not isinstance(rows, list) or len(rows) > 1:
        raise CatalogActivationError("source stream state is ambiguous")
    if not rows:
        return False
    row = rows[0]
    if not isinstance(row, dict):
        raise CatalogActivationError("source stream state is invalid")
    if row.get("status") == "open":
        raise CatalogActivationError("catalog epoch still has an open writer stream")
    if _strict_uint(row.get("latest_state_variants"), "latest_state_variants") != 1:
        raise CatalogActivationError("source stream latest state conflicts")
    comparable = {name: row.get(name) for name in expected}
    comparable["gap_reasons"] = list(comparable.get("gap_reasons") or [])
    if comparable != dict(expected):
        raise CatalogActivationError(
            "existing frozen source stream does not match audit"
        )
    return True


def _validate_existing_activation(
    rows,
    *,
    config: CatalogActivationConfig,
) -> _ActivationDecision:
    if not isinstance(rows, list) or len(rows) > 1:
        raise CatalogActivationError("activation state is ambiguous")
    if not rows:
        return _ActivationDecision(False, 0, None)
    row = rows[0]
    if not isinstance(row, dict):
        raise CatalogActivationError("activation state is invalid")
    if _strict_uint(row.get("latest_state_variants"), "latest_state_variants") != 1:
        raise CatalogActivationError("activation latest state conflicts")
    prior_state_version = _strict_uint(row.get("state_version"), "state_version")
    if prior_state_version <= 0:
        raise CatalogActivationError("activation state version is missing")
    expected = (
        config.catalog_epoch,
        CATALOG_PROJECTION_VERSION,
        config.since,
        config.until,
        config.until,
        "active",
    )
    actual = (
        _strict_uint(row.get("catalog_epoch"), "catalog_epoch"),
        _strict_uint(row.get("projection_version"), "projection_version"),
        _aware_utc(row.get("handoff_start"), "handoff_start"),
        _aware_utc(row.get("handoff_end"), "handoff_end"),
        _aware_utc(row.get("writer_watermark"), "writer_watermark"),
        row.get("status"),
    )
    if actual == expected:
        return _ActivationDecision(True, prior_state_version, None)

    prior_epoch, prior_projection, prior_since, prior_until, prior_watermark, status = (
        actual
    )
    if not config.allow_projection_supersession:
        raise CatalogActivationError("project already has a different activation")
    if (
        prior_projection != 1
        or CATALOG_PROJECTION_VERSION != 2
        or config.catalog_epoch <= prior_epoch
        or prior_since != config.since
        or prior_until != config.until
        or prior_watermark != config.until
        or status != "active"
    ):
        raise CatalogActivationError(
            "existing activation is not an exact v1 snapshot eligible for v2 supersession"
        )
    return _ActivationDecision(False, prior_state_version, prior_epoch)


def _checkpoint_digest(config, checkpoints) -> str:
    identity = {
        "project_id": config.project_id,
        "catalog_epoch": config.catalog_epoch,
        "since": config.since.isoformat(timespec="microseconds"),
        "until": config.until.isoformat(timespec="microseconds"),
        "checkpoints": [
            {
                "start": _aware_utc(row["window_start"], "window_start").isoformat(
                    timespec="microseconds"
                ),
                "end": _aware_utc(row["window_end"], "window_end").isoformat(
                    timespec="microseconds"
                ),
                "source_version_fence": _strict_uint(
                    row["source_version_fence"], "source_version_fence"
                ),
                "projection_version": _strict_uint(
                    row.get("projection_version"),
                    "projection_version",
                ),
                "source_rows": _strict_uint(row["source_rows"], "source_rows"),
                "processed_rows": _strict_uint(row["processed_rows"], "processed_rows"),
                "key_rows": _strict_uint(row["key_rows"], "key_rows"),
                "value_rows": _strict_uint(row["value_rows"], "value_rows"),
            }
            for row in checkpoints
        ],
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(b"futureagi.catalog-frozen-epoch.v1\0" + encoded).hexdigest()


def _terminal_digest(row) -> str:
    encoded = json.dumps(
        {
            "window_end": _aware_utc(row["window_end"], "window_end").isoformat(
                timespec="microseconds"
            ),
            "source_version_fence": _strict_uint(
                row["source_version_fence"], "source_version_fence"
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"futureagi.catalog-terminal.v1\0" + encoded).hexdigest()


def _read_settings(max_result_rows: int) -> dict[str, Any]:
    return {
        "readonly": 1,
        "max_execution_time": CATALOG_ACTIVATION_QUERY_TIMEOUT_MS / 1_000,
        "max_threads": 2,
        "max_memory_usage": 512 * 1024 * 1024,
        "max_bytes_to_read": 512 * 1024 * 1024,
        "read_overflow_mode": "throw",
        "max_result_rows": max_result_rows,
        "max_result_bytes": 32 * 1024 * 1024,
        "result_overflow_mode": "throw",
    }


def _write_settings(suffix: Literal["source-stream", "activation"]) -> dict[str, Any]:
    return {
        "async_insert": 0,
        "wait_for_async_insert": 1,
        "insert_quorum": 1,
        "query_id": f"property-catalog-dev-{suffix}",
    }


def _strict_uint(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise CatalogActivationError(f"{label} must be a non-negative integer")
    return value


def _utc_hour(value: datetime, label: str) -> datetime:
    value = _aware_utc(value, label)
    if value.minute or value.second or value.microsecond:
        raise CatalogActivationError(f"{label} must be aligned to a UTC hour")
    return value


def _aware_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise CatalogActivationError(f"{label} must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _database(value: str) -> str:
    if (
        not isinstance(value, str)
        or not _DATABASE_RE.fullmatch(value)
        or "dev" not in value.lower()
        or value.lower() in {"default", "system", "information_schema", "futureagi"}
    ):
        raise CatalogActivationError(
            "target_database must be an isolated development identifier"
        )
    return value


def _qualified(database: str, table: str) -> str:
    database = _database(database)
    if table not in {
        CHECKPOINT_TABLE,
        DELIVERY_TABLE,
        KEY_TABLE,
        VALUE_TABLE,
        SOURCE_STREAM_TABLE,
        ACTIVATION_TABLE,
    }:
        raise CatalogActivationError("catalog table is not allowlisted")
    return f"`{database}`.`{table}`"


__all__ = [
    "ACTIVATION_TABLE",
    "CATALOG_ACTIVATION_ACK",
    "CATALOG_ACTIVATION_ENVIRONMENT",
    "CATALOG_ACTIVATION_WRITE_TABLES",
    "CatalogActivationConfig",
    "CatalogActivationError",
    "CatalogActivationSummary",
    "CatalogFrozenEpochActivator",
    "SOURCE_STREAM_TABLE",
]
