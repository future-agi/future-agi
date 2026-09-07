"""Frozen terminal rows derived from original evidence; no I/O or write authority.

The caller owns the workspace lock, marker check, and exact quarantine admission.
In particular, publication_document=None is permissible only after that caller
has excluded any publication marker or possibly sent ACTIVE outside this evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from .activation import RevisionLease
from .codec import canonical_uuid
from .native_write_journal import (
    MAX_SCOPE_PENDING,
    NativeQuarantineIntent,
    NativeQuarantineReason,
    NativeScopeQuarantine,
    NativeTerminalRepairBinding,
    NativeTerminalWriteIntent,
    NativeWriteScope,
    _cell,
    _parameters,
    _uncell,
    native_parameters_sha256,
)
from .native_write_proof import _COLUMNS, _parameter
from .publication_journal import (
    PublicationJournalError,
    _lease_document,
    _record,
    _validate,
)
from .publisher import PropertyCatalogPublishError, require_catalog_database
from .state_store import _activation_row
from .write_admission import _canonical

UINT64_MAX = (1 << 64) - 1
MAX_TERMINAL_REPAIR_INTENT_BYTES = 2 << 20
_FORMAT = "futureagi.property-catalog-terminal-repair-intent"
_ACTIVE = "property_catalog_activations"
_SOURCE = "property_catalog_source_streams"
_EVIDENCE_FIELDS = frozenset(
    {
        "database",
        "quarantine",
        "lease",
        "reservation",
        "publication_document",
        "repaired_at",
    }
)


class TerminalRepairIntentError(ValueError):
    """Missing, changed, unbounded or noncanonical terminal-repair evidence."""


def _bounded(document: Any) -> bytes:
    raw = _canonical(document) + b"\n"
    if len(raw) > MAX_TERMINAL_REPAIR_INTENT_BYTES:
        raise TerminalRepairIntentError("terminal repair intent exceeds byte bound")
    return raw


def _quarantine(document: Mapping[str, Any]) -> NativeScopeQuarantine:
    if set(document) != {
        "scope",
        "admission_sha256",
        "generation",
        "pending",
        "reason",
    }:
        raise TerminalRepairIntentError("quarantine evidence fields differ")
    pending = document["pending"]
    if type(pending) is not list or len(pending) > MAX_SCOPE_PENDING:
        raise TerminalRepairIntentError("quarantine pending inventory exceeds bound")
    return NativeScopeQuarantine(
        scope=NativeWriteScope(**document["scope"]),
        admission_sha256=document["admission_sha256"],
        generation=document["generation"],
        pending=tuple(NativeQuarantineIntent(**item) for item in pending),
        reason=NativeQuarantineReason(document["reason"]),
    )


def _row(table: str, parameters: Any) -> Mapping[str, Any]:
    if type(parameters) is not list or len(parameters) != len(_COLUMNS[table]):
        raise TerminalRepairIntentError("terminal evidence requires a complete row")
    # Native decoding freezes arrays to tuples while retaining UUID/datetime
    # tags and their original microseconds. Column order is the schema order.
    return MappingProxyType(
        dict(zip(_COLUMNS[table], (_uncell(cell) for cell in parameters), strict=True))
    )


def _validate_reservation(
    row: Mapping[str, Any], lease: RevisionLease, scope: NativeWriteScope
) -> None:
    if NativeWriteScope.from_rows(_SOURCE, (row,), scope) != scope:
        raise TerminalRepairIntentError("reservation differs from quarantined scope")
    if (
        row["source_adapter"] != "system_manifest"
        or type(row["envelope_version"]) is not int
        or row["envelope_version"] != 0
        or canonical_uuid(row["producer_stream_id"], field="producer_stream_id")
        != lease.build_token
        or row["build_plan_json"] != lease.build_plan_json
        or row["build_lease_sha256"] != lease.build_lease_sha256
        or _parameter(row["started_at"]) != _parameter(lease.issued_at)
        or _parameter(row["drain_deadline"]) != _parameter(lease.expires_at)
    ):
        raise TerminalRepairIntentError("reservation differs from frozen lease/plan")
    if row["status"] not in {"open", "draining", "fenced", "complete", "gap", "failed"}:
        raise TerminalRepairIntentError("unsupported original reservation status")
    if type(row["_version"]) is not int or not 1 <= row["_version"] < UINT64_MAX:
        raise TerminalRepairIntentError(
            "original reservation has no higher terminal version"
        )


@dataclass(frozen=True, slots=True, init=False)
class FrozenTerminalRepairIntent:
    binding: NativeTerminalRepairBinding
    rows: tuple[Mapping[str, Any], ...]
    lease: RevisionLease
    _raw: bytes

    def __init__(self):
        raise TypeError("construct terminal intent with from_evidence or decode")

    @classmethod
    def from_evidence(
        cls,
        *,
        database: str,
        quarantine: NativeScopeQuarantine,
        lease: RevisionLease,
        reservation: Mapping[str, Any],
        publication_document: dict[str, Any] | None,
        repaired_at: datetime,
    ) -> FrozenTerminalRepairIntent:
        """Bind complete original rows; marker absence remains the caller's check."""
        try:
            if (
                type(database) is not str
                or require_catalog_database(database) != database
            ):
                raise TerminalRepairIntentError("invalid terminal repair database")
            if (
                type(lease) is not RevisionLease
                or type(quarantine) is not NativeScopeQuarantine
            ):
                raise TerminalRepairIntentError(
                    "terminal repair requires a frozen lease and quarantine"
                )
            if type(repaired_at) is not datetime:
                raise TerminalRepairIntentError("repaired_at requires a UTC datetime")
            if (
                publication_document is not None
                and type(publication_document) is not dict
            ):
                raise TerminalRepairIntentError(
                    "publication evidence requires its complete document"
                )
            # Snapshot all inputs before deriving anything. The persisted format
            # retains the full typed lease and full original source row, not a head.
            evidence = json.loads(
                _bounded(
                    {
                        "database": database,
                        "quarantine": asdict(quarantine),
                        "lease": {
                            key: _cell(value) for key, value in asdict(lease).items()
                        },
                        "reservation": _parameters(_COLUMNS[_SOURCE], (reservation,))[
                            0
                        ],
                        "publication_document": publication_document,
                        "repaired_at": _cell(repaired_at),
                    }
                )
            )
            frozen_lease = RevisionLease(
                **{key: _uncell(value) for key, value in evidence["lease"].items()}
            )
            frozen_quarantine = _quarantine(evidence["quarantine"])
            scope = NativeWriteScope(
                frozen_lease.organization_id,
                frozen_lease.workspace_id,
                frozen_lease.catalog_epoch,
                frozen_lease.projection_version,
                frozen_lease.catalog_revision,
                frozen_lease.build_token,
            )
            if scope != frozen_quarantine.scope:
                raise TerminalRepairIntentError("lease differs from quarantined scope")
            original = _row(_SOURCE, evidence["reservation"])
            _validate_reservation(original, frozen_lease, scope)
            timestamp = _uncell(evidence["repaired_at"])
            if timestamp < frozen_lease.issued_at:
                raise TerminalRepairIntentError("repair predates frozen lease issuance")
            document = evidence["publication_document"]
            publication = None
            if document is not None:
                _validate(document)
                if document["database"] != database or document[
                    "lease"
                ] != _lease_document(frozen_lease):
                    raise TerminalRepairIntentError(
                        "publication differs from frozen database/lease"
                    )
                publication = document["publication"]
            rows, tables = [], []
            publication_sha256 = None
            active_token = active_digest = None
            if publication is not None:
                record = _record(publication["record"])
                if record.version >= UINT64_MAX:
                    raise TerminalRepairIntentError(
                        "original ACTIVE has no higher terminal version"
                    )
                active = _activation_row(record)
                active_token = f"property-catalog-activation-v1:{record.build_token}:{record.activation_sha256}"
                active_digest = native_parameters_sha256(_COLUMNS[_ACTIVE], (active,))
                publication_sha256 = publication["sha256"]
                rows.append(
                    {
                        **active,
                        "status": "disabled",
                        "_version": UINT64_MAX,
                        "updated_at": timestamp,
                    }
                )
                tables.append(_ACTIVE)
            for pending in frozen_quarantine.pending:
                if pending.table == _ACTIVE and (
                    publication is None
                    or pending.deduplication_token != active_token
                    or pending.parameters_sha256 != active_digest
                ):
                    raise TerminalRepairIntentError(
                        "pending ACTIVE differs from frozen publication evidence"
                    )
            reason = _canonical(
                {
                    "format": _FORMAT,
                    "reason": frozen_quarantine.reason.value,
                    "evidence_sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
                }
            ).decode("utf-8")
            rows.append(
                {
                    **original,
                    "status": "failed",
                    "_version": UINT64_MAX,
                    "updated_at": timestamp,
                    "gap_count": 1,
                    "gap_reasons": (reason,),
                }
            )
            tables.append(_SOURCE)
            binding = NativeTerminalRepairBinding(
                quarantine=frozen_quarantine,
                build_lease_sha256=frozen_lease.build_lease_sha256,
                publication_sha256=publication_sha256,
                writes=tuple(
                    NativeTerminalWriteIntent(
                        table=table,
                        deduplication_token=f"property-catalog-terminal-repair-v1:{scope.build_token}:{table}",
                        parameters_sha256=native_parameters_sha256(
                            _COLUMNS[table], (row,)
                        ),
                    )
                    for table, row in zip(tables, rows, strict=True)
                ),
            )
            parameters = [
                _parameters(_COLUMNS[table], (row,))[0]
                for table, row in zip(tables, rows, strict=True)
            ]
            body = {
                "format": _FORMAT,
                "version": 1,
                "evidence": evidence,
                "binding": asdict(binding),
                "rows": parameters,
            }
            raw = _bounded(
                {**body, "sha256": hashlib.sha256(_canonical(body)).hexdigest()}
            )
            result = object.__new__(cls)
            object.__setattr__(result, "binding", binding)
            object.__setattr__(
                result,
                "rows",
                tuple(
                    _row(table, values)
                    for table, values in zip(tables, parameters, strict=True)
                ),
            )
            object.__setattr__(result, "lease", frozen_lease)
            object.__setattr__(result, "_raw", raw)
            return result
        except TerminalRepairIntentError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
            RecursionError,
            OverflowError,
            PublicationJournalError,
            PropertyCatalogPublishError,
        ) as exc:
            raise TerminalRepairIntentError(
                f"invalid terminal repair evidence: {exc}"
            ) from exc

    def encode(self) -> bytes:
        return self._raw

    @property
    def database(self) -> str:
        return json.loads(self._raw)["evidence"]["database"]

    @property
    def sha256(self) -> str:
        """Digest of the exact canonical encode() bytes, including its newline."""
        return hashlib.sha256(self._raw).hexdigest()

    @property
    def publication_document(self) -> dict[str, Any] | None:
        return json.loads(self._raw)["evidence"]["publication_document"]

    @property
    def reservation(self) -> dict[str, Any]:
        return dict(_row(_SOURCE, json.loads(self._raw)["evidence"]["reservation"]))

    @classmethod
    def decode(cls, raw: bytes) -> FrozenTerminalRepairIntent:
        """Revalidate original evidence and rederive every stored terminal byte."""
        try:
            if (
                type(raw) is not bytes
                or not 0 < len(raw) <= MAX_TERMINAL_REPAIR_INTENT_BYTES
            ):
                raise TerminalRepairIntentError(
                    "terminal repair bytes exceed bound or are missing"
                )
            document = json.loads(raw)
            if (
                type(document) is not dict
                or set(document)
                != {"format", "version", "evidence", "binding", "rows", "sha256"}
                or document["format"] != _FORMAT
                or type(document["version"]) is not int
                or document["version"] != 1
                or _bounded(document) != raw
            ):
                raise TerminalRepairIntentError("noncanonical terminal repair document")
            body = {key: value for key, value in document.items() if key != "sha256"}
            if document["sha256"] != hashlib.sha256(_canonical(body)).hexdigest():
                raise TerminalRepairIntentError("terminal repair checksum differs")
            evidence = document["evidence"]
            if type(evidence) is not dict or set(evidence) != _EVIDENCE_FIELDS:
                raise TerminalRepairIntentError(
                    "terminal repair evidence fields differ"
                )
            result = cls.from_evidence(
                database=evidence["database"],
                quarantine=_quarantine(evidence["quarantine"]),
                lease=RevisionLease(
                    **{key: _uncell(value) for key, value in evidence["lease"].items()}
                ),
                reservation=_row(_SOURCE, evidence["reservation"]),
                publication_document=evidence["publication_document"],
                repaired_at=_uncell(evidence["repaired_at"]),
            )
            if result.encode() != raw:
                raise TerminalRepairIntentError(
                    "terminal repair derived rows/binding differ from evidence"
                )
            return result
        except TerminalRepairIntentError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
            RecursionError,
            OverflowError,
            PublicationJournalError,
            PropertyCatalogPublishError,
        ) as exc:
            raise TerminalRepairIntentError(
                f"invalid terminal repair document: {exc}"
            ) from exc
