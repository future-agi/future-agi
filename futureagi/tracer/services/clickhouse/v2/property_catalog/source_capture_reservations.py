"""Bounded capture ownership index on the existing shared control volume.

The per-build journal remains authoritative for phase. This index is persisted
after create_prepared and BEFORE creating, so every potentially dispatched
CREATE is discoverable even when its table is absent. There is no expiry or
absence-based release. All methods require the manager's namespace lock.

These fixed controller bounds are not operator configuration. Abandoned empty
CREATEs have a separate allowance from useful captures, but an attempt must
reserve uncertainty headroom before it can send CREATE. Native part/byte/disk
admission is still required independently; this index bounds potential tables,
not their data size. Keep the bounded index AND permanent per-build tombstones.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

from .coordinator import FileSupersessionJournal
from .source_capture import SourceCaptureError, SourceCaptureSpec

MAX_LIVE_CAPTURES = 2
MAX_UNRESOLVED_CAPTURE_CREATES = 4
_FORMAT = "futureagi.property-catalog.source-capture-reservations.v1"
_LIVE = frozenset({"create_prepared", "created", "attaching", "captured", "retiring"})
_UNRESOLVED = frozenset({"create_prepared", "creating", "abandoned_create"})


class SourceCaptureBackpressure(SourceCaptureError):
    """No new CREATE can fit; retained captures and GC must remain usable."""


@dataclass(frozen=True)
class CaptureReservation:
    spec: SourceCaptureSpec
    phase: str


class CaptureReservations:
    """Manager-internal index; callers must hold its existing namespace lock."""

    def __init__(self, journal: FileSupersessionJournal, load_capture: Callable):
        self._journal = journal
        self._load_capture = load_capture

    @staticmethod
    def _key(spec):
        # Same resource namespace as the manager lock. Do not hide old entries
        # by changing installation/source identity; validate those in the body.
        return _FORMAT + ":" + spec.capture_database

    @staticmethod
    def _scope(spec):
        return {
            name: getattr(spec, name)
            for name in (
                "installation_id",
                "source_server_uuid",
                "source_database",
                "catalog_database",
            )
        }

    def _owner_key(self, spec):
        return self._key(spec) + ":owner:" + spec.capture_table

    def _check_owner(self, spec, *, required=False):
        owner = self._journal.load_record(self._owner_key(spec))
        if owner is None:
            if required:
                raise SourceCaptureError("capture reservation lost its table owner")
        elif owner != {"spec": asdict(spec)}:
            raise SourceCaptureError(
                "capture table name belongs to another build binding"
            )
        return owner

    def _entries(self, spec):
        record = self._journal.load_record(self._key(spec))
        if record is None:
            return ()
        if (
            set(record) != {"format", "scope", "specs"}
            or record["format"] != _FORMAT
            or record["scope"] != self._scope(spec)
            or type(record["specs"]) is not list
            or len(record["specs"]) > MAX_LIVE_CAPTURES + MAX_UNRESOLVED_CAPTURE_CREATES
        ):
            raise SourceCaptureError("capture reservation namespace or bound changed")
        entries, owners = [], set()
        for document in record["specs"]:
            try:
                reserved = SourceCaptureSpec(**document)
            except (TypeError, ValueError) as exc:
                raise SourceCaptureError("capture reservation spec is invalid") from exc
            if self._scope(reserved) != self._scope(spec):
                raise SourceCaptureError(
                    "capture reservation has another source identity"
                )
            if reserved.capture_table in owners:
                raise SourceCaptureError(
                    "capture reservation has duplicate table owners"
                )
            owners.add(reserved.capture_table)
            self._check_owner(reserved, required=True)
            state = self._load_capture(reserved)
            if state is None:
                # Never infer 'unsent' from a lost per-build journal.
                raise SourceCaptureError("capture reservation lost its build journal")
            entries.append(CaptureReservation(reserved, state["phase"]))
        return tuple(entries)

    def snapshot(self, spec):
        return tuple(e for e in self._entries(spec) if e.phase != "retired")

    def require(self, spec):
        for entry in self._entries(spec):
            if entry.spec.capture_table == spec.capture_table:
                if entry.spec != spec:
                    raise SourceCaptureError(
                        "capture reservation exact binding changed"
                    )
                return
        raise SourceCaptureError("dispatched capture has no durable reservation")

    def _admitted(self, spec):
        self._check_owner(spec)
        entries = list(self.snapshot(spec))
        for entry in entries:
            if entry.spec.capture_table == spec.capture_table:
                if entry.spec != spec:
                    raise SourceCaptureError(
                        "capture reservation exact binding changed"
                    )
                break
        else:
            entries.append(CaptureReservation(spec, "create_prepared"))
        if sum(e.phase in _LIVE for e in entries) > MAX_LIVE_CAPTURES:
            raise SourceCaptureBackpressure("capture live reservation slots are in use")
        if (
            sum(e.phase in _UNRESOLVED for e in entries)
            > MAX_UNRESOLVED_CAPTURE_CREATES
        ):
            raise SourceCaptureBackpressure(
                "unresolved CREATE reservation slots are in use"
            )
        return tuple(entries)

    def check_admission(self, spec):
        # Read-only preflight preserves the manager's no-intent/no-DDL behavior
        # when source or native capacity qualification fails.
        self._admitted(spec)

    def _save(self, spec, entries):
        self._journal.save_record(
            self._key(spec),
            {
                "format": _FORMAT,
                "scope": self._scope(spec),
                "specs": [
                    asdict(e.spec)
                    for e in sorted(entries, key=lambda e: e.spec.build_token)
                ],
            },
        )

    def reserve(self, spec):
        state = self._load_capture(spec)
        if state is None or state["phase"] != "create_prepared":
            raise SourceCaptureError("only a prepared CREATE can reserve new capacity")
        entries = self._admitted(spec)
        if self._check_owner(spec) is None:
            # Permanent name ownership survives index release and prevents
            # reusing a retired build token under a different workspace key.
            self._journal.save_record(self._owner_key(spec), {"spec": asdict(spec)})
        self._save(spec, entries)

    def release(self, spec):
        state = self._load_capture(spec)
        if state is None or state["phase"] != "retired":
            raise SourceCaptureError(
                "only durable terminal retirement releases capacity"
            )
        # A crash after retired but before this index update is recoverable:
        # future admission prunes terminal entries, never uncertain CREATEs.
        entries = self._entries(spec)
        for entry in entries:
            if entry.spec.capture_table == spec.capture_table and entry.spec != spec:
                raise SourceCaptureError("capture reservation exact binding changed")
        self._save(spec, tuple(e for e in entries if e.phase != "retired"))
