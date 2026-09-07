"""Bind an existing lifecycle reservation to one durable physical source.

There is no second revision protocol here. The existing lease/build token owns
the capture, and its existing source window/audit generation remain unchanged.
The runtime must call bind before opening streams or reading canonical values.
An older resumed build without capture evidence must be replaced, never silently
continued against a newly captured set of rows.
"""

from __future__ import annotations

import os
import stat
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import get_ident

from .activation import ActivationRecord, ActivationStatus, RevisionLease
from .codec import canonical_uuid
from .coordinator import FileSupersessionJournal
from .durable_lifecycle import PreparedLifecycleRevision, ReservationStatus
from .repair_files import locked_file
from .source_capture import DurableSourceCapture, SourceCaptureError, SourceCaptureSpec
from .source_capture_schema import qualified_capture_schema
from .source_parts import checked_parts

_FORMAT = "futureagi.property-catalog.lifecycle-source-binding.v1"


class SourceCaptureNeedsReplacement(SourceCaptureError):
    """The old reservation cannot resume against a different physical source."""


@dataclass(frozen=True)
class LifecycleCaptureBinding:
    spec: SourceCaptureSpec
    reader: object
    started_parts: tuple[tuple[str, str], ...]


class CaptureAwareCutoffFreezer:
    """Observe live parts before discovery, only when allocating a new plan.

    Existing lifecycle resume never calls its freezer, so FENCED completion
    remains source-independent. Failed discovery must not expose an old baseline.
    """

    def __init__(self, freezer, live_reader):
        self._freezer, self._reader = freezer, live_reader
        self.started_parts = None

    def __call__(self, **kwargs):
        self.started_parts = None
        parts = (
            checked_parts(self._reader.parts_snapshot())
            if kwargs["scope"].project_ids
            else ()
        )
        frozen = self._freezer(**kwargs)
        self.started_parts = parts
        return frozen


class LifecycleSourceCapture:
    """Factory-owned bridge, using the controller's existing durable directory.

    metadata_loader returns one exact source server/table/CREATE observation.
    backend_factory supplies the admitted native capability and storage budget.
    reader_factory supplies a server-readonly scanner bound to that exact table.
    These are internal capabilities, not request parameters or settings.
    """

    def __init__(
        self,
        *,
        directory,
        installation_id,
        source_database,
        catalog_database,
        metadata_loader,
        live_reader,
        backend_factory,
        reader_factory,
        can_retire,
        cutoff_freezer=None,
    ):
        if canonical_uuid(installation_id, field="installation_id") != installation_id:
            raise ValueError("capture installation identity is not canonical")
        self._root = Path(directory)
        if (
            not self._root.is_absolute()
            or self._root.is_symlink()
            or not self._root.is_dir()
        ):
            raise ValueError("capture binding requires the private control directory")
        info = self._root.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError(
                "capture binding directory ownership or permissions changed"
            )
        self._journal = FileSupersessionJournal(directory)
        self._installation = installation_id
        self._source, self._catalog = source_database, catalog_database
        self._metadata, self._live_reader = metadata_loader, live_reader
        self._backend_factory, self._reader_factory = backend_factory, reader_factory
        self._can_retire = can_retire
        self.cutoff_freezer = cutoff_freezer
        if any(
            not callable(value)
            for value in (metadata_loader, backend_factory, reader_factory, can_retire)
        ):
            raise TypeError("capture binding requires factory-owned capabilities")

    def _identity(self, prepared):
        if type(prepared) is not PreparedLifecycleRevision:
            raise TypeError("capture must bind a prepared lifecycle revision")
        prepared.__post_init__()
        return self._lease_identity(prepared.lease)

    def _lease_identity(self, lease):
        if type(lease) is not RevisionLease:
            raise TypeError("capture retirement requires its exact frozen lease")
        lease.__post_init__()
        return {
            "installation_id": self._installation,
            "organization_id": lease.organization_id,
            "workspace_id": lease.workspace_id,
            "build_token": lease.build_token,
            "build_lease_sha256": lease.build_lease_sha256,
            "catalog_epoch": lease.catalog_epoch,
            "project_ids": list(lease.build_plan.source_scope.project_ids),
            "source_database": self._source,
            "catalog_database": self._catalog,
        }

    def _key(self, identity):
        return ":".join(
            (
                _FORMAT,
                *(
                    identity[k]
                    for k in (
                        "installation_id",
                        "organization_id",
                        "workspace_id",
                        "build_token",
                    )
                ),
            )
        )

    def _load(self, identity):
        record = self._journal.load_record(self._key(identity))
        if record is None:
            return None
        if (
            set(record)
            != {"format", "identity", "spec", "source_create", "started_parts"}
            or record["format"] != _FORMAT
            or (
                record["identity"] != identity
                # Existing captured builds remain resumable; without a stored
                # epoch they are not eligible for the new bounded cleanup.
                and record["identity"]
                != {k: v for k, v in identity.items() if k != "catalog_epoch"}
            )
        ):
            raise SourceCaptureNeedsReplacement(
                "capture binding differs from its lease"
            )
        spec = SourceCaptureSpec(**record["spec"])
        if any(
            getattr(spec, k) != identity[k]
            for k in (
                "installation_id",
                "organization_id",
                "workspace_id",
                "build_token",
                "source_database",
                "catalog_database",
            )
        ):
            raise SourceCaptureNeedsReplacement(
                "capture spec changed its reserved scope"
            )
        schema = qualified_capture_schema(
            record["source_create"],
            source_database=spec.source_database,
            source_table="spans",
            target_database=spec.capture_database,
            target_table=spec.capture_table,
            target_uuid=spec.capture_table_uuid,
        )
        if (schema.source_sha256, schema.capture_sha256) != (
            spec.source_schema_sha256,
            spec.capture_schema_sha256,
        ):
            raise SourceCaptureNeedsReplacement("persisted capture schema changed")
        parts = checked_parts(record["started_parts"])
        return spec, schema, parts

    def bind(self, prepared, *, started_parts=None, coordinator=None):
        identity = self._identity(prepared)
        if prepared.reservation_status is ReservationStatus.FENCED:
            raise SourceCaptureError("fenced completion must not reopen source capture")
        with locked_file(self._root / ("capture-binding-" + identity["build_token"])):
            loaded = self._load(identity)
            if loaded is None:
                if prepared.resumed:
                    raise SourceCaptureNeedsReplacement(
                        "resumed reservation has no original captured source"
                    )
                metadata = self._metadata()
                if not isinstance(metadata, dict) or set(metadata) != {
                    "server_uuid",
                    "table_uuid",
                    "create_table_query",
                }:
                    raise SourceCaptureError("source capture metadata is incomplete")
                spec = SourceCaptureSpec(
                    **{
                        k: identity[k]
                        for k in (
                            "installation_id",
                            "organization_id",
                            "workspace_id",
                            "build_token",
                            "source_database",
                            "catalog_database",
                        )
                    },
                    source_server_uuid=metadata["server_uuid"],
                    source_table_uuid=metadata["table_uuid"],
                    source_schema_sha256="0" * 64,
                    capture_schema_sha256="0" * 64,
                )
                schema = qualified_capture_schema(
                    metadata["create_table_query"],
                    source_database=spec.source_database,
                    source_table="spans",
                    target_database=spec.capture_database,
                    target_table=spec.capture_table,
                    target_uuid=spec.capture_table_uuid,
                )
                spec = replace(
                    spec,
                    source_schema_sha256=schema.source_sha256,
                    capture_schema_sha256=schema.capture_sha256,
                )
                # Persist the LIVE pre-capture inventory. ATTACH may rename parts;
                # its destination inventory must never acknowledge live arrivals.
                if started_parts is None and self.cutoff_freezer is not None:
                    started_parts = self.cutoff_freezer.started_parts
                if started_parts is None:
                    raise SourceCaptureError(
                        "new capture requires the pre-discovery live baseline"
                    )
                parts = checked_parts(started_parts)
                self._journal.save_record(
                    self._key(identity),
                    {
                        "format": _FORMAT,
                        "identity": identity,
                        "spec": asdict(spec),
                        "source_create": metadata["create_table_query"],
                        "started_parts": [list(p) for p in parts],
                    },
                )
                loaded = spec, schema, parts
            spec, schema, parts = loaded
        # The immutable binding is durable. Release its lock before acquiring a
        # workspace lock; each manager independently serializes acquire/retire.
        if coordinator is not None:
            self.retire_failed(coordinator=coordinator, spec=spec)
        self._manager(spec, schema).acquire(spec)
        reader = self._reader_factory(spec)
        return LifecycleCaptureBinding(spec, reader, parts)

    def retire_failed(self, *, coordinator, spec):
        """Reclaim bounded exact captures with durable invalidation/revocation.

        Call before a capture attempt, without holding workspace/native/capture
        locks. Uncertain CREATE reservations are deliberately left untouched:
        their four-entry budget has no automatic release, even for an absent
        table. Exhaustion requires investigation of the original CREATEs; never
        clear their index/tombstones based on elapsed time or table absence.
        """
        from .coordinator import ClickHouseRevisionCoordinator

        if not isinstance(coordinator, ClickHouseRevisionCoordinator):
            raise TypeError("failed capture cleanup requires its real coordinator")
        if type(spec) is not SourceCaptureSpec or (
            spec.installation_id != self._installation
            or spec.source_database != self._source
            or spec.catalog_database != self._catalog
        ):
            raise SourceCaptureError("capture cleanup namespace changed")
        if (
            coordinator._database != self._catalog
            or coordinator._recovery_journal is None
            or coordinator._recovery_journal._directory != self._root
        ):
            raise SourceCaptureError("capture cleanup coordinator binding changed")
        # reservation_snapshot reads only the existing journal/index. In
        # particular it must not construct/admit a new source-member backend.
        inventory = DurableSourceCapture(
            str(self._root), None, can_retire=lambda _: False
        )
        entries = inventory.reservation_snapshot(spec)
        retired = []
        for entry in entries:
            if entry.phase not in {
                "create_prepared",
                "created",
                "attaching",
                "captured",
                "retiring",
            }:
                continue
            old = entry.spec
            record = self._journal.load_record(self._key(asdict(old)))
            if record is None:
                continue  # Unknown authority retains its slot, not a new lease failure.
            if not isinstance(record.get("identity"), dict):
                raise SourceCaptureNeedsReplacement("capture cleanup identity changed")
            identity = record["identity"]
            fields = {
                "installation_id",
                "organization_id",
                "workspace_id",
                "build_token",
                "source_database",
                "catalog_database",
                "build_lease_sha256",
                "project_ids",
            }
            if set(identity) not in (fields, fields | {"catalog_epoch"}):
                raise SourceCaptureNeedsReplacement("capture cleanup identity changed")
            if record.get("spec") != asdict(old) or any(
                identity.get(field) != getattr(old, field)
                for field in fields - {"build_lease_sha256", "project_ids"}
            ):
                raise SourceCaptureNeedsReplacement("capture cleanup spec changed")
            if "catalog_epoch" not in identity:
                continue  # Never guess an old epoch from the current head/config.
            finished = False

            def cleanup(old=old, identity=identity):
                nonlocal finished
                loaded = self._load(identity)
                if loaded is None or loaded[0] != old:
                    raise SourceCaptureNeedsReplacement(
                        "capture cleanup binding changed"
                    )
                old_schema = loaded[1]
                owner, opened = get_ident(), True

                def authorized(candidate):
                    return (
                        opened
                        and get_ident() == owner
                        and type(candidate) is SourceCaptureSpec
                        and candidate == old
                    )

                try:
                    manager = self._manager(old, old_schema, can_retire=authorized)
                    manager.retire(old)
                    # A concurrent pre-veto CREATE may have become uncertain
                    # since enumeration. Its permanent reservation is not freed.
                    finished = manager._load(old)["phase"] == "retired"
                finally:
                    opened = False

            coordinator.serialize_source_capture_retirement(
                database=old.catalog_database,
                organization_id=old.organization_id,
                workspace_id=old.workspace_id,
                catalog_epoch=identity["catalog_epoch"],
                build_token=old.build_token,
                build_lease_sha256=identity["build_lease_sha256"],
                operation=cleanup,
            )
            if finished:
                retired.append(old)
        return tuple(retired)

    def _manager(self, spec, schema, *, can_retire=None):
        return DurableSourceCapture(
            str(self._root),
            self._backend_factory(spec, schema),
            can_retire=self._can_retire if can_retire is None else can_retire,
        )

    def verify(self, prepared):
        """Recheck exact immutable contents before/after the final source audit."""
        loaded = self._load(self._identity(prepared))
        if loaded is None:
            raise SourceCaptureNeedsReplacement("capture binding is missing")
        spec, schema, _ = loaded
        return self._manager(spec, schema).acquire(spec)

    def retire(self, prepared, *, completion_proof=None):
        """Caller holds existing completion/revocation proof; backend rechecks owner."""
        loaded = self._load(self._identity(prepared))
        if loaded is None:
            return  # No durable binding means this bridge could not send any DDL.
        self._retire_loaded(loaded, completion_proof=completion_proof)

    def needs_completed_retirement(self, record):
        """Local exact-key preflight only; not permission to DROP or recapture."""
        if (
            type(record) is not ActivationRecord
            or record.status is not ActivationStatus.ACTIVE
        ):
            raise TypeError("capture completion requires an exact ACTIVE record")
        record.__post_init__()
        key = self._key(
            {
                "installation_id": self._installation,
                "organization_id": record.organization_id,
                "workspace_id": record.workspace_id,
                "build_token": record.build_token,
            }
        )
        binding = self._journal.load_record(key)
        if binding is None:
            return False
        identity = binding.get("identity")
        if not isinstance(identity, dict) or self._key(identity) != key:
            raise SourceCaptureNeedsReplacement("completed capture binding changed")
        loaded = self._load(identity)
        if loaded is None:
            raise SourceCaptureNeedsReplacement("completed capture binding disappeared")
        spec = loaded[0]
        if (
            spec.installation_id != self._installation
            or spec.source_database != self._source
            or spec.catalog_database != self._catalog
            or spec.organization_id != record.organization_id
            or spec.workspace_id != record.workspace_id
            or spec.build_token != record.build_token
            or (
                "catalog_epoch" in identity
                and identity["catalog_epoch"] != record.catalog_epoch
            )
        ):
            raise SourceCaptureNeedsReplacement("completed capture scope changed")
        inventory = DurableSourceCapture(
            str(self._root), None, can_retire=lambda _: False
        )
        state = inventory._load(spec)
        if state is None:
            raise SourceCaptureNeedsReplacement("completed capture lost its journal")
        if state["phase"] == "retired":
            return False
        if state["phase"] not in {"captured", "retiring"}:
            raise SourceCaptureNeedsReplacement("ACTIVE capture was never completed")
        if not any(
            entry.spec == spec for entry in inventory.reservation_snapshot(spec)
        ):
            raise SourceCaptureNeedsReplacement(
                "completed capture lost its reservation"
            )
        return True

    def retire_completed(self, lease, *, completion_proof):
        """Retire an old published capture, without rebuilding a lifecycle execution."""
        from .native_publication import NativeCompletionProof

        if type(completion_proof) is not NativeCompletionProof:
            raise TypeError("capture retirement requires held native completion")
        loaded = self._load(self._lease_identity(lease))
        if loaded is None:
            raise SourceCaptureNeedsReplacement("completed capture binding is missing")
        self._retire_loaded(loaded, completion_proof=completion_proof)

    def _retire_loaded(self, loaded, *, completion_proof):
        spec, schema, _ = loaded
        can_retire = None
        if completion_proof is not None:
            from .native_publication import NativeCompletionProof

            if type(completion_proof) is not NativeCompletionProof:
                raise TypeError("capture retirement requires held native completion")
            completion_proof.require_capture(spec)
            can_retire = completion_proof.require_capture
        self._manager(spec, schema, can_retire=can_retire).retire(spec)
