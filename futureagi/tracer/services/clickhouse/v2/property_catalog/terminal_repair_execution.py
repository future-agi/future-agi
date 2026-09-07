"""Serialize exact native terminal repair; never replace, delete, or select readers.

The workspace lock is held before the native BUILD lock. A frozen local intent
and lease revocation precede SQL. An independently proven disabled activation
precedes the failed reservation. Original uncertain attempts remain untouched.
"""

from __future__ import annotations

import json

from .coordinator import (
    ClickHouseRevisionCoordinator,
    FileSupersessionJournal,
    _journal_row_document,
)
from .durable_native_writer import DurableNativeCatalogWriter, NativeWriteUnresolved
from .native_write_journal import (
    NATIVE_WRITE_ATTEMPT_DIRECTORY,
    NativeTerminalRepairReceipt,
    native_insert_sql,
)
from .native_write_proof import _COLUMNS, coverage_plan
from .publication_journal import _key as publication_key
from .write_admission import _canonical

_FORMAT = "futureagi.property-catalog-terminal-repair-execution.v1"


class NativeTerminalRepairExecutor:
    """An internal coordinator operation, not an unscoped writer permission."""

    def __init__(self, writer, coordinator):
        if (
            type(writer) is not DurableNativeCatalogWriter
            or type(coordinator) is not ClickHouseRevisionCoordinator
            or type(coordinator._recovery_journal) is not FileSupersessionJournal
            or coordinator._database != writer.database
        ):
            raise TypeError("terminal repair requires its real writer and coordinator")
        self.writer, self.coordinator = writer, coordinator

    def apply(self, intent, *, timeout_ms=30_000):
        return self._run(intent, timeout_ms=timeout_ms, allow_dispatch=True)

    def confirm(self, intent, *, timeout_ms=30_000):
        """Reprove the exact terminal receipts; cannot initialize or dispatch SQL."""
        return self._run(intent, timeout_ms=timeout_ms, allow_dispatch=False)

    def confirm_marker_serialized(self, lease, marker, *, timeout_ms=30_000):
        """Called by the coordinator while its workspace lock is already held.

        A legacy marker or absent repair archive is not proof. This path never
        sends SQL or manufactures a repair intent from a visible activation.
        """
        from .activation import RevisionLease
        from .publication_journal import _marker
        from .terminal_repair_intent import FrozenTerminalRepairIntent

        if type(lease) is not RevisionLease:
            raise TypeError("terminal marker confirmation requires the current lease")
        _marker(marker)
        if marker is None:
            return None
        if marker["catalog_revision"] > lease.catalog_revision:
            raise NativeWriteUnresolved(
                "terminal marker is newer than the current lease"
            )
        key = self.coordinator._revision_key_for_lease(lease)
        saved = self.coordinator._recovery_journal.load_record(
            f"{key}:terminal-repair:{marker['build_lease_sha256']}"
        )
        if saved is None:
            return None
        intent = FrozenTerminalRepairIntent.decode(
            _canonical(saved.get("intent")) + b"\n"
        )
        old = intent.lease
        if (
            any(
                getattr(old, field) != getattr(lease, field)
                for field in (
                    "organization_id",
                    "workspace_id",
                    "catalog_epoch",
                    "projection_version",
                )
            )
            or marker
            != {
                "catalog_revision": old.catalog_revision,
                "build_token": old.build_token,
                "build_lease_sha256": old.build_lease_sha256,
            }
            or intent.database != self.writer.database
            or intent.binding.quarantine.admission_sha256
            != self.writer.admission_sha256
        ):
            raise NativeWriteUnresolved(
                "terminal marker differs from exact repair evidence"
            )
        self.writer._validate_driver()
        return self._serialized(
            intent, key, self.writer.proof._budget(timeout_ms), allow_dispatch=False
        )

    def _run(self, intent, *, timeout_ms, allow_dispatch):
        from .terminal_repair_intent import FrozenTerminalRepairIntent

        if type(intent) is not FrozenTerminalRepairIntent:
            raise TypeError("terminal repair requires frozen complete build evidence")
        # Decode again rather than trusting mutable caller-derived attributes.
        intent = FrozenTerminalRepairIntent.decode(intent.encode())
        binding = intent.binding
        if (
            intent.database != self.writer.database
            or binding.quarantine.admission_sha256 != self.writer.admission_sha256
            or binding.quarantine.scope.catalog_epoch
            != self.writer.proof.identity.catalog_epoch
            or binding.quarantine.scope.projection_version
            != self.writer.proof.identity.projection_version
        ):
            raise NativeWriteUnresolved("terminal repair installation identity differs")
        for write, row in zip(binding.writes, intent.rows, strict=True):
            coverage_plan(
                self.writer.database, write.table, (row,), _COLUMNS[write.table]
            )
        self.writer._validate_driver()
        remaining = self.writer.proof._budget(timeout_ms)
        key = self.coordinator._revision_key_for_lease(intent.lease)
        return self.coordinator._serializer.serialize(
            key,
            lambda: self._serialized(
                intent, key, remaining, allow_dispatch=allow_dispatch
            ),
        )

    def _serialized(self, intent, key, remaining, *, allow_dispatch):
        storage = self.coordinator._recovery_journal
        binding, lease = intent.binding, intent.lease
        execution_key = f"{key}:terminal-repair:{lease.build_lease_sha256}"
        saved = storage.load_record(execution_key)
        expected = {
            "format": _FORMAT,
            "intent": json.loads(intent.encode()),
            "intent_sha256": intent.sha256,
        }
        if saved is not None and (
            set(saved) != set(expected) | {"complete", "receipt_sha256s"}
            or any(
                _canonical(saved[field]) != _canonical(value)
                for field, value in expected.items()
            )
            or type(saved["complete"]) is not bool
            or (not saved["complete"] and saved["receipt_sha256s"] is not None)
        ):
            raise NativeWriteUnresolved("terminal recovery intent or receipt conflicts")
        if saved is not None and saved["complete"]:
            if type(saved["receipt_sha256s"]) is not list:
                raise NativeWriteUnresolved("terminal recovery receipt is malformed")
            NativeTerminalRepairReceipt(binding, tuple(saved["receipt_sha256s"]))
        if saved is None and not allow_dispatch:
            raise NativeWriteUnresolved("terminal repair has no frozen execution")
        current_publication = storage.load_record(
            publication_key(key, lease.build_lease_sha256)
        )
        if current_publication is not None:
            from .publication_journal import _validate

            _validate(current_publication)
        if _canonical(current_publication) != _canonical(intent.publication_document):
            raise NativeWriteUnresolved("terminal repair publication evidence changed")
        marker = storage.load_record(key + ":activation")
        if marker is not None:
            from .publication_journal import _marker

            _marker(marker)
            if marker["catalog_revision"] == lease.catalog_revision and (
                marker["build_token"] != lease.build_token
                or marker["build_lease_sha256"] != lease.build_lease_sha256
            ):
                raise NativeWriteUnresolved(
                    "terminal repair activation marker conflicts"
                )
            if (
                marker["catalog_revision"] >= lease.catalog_revision
                and intent.publication_document is None
                and (
                    saved is None
                    or marker["catalog_revision"] == lease.catalog_revision
                )
            ):
                raise NativeWriteUnresolved(
                    "activation marker lacks frozen publication evidence"
                )
        revoked_key = f"{key}:revoked:{lease.build_lease_sha256}"
        revoked = {"revoked": _journal_row_document(intent.rows[-1])}
        previous_revocation = storage.load_record(revoked_key)
        if previous_revocation is not None and _canonical(
            previous_revocation
        ) != _canonical(revoked):
            raise NativeWriteUnresolved("another operation already revoked this lease")
        if not allow_dispatch and previous_revocation is None:
            raise NativeWriteUnresolved("terminal repair lease revocation is missing")
        if not (self.writer.directory / NATIVE_WRITE_ATTEMPT_DIRECTORY).is_dir():
            raise NativeWriteUnresolved("terminal repair native journal is missing")
        with (
            self.writer._journal() as journal,
            journal.scope(binding.quarantine.scope) as scoped,
        ):
            if scoped.quarantine_binding != binding.quarantine:
                raise NativeWriteUnresolved(
                    "terminal repair quarantine generation changed"
                )
            self.writer.proof.attest(timeout_ms=remaining())
            # These records do not claim remote completion. They prevent an old
            # publisher from resuming even if this process dies before dispatch.
            if saved is None:
                saved = {**expected, "complete": False, "receipt_sha256s": None}
                storage.save_record(execution_key, saved)
            if previous_revocation is None:
                storage.save_record(revoked_key, revoked)
            if allow_dispatch:
                scoped.begin_terminal_repair(binding)
            else:
                scoped._require_terminal_binding(binding)
            completed = []
            quorum = (
                len(self.writer.proof.connections)
                if self.writer.proof.admission.family == "replicated"
                else 0
            )
            for write, row in zip(binding.writes, intent.rows, strict=True):
                columns = _COLUMNS[write.table]
                with scoped.session(
                    write.table, write.deduplication_token, terminal_repair=binding
                ) as session:
                    if allow_dispatch:
                        attempt = self.writer._insert_session(
                            session,
                            (row,),
                            columns=columns,
                            token=write.deduplication_token,
                            digest=write.parameters_sha256,
                            sql=native_insert_sql(
                                self.writer.database,
                                write.table,
                                columns,
                                quorum=quorum,
                            ),
                            quorum=quorum,
                            remaining=remaining,
                        )
                    else:
                        attempt = session.load()
                        if attempt is None:
                            raise NativeWriteUnresolved(
                                "terminal repair receipt is missing"
                            )
                        scoped._validate_terminal_attempt(attempt, binding)
                        attempt = self.writer._settle_and_cover(
                            session, attempt, remaining=remaining
                        )
                    completed.append(attempt)
            # Revalidate all terminal rows before recording the completed pair;
            # this also applies to a previously completed execution on restart.
            for write, attempt in zip(binding.writes, completed, strict=True):
                with scoped.session(write.table, write.deduplication_token) as session:
                    self.writer._settle_and_cover(session, attempt, remaining=remaining)
            receipt = scoped.finish_terminal_repair(binding, tuple(completed))
            hashes = list(receipt.attempt_record_sha256s)
            if saved["complete"] and saved["receipt_sha256s"] != hashes:
                raise NativeWriteUnresolved(
                    "terminal repair completion receipt changed"
                )
            remaining()
            if not saved["complete"]:
                storage.save_record(
                    execution_key,
                    {**expected, "complete": True, "receipt_sha256s": hashes},
                )
            return receipt
