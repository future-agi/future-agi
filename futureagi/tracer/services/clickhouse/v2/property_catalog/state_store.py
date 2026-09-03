"""Strict ClickHouse state and current-binding repositories.

No query in this module uses ``FINAL``. ``ReplacingMergeTree`` is not a
conflict resolver: two different rows at the same ``_version`` are corruption,
not a state that ClickHouse may choose nondeterministically. Reads inspect raw
latest-version rows, accept exact replay duplicates, and fail on disagreement.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from .activation import (
    ActivationManifest,
    ActivationRecord,
    ActivationStatus,
    ActivationStore,
    CatalogLifecycleMode,
    ManifestStreamRole,
    RevisionBuildPlan,
    RevisionLease,
)
from .codec import framed_sha256, require_sha256
from .models import (
    CanonicalDefinition,
    PropertyBindingRow,
    PropertyCategory,
    PropertyKind,
    PropertyRole,
    SourceAdapter,
    VisibilityScope,
)
from .mutation_lock import CatalogMutationSerializer
from .projection import PostgresSnapshotContext, resolve_binding_history
from .proof_limits import (
    MAX_DELIVERIES_PER_REVISION,
    MAX_DELIVERY_REPLAYS,
    MAX_LOGICAL_STATE_VARIANTS,
)
from .publisher import (
    PROPERTY_CATALOG_TABLES,
    SharedCatalogDeadline,
    require_catalog_database,
)
from .qualification import CatalogCheckpoint, CheckpointStatus, RevisionRequirement
from .reconciler import CheckpointWrite, CurrentBindingReader
from .runtime_limits import RUNTIME_LIMITS
from .wire import ZERO_SHA256

_CHECKPOINT_TABLE = "property_catalog_checkpoints"
_ACTIVATION_TABLE = "property_catalog_activations"
_DEFINITION_TABLE = "property_definition_catalog"
_DELIVERY_TABLE = "property_catalog_deliveries"
_SOURCE_STREAM_TABLE = "property_catalog_source_streams"
_CONTROL_WRITE_TABLES = frozenset({_CHECKPOINT_TABLE, _ACTIVATION_TABLE})
_MAX_ACTIVE_LINEAGE_REVISIONS = RUNTIME_LIMITS.max_lineage_revisions
_MAX_STATE_VARIANTS = MAX_LOGICAL_STATE_VARIANTS
_MAX_DELIVERIES = MAX_DELIVERIES_PER_REVISION
_MAX_DELIVERY_REPLAYS = MAX_DELIVERY_REPLAYS

_CHECKPOINT_COLUMNS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "source_adapter",
    "producer_stream_id",
    "status",
    "terminal",
    "source_cursor",
    "watermark",
    "source_version_fence",
    "source_fingerprint",
    "source_rows",
    "processed_rows",
    "definition_rows",
    "value_rows",
    "tombstone_rows",
    "gap_count",
    "poison_count",
    "conflict_count",
    "gap_reasons",
    "first_sequence",
    "last_sequence",
    "last_issued_sequence",
    "fenced_sequence",
    "terminal_payload_sha256",
    "delivery_count",
    "source_digest",
    "emitted_digest",
    "previous_payload_sha256",
    "run_id",
    "worker_id",
    "error",
    "started_at",
    "updated_at",
    "finished_at",
    "_version",
)
_CHECKPOINT_LOGICAL_COLUMNS = tuple(
    column
    for column in _CHECKPOINT_COLUMNS
    if column
    not in {
        "run_id",
        "worker_id",
        "started_at",
        "updated_at",
        "finished_at",
        "_version",
    }
)
_ACTIVATION_COLUMNS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "lifecycle_mode",
    "lineage_anchor_revision",
    "activation_sequence",
    "source_manifest_json",
    "source_manifest_sha256",
    "revision_fence_sha256",
    "activation_sha256",
    "status",
    "live_definition_rows",
    "tombstone_rows",
    "value_rows",
    "qualified_at",
    "updated_at",
    "_version",
)
_BINDING_COLUMNS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "binding_id",
    "visibility_scope",
    "visibility_id",
    "source_adapter",
    "source_entity_id",
    "source_version",
    "source_fingerprint",
    "producer_stream_id",
    "producer_sequence",
    "property_id",
    "property_kind",
    "category",
    "category_rank",
    "source_rank",
    "definition_source",
    "primary_source",
    "primary_source_folded",
    "source_tokens",
    "value_adapter",
    "name",
    "display_name",
    "sort_name_folded",
    "search_text_folded",
    "role",
    "definition_json",
    "definition_sha256",
    "first_seen",
    "last_seen",
    "is_deleted",
    "deleted_at",
    "state_sha256",
    "emitted_at",
)
_DELIVERY_AUDIT_COLUMNS = (
    "projection_version",
    "sequence",
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
    "_version",
)
_HOT_DELIVERY_COLUMNS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "source_adapter",
    "producer_stream_id",
    "sequence",
    "terminal",
    "envelope_format",
    "envelope_version",
    "envelope_id",
    "payload_sha256",
    "previous_payload_sha256",
    "source_batch_digest",
    "outcome",
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
_SOURCE_STREAM_AUDIT_COLUMNS = (
    "projection_version",
    "source_adapter",
    "producer_stream_id",
    "envelope_version",
    "first_sequence",
    "last_sequence",
    "max_contiguous_sequence",
    "last_issued_sequence",
    "fenced_sequence",
    "terminal_payload_sha256",
    "build_plan_json",
    "build_lease_sha256",
    "status",
    "gap_count",
    "fenced_at",
    "_version",
)
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class PropertyCatalogStateError(RuntimeError):
    """The store cannot prove one deterministic latest state."""


class PropertyCatalogStateConflict(PropertyCatalogStateError):
    """Different rows claim the same ReplacingMergeTree version."""


class CatalogStateClient(Protocol):
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


class ClickHouseCatalogStateStore(ActivationStore):
    """Append-only checkpoint/activation store under one shrinking deadline."""

    def __init__(
        self,
        client: CatalogStateClient,
        *,
        database: str,
        serializer: CatalogMutationSerializer,
        deadline: SharedCatalogDeadline | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        _validate_client(client, database)
        if not callable(getattr(serializer, "serialize", None)):
            raise TypeError("state store requires a mutation serializer")
        if timeout_ms is None:
            timeout_ms = RUNTIME_LIMITS.state_store_timeout_ms
        if (
            type(timeout_ms) is not int
            or not 1 <= timeout_ms <= RUNTIME_LIMITS.state_store_timeout_ms
        ):
            raise ValueError(
                "state-store timeout_ms must be in [1, "
                f"{RUNTIME_LIMITS.state_store_timeout_ms}]"
            )
        self._client = client
        self._database = database
        self._serializer = serializer
        self._deadline = deadline
        self._timeout_ms = timeout_ms

    def append(self, value: CheckpointWrite) -> None:
        checkpoint = value.checkpoint
        key = (
            f"checkpoint:{self._database}:{checkpoint.organization_id}:"
            f"{checkpoint.workspace_id}:{checkpoint.catalog_epoch}:"
            f"{checkpoint.catalog_revision}:{checkpoint.build_token}:"
            f"{checkpoint.source_adapter}:{checkpoint.producer_stream_id}"
        )
        self._serializer.serialize(key, lambda: self._append_checkpoint(value))

    def audit_build_plan(
        self,
        *,
        build_plan: RevisionBuildPlan,
        manifest: ActivationManifest,
    ) -> None:
        """Re-prove the exact physical reservation and stream inventory."""

        if not build_plan.matches_manifest(manifest):
            raise PropertyCatalogStateConflict(
                "final manifest does not match the immutable build plan"
            )
        row_cap = (len(build_plan.streams) + 1) * _MAX_STATE_VARIANTS
        rows = self._query(
            f"SELECT {', '.join(_SOURCE_STREAM_AUDIT_COLUMNS)} FROM ("
            f"SELECT {', '.join(_SOURCE_STREAM_AUDIT_COLUMNS)}, "
            "max(_version) OVER (PARTITION BY source_adapter, producer_stream_id) "
            "AS latest_version "
            f"FROM {_qualified(self._database, _SOURCE_STREAM_TABLE)} "
            "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
            "AND catalog_epoch=%(catalog_epoch)s AND catalog_revision=%(catalog_revision)s "
            "AND build_token=%(build_token)s) WHERE _version=latest_version "
            "ORDER BY source_adapter, producer_stream_id, _version DESC "
            "LIMIT %(row_limit)s",
            {
                "organization_id": build_plan.organization_id,
                "workspace_id": build_plan.workspace_id,
                "catalog_epoch": build_plan.catalog_epoch,
                "catalog_revision": build_plan.catalog_revision,
                "build_token": build_plan.build_token,
                "row_limit": row_cap + 1,
            },
        )
        _require_below_row_cap(rows, cap=row_cap, label="source-stream inventory")
        grouped: dict[tuple[SourceAdapter, str], list[Mapping[str, Any]]] = defaultdict(
            list
        )
        for row in rows:
            grouped[
                (
                    SourceAdapter(_text(row.get("source_adapter"))),
                    _text(row.get("producer_stream_id")),
                )
            ].append(row)
        logical = tuple(
            column for column in _SOURCE_STREAM_AUDIT_COLUMNS if column != "_version"
        )
        latest = {
            key: _latest_row(
                values,
                logical_columns=logical,
                label=f"source-stream:{key[0]}:{key[1]}",
            )
            for key, values in grouped.items()
        }
        reservation_key = (SourceAdapter.SYSTEM_MANIFEST, build_plan.build_token)
        expected_keys = {stream.key for stream in build_plan.streams} | {
            reservation_key
        }
        if set(latest) != expected_keys:
            raise PropertyCatalogStateConflict(
                "physical source-stream inventory does not match build plan"
            )
        requirements = {
            stream.requirement.key: stream.requirement for stream in manifest.streams
        }
        for key, row in latest.items():
            assert row is not None
            reservation = key == reservation_key
            if (
                _uint(row.get("projection_version"), "projection_version")
                != build_plan.projection_version
                or _text(row.get("build_plan_json")) != build_plan.canonical_json
                or _text(row.get("build_lease_sha256")) != build_plan.sha256
                or _uint(row.get("envelope_version"), "envelope_version")
                != (0 if reservation else 1)
                or _text(row.get("status")) != ("fenced" if reservation else "complete")
                or row.get("fenced_at") is None
            ):
                raise PropertyCatalogStateConflict(
                    "physical source-stream state conflicts with build plan"
                )
            if reservation:
                continue
            requirement = requirements[key]
            last = _uint(row.get("last_sequence"), "last_sequence")
            if (
                _uint(row.get("first_sequence"), "first_sequence") != 1
                or last != requirement.expected_last_sequence
                or _uint(row.get("max_contiguous_sequence"), "max_contiguous_sequence")
                != last
                or _uint(row.get("last_issued_sequence"), "last_issued_sequence")
                != last
                or _uint(row.get("fenced_sequence"), "fenced_sequence") != last
                or _text(row.get("terminal_payload_sha256"))
                != requirement.expected_terminal_payload_sha256
                or _uint(row.get("gap_count"), "gap_count") != 0
            ):
                raise PropertyCatalogStateConflict(
                    "physical source-stream terminal proof is invalid"
                )

    def _append_checkpoint(self, value: CheckpointWrite) -> None:
        checkpoint = value.checkpoint
        existing = self._load_latest_checkpoint_rows(
            organization_id=checkpoint.organization_id,
            workspace_id=checkpoint.workspace_id,
            catalog_epoch=checkpoint.catalog_epoch,
            catalog_revision=checkpoint.catalog_revision,
            build_token=checkpoint.build_token,
            source_adapter=checkpoint.source_adapter,
            producer_stream_id=checkpoint.producer_stream_id,
            label="checkpoint",
        )
        latest = _latest_row(
            existing,
            logical_columns=_CHECKPOINT_LOGICAL_COLUMNS,
            label="checkpoint",
        )
        next_version = (
            1 if latest is None else _uint(latest["_version"], "_version") + 1
        )
        row = _checkpoint_row(value, now=datetime.now(UTC), version=next_version)
        if latest is not None and _row_identity(
            latest, _CHECKPOINT_LOGICAL_COLUMNS
        ) == _row_identity(row, _CHECKPOINT_LOGICAL_COLUMNS):
            return
        if next_version >= 1 << 64:
            raise PropertyCatalogStateError("checkpoint version exhausted UInt64")
        self._insert(
            _CHECKPOINT_TABLE,
            row,
            deduplication_token=(
                "property-catalog-checkpoint-v1:"
                f"{checkpoint.build_token}:{checkpoint.source_adapter}:"
                f"{checkpoint.producer_stream_id}:{next_version}:"
                f"{checkpoint.state_sha256}"
            ),
        )
        persisted = self._load_latest_checkpoint_rows(
            organization_id=checkpoint.organization_id,
            workspace_id=checkpoint.workspace_id,
            catalog_epoch=checkpoint.catalog_epoch,
            catalog_revision=checkpoint.catalog_revision,
            build_token=checkpoint.build_token,
            source_adapter=checkpoint.source_adapter,
            producer_stream_id=checkpoint.producer_stream_id,
            label="checkpoint-post-write",
        )
        verified = _latest_row(
            persisted,
            logical_columns=_CHECKPOINT_LOGICAL_COLUMNS,
            label="checkpoint-post-write",
        )
        if (
            verified is None
            or _uint(verified["_version"], "_version") != next_version
            or _row_identity(verified, _CHECKPOINT_LOGICAL_COLUMNS)
            != _row_identity(row, _CHECKPOINT_LOGICAL_COLUMNS)
        ):
            raise PropertyCatalogStateConflict(
                "checkpoint append was not preserved as the unique latest state"
            )

    def _load_latest_checkpoint_rows(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
        catalog_revision: int,
        build_token: str,
        source_adapter: SourceAdapter,
        producer_stream_id: str,
        label: str,
    ) -> Sequence[Mapping[str, Any]]:
        rows = self._query(
            f"SELECT {', '.join(_CHECKPOINT_COLUMNS)} FROM ("
            f"SELECT {', '.join(_CHECKPOINT_COLUMNS)}, "
            "max(_version) OVER () AS latest_version "
            f"FROM {_qualified(self._database, _CHECKPOINT_TABLE)} "
            "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
            "AND catalog_epoch=%(catalog_epoch)s AND catalog_revision=%(catalog_revision)s "
            "AND build_token=%(build_token)s AND source_adapter=%(source_adapter)s "
            "AND producer_stream_id=%(producer_stream_id)s) "
            "WHERE _version=latest_version ORDER BY _version DESC LIMIT %(row_limit)s",
            {
                "organization_id": organization_id,
                "workspace_id": workspace_id,
                "catalog_epoch": catalog_epoch,
                "catalog_revision": catalog_revision,
                "build_token": build_token,
                "source_adapter": str(source_adapter),
                "producer_stream_id": producer_stream_id,
                "row_limit": _MAX_STATE_VARIANTS + 1,
            },
        )
        _require_below_row_cap(rows, cap=_MAX_STATE_VARIANTS, label=label)
        return rows

    def load_checkpoints(
        self, requirement: RevisionRequirement
    ) -> Sequence[CatalogCheckpoint]:
        row_cap = max(
            RUNTIME_LIMITS.state_store_min_row_cap,
            len(requirement.streams) * _MAX_STATE_VARIANTS,
        )
        rows = self._query(
            f"SELECT {', '.join(_CHECKPOINT_COLUMNS)} FROM ("
            f"SELECT {', '.join(_CHECKPOINT_COLUMNS)}, "
            "max(_version) OVER (PARTITION BY source_adapter, producer_stream_id) "
            "AS latest_version "
            f"FROM {_qualified(self._database, _CHECKPOINT_TABLE)} "
            "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
            "AND catalog_epoch=%(catalog_epoch)s AND catalog_revision=%(catalog_revision)s "
            "AND build_token=%(build_token)s AND projection_version=%(projection_version)s) "
            "WHERE _version=latest_version "
            "ORDER BY source_adapter, producer_stream_id, _version DESC LIMIT %(row_limit)s",
            {
                "organization_id": requirement.organization_id,
                "workspace_id": requirement.workspace_id,
                "catalog_epoch": requirement.catalog_epoch,
                "catalog_revision": requirement.catalog_revision,
                "build_token": requirement.build_token,
                "projection_version": requirement.projection_version,
                "row_limit": row_cap + 1,
            },
        )
        _require_below_row_cap(rows, cap=row_cap, label="checkpoint inventory")
        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[
                (_text(row["source_adapter"]), _text(row["producer_stream_id"]))
            ].append(row)
        checkpoints: list[CatalogCheckpoint] = []
        for key in sorted(grouped):
            latest = _latest_row(
                grouped[key],
                logical_columns=_CHECKPOINT_LOGICAL_COLUMNS,
                label=f"checkpoint:{key[0]}:{key[1]}",
            )
            assert latest is not None
            checkpoint = _checkpoint(latest)
            self._audit_delivery_chain(checkpoint)
            checkpoints.append(checkpoint)
        return tuple(checkpoints)

    def _audit_delivery_chain(self, checkpoint: CatalogCheckpoint) -> None:
        """Re-derive terminal/contiguity/count evidence from physical ledger rows."""

        if checkpoint.delivery_count > _MAX_DELIVERIES:
            raise PropertyCatalogStateConflict(
                "physical delivery count exceeds the bounded ledger audit"
            )
        row_cap = max(
            _MAX_STATE_VARIANTS,
            checkpoint.delivery_count * _MAX_DELIVERY_REPLAYS + _MAX_DELIVERY_REPLAYS,
        )
        rows = self._query(
            f"SELECT {', '.join(_DELIVERY_AUDIT_COLUMNS)} "
            f"FROM {_qualified(self._database, _DELIVERY_TABLE)} "
            "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
            "AND catalog_epoch=%(catalog_epoch)s AND catalog_revision=%(catalog_revision)s "
            "AND build_token=%(build_token)s AND source_adapter=%(source_adapter)s "
            "AND producer_stream_id=%(producer_stream_id)s "
            "ORDER BY sequence, _version DESC LIMIT %(row_limit)s",
            {
                "organization_id": checkpoint.organization_id,
                "workspace_id": checkpoint.workspace_id,
                "catalog_epoch": checkpoint.catalog_epoch,
                "catalog_revision": checkpoint.catalog_revision,
                "build_token": checkpoint.build_token,
                "source_adapter": str(checkpoint.source_adapter),
                "producer_stream_id": checkpoint.producer_stream_id,
                "row_limit": row_cap + 1,
            },
        )
        _require_below_row_cap(rows, cap=row_cap, label="physical delivery audit")
        grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[_uint(row.get("sequence"), "sequence")].append(row)
        sequences = tuple(sorted(grouped))
        expected_sequences = (
            tuple(range(1, (checkpoint.last_sequence or 0) + 1))
            if checkpoint.last_sequence is not None
            else ()
        )
        expected_first = 1 if sequences else None
        if (
            sequences != expected_sequences
            or checkpoint.first_sequence != expected_first
        ):
            raise PropertyCatalogStateConflict(
                "physical delivery ledger is not contiguous from sequence one"
            )
        logical = tuple(
            column for column in _DELIVERY_AUDIT_COLUMNS if column != "_version"
        )
        deliveries: list[Mapping[str, Any]] = []
        for sequence in sequences:
            identities = {_row_identity(row, logical) for row in grouped[sequence]}
            if len(identities) != 1:
                raise PropertyCatalogStateConflict(
                    "physical delivery sequence has conflicting immutable identities"
                )
            deliveries.append(grouped[sequence][0])
        previous = ZERO_SHA256
        emitted_digest = _EMPTY_SHA256
        counts = [0, 0, 0, 0]
        gaps: set[str] = set()
        for index, row in enumerate(deliveries):
            payload = _text(row.get("payload_sha256"))
            envelope_id = _text(row.get("envelope_id"))
            source_batch = _text(row.get("source_batch_digest"))
            for field, value in (
                ("payload_sha256", payload),
                ("envelope_id", envelope_id),
                ("source_batch_digest", source_batch),
            ):
                try:
                    require_sha256(value, field=field)
                except (TypeError, ValueError) as exc:
                    raise PropertyCatalogStateConflict(
                        f"physical delivery {field} is invalid"
                    ) from exc
            if _text(row.get("previous_payload_sha256")) != previous:
                raise PropertyCatalogStateConflict(
                    "physical delivery payload chain is broken"
                )
            previous = payload
            emitted_digest = framed_sha256(
                "futureagi.property-catalog.emitted-stream.v1",
                emitted_digest,
                payload,
            )
            if (
                _uint(row.get("projection_version"), "projection_version")
                != checkpoint.projection_version
            ):
                raise PropertyCatalogStateConflict(
                    "physical delivery projection does not match checkpoint"
                )
            terminal = _bool(row.get("terminal"), "terminal")
            if terminal != (checkpoint.terminal and index == len(deliveries) - 1):
                raise PropertyCatalogStateConflict(
                    "physical delivery terminal is missing, early, or duplicated"
                )
            reasons = tuple(_text(reason) for reason in row.get("gap_reasons", ()))
            if reasons != tuple(sorted(set(reasons))):
                raise PropertyCatalogStateConflict(
                    "physical delivery gap reasons are not canonical"
                )
            outcome = _text(row.get("outcome"))
            if (outcome == "committed") != (not reasons):
                raise PropertyCatalogStateConflict(
                    "physical delivery outcome disagrees with gap reasons"
                )
            gaps.update(reasons)
            for count_index, field in enumerate(
                ("source_rows", "definition_rows", "value_rows", "tombstone_rows")
            ):
                counts[count_index] += _uint(row.get(field), field)
        if (
            tuple(counts)
            != (
                checkpoint.source_count,
                checkpoint.definition_count,
                checkpoint.value_count,
                checkpoint.tombstone_count,
            )
            or len(deliveries) != checkpoint.delivery_count
            or emitted_digest != checkpoint.emitted_digest
            or (
                deliveries
                and previous != checkpoint.terminal_payload_sha256
                and checkpoint.terminal
            )
            or len(gaps) != checkpoint.gap_count
        ):
            raise PropertyCatalogStateConflict(
                "physical delivery evidence disagrees with checkpoint totals"
            )

    def load_checkpoint_write(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
        catalog_revision: int,
        build_token: str,
        source_adapter: SourceAdapter,
        producer_stream_id: str,
    ) -> CheckpointWrite | None:
        rows = self._load_latest_checkpoint_rows(
            organization_id=organization_id,
            workspace_id=workspace_id,
            catalog_epoch=catalog_epoch,
            catalog_revision=catalog_revision,
            build_token=build_token,
            source_adapter=source_adapter,
            producer_stream_id=producer_stream_id,
            label="checkpoint-load",
        )
        latest = _latest_row(
            rows, logical_columns=_CHECKPOINT_LOGICAL_COLUMNS, label="checkpoint"
        )
        return None if latest is None else _checkpoint_write(latest)

    def append_hot_checkpoint_from_proof(
        self,
        *,
        lease: RevisionLease,
        assignment: Any,
        proof: Any,
    ) -> CatalogCheckpoint:
        """Re-derive and append HOT_VALUES only from the raw immutable ledger."""

        hot_streams = tuple(
            stream
            for stream in lease.build_plan.streams
            if stream.role is ManifestStreamRole.HOT_VALUES
        )
        if len(hot_streams) != 1:
            raise PropertyCatalogStateConflict("build plan has no unique hot stream")
        hot = hot_streams[0]
        if not callable(getattr(proof, "to_checkpoint", None)):
            raise TypeError("proof must implement the v2 producer drain contract")
        key = (
            f"revision:{self._database}:{lease.organization_id}:"
            f"{lease.workspace_id}:{lease.catalog_epoch}"
        )

        def append_serialized() -> CatalogCheckpoint:
            terminal_sequence = _uint(
                getattr(proof, "terminal_sequence", None), "terminal_sequence"
            )
            if not 1 <= terminal_sequence <= _MAX_DELIVERIES:
                raise PropertyCatalogStateConflict(
                    "hot terminal sequence exceeds the physical audit bound"
                )
            stream_rows = self._query(
                f"SELECT {', '.join(_SOURCE_STREAM_AUDIT_COLUMNS)} FROM ("
                f"SELECT {', '.join(_SOURCE_STREAM_AUDIT_COLUMNS)}, "
                "max(_version) OVER () AS latest_version "
                f"FROM {_qualified(self._database, _SOURCE_STREAM_TABLE)} "
                "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
                "AND catalog_epoch=%(catalog_epoch)s AND catalog_revision=%(catalog_revision)s "
                "AND build_token=%(build_token)s AND source_adapter='span_attribute' "
                "AND producer_stream_id=%(producer_stream_id)s) "
                "WHERE _version=latest_version ORDER BY _version DESC "
                "LIMIT %(row_limit)s",
                {
                    "organization_id": lease.organization_id,
                    "workspace_id": lease.workspace_id,
                    "catalog_epoch": lease.catalog_epoch,
                    "catalog_revision": lease.catalog_revision,
                    "build_token": lease.build_token,
                    "producer_stream_id": hot.producer_stream_id,
                    "row_limit": _MAX_STATE_VARIANTS + 1,
                },
            )
            _require_below_row_cap(
                stream_rows,
                cap=_MAX_STATE_VARIANTS,
                label="hot source-stream state",
            )
            stream = _latest_row(
                stream_rows,
                logical_columns=tuple(
                    value
                    for value in _SOURCE_STREAM_AUDIT_COLUMNS
                    if value != "_version"
                ),
                label="hot-source-stream",
            )
            if (
                stream is None
                or _text(stream.get("status")) != "draining"
                or _text(stream.get("build_plan_json")) != lease.build_plan_json
                or _text(stream.get("build_lease_sha256")) != lease.build_lease_sha256
                or _uint(stream.get("last_issued_sequence"), "last_issued_sequence")
                != terminal_sequence
                or _uint(stream.get("fenced_sequence"), "fenced_sequence")
                != terminal_sequence
            ):
                raise PropertyCatalogStateConflict(
                    "hot source stream is not bound to the ready producer proof"
                )
            delivery_row_cap = max(
                _MAX_STATE_VARIANTS,
                terminal_sequence * _MAX_DELIVERY_REPLAYS + _MAX_DELIVERY_REPLAYS,
            )
            delivery_rows = self._query(
                f"SELECT {', '.join(_HOT_DELIVERY_COLUMNS)} "
                f"FROM {_qualified(self._database, _DELIVERY_TABLE)} "
                "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
                "AND catalog_epoch=%(catalog_epoch)s AND catalog_revision=%(catalog_revision)s "
                "AND build_token=%(build_token)s AND source_adapter='span_attribute' "
                "AND producer_stream_id=%(producer_stream_id)s "
                "ORDER BY sequence, _version LIMIT %(row_limit)s",
                {
                    "organization_id": lease.organization_id,
                    "workspace_id": lease.workspace_id,
                    "catalog_epoch": lease.catalog_epoch,
                    "catalog_revision": lease.catalog_revision,
                    "build_token": lease.build_token,
                    "producer_stream_id": hot.producer_stream_id,
                    "row_limit": delivery_row_cap + 1,
                },
            )
            _require_below_row_cap(
                delivery_rows,
                cap=delivery_row_cap,
                label="hot physical delivery audit",
            )
            checkpoint = proof.to_checkpoint(
                assignment=assignment,
                source_version_fence=hot.source_version_fence,
                delivery_rows=delivery_rows,
            )
            value = CheckpointWrite(
                checkpoint=checkpoint,
                source_cursor="",
                watermark=str(hot.source_version_fence),
                source_version_fence=hot.source_version_fence,
                source_fingerprint=checkpoint.source_digest,
                previous_payload_sha256=checkpoint.terminal_payload_sha256,
                processed_rows=checkpoint.source_count,
                gap_reasons=(),
            )
            self._append_checkpoint(value)
            self._audit_delivery_chain(checkpoint)
            return checkpoint

        return self._serializer.serialize(key, append_serialized)

    def list_activations(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
    ) -> Sequence[ActivationRecord]:
        rows = self.list_activation_rows(
            organization_id=organization_id,
            workspace_id=workspace_id,
            catalog_epoch=catalog_epoch,
        )
        grouped: dict[tuple[int, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[
                (
                    _uint(row["catalog_revision"], "catalog_revision"),
                    _text(row["build_token"]),
                )
            ].append(row)
        records: list[ActivationRecord] = []
        logical = tuple(
            column for column in _ACTIVATION_COLUMNS if column != "_version"
        )
        for key in sorted(grouped):
            latest = _latest_row(
                grouped[key], logical_columns=logical, label=f"activation:{key}"
            )
            assert latest is not None
            records.append(_activation(latest))
        return tuple(records)

    def append_active(
        self,
        record: ActivationRecord,
        *,
        fence_sha256: str,
        checkpoint_state_sha256s: tuple[str, ...],
    ) -> ActivationRecord:
        key = (
            f"activation:{self._database}:{record.organization_id}:"
            f"{record.workspace_id}:{record.catalog_epoch}"
        )
        return self._serializer.serialize(
            key,
            lambda: self._append_active_serialized(
                record,
                fence_sha256=fence_sha256,
                checkpoint_state_sha256s=checkpoint_state_sha256s,
            ),
        )

    def _append_active_serialized(
        self,
        record: ActivationRecord,
        *,
        fence_sha256: str,
        checkpoint_state_sha256s: tuple[str, ...],
    ) -> ActivationRecord:
        if record.revision_fence_sha256 != fence_sha256:
            raise ValueError("activation fence digest mismatch")
        if not checkpoint_state_sha256s:
            raise ValueError("activation requires checkpoint state evidence")
        rows = self._activation_rows(record)
        logical = tuple(
            column for column in _ACTIVATION_COLUMNS if column != "_version"
        )
        latest_by_key = _latest_activations(rows, logical_columns=logical)
        record_key = (record.catalog_revision, record.build_token)
        latest = latest_by_key.get(record_key)
        row = _activation_row(record)
        if latest is not None:
            if _row_identity(latest, logical) == _row_identity(row, logical):
                return record
            raise PropertyCatalogStateConflict(
                "activation key already contains another immutable state"
            )
        if any(
            revision == record.catalog_revision for revision, _token in latest_by_key
        ):
            raise PropertyCatalogStateConflict(
                "catalog revision already has another active build"
            )
        sequences = [
            _uint(value.get("activation_sequence"), "activation_sequence")
            for value in latest_by_key.values()
        ]
        if len(sequences) != len(set(sequences)):
            raise PropertyCatalogStateConflict(
                "multiple activations claim the same activation_sequence"
            )
        expected_sequence = 1 if not sequences else max(sequences) + 1
        maximum_revision = max(
            (revision for revision, _token in latest_by_key), default=0
        )
        if (
            record.activation_sequence != expected_sequence
            or record.version != expected_sequence
            or record.catalog_revision <= maximum_revision
        ):
            raise PropertyCatalogStateConflict(
                "activation sequence or revision is not workspace-monotonic"
            )
        self._insert(
            _ACTIVATION_TABLE,
            row,
            deduplication_token=(
                "property-catalog-activation-v1:"
                f"{record.build_token}:{record.activation_sha256}"
            ),
        )
        persisted = self._activation_rows(record)
        verified_by_key = _latest_activations(persisted, logical_columns=logical)
        persisted_sequences = [
            _uint(value.get("activation_sequence"), "activation_sequence")
            for value in verified_by_key.values()
        ]
        verified = verified_by_key.get(record_key)
        if (
            verified is None
            or _row_identity(verified, logical) != _row_identity(row, logical)
            or len(persisted_sequences) != len(set(persisted_sequences))
        ):
            raise PropertyCatalogStateConflict(
                "activation append was not preserved as the unique latest state"
            )
        return record

    def _activation_rows(self, record: ActivationRecord) -> Sequence[Mapping[str, Any]]:
        return self.list_activation_rows(
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            catalog_epoch=record.catalog_epoch,
        )

    def list_activation_rows(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
    ) -> Sequence[Mapping[str, Any]]:
        """Return conflict-visible latest rows for the newest bounded lineage."""

        return self._query(
            f"SELECT {', '.join(f'activation.{column}' for column in _ACTIVATION_COLUMNS)} "
            f"FROM {_qualified(self._database, _ACTIVATION_TABLE)} AS activation "
            "INNER JOIN (SELECT catalog_revision, build_token, max(_version) AS latest_version, "
            "max(activation_sequence) AS latest_activation_sequence "
            f"FROM {_qualified(self._database, _ACTIVATION_TABLE)} "
            "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
            "AND catalog_epoch=%(catalog_epoch)s AND status='active' "
            "GROUP BY catalog_revision, build_token "
            "ORDER BY latest_activation_sequence DESC, catalog_revision DESC LIMIT 4096) "
            "AS recent USING (catalog_revision, build_token) "
            "WHERE activation.organization_id=%(organization_id)s "
            "AND activation.workspace_id=%(workspace_id)s "
            "AND activation.catalog_epoch=%(catalog_epoch)s "
            "AND activation.status='active' AND activation._version=recent.latest_version "
            "ORDER BY activation.activation_sequence DESC, activation.catalog_revision DESC, "
            "activation.build_token",
            {
                "organization_id": organization_id,
                "workspace_id": workspace_id,
                "catalog_epoch": catalog_epoch,
            },
        )

    def _query(
        self, sql: str, params: Mapping[str, Any]
    ) -> Sequence[Mapping[str, Any]]:
        _validate_client(self._client, self._database)
        return self._client.query(
            sql, params, timeout_ms=_remaining(self._deadline, self._timeout_ms)
        )

    def _insert(
        self,
        table: str,
        row: Mapping[str, Any],
        *,
        deduplication_token: str,
    ) -> None:
        if table not in _CONTROL_WRITE_TABLES:
            raise PropertyCatalogStateError("forbidden property catalog control table")
        _validate_client(self._client, self._database)
        self._client.insert(
            _qualified(self._database, table),
            (row,),
            columns=tuple(row),
            timeout_ms=_remaining(self._deadline, self._timeout_ms),
            deduplication_token=deduplication_token,
        )


class ClickHouseCurrentBindingReader(CurrentBindingReader):
    """Resolve one exact current row per definition binding without ``FINAL``."""

    def __init__(
        self,
        client: CatalogStateClient,
        *,
        database: str,
        deadline: SharedCatalogDeadline | None = None,
        timeout_ms: int | None = None,
        max_rows: int | None = None,
    ) -> None:
        _validate_client(client, database)
        if timeout_ms is None:
            timeout_ms = RUNTIME_LIMITS.state_store_timeout_ms
        if max_rows is None:
            max_rows = RUNTIME_LIMITS.current_binding_max_rows
        if (
            type(timeout_ms) is not int
            or not 1 <= timeout_ms <= RUNTIME_LIMITS.state_store_timeout_ms
        ):
            raise ValueError(
                "current-binding timeout_ms must be in [1, "
                f"{RUNTIME_LIMITS.state_store_timeout_ms}]"
            )
        if (
            type(max_rows) is not int
            or not 1 <= max_rows <= RUNTIME_LIMITS.current_binding_max_rows
        ):
            raise ValueError(
                "current-binding max_rows must be in [1, "
                f"{RUNTIME_LIMITS.current_binding_max_rows}]"
            )
        self._client = client
        self._database = database
        self._deadline = deadline
        self._timeout_ms = timeout_ms
        self._max_rows = max_rows

    def read_current(
        self,
        *,
        context: PostgresSnapshotContext,
        source_adapter: SourceAdapter,
        at_revision: int,
        build_token: str,
    ) -> Sequence[PropertyBindingRow]:
        _validate_client(self._client, self._database)
        if type(at_revision) is not int or not (
            1 <= at_revision <= context.catalog_revision
        ):
            raise ValueError(
                "current-binding revision must be positive and no newer than its context"
            )
        activation_rows = tuple(
            self._client.query(
                f"SELECT {', '.join(f'activation.{column}' for column in _ACTIVATION_COLUMNS)} "
                f"FROM {_qualified(self._database, _ACTIVATION_TABLE)} AS activation "
                "INNER JOIN (SELECT catalog_revision, build_token, "
                "max(_version) AS latest_version, "
                "max(activation_sequence) AS latest_activation_sequence "
                f"FROM {_qualified(self._database, _ACTIVATION_TABLE)} "
                "WHERE organization_id=%(organization_id)s "
                "AND workspace_id=%(workspace_id)s AND catalog_epoch=%(catalog_epoch)s "
                "AND catalog_revision<=%(catalog_revision)s "
                "GROUP BY catalog_revision, build_token "
                "ORDER BY latest_activation_sequence DESC, catalog_revision DESC LIMIT 4096) "
                "AS recent USING (catalog_revision, build_token) "
                "WHERE activation.organization_id=%(organization_id)s "
                "AND activation.workspace_id=%(workspace_id)s "
                "AND activation.catalog_epoch=%(catalog_epoch)s "
                "AND activation.catalog_revision<=%(catalog_revision)s "
                "AND activation._version=recent.latest_version "
                "ORDER BY activation.activation_sequence DESC, "
                "activation.catalog_revision DESC, activation.build_token",
                {
                    "organization_id": context.organization_id,
                    "workspace_id": context.workspace_id,
                    "catalog_epoch": context.catalog_epoch,
                    "catalog_revision": at_revision,
                },
                timeout_ms=_remaining(self._deadline, self._timeout_ms),
            )
        )
        lineage = _active_lineage(activation_rows)
        # A building token is readable only at its exact current revision.  The
        # FULL_REPAIR rev-1 baseline must remain exclusively on successfully
        # activated lineage and must never admit an aborted/unactivated build.
        allowed = set(lineage)
        if at_revision == context.catalog_revision:
            allowed.add((at_revision, build_token, context.projection_version))
        allowed_lineage = tuple(sorted(allowed))
        # A full repair may legitimately ask for the rev-1 baseline when the
        # previous initial backfill never activated.  There is no readable
        # lineage in that case.  Avoid sending an empty tuple set to
        # ClickHouse: recent analyzers reject it as a zero-width tuple before
        # they can reduce the predicate to false.
        if not allowed_lineage:
            return ()
        _validate_client(self._client, self._database)
        rows = tuple(
            self._client.query(
                f"SELECT {', '.join(_BINDING_COLUMNS)} "
                f"FROM {_qualified(self._database, _DEFINITION_TABLE)} "
                "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
                "AND catalog_epoch=%(catalog_epoch)s AND catalog_revision<=%(catalog_revision)s "
                "AND source_adapter=%(source_adapter)s "
                "AND tuple(catalog_revision, toString(build_token), projection_version) "
                "IN %(allowed_lineage)s "
                "ORDER BY binding_id, catalog_revision, source_version, state_sha256, "
                "producer_sequence LIMIT %(row_limit)s",
                {
                    "organization_id": context.organization_id,
                    "workspace_id": context.workspace_id,
                    "catalog_epoch": context.catalog_epoch,
                    "catalog_revision": at_revision,
                    "source_adapter": str(source_adapter),
                    "allowed_lineage": allowed_lineage,
                    "row_limit": self._max_rows + 1,
                },
                timeout_ms=_remaining(self._deadline, self._timeout_ms),
            )
        )
        if len(rows) > self._max_rows:
            raise PropertyCatalogStateError("current-binding read exceeded row ceiling")
        histories: dict[str, list[PropertyBindingRow]] = defaultdict(list)
        for raw in rows:
            binding = _binding(raw)
            histories[binding.binding_id].append(binding)
        resolved = []
        for binding_id in sorted(histories):
            try:
                resolved.append(
                    resolve_binding_history(
                        histories[binding_id], at_revision=at_revision
                    ).current
                )
            except ValueError as exc:
                raise PropertyCatalogStateConflict(
                    f"definition binding {binding_id} has conflicting latest state"
                ) from exc
        return tuple(resolved)


def _validate_client(client: CatalogStateClient, database: str) -> None:
    try:
        require_catalog_database(database)
    except ValueError as exc:
        raise ValueError(
            "control-plane access requires an isolated DEV catalog database"
        ) from exc
    except RuntimeError as exc:
        raise ValueError(
            "control-plane access requires an isolated DEV catalog database"
        ) from exc
    if getattr(client, "catalog_database", None) != database:
        raise ValueError("catalog client database does not match the DEV target")


def _qualified(database: str, table: str) -> str:
    try:
        require_catalog_database(database)
    except RuntimeError as exc:
        raise PropertyCatalogStateError("invalid DEV catalog database") from exc
    if table not in PROPERTY_CATALOG_TABLES:
        raise PropertyCatalogStateError("forbidden property catalog table")
    return f"`{database}`.`{table}`"


def _remaining(deadline: SharedCatalogDeadline | None, timeout_ms: int) -> int:
    return timeout_ms if deadline is None else deadline.remaining_ms(cap_ms=timeout_ms)


def _require_below_row_cap(
    rows: Sequence[Mapping[str, Any]],
    *,
    cap: int,
    label: str,
) -> None:
    """Reject cap+1 evidence before any latest/immutable state is trusted."""

    if type(cap) is not int or cap < 1:
        raise ValueError("row cap must be a positive integer")
    if len(rows) > cap:
        raise PropertyCatalogStateConflict(
            f"{label} exceeded its conflict-proof row cap"
        )


def _latest_row(
    rows: Sequence[Mapping[str, Any]],
    *,
    logical_columns: Sequence[str],
    label: str,
) -> Mapping[str, Any] | None:
    if not rows:
        return None
    maximum = max(_uint(row.get("_version"), "_version") for row in rows)
    latest = [row for row in rows if _uint(row.get("_version"), "_version") == maximum]
    identities = {_row_identity(row, logical_columns) for row in latest}
    if len(identities) != 1:
        raise PropertyCatalogStateConflict(
            f"{label} has different rows at _version={maximum}"
        )
    return latest[0]


def _latest_activations(
    rows: Sequence[Mapping[str, Any]], *, logical_columns: Sequence[str]
) -> dict[tuple[int, str], Mapping[str, Any]]:
    grouped: dict[tuple[int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                _uint(row.get("catalog_revision"), "catalog_revision"),
                _text(row.get("build_token")),
            )
        ].append(row)
    result: dict[tuple[int, str], Mapping[str, Any]] = {}
    for key, values in grouped.items():
        latest = _latest_row(
            values,
            logical_columns=logical_columns,
            label=f"activation:{key[0]}:{key[1]}",
        )
        assert latest is not None
        result[key] = latest
    return result


def _active_lineage(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[int, str, int], ...]:
    """Return the conflict-checked lineage of the newest active activation.

    Older lineages before the persisted full-repair/initial anchor are
    intentionally excluded.  The caller may then add only the exact current
    building token; every prior build admitted here is a successfully active
    activation.
    """

    grouped: dict[tuple[int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                _uint(row.get("catalog_revision"), "catalog_revision"),
                _text(row.get("build_token")),
            )
        ].append(row)
    logical = tuple(column for column in _ACTIVATION_COLUMNS if column != "_version")
    by_revision: dict[int, ActivationRecord] = {}
    by_sequence: dict[int, tuple[int, str]] = {}
    for key in sorted(grouped):
        latest = _latest_row(
            grouped[key], logical_columns=logical, label=f"activation-lineage:{key}"
        )
        assert latest is not None
        if _text(latest.get("status")) != str(ActivationStatus.ACTIVE):
            continue
        try:
            record = _activation(latest)
        except (KeyError, TypeError, ValueError, PropertyCatalogStateError) as exc:
            raise PropertyCatalogStateConflict(
                f"activation lineage {key[0]}:{key[1]} is invalid"
            ) from exc
        existing = by_revision.get(record.catalog_revision)
        if existing is not None and existing.build_token != record.build_token:
            raise PropertyCatalogStateConflict(
                f"catalog revision {record.catalog_revision} has multiple active build lineages"
            )
        by_revision[record.catalog_revision] = record
        sequence_key = (record.catalog_revision, record.build_token)
        sequence_owner = by_sequence.get(record.activation_sequence)
        if sequence_owner is not None and sequence_owner != sequence_key:
            raise PropertyCatalogStateConflict(
                f"activation sequence {record.activation_sequence} has multiple active owners"
            )
        by_sequence[record.activation_sequence] = sequence_key
    if not by_revision:
        return ()

    ordered_active = tuple(by_revision[revision] for revision in sorted(by_revision))
    if any(
        later.activation_sequence <= earlier.activation_sequence
        for earlier, later in zip(ordered_active, ordered_active[1:], strict=False)
    ):
        raise PropertyCatalogStateConflict(
            "active catalog revisions and activation sequences are not monotonic"
        )
    newest = max(
        by_revision.values(),
        key=lambda value: (
            value.activation_sequence,
            value.catalog_revision,
            value.build_token,
        ),
    )
    anchor = newest.lineage_anchor_revision
    if newest.catalog_revision - anchor > _MAX_ACTIVE_LINEAGE_REVISIONS:
        raise PropertyCatalogStateConflict(
            "active catalog lineage exceeds the bounded revision window"
        )
    selected = {
        revision: record
        for revision, record in by_revision.items()
        if anchor <= revision <= newest.catalog_revision
    }
    if anchor not in selected:
        raise PropertyCatalogStateConflict(
            "active catalog lineage is missing its persisted anchor"
        )
    ordered_lineage = tuple(selected[revision] for revision in sorted(selected))
    if any(
        later.activation_sequence != earlier.activation_sequence + 1
        for earlier, later in zip(ordered_lineage, ordered_lineage[1:], strict=False)
    ):
        raise PropertyCatalogStateConflict(
            "active catalog lineage has a missing activation sequence"
        )
    for revision, record in selected.items():
        if (
            record.lineage_anchor_revision != anchor
            or record.projection_version != newest.projection_version
        ):
            raise PropertyCatalogStateConflict(
                "active catalog lineage disagrees with its anchor or projection"
            )
        if revision == anchor:
            if record.lifecycle_mode not in {
                CatalogLifecycleMode.INITIAL_BACKFILL,
                CatalogLifecycleMode.FULL_REPAIR,
            }:
                raise PropertyCatalogStateConflict(
                    "active catalog lineage anchor is not a snapshot activation"
                )
        elif record.lifecycle_mode is not CatalogLifecycleMode.INCREMENTAL:
            raise PropertyCatalogStateConflict(
                "active catalog lineage contains a non-incremental descendant"
            )
    return tuple(
        (
            selected[revision].catalog_revision,
            selected[revision].build_token,
            selected[revision].projection_version,
        )
        for revision in sorted(selected)
    )


def _row_identity(row: Mapping[str, Any], columns: Sequence[str]) -> str:
    def normalize(value: Any) -> Any:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC).isoformat(timespec="microseconds")
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        return value

    return json.dumps(
        [normalize(row.get(column)) for column in columns],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _checkpoint_row(
    value: CheckpointWrite, *, now: datetime, version: int
) -> dict[str, Any]:
    checkpoint = value.checkpoint
    return {
        "organization_id": checkpoint.organization_id,
        "workspace_id": checkpoint.workspace_id,
        "catalog_epoch": checkpoint.catalog_epoch,
        "catalog_revision": checkpoint.catalog_revision,
        "build_token": checkpoint.build_token,
        "projection_version": checkpoint.projection_version,
        "source_adapter": str(checkpoint.source_adapter),
        "producer_stream_id": checkpoint.producer_stream_id,
        "status": str(checkpoint.status),
        "terminal": int(checkpoint.terminal),
        "source_cursor": value.source_cursor,
        "watermark": value.watermark,
        "source_version_fence": value.source_version_fence,
        "source_fingerprint": value.source_fingerprint,
        "source_rows": checkpoint.source_count,
        "processed_rows": value.processed_rows,
        "definition_rows": checkpoint.definition_count,
        "value_rows": checkpoint.value_count,
        "tombstone_rows": checkpoint.tombstone_count,
        "gap_count": checkpoint.gap_count,
        "poison_count": checkpoint.poison_count,
        "conflict_count": checkpoint.conflict_count,
        "gap_reasons": list(value.gap_reasons),
        "first_sequence": checkpoint.first_sequence,
        "last_sequence": checkpoint.last_sequence,
        "last_issued_sequence": checkpoint.last_issued_sequence,
        "fenced_sequence": checkpoint.fenced_sequence,
        "terminal_payload_sha256": checkpoint.terminal_payload_sha256,
        "delivery_count": checkpoint.delivery_count,
        "source_digest": checkpoint.source_digest,
        "emitted_digest": checkpoint.emitted_digest,
        "previous_payload_sha256": value.previous_payload_sha256,
        "run_id": "00000000-0000-0000-0000-000000000000",
        "worker_id": "property-catalog-reconciler",
        "error": "",
        "started_at": now,
        "updated_at": now,
        "finished_at": now if checkpoint.terminal else None,
        "_version": version,
    }


def _checkpoint(row: Mapping[str, Any]) -> CatalogCheckpoint:
    """Map physical ``*_rows`` names to logical checkpoint counts."""

    return CatalogCheckpoint(
        organization_id=_text(row["organization_id"]),
        workspace_id=_text(row["workspace_id"]),
        catalog_epoch=_uint(row["catalog_epoch"], "catalog_epoch"),
        catalog_revision=_uint(row["catalog_revision"], "catalog_revision"),
        build_token=_text(row["build_token"]),
        projection_version=_uint(row["projection_version"], "projection_version"),
        source_adapter=SourceAdapter(_text(row["source_adapter"])),
        producer_stream_id=_text(row["producer_stream_id"]),
        source_version_fence=_uint(row["source_version_fence"], "source_version_fence"),
        status=CheckpointStatus(_text(row["status"])),
        terminal=_bool(row["terminal"], "terminal"),
        source_count=_uint(row["source_rows"], "source_rows"),
        definition_count=_uint(row["definition_rows"], "definition_rows"),
        value_count=_uint(row["value_rows"], "value_rows"),
        tombstone_count=_uint(row["tombstone_rows"], "tombstone_rows"),
        gap_count=_uint(row["gap_count"], "gap_count"),
        poison_count=_uint(row["poison_count"], "poison_count"),
        conflict_count=_uint(row["conflict_count"], "conflict_count"),
        first_sequence=_nullable_uint(row.get("first_sequence"), "first_sequence"),
        last_sequence=_nullable_uint(row.get("last_sequence"), "last_sequence"),
        last_issued_sequence=_uint(row["last_issued_sequence"], "last_issued_sequence"),
        fenced_sequence=_uint(row["fenced_sequence"], "fenced_sequence"),
        terminal_payload_sha256=_text(row["terminal_payload_sha256"]),
        delivery_count=_uint(row["delivery_count"], "delivery_count"),
        source_digest=_text(row["source_digest"]),
        emitted_digest=_text(row["emitted_digest"]),
    )


def _checkpoint_write(row: Mapping[str, Any]) -> CheckpointWrite:
    return CheckpointWrite(
        checkpoint=_checkpoint(row),
        source_cursor=_text(row["source_cursor"]),
        watermark=_text(row["watermark"]),
        source_version_fence=_uint(row["source_version_fence"], "source_version_fence"),
        source_fingerprint=_text(row["source_fingerprint"]),
        previous_payload_sha256=_text(row["previous_payload_sha256"]),
        processed_rows=_uint(row["processed_rows"], "processed_rows"),
        gap_reasons=tuple(_text(reason) for reason in row.get("gap_reasons", ())),
    )


def _activation_row(record: ActivationRecord) -> dict[str, Any]:
    return {
        "organization_id": record.organization_id,
        "workspace_id": record.workspace_id,
        "catalog_epoch": record.catalog_epoch,
        "catalog_revision": record.catalog_revision,
        "build_token": record.build_token,
        "projection_version": record.projection_version,
        "lifecycle_mode": str(record.lifecycle_mode),
        "lineage_anchor_revision": record.lineage_anchor_revision,
        "activation_sequence": record.activation_sequence,
        "source_manifest_json": record.source_manifest_json,
        "source_manifest_sha256": record.source_manifest_sha256,
        "revision_fence_sha256": record.revision_fence_sha256,
        "activation_sha256": record.activation_sha256,
        "status": str(record.status),
        "live_definition_rows": record.live_definition_rows,
        "tombstone_rows": record.tombstone_rows,
        "value_rows": record.value_rows,
        "qualified_at": record.qualified_at,
        "updated_at": record.updated_at,
        "_version": record.version,
    }


def _activation(row: Mapping[str, Any]) -> ActivationRecord:
    return ActivationRecord(
        organization_id=_text(row["organization_id"]),
        workspace_id=_text(row["workspace_id"]),
        catalog_epoch=_uint(row["catalog_epoch"], "catalog_epoch"),
        catalog_revision=_uint(row["catalog_revision"], "catalog_revision"),
        build_token=_text(row["build_token"]),
        projection_version=_uint(row["projection_version"], "projection_version"),
        lifecycle_mode=CatalogLifecycleMode(_text(row["lifecycle_mode"])),
        lineage_anchor_revision=_uint(
            row["lineage_anchor_revision"], "lineage_anchor_revision"
        ),
        activation_sequence=_uint(row["activation_sequence"], "activation_sequence"),
        source_manifest_json=_text(row["source_manifest_json"]),
        source_manifest_sha256=_text(row["source_manifest_sha256"]),
        revision_fence_sha256=_text(row["revision_fence_sha256"]),
        activation_sha256=_text(row["activation_sha256"]),
        status=ActivationStatus(_text(row["status"])),
        live_definition_rows=_uint(row["live_definition_rows"], "live_definition_rows"),
        tombstone_rows=_uint(row["tombstone_rows"], "tombstone_rows"),
        value_rows=_uint(row["value_rows"], "value_rows"),
        qualified_at=_datetime(row["qualified_at"], "qualified_at"),
        updated_at=_datetime(row["updated_at"], "updated_at"),
        version=_uint(row["_version"], "_version"),
    )


def _binding(row: Mapping[str, Any]) -> PropertyBindingRow:
    try:
        definition_json = _text(row["definition_json"])
        decoded = json.loads(definition_json)
        definition = CanonicalDefinition(
            property_id=_text(row["property_id"]),
            property_kind=PropertyKind(_text(row["property_kind"])),
            category=PropertyCategory(_text(row["category"])),
            category_rank=_uint(row["category_rank"], "category_rank"),
            source_rank=_uint(row["source_rank"], "source_rank"),
            definition_source=_text(row["definition_source"]),
            primary_source=_text(row["primary_source"]),
            primary_source_folded=_text(row["primary_source_folded"]),
            source_tokens=tuple(_text(item) for item in row["source_tokens"]),
            value_adapter=_text(row["value_adapter"]),
            name=_text(row["name"]),
            display_name=_text(row["display_name"]),
            sort_name_folded=_text(row["sort_name_folded"]),
            search_text_folded=_text(row["search_text_folded"]),
            value_type=_text(decoded["value_type"]),
            output_type=_text(decoded["output_type"]),
            role=PropertyRole(_text(row["role"])),
            definition_json=definition_json,
            definition_sha256=_text(row["definition_sha256"]),
        )
        return PropertyBindingRow(
            organization_id=_text(row["organization_id"]),
            workspace_id=_text(row["workspace_id"]),
            catalog_epoch=_uint(row["catalog_epoch"], "catalog_epoch"),
            catalog_revision=_uint(row["catalog_revision"], "catalog_revision"),
            build_token=_text(row["build_token"]),
            projection_version=_uint(row["projection_version"], "projection_version"),
            binding_id=_text(row["binding_id"]),
            visibility_scope=VisibilityScope(_text(row["visibility_scope"])),
            visibility_id=_text(row["visibility_id"]),
            definition=definition,
            source_adapter=SourceAdapter(_text(row["source_adapter"])),
            source_entity_id=_text(row["source_entity_id"]),
            source_version=_uint(row["source_version"], "source_version"),
            source_fingerprint=_text(row["source_fingerprint"]),
            is_deleted=_bool(row["is_deleted"], "is_deleted"),
            deleted_at=_nullable_datetime(row.get("deleted_at"), "deleted_at"),
            state_sha256=_text(row["state_sha256"]),
            producer_stream_id=_text(row["producer_stream_id"]),
            producer_sequence=_uint(row["producer_sequence"], "producer_sequence"),
            first_seen=_nullable_datetime(row.get("first_seen"), "first_seen"),
            last_seen=_nullable_datetime(row.get("last_seen"), "last_seen"),
            emitted_at=_datetime(row["emitted_at"], "emitted_at"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PropertyCatalogStateError(
            "catalog contains an invalid definition row"
        ) from exc


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    return str(value)


def _uint(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value < (1 << 64):
        raise PropertyCatalogStateError(f"{field} is not a UInt64")
    return value


def _nullable_uint(value: Any, field: str) -> int | None:
    return None if value is None else _uint(value, field)


def _bool(value: Any, field: str) -> bool:
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise PropertyCatalogStateError(f"{field} is not a UInt8 boolean")


def _datetime(value: Any, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PropertyCatalogStateError(f"{field} is not a timestamp") from exc
    if not isinstance(value, datetime):
        raise PropertyCatalogStateError(f"{field} is not a timestamp")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _nullable_datetime(value: Any, field: str) -> datetime | None:
    return None if value is None else _datetime(value, field)


__all__ = [
    "CatalogStateClient",
    "ClickHouseCatalogStateStore",
    "ClickHouseCurrentBindingReader",
    "PropertyCatalogStateConflict",
    "PropertyCatalogStateError",
]
