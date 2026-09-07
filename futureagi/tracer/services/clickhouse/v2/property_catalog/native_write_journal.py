"""Exact native INSERT attempts; local persistence, never transport or proof.

An exclusive nonblocking lease covers prepare, dispatch and completion. Only a
durably prepared attempt may be sent, once; sent is permanently proof-only.
The executor owns positive ACK/settlement and all-member coverage evidence.
No expiry, resend, deletion, or repair acknowledgement is inferred here.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import threading
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from .installation_identity import IDENTITY_FILENAME, InstallationIdentity
from .native_write_proof import _COLUMNS
from .write_admission import (
    MAX_ADMISSION_BYTES,
    WRITE_ADMISSION_FILENAME,
    WriteAdmission,
    _canonical,
)

NATIVE_WRITE_ATTEMPT_DIRECTORY = "native-write-attempts"
MAX_ATTEMPT_BYTES = 24 << 20
MAX_ATTEMPT_ROWS = 16_384
MAX_SCOPE_PENDING = 128
MAX_SCOPE_BYTES = MAX_ATTEMPT_BYTES + (1 << 20)
_FORMAT = "futureagi.property-catalog-native-write-attempt"
_SCOPE_FORMAT = "futureagi.property-catalog-native-write-scope"
_ACTIVE_TABLE = "property_catalog_activations"
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,253}\Z", re.ASCII)
_SETTINGS = frozenset(
    {
        "async_insert",
        "insert_deduplication_token",
        "insert_deduplicate",
        "insert_quorum",
        "insert_quorum_parallel",
        "insert_quorum_timeout",
        "max_execution_time",
        "insert_block_size",
        "log_comment",
    }
)
_FIELDS = frozenset(
    {
        "format",
        "version",
        "database",
        "table",
        "deduplication_token",
        "member",
        "user",
        "admission_sha256",
        "columns",
        "parameters",
        "parameters_sha256",
        "row_count",
        "sql",
        "query_id",
        "settings",
        "state",
        "acknowledgement",
        "completion",
        "record_sha256",
        "scope",
    }
)


class NativeWriteJournalError(ValueError):
    """Corrupt, conflicting, unsupported, or uncertain journal state."""


class NativeWriteJournalBusy(BlockingIOError):
    """Another process owns this exact table/token; no waiting or resend."""


def _hash(document: Any) -> str:
    return hashlib.sha256(_canonical(document)).hexdigest()


def _sha(value: Any) -> None:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise NativeWriteJournalError("invalid native attempt SHA256")


def _key(table: str, token: str) -> str:
    if (
        not isinstance(table, str)
        or table not in _COLUMNS
        or not isinstance(token, str)
        or not 1 <= len(token.encode()) <= 1024
        or any(ord(c) < 32 or ord(c) == 127 for c in token)
    ):
        raise NativeWriteJournalError(
            "native attempt requires a bare catalog table and bounded exact token"
        )
    return _hash({"table": table, "deduplication_token": token})


def _uuid(value: Any) -> str:
    if type(value) not in (str, UUID):
        raise NativeWriteJournalError("scope requires canonical nonzero UUIDs")
    parsed = UUID(str(value))
    if not parsed.int or str(parsed) != str(value):
        raise NativeWriteJournalError("scope requires canonical nonzero UUIDs")
    return str(parsed)


@dataclass(frozen=True, slots=True)
class NativeWriteScope:
    """Exact build, or explicitly targetless control; never project authorization."""

    organization_id: str
    workspace_id: str
    catalog_epoch: int
    projection_version: int
    catalog_revision: int | None
    build_token: str | None
    kind: str = "build"

    def __post_init__(self):
        for field in ("organization_id", "workspace_id"):
            if type(getattr(self, field)) is not str:
                raise NativeWriteJournalError("scope UUIDs must be canonical strings")
            _uuid(getattr(self, field))
        for value in (self.catalog_epoch, self.projection_version):
            if type(value) is not int or not 0 < value < 65536:
                raise NativeWriteJournalError(
                    "scope requires installed UInt16 identity"
                )
        if self.kind == "build":
            if (
                type(self.catalog_revision) is not int
                or not 0 < self.catalog_revision < 1 << 64
            ):
                raise NativeWriteJournalError("scope requires positive UInt64 revision")
            if type(self.build_token) is not str:
                raise NativeWriteJournalError("scope requires canonical build token")
            _uuid(self.build_token)
        elif (
            self.kind != "control"
            or self.catalog_revision is not None
            or self.build_token is not None
        ):
            raise NativeWriteJournalError("targetless control cannot invent a build")

    @classmethod
    def from_rows(cls, table, rows, identity: InstallationIdentity):
        if (
            table not in _COLUMNS
            or not isinstance(rows, (list, tuple))
            or not 1 <= len(rows) <= MAX_ATTEMPT_ROWS
        ):
            raise NativeWriteJournalError("scope requires bounded exact catalog rows")
        scopes = []
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != set(_COLUMNS[table]):
                raise NativeWriteJournalError("scope requires every exact row column")
            if (
                type(row["catalog_epoch"]) is not int
                or row["catalog_epoch"] != identity.catalog_epoch
            ):
                raise NativeWriteJournalError("scope epoch differs from installation")
            if "projection_version" in row and (
                type(row["projection_version"]) is not int
                or row["projection_version"] != identity.projection_version
            ):
                raise NativeWriteJournalError(
                    "scope projection differs from installation"
                )
            kind = "build"
            if table == "property_catalog_activation_control_events":
                revision, token = (
                    row["target_catalog_revision"],
                    row["target_build_token"],
                )
                if (
                    type(revision) is int
                    and revision == 0
                    and token in ("", str(UUID(int=0)), UUID(int=0))
                ):
                    if row["action"] != "disable":
                        raise NativeWriteJournalError(
                            "only DISABLE admits targetless control"
                        )
                    kind, revision, token = "control", None, None
            else:
                revision, token = row["catalog_revision"], row["build_token"]
            scopes.append(
                cls(
                    _uuid(row["organization_id"]),
                    _uuid(row["workspace_id"]),
                    identity.catalog_epoch,
                    identity.projection_version,
                    revision,
                    _uuid(token) if kind == "build" else None,
                    kind,
                )
            )
        if any(scope != scopes[0] for scope in scopes):
            raise NativeWriteJournalError("native attempt mixes exact write scopes")
        return scopes[0]


@dataclass(frozen=True, slots=True)
class NativePublicationBinding:
    deduplication_token: str
    parameters_sha256: str
    publication_sha256: str
    fence_sha256: str
    build_lease_sha256: str
    checkpoint_state_sha256s: tuple[str, ...]
    previous_active_sha256: str | None = None

    def __post_init__(self):
        _key(_ACTIVE_TABLE, self.deduplication_token)
        for value in (
            self.parameters_sha256,
            self.publication_sha256,
            self.fence_sha256,
            self.build_lease_sha256,
        ):
            _sha(value)
        if self.previous_active_sha256 is not None:
            _sha(self.previous_active_sha256)
        if (
            type(self.checkpoint_state_sha256s) is not tuple
            or not 1 <= len(self.checkpoint_state_sha256s) <= 128
        ):
            raise NativeWriteJournalError(
                "publication requires bounded checkpoint evidence"
            )
        for value in self.checkpoint_state_sha256s:
            _sha(value)
        if len(set(self.checkpoint_state_sha256s)) != len(
            self.checkpoint_state_sha256s
        ):
            raise NativeWriteJournalError("duplicate publication checkpoint evidence")


@dataclass(frozen=True, slots=True)
class NativeScopeClosure:
    binding: NativePublicationBinding
    generation: int
    completed_generation: int | None = None
    attempt_record_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class NativePendingAttempt:
    table: str
    deduplication_token: str
    query_id: str
    parameters_sha256: str


class NativeQuarantineReason(StrEnum):
    """Bounded local decisions, never claims about whether an INSERT executed."""

    UNRESOLVED_NATIVE_WRITE = "unresolved_native_write"
    BUILD_SUPERSEDED = "build_superseded"
    PUBLICATION_BLOCKED = "publication_blocked"


@dataclass(frozen=True, slots=True)
class NativeQuarantineIntent(NativePendingAttempt):
    """The immutable intent identity survives receipt ACK/Complete settlement."""

    intent_sha256: str

    def __post_init__(self):
        _key(self.table, self.deduplication_token)
        if type(self.query_id) is not str:
            raise NativeWriteJournalError("quarantine requires an exact query UUID")
        try:
            query = UUID(self.query_id)
        except ValueError as exc:
            raise NativeWriteJournalError("invalid quarantine query UUID") from exc
        if str(query) != self.query_id or query.version != 4:
            raise NativeWriteJournalError("invalid quarantine query UUID")
        _sha(self.parameters_sha256)
        _sha(self.intent_sha256)


@dataclass(frozen=True, slots=True)
class NativeScopeQuarantine:
    """Frozen exact BUILD binding; admission also binds the installation identity."""

    scope: NativeWriteScope
    admission_sha256: str
    generation: int
    pending: tuple[NativeQuarantineIntent, ...]
    reason: NativeQuarantineReason

    def __post_init__(self):
        if type(self.scope) is not NativeWriteScope or self.scope.kind != "build":
            raise NativeWriteJournalError("quarantine requires an exact BUILD scope")
        _sha(self.admission_sha256)
        if type(self.generation) is not int or not 0 <= self.generation < 1 << 64:
            raise NativeWriteJournalError("invalid quarantine generation")
        if (
            type(self.pending) is not tuple
            or len(self.pending) > MAX_SCOPE_PENDING
            or len(self.pending) > self.generation
            or any(type(entry) is not NativeQuarantineIntent for entry in self.pending)
        ):
            raise NativeWriteJournalError("quarantine requires bounded exact intents")
        keys = [_key(entry.table, entry.deduplication_token) for entry in self.pending]
        if keys != sorted(set(keys)):
            raise NativeWriteJournalError(
                "quarantine intents must be unique and sorted"
            )
        if type(self.reason) is not NativeQuarantineReason:
            raise NativeWriteJournalError("quarantine requires a typed bounded reason")


@dataclass(frozen=True, slots=True)
class NativeTerminalWriteIntent:
    """One frozen terminal row, not a general quarantined-write permission."""

    table: str
    deduplication_token: str
    parameters_sha256: str

    def __post_init__(self):
        if self.table not in {_ACTIVE_TABLE, "property_catalog_source_streams"}:
            raise NativeWriteJournalError("repair permits only terminal state tables")
        _key(self.table, self.deduplication_token)
        _sha(self.parameters_sha256)


@dataclass(frozen=True, slots=True)
class NativeTerminalRepairBinding:
    """Exact local repair plan; the coordinator still owns authorization."""

    quarantine: NativeScopeQuarantine
    build_lease_sha256: str
    publication_sha256: str | None
    writes: tuple[NativeTerminalWriteIntent, ...]

    def __post_init__(self):
        if type(self.quarantine) is not NativeScopeQuarantine:
            raise NativeWriteJournalError("terminal repair requires exact quarantine")
        _sha(self.build_lease_sha256)
        if self.publication_sha256 is not None:
            _sha(self.publication_sha256)
        tables = ["property_catalog_source_streams"]
        if self.publication_sha256 is not None:
            tables.insert(0, _ACTIVE_TABLE)
        if (
            type(self.writes) is not tuple
            or any(
                type(write) is not NativeTerminalWriteIntent for write in self.writes
            )
            or [write.table for write in self.writes] != tables
        ):
            raise NativeWriteJournalError("repair requires exact ordered terminal rows")
        for write in self.writes:
            if write.deduplication_token != (
                "property-catalog-terminal-repair-v1:"
                f"{self.quarantine.scope.build_token}:{write.table}"
            ):
                raise NativeWriteJournalError(
                    "terminal repair token differs from build"
                )


@dataclass(frozen=True, slots=True)
class NativeTerminalRepairReceipt:
    binding: NativeTerminalRepairBinding
    attempt_record_sha256s: tuple[str, ...]

    def __post_init__(self):
        if (
            type(self.binding) is not NativeTerminalRepairBinding
            or type(self.attempt_record_sha256s) is not tuple
            or len(self.attempt_record_sha256s) != len(self.binding.writes)
        ):
            raise NativeWriteJournalError("repair receipt requires all terminal writes")
        for digest in self.attempt_record_sha256s:
            _sha(digest)


def native_insert_sql(
    database: str, table: str, columns: Sequence[str], *, quorum: int = 0
) -> str:
    from .publisher import require_catalog_database

    require_catalog_database(database)
    if table not in _COLUMNS or tuple(columns) != _COLUMNS[table]:
        raise NativeWriteJournalError("native INSERT requires exact pinned columns")
    if type(quorum) is not int or not 0 <= quorum < 1 << 64:
        raise NativeWriteJournalError("native INSERT quorum requires an exact UInt64")
    return (
        f"INSERT INTO {database}.{table} ({', '.join(columns)}) "
        f"SETTINGS async_insert=0, insert_quorum={quorum}, "
        "insert_quorum_parallel=1 VALUES"
    )


def _cell(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> list:
    if depth > 16:
        raise NativeWriteJournalError("native parameter nesting exceeds bound")
    if budget is not None:
        budget[1] += 1
        if budget[1] > 1_048_576:
            raise NativeWriteJournalError("native parameter element bound exceeded")
    if value is None:
        result = ["null"]
    elif type(value) is str:
        if len(value.encode("utf-8")) > MAX_ATTEMPT_BYTES:
            raise NativeWriteJournalError("native string exceeds attempt bound")
        result = ["str", value]
    elif type(value) is int and -(1 << 63) <= value < 1 << 64:
        result = ["int64" if value < 0 else "uint64", str(value)]
    elif type(value) is UUID:
        result = ["uuid", str(value)]
    elif type(value) is datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise NativeWriteJournalError(
                "native datetime must be UTC with microsecond precision"
            )
        result = [
            "datetime",
            value.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
        ]
    elif type(value) in (tuple, list) and len(value) <= MAX_ATTEMPT_ROWS:
        if budget is not None:
            budget[0] += 12 + len(value)
        return [
            "array",
            [_cell(item, depth=depth + 1, budget=budget) for item in value],
        ]
    else:
        raise NativeWriteJournalError(
            "unsupported native parameter type or integer range"
        )
    if budget is not None:
        budget[0] += len(_canonical(result))
        if budget[0] > MAX_ATTEMPT_BYTES:
            raise NativeWriteJournalError("native parameters exceed attempt size")
    return result


def _uncell(value: Any, *, depth: int = 0) -> Any:
    if type(value) is not list or not value or depth > 16:
        raise NativeWriteJournalError("invalid typed native parameter")
    tag = value[0]
    if tag == "null" and len(value) == 1:
        return None
    if len(value) != 2:
        raise NativeWriteJournalError("invalid typed native parameter arity")
    raw = value[1]
    try:
        if tag == "str" and type(raw) is str:
            result = raw
        elif tag in {"uint64", "int64"} and type(raw) is str and len(raw) <= 20:
            result = int(raw)
        elif tag == "uuid" and type(raw) is str:
            result = UUID(raw)
        elif tag == "datetime" and type(raw) is str:
            result = datetime.fromisoformat(raw)
        elif tag == "array" and type(raw) is list and len(raw) <= MAX_ATTEMPT_ROWS:
            result = tuple(_uncell(item, depth=depth + 1) for item in raw)
        else:
            raise ValueError
        if _cell(result, depth=depth) != value:
            raise ValueError
        return result
    except (ValueError, TypeError, OverflowError) as exc:
        raise NativeWriteJournalError("noncanonical typed native parameter") from exc


def _parameters(columns: Sequence[str], rows: Sequence[Mapping]) -> list:
    if (
        not isinstance(rows, (tuple, list))
        or not 1 <= len(rows) <= MAX_ATTEMPT_ROWS
        or not isinstance(columns, (tuple, list))
        or not columns
        or any(type(column) is not str for column in columns)
        or len(set(columns)) != len(columns)
    ):
        raise NativeWriteJournalError("native attempt row/column bound is invalid")
    result, size, budget = [], 0, [0, 0]
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != set(columns):
            raise NativeWriteJournalError(
                "native row does not exactly match ordered columns"
            )
        encoded = [_cell(row[column], budget=budget) for column in columns]
        size += len(_canonical(encoded))
        if size > MAX_ATTEMPT_BYTES:
            raise NativeWriteJournalError("native parameters exceed attempt size")
        result.append(encoded)
    return result


def native_parameters_sha256(columns: Sequence[str], rows: Sequence[Mapping]) -> str:
    if tuple(columns) not in _COLUMNS.values():
        raise NativeWriteJournalError("native parameters require exact pinned columns")
    return _hash({"columns": list(columns), "parameters": _parameters(columns, rows)})


def _settings(settings: Mapping, token: str, digest: str) -> dict:
    if not isinstance(settings, Mapping) or not set(settings).issubset(_SETTINGS):
        raise NativeWriteJournalError(
            "unreviewed native settings (credentials are not journal settings)"
        )
    result = dict(settings)
    for value in result.values():
        if not (
            (type(value) is str and len(value.encode()) <= 1024)
            or (type(value) is int and -(1 << 63) <= value < 1 << 64)
        ):
            raise NativeWriteJournalError(
                "native settings require bounded exact strings or integers"
            )
    if result.get("async_insert") not in (0, "0"):
        raise NativeWriteJournalError("native attempts require synchronous INSERT")
    if result.get("insert_quorum_parallel") not in (1, "1"):
        raise NativeWriteJournalError(
            "native settings contradict pinned parallel quorum"
        )
    for key, expected in (
        ("insert_deduplication_token", token),
        ("log_comment", digest),
    ):
        if key in result and result[key] != expected:
            raise NativeWriteJournalError(
                "native settings conflict with exact token/payload binding"
            )
        result[key] = expected
    return result


def _evidence(document: dict, evidence: Any, *, complete: bool) -> None:
    common = {"kind", "query_id", "parameters_sha256", "admission_sha256"}
    fields = common | ({"members", "settlement"} if complete else {"written_rows"})
    if type(evidence) is not dict or set(evidence) != fields:
        raise NativeWriteJournalError(
            "attempt requires full bounded completion/ACK evidence"
        )
    for key in ("query_id", "parameters_sha256", "admission_sha256"):
        if evidence[key] != document[key]:
            raise NativeWriteJournalError("native evidence is bound to another attempt")
    if complete:
        members = evidence["members"]
        if (
            evidence["kind"] != "all_member_coverage"
            or type(members) is not list
            or not members
            or any(type(m) is not str or not _NAME.fullmatch(m) for m in members)
            or members != sorted(set(members))
            or document["member"] not in members
            or evidence["settlement"] != document["acknowledgement"]["kind"]
        ):
            raise NativeWriteJournalError("invalid all-member completion evidence")
    elif (
        evidence["kind"] not in {"native_end_of_stream", "query_log_finish"}
        or type(evidence["written_rows"]) is not int
        or evidence["written_rows"] != document["row_count"]
    ):
        raise NativeWriteJournalError(
            "native acknowledgement did not finish exact rows"
        )
    if len(_canonical(evidence)) > 32768:
        raise NativeWriteJournalError("native evidence exceeds bound")


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) in (list, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True, init=False)
class NativeWriteAttempt(Mapping[str, Any]):
    _raw: bytes
    _data: Mapping

    def __init__(self, raw: bytes):
        try:
            if (
                type(raw) is not bytes
                or not 0 < len(raw) <= MAX_ATTEMPT_BYTES
                or not raw.endswith(b"\n")
            ):
                raise ValueError
            document = json.loads(raw)
            if (
                type(document) is not dict
                or set(document) != _FIELDS
                or _canonical(document) + b"\n" != raw
            ):
                raise ValueError
            expected = document["record_sha256"]
            _sha(expected)
            if (
                _hash({k: v for k, v in document.items() if k != "record_sha256"})
                != expected
            ):
                raise ValueError
            if (
                document["format"] != _FORMAT
                or type(document["version"]) is not int
                or document["version"] != 2
            ):
                raise ValueError
            _key(document["table"], document["deduplication_token"])
            for key in ("admission_sha256", "parameters_sha256"):
                _sha(document[key])
            for key in ("member", "user"):
                if type(document[key]) is not str or not _NAME.fullmatch(document[key]):
                    raise ValueError
            query = UUID(document["query_id"])
            if str(query) != document["query_id"] or query.version != 4:
                raise ValueError
            columns, parameters = document["columns"], document["parameters"]
            if type(columns) is not list or type(parameters) is not list:
                raise ValueError
            if document["sql"] != native_insert_sql(
                document["database"],
                document["table"],
                columns,
                quorum=document["settings"].get("insert_quorum"),
            ):
                raise ValueError
            if (
                type(document["row_count"]) is not int
                or not 1 <= len(parameters) == document["row_count"] <= MAX_ATTEMPT_ROWS
            ):
                raise ValueError
            if any(
                type(row) is not list or len(row) != len(columns) for row in parameters
            ):
                raise ValueError
            if (
                _hash({"columns": columns, "parameters": parameters})
                != document["parameters_sha256"]
            ):
                raise ValueError
            values = tuple(tuple(_uncell(cell) for cell in row) for row in parameters)
            scope = NativeWriteScope(**document["scope"])
            decoded_rows = tuple(dict(zip(columns, row, strict=True)) for row in values)
            if (
                NativeWriteScope.from_rows(document["table"], decoded_rows, scope)
                != scope
            ):
                raise ValueError
            if document["settings"] != _settings(
                document["settings"],
                document["deduplication_token"],
                document["parameters_sha256"],
            ):
                raise ValueError
            state = document["state"]
            if state not in {"prepared", "sent", "acknowledged", "complete"}:
                raise ValueError
            if state in {"prepared", "sent"}:
                if (
                    document["acknowledgement"] is not None
                    or document["completion"] is not None
                ):
                    raise ValueError
            else:
                _evidence(document, document["acknowledgement"], complete=False)
                if state == "complete":
                    _evidence(document, document["completion"], complete=True)
                elif document["completion"] is not None:
                    raise ValueError
            document["parameters"] = values
            document["rows"] = decoded_rows
            object.__setattr__(self, "_raw", raw)
            object.__setattr__(self, "_data", _freeze(document))
        except (
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            UnicodeError,
            RecursionError,
        ) as exc:
            raise NativeWriteJournalError("invalid canonical native attempt") from exc

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    @property
    def state(self):
        return self["state"]

    @property
    def query_id(self):
        return self["query_id"]

    @property
    def user(self):
        return self["user"]

    @property
    def sql(self):
        return self["sql"]

    @property
    def row_count(self):
        return self["row_count"]

    @property
    def acknowledgement(self):
        return self["acknowledgement"]

    @property
    def columns(self):
        return self["columns"]

    @property
    def rows(self):
        return self["rows"]

    @property
    def parameters(self):
        return self["parameters"]

    @property
    def parameters_sha256(self):
        return self["parameters_sha256"]

    @property
    def settings(self):
        return self["settings"]

    def as_mapping(self) -> dict:
        return _thaw(self._data)

    def encode(self) -> bytes:
        return self._raw


def _record(document: dict) -> NativeWriteAttempt:
    document = {k: v for k, v in document.items() if k != "record_sha256"}
    return NativeWriteAttempt(
        _canonical({**document, "record_sha256": _hash(document)}) + b"\n"
    )


def _intent_sha256(attempt: NativeWriteAttempt) -> str:
    return _hash(
        {
            key: value
            for key, value in json.loads(attempt.encode()).items()
            if key not in {"state", "acknowledgement", "completion", "record_sha256"}
        }
    )


def _atomic(directory: int, name: str, raw: bytes) -> None:
    temporary = ".native-attempt-" + uuid4().hex + ".tmp"
    fd = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=directory,
    )
    try:
        with os.fdopen(fd, "wb") as target:
            _private(target.fileno())
            if target.write(raw) != len(raw):
                raise NativeWriteJournalError("short native attempt write")
            target.flush()
            os.fsync(target.fileno())
        os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _private(fd: int, *, directory: bool = False) -> None:
    info = os.fstat(fd)
    if (
        not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or (not directory and info.st_nlink != 1)
    ):
        raise NativeWriteJournalError(
            "native journal requires private owned single-link regular files/directories"
        )


def _read(fd: int, name: str, limit: int, *, private: bool = True) -> bytes:
    file = os.open(
        name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
    )
    with os.fdopen(file, "rb") as source:
        if private:
            _private(source.fileno())
        else:
            from .write_admission import _private as private_descriptor

            private_descriptor(source.fileno())
        size = os.fstat(source.fileno()).st_size
        if not 0 < size <= limit:
            raise NativeWriteJournalError("native journal file is empty or oversized")
        raw = source.read(limit + 1)
        if len(raw) != size:
            raise NativeWriteJournalError(
                "native journal read is incomplete or changed"
            )
        return raw


def _installation(parent: int) -> tuple[InstallationIdentity, WriteAdmission]:
    identity = InstallationIdentity.decode(
        _read(parent, IDENTITY_FILENAME, 4096, private=False)
    )
    admission = WriteAdmission.decode(
        _read(parent, WRITE_ADMISSION_FILENAME, MAX_ADMISSION_BYTES, private=False)
    )
    if admission.installation_sha256 != json.loads(identity.encode())[
        "identity_sha256"
    ] or (admission.database, admission.environment) != (
        identity.target_database,
        identity.environment,
    ):
        raise NativeWriteJournalError(
            "native journal installation/admission binding differs"
        )
    return identity, admission


class NativeWriteJournal:
    """Private fixed child of an existing installation; no transport or garbage collection."""

    MAX_RECOVERY_SCOPES = 32
    _MAX_RECOVERY_BYTES = 64 << 10

    def __init__(self, installation_directory: str | Path):
        path = Path(installation_directory)
        if not path.is_absolute() or path == Path(path.anchor):
            raise NativeWriteJournalError(
                "native journal requires an existing absolute installation directory"
            )
        self._guard = threading.Lock()
        self._parent = self._directory = None
        parent = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        )
        try:
            _private(parent, directory=True)
            self.identity, self.admission = _installation(parent)
            try:
                os.mkdir(NATIVE_WRITE_ATTEMPT_DIRECTORY, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            directory = os.open(
                NATIVE_WRITE_ATTEMPT_DIRECTORY,
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_NOFOLLOW
                | os.O_CLOEXEC
                | os.O_NONBLOCK,
                dir_fd=parent,
            )
            try:
                _private(directory, directory=True)
                os.fsync(parent)
            except BaseException:
                os.close(directory)
                raise
            self._parent, self._directory = parent, directory
        except BaseException:
            os.close(parent)
            raise

    def __enter__(self):
        if self._parent is None:
            raise NativeWriteJournalError("native journal is closed")
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        with self._guard:
            for key in ("_directory", "_parent"):
                fd = getattr(self, key)
                if fd is not None:
                    os.close(fd)
                    setattr(self, key, None)

    @contextmanager
    def _locked(self, key):
        with self._guard:
            if self._directory is None:
                raise NativeWriteJournalError("native journal is closed")
            parent = os.dup(self._parent)
            try:
                directory = os.dup(self._directory)
            except BaseException:
                os.close(parent)
                raise
        lock = None
        try:
            lock = os.open(
                key + ".lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            _private(lock)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise NativeWriteJournalBusy(
                    "native table/token attempt already owned"
                ) from exc
            yield parent, directory, lock
        finally:
            if lock is not None:
                os.close(lock)
            os.close(directory)
            os.close(parent)

    def session(self, table: str, dedup_token: str):
        """Diagnostic LOAD ONLY. Managed mutations require scope(...).session(...)."""
        return self._session(table, dedup_token)

    @contextmanager
    def _session(self, table, dedup_token, scoped=None):
        key = _key(table, dedup_token)
        with self._locked(key) as (parent, directory, lock):
            session = NativeWriteSession(
                parent,
                directory,
                lock,
                key,
                table,
                dedup_token,
                self.identity,
                self.admission,
                scoped,
            )
            try:
                session._check()
                yield session
            finally:
                session._open = False

    def _recovery_workspace(
        self, *, organization_id, workspace_id, catalog_epoch, projection_version
    ):
        # Reuse the strict tenant/installation types without inventing a BUILD.
        tenant = NativeWriteScope(
            organization_id,
            workspace_id,
            catalog_epoch,
            projection_version,
            None,
            None,
            kind="control",
        )
        if (tenant.catalog_epoch, tenant.projection_version) != (
            self.identity.catalog_epoch,
            self.identity.projection_version,
        ):
            raise NativeWriteJournalError(
                "recovery workspace differs from installation"
            )
        return {
            field: getattr(tenant, field)
            for field in (
                "organization_id",
                "workspace_id",
                "catalog_epoch",
                "projection_version",
            )
        }

    def _recovery_scope(self, scoped, *, idle=False):
        if (
            type(scoped) is not NativeWriteScopeSession
            or scoped._journal is not self
            or scoped.scope.kind != "build"
        ):
            raise NativeWriteJournalError(
                "recovery pointer requires this journal's BUILD lock"
            )
        scoped._idle() if idle else scoped._check()
        workspace = self._recovery_workspace(
            **{
                field: getattr(scoped.scope, field)
                for field in (
                    "organization_id",
                    "workspace_id",
                    "catalog_epoch",
                    "projection_version",
                )
            }
        )
        return workspace, scoped._read_index()

    @contextmanager
    def _recovery_index(self, workspace):
        # Admission belongs in the record, not the filename: a mismatched
        # installation must not discover a different, apparently empty index.
        key = _hash(workspace) + ".recovery"
        base = {
            "format": "futureagi.property-catalog-native-workspace-recovery",
            "version": 1,
            "workspace": workspace,
            "admission_sha256": hashlib.sha256(self.admission.encode()).hexdigest(),
        }
        with self._locked(key) as (parent, directory, lock):
            guard = NativeWriteSession(
                parent, directory, lock, key, None, None, self.identity, self.admission
            )
            try:
                guard._check()
                marker = os.pread(lock, 2, 0)
                if marker not in (b"", b"1"):
                    raise NativeWriteJournalError(
                        "invalid workspace recovery lock marker"
                    )
                try:
                    raw = _read(directory, key + "-index", self._MAX_RECOVERY_BYTES)
                except FileNotFoundError:
                    if marker:
                        raise NativeWriteJournalError(
                            "missing initialized workspace recovery index"
                        ) from None
                    scopes = ()
                else:
                    scopes = self._decode_recovery(raw, base)
                yield guard, key, base, scopes
            finally:
                guard._open = False

    def _decode_recovery(self, raw, base):
        try:
            document = json.loads(raw)
            if (
                type(document) is not dict
                or set(document) != set(base) | {"scopes", "sha256"}
                or _canonical(document) + b"\n" != raw
                or _canonical({key: document[key] for key in base}) != _canonical(base)
                or document["sha256"]
                != _hash(
                    {key: value for key, value in document.items() if key != "sha256"}
                )
                or type(document["scopes"]) is not list
                or len(document["scopes"]) > self.MAX_RECOVERY_SCOPES
            ):
                raise ValueError
            scopes = tuple(NativeWriteScope(**entry) for entry in document["scopes"])
            if _canonical([asdict(scope) for scope in scopes]) != _canonical(
                document["scopes"]
            ):
                raise ValueError
            keys = [_hash(asdict(scope)) for scope in scopes]
            if keys != sorted(set(keys)) or any(
                scope.kind != "build"
                or any(
                    getattr(scope, field) != value
                    for field, value in base["workspace"].items()
                )
                for scope in scopes
            ):
                raise ValueError
            return scopes
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise NativeWriteJournalError(
                "invalid canonical workspace recovery index"
            ) from exc

    @staticmethod
    def _seal_recovery(guard):
        # Reuse the persistent lock inode to distinguish an absent legacy index
        # from a deleted initialized one. No extra index/anchor layer is needed.
        guard._check()
        if os.pwrite(guard._lock, b"1", 0) != 1:
            raise NativeWriteJournalError("short workspace recovery lock marker")
        os.fsync(guard._lock)

    def _save_recovery(self, guard, key, base, scopes):
        body = {
            **base,
            "scopes": [
                asdict(scope)
                for scope in sorted(scopes, key=lambda s: _hash(asdict(s)))
            ],
        }
        raw = _canonical({**body, "sha256": _hash(body)}) + b"\n"
        if len(raw) > self._MAX_RECOVERY_BYTES:
            raise NativeWriteJournalError("workspace recovery index exceeds byte bound")
        self._decode_recovery(raw, base)
        guard._check()
        _atomic(guard._directory, key + "-index", raw)
        self._seal_recovery(guard)

    def recovery_scopes(
        self, *, organization_id, workspace_id, catalog_epoch, projection_version
    ) -> tuple[NativeWriteScope, ...]:
        """Read only this bounded workspace index; never scan or adopt receipts.

        These are candidates, not failure/proof declarations. The caller takes
        its workspace lock, then each BUILD lock, and reads that scope's journal.
        An uninitialized legacy index is empty; no backfill is inferred. A missing
        initialized index is corruption, never evidence of an empty workspace.
        """
        workspace = self._recovery_workspace(
            organization_id=organization_id,
            workspace_id=workspace_id,
            catalog_epoch=catalog_epoch,
            projection_version=projection_version,
        )
        with self._recovery_index(workspace) as (_, _, _, scopes):
            return scopes

    def track_recovery_scope(self, scoped) -> None:
        """Fsync the exact BUILD pointer before REGISTERING; no copied operation state.

        Requires this journal's live BUILD lock; an attempt lock may also be held.
        No revision, receipt, quarantine, or publication state is changed here.
        """
        workspace, _ = self._recovery_scope(scoped)
        with self._recovery_index(workspace) as (guard, key, base, scopes):
            if scoped.scope in scopes:
                # Also complete a prior rename whose directory fsync failed.
                guard._check()
                os.fsync(guard._directory)
                self._seal_recovery(guard)
                return
            if len(scopes) >= self.MAX_RECOVERY_SCOPES:
                raise NativeWriteJournalError("workspace recovery scope bound exceeded")
            self._save_recovery(guard, key, base, (*scopes, scoped.scope))

    def clear_recovery_scope(
        self, scoped, *, expected_generation, terminal_receipt=None
    ) -> bool:
        """CAS-remove one pointer, not any receipts; stale captures return False.

        Caller holds workspace then idle BUILD lock. An empty current index is
        sufficient. Otherwise the caller must freshly prove terminal repair and
        supply its exact archived receipt; this method validates local bindings
        only and never substitutes for external proof or settles pending writes.
        """
        workspace, document = self._recovery_scope(scoped, idle=True)
        if (
            type(expected_generation) is not int
            or not 0 <= expected_generation < 1 << 64
        ):
            raise NativeWriteJournalError("recovery removal requires exact generation")
        if document["generation"] != expected_generation:
            return False
        if terminal_receipt is not None:
            if (
                type(terminal_receipt) is not NativeTerminalRepairReceipt
                or "terminal_repair" not in document
            ):
                raise NativeWriteJournalError(
                    "recovery removal requires exact terminal receipt"
                )
            binding, attempts, completion = scoped._terminal_repair(
                document["terminal_repair"]
            )
            if completion != terminal_receipt or set(attempts) & set(
                document["pending"]
            ):
                raise NativeWriteJournalError(
                    "terminal repair is incomplete or differs"
                )
            for write, digest in zip(
                binding.writes, completion.attempt_record_sha256s, strict=True
            ):
                with scoped.session(write.table, write.deduplication_token) as session:
                    attempt = session.load()
                    if (
                        attempt is None
                        or attempt.state != "complete"
                        or attempt["record_sha256"] != digest
                    ):
                        raise NativeWriteJournalError(
                            "terminal recovery receipt is missing or changed"
                        )
        elif document["pending"]:
            return False
        with self._recovery_index(workspace) as (guard, key, base, scopes):
            if scoped.scope not in scopes:
                return False
            self._save_recovery(
                guard, key, base, tuple(s for s in scopes if s != scoped.scope)
            )
            return True

    @contextmanager
    def scope(self, scope: NativeWriteScope, *, create: bool = False):
        if type(scope) is not NativeWriteScope or type(create) is not bool:
            raise NativeWriteJournalError("managed session requires an exact scope")
        if (scope.catalog_epoch, scope.projection_version) != (
            self.identity.catalog_epoch,
            self.identity.projection_version,
        ):
            raise NativeWriteJournalError("scope differs from installed identity")
        key = (
            _hash(
                {
                    "scope": asdict(scope),
                    "admission_sha256": hashlib.sha256(
                        self.admission.encode()
                    ).hexdigest(),
                }
            )
            + ".scope"
        )
        with self._locked(key) as (parent, directory, lock):
            scoped = NativeWriteScopeSession(self, parent, directory, lock, key, scope)
            try:
                scoped._initialize(create=create)
                yield scoped
            finally:
                scoped._open = False


class NativeWriteScopeSession:
    """One nonblocking scope lock. This is local evidence, not authorization or proof."""

    def __init__(self, journal, parent, directory, lock, key, scope):
        self.scope, self._journal, self._directory, self._key = (
            scope,
            journal,
            directory,
            key,
        )
        self._open, self._active, self._thread = True, False, threading.get_ident()
        self._guard = NativeWriteSession(
            parent,
            directory,
            lock,
            key,
            None,
            None,
            journal.identity,
            journal.admission,
        )
        self._base = {
            "format": _SCOPE_FORMAT,
            "version": 1,
            "scope": asdict(scope),
            "admission_sha256": hashlib.sha256(journal.admission.encode()).hexdigest(),
        }

    def _check(self):
        if not self._open or threading.get_ident() != self._thread:
            raise NativeWriteJournalError(
                "scope requires its live owning thread/session"
            )
        self._guard._check()
        if (
            _read(self._directory, self._key + ".anchor", 4096)
            != _canonical(self._base) + b"\n"
        ):
            raise NativeWriteJournalError("scope anchor changed")

    def _idle(self):
        self._check()
        if self._active:
            raise NativeWriteJournalError(
                "reuse scope after closing its attempt session; no nested locks"
            )

    def _initialize(self, *, create):
        self._guard._check()
        try:
            anchor = _read(self._directory, self._key + ".anchor", 4096)
        except FileNotFoundError:
            anchor = None
        if anchor is not None and anchor != _canonical(self._base) + b"\n":
            raise NativeWriteJournalError("scope anchor conflicts")
        try:
            document = self._read_index()
        except FileNotFoundError:
            if anchor is not None or not create:
                raise NativeWriteJournalError(
                    "missing initialized scope index; not empty"
                ) from None
            document = {**self._base, "generation": 0, "pending": {}, "closure": None}
            self._save(document)
        if anchor is None:
            if (
                not create
                or document["generation"] != 0
                or document["pending"]
                or document["closure"] is not None
                or document.get("quarantine") is not None
            ):
                raise NativeWriteJournalError(
                    "missing scope anchor; not a fresh initialization"
                )
            _atomic(
                self._directory, self._key + ".anchor", _canonical(self._base) + b"\n"
            )
        self._check()

    def _decode_index(self, raw):
        try:
            document = json.loads(raw)
            # v4/v5/v6 preserve the v1/v2/v3 fields, respectively, but READY
            # Complete remains pending until fresh proof. Older readers reject
            # these versions instead of forgetting the proof obligation.
            # The immutable v1 scope anchor is never upgraded or recreated.
            if (
                type(document) is not dict
                or type(document.get("version")) is not int
                or document["version"] not in {1, 2, 3, 4, 5, 6}
            ):
                raise ValueError
            fields = set(self._base) | {"generation", "pending", "closure", "sha256"}
            quarantined = document["version"] in {2, 3, 5, 6}
            terminal = document["version"] in {3, 6}
            if quarantined:
                fields.add("quarantine")
            if terminal:
                fields.add("terminal_repair")
            if set(document) != fields or _canonical(document) + b"\n" != raw:
                raise ValueError
            if _canonical({key: document[key] for key in self._base}) != _canonical(
                {**self._base, "version": document["version"]}
            ):
                raise ValueError
            if document["sha256"] != _hash(
                {k: v for k, v in document.items() if k != "sha256"}
            ):
                raise ValueError
            if (
                type(document["generation"]) is not int
                or not 0 <= document["generation"] < 1 << 64
            ):
                raise ValueError
            repair = document.get("terminal_repair")
            repair_attempts = {}
            if repair is not None:
                binding, repair_attempts, _ = self._terminal_repair(repair)
                if binding.quarantine != self._quarantine(document["quarantine"]):
                    raise ValueError
            elif terminal:
                raise ValueError
            pending = document["pending"]
            if type(pending) is not dict or len(pending) > MAX_SCOPE_PENDING + len(
                repair_attempts
            ):
                raise ValueError
            registering = 0
            repair_registering = 0
            for key, entry in pending.items():
                if (
                    type(entry) is not dict
                    or set(entry)
                    != {
                        "table",
                        "deduplication_token",
                        "query_id",
                        "parameters_sha256",
                        "intent_sha256",
                        "stage",
                        "prepared",
                    }
                    or key != _key(entry["table"], entry["deduplication_token"])
                ):
                    raise ValueError
                if (
                    str(UUID(entry["query_id"])) != entry["query_id"]
                    or UUID(entry["query_id"]).version != 4
                ):
                    raise ValueError
                _sha(entry["parameters_sha256"])
                _sha(entry["intent_sha256"])
                if entry["stage"] == "registering":
                    if key in repair_attempts:
                        repair_registering += 1
                    else:
                        registering += 1
                    attempt = NativeWriteAttempt(_canonical(entry["prepared"]) + b"\n")
                    if (
                        attempt.state != "prepared"
                        or _thaw(attempt["scope"]) != asdict(self.scope)
                        or attempt["admission_sha256"] != self._base["admission_sha256"]
                    ):
                        raise ValueError
                    self._match(entry, attempt)
                    if key in repair_attempts:
                        self._validate_terminal_attempt(attempt, binding)
                elif entry["stage"] != "ready" or entry["prepared"] is not None:
                    raise ValueError
            if (
                registering > 1
                or repair_registering > 1
                or document["generation"] < len(pending)
            ):
                raise ValueError
            if (
                repair is not None
                and repair["completion"] is not None
                and (pending.keys() & repair_attempts.keys())
            ):
                raise ValueError
            closure = document["closure"]
            if closure is not None:
                parsed = self._closure(closure)
                if (
                    self.scope.kind != "build"
                    or parsed.generation > document["generation"]
                ):
                    raise ValueError
                if parsed.completed_generation is None:
                    if document["generation"] - len(repair_attempts) not in {
                        parsed.generation,
                        parsed.generation + 1,
                    } or any(
                        not self._is_publication(entry, parsed.binding)
                        for key, entry in pending.items()
                        if key not in repair_attempts
                    ):
                        raise ValueError
                elif parsed.completed_generation > document["generation"]:
                    raise ValueError
            if quarantined:
                quarantine = self._quarantine(document["quarantine"])
                if (
                    quarantine.scope != self.scope
                    or quarantine.admission_sha256 != self._base["admission_sha256"]
                    or quarantine.generation + len(repair_attempts)
                    != document["generation"]
                ):
                    raise ValueError
                captured = {
                    _key(entry.table, entry.deduplication_token): asdict(entry)
                    for entry in quarantine.pending
                }
                if captured.keys() & repair_attempts.keys():
                    raise ValueError
                captured.update(repair_attempts)
                # Original intents stay frozen. Only the two separately bound
                # terminal intents may register; settlement may remove references.
                if any(
                    key not in captured
                    or self._quarantine_intent(entry) != captured[key]
                    for key, entry in pending.items()
                ):
                    raise ValueError
            return document
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
            raise NativeWriteJournalError("invalid canonical scope index") from exc

    def _read_index(self):
        self._guard._check()
        return self._decode_index(
            _read(self._directory, self._key + ".json", MAX_SCOPE_BYTES)
        )

    def _save(self, document):
        body = {key: value for key, value in document.items() if key != "sha256"}
        raw = _canonical({**body, "sha256": _hash(body)}) + b"\n"
        if len(raw) > MAX_SCOPE_BYTES:
            raise NativeWriteJournalError("scope index exceeds bound")
        self._decode_index(raw)
        self._guard._check()
        _atomic(self._directory, self._key + ".json", raw)

    @staticmethod
    def _match(entry, attempt):
        if any(
            entry[key] != attempt[key]
            for key in ("table", "deduplication_token", "query_id", "parameters_sha256")
        ) or entry["intent_sha256"] != _intent_sha256(attempt):
            raise NativeWriteJournalError(
                "scope index conflicts with exact frozen intent"
            )

    @staticmethod
    def _is_publication(entry, binding):
        return (
            entry["table"] == _ACTIVE_TABLE
            and entry["deduplication_token"] == binding.deduplication_token
            and entry["parameters_sha256"] == binding.parameters_sha256
        )

    @staticmethod
    def _validate_active(attempt, binding):
        if (
            not NativeWriteScopeSession._is_publication(attempt, binding)
            or attempt.row_count != 1
            or attempt.rows[0]["status"] != "active"
            or attempt.rows[0]["revision_fence_sha256"] != binding.fence_sha256
        ):
            raise NativeWriteJournalError(
                "publication binding requires one exact ACTIVE with matching fence"
            )

    @staticmethod
    def _closure(document):
        if type(document) is not dict or set(document) != {
            "binding",
            "generation",
            "completed_generation",
            "attempt_record_sha256",
        }:
            raise ValueError("invalid closure")
        binding = dict(document["binding"])
        binding["checkpoint_state_sha256s"] = tuple(binding["checkpoint_state_sha256s"])
        result = NativeScopeClosure(
            NativePublicationBinding(**binding),
            document["generation"],
            document["completed_generation"],
            document["attempt_record_sha256"],
        )
        if type(result.generation) is not int or not 0 <= result.generation < 1 << 64:
            raise ValueError("invalid closure generation")
        if result.completed_generation is None:
            if result.attempt_record_sha256 is not None:
                raise ValueError("premature closure completion")
        else:
            if (
                type(result.completed_generation) is not int
                or result.completed_generation
                not in {result.generation, result.generation + 1}
                or result.completed_generation >= 1 << 64
            ):
                raise ValueError("invalid completed generation")
            _sha(result.attempt_record_sha256)
        return result

    @contextmanager
    def session(self, table, dedup_token, *, terminal_repair=None):
        self._idle()
        if terminal_repair is not None:
            self._require_terminal_binding(terminal_repair)
            if not any(
                write.table == table and write.deduplication_token == dedup_token
                for write in terminal_repair.writes
            ):
                raise NativeWriteJournalError("session is not an exact terminal intent")
        self._active = True
        try:
            with self._journal._session(table, dedup_token, self) as session:
                session._terminal_repair = terminal_repair
                yield session
        finally:
            self._active = False

    @property
    def generation(self):
        self._check()
        return self._read_index()["generation"]

    def reservation_seed(self):
        """Original typed reservation and token; not receipt/completion evidence.

        Lives in the same native journal and survives pending-reference removal.
        No database visibility, file inventory scan, or duplicated repair queue.
        """
        self._idle()
        try:
            raw = _read(self._directory, self._key + ".reservation", MAX_ATTEMPT_BYTES)
        except FileNotFoundError:
            return None
        document = json.loads(raw)
        base = {**self._base, "format": "futureagi.property-catalog-native-reservation"}
        if (
            type(document) is not dict
            or set(document) != set(base) | {"token", "parameters", "parameters_sha256"}
            or any(document[k] != v for k, v in base.items())
            or _canonical(document) + b"\n" != raw
            or type(document["parameters"]) is not list
            or len(document["parameters"]) != 1
            or type(document["parameters"][0]) is not list
            or len(document["parameters"][0])
            != len(_COLUMNS["property_catalog_source_streams"])
        ):
            raise NativeWriteJournalError("original reservation seed binding differs")
        table = "property_catalog_source_streams"
        _key(table, document["token"])
        row = dict(
            zip(_COLUMNS[table], map(_uncell, document["parameters"][0]), strict=True)
        )
        if (
            NativeWriteScope.from_rows(table, (row,), self._journal.identity)
            != self.scope
            or native_parameters_sha256(_COLUMNS[table], (row,))
            != document["parameters_sha256"]
            or not self._is_initial_reservation(table, (row,))
        ):
            raise NativeWriteJournalError("original reservation seed parameters differ")
        return row, document["token"]

    def _is_initial_reservation(self, table, rows):
        return (
            self.scope.kind == "build"
            and table == "property_catalog_source_streams"
            and len(rows) == 1
            and rows[0]["source_adapter"] == "system_manifest"
            and type(rows[0]["envelope_version"]) is int
            and rows[0]["envelope_version"] == 0
            and _uuid(rows[0]["producer_stream_id"]) == self.scope.build_token
            and rows[0]["status"] == "open"
            and type(rows[0]["_version"]) is int
            and rows[0]["_version"] == 1
        )

    def _retain_reservation_seed(self, attempt):
        if not self._is_initial_reservation(attempt["table"], attempt.rows):
            return
        raw = (
            _canonical(
                {
                    **self._base,
                    "format": "futureagi.property-catalog-native-reservation",
                    "token": attempt["deduplication_token"],
                    "parameters": _parameters(attempt.columns, attempt.rows),
                    "parameters_sha256": attempt.parameters_sha256,
                }
            )
            + b"\n"
        )
        name = self._key + ".reservation"
        try:
            previous = _read(self._directory, name, MAX_ATTEMPT_BYTES)
        except FileNotFoundError:
            _atomic(self._directory, name, raw)
            return
        if previous != raw:
            raise NativeWriteJournalError("original reservation seed is immutable")

    @staticmethod
    def _quarantine_intent(entry):
        return {
            key: entry[key]
            for key in (
                "table",
                "deduplication_token",
                "query_id",
                "parameters_sha256",
                "intent_sha256",
            )
        }

    @staticmethod
    def _quarantine(document):
        if type(document) is not dict or set(document) != {
            "scope",
            "admission_sha256",
            "generation",
            "pending",
            "reason",
        }:
            raise NativeWriteJournalError("invalid quarantine binding")
        if (
            type(document["pending"]) is not list
            or len(document["pending"]) > MAX_SCOPE_PENDING
        ):
            raise NativeWriteJournalError("invalid quarantine intent bound")
        result = NativeScopeQuarantine(
            scope=NativeWriteScope(**document["scope"]),
            admission_sha256=document["admission_sha256"],
            generation=document["generation"],
            pending=tuple(
                NativeQuarantineIntent(**entry) for entry in document["pending"]
            ),
            reason=NativeQuarantineReason(document["reason"]),
        )
        if _canonical(asdict(result)) != _canonical(document):
            raise NativeWriteJournalError("noncanonical quarantine binding")
        return result

    @classmethod
    def _terminal_repair(cls, document):
        if type(document) is not dict or set(document) != {
            "binding",
            "attempts",
            "completion",
        }:
            raise NativeWriteJournalError("invalid terminal repair accounting")
        values = document["binding"]
        if type(values) is not dict or set(values) != {
            "quarantine",
            "build_lease_sha256",
            "publication_sha256",
            "writes",
        }:
            raise NativeWriteJournalError("invalid terminal repair binding")
        if type(values["writes"]) is not list or not 1 <= len(values["writes"]) <= 2:
            raise NativeWriteJournalError("terminal repair write bound exceeded")
        binding = NativeTerminalRepairBinding(
            cls._quarantine(values["quarantine"]),
            values["build_lease_sha256"],
            values["publication_sha256"],
            tuple(NativeTerminalWriteIntent(**entry) for entry in values["writes"]),
        )
        if _canonical(asdict(binding)) != _canonical(values):
            raise NativeWriteJournalError("noncanonical terminal repair binding")
        attempts = document["attempts"]
        if type(attempts) is not dict or len(attempts) > len(binding.writes):
            raise NativeWriteJournalError("terminal repair attempt bound exceeded")
        allowed = {_key(w.table, w.deduplication_token): w for w in binding.writes}
        for key, entry in attempts.items():
            intent = NativeQuarantineIntent(**entry)
            if (
                key not in allowed
                or key != _key(intent.table, intent.deduplication_token)
                or intent.parameters_sha256 != allowed[key].parameters_sha256
                or _canonical(asdict(intent)) != _canonical(entry)
            ):
                raise NativeWriteJournalError(
                    "terminal attempt differs from frozen row"
                )
        completion = document["completion"]
        if completion is not None:
            if type(completion) is not list or set(attempts) != set(allowed):
                raise NativeWriteJournalError("premature terminal repair completion")
            completion = NativeTerminalRepairReceipt(binding, tuple(completion))
        return binding, attempts, completion

    def _require_terminal_binding(self, expected):
        self._check()
        document = self._read_index()
        if (
            type(expected) is not NativeTerminalRepairBinding
            or "terminal_repair" not in document
            or self._terminal_repair(document["terminal_repair"])[0] != expected
        ):
            raise NativeWriteJournalError("terminal repair requires its frozen binding")
        return document

    def begin_terminal_repair(self, binding: NativeTerminalRepairBinding):
        """Freeze local accounting under the held BUILD lock; no SQL/proof implied."""
        self._idle()
        if (
            type(binding) is not NativeTerminalRepairBinding
            or binding.quarantine != self.quarantine_binding
        ):
            raise NativeWriteJournalError("terminal repair quarantine differs")
        document = self._read_index()
        if "terminal_repair" in document:
            self._require_terminal_binding(binding)
            return binding
        # An existing attempt must never be adopted as a newly authorized repair.
        for write in binding.writes:
            with self.session(write.table, write.deduplication_token) as session:
                if session.load() is not None:
                    raise NativeWriteJournalError(
                        "terminal repair receipt already exists"
                    )
        document.update(
            version=6 if document["version"] >= 4 else 3,
            terminal_repair={
                "binding": asdict(binding),
                "attempts": {},
                "completion": None,
            },
        )
        self._save(document)
        return self._terminal_repair(self._read_index()["terminal_repair"])[0]

    @staticmethod
    def _validate_terminal_attempt(attempt, binding):
        if type(attempt) is not NativeWriteAttempt or attempt.row_count != 1:
            raise NativeWriteJournalError("terminal repair requires one frozen row")
        write = next((w for w in binding.writes if w.table == attempt["table"]), None)
        if (
            write is None
            or attempt["deduplication_token"] != write.deduplication_token
            or attempt.parameters_sha256 != write.parameters_sha256
            or _thaw(attempt["scope"]) != asdict(binding.quarantine.scope)
            or attempt["admission_sha256"] != binding.quarantine.admission_sha256
        ):
            raise NativeWriteJournalError(
                "terminal repair row differs from frozen intent"
            )
        row = attempt.rows[0]
        if type(row["_version"]) is not int or row["_version"] != (1 << 64) - 1:
            raise NativeWriteJournalError(
                "terminal row must permanently dominate the build"
            )
        if attempt["table"] == _ACTIVE_TABLE:
            if row["status"] != "disabled":
                raise NativeWriteJournalError("terminal activation must be disabled")
        elif (
            row["status"] != "failed"
            or row["source_adapter"] != "system_manifest"
            or type(row["envelope_version"]) is not int
            or row["envelope_version"] != 0
            or _uuid(row["producer_stream_id"]) != binding.quarantine.scope.build_token
            or row["build_lease_sha256"] != binding.build_lease_sha256
        ):
            raise NativeWriteJournalError(
                "terminal reservation differs from exact lease"
            )

    def _require_write_allowed(self, attempt, binding=None):
        if binding is None:
            self._require_not_quarantined()
            return
        document = self._require_terminal_binding(binding)
        self._validate_terminal_attempt(attempt, binding)
        if (
            attempt["table"] == "property_catalog_source_streams"
            and len(binding.writes) == 2
        ):
            # A failed reservation alone cannot suppress a possibly late ACTIVE.
            # The terminal activation must have its own durable Complete first.
            write = binding.writes[0]
            key = _key(write.table, write.deduplication_token)
            registered = document["terminal_repair"]["attempts"].get(key)
            if registered is None:
                raise NativeWriteJournalError("disabled activation must complete first")
            try:
                completed = NativeWriteAttempt(
                    _read(self._directory, key + ".json", MAX_ATTEMPT_BYTES)
                )
            except FileNotFoundError as exc:
                raise NativeWriteJournalError(
                    "disabled activation receipt is missing"
                ) from exc
            self._validate_terminal_attempt(completed, binding)
            self._match(registered, completed)
            if completed.state != "complete" or completed["completion"][
                "members"
            ] != tuple(m.name for m in self._journal.admission.members):
                raise NativeWriteJournalError("disabled activation must complete first")

    def finish_terminal_repair(self, binding, completed_attempts):
        """Persist exact Complete references. The executor owns remote proof."""
        self._idle()
        document = self._require_terminal_binding(binding)
        if type(completed_attempts) is not tuple or len(completed_attempts) != len(
            binding.writes
        ):
            raise NativeWriteJournalError(
                "terminal repair needs every completed receipt"
            )
        hashes = []
        for write, attempt in zip(binding.writes, completed_attempts, strict=True):
            self._validate_terminal_attempt(attempt, binding)
            if attempt["table"] != write.table or attempt.state != "complete":
                raise NativeWriteJournalError(
                    "terminal repair receipts are not complete"
                )
            with self.session(write.table, write.deduplication_token) as session:
                persisted = session.load()
                if persisted is None or persisted.encode() != attempt.encode():
                    raise NativeWriteJournalError(
                        "terminal receipt differs from durable record"
                    )
                self._forget_completed(session, persisted)
            hashes.append(attempt["record_sha256"])
        receipt = NativeTerminalRepairReceipt(binding, tuple(hashes))
        document = self._require_terminal_binding(binding)
        saved = document["terminal_repair"]["completion"]
        if saved is not None and tuple(saved) != receipt.attempt_record_sha256s:
            raise NativeWriteJournalError("terminal completion is immutable")
        if saved is None:
            document["terminal_repair"]["completion"] = hashes
            self._save(document)
        return receipt

    @property
    def quarantine_binding(self) -> NativeScopeQuarantine | None:
        """Read the original binding without recovering or settling any receipt."""
        self._check()
        document = self._read_index()
        return (
            self._quarantine(document["quarantine"])
            if "quarantine" in document
            else None
        )

    def _capture_quarantine(self, document, reason):
        return NativeScopeQuarantine(
            self.scope,
            self._base["admission_sha256"],
            document["generation"],
            tuple(
                NativeQuarantineIntent(
                    **self._quarantine_intent(document["pending"][key])
                )
                for key in sorted(document["pending"])
            ),
            reason,
        )

    def capture_quarantine(
        self, reason: NativeQuarantineReason
    ) -> NativeScopeQuarantine:
        """Capture bounded indexed intents under this lock, with no receipt writes.

        Includes REGISTERING and any Complete receipt whose reference has not yet
        been removed. It makes no execution/settlement inference. A prior binding
        is returned unchanged, including references subsequently settled.
        """
        self._idle()
        if self.scope.kind != "build" or type(reason) is not NativeQuarantineReason:
            raise NativeWriteJournalError(
                "quarantine requires BUILD and a typed reason"
            )
        document = self._read_index()
        if "quarantine" in document:
            original = self._quarantine(document["quarantine"])
            if original.reason != reason:
                raise NativeWriteJournalError("scope quarantine binding is immutable")
            return original
        return self._capture_quarantine(document, reason)

    def quarantine(self, expected: NativeScopeQuarantine) -> NativeScopeQuarantine:
        """Irreversibly persist an exact captured binding, never modify receipts.

        This is journal state only. It neither completes a receipt nor authorizes
        repair writes, SQL dispatch, revision allocation, or publication.
        """
        self._idle()
        if (
            type(expected) is not NativeScopeQuarantine
            or expected.scope != self.scope
            or expected.admission_sha256 != self._base["admission_sha256"]
        ):
            raise NativeWriteJournalError("quarantine scope/admission binding differs")
        document = self._read_index()
        if "quarantine" in document:
            original = self._quarantine(document["quarantine"])
            if original != expected:
                raise NativeWriteJournalError("scope quarantine binding is immutable")
            return original
        if self._capture_quarantine(document, expected.reason) != expected:
            raise NativeWriteJournalError(
                "quarantine generation/intent compare-and-swap failed"
            )
        document.update(
            version=5 if document["version"] >= 4 else 2,
            quarantine=asdict(expected),
        )
        self._save(document)
        return self._quarantine(self._read_index()["quarantine"])

    def _require_not_quarantined(self):
        self._check()
        if "quarantine" in self._read_index():
            raise NativeWriteJournalError(
                "BUILD scope is quarantined; no write or publication"
            )

    def _register(self, attempt, *, terminal_repair=None):
        self._require_write_allowed(attempt, terminal_repair)
        if (
            terminal_repair is None
            and self.scope.kind == "build"
            and attempt["table"] != "property_catalog_activation_control_events"
        ):
            self._journal.track_recovery_scope(self)
        self._retain_reservation_seed(attempt)
        document = self._read_index()
        key = _key(attempt["table"], attempt["deduplication_token"])
        repair_attempts = document.get("terminal_repair", {}).get("attempts", {})
        if (
            key in document["pending"]
            or key in repair_attempts
            or any(
                entry["stage"] == "registering"
                and ((at_key in repair_attempts) == (terminal_repair is not None))
                for at_key, entry in document["pending"].items()
            )
        ):
            raise NativeWriteJournalError(
                "recover existing registration before preparing another"
            )
        if (
            len(document["pending"])
            >= MAX_SCOPE_PENDING + (2 if terminal_repair else 0)
            or document["generation"] >= (1 << 64) - 1
        ):
            raise NativeWriteJournalError(
                "scope pending/generation bound exceeded; no dispatch"
            )
        if document["closure"] is not None and terminal_repair is None:
            closure = self._closure(document["closure"])
            if closure.completed_generation is None and not self._is_publication(
                attempt, closure.binding
            ):
                raise NativeWriteJournalError(
                    "scope is closing; only exact reserved ACTIVE may register"
                )
            if closure.completed_generation is None:
                self._validate_active(attempt, closure.binding)
        document["generation"] += 1
        document["pending"][key] = {
            **{
                field: attempt[field]
                for field in (
                    "table",
                    "deduplication_token",
                    "query_id",
                    "parameters_sha256",
                )
            },
            "intent_sha256": _intent_sha256(attempt),
            "stage": "registering",
            "prepared": json.loads(attempt.encode()),
        }
        if terminal_repair is not None:
            repair_attempts[key] = self._quarantine_intent(document["pending"][key])
        self._save(document)

    def _recover_registration(self, session):
        document = self._read_index()
        entry = document["pending"].get(session._key)
        if entry is None or entry["stage"] != "registering":
            return
        session._scope_claim(required=True)
        prepared = NativeWriteAttempt(_canonical(entry["prepared"]) + b"\n")
        session._bind(prepared)
        existing = session._load_file()
        if existing is not None and existing.encode() != prepared.encode():
            raise NativeWriteJournalError(
                "REGISTERING can recover only original Prepared, never Sent"
            )
        terminal_authorized = (
            session._terminal_repair is not None
            and session._key in document.get("terminal_repair", {}).get("attempts", {})
        )
        if terminal_authorized:
            self._require_write_allowed(prepared, session._terminal_repair)
        if "quarantine" in document and not terminal_authorized:
            # Expose the original journaled Prepared for diagnosis only. Do not
            # materialize a missing receipt or change the registration stage.
            return prepared
        if existing is None:
            session._persist(prepared)
        self._ready(session, prepared)

    def _ready(self, session, attempt):
        document = self._read_index()
        entry = document["pending"][session._key]
        self._match(entry, attempt)
        if (
            entry["stage"] != "registering"
            or session._load_file().encode() != attempt.encode()
            or attempt.state != "prepared"
        ):
            raise NativeWriteJournalError(
                "READY requires exact durable Prepared intent"
            )
        entry["stage"], entry["prepared"] = "ready", None
        self._save(document)

    def _validate_receipt(self, session, attempt):
        document = self._read_index()
        if session._key in document.get("terminal_repair", {}).get("attempts", {}):
            binding, attempts, _ = self._terminal_repair(document["terminal_repair"])
            self._validate_terminal_attempt(attempt, binding)
            self._match(attempts[session._key], attempt)
        entry = document["pending"].get(session._key)
        if entry is not None:
            if attempt is None or (
                entry["stage"] != "ready"
                and not ("quarantine" in document and attempt.state == "prepared")
            ):
                raise NativeWriteJournalError("READY receipt is missing or ambiguous")
            self._match(entry, attempt)
        elif attempt is not None and attempt.state != "complete":
            raise NativeWriteJournalError(
                "unindexed unresolved native attempt; no adoption"
            )

    def _forget_completed(self, session, attempt):
        if (
            attempt.state != "complete"
            or session._load_file().encode() != attempt.encode()
        ):
            raise NativeWriteJournalError(
                "pending reference requires exact durable Complete"
            )
        document = self._read_index()
        entry = document["pending"].get(session._key)
        if entry is not None:
            self._match(entry, attempt)
            del document["pending"][session._key]
            self._save(document)

    def _retain_complete(self, session, attempt):
        """Retain an existing exact receipt for proof, not a new registration."""
        session._require_scoped()
        self._require_not_quarantined()
        persisted = session.load()
        if (
            session._scoped is not self
            or type(attempt) is not NativeWriteAttempt
            or attempt.state != "complete"
            or persisted is None
            or persisted.encode() != attempt.encode()
        ):
            raise NativeWriteJournalError("reproof requires exact persisted Complete")
        document = self._read_index()
        closure = document["closure"]
        if closure is not None:
            closure = self._closure(closure)
            if closure.completed_generation is None:
                self._validate_active(attempt, closure.binding)
        entry = document["pending"].get(session._key)
        if entry is not None:
            self._match(entry, attempt)
        elif len(document["pending"]) >= MAX_SCOPE_PENDING:
            raise NativeWriteJournalError("scope reproof pending bound exceeded")
        if self.scope.kind == "build" and attempt["table"] != (
            "property_catalog_activation_control_events"
        ):
            self._journal.track_recovery_scope(self)
        if entry is not None and document["version"] == 4:
            # Complete a previous rename whose directory fsync failed.
            os.fsync(self._directory)
            return
        document["pending"][session._key] = {
            **{
                field: attempt[field]
                for field in (
                    "table",
                    "deduplication_token",
                    "query_id",
                    "parameters_sha256",
                )
            },
            "intent_sha256": _intent_sha256(attempt),
            "stage": "ready",
            "prepared": None,
        }
        document["version"] = 4
        self._save(document)

    def pending(self, *, limit=MAX_SCOPE_PENDING) -> tuple[NativePendingAttempt, ...]:
        """Complete bounded enumeration, never a silently truncated empty proof."""
        self._idle()
        if type(limit) is not int or not 1 <= limit <= MAX_SCOPE_PENDING:
            raise NativeWriteJournalError("pending limit must be between 1 and 128")
        entries = self._read_index()["pending"]
        if len(entries) > limit:
            raise NativeWriteJournalError("pending enumeration exceeds caller bound")
        result = []
        complete = False
        for key in sorted(entries):
            entry = entries[key]
            with self.session(entry["table"], entry["deduplication_token"]) as session:
                attempt = session.load()
                complete |= attempt.state == "complete"
                result.append(
                    NativePendingAttempt(
                        *(
                            attempt[field]
                            for field in (
                                "table",
                                "deduplication_token",
                                "query_id",
                                "parameters_sha256",
                            )
                        )
                    )
                )
        if complete:
            # Also protect Complete-before-forget crash recovery from an older
            # binary's automatic cleanup. No attempt/generation is changed.
            document = self._read_index()
            if document["version"] < 4:
                document["version"] += 3
                self._save(document)
        return tuple(result)

    def begin_closure(self, binding: NativePublicationBinding) -> NativeScopeClosure:
        self._idle()
        self._require_not_quarantined()
        if self.scope.kind != "build" or type(binding) is not NativePublicationBinding:
            raise NativeWriteJournalError(
                "closure requires exact build publication binding"
            )
        pending = self.pending()
        if any(not self._is_publication(asdict(entry), binding) for entry in pending):
            raise NativeWriteJournalError("publication dependencies remain unresolved")
        if pending:
            with self.session(_ACTIVE_TABLE, binding.deduplication_token) as session:
                self._validate_active(session.load(), binding)
        document = self._read_index()
        previous = document["closure"]
        if previous is not None:
            previous = self._closure(previous)
            if previous.binding != binding:
                raise NativeWriteJournalError("scope publication binding is immutable")
            if (
                previous.completed_generation is None
                or previous.completed_generation == document["generation"]
            ):
                return previous
        closure = NativeScopeClosure(binding, document["generation"])
        document["closure"] = asdict(closure)
        self._save(document)
        return closure

    def finish_closure(
        self, expected: NativeScopeClosure, completed_active: NativeWriteAttempt
    ) -> NativeScopeClosure:
        self._idle()
        self._require_not_quarantined()
        if (
            type(expected) is not NativeScopeClosure
            or type(completed_active) is not NativeWriteAttempt
        ):
            raise NativeWriteJournalError("closure completion requires exact receipt")
        if self.pending():
            raise NativeWriteJournalError("closure still has pending attempts")
        document = self._read_index()
        if (
            document["closure"] is None
            or self._closure(document["closure"]) != expected
        ):
            raise NativeWriteJournalError("closure compare-and-swap failed")
        if (
            not self._is_publication(completed_active, expected.binding)
            or completed_active.state != "complete"
        ):
            raise NativeWriteJournalError("closure requires exact completed ACTIVE")
        self._validate_active(completed_active, expected.binding)
        with self.session(
            _ACTIVE_TABLE, expected.binding.deduplication_token
        ) as session:
            persisted = session.load()
            if persisted is None or persisted.encode() != completed_active.encode():
                raise NativeWriteJournalError(
                    "closure ACTIVE receipt differs from durable record"
                )
        if expected.completed_generation is not None:
            self.require_closed(expected)
            return expected
        closed = NativeScopeClosure(
            expected.binding,
            expected.generation,
            document["generation"],
            completed_active["record_sha256"],
        )
        document["closure"] = asdict(closed)
        self._save(document)
        return closed

    def require_closed(self, expected: NativeScopeClosure) -> None:
        self._idle()
        self._require_not_quarantined()
        document = self._read_index()
        if (
            type(expected) is not NativeScopeClosure
            or expected.completed_generation is None
            or document["closure"] is None
            or self._closure(document["closure"]) != expected
            or document["generation"] != expected.completed_generation
            or self.pending()
        ):
            raise NativeWriteJournalError(
                "scope closure is absent, stale, or unresolved"
            )
        with self.session(
            _ACTIVE_TABLE, expected.binding.deduplication_token
        ) as session:
            attempt = session.load()
            if (
                attempt is None
                or attempt.state != "complete"
                or attempt["record_sha256"] != expected.attempt_record_sha256
            ):
                raise NativeWriteJournalError(
                    "closed ACTIVE receipt is missing or changed"
                )


class NativeWriteSession:
    """A locked exact table/token. Never store/use a session outside its context."""

    def __init__(
        self,
        parent,
        directory,
        lock,
        key,
        table,
        token,
        identity,
        admission,
        scoped=None,
    ):
        self._parent, self._directory, self._key = parent, directory, key
        self._lock = lock
        self._table, self._token = table, token
        self._identity, self._admission, self._open = identity, admission, True
        self._scoped = scoped
        self._terminal_repair = None

    def _check(self):
        if not self._open:
            raise NativeWriteJournalError(
                "native attempt requires a live exclusive session"
            )
        _private(self._parent, directory=True)
        _private(self._directory, directory=True)
        _private(self._lock)
        observed_lock = os.stat(
            self._key + ".lock", dir_fd=self._directory, follow_symlinks=False
        )
        opened_lock = os.fstat(self._lock)
        if (observed_lock.st_dev, observed_lock.st_ino) != (
            opened_lock.st_dev,
            opened_lock.st_ino,
        ):
            raise NativeWriteJournalError("native journal exclusive lock changed")
        observed = os.stat(
            NATIVE_WRITE_ATTEMPT_DIRECTORY, dir_fd=self._parent, follow_symlinks=False
        )
        opened = os.fstat(self._directory)
        if (observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino):
            raise NativeWriteJournalError("native journal directory changed")
        if _installation(self._parent) != (self._identity, self._admission):
            raise NativeWriteJournalError("native journal installed identity changed")
        if self._scoped is not None:
            self._scoped._check()

    def _require_scoped(self):
        self._check()
        if self._scoped is None:
            raise NativeWriteJournalError(
                "managed writes require an explicit scoped session; diagnostics are read-only"
            )

    def load(self) -> NativeWriteAttempt | None:
        self._check()
        recovered = None
        if self._scoped is not None:
            self._scope_claim()
            recovered = self._scoped._recover_registration(self)
        attempt = recovered if recovered is not None else self._load_file()
        if self._scoped is not None:
            self._scoped._validate_receipt(self, attempt)
        return attempt

    def _scope_claim(self, *, create=False, required=False):
        self._require_scoped()
        raw = (
            _canonical(
                {
                    "format": "futureagi.property-catalog-native-key-scope",
                    "version": 1,
                    "key": self._key,
                    "scope": asdict(self._scoped.scope),
                    "admission_sha256": hashlib.sha256(
                        self._admission.encode()
                    ).hexdigest(),
                }
            )
            + b"\n"
        )
        name = self._key + ".scope-binding"
        try:
            existing = _read(self._directory, name, 4096)
        except FileNotFoundError:
            if create:
                _atomic(self._directory, name, raw)
                return
            if required:
                raise NativeWriteJournalError(
                    "missing durable table/token scope binding"
                ) from None
            return
        if existing != raw:
            raise NativeWriteJournalError(
                "table/token already bound to a different scope or installation"
            )

    def _load_file(self):
        try:
            raw = _read(self._directory, self._key + ".json", MAX_ATTEMPT_BYTES)
        except FileNotFoundError:
            return None
        attempt = NativeWriteAttempt(raw)
        self._bind(attempt)
        if self._scoped is not None:
            self._scope_claim(required=True)
        return attempt

    def _bind(self, attempt):
        if (
            attempt["table"] != self._table
            or attempt["deduplication_token"] != self._token
            or attempt["admission_sha256"]
            != hashlib.sha256(self._admission.encode()).hexdigest()
            or attempt["database"] != self._admission.database
            or attempt["member"] not in {m.name for m in self._admission.members}
        ):
            raise NativeWriteJournalError(
                "native attempt conflicts with installed target or lock key"
            )
        if attempt.state == "complete" and attempt["completion"]["members"] != tuple(
            m.name for m in self._admission.members
        ):
            raise NativeWriteJournalError(
                "native completion lacks exact admitted membership"
            )
        if self._scoped is not None and _thaw(attempt["scope"]) != asdict(
            self._scoped.scope
        ):
            raise NativeWriteJournalError("native attempt conflicts with held scope")

    def prepare(
        self,
        *,
        database: str,
        member: str,
        user: str,
        admission_sha256: str,
        columns: Sequence[str],
        rows: Sequence[Mapping],
        settings: Mapping,
        sql: str | None = None,
    ) -> NativeWriteAttempt:
        self._require_scoped()
        parameters = _parameters(columns, rows)
        digest = _hash({"columns": list(columns), "parameters": parameters})
        settings = _settings(settings, self._token, digest)
        expected_sql = native_insert_sql(
            database, self._table, columns, quorum=settings.get("insert_quorum")
        )
        if sql is not None and sql != expected_sql:
            raise NativeWriteJournalError(
                "native SQL differs from exact catalog INSERT"
            )
        document = {
            "format": _FORMAT,
            "version": 2,
            "scope": asdict(self._scoped.scope),
            "database": database,
            "table": self._table,
            "deduplication_token": self._token,
            "member": member,
            "user": user,
            "admission_sha256": admission_sha256,
            "columns": list(columns),
            "parameters": parameters,
            "parameters_sha256": digest,
            "row_count": len(rows),
            "sql": expected_sql,
            "settings": settings,
        }
        current = self.load()
        if current is not None:
            frozen = json.loads(current.encode())
            if any(frozen[key] != value for key, value in document.items()):
                raise NativeWriteJournalError(
                    "native attempt intent/settings conflict; existing attempts never replay"
                )
            return current
        if self._terminal_repair is None:
            self._scoped._require_not_quarantined()
        attempt = _record(
            {
                **document,
                "query_id": str(uuid4()),
                "state": "prepared",
                "acknowledgement": None,
                "completion": None,
            }
        )
        self._bind(attempt)
        self._scoped._require_write_allowed(attempt, self._terminal_repair)
        self._scope_claim(create=True)
        self._scoped._register(attempt, terminal_repair=self._terminal_repair)
        self._persist(attempt)
        self._scoped._ready(self, attempt)
        return self.load()

    def mark_sent(self, expected: NativeWriteAttempt) -> NativeWriteAttempt:
        """Return only after sent is fsynced. The transport must call before I/O."""
        return self._advance(expected, "sent")

    def mark_acknowledged(
        self, expected: NativeWriteAttempt, *, evidence: Mapping
    ) -> NativeWriteAttempt:
        return self._advance(expected, "acknowledged", evidence)

    def mark_complete(
        self, expected: NativeWriteAttempt, *, evidence: Mapping
    ) -> NativeWriteAttempt:
        return self._advance(expected, "complete", evidence)

    def _advance(self, expected, state, evidence=None):
        self._require_scoped()
        if state == "sent":
            self._scoped._require_write_allowed(expected, self._terminal_repair)
        current = self.load()
        if (
            current is None
            or type(expected) is not NativeWriteAttempt
            or current.encode() != expected.encode()
        ):
            raise NativeWriteJournalError("native attempt compare-and-swap failed")
        if (current.state, state) not in {
            ("prepared", "sent"),
            ("sent", "acknowledged"),
            ("acknowledged", "complete"),
        }:
            raise NativeWriteJournalError(
                "unsafe native attempt transition: no replay or proof bypass"
            )
        document = json.loads(current.encode())
        document["state"] = state
        if state != "sent":
            document["completion" if state == "complete" else "acknowledgement"] = (
                _thaw(evidence)
            )
        updated = _record(document)
        self._bind(updated)
        result = self._persist(updated)
        if state == "complete":
            self._scoped._forget_completed(self, result)
        return result

    def _persist(self, attempt):
        self._require_scoped()
        _atomic(self._directory, self._key + ".json", attempt.encode())
        return self._load_file()
