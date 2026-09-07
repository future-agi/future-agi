"""Durable positive source-change signals, independent of Kafka receipts.

This does not turn source versions into a completeness watermark. A positive
probe schedules the existing audited full replacement; a negative probe does
not suppress periodic reconciliation. No source data or candidate is written.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .codec import canonical_uuid
from .repair_files import locked_file, publish_file, read_file, utc_micros

_FORMAT = "futureagi.property-catalog-source-repair"
_FIELDS = {
    "format",
    "version",
    "generation",
    "organization_id",
    "workspace_id",
    "source_database",
    "source_table",
    "first_seen_us",
    "last_seen_us",
    "observed_at_us",
}


def _encode(doc: dict) -> bytes:
    return (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _parse(raw: bytes, identity: dict) -> dict:
    doc = json.loads(raw)
    if (
        not isinstance(doc, dict)
        or set(doc) != _FIELDS
        or any(doc.get(key) != value for key, value in identity.items())
        or doc["format"] != _FORMAT
        or type(doc["version"]) is not int
        or doc["version"] != 1
        or any(
            type(doc[key]) is not int
            for key in ("generation", "first_seen_us", "last_seen_us", "observed_at_us")
        )
        or not 0 < doc["generation"] < 2**64
        or not 0
        < doc["first_seen_us"]
        <= doc["last_seen_us"]
        < doc["observed_at_us"]
        < 2**63
        or _encode(doc) != raw
    ):
        raise ValueError("source repair is noncanonical or cross-scope")
    return doc


def _location(
    *,
    state_directory: str,
    organization_id: str,
    workspace_id: str,
    source_database: str,
    create: bool,
) -> tuple[Path, dict]:
    identity = {
        "organization_id": canonical_uuid(organization_id, field="organization_id"),
        "workspace_id": canonical_uuid(workspace_id, field="workspace_id"),
        "source_database": source_database,
        "source_table": "spans",
    }
    if (
        not isinstance(source_database, str)
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source_database) is None
    ):
        raise ValueError("source repair requires an exact source database")
    root = Path(state_directory)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError(
            "source repair requires the existing persistent control directory"
        )
    directory = root / "source-repairs"
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError("source repair directory must not be redirected")
    if create:
        # Persist the new directory entry before a catalog activation can rely
        # on a notice inside it surviving a machine restart.
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return directory / f"repair-{organization_id}-{workspace_id}.json", identity


def _state(
    path: Path, identity: dict
) -> tuple[bytes | None, dict | None, bytes | None]:
    raw, ack = read_file(path), read_file(Path(str(path) + ".ack"))
    doc = _parse(raw, identity) if raw is not None else None
    if ack is not None:
        acknowledged = _parse(ack, identity)
        if doc is None or acknowledged["generation"] > doc["generation"]:
            raise ValueError("source repair request regressed behind acknowledgement")
        if acknowledged["generation"] == doc["generation"] and ack != raw:
            raise ValueError("source repair generation has conflicting identities")
    return raw, doc, ack


@dataclass(frozen=True)
class SourceRepair:
    path: Path
    raw: bytes
    identity: dict

    def acknowledge_replacement(self, *, since: datetime, until: datetime) -> bool:
        """Called ONLY after a verified full replacement, never an increment.

        A full replacement also removes prior catalog rows outside its history
        window. A noticed row aging out of that window must not cause an endless
        repair loop. An older frozen build still cannot acknowledge newer work.
        """
        doc = _parse(self.raw, self.identity)
        if not (
            utc_micros(since) < utc_micros(until)
            and utc_micros(until) >= doc["observed_at_us"]
        ):
            return False
        with locked_file(self.path):
            raw, current, ack = _state(self.path, self.identity)
            if current is None or current["generation"] < doc["generation"]:
                raise ValueError("source repair request disappeared or regressed")
            if current["generation"] == doc["generation"] and raw != self.raw:
                raise ValueError("source repair generation has conflicting identities")
            if (
                ack is not None
                and _parse(ack, self.identity)["generation"] > doc["generation"]
            ):
                return False
            publish_file(Path(str(self.path) + ".ack"), self.raw)
        return True


def pending_source_repair(**scope) -> SourceRepair | None:
    path, identity = _location(**scope, create=False)
    if not path.parent.exists():
        return None
    with locked_file(path):
        raw, _, ack = _state(path, identity)
        return (
            SourceRepair(path, raw, identity)
            if raw is not None and raw != ack
            else None
        )


def record_source_repair(
    *, seen_at: datetime, observed_at: datetime, **scope
) -> SourceRepair:
    first = last = utc_micros(seen_at)
    observed = utc_micros(observed_at)
    if not 0 < first < observed:
        raise ValueError("source repair observation must follow the historical event")
    path, identity = _location(**scope, create=True)
    with locked_file(path):
        raw, previous, ack = _state(path, identity)
        if previous is not None and raw != ack:
            first = min(first, previous["first_seen_us"])
            last = max(last, previous["last_seen_us"])
            observed = max(observed, previous["observed_at_us"])
        doc = {
            **identity,
            "format": _FORMAT,
            "version": 1,
            "generation": previous["generation"] + 1 if previous else 1,
            "first_seen_us": first,
            "last_seen_us": last,
            "observed_at_us": observed,
        }
        encoded = _encode(doc)
        _parse(encoded, identity)
        publish_file(path, encoded)
        return SourceRepair(path, encoded, identity)
