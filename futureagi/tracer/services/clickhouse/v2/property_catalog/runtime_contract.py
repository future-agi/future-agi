"""Strict cross-language hot-producer drain handoff v2.

The producer fence and proof files must be mounted from the same shared volume
into the Python rollout process and the Go collector sidecar. A standalone
Python pod cannot safely prepare, bind, or activate a hot stream.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from .activation import StreamDrainProof
from .codec import canonical_uuid, framed_sha256, require_sha256
from .coordinator import ProducerRevisionAssignment
from .models import SourceAdapter
from .proof_limits import (
    MAX_DELIVERIES_PER_REVISION,
    MAX_PROOF_BYTES,
)
from .qualification import CatalogCheckpoint, CheckpointStatus

_MAX_PROOF_BYTES = MAX_PROOF_BYTES
_MAX_DELIVERIES = MAX_DELIVERIES_PER_REVISION
_ZERO_SHA256 = "0" * 64
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_PROOF_FIELDS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "source_adapter",
    "producer_stream_id",
    "build_lease_sha256",
    "drain_intent_fence_sha256",
    "observed_fence_sha256",
    "drain_deadline",
    "phase",
    "last_data_sequence",
    "terminal_sequence",
    "last_issued_sequence",
    "last_acknowledged_sequence",
    "terminal_issued",
    "terminal_acknowledged",
    "source_count",
    "definition_count",
    "value_count",
    "tombstone_count",
    "delivery_count",
    "source_digest",
    "emitted_digest",
    "terminal_payload_sha256",
    "gap_issued",
    "gap_acknowledged",
    "pending_envelopes",
    "pending_admissions",
    "poisoned",
    "ready",
)
_DELIVERY_IDENTITY_FIELDS = (
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
)


class ProducerDrainProofError(ValueError):
    """The hot producer/physical ledger cannot prove one lossless drain."""


@dataclass(frozen=True, slots=True)
class HotDeliveryEvidence:
    source_count: int
    definition_count: int
    value_count: int
    tombstone_count: int
    delivery_count: int
    source_digest: str
    emitted_digest: str
    terminal_payload_sha256: str


@dataclass(frozen=True, slots=True)
class ProducerDrainProof:
    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    source_adapter: SourceAdapter
    producer_stream_id: str
    build_lease_sha256: str
    drain_intent_fence_sha256: str
    observed_fence_sha256: str
    drain_deadline: str
    phase: str
    last_data_sequence: int
    terminal_sequence: int
    last_issued_sequence: int
    last_acknowledged_sequence: int
    terminal_issued: bool
    terminal_acknowledged: bool
    source_count: int
    definition_count: int
    value_count: int
    tombstone_count: int
    delivery_count: int
    source_digest: str
    emitted_digest: str
    terminal_payload_sha256: str
    gap_issued: bool
    gap_acknowledged: bool
    pending_envelopes: int
    pending_admissions: int
    poisoned: bool
    ready: bool

    def __post_init__(self) -> None:
        for field_name in (
            "organization_id",
            "workspace_id",
            "build_token",
            "producer_stream_id",
        ):
            object.__setattr__(
                self,
                field_name,
                canonical_uuid(getattr(self, field_name), field=field_name),
            )
        if self.source_adapter is not SourceAdapter.SPAN_ATTRIBUTE:
            raise ProducerDrainProofError("hot proof must be span_attribute")
        if self.phase not in {
            "building",
            "preparing",
            "prepared",
            "bound",
            "ready",
            "poisoned",
        }:
            raise ProducerDrainProofError("hot proof phase is invalid")
        for field_name in (
            "build_lease_sha256",
            "observed_fence_sha256",
            "source_digest",
            "emitted_digest",
            "terminal_payload_sha256",
        ):
            require_sha256(getattr(self, field_name), field=field_name)
        if self.drain_intent_fence_sha256:
            require_sha256(
                self.drain_intent_fence_sha256,
                field="drain_intent_fence_sha256",
            )
        for field_name in (
            "catalog_epoch",
            "catalog_revision",
            "projection_version",
            "last_data_sequence",
            "terminal_sequence",
            "last_issued_sequence",
            "last_acknowledged_sequence",
            "source_count",
            "definition_count",
            "value_count",
            "tombstone_count",
            "delivery_count",
            "pending_envelopes",
            "pending_admissions",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or not 0 <= value < (1 << 64):
                raise ProducerDrainProofError(f"{field_name} is outside UInt64")
        if (
            not self.catalog_epoch
            or not self.catalog_revision
            or not self.projection_version
        ):
            raise ProducerDrainProofError("proof revision identity must be positive")
        if self.delivery_count > _MAX_DELIVERIES:
            raise ProducerDrainProofError("proof delivery count exceeds bounded audit")
        for field_name in (
            "terminal_issued",
            "terminal_acknowledged",
            "gap_issued",
            "gap_acknowledged",
            "poisoned",
            "ready",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ProducerDrainProofError(f"{field_name} must be boolean")
        if self.definition_count or self.tombstone_count:
            raise ProducerDrainProofError(
                "hot values proof contains definition/tombstone rows"
            )
        if self.last_acknowledged_sequence > self.last_issued_sequence:
            raise ProducerDrainProofError("proof acknowledgement exceeds issuance")
        if self.delivery_count != self.last_acknowledged_sequence:
            raise ProducerDrainProofError(
                "proof delivery count differs from acknowledged tail"
            )
        if self.phase in {"prepared", "bound", "ready"}:
            if (
                not self.drain_intent_fence_sha256
                or not self.drain_deadline
                or self.terminal_sequence != self.last_data_sequence + 1
            ):
                raise ProducerDrainProofError(
                    "prepared proof lacks one exact terminal boundary"
                )
            _parse_time(self.drain_deadline, field="drain_deadline")
        prepared = (
            self.phase == "prepared"
            and not self.poisoned
            and not self.terminal_issued
            and not self.terminal_acknowledged
            and self.last_issued_sequence == self.last_data_sequence
            and self.last_acknowledged_sequence == self.last_data_sequence
            and self.pending_envelopes == 0
            and self.pending_admissions == 0
            and not self.gap_issued
            and not self.gap_acknowledged
        )
        if self.phase == "prepared" and not prepared:
            raise ProducerDrainProofError(
                "prepared proof is not quiescent at its data tail"
            )
        computed_ready = (
            self.phase == "ready"
            and not self.poisoned
            and self.terminal_issued
            and self.terminal_acknowledged
            and self.last_issued_sequence == self.terminal_sequence
            and self.last_acknowledged_sequence == self.terminal_sequence
            and self.pending_envelopes == 0
            and self.pending_admissions == 0
            and not self.gap_issued
            and not self.gap_acknowledged
            and self.terminal_payload_sha256 != _ZERO_SHA256
        )
        if self.ready != computed_ready:
            raise ProducerDrainProofError(
                "ready flag disagrees with canonical proof state"
            )

    @property
    def prepared(self) -> bool:
        return self.phase == "prepared"

    def validate_prepared(
        self,
        *,
        assignment: ProducerRevisionAssignment,
        delivery_rows: Sequence[Mapping[str, Any]],
    ) -> HotDeliveryEvidence:
        if not self.prepared:
            raise ProducerDrainProofError("hot producer has not prepared a boundary")
        self._require_assignment(assignment, expected_sequence=0, intent=True)
        evidence = derive_hot_delivery_evidence(
            proof=self,
            delivery_rows=delivery_rows,
            terminal_required=False,
        )
        self._require_evidence(evidence)
        return evidence

    def to_checkpoint(
        self,
        *,
        assignment: ProducerRevisionAssignment,
        source_version_fence: int,
        delivery_rows: Sequence[Mapping[str, Any]],
    ) -> CatalogCheckpoint:
        if not self.ready:
            raise ProducerDrainProofError("hot producer is not losslessly drained")
        self._require_assignment(
            assignment, expected_sequence=self.terminal_sequence, intent=False
        )
        evidence = derive_hot_delivery_evidence(
            proof=self,
            delivery_rows=delivery_rows,
            terminal_required=True,
        )
        self._require_evidence(evidence)
        if type(source_version_fence) is not int or not 1 <= source_version_fence < (
            1 << 64
        ):
            raise ProducerDrainProofError("hot source version fence is invalid")
        return CatalogCheckpoint(
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            catalog_epoch=self.catalog_epoch,
            catalog_revision=self.catalog_revision,
            build_token=self.build_token,
            projection_version=self.projection_version,
            source_adapter=self.source_adapter,
            producer_stream_id=self.producer_stream_id,
            source_version_fence=source_version_fence,
            status=CheckpointStatus.COMPLETE,
            terminal=True,
            source_count=self.source_count,
            definition_count=0,
            value_count=self.value_count,
            tombstone_count=0,
            gap_count=0,
            poison_count=0,
            conflict_count=0,
            first_sequence=1,
            last_sequence=self.terminal_sequence,
            last_issued_sequence=self.terminal_sequence,
            fenced_sequence=self.terminal_sequence,
            terminal_payload_sha256=self.terminal_payload_sha256,
            delivery_count=self.delivery_count,
            source_digest=self.source_digest,
            emitted_digest=self.emitted_digest,
        )

    def to_stream_proof(
        self,
        *,
        assignment: ProducerRevisionAssignment,
        checkpoint: CatalogCheckpoint,
    ) -> StreamDrainProof:
        self._require_assignment(
            assignment, expected_sequence=self.terminal_sequence, intent=False
        )
        if (
            not self.ready
            or checkpoint.organization_id != self.organization_id
            or checkpoint.workspace_id != self.workspace_id
            or checkpoint.catalog_epoch != self.catalog_epoch
            or checkpoint.catalog_revision != self.catalog_revision
            or checkpoint.build_token != self.build_token
            or checkpoint.projection_version != self.projection_version
            or checkpoint.source_adapter is not self.source_adapter
            or checkpoint.producer_stream_id != self.producer_stream_id
            or not checkpoint.terminal
            or checkpoint.last_sequence != self.terminal_sequence
            or checkpoint.source_count != self.source_count
            or checkpoint.definition_count != self.definition_count
            or checkpoint.value_count != self.value_count
            or checkpoint.tombstone_count != self.tombstone_count
            or checkpoint.delivery_count != self.delivery_count
            or checkpoint.source_digest != self.source_digest
            or checkpoint.emitted_digest != self.emitted_digest
            or checkpoint.terminal_payload_sha256 != self.terminal_payload_sha256
        ):
            raise ProducerDrainProofError(
                "hot proof, exact assignment, and physical checkpoint differ"
            )
        return StreamDrainProof(
            source_adapter=self.source_adapter,
            producer_stream_id=self.producer_stream_id,
            last_issued_sequence=self.terminal_sequence,
            fenced_sequence=self.terminal_sequence,
            terminal_sequence=self.terminal_sequence,
            terminal_payload_sha256=self.terminal_payload_sha256,
        )

    def _require_assignment(
        self,
        assignment: ProducerRevisionAssignment,
        *,
        expected_sequence: int,
        intent: bool,
    ) -> None:
        expected_fence = (
            self.drain_intent_fence_sha256 if intent else self.observed_fence_sha256
        )
        if (
            assignment.organization_id != self.organization_id
            or assignment.workspace_id != self.workspace_id
            or assignment.catalog_epoch != self.catalog_epoch
            or assignment.catalog_revision != self.catalog_revision
            or assignment.build_token != self.build_token
            or assignment.projection_version != self.projection_version
            or assignment.build_lease_sha256 != self.build_lease_sha256
            or assignment.status != "draining"
            or assignment.fenced_sequence != expected_sequence
            or assignment.fence_sha256 != expected_fence
            or assignment.drain_deadline is None
            or _time_text(assignment.drain_deadline) != self.drain_deadline
        ):
            raise ProducerDrainProofError(
                "proof differs from its exact drain assignment"
            )

    def _require_evidence(self, evidence: HotDeliveryEvidence) -> None:
        if (
            evidence.source_count != self.source_count
            or evidence.definition_count != self.definition_count
            or evidence.value_count != self.value_count
            or evidence.tombstone_count != self.tombstone_count
            or evidence.delivery_count != self.delivery_count
            or evidence.source_digest != self.source_digest
            or evidence.emitted_digest != self.emitted_digest
            or evidence.terminal_payload_sha256 != self.terminal_payload_sha256
        ):
            raise ProducerDrainProofError(
                "producer proof differs from raw physical delivery evidence"
            )


def derive_hot_delivery_evidence(
    *,
    proof: ProducerDrainProof,
    delivery_rows: Sequence[Mapping[str, Any]],
    terminal_required: bool,
) -> HotDeliveryEvidence:
    """Derive one raw immutable identity per sequence; never use latest version."""

    if not isinstance(delivery_rows, Sequence) or len(delivery_rows) > (
        _MAX_DELIVERIES * 8
    ):
        raise ProducerDrainProofError("physical delivery audit exceeds its bound")
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for raw in delivery_rows:
        if not isinstance(raw, Mapping):
            raise ProducerDrainProofError("physical delivery row is not a mapping")
        sequence = _uint(raw.get("sequence"), field="sequence")
        grouped[sequence].append(raw)
    final_sequence = (
        proof.terminal_sequence if terminal_required else proof.last_data_sequence
    )
    if tuple(sorted(grouped)) != tuple(range(1, final_sequence + 1)):
        raise ProducerDrainProofError(
            "physical delivery ledger is not exact/contiguous"
        )
    deliveries: list[Mapping[str, Any]] = []
    for sequence in sorted(grouped):
        identities = {
            json.dumps(
                [_normalize(row.get(field)) for field in _DELIVERY_IDENTITY_FIELDS],
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            for row in grouped[sequence]
        }
        if len(identities) != 1:
            raise ProducerDrainProofError(
                f"physical delivery sequence {sequence} has conflicting identities"
            )
        deliveries.append(grouped[sequence][0])

    previous = _ZERO_SHA256
    source_digest = _EMPTY_SHA256
    emitted_digest = _EMPTY_SHA256
    counts = [0, 0, 0, 0]
    for index, row in enumerate(deliveries, start=1):
        if (
            str(row.get("organization_id")) != proof.organization_id
            or str(row.get("workspace_id")) != proof.workspace_id
            or _uint(row.get("catalog_epoch"), field="catalog_epoch")
            != proof.catalog_epoch
            or _uint(row.get("catalog_revision"), field="catalog_revision")
            != proof.catalog_revision
            or str(row.get("build_token")) != proof.build_token
            or _uint(row.get("projection_version"), field="projection_version")
            != proof.projection_version
            or str(row.get("source_adapter")) != str(proof.source_adapter)
            or str(row.get("producer_stream_id")) != proof.producer_stream_id
            or _uint(row.get("sequence"), field="sequence") != index
            or str(row.get("envelope_format")) != "futureagi.property-catalog-envelope"
            or _uint(row.get("envelope_version"), field="envelope_version") != 1
        ):
            raise ProducerDrainProofError("physical delivery crosses proof identity")
        payload = require_sha256(str(row.get("payload_sha256")), field="payload_sha256")
        prior = require_sha256(
            str(row.get("previous_payload_sha256")),
            field="previous_payload_sha256",
        )
        source_batch = require_sha256(
            str(row.get("source_batch_digest")), field="source_batch_digest"
        )
        require_sha256(str(row.get("envelope_id")), field="envelope_id")
        if prior != previous:
            raise ProducerDrainProofError("physical delivery payload chain is broken")
        terminal = _bool(row.get("terminal"), field="terminal")
        if terminal != (terminal_required and index == final_sequence):
            raise ProducerDrainProofError(
                "physical terminal is missing, early, or duplicated"
            )
        reasons = tuple(str(reason) for reason in row.get("gap_reasons", ()))
        if reasons or str(row.get("outcome")) != "committed":
            raise ProducerDrainProofError("hot physical delivery contains a gap")
        row_counts = [
            _uint(row.get(field), field=field)
            for field in (
                "source_rows",
                "definition_rows",
                "value_rows",
                "tombstone_rows",
            )
        ]
        if terminal and any(row_counts):
            raise ProducerDrainProofError("terminal delivery is not an empty fence")
        for offset, value in enumerate(row_counts):
            counts[offset] += value
            if counts[offset] >= 1 << 64:
                raise ProducerDrainProofError(
                    "physical delivery count overflows UInt64"
                )
        if not terminal:
            source_digest = framed_sha256(
                "futureagi.property-catalog.hot-source-stream.v1",
                source_digest,
                source_batch,
            )
        emitted_digest = framed_sha256(
            "futureagi.property-catalog.emitted-stream.v1",
            emitted_digest,
            payload,
        )
        previous = payload
    terminal_payload = previous if terminal_required else _ZERO_SHA256
    return HotDeliveryEvidence(
        source_count=counts[0],
        definition_count=counts[1],
        value_count=counts[2],
        tombstone_count=counts[3],
        delivery_count=len(deliveries),
        source_digest=source_digest,
        emitted_digest=emitted_digest,
        terminal_payload_sha256=terminal_payload,
    )


def parse_producer_drain_proof(raw: bytes) -> tuple[ProducerDrainProof, ...]:
    if (
        not isinstance(raw, bytes)
        or len(raw) < 2
        or len(raw) > _MAX_PROOF_BYTES
        or not raw.endswith(b"\n")
        or b"\n" in raw[:-1]
    ):
        raise ProducerDrainProofError("drain proof must be one bounded JSON line")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ProducerDrainProofError("drain proof contains a duplicate key")
            result[key] = value
        return result

    try:
        document = json.loads(raw[:-1].decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ProducerDrainProofError("drain proof JSON is invalid") from exc
    if (
        not isinstance(document, dict)
        or tuple(document) != ("format", "version", "proofs")
        or document["format"] != "futureagi.property-catalog-drain-proof"
        or document["version"] != 2
        or not isinstance(document["proofs"], list)
        or json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
        != raw[:-1]
    ):
        raise ProducerDrainProofError("drain proof document is not canonical v2")
    proofs: list[ProducerDrainProof] = []
    seen: set[tuple[str, str, int, int, str, str]] = set()
    for raw_proof in document["proofs"]:
        if not isinstance(raw_proof, dict) or tuple(raw_proof) != _PROOF_FIELDS:
            raise ProducerDrainProofError("drain proof entry shape is invalid")
        try:
            proof = ProducerDrainProof(
                **{
                    **raw_proof,
                    "source_adapter": SourceAdapter(raw_proof["source_adapter"]),
                }
            )
        except (TypeError, ValueError) as exc:
            raise ProducerDrainProofError("drain proof entry is invalid") from exc
        key = (
            proof.organization_id,
            proof.workspace_id,
            proof.catalog_epoch,
            proof.catalog_revision,
            proof.build_token,
            proof.producer_stream_id,
        )
        if key in seen:
            raise ProducerDrainProofError("drain proof contains a duplicate stream")
        seen.add(key)
        proofs.append(proof)
    return tuple(proofs)


def select_producer_drain_proof(
    proofs: Sequence[ProducerDrainProof],
    *,
    assignment: ProducerRevisionAssignment,
    producer_stream_id: str,
    phase: str,
) -> ProducerDrainProof:
    """Select one current proof and reject same-workspace stale file content."""

    producer_stream_id = canonical_uuid(producer_stream_id, field="producer_stream_id")
    if phase not in {"prepared", "ready"}:
        raise ProducerDrainProofError("poll phase must be prepared or ready")
    if assignment.status != "draining" or assignment.drain_deadline is None:
        raise ProducerDrainProofError("proof polling requires a draining assignment")
    same_workspace = tuple(
        proof
        for proof in proofs
        if proof.organization_id == assignment.organization_id
        and proof.workspace_id == assignment.workspace_id
    )
    expected = tuple(
        proof
        for proof in same_workspace
        if proof.catalog_epoch == assignment.catalog_epoch
        and proof.catalog_revision == assignment.catalog_revision
        and proof.build_token == assignment.build_token
        and proof.projection_version == assignment.projection_version
        and proof.build_lease_sha256 == assignment.build_lease_sha256
        and proof.producer_stream_id == producer_stream_id
    )
    if len(same_workspace) != 1 or len(expected) != 1:
        raise ProducerDrainProofError(
            "proof file contains stale or conflicting workspace build evidence"
        )
    proof = expected[0]
    assignment_digest = (
        proof.drain_intent_fence_sha256
        if phase == "prepared"
        else proof.observed_fence_sha256
    )
    expected_sequence = 0 if phase == "prepared" else proof.terminal_sequence
    if (
        proof.phase != phase
        or proof.ready != (phase == "ready")
        or assignment.fenced_sequence != expected_sequence
        or assignment.fence_sha256 != assignment_digest
        or proof.drain_deadline != _time_text(assignment.drain_deadline)
    ):
        raise ProducerDrainProofError(
            "proof phase, exact boundary, deadline, or fence digest is stale"
        )
    return proof


def _parse_time(value: str, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ProducerDrainProofError(f"{field} must be a timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ProducerDrainProofError(
            f"{field} is not canonical DateTime64(6)"
        ) from exc
    if _time_text(parsed) != value:
        raise ProducerDrainProofError(f"{field} is not canonical DateTime64(6)")
    return parsed


def _time_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _uint(value: Any, *, field: str) -> int:
    if type(value) is not int or not 0 <= value < (1 << 64):
        raise ProducerDrainProofError(f"{field} is not UInt64")
    return value


def _bool(value: Any, *, field: str) -> bool:
    if type(value) is bool:
        return value
    if type(value) is int and value in {0, 1}:
        return bool(value)
    raise ProducerDrainProofError(f"{field} is not boolean")


def _normalize(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _time_text(value if value.tzinfo else value.replace(tzinfo=UTC))
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    return value


__all__ = [
    "HotDeliveryEvidence",
    "ProducerDrainProof",
    "ProducerDrainProofError",
    "derive_hot_delivery_evidence",
    "parse_producer_drain_proof",
    "select_producer_drain_proof",
]
