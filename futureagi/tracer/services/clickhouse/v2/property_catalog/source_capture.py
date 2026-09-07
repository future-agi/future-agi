"""Durable lifecycle for a bounded, immutable canonical-source capture.

This reuses the controller's existing file journal and locking. It does not
discover endpoints, borrow ledger credentials, install grants or rewrite source
spans. A factory-owned source-local backend must provide the narrow operations
below. An ATTACH with an uncertain result is never repeated: the unexposed
derived table must be discarded and a new build allocated instead.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .codec import canonical_json, canonical_json_sha256, canonical_uuid
from .coordinator import FileSupersessionJournal
from .repair_files import locked_file

if TYPE_CHECKING:
    from .source_capture_reservations import CaptureReservation

_FORMAT = "futureagi.property-catalog.source-capture.v2"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,126}\Z", re.ASCII)
_SHA256 = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_PHASES = frozenset(
    {
        "create_prepared",
        "creating",
        "created",
        "abandoned_create",
        "attaching",
        "captured",
        "retiring",
        "retired",
    }
)


class SourceCaptureError(RuntimeError):
    pass


class SourceCaptureUncertain(SourceCaptureError):
    """Discard this unexposed capture; do not retry its ATTACH or reuse its build."""


class SourceCaptureRetired(SourceCaptureError):
    """This build identity can never create another capture."""


def _uuid(value: str, name: str) -> str:
    result = canonical_uuid(value, field=name)
    if result != value or UUID(result).int == 0:
        raise ValueError(f"{name} must be a canonical nonzero UUID")
    return result


def _sha(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a SHA256 digest")
    return value


@dataclass(frozen=True)
class SourceCaptureSpec:
    installation_id: str
    organization_id: str
    workspace_id: str
    build_token: str
    source_server_uuid: str
    source_database: str
    catalog_database: str
    source_table_uuid: str
    source_schema_sha256: str
    capture_schema_sha256: str

    def __post_init__(self):
        for name in (
            "installation_id",
            "organization_id",
            "workspace_id",
            "build_token",
            "source_server_uuid",
            "source_table_uuid",
        ):
            _uuid(getattr(self, name), name)
        if (
            not isinstance(self.source_database, str)
            or _IDENTIFIER.fullmatch(self.source_database) is None
        ):
            raise ValueError("source database must be an exact identifier")
        if (
            not isinstance(self.catalog_database, str)
            or _IDENTIFIER.fullmatch(self.catalog_database) is None
        ):
            raise ValueError("catalog database must be an exact identifier")
        if (
            self.catalog_database == self.source_database
            or _IDENTIFIER.fullmatch(self.capture_database) is None
        ):
            raise ValueError("capture namespace must be isolated from canonical source")
        _sha(self.source_schema_sha256, "source schema")
        _sha(self.capture_schema_sha256, "capture schema")
        if self.source_database == self.capture_database:
            raise ValueError("capture and source databases must differ")

    @property
    def capture_database(self) -> str:
        # The normal installer can provision this from the existing catalog DB;
        # no new setting or runtime-generated broad database-creation grant.
        return self.catalog_database + "_source_capture"

    @property
    def capture_table(self) -> str:
        return "spans_" + UUID(self.build_token).hex

    @property
    def capture_table_uuid(self) -> str:
        # Schema hashes may include the destination CREATE UUID. Derive that
        # UUID from identities only, never recursively from the CREATE hash.
        identity = {k: v for k, v in asdict(self).items() if not k.endswith("_sha256")}
        digest = canonical_json_sha256(canonical_json(identity))
        return str(uuid5(NAMESPACE_URL, _FORMAT + ":" + digest))

    @property
    def binding_sha256(self) -> str:
        return canonical_json_sha256(canonical_json(asdict(self)))

    @property
    def query_id(self) -> str:
        return "pc-capture-" + self.binding_sha256


@dataclass(frozen=True)
class SourceCaptureObservation:
    """Fresh source-local physical-table identity and immutable content summary.

    The backend must qualify plain MergeTree, no TTL, disabled automatic merges,
    the exact schema/UUID and physically stored version columns before returning
    this observation. Its bounded part-content digest is not a source watermark.
    """

    server_uuid: str
    database: str
    table: str
    table_uuid: str
    schema_sha256: str
    parts_sha256: str
    parts: int
    rows: int
    bytes_on_disk: int

    def __post_init__(self):
        _uuid(self.server_uuid, "capture server")
        _uuid(self.table_uuid, "capture table")
        for name in ("database", "table"):
            if (
                not isinstance(getattr(self, name), str)
                or _IDENTIFIER.fullmatch(getattr(self, name)) is None
            ):
                raise ValueError("invalid capture identifier")
        _sha(self.schema_sha256, "capture schema")
        _sha(self.parts_sha256, "capture parts")
        for name in ("parts", "rows", "bytes_on_disk"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value < 2**64:
                raise ValueError("capture sizes must be UInt64 values")
        if self.parts == 0 and (self.rows != 0 or self.bytes_on_disk != 0):
            raise ValueError("an empty capture cannot contain rows or part bytes")

    def bind(self, spec: SourceCaptureSpec) -> None:
        if (
            self.server_uuid,
            self.database,
            self.table,
            self.table_uuid,
            self.schema_sha256,
        ) != (
            spec.source_server_uuid,
            spec.capture_database,
            spec.capture_table,
            spec.capture_table_uuid,
            spec.capture_schema_sha256,
        ):
            raise SourceCaptureError("capture observation changed its exact binding")


class SourceCaptureBackend(Protocol):
    """Source-local capability, qualified by the runtime factory, not a raw driver."""

    def check_capacity(self, spec: SourceCaptureSpec) -> None:
        """Check bounded owned-table count/parts/retained bytes before creation."""

    def verify_source(self, spec: SourceCaptureSpec) -> None:
        """Verify same source server/table incarnation/schema and stored inputs."""

    def create_empty_once(
        self, spec: SourceCaptureSpec, *, before_send: Callable[[], None]
    ) -> None:
        """Fsync before_send immediately before the ONE native CREATE dispatch.

        No automatic retry, pre-existing-target adoption or ATTACH is permitted.
        """

    def inspect(self, spec: SourceCaptureSpec) -> SourceCaptureObservation | None:
        """Fresh bounded metadata. Unknown/wrong/incomplete observations raise."""

    def attach_once(self, spec: SourceCaptureSpec, *, query_id: str) -> None:
        """One ATTACH FROM on target only. No retries or compound CREATE statement."""

    def drop_owned(
        self, spec: SourceCaptureSpec, *, require_empty: bool = False
    ) -> None:
        """Recheck exact target UUID and synchronously discard only that target.

        Never recreate/reuse its name. No source operation is permitted. Empty
        late CREATE cleanup must reject any parts, not turn into data deletion.
        """


class DurableSourceCapture:
    def __init__(
        self,
        directory: str,
        backend: SourceCaptureBackend,
        *,
        can_retire: Callable[[SourceCaptureSpec], bool],
    ):
        self._root = Path(directory)
        if (
            not self._root.is_absolute()
            or self._root.is_symlink()
            or not self._root.is_dir()
        ):
            raise ValueError(
                "source capture needs the existing private control directory"
            )
        info = self._root.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError(
                "source capture control directory has unsafe ownership or permissions"
            )
        if not callable(can_retire):
            raise TypeError("capture retirement requires a finished-build verifier")
        self._backend = backend
        self._can_retire = can_retire
        self._journal = FileSupersessionJournal(directory)
        from .source_capture_reservations import CaptureReservations

        self._reservations = CaptureReservations(self._journal, self._load)

    def _key(self, spec):
        # Deliberately excludes schema: a changed contract cannot hide a prior
        # uncertain ATTACH behind a new journal key for the same build.
        return ":".join(
            (
                _FORMAT,
                spec.installation_id,
                spec.organization_id,
                spec.workspace_id,
                spec.build_token,
            )
        )

    def _lock(self, spec):
        key = canonical_json_sha256(self._key(spec))
        return locked_file(self._root / ("capture-" + key))

    def _source_lock(self, spec):
        # Serialize slot admission with creation/retirement across workspaces on
        # the existing shared control volume, not just within one build.
        return locked_file(self._root / spec.capture_database)

    def _load(self, spec):
        record = self._journal.load_record(self._key(spec))
        if record is None:
            return None
        if (
            set(record) != {"format", "spec", "phase", "observation"}
            or record["format"] != _FORMAT
            or record["spec"] != asdict(spec)
            or record["phase"] not in _PHASES
        ):
            raise SourceCaptureError("capture journal identity or shape changed")
        observation = record["observation"]
        if observation is not None:
            try:
                SourceCaptureObservation(**observation).bind(spec)
            except (TypeError, ValueError) as exc:
                raise SourceCaptureError(
                    "capture journal observation is invalid"
                ) from exc
        if (record["phase"] == "captured" and observation is None) or (
            observation is not None and record["phase"] not in {"captured", "retiring"}
        ):
            raise SourceCaptureError("capture journal phase has no valid observation")
        return record

    def _save(self, spec, phase, observation=None):
        self._journal.save_record(
            self._key(spec),
            {
                "format": _FORMAT,
                "spec": asdict(spec),
                "phase": phase,
                "observation": asdict(observation) if observation is not None else None,
            },
        )

    def _inspect(self, spec):
        observation = self._backend.inspect(spec)
        if not isinstance(observation, SourceCaptureObservation):
            raise SourceCaptureError("expected capture table is absent or unqualified")
        observation.bind(spec)
        return observation

    def reservation_snapshot(
        self, spec: SourceCaptureSpec
    ) -> tuple[CaptureReservation, ...]:
        """Bounded exact specs/phases for the runtime's existing recovery/GC loop.

        The namespace lock is released before returning. Construct the normal
        backend for each frozen spec and call retire on that spec's manager to
        abandon creating attempts or sweep abandoned_create late empties. Never
        treat a successful sweep/absent table as release of its reservation.
        Workspace locks, if needed by can_retire, MUST precede the source lock;
        the callback must not acquire a workspace lock from inside retire.
        """
        if type(spec) is not SourceCaptureSpec:
            raise TypeError("capture requires its exact validated spec")
        spec.__post_init__()
        with self._source_lock(spec):
            return self._reservations.snapshot(spec)

    def acquire(self, spec: SourceCaptureSpec) -> SourceCaptureObservation:
        if type(spec) is not SourceCaptureSpec:
            raise TypeError("capture requires its exact validated spec")
        spec.__post_init__()
        with self._source_lock(spec), self._lock(spec):
            record = self._load(spec)
            if record is not None and record["phase"] in {
                "retiring",
                "retired",
                "abandoned_create",
            }:
                raise SourceCaptureRetired("a retired capture build cannot be reused")
            if record is not None and record["phase"] == "creating":
                raise SourceCaptureUncertain(
                    "CREATE outcome is uncertain; abandon this unexposed build, never replay CREATE"
                )
            if record is not None and record["phase"] == "attaching":
                raise SourceCaptureUncertain(
                    "ATTACH outcome is uncertain; never replay it"
                )
            if record is not None and record["phase"] == "captured":
                self._reservations.require(spec)
                observed = self._inspect(spec)
                if asdict(observed) != record["observation"]:
                    raise SourceCaptureError(
                        "captured contents changed after completion"
                    )
                return observed
            if record is None or record["phase"] == "create_prepared":
                self._reservations.check_admission(spec)
                self._backend.verify_source(spec)
                # Prepared retries must recheck physical capacity too; another
                # workspace may have used it while no CREATE was dispatched.
                self._backend.check_capacity(spec)
                if record is None:
                    self._save(spec, "create_prepared")
                self._reservations.reserve(spec)
                self._backend.create_empty_once(
                    spec, before_send=lambda: self._save(spec, "creating")
                )
                self._save(spec, "created")
            else:
                self._reservations.require(spec)
                self._backend.verify_source(spec)
            empty = self._inspect(spec)
            if empty.parts or empty.rows or empty.bytes_on_disk:
                raise SourceCaptureError("refusing ATTACH into a nonempty target")
            self._backend.verify_source(spec)
            self._save(spec, "attaching")  # Durable BEFORE the only non-idempotent RPC.
            self._backend.attach_once(spec, query_id=spec.query_id)
            self._backend.verify_source(spec)
            observed = self._inspect(spec)
            self._save(spec, "captured", observed)
            return observed

    def retire(self, spec: SourceCaptureSpec) -> None:
        if type(spec) is not SourceCaptureSpec:
            raise TypeError("capture requires its exact validated spec")
        spec.__post_init__()
        with self._source_lock(spec), self._lock(spec):
            record = self._load(spec)
            if record is None:
                raise SourceCaptureError("unknown capture has no ownership intent")
            if record["phase"] == "create_prepared":
                if self._backend.inspect(spec) is not None:
                    raise SourceCaptureError("unsent CREATE unexpectedly has a table")
                self._save(spec, "retired")
                self._reservations.release(spec)
                return
            if record["phase"] != "retired":
                self._reservations.require(spec)
            if record["phase"] in {"creating", "abandoned_create"}:
                # CREATE was never positively acknowledged, so ATTACH could
                # never have been sent. Permanently veto this build, and allow
                # only exact-owned EMPTY late-CREATE cleanup on future GC passes.
                # Do not claim the table can never appear: an in-flight CREATE
                # may finish later. Runtime GC must retain this intent/reservation.
                if record["phase"] == "creating":
                    self._save(spec, "abandoned_create")
                self._backend.drop_owned(spec, require_empty=True)
                observed = self._backend.inspect(spec)
                if observed is not None:
                    if not isinstance(observed, SourceCaptureObservation):
                        raise SourceCaptureError("unqualified late CREATE observation")
                    observed.bind(spec)
                    if observed.parts or observed.rows or observed.bytes_on_disk:
                        raise SourceCaptureError(
                            "abandoned CREATE unexpectedly contains parts"
                        )
                    # The sole late CREATE may have arrived just after an absent
                    # check. Leave it on the bounded GC index for the next pass.
                return
            if record["phase"] == "retired":
                if self._backend.inspect(spec) is not None:
                    raise SourceCaptureError("retired capture unexpectedly reappeared")
                self._reservations.release(spec)
                return
            if record["phase"] == "captured" and self._can_retire(spec) is not True:
                raise SourceCaptureError(
                    "capture is still referenced by an unfinished build"
                )
            if record["phase"] != "retiring":
                observation = (
                    SourceCaptureObservation(**record["observation"])
                    if record["observation"] is not None
                    else None
                )
                self._save(spec, "retiring", observation)
            # Exact destination ownership is checked inside drop_owned. Do not
            # require a valid content audit here: an uncertain/invalid ATTACH
            # must remain reclaimable without touching canonical source data.
            self._backend.drop_owned(spec)
            if self._backend.inspect(spec) is not None:
                raise SourceCaptureError("capture DROP is not confirmed")
            self._save(spec, "retired")
            self._reservations.release(spec)
