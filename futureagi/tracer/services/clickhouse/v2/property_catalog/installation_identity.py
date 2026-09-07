"""Durable, shared installation identity; never an operator version counter.

The lifecycle process is the sole initializer. The sequencer reads the same
file from its persistent fence volume. Database and Kafka destinations are
bound into the identity so an upgrade cannot accidentally reuse another
catalog's spool. Choosing/adopting an identity requires database evidence at
the call site; absence of this file is NOT evidence of an empty database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from uuid import UUID, uuid4

from .mutation_lock import FileCatalogMutationSerializer

IDENTITY_FILENAME = "runtime-identity-v1.json"
IDENTITY_FORMAT = "futureagi.property-catalog-runtime-identity"
IDENTITY_VERSION = 1
PROJECTION_FORMAT = "futureagi.property-catalog-values.v1"
INITIAL_CATALOG_EPOCH = 1
INITIAL_PROJECTION_VERSION = 1
MAX_IDENTITY_BYTES = 4096
_DATABASE = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")
_TOPIC = re.compile(r"[A-Za-z0-9._-]{1,249}\Z")


class InstallationIdentityError(ValueError):
    """Missing, conflicting, unsupported, or corrupt installation metadata."""


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


@dataclass(frozen=True, slots=True)
class InstallationIdentity:
    environment: str
    target_database: str
    candidate_topic: str
    ordered_topic: str
    catalog_epoch: int
    projection_version: int
    producer_stream_id: str
    projection_format: str = PROJECTION_FORMAT

    def __post_init__(self) -> None:
        if self.environment not in {"development", "production"}:
            raise InstallationIdentityError("identity environment is unsupported")
        if not isinstance(self.target_database, str) or not _DATABASE.fullmatch(
            self.target_database
        ):
            raise InstallationIdentityError("identity target database is invalid")
        if self.target_database in {"default", "system", "information_schema"}:
            raise InstallationIdentityError(
                "identity target is not an isolated catalog"
            )
        for topic in (self.candidate_topic, self.ordered_topic):
            if (
                not isinstance(topic, str)
                or not _TOPIC.fullmatch(topic)
                or topic in {".", ".."}
            ):
                raise InstallationIdentityError("identity Kafka topic is invalid")
        if self.candidate_topic == self.ordered_topic:
            raise InstallationIdentityError("identity requires distinct Kafka topics")
        for name in ("catalog_epoch", "projection_version"):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value < 65536:
                raise InstallationIdentityError(
                    f"identity {name} must be a positive UInt16"
                )
        if self.projection_format != PROJECTION_FORMAT:
            raise InstallationIdentityError("identity projection format is unsupported")
        try:
            parsed = UUID(self.producer_stream_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise InstallationIdentityError(
                "identity producer stream must be a UUID"
            ) from exc
        if str(parsed) != self.producer_stream_id or not parsed.int:
            raise InstallationIdentityError(
                "identity producer stream must be a canonical nonzero UUID"
            )

    @classmethod
    def fresh(
        cls,
        *,
        environment: str,
        target_database: str,
        candidate_topic: str,
        ordered_topic: str,
    ) -> InstallationIdentity:
        """Call only after proving an empty, isolated destination and spool."""
        return cls(
            environment=environment,
            target_database=target_database,
            candidate_topic=candidate_topic,
            ordered_topic=ordered_topic,
            catalog_epoch=INITIAL_CATALOG_EPOCH,
            projection_version=INITIAL_PROJECTION_VERSION,
            producer_stream_id=str(uuid4()),
        )

    def encode(self) -> bytes:
        unsigned = {
            "format": IDENTITY_FORMAT,
            "version": IDENTITY_VERSION,
            **asdict(self),
        }
        digest = hashlib.sha256(_canonical(unsigned)).hexdigest()
        return _canonical({**unsigned, "identity_sha256": digest}) + b"\n"

    @classmethod
    def decode(cls, raw: bytes) -> InstallationIdentity:
        if len(raw) > MAX_IDENTITY_BYTES or not raw.endswith(b"\n"):
            raise InstallationIdentityError(
                "identity must be a bounded canonical JSON line"
            )
        try:
            document = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise InstallationIdentityError("identity JSON is invalid") from exc
        if not isinstance(document, dict) or _canonical(document) + b"\n" != raw:
            raise InstallationIdentityError("identity JSON is not canonical")
        if set(document) != {field.name for field in fields(cls)} | {
            "format",
            "version",
            "identity_sha256",
        }:
            raise InstallationIdentityError("identity fields do not match the contract")
        if (
            document.get("format") != IDENTITY_FORMAT
            or type(document.get("version")) is not int
            or document["version"] != IDENTITY_VERSION
        ):
            raise InstallationIdentityError("identity format/version is unsupported")
        digest = document.pop("identity_sha256", None)
        if hashlib.sha256(_canonical(document)).hexdigest() != digest:
            raise InstallationIdentityError("identity digest mismatch")
        del document["format"], document["version"]
        try:
            return cls(**document)
        except TypeError as exc:
            raise InstallationIdentityError(
                "identity fields do not match the contract"
            ) from exc

    def require_destination(
        self,
        *,
        environment: str,
        target_database: str,
        candidate_topic: str,
        ordered_topic: str,
    ) -> None:
        for field, expected in (
            ("environment", environment),
            ("target_database", target_database),
            ("candidate_topic", candidate_topic),
            ("ordered_topic", ordered_topic),
        ):
            if getattr(self, field) != expected:
                raise InstallationIdentityError(
                    f"persisted catalog identity conflicts with {field}"
                )


def identity_path(revision_fence_file: str) -> Path:
    fence = Path(revision_fence_file)
    if not fence.is_absolute() or not fence.parent.is_dir():
        raise InstallationIdentityError(
            "identity requires an existing absolute fence directory"
        )
    return fence.parent / IDENTITY_FILENAME


def load_identity(path: Path) -> InstallationIdentity:
    """Read only: missing identity must not generate a new producer on restart."""
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise InstallationIdentityError(
                    "persisted catalog identity is not a regular file"
                )
            raw = source.read(MAX_IDENTITY_BYTES + 1)
    except OSError as exc:
        raise InstallationIdentityError(
            f"cannot read persisted catalog identity: {exc.strerror}"
        ) from exc
    return InstallationIdentity.decode(raw)


def load_or_initialize_identity(
    path: Path,
    *,
    initialize: Callable[[], InstallationIdentity],
) -> InstallationIdentity:
    """Persist once under the shared writer lock, after evidence-based initialization.

    The initializer may inspect an empty DB or validate an existing installation.
    It runs only while holding the lock and only when the file is absent. Any
    validation failure leaves existing state untouched; a corrupt file is never
    replaced. Publication is atomic and fsynced, including the directory entry.
    """
    if not path.is_absolute() or path.name != IDENTITY_FILENAME:
        raise InstallationIdentityError(
            "identity path must use the fixed absolute filename"
        )

    def resolve() -> InstallationIdentity:
        if os.path.lexists(path):
            return load_identity(path)
        identity = initialize()
        if not isinstance(identity, InstallationIdentity):
            raise InstallationIdentityError(
                "identity initializer returned invalid evidence"
            )
        raw = identity.encode()
        descriptor, temporary = tempfile.mkstemp(
            prefix=".catalog-identity-", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
            # Never replace an existing identity, including one created by a
            # writer that did not participate in this process's file lock.
            os.link(temporary, path, follow_symlinks=False)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            os.unlink(temporary)
        return identity

    return FileCatalogMutationSerializer(str(path.parent)).serialize(
        "property-catalog-installation-identity-v1", resolve
    )
