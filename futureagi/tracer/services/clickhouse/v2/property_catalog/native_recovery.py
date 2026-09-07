"""Workspace recovery over existing native journals; no separate notice protocol."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from .activation import ActivationRecord, CatalogLifecycleMode
from .coordinator import _row_lease
from .durable_lifecycle import WorkspaceCatalogScope
from .durable_native_writer import DurableNativeCatalogWriter, NativeWriteUnresolved
from .native_write_journal import (
    NATIVE_WRITE_ATTEMPT_DIRECTORY,
    NativeQuarantineReason,
    NativeTerminalRepairReceipt,
    NativeWriteJournal,
    _cell,
    native_parameters_sha256,
)
from .native_write_proof import _COLUMNS, NativeWriteProofError, coverage_plan
from .publication_journal import _key as publication_key
from .publication_journal import _marker
from .terminal_repair_execution import _FORMAT, NativeTerminalRepairExecutor
from .terminal_repair_intent import (
    FrozenTerminalRepairIntent,
    _validate_reservation,
)
from .write_admission import _canonical

_SOURCE = "property_catalog_source_streams"
_ACTIVE = "property_catalog_activations"
_CONTROL = "property_catalog_activation_control_events"
_WORKSPACE = ("organization_id", "workspace_id", "catalog_epoch", "projection_version")


def _validate_intent(writer, intent, scope):
    if (
        intent.database != writer.database
        or intent.binding.quarantine.admission_sha256 != writer.admission_sha256
        or intent.binding.quarantine.scope != scope
        or scope.catalog_epoch != writer.proof.identity.catalog_epoch
        or scope.projection_version != writer.proof.identity.projection_version
    ):
        raise NativeWriteUnresolved(
            "native recovery intent differs from installation/scope"
        )
    for write, row in zip(intent.binding.writes, intent.rows, strict=True):
        coverage_plan(writer.database, write.table, (row,), _COLUMNS[write.table])


def _archive_intent(saved, writer, scoped, reservation, lease):
    intent = FrozenTerminalRepairIntent.decode(_canonical(saved.get("intent")) + b"\n")
    _validate_intent(writer, intent, scoped.scope)
    if (
        set(saved)
        != {"format", "intent", "intent_sha256", "complete", "receipt_sha256s"}
        or saved["format"] != _FORMAT
        or saved["intent_sha256"] != intent.sha256
        or type(saved["complete"]) is not bool
        or (not saved["complete"] and saved["receipt_sha256s"] is not None)
        or intent.lease != lease
        or native_parameters_sha256(_COLUMNS[_SOURCE], (intent.reservation,))
        != native_parameters_sha256(_COLUMNS[_SOURCE], (reservation,))
    ):
        raise NativeWriteUnresolved(
            "native recovery archive differs from frozen reservation"
        )
    if saved["complete"]:
        if type(saved["receipt_sha256s"]) is not list:
            raise NativeWriteUnresolved("native recovery archive receipt is malformed")
        NativeTerminalRepairReceipt(intent.binding, tuple(saved["receipt_sha256s"]))
    # Recover the archive-before-quarantine crash using its original capture.
    # A new registration/changed intent fails this CAS; never rebuild the archive.
    scoped.quarantine(intent.binding.quarantine)
    return intent, not saved["complete"]


def _recover_build(writer, coordinator, journal, scoped, key, now, remaining):
    seed = scoped.reservation_seed()
    lease = reservation = seed_token = None
    if seed is not None:
        reservation, seed_token = seed
        lease = _row_lease(reservation)
        _validate_reservation(reservation, lease, scoped.scope)
        archive_key = f"{key}:terminal-repair:{lease.build_lease_sha256}"
        saved = coordinator._recovery_journal.load_record(archive_key)
        if saved is not None:
            return _archive_intent(saved, writer, scoped, reservation, lease)
    if scoped.quarantine_binding is not None:
        raise NativeWriteUnresolved(
            "quarantined build lacks its frozen terminal execution"
        )

    closure = scoped._read_index()["closure"]
    closure_unsent = False
    if closure is not None:
        closure = scoped._closure(closure)
        with scoped.session(_ACTIVE, closure.binding.deduplication_token) as session:
            attempt = session.load()
            if attempt is None:
                if closure.completed_generation is not None:
                    raise NativeWriteUnresolved(
                        "native closure ACTIVE receipt is missing"
                    )
                closure_unsent = True
            else:
                scoped._validate_active(attempt, closure.binding)
                if closure.completed_generation is not None and (
                    attempt.state != "complete"
                    or attempt["record_sha256"] != closure.attempt_record_sha256
                ):
                    raise NativeWriteUnresolved("native closure ACTIVE receipt changed")
                writer._retain_complete(session, attempt)
    pending = scoped.pending()
    prepared = closure_unsent
    initial_prepared = control = False
    unresolved = []
    writer.proof.attest(timeout_ms=remaining())
    for reference in pending:
        control |= reference.table == _CONTROL
        with scoped.session(reference.table, reference.deduplication_token) as session:
            attempt = session.load()
            if attempt is None or (
                attempt.query_id != reference.query_id
                or attempt.parameters_sha256 != reference.parameters_sha256
            ):
                raise NativeWriteUnresolved("native recovery pending receipt changed")
            writer._validate_attempt(attempt)
            if attempt.state == "prepared":
                prepared = True
                initial_prepared |= (
                    reference.table == _SOURCE
                    and reference.deduplication_token == seed_token
                )
                continue
            try:
                writer._settle_and_cover(session, attempt, remaining=remaining)
            except (
                NativeWriteUnresolved,
                NativeWriteProofError,
                TimeoutError,
                ConnectionError,
            ) as exc:
                # Finish the bounded positive-proof pass, including entries after
                # an unresolved Sent/Prepared. Local journal errors are fatal.
                unresolved.append(exc)

    expired = lease is not None and now >= lease.expires_at
    if control:
        if unresolved or prepared:
            raise NativeWriteUnresolved(
                "reader-control recovery cannot authorize build termination"
            )
        journal.clear_recovery_scope(scoped, expected_generation=scoped.generation)
        return None
    if not unresolved and not prepared:
        journal.clear_recovery_scope(scoped, expected_generation=scoped.generation)
        return None
    if seed is None:
        raise NativeWriteUnresolved(
            "unresolved native build lacks its original reservation"
        )
    if not expired:
        if initial_prepared:
            raise NativeWriteUnresolved(
                "Prepared initial reservation requires its original live lifecycle; no new allocation"
            )
        if unresolved:
            raise NativeWriteUnresolved(
                "unexpired native build remains unresolved; no new allocation"
            )
        # Only ordinary Prepared rows remain. Their original live lifecycle may
        # replay them; neither expiry nor abandonment has been established.
        return None

    # Reattest after failed positive proofs; an invalid installation or exhausted
    # budget must not be converted into authority for terminal writes.
    writer.proof.attest(timeout_ms=remaining())
    storage = coordinator._recovery_journal
    document = storage.load_record(publication_key(key, lease.build_lease_sha256))
    marker = storage.load_record(key + ":activation")
    _marker(marker)
    if marker is not None and (
        (marker["catalog_revision"] >= lease.catalog_revision and document is None)
        or (
            marker["catalog_revision"] == lease.catalog_revision
            and marker
            != {
                "catalog_revision": lease.catalog_revision,
                "build_token": lease.build_token,
                "build_lease_sha256": lease.build_lease_sha256,
            }
        )
    ):
        raise NativeWriteUnresolved(
            "fresh native recovery lacks exact publication/marker evidence"
        )
    quarantine = scoped.capture_quarantine(
        NativeQuarantineReason.UNRESOLVED_NATIVE_WRITE
        if unresolved
        else NativeQuarantineReason.BUILD_SUPERSEDED
    )
    intent = FrozenTerminalRepairIntent.from_evidence(
        database=writer.database,
        quarantine=quarantine,
        lease=lease,
        reservation=reservation,
        publication_document=document,
        repaired_at=now,
    )
    if closure is not None and (
        intent.binding.build_lease_sha256 != closure.binding.build_lease_sha256
        or intent.binding.publication_sha256 != closure.binding.publication_sha256
    ):
        raise NativeWriteUnresolved(
            "native closure differs from frozen publication evidence"
        )
    _validate_intent(writer, intent, scoped.scope)
    remaining()
    # Same archive/format owned by the executor, not a second recovery record.
    # This precedes quarantine so repaired_at and every row survive interruption.
    storage.save_record(
        archive_key,
        {
            "format": _FORMAT,
            "intent": json.loads(intent.encode()),
            "intent_sha256": intent.sha256,
            "complete": False,
            "receipt_sha256s": None,
        },
    )
    scoped.quarantine(quarantine)
    return intent, True


def recover_workspace(
    writer: DurableNativeCatalogWriter,
    coordinator,
    scope: WorkspaceCatalogScope,
    now: datetime,
    timeout_ms: int = 30_000,
) -> tuple[tuple[FrozenTerminalRepairIntent, NativeTerminalRepairReceipt], ...]:
    """Before reader selection, settle or terminally retire indexed exact builds."""
    executor = NativeTerminalRepairExecutor(writer, coordinator)
    if type(scope) is not WorkspaceCatalogScope or type(now) is not datetime:
        raise TypeError("native recovery requires workspace scope and UTC datetime")
    _cell(now)
    writer._validate_driver()
    if (scope.catalog_epoch, scope.projection_version) != (
        writer.proof.identity.catalog_epoch,
        writer.proof.identity.projection_version,
    ):
        raise NativeWriteUnresolved(
            "native recovery workspace differs from installation"
        )
    remaining = writer.proof._budget(timeout_ms)
    if not (writer.directory / NATIVE_WRITE_ATTEMPT_DIRECTORY).exists():
        return ()
    key = coordinator._revision_key(
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        catalog_epoch=scope.catalog_epoch,
    )

    def recover():
        result = []
        with writer._journal() as journal:
            candidates = journal.recovery_scopes(
                **{field: getattr(scope, field) for field in _WORKSPACE}
            )
            for native_scope in candidates:
                with journal.scope(native_scope) as scoped:
                    action = _recover_build(
                        writer, coordinator, journal, scoped, key, now, remaining
                    )
                if action is not None:
                    intent, dispatch = action
                    # Keep workspace ownership, but release BUILD/table locks
                    # before the executor opens the same exact BUILD again.
                    receipt = executor._serialized(
                        intent, key, remaining, allow_dispatch=dispatch
                    )
                    result.append((intent, receipt))
        remaining()
        return tuple(result)

    return coordinator._serializer.serialize(key, recover)


def acknowledge_replacement(writer, repairs, record, since, until) -> None:
    """Prune old terminal pointers inside the caller's proven new-build completion.

    No database calls, original settlement, new-build lock reentry, or receipt
    deletion. The caller owns NativePublicationBarrier.completion(record).
    """
    if (
        type(writer) is not DurableNativeCatalogWriter
        or type(record) is not ActivationRecord
    ):
        raise TypeError("native replacement requires its real writer and ACTIVE record")
    if (
        type(repairs) is not tuple
        or len(repairs) > NativeWriteJournal.MAX_RECOVERY_SCOPES
    ):
        raise ValueError("native replacement requires bounded exact repair pairs")
    for value in (since, until):
        if type(value) is not datetime:
            raise TypeError("native replacement bounds require UTC datetimes")
        _cell(value)
    if since >= until or record.lifecycle_mode not in {
        CatalogLifecycleMode.INITIAL_BACKFILL,
        CatalogLifecycleMode.FULL_REPAIR,
    }:
        raise NativeWriteUnresolved(
            "native replacement requires an INITIAL/FULL_REPAIR window"
        )
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    lower, upper = (
        (value - epoch) // timedelta(microseconds=1) for value in (since, until)
    )
    for intent, receipt in repairs:
        if (
            type(intent) is not FrozenTerminalRepairIntent
            or type(receipt) is not NativeTerminalRepairReceipt
        ):
            raise TypeError("native replacement requires frozen intent/receipt pairs")
        intent = FrozenTerminalRepairIntent.decode(intent.encode())
        old = intent.binding.quarantine.scope
        _validate_intent(writer, intent, old)
        window = intent.lease.build_plan.source_scope
        if (
            receipt.binding != intent.binding
            or any(
                getattr(record, field) != getattr(old, field) for field in _WORKSPACE
            )
            or record.catalog_revision <= old.catalog_revision
            or record.build_token == old.build_token
            or lower > window.span_since_us
            or upper < window.span_until_us
        ):
            raise NativeWriteUnresolved(
                "replacement does not cover the exact abandoned build"
            )
    with writer._journal() as journal:
        for intent, receipt in repairs:
            with journal.scope(intent.binding.quarantine.scope) as scoped:
                if scoped.quarantine_binding != intent.binding.quarantine:
                    raise NativeWriteUnresolved(
                        "replacement terminal quarantine changed"
                    )
                journal.clear_recovery_scope(
                    scoped,
                    expected_generation=scoped.generation,
                    terminal_receipt=receipt,
                )
