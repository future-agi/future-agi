"""Attempt-bound backup proofs; legacy attestations never authorize v2 restore.

No hooks, commands, credentials, backup locations or stdout are accepted here.
Manual hook behavior is unchanged. Restore-backed automation requires the v2
cold-snapshot proof and its exact independently retained inventory/seal; a v1
operator attestation or successful hook exit does not grant automatic permission.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple

from .compatibility import (MAX_BACKUP_BYTES, CompatibilityError, aware, decode_document, require,
                            schema, shape, timestamp)
from .contracts import BackupSeal, ReasonCode, PERSISTENT_DOMAINS
from .manifest import is_version


@dataclass(frozen=True, repr=False)
class BackupExpectation:
    attempt_id: str
    installation_fingerprint: str
    target_manifest_hash: str  # Canonical manifest hash; stage also retains the raw hash.
    source_version: str
    reserved_at: datetime
    covered_domains: Tuple[str, ...]

    def __post_init__(self) -> None:
        reason = ReasonCode.BACKUP_PROOF_INVALID
        for value, length in ((self.attempt_id, 32), (self.installation_fingerprint, 64),
                              (self.target_manifest_hash, 64)):
            require(type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is not None, reason)
        require(is_version(self.source_version), reason)
        aware(self.reserved_at)
        require(isinstance(self.covered_domains, (tuple, list)), reason)
        domains = tuple(self.covered_domains)
        allowed = schema("backup-proof.schema.json")["properties"]["covered_domains"]["items"]["enum"]
        require(bool(domains) and all(type(d) is str and d in allowed for d in domains)
                and len(set(domains)) == len(domains), reason)
        object.__setattr__(self, "covered_domains", domains)


@dataclass(frozen=True, repr=False)
class BackupProof:
    """Private validated evidence; intentionally no whole-object JSON projection."""

    attempt_id: str
    installation_fingerprint: str
    target_manifest_hash: str
    source_version: str
    backup_id: str
    started_at: datetime
    completed_at: datetime
    covered_domains: Tuple[str, ...]
    restore_procedure_id: str
    sha256: str
    raw: bytes = field(repr=False)
    schema_version: int = 1
    binding_sha256: str = ''
    inventory_sha256: str = ''
    source_observation_sha256: str = ''
    quiescence_evidence_sha256: str = ''
    snapshot_at: Optional[datetime] = None
    inventory_version: int = 1

    def public_summary(self) -> dict:
        # A successful attestation is not a statement about restore guarantees.
        return {"backup_state": "reported_complete"}


def validate_backup_proof(raw: bytes, expected: BackupExpectation, *, now: datetime,
                          max_age_seconds: int = 3600, seal: BackupSeal = None,
                          for_recovery: bool = False) -> BackupProof:
    """Check exact attempt, target, source, coverage and inclusive freshness.

    No future tolerance: a backup from the future cannot precede mutation.
    Clock regressions before reservation fail closed. Both start and completion
    must be after reservation; completion freshness alone cannot reuse a backup
    started for an older attempt. The adapter revalidates these same bytes at
    first mutation. Never construct a refreshed proof from an old stage.
    """
    try:
        require(isinstance(expected, BackupExpectation), ReasonCode.BACKUP_PROOF_INVALID)
        require(type(max_age_seconds) is int and 60 <= max_age_seconds <= 86400, ReasonCode.BACKUP_PROOF_INVALID)
        value = decode_document(raw, MAX_BACKUP_BYTES)
        shape(value, schema("backup-proof.schema.json"))
        for key in ("attempt_id", "installation_fingerprint", "target_manifest_hash", "source_version"):
            require(value[key] == getattr(expected, key), ReasonCode.BACKUP_PROOF_INVALID)
        started, completed = timestamp(value["started_at"]), timestamp(value["completed_at"])
        now, reserved = aware(now), aware(expected.reserved_at)
        require(now >= reserved and started <= now and completed <= now, ReasonCode.CLOCK_SKEW)
        require(reserved <= started <= completed, ReasonCode.BACKUP_PROOF_INVALID)
        require(set(expected.covered_domains) <= set(value["covered_domains"]), ReasonCode.BACKUP_PROOF_INVALID)
        require(type(for_recovery) is bool and (not for_recovery or isinstance(seal, BackupSeal)),
                ReasonCode.BACKUP_PROOF_INVALID)
        v2 = value['schema_version'] == 2
        snapshot = timestamp(value['snapshot_at']) if v2 else None
        if v2:
            require(set(value['covered_domains']) == set(PERSISTENT_DOMAINS)
                    == set(expected.covered_domains) and started <= snapshot <= completed,
                    ReasonCode.BACKUP_PROOF_INVALID)
        if not for_recovery:
            require((now - completed).total_seconds() <= max_age_seconds
                    and (not v2 or (now - snapshot).total_seconds() <= max_age_seconds), ReasonCode.BACKUP_EXPIRED)
        if seal is not None:
            require(v2 and isinstance(seal, BackupSeal), ReasonCode.BACKUP_PROOF_INVALID)
            for key in ('attempt_id', 'target_manifest_hash', 'source_version', 'binding_sha256',
                        'inventory_sha256', 'source_observation_sha256', 'quiescence_evidence_sha256',
                        'inventory_version', 'restore_procedure_id'):
                require(value[key] == getattr(seal, key), ReasonCode.BACKUP_PROOF_INVALID)
            require(seal.proof_sha256 == hashlib.sha256(raw).hexdigest() and seal.snapshot_at == snapshot
                    and completed <= aware(seal.authorised_at) <= now, ReasonCode.BACKUP_PROOF_INVALID)
        return BackupProof(value["attempt_id"], value["installation_fingerprint"], value["target_manifest_hash"],
                           value["source_version"], value["backup_id"], started, completed,
                           tuple(value["covered_domains"]), value["restore_procedure_id"],
                           hashlib.sha256(raw).hexdigest(), raw, value['schema_version'],
                           value.get('binding_sha256', ''), value.get('inventory_sha256', ''),
                           value.get('source_observation_sha256', ''), value.get('quiescence_evidence_sha256', ''),
                           snapshot, value.get('inventory_version', 1))
    except CompatibilityError as exc:
        reason = exc.reason_code if exc.reason_code in (ReasonCode.CLOCK_SKEW, ReasonCode.BACKUP_EXPIRED) else ReasonCode.BACKUP_PROOF_INVALID
        raise CompatibilityError(reason) from None
    except (TypeError, ValueError, KeyError, RecursionError):
        raise CompatibilityError(ReasonCode.BACKUP_PROOF_INVALID) from None


def read_backup_proof(directory_fd: int, filename: str, expected: BackupExpectation, *,
                      now: datetime, expected_uid: int, max_age_seconds: int = 3600) -> BackupProof:
    """Read a leaf relative to an ALREADY qualified private attempt directory fd.

    Caller owns safe ancestry/native-filesystem qualification and must not reuse
    an attempt directory/name. Opening relative to the directory fd prevents
    parent replacement; O_NOFOLLOW and O_NONBLOCK reject symlinks/FIFOs without
    hanging. Only an owned 0600, single-link regular file <=16KiB is accepted.
    No location is read from the proof; no error discloses a host path.
    """
    fd = None
    try:
        require(type(filename) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", filename) is not None)
        require(type(expected_uid) is int and expected_uid >= 0)
        parent = os.fstat(directory_fd)
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == expected_uid
                and stat.S_IMODE(parent.st_mode) == 0o700)
        require(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_NONBLOCK"))
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == expected_uid and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size <= MAX_BACKUP_BYTES)
        chunks, size = [], 0
        while size <= MAX_BACKUP_BYTES:
            chunk = os.read(fd, min(4096, MAX_BACKUP_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns,
                 before.st_mode, before.st_uid, before.st_nlink)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns,
                    after.st_mode, after.st_uid, after.st_nlink))
        require(size == before.st_size and size <= MAX_BACKUP_BYTES)
        raw = b"".join(chunks)
    except (OSError, ValueError, TypeError):
        raise CompatibilityError(ReasonCode.BACKUP_PROOF_INVALID) from None
    finally:
        if fd is not None:
            os.close(fd)
    return validate_backup_proof(raw, expected, now=now, max_age_seconds=max_age_seconds)
