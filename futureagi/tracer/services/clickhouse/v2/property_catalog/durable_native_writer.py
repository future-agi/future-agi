"""Durable synchronous native writes plus full serving-member completion proof.

Each exact statement is sent at most once from its journal. Lost responses are
resolved by positive original-query evidence, never by replaying after a timeout
or assuming a deduplication window is still open. This is not source ingestion.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from .native_write_proof import _COLUMNS, NativeWriteProof, coverage_plan
from .native_write_transport import insert_once


class NativeWriteUnresolved(RuntimeError):
    """Keep the original attempt; recovery has not proved its complete outcome."""


class NativeCheckpointReloadRequired(NativeWriteUnresolved):
    """Recovered a different checkpoint; resume from it instead of rewriting it."""


_REPLAY_METADATA = {
    "property_catalog_deliveries": frozenset({"delivered_at"}),
    "property_catalog_checkpoints": frozenset(
        {"run_id", "worker_id", "started_at", "updated_at", "finished_at"}
    ),
}


class DurableNativeCatalogWriter:
    def __init__(
        self,
        driver: Any,
        *,
        directory: str | Path,
        proof: NativeWriteProof,
        member_name: str,
    ):
        if type(proof) is not NativeWriteProof:
            raise TypeError("native catalog writer requires real installation proof")
        self.proof = proof
        self.directory = Path(directory)
        self.driver = driver
        self.database = proof.admission.database
        self.admission_sha256 = hashlib.sha256(proof.admission.encode()).hexdigest()
        members = {member.name: member for member in proof.admission.members}
        connections = {connection.name: connection for connection in proof.connections}
        if member_name not in members or member_name not in connections:
            raise ValueError("native catalog writer member is not admitted")
        self.member = members[member_name]
        self.connection = connections[member_name]
        self._validate_driver()

    def _validate_driver(self):
        if (
            getattr(self.driver, "database", None) != self.database
            or getattr(self.driver, "server_enforced_readonly", None) is not False
            or not isinstance(getattr(self.driver, "user", None), str)
            or not self.driver.user
            or getattr(self.driver, "host", None)
            != getattr(self.connection.driver, "host", None)
            or getattr(self.driver, "port", None)
            != getattr(self.connection.driver, "port", None)
            or not getattr(self.driver, "host", None)
            or type(getattr(self.driver, "port", None)) is not int
        ):
            raise ValueError(
                "native writer must use the admitted direct native endpoint"
            )

    def query(self, sql, params, *, timeout_ms: int, agreement=None):
        self._validate_driver()
        remaining = self.proof._budget(timeout_ms)
        self.proof.attest(timeout_ms=remaining())
        result = self.proof.agreed_read(
            sql,
            params,
            timeout_ms=remaining(),
            **({"agreement": agreement} if agreement is not None else {}),
        )
        self.proof.attest(timeout_ms=remaining())
        return result

    @contextmanager
    def _journal(self):
        from .native_write_journal import NativeWriteJournal

        with NativeWriteJournal(self.directory) as journal:
            if (
                journal.identity.encode() != self.proof.identity.encode()
                or journal.admission.encode() != self.proof.admission.encode()
            ):
                raise NativeWriteUnresolved(
                    "native journal differs from writer installation/admission"
                )
            yield journal

    def insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
        write_scope=None,
    ) -> None:
        from .native_write_journal import (
            NativeWriteScope,
            native_insert_sql,
            native_parameters_sha256,
        )

        self._validate_driver()
        remaining = self.proof._budget(timeout_ms)
        match = re.fullmatch(r"`([a-z][a-z0-9_]{0,127})`\.`([a-z_]+)`", table)
        if (
            match is None
            or match[1] != self.database
            or match[2] not in _COLUMNS
            or tuple(columns) != _COLUMNS[match[2]]
            or not isinstance(deduplication_token, str)
            or not 1 <= len(deduplication_token.encode()) <= 1024
        ):
            raise ValueError("native durable insert target/columns/token invalid")
        if not rows:
            return
        if len(rows) > 16_384:
            raise ValueError("native durable insert row bound exceeded")
        bare_table = match[2]
        columns = tuple(columns)
        # Validate every row's exact SQL-comparable representation before any
        # remote read, journal write or INSERT. Journal independently validates
        # and freezes the typed native data with its own byte/row limits.
        coverage_plan(self.database, bare_table, rows, columns)
        quorum = (
            len(self.proof.connections)
            if self.proof.admission.family == "replicated"
            else 0
        )
        sql = native_insert_sql(self.database, bare_table, columns, quorum=quorum)
        digest = native_parameters_sha256(columns, rows)

        scope = NativeWriteScope.from_rows(bare_table, rows, self.proof.identity)
        with ExitStack() as resources:
            if write_scope is None:
                journal = resources.enter_context(self._journal())
                scoped = resources.enter_context(journal.scope(scope, create=True))
            else:
                from .native_publication import NativePublicationWriteScope

                if type(write_scope) is not NativePublicationWriteScope:
                    raise TypeError("native publication requires its exact live scope")
                scoped = write_scope.require_insert(
                    self,
                    scope,
                    table=bare_table,
                    token=deduplication_token,
                    digest=digest,
                )
                local_remaining = remaining

                def remaining():
                    return min(local_remaining(), write_scope.remaining())

            if (
                scope.kind == "build"
                and bare_table != "property_catalog_activation_control_events"
            ):
                # Discovery only: a crash at any point below leaves the exact
                # BUILD discoverable; receipts remain the operation authority.
                scoped._journal.track_recovery_scope(scoped)
            with scoped.session(bare_table, deduplication_token) as session:
                self._insert_session(
                    session,
                    rows,
                    columns=columns,
                    token=deduplication_token,
                    digest=digest,
                    sql=sql,
                    quorum=quorum,
                    remaining=remaining,
                )

    def _insert_session(
        self, session, rows, *, columns, token, digest, sql, quorum, remaining
    ):
        """One already-authorized locked intent; normal and terminal use identical proof."""
        previous = session.load()
        settings = (
            dict(previous.settings)
            if previous is not None
            else {
                "async_insert": 0,
                "insert_deduplicate": 1,
                "insert_deduplication_token": token,
                "insert_quorum": quorum,
                "insert_quorum_parallel": 1,
                "insert_quorum_timeout": max(1, remaining() // 2),
                "max_execution_time": max(1, (remaining() + 999) // 1000),
                "insert_block_size": 16_384,
                "log_comment": digest,
            }
        )
        attempt = session.prepare(
            database=self.database,
            member=self.member.name,
            user=self.driver.user,
            admission_sha256=self.admission_sha256,
            columns=columns,
            rows=rows,
            sql=sql,
            settings=settings,
        )
        self._validate_settings(attempt.settings, token, digest)
        self._retain_complete(session, attempt)
        self.proof.attest(timeout_ms=remaining())
        if attempt.state == "prepared":
            if attempt.settings["insert_quorum_timeout"] >= remaining():
                raise NativeWriteUnresolved(
                    "saved native INSERT timeout does not fit remaining budget"
                )

            def before_send():
                nonlocal attempt
                attempt = session.mark_sent(attempt)

            count = insert_once(
                self.driver,
                member=self.member,
                database=self.database,
                user=attempt.user,
                sql=attempt.sql,
                values=list(attempt.parameters),
                query_id=attempt.query_id,
                settings=dict(attempt.settings),
                timeout_ms=remaining(),
                before_send=before_send,
            )
            if type(count) is not int or count != attempt.row_count:
                raise NativeWriteUnresolved(
                    "native INSERT has no exact full acknowledgement"
                )
            attempt = session.mark_acknowledged(
                attempt,
                evidence=self._ack_evidence(attempt, "native_end_of_stream"),
            )
        return self._settle_and_cover(session, attempt, remaining=remaining)

    def recover_scope(self, scope, *, timeout_ms: int) -> int:
        """Settle only already-sent writes in an existing exact build/control scope.

        This never authorizes a new INSERT, including a Prepared attempt. A
        caller resuming Prepared must revalidate its current lifecycle lease and
        use the original intent through the normal write entrypoint. Historical
        checkpoint visibility alone is not that authorization.
        """
        from .native_write_journal import NativeWriteScope

        self._validate_driver()
        if type(scope) is not NativeWriteScope:
            raise TypeError("native recovery requires an exact write scope")
        remaining = self.proof._budget(timeout_ms)
        with self._journal() as journal, journal.scope(scope) as scoped:
            count = self._recover_scoped(scoped, remaining=remaining)
            if scoped.pending():
                raise NativeWriteUnresolved("native scope remains unresolved")
            remaining()
            return count

    def _replay_scope(self, table, rows, columns):
        from .native_write_journal import NativeWriteScope

        self._validate_driver()
        bare = next(
            (
                name
                for name in _REPLAY_METADATA
                if table == f"`{self.database}`.`{name}`"
            ),
            None,
        )
        if bare is None or len(rows) != 1 or tuple(columns) != _COLUMNS[bare]:
            raise ValueError("native replay requires one exact delivery/checkpoint row")
        coverage_plan(self.database, bare, rows, columns)
        return bare, NativeWriteScope.from_rows(bare, rows, self.proof.identity)

    def _frozen_replay_rows(self, attempt, rows, *, observed=False):
        from .native_write_journal import native_parameters_sha256

        self._validate_attempt(attempt)
        bare = attempt["table"]
        if bare not in _REPLAY_METADATA or attempt.row_count != 1:
            raise NativeWriteUnresolved("native replay receipt has another shape")
        saved = attempt.rows[0]
        if observed:
            from .native_write_proof import _parameter

            # Native reads return UUID objects while the original INSERT may
            # carry canonical UUID strings. Match their SQL representation;
            # the original receipt/INSERT bytes themselves are never rewritten.
            if any(
                _parameter(rows[0][column]) != _parameter(saved[column])
                for column in attempt.columns
                if column not in _REPLAY_METADATA[bare]
            ):
                raise NativeWriteUnresolved("native replay changed non-metadata intent")
            return attempt.rows
        frozen = dict(rows[0])
        for column in _REPLAY_METADATA[bare]:
            frozen[column] = saved[column]
        if (
            native_parameters_sha256(attempt.columns, (frozen,))
            != attempt.parameters_sha256
        ):
            raise NativeWriteUnresolved("native replay changed non-metadata intent")
        return (frozen,)

    def restore_insert_metadata(self, table, rows, *, columns, deduplication_token):
        """Recover only allocated timestamps/worker metadata; never a version.

        This does not send or settle anything. The domain caller must still hold
        its write authorization, and insert() independently rechecks frozen bytes.
        """
        bare, scope = self._replay_scope(table, rows, columns)
        with self._journal() as journal, journal.scope(scope, create=True) as scoped:
            with scoped.session(bare, deduplication_token) as session:
                attempt = session.load()
                return (
                    tuple(dict(row) for row in rows)
                    if attempt is None
                    else self._frozen_replay_rows(attempt, rows)
                )

    def recover_checkpoint_dependencies(self, row, *, timeout_ms):
        """Settle before version allocation; reserve only the same Prepared state.

        Return its immutable (version, token), not permission to change either.
        The state store must match that reservation to its visible-version
        decision, then perform the ordinarily authorized exact insert.
        """
        from .native_write_proof import _parameter

        table = "property_catalog_checkpoints"
        _, scope = self._replay_scope(
            f"`{self.database}`.`{table}`", (row,), _COLUMNS[table]
        )
        remaining = self.proof._budget(timeout_ms)
        prepared = None
        reload_required = False
        with self._journal() as journal, journal.scope(scope, create=True) as scoped:
            pending = scoped.pending()
            self.proof.attest(timeout_ms=remaining())
            for reference in pending:
                with scoped.session(
                    reference.table, reference.deduplication_token
                ) as session:
                    attempt = session.load()
                    if attempt is None:
                        raise NativeWriteUnresolved(
                            "pending checkpoint receipt disappeared"
                        )
                    self._validate_attempt(attempt)
                    if attempt.state == "prepared" and reference.table == table:
                        if (
                            attempt.row_count != 1
                            or any(
                                _parameter(row[column])
                                != _parameter(attempt.rows[0][column])
                                for column in _COLUMNS[table]
                                if column not in _REPLAY_METADATA[table] | {"_version"}
                            )
                            or prepared is not None
                        ):
                            raise NativeWriteUnresolved(
                                "pending checkpoint intent must settle before version allocation"
                            )
                        prepared = (
                            attempt.rows[0]["_version"],
                            reference.deduplication_token,
                        )
                    else:
                        self._settle_and_cover(session, attempt, remaining=remaining)
                        if reference.table == table and attempt.row_count == 1:
                            saved = attempt.rows[0]
                            same_stream = all(
                                _parameter(row[column]) == _parameter(saved[column])
                                for column in ("source_adapter", "producer_stream_id")
                            )
                            if same_stream and any(
                                _parameter(row[column]) != _parameter(saved[column])
                                for column in _COLUMNS[table]
                                if column not in _REPLAY_METADATA[table] | {"_version"}
                            ):
                                reload_required = True
            remaining()
        if reload_required:
            raise NativeCheckpointReloadRequired(
                "recovered checkpoint changed this stream; reload its current state"
            )
        return prepared

    def confirm_insert(self, table, rows, *, columns, timeout_ms, deduplication_token):
        """Require an exact receipt and settled dependencies before positive replay."""
        bare, scope = self._replay_scope(table, rows, columns)
        remaining = self.proof._budget(timeout_ms)
        with self._journal() as journal, journal.scope(scope) as scoped:
            with scoped.session(bare, deduplication_token) as session:
                attempt = session.load()
                if attempt is None:
                    raise NativeWriteUnresolved(
                        "visible replay lacks its exact native receipt"
                    )
                self._frozen_replay_rows(attempt, rows, observed=True)
                self._retain_complete(session, attempt)
            self._recover_scoped(
                scoped, remaining=remaining, final_confirmation=attempt
            )
            for reference in scoped.pending():
                with scoped.session(
                    reference.table, reference.deduplication_token
                ) as session:
                    future = session.load()
                    if future is None or not self._later_prepared_delivery(
                        scoped, future, attempt
                    ):
                        raise NativeWriteUnresolved(
                            "native replay dependencies remain unresolved"
                        )
            remaining()

    def _later_prepared_delivery(self, scoped, attempt, confirmed):
        """A future unsent delivery is not a dependency of an earlier replay."""
        from .native_write_journal import NativeWriteScope
        from .native_write_proof import _parameter

        table = "property_catalog_deliveries"
        if (
            confirmed is None
            or confirmed.state != "complete"
            or attempt.state != "prepared"
            or confirmed["table"] != table
            or attempt["table"] != table
            or confirmed.row_count != 1
            or attempt.row_count != 1
            or scoped.quarantine_binding is not None
            or scoped._read_index()["closure"] is not None
        ):
            return False
        self._validate_attempt(attempt)
        if NativeWriteScope.from_rows(table, attempt.rows, self.proof.identity) != (
            scoped.scope
        ):
            raise NativeWriteUnresolved("future delivery differs from held scope")
        earlier, later = confirmed.rows[0], attempt.rows[0]
        return (
            type(earlier["sequence"]) is int
            and type(later["sequence"]) is int
            and 0 < earlier["sequence"] < later["sequence"] < 1 << 64
            and type(earlier["terminal"]) is int
            and earlier["terminal"] == 0
            and all(
                _parameter(earlier[column]) == _parameter(later[column])
                for column in (
                    "source_adapter",
                    "producer_stream_id",
                    "envelope_format",
                    "envelope_version",
                    "transport",
                )
            )
        )

    def confirm_receipt(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None:
        """Prove one exact control event, never INSERT or complete its whole build.

        No metadata is ignored. SQL parameter normalization accepts native UUID
        and UTC DateTime64 read forms without changing the original typed receipt.
        In particular, confirming prepublished FOLLOW must not wait for a sibling
        Prepared ACTIVE which the lifecycle publisher has yet to authorize.
        """
        self._confirm_one_row_receipt(
            table,
            rows,
            columns=columns,
            timeout_ms=timeout_ms,
            deduplication_token=deduplication_token,
            reservation=False,
        )

    def confirm_reservation_receipt(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None:
        """Prove one exact source reservation, including all frozen metadata.

        This never sends, initializes a journal, or drains sibling attempts.
        Existing quarantined scopes permit proof, but never a Prepared send.
        """
        self._confirm_one_row_receipt(
            table,
            rows,
            columns=columns,
            timeout_ms=timeout_ms,
            deduplication_token=deduplication_token,
            reservation=True,
        )

    def _confirm_one_row_receipt(
        self,
        table,
        rows,
        *,
        columns,
        timeout_ms,
        deduplication_token,
        reservation: bool,
    ) -> None:
        from .native_write_journal import (
            NATIVE_WRITE_ATTEMPT_DIRECTORY,
            NativeWriteScope,
            native_parameters_sha256,
        )
        from .native_write_proof import _parameter

        self._validate_driver()
        remaining = self.proof._budget(timeout_ms)
        bare = (
            "property_catalog_source_streams"
            if reservation
            else "property_catalog_activation_control_events"
        )
        kind = "reservation" if reservation else "control"
        label = "source reservation" if reservation else "control event"
        if (
            table != f"`{self.database}`.`{bare}`"
            or not isinstance(rows, (tuple, list))
            or len(rows) != 1
            or tuple(columns) != _COLUMNS[bare]
            or not isinstance(deduplication_token, str)
            or not 1 <= len(deduplication_token.encode()) <= 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in deduplication_token)
        ):
            raise ValueError(
                f"native receipt requires one exact {kind} row/columns/token"
            )
        columns = tuple(columns)
        # Validate original types and size before normalization or journal I/O.
        native_parameters_sha256(columns, rows)
        coverage_plan(self.database, bare, rows, columns)
        scope = NativeWriteScope.from_rows(bare, rows, self.proof.identity)
        if reservation:
            from .codec import canonical_uuid

            row = rows[0]
            if (
                _parameter(row["source_adapter"]) != "system_manifest"
                or type(row["envelope_version"]) is not int
                or row["envelope_version"] != 0
                or canonical_uuid(row["producer_stream_id"], field="producer_stream_id")
                != scope.build_token
            ):
                raise ValueError("native receipt requires an exact source reservation")

        def parameter_digest(row):
            return native_parameters_sha256(
                columns, ({column: _parameter(row[column]) for column in columns},)
            )

        requested_digest = parameter_digest(rows[0])
        if not (self.directory / NATIVE_WRITE_ATTEMPT_DIRECTORY).is_dir():
            raise NativeWriteUnresolved(f"{kind} receipt journal does not exist")
        with self._journal() as journal, journal.scope(scope) as scoped:
            with scoped.session(bare, deduplication_token) as session:
                attempt = session.load()
                if attempt is None:
                    raise NativeWriteUnresolved(
                        f"{label} lacks its exact native receipt"
                    )
                self._validate_attempt(attempt)
                if (
                    attempt.row_count != 1
                    or attempt.columns != columns
                    or parameter_digest(attempt.rows[0]) != requested_digest
                ):
                    raise NativeWriteUnresolved(
                        f"{label} differs from its exact native receipt"
                    )
                self._retain_complete(session, attempt)
                self.proof.attest(timeout_ms=remaining())
                self._settle_and_cover(session, attempt, remaining=remaining)
            remaining()

    def _recover_scoped(
        self, scoped, *, remaining, prepared_publication=None, final_confirmation=None
    ):
        from .native_write_journal import NativeWriteAttempt, NativeWriteScope

        final_key = None
        if final_confirmation is not None:
            if (
                prepared_publication is not None
                or type(final_confirmation) is not NativeWriteAttempt
                or NativeWriteScope.from_rows(
                    final_confirmation["table"],
                    final_confirmation.rows,
                    self.proof.identity,
                )
                != scoped.scope
            ):
                raise NativeWriteUnresolved(
                    "native confirmation target differs from held scope"
                )
            self._validate_attempt(final_confirmation)
            final_key = (
                final_confirmation["table"],
                final_confirmation["deduplication_token"],
            )
        pending = scoped.pending()
        self.proof.attest(timeout_ms=remaining())
        settled = 0
        for reference in pending:
            with scoped.session(
                reference.table, reference.deduplication_token
            ) as session:
                attempt = session.load()
                if attempt is None or (
                    attempt.query_id != reference.query_id
                    or attempt.parameters_sha256 != reference.parameters_sha256
                ):
                    raise NativeWriteUnresolved("pending native receipt changed")
                self._validate_attempt(attempt)
                if (reference.table, reference.deduplication_token) == final_key:
                    if attempt.encode() != final_confirmation.encode():
                        raise NativeWriteUnresolved(
                            "native confirmation receipt changed"
                        )
                    # Leave READY intact. The exact target must be proved below,
                    # after every sibling, before this operation can succeed.
                    continue
                if (
                    attempt.state == "prepared"
                    and prepared_publication is not None
                    and reference.table == "property_catalog_activations"
                    and reference.deduplication_token
                    == prepared_publication.deduplication_token
                    and reference.parameters_sha256
                    == prepared_publication.parameters_sha256
                ):
                    # The currently authorized immutable ACTIVE may still be
                    # dispatched by append_active. No other Prepared is skipped.
                    continue
                if self._later_prepared_delivery(scoped, attempt, final_confirmation):
                    # Keep READY and the frozen receipt untouched. Confirming an
                    # earlier delivery must let its authorized publisher reach
                    # this later INSERT. Whole-build/publication recovery has no
                    # completed delivery target and still rejects every Prepared.
                    continue
                self._settle_and_cover(session, attempt, remaining=remaining)
                settled += 1
        if final_key is not None:
            with scoped.session(*final_key) as session:
                attempt = session.load()
                if attempt is None or attempt.encode() != final_confirmation.encode():
                    raise NativeWriteUnresolved("native confirmation receipt changed")
                self._settle_and_cover(session, attempt, remaining=remaining)
                settled += 1
        return settled

    def _retain_complete(self, session, attempt):
        # Control receipts never authorize automatic build abandonment. Frozen
        # quarantine/terminal accounting remains proof-only and immutable.
        if (
            attempt.state == "complete"
            and session._scoped.scope.kind == "build"
            and attempt["table"] != "property_catalog_activation_control_events"
            and session._scoped.quarantine_binding is None
        ):
            self._validate_attempt(attempt)
            session._scoped._retain_complete(session, attempt)

    def _settle_and_cover(self, session, attempt, *, remaining):
        self._validate_attempt(attempt)
        self._retain_complete(session, attempt)
        if attempt.state == "sent":
            if not self.proof.settled(attempt, timeout_ms=remaining()):
                raise NativeWriteUnresolved(
                    "original native INSERT outcome is still unresolved"
                )
            attempt = session.mark_acknowledged(
                attempt,
                evidence=self._ack_evidence(attempt, "query_log_finish"),
            )
        elif attempt.state == "prepared":
            raise NativeWriteUnresolved(
                "Prepared native attempt requires current write authorization"
            )
        elif attempt.state not in {"acknowledged", "complete"}:
            raise NativeWriteUnresolved("native INSERT journal has an invalid state")

        # Even a saved complete receipt cannot hide replica replacement or lost
        # rows. Repeated calls prove coverage, never re-INSERT.
        self.proof.cover(
            attempt["table"],
            attempt.rows,
            columns=attempt.columns,
            timeout_ms=remaining(),
        )
        self.proof.attest(timeout_ms=remaining())
        if attempt.state != "complete":
            attempt = session.mark_complete(
                attempt,
                evidence={
                    "kind": "all_member_coverage",
                    "query_id": attempt.query_id,
                    "parameters_sha256": attempt.parameters_sha256,
                    "admission_sha256": self.admission_sha256,
                    "members": [member.name for member in self.proof.admission.members],
                    "settlement": attempt.acknowledgement["kind"],
                },
            )
        else:
            session._scoped._forget_completed(session, attempt)
        return attempt

    def _validate_attempt(self, attempt):
        if (
            attempt["admission_sha256"] != self.admission_sha256
            or attempt["database"] != self.database
            or attempt["member"] not in {m.name for m in self.proof.admission.members}
        ):
            raise NativeWriteUnresolved(
                "native receipt differs from writer installation/admission"
            )
        self._validate_settings(
            attempt.settings, attempt["deduplication_token"], attempt.parameters_sha256
        )

    def _ack_evidence(self, attempt, kind):
        return {
            "kind": kind,
            "query_id": attempt.query_id,
            "parameters_sha256": attempt.parameters_sha256,
            "admission_sha256": self.admission_sha256,
            "written_rows": attempt.row_count,
        }

    def _validate_settings(self, settings, token, digest):
        exact = {
            "async_insert": 0,
            "insert_deduplicate": 1,
            "insert_deduplication_token": token,
            "insert_quorum": len(self.proof.connections)
            if self.proof.admission.family == "replicated"
            else 0,
            "insert_quorum_parallel": 1,
            "insert_block_size": 16_384,
            "log_comment": digest,
        }
        if (
            set(settings)
            != set(exact) | {"max_execution_time", "insert_quorum_timeout"}
            or any(
                type(settings[key]) is not type(value) or settings[key] != value
                for key, value in exact.items()
            )
            or type(settings["max_execution_time"]) is not int
            or not 1 <= settings["max_execution_time"] <= 30
            or type(settings["insert_quorum_timeout"]) is not int
            or not 1 <= settings["insert_quorum_timeout"] <= 15_000
        ):
            raise NativeWriteUnresolved(
                "frozen native write settings differ from admitted policy"
            )
