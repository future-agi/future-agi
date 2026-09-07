"""Exact ACTIVE write intents, used only inside the workspace activation lock.

The existing fsynced, bounded shared-volume journal owns physical persistence.
Receipts are retained per lease; the coordinator's activation marker remains the
workspace high-water witness. This is not historical-scope authorization and
does not revoke leases, publish reader control, or acknowledge source repairs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any, Protocol

from .activation import (
    ActivationRecord,
    ActivationStatus,
    CatalogLifecycleMode,
    RevisionBuildPlan,
    RevisionFence,
    RevisionFenceStatus,
    RevisionLease,
    StreamDrainProof,
)
from .codec import canonical_json, canonical_uuid, framed_sha256, require_sha256
from .models import SourceAdapter

PUBLICATION_FORMAT = "futureagi.property-catalog-active-publication.v1"
MAX_PUBLICATION_BYTES = 262_144


class PublicationJournalError(RuntimeError):
    """Publication cannot be replayed or resolved from exact durable evidence."""


class PublicationStorage(Protocol):
    def load_record(self, key: str) -> dict[str, Any] | None: ...

    def save_record(self, key: str, intent: Mapping[str, Any]) -> None: ...


def _document(value: Any) -> dict[str, Any]:
    return json.loads(
        json.dumps(
            asdict(value), default=lambda v: v.isoformat(timespec="microseconds")
        )
    )


def _record(document: Mapping[str, Any]) -> ActivationRecord:
    values = dict(document)
    values["status"] = ActivationStatus(values["status"])
    values["lifecycle_mode"] = CatalogLifecycleMode(values["lifecycle_mode"])
    for field in ("qualified_at", "updated_at"):
        values[field] = datetime.fromisoformat(values[field])
    result = ActivationRecord(**values)
    manifest = json.loads(result.source_manifest_json)
    for field in (
        "organization_id",
        "workspace_id",
        "catalog_epoch",
        "catalog_revision",
        "build_token",
        "projection_version",
    ):
        if manifest.get(field) != getattr(result, field):
            raise ValueError("ACTIVE manifest identity differs from its row")
    if result.version != result.activation_sequence or _document(result) != document:
        raise ValueError("ACTIVE row is not canonical")
    return result


def _fence(document: Mapping[str, Any]) -> RevisionFence:
    values = dict(document)
    values["status"] = RevisionFenceStatus(values["status"])
    values["stream_proofs"] = tuple(
        StreamDrainProof(
            **{**proof, "source_adapter": SourceAdapter(proof["source_adapter"])}
        )
        for proof in values["stream_proofs"]
    )
    values["checkpoint_state_sha256s"] = tuple(values["checkpoint_state_sha256s"])
    for field in ("drain_deadline", "fenced_at"):
        values[field] = datetime.fromisoformat(values[field])
    result = RevisionFence(**values)
    if _document(result) != document:
        raise ValueError("publication fence is not canonical")
    return result


def _marker(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {
        "catalog_revision",
        "build_token",
        "build_lease_sha256",
    }:
        raise ValueError("invalid activation marker")
    if (
        type(value["catalog_revision"]) is not int
        or not 0 < value["catalog_revision"] < 1 << 64
    ):
        raise ValueError("invalid activation marker revision")
    if (
        canonical_uuid(value["build_token"], field="build_token")
        != value["build_token"]
    ):
        raise ValueError("invalid activation marker token")
    require_sha256(value["build_lease_sha256"], field="build_lease_sha256")


def _key(workspace_key: str, lease_sha256: str) -> str:
    return f"{workspace_key}:active-publication:{lease_sha256}"


def _validate(document: dict[str, Any]) -> None:
    """Validate even checksum-correct but malformed/conflicting journal content."""
    try:
        canonical_json(document, max_bytes=MAX_PUBLICATION_BYTES)
        if set(document) != {
            "format",
            "database",
            "lease",
            "phase",
            "previous_marker",
            "publication",
        }:
            raise ValueError("invalid publication fields")
        if document["format"] != PUBLICATION_FORMAT:
            raise ValueError("invalid publication format")
        _marker(document["previous_marker"])
        lease_doc = document["lease"]
        plan = RevisionBuildPlan.from_json(lease_doc["build_plan_json"])
        lease = RevisionLease(
            organization_id=plan.organization_id,
            workspace_id=plan.workspace_id,
            catalog_epoch=plan.catalog_epoch,
            catalog_revision=plan.catalog_revision,
            build_token=plan.build_token,
            projection_version=plan.projection_version,
            build_plan_json=plan.canonical_json,
            build_lease_sha256=plan.sha256,
            issued_at=datetime.fromisoformat(lease_doc["issued_at"]),
            expires_at=datetime.fromisoformat(lease_doc["expires_at"]),
        )
        if _lease_document(lease) != lease_doc:
            raise ValueError("invalid publication lease")
        if (
            document["previous_marker"] is not None
            and document["previous_marker"]["catalog_revision"] >= plan.catalog_revision
        ):
            raise ValueError("publication predecessor is not older")
        publication = document["publication"]
        phase = document["phase"]
        if phase == "admitted":
            if publication is not None:
                raise ValueError("admitted publication must be unsent")
            return
        if phase not in {"prepared", "armed", "resolved"}:
            raise ValueError("invalid publication phase")
        if set(publication) != {
            "record",
            "fence",
            "checkpoint_states",
            "previous_active",
            "sha256",
        }:
            raise ValueError("invalid publication evidence")
        record, fence = _record(publication["record"]), _fence(publication["fence"])
        if (
            fence.build_plan_json != lease.build_plan_json
            or fence.build_lease_sha256 != lease.build_lease_sha256
            or record.revision_fence_sha256 != fence.fence_sha256
            or record.source_manifest_sha256 != fence.manifest_sha256
            or any(
                getattr(record, field) != getattr(fence, field)
                for field in (
                    "organization_id",
                    "workspace_id",
                    "catalog_epoch",
                    "catalog_revision",
                    "build_token",
                    "projection_version",
                )
            )
            or sorted(publication["checkpoint_states"])
            != sorted(fence.checkpoint_state_sha256s)
        ):
            raise ValueError("publication evidence changed identity")
        previous = publication["previous_active"]
        # Only a self-anchored snapshot can cross consumed (invalidated) slots.
        # INITIAL may have no ACTIVE predecessor despite nonzero history; the
        # marker proof and store's exact-next append guard still authorize it.
        if previous is not None:
            prior = _record(previous)
            if (
                any(
                    getattr(record, field) != getattr(prior, field)
                    for field in (
                        "organization_id",
                        "workspace_id",
                        "catalog_epoch",
                        "projection_version",
                    )
                )
                or prior.catalog_revision >= record.catalog_revision
                or prior.activation_sequence >= record.activation_sequence
                or record.lifecycle_mode is CatalogLifecycleMode.INITIAL_BACKFILL
                or (
                    record.lifecycle_mode is CatalogLifecycleMode.INCREMENTAL
                    and prior.activation_sequence + 1 != record.activation_sequence
                )
            ):
                raise ValueError("publication predecessor changed lineage")
        elif record.lifecycle_mode is not CatalogLifecycleMode.INITIAL_BACKFILL:
            raise ValueError("publication lacks its predecessor")
        body = {key: value for key, value in publication.items() if key != "sha256"}
        if publication["sha256"] != framed_sha256(
            PUBLICATION_FORMAT,
            document["database"],
            canonical_json(lease_doc),
            canonical_json(body, max_bytes=MAX_PUBLICATION_BYTES),
        ):
            raise ValueError("publication evidence digest changed")
    except (KeyError, TypeError, ValueError, RecursionError, AttributeError) as exc:
        raise PublicationJournalError("ACTIVE publication journal is corrupt") from exc


def _lease_document(lease: RevisionLease) -> dict[str, Any]:
    return {
        "build_plan_json": lease.build_plan_json,
        "issued_at": lease.issued_at.isoformat(timespec="microseconds"),
        "expires_at": lease.expires_at.isoformat(timespec="microseconds"),
    }


class ActivationPublicationSession:
    """One lock-scoped lease. No operation here can send an ACTIVE insert."""

    def __init__(
        self,
        storage: PublicationStorage,
        *,
        workspace_key: str,
        database: str,
        lease: RevisionLease,
        marker: dict[str, Any] | None,
        confirm_terminal_repair: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        try:
            _marker(marker)
        except (TypeError, ValueError) as exc:
            raise PublicationJournalError("invalid legacy activation marker") from exc
        self.storage, self.workspace_key, self.database, self.lease = (
            storage,
            workspace_key,
            database,
            lease,
        )
        self._confirm_terminal_repair = confirm_terminal_repair
        self.key = _key(workspace_key, lease.build_lease_sha256)
        self.document = storage.load_record(self.key)
        self.positive_only = (
            marker is not None and marker["catalog_revision"] > lease.catalog_revision
        )
        if self.document is not None:
            _validate(self.document)
            if self.document["database"] != database or self.document[
                "lease"
            ] != _lease_document(lease):
                raise PublicationJournalError(
                    "publication journal changed database/lease identity"
                )
        elif (
            marker is not None and marker["catalog_revision"] >= lease.catalog_revision
        ):
            # Never turn an old three-field marker into permission to resend.
            self.positive_only = True
        else:
            self.document = {
                "format": PUBLICATION_FORMAT,
                "database": database,
                "lease": _lease_document(lease),
                "phase": "admitted",
                "previous_marker": marker,
                "publication": None,
            }
            self._save(self.document)

    def _save(self, document: dict[str, Any]) -> None:
        _validate(document)
        self.storage.save_record(self.key, document)
        self.document = document

    @property
    def record(self) -> ActivationRecord | None:
        publication = self.document["publication"] if self.document else None
        return _record(publication["record"]) if publication else None

    def check_evidence(
        self, *, fence: RevisionFence, checkpoint_states: tuple[str, ...]
    ) -> None:
        if self.document and self.document["publication"]:
            publication = self.document["publication"]
            # fenced_at is diagnostic, not part of the fence digest. Recovery
            # may reconstruct it; all hashed drain/checkpoint/plan bytes must agree.
            if publication["fence"][
                "fence_sha256"
            ] != fence.fence_sha256 or publication["checkpoint_states"] != list(
                checkpoint_states
            ):
                raise PublicationJournalError(
                    "publication qualification evidence changed"
                )

    def check_visible_head(self, activations: Sequence[ActivationRecord]) -> None:
        publication = self.document["publication"] if self.document else None
        previous = publication["previous_active"] if publication else None
        if previous is not None:
            for observed in activations:
                if (
                    observed.catalog_revision == previous["catalog_revision"]
                    and _document(observed) != previous
                ):
                    raise PublicationJournalError(
                        "publication previous ACTIVE head changed"
                    )

    def require_predecessor(
        self,
        activations: Sequence[ActivationRecord],
        *,
        confirm_completion: Callable[[ActivationRecord], None],
    ) -> None:
        marker = self.document["previous_marker"] if self.document else None
        if marker is None:
            return
        observed = next(
            (
                row
                for row in activations
                if row.catalog_revision == marker["catalog_revision"]
                and row.build_token == marker["build_token"]
            ),
            None,
        )
        if observed is None:
            if self._confirm_terminal_repair is not None:
                from .native_write_journal import NativeTerminalRepairReceipt

                receipt = self._confirm_terminal_repair(marker)
                if type(receipt) is NativeTerminalRepairReceipt:
                    scope = receipt.binding.quarantine.scope
                    if (
                        receipt.binding.build_lease_sha256
                        == marker["build_lease_sha256"]
                        and scope.catalog_revision == marker["catalog_revision"]
                        and scope.build_token == marker["build_token"]
                        and all(
                            getattr(scope, field) == getattr(self.lease, field)
                            for field in (
                                "organization_id",
                                "workspace_id",
                                "catalog_epoch",
                                "projection_version",
                            )
                        )
                    ):
                        # Preserve the old marker and publication bytes. Only
                        # exact proven terminal state can replace its ACTIVE proof.
                        return
            raise PublicationJournalError(
                "previous publication is not positively visible"
            )
        previous_key = _key(self.workspace_key, marker["build_lease_sha256"])
        previous = self.storage.load_record(previous_key)
        if previous is not None:
            _validate(previous)
            if (
                previous["database"] != self.database
                or previous["phase"] not in {"armed", "resolved"}
                or previous["publication"]["record"] != _document(observed)
                or RevisionBuildPlan.from_json(
                    previous["lease"]["build_plan_json"]
                ).sha256
                != marker["build_lease_sha256"]
            ):
                raise PublicationJournalError(
                    "previous publication conflicts with ACTIVE"
                )
            confirm_completion(observed)
            if previous["phase"] == "armed":
                self.storage.save_record(
                    previous_key, {**previous, "phase": "resolved"}
                )
        else:
            # Legacy predecessor has positive ACTIVE evidence, not resend
            # authority. The same completion policy must admit it as a head.
            confirm_completion(observed)

    def prepare(
        self,
        record: ActivationRecord,
        *,
        fence: RevisionFence,
        checkpoint_states: tuple[str, ...],
        previous: ActivationRecord | None,
    ) -> None:
        if self.positive_only or self.document is None:
            raise PublicationJournalError(
                "legacy/unresolved ACTIVE requires positive evidence only"
            )
        if self.document["phase"] != "admitted":
            raise PublicationJournalError("publication intent is already immutable")
        body = {
            "record": _document(record),
            "fence": _document(fence),
            "checkpoint_states": list(checkpoint_states),
            "previous_active": _document(previous) if previous else None,
        }
        publication = {
            **body,
            "sha256": framed_sha256(
                PUBLICATION_FORMAT,
                self.database,
                canonical_json(self.document["lease"]),
                canonical_json(body, max_bytes=MAX_PUBLICATION_BYTES),
            ),
        }
        self._save({**self.document, "phase": "prepared", "publication": publication})

    def arm(self, *, previous: ActivationRecord | None) -> None:
        if self.positive_only or self.document is None:
            raise PublicationJournalError(
                "legacy/unresolved ACTIVE requires positive evidence only"
            )
        if self.document["phase"] not in {"prepared", "armed"}:
            raise PublicationJournalError(
                "resolved publication is not positively visible"
            )
        if self.document["publication"]["previous_active"] != (
            _document(previous) if previous else None
        ):
            raise PublicationJournalError("publication previous ACTIVE head changed")
        if self.document["phase"] == "prepared":
            self._save({**self.document, "phase": "armed"})

    def resolve(self, observed: ActivationRecord) -> None:
        if self.record != observed or self.document["phase"] not in {
            "armed",
            "resolved",
        }:
            raise PublicationJournalError(
                "ACTIVE differs from its armed immutable publication"
            )
        if self.document["phase"] != "resolved":
            self._save({**self.document, "phase": "resolved"})
