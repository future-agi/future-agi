"""Durable activation proof for bounded Go hot-producer state retirement."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .activation import CatalogLifecycleMode, ManifestStreamRole, RevisionBuildPlan
from .codec import canonical_uuid, framed_sha256, require_sha256
from .durable_lifecycle import PriorActiveEvidence
from .models import SourceAdapter
from .runtime_limits import RUNTIME_LIMITS

PRODUCER_RETIREMENT_FORMAT = "futureagi.property-catalog-producer-state-retirements"
PRODUCER_RETIREMENT_VERSION = 1
PRODUCER_RETIREMENT_FILE_NAME = "producer-state-retirements-v1.json"
PRODUCER_RETIREMENT_SHA_DOMAIN = (
    "futureagi.property-catalog.producer-state-retirement.v1"
)
MAX_PRODUCER_RETIREMENTS = 256
MAX_PRODUCER_RETIREMENT_BYTES = RUNTIME_LIMITS.producer_retirement_max_bytes
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
_ZERO_SHA256 = "0" * 64
_RECORD_FIELDS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "lifecycle_mode",
    "build_plan_json",
    "build_lease_sha256",
    "source_manifest_sha256",
    "activation_sequence",
    "activation_sha256",
    "lineage_anchor_revision",
    "lineage_anchor_build_token",
    "lineage_anchor_activation_sequence",
    "lineage_anchor_activation_sha256",
    "active_revisions_since_anchor",
    "hot_producer_stream_id",
    "emitted_at",
    "retirement_sha256",
)


class ProducerRetirementError(RuntimeError):
    """Activation retirement evidence is missing, ambiguous, or unsafe."""


def _positive_uint(value: object, bits: int, field: str) -> int:
    if type(value) is not int or not 1 <= value < (1 << bits):
        raise ProducerRetirementError(f"{field} must be a positive UInt{bits}")
    return value


def _time_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ProducerRetirementError("emitted_at must be timezone-aware")
    normalized = value.astimezone(UTC)
    if value.utcoffset() != UTC.utcoffset(value):
        raise ProducerRetirementError("emitted_at must use UTC")
    return normalized.strftime(_TIME_FORMAT)


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ProducerRetirementError("emitted_at must be canonical text")
    try:
        parsed = datetime.strptime(value, _TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise ProducerRetirementError("emitted_at must be canonical UTC") from exc
    if _time_text(parsed) != value:
        raise ProducerRetirementError("emitted_at must be canonical UTC")
    return parsed


@dataclass(frozen=True, slots=True)
class ProducerStateRetirement:
    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    lifecycle_mode: CatalogLifecycleMode
    build_plan_json: str
    build_lease_sha256: str
    source_manifest_sha256: str
    activation_sequence: int
    activation_sha256: str
    lineage_anchor_revision: int
    lineage_anchor_build_token: str
    lineage_anchor_activation_sequence: int
    lineage_anchor_activation_sha256: str
    active_revisions_since_anchor: int
    hot_producer_stream_id: str
    emitted_at: datetime
    retirement_sha256: str = _ZERO_SHA256

    def __post_init__(self) -> None:
        for field in (
            "organization_id",
            "workspace_id",
            "build_token",
            "lineage_anchor_build_token",
            "hot_producer_stream_id",
        ):
            object.__setattr__(
                self,
                field,
                canonical_uuid(getattr(self, field), field=field),
            )
        _positive_uint(self.catalog_epoch, 16, "catalog_epoch")
        _positive_uint(self.catalog_revision, 64, "catalog_revision")
        _positive_uint(self.projection_version, 16, "projection_version")
        _positive_uint(self.activation_sequence, 64, "activation_sequence")
        _positive_uint(
            self.lineage_anchor_revision,
            64,
            "lineage_anchor_revision",
        )
        _positive_uint(
            self.lineage_anchor_activation_sequence,
            64,
            "lineage_anchor_activation_sequence",
        )
        if type(self.active_revisions_since_anchor) is not int or not (
            0 <= self.active_revisions_since_anchor <= 2_048
        ):
            raise ProducerRetirementError(
                "active_revisions_since_anchor exceeds its lifecycle bound"
            )
        if not isinstance(self.lifecycle_mode, CatalogLifecycleMode):
            raise ProducerRetirementError("lifecycle_mode is invalid")
        for field in (
            "build_lease_sha256",
            "source_manifest_sha256",
            "activation_sha256",
            "lineage_anchor_activation_sha256",
        ):
            require_sha256(getattr(self, field), field=field)
        _time_text(self.emitted_at)
        try:
            plan = RevisionBuildPlan.from_json(self.build_plan_json)
        except (TypeError, ValueError) as exc:
            raise ProducerRetirementError(
                "retirement build_plan_json is not canonical v2"
            ) from exc
        if (
            plan.organization_id != self.organization_id
            or plan.workspace_id != self.workspace_id
            or plan.catalog_epoch != self.catalog_epoch
            or plan.catalog_revision != self.catalog_revision
            or plan.build_token != self.build_token
            or plan.projection_version != self.projection_version
            or plan.sha256 != self.build_lease_sha256
        ):
            raise ProducerRetirementError(
                "retirement identity differs from its canonical build plan"
            )
        hot = tuple(
            stream
            for stream in plan.streams
            if stream.source_adapter is SourceAdapter.SPAN_ATTRIBUTE
            and stream.role is ManifestStreamRole.HOT_VALUES
        )
        if len(hot) != 1 or hot[0].producer_stream_id != self.hot_producer_stream_id:
            raise ProducerRetirementError(
                "retirement hot stream differs from the active build plan"
            )
        if (
            self.activation_sequence - self.lineage_anchor_activation_sequence
            != self.active_revisions_since_anchor
        ):
            raise ProducerRetirementError(
                "retirement activation depth differs from its lineage anchor"
            )
        snapshot_mode = self.lifecycle_mode in {
            CatalogLifecycleMode.INITIAL_BACKFILL,
            CatalogLifecycleMode.FULL_REPAIR,
        }
        if snapshot_mode and (
            self.lineage_anchor_revision != self.catalog_revision
            or self.lineage_anchor_build_token != self.build_token
            or self.lineage_anchor_activation_sequence != self.activation_sequence
            or self.lineage_anchor_activation_sha256 != self.activation_sha256
            or self.active_revisions_since_anchor != 0
        ):
            raise ProducerRetirementError(
                "snapshot retirement is not its own lineage anchor"
            )
        if not snapshot_mode and (
            self.lineage_anchor_revision >= self.catalog_revision
            or self.lineage_anchor_activation_sequence >= self.activation_sequence
        ):
            raise ProducerRetirementError(
                "incremental retirement has no earlier lineage anchor"
            )
        expected = producer_retirement_sha256(self)
        if self.retirement_sha256 == _ZERO_SHA256:
            object.__setattr__(self, "retirement_sha256", expected)
        elif self.retirement_sha256 != expected:
            raise ProducerRetirementError("retirement digest does not match its fields")

    @classmethod
    def from_active(
        cls,
        active: PriorActiveEvidence,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
        emitted_at: datetime,
    ) -> ProducerStateRetirement:
        if not isinstance(active, PriorActiveEvidence):
            raise TypeError("active must be PriorActiveEvidence")
        plan = active.build_plan
        if (
            plan.organization_id
            != canonical_uuid(
                organization_id,
                field="organization_id",
            )
            or plan.workspace_id != canonical_uuid(workspace_id, field="workspace_id")
            or plan.catalog_epoch != catalog_epoch
        ):
            raise ProducerRetirementError(
                "active evidence differs from the requested retirement scope"
            )
        hot = active.stream(
            SourceAdapter.SPAN_ATTRIBUTE,
            ManifestStreamRole.HOT_VALUES,
        )
        anchor = active.lineage_anchor
        return cls(
            organization_id=plan.organization_id,
            workspace_id=plan.workspace_id,
            catalog_epoch=plan.catalog_epoch,
            catalog_revision=active.catalog_revision,
            build_token=active.build_token,
            projection_version=active.projection_version,
            lifecycle_mode=active.lifecycle_mode,
            build_plan_json=plan.canonical_json,
            build_lease_sha256=plan.sha256,
            source_manifest_sha256=active.source_manifest_sha256,
            activation_sequence=active.activation_sequence,
            activation_sha256=active.activation_sha256,
            lineage_anchor_revision=anchor.catalog_revision,
            lineage_anchor_build_token=anchor.build_token,
            lineage_anchor_activation_sequence=anchor.activation_sequence,
            lineage_anchor_activation_sha256=anchor.activation_sha256,
            active_revisions_since_anchor=anchor.active_revisions_since,
            hot_producer_stream_id=hot.producer_stream_id,
            emitted_at=emitted_at,
        )

    @property
    def tenant_key(self) -> tuple[str, str]:
        return self.organization_id, self.workspace_id

    @property
    def document(self) -> Mapping[str, Any]:
        return {
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "catalog_epoch": self.catalog_epoch,
            "catalog_revision": self.catalog_revision,
            "build_token": self.build_token,
            "projection_version": self.projection_version,
            "lifecycle_mode": self.lifecycle_mode.value,
            "build_plan_json": self.build_plan_json,
            "build_lease_sha256": self.build_lease_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "activation_sequence": self.activation_sequence,
            "activation_sha256": self.activation_sha256,
            "lineage_anchor_revision": self.lineage_anchor_revision,
            "lineage_anchor_build_token": self.lineage_anchor_build_token,
            "lineage_anchor_activation_sequence": (
                self.lineage_anchor_activation_sequence
            ),
            "lineage_anchor_activation_sha256": (self.lineage_anchor_activation_sha256),
            "active_revisions_since_anchor": self.active_revisions_since_anchor,
            "hot_producer_stream_id": self.hot_producer_stream_id,
            "emitted_at": _time_text(self.emitted_at),
            "retirement_sha256": self.retirement_sha256,
        }


def producer_retirement_sha256(value: ProducerStateRetirement) -> str:
    return framed_sha256(
        PRODUCER_RETIREMENT_SHA_DOMAIN,
        value.organization_id,
        value.workspace_id,
        value.catalog_epoch,
        value.catalog_revision,
        value.build_token,
        value.projection_version,
        value.lifecycle_mode.value,
        value.build_plan_json,
        value.build_lease_sha256,
        value.source_manifest_sha256,
        value.activation_sequence,
        value.activation_sha256,
        value.lineage_anchor_revision,
        value.lineage_anchor_build_token,
        value.lineage_anchor_activation_sequence,
        value.lineage_anchor_activation_sha256,
        value.active_revisions_since_anchor,
        value.hot_producer_stream_id,
        _time_text(value.emitted_at),
    )


def encode_producer_retirements(
    values: Sequence[ProducerStateRetirement],
) -> bytes:
    ordered = tuple(sorted(values, key=lambda value: value.tenant_key))
    if not 1 <= len(ordered) <= MAX_PRODUCER_RETIREMENTS or len(
        {value.tenant_key for value in ordered}
    ) != len(ordered):
        raise ProducerRetirementError(
            "retirement document requires unique bounded tenant records"
        )
    document = {
        "format": PRODUCER_RETIREMENT_FORMAT,
        "version": PRODUCER_RETIREMENT_VERSION,
        "retirements": [value.document for value in ordered],
    }
    raw = (
        json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    if len(raw) > MAX_PRODUCER_RETIREMENT_BYTES:
        raise ProducerRetirementError("retirement document exceeds its byte limit")
    return raw


def decode_producer_retirements(raw: bytes) -> tuple[ProducerStateRetirement, ...]:
    if (
        not isinstance(raw, bytes)
        or len(raw) < 2
        or len(raw) > MAX_PRODUCER_RETIREMENT_BYTES
        or not raw.endswith(b"\n")
        or b"\n" in raw[:-1]
    ):
        raise ProducerRetirementError(
            "retirement document is not one bounded canonical JSON line"
        )
    try:
        decoded = json.loads(raw[:-1])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProducerRetirementError("retirement document is invalid JSON") from exc
    if not isinstance(decoded, dict) or tuple(decoded) != (
        "format",
        "version",
        "retirements",
    ):
        raise ProducerRetirementError("retirement document fields are invalid")
    records = decoded["retirements"]
    if (
        decoded["format"] != PRODUCER_RETIREMENT_FORMAT
        or decoded["version"] != PRODUCER_RETIREMENT_VERSION
        or not isinstance(records, list)
        or not 1 <= len(records) <= MAX_PRODUCER_RETIREMENTS
    ):
        raise ProducerRetirementError("retirement document format/count is invalid")
    values: list[ProducerStateRetirement] = []
    for index, raw_record in enumerate(records):
        if not isinstance(raw_record, dict) or tuple(raw_record) != _RECORD_FIELDS:
            raise ProducerRetirementError(
                f"retirement record {index} fields/order are invalid"
            )
        try:
            values.append(
                ProducerStateRetirement(
                    organization_id=raw_record["organization_id"],
                    workspace_id=raw_record["workspace_id"],
                    catalog_epoch=raw_record["catalog_epoch"],
                    catalog_revision=raw_record["catalog_revision"],
                    build_token=raw_record["build_token"],
                    projection_version=raw_record["projection_version"],
                    lifecycle_mode=CatalogLifecycleMode(raw_record["lifecycle_mode"]),
                    build_plan_json=raw_record["build_plan_json"],
                    build_lease_sha256=raw_record["build_lease_sha256"],
                    source_manifest_sha256=raw_record["source_manifest_sha256"],
                    activation_sequence=raw_record["activation_sequence"],
                    activation_sha256=raw_record["activation_sha256"],
                    lineage_anchor_revision=raw_record["lineage_anchor_revision"],
                    lineage_anchor_build_token=raw_record["lineage_anchor_build_token"],
                    lineage_anchor_activation_sequence=raw_record[
                        "lineage_anchor_activation_sequence"
                    ],
                    lineage_anchor_activation_sha256=raw_record[
                        "lineage_anchor_activation_sha256"
                    ],
                    active_revisions_since_anchor=raw_record[
                        "active_revisions_since_anchor"
                    ],
                    hot_producer_stream_id=raw_record["hot_producer_stream_id"],
                    emitted_at=_parse_time(raw_record["emitted_at"]),
                    retirement_sha256=raw_record["retirement_sha256"],
                )
            )
        except (TypeError, ValueError) as exc:
            raise ProducerRetirementError(
                f"retirement record {index} is invalid: {exc}"
            ) from exc
    result = tuple(values)
    if tuple(value.tenant_key for value in result) != tuple(
        sorted({value.tenant_key for value in result})
    ):
        raise ProducerRetirementError(
            "retirement records must be unique and tenant-sorted"
        )
    if encode_producer_retirements(result) != raw:
        raise ProducerRetirementError("retirement document is not canonical JSON")
    return result


class AtomicProducerStateRetirementFile:
    """Publish one monotonic active high-water per tenant on the shared volume."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if not self._path.is_absolute():
            raise ValueError("producer retirement path must be absolute")
        try:
            parent = os.lstat(self._path.parent)
        except OSError as exc:
            raise ValueError("producer retirement parent is unavailable") from exc
        if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode):
            raise ValueError("producer retirement parent must be a physical directory")

    @property
    def path(self) -> Path:
        return self._path

    def publish(self, value: ProducerStateRetirement) -> None:
        if not isinstance(value, ProducerStateRetirement):
            raise TypeError("value must be ProducerStateRetirement")
        current = {record.tenant_key: record for record in self._read()}
        previous = current.get(value.tenant_key)
        if previous is not None:
            previous_order = (
                previous.catalog_epoch,
                previous.activation_sequence,
                previous.catalog_revision,
            )
            value_order = (
                value.catalog_epoch,
                value.activation_sequence,
                value.catalog_revision,
            )
            if value_order < previous_order:
                raise ProducerRetirementError("retirement high-water regressed")
            if value_order == previous_order:
                if _activation_identity(value) != _activation_identity(previous):
                    raise ProducerRetirementError(
                        "retirement conflicts at one activation high-water"
                    )
                return
            if value.catalog_epoch == previous.catalog_epoch and (
                value.activation_sequence <= previous.activation_sequence
                or value.catalog_revision <= previous.catalog_revision
            ):
                raise ProducerRetirementError(
                    "retirement revision/activation sequence is not monotonic"
                )
        current[value.tenant_key] = value
        self._write(tuple(current.values()))

    def _read(self) -> tuple[ProducerStateRetirement, ...]:
        try:
            info = os.lstat(self._path)
        except FileNotFoundError:
            return ()
        except OSError as exc:
            raise ProducerRetirementError(
                "cannot inspect producer retirement file"
            ) from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_mode & 0o022
            or not 2 <= info.st_size <= MAX_PRODUCER_RETIREMENT_BYTES
        ):
            raise ProducerRetirementError(
                "producer retirement file type/mode/size is unsafe"
            )
        try:
            raw = self._path.read_bytes()
        except OSError as exc:
            raise ProducerRetirementError(
                "cannot read producer retirement file"
            ) from exc
        return decode_producer_retirements(raw)

    def _write(self, values: Sequence[ProducerStateRetirement]) -> None:
        raw = encode_producer_retirements(values)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".producer-retirement-",
            dir=self._path.parent,
        )
        temporary = Path(temporary_name)
        keep = True
        try:
            os.fchmod(descriptor, 0o600)
            written = os.write(descriptor, raw)
            if written != len(raw):
                raise ProducerRetirementError(
                    "producer retirement file was only partially written"
                )
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self._path)
            keep = False
            directory_fd = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if keep:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass


def _activation_identity(value: ProducerStateRetirement) -> ProducerStateRetirement:
    return replace(
        value,
        emitted_at=datetime(1970, 1, 1, tzinfo=UTC),
        retirement_sha256=_ZERO_SHA256,
    )


__all__ = [
    "AtomicProducerStateRetirementFile",
    "MAX_PRODUCER_RETIREMENT_BYTES",
    "MAX_PRODUCER_RETIREMENTS",
    "PRODUCER_RETIREMENT_FILE_NAME",
    "PRODUCER_RETIREMENT_FORMAT",
    "PRODUCER_RETIREMENT_SHA_DOMAIN",
    "PRODUCER_RETIREMENT_VERSION",
    "ProducerRetirementError",
    "ProducerStateRetirement",
    "decode_producer_retirements",
    "encode_producer_retirements",
    "producer_retirement_sha256",
]
