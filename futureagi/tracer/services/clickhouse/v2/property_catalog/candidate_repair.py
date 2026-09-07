"""Durable handoff from skipped hot candidates to authoritative reconciliation.

The sequencer owns one bounded request per workspace in its receipt directory.
The lifecycle only acknowledges the exact bytes observed before a successful
source scan. Concurrent arrivals, stale acknowledgements and process restarts
can cause extra work, never silently discard a newer repair request.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .codec import canonical_uuid
from .repair_files import (
    locked_file as _lock,
)
from .repair_files import (
    publish_file as _publish,
)
from .repair_files import (
    read_file as _read,
)
from .repair_files import (
    utc_micros as _micros,
)

_FORMAT = "futureagi.property-catalog-candidate-repair"
_FIELDS = {
    "candidate_id",
    "candidate_topic",
    "first_seen_us",
    "format",
    "generation",
    "last_seen_us",
    "organization_id",
    "version",
    "workspace_id",
}


def _parse(raw: bytes, *, organization_id: str, workspace_id: str) -> dict:
    doc = json.loads(raw)
    if not isinstance(doc, dict) or set(doc) != _FIELDS:
        raise ValueError("candidate repair fields differ from the contract")
    canonical = (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if (
        raw != canonical
        or doc["format"] != _FORMAT
        or type(doc["version"]) is not int
        or doc["version"] != 1
        or doc["organization_id"] != organization_id
        or doc["workspace_id"] != workspace_id
        or not isinstance(doc["candidate_id"], str)
        or re.fullmatch(r"[0-9a-f]{64}", doc["candidate_id"]) is None
        or not isinstance(doc["candidate_topic"], str)
        or re.fullmatch(r"[A-Za-z0-9._-]{1,249}", doc["candidate_topic"]) is None
        or any(
            type(doc[key]) is not int
            for key in ("generation", "first_seen_us", "last_seen_us")
        )
        or not 0 < doc["generation"] < 2**64
        or not 0 < doc["first_seen_us"] <= doc["last_seen_us"] < 2**63
    ):
        raise ValueError("candidate repair is noncanonical or cross-scope")
    return doc


@dataclass(frozen=True)
class CandidateRepair:
    path: Path
    raw: bytes
    first_seen_us: int
    last_seen_us: int
    observed_at: datetime

    def needs_history(self, *, prior_until: datetime, now: datetime) -> bool:
        return self.first_seen_us < min(_micros(prior_until), _micros(now))

    def acknowledge_covered(self, *, since: datetime, until: datetime) -> bool:
        # In particular, resuming an older frozen build cannot acknowledge a
        # candidate committed after its cutoff, even with an old event time.
        if not (
            _micros(until) >= _micros(self.observed_at)
            and _micros(since) <= self.first_seen_us
            and self.last_seen_us < _micros(until)
        ):
            return False
        with _lock(self.path):
            ack_path = Path(str(self.path) + ".ack")
            previous = _read(ack_path)
            current = json.loads(self.raw)
            if previous is not None:
                doc = _parse(
                    previous,
                    organization_id=current["organization_id"],
                    workspace_id=current["workspace_id"],
                )
                if doc["generation"] > current["generation"]:
                    return False
            _publish(ack_path, self.raw)
        return True


def pending_candidate_repair(
    *,
    drain_proof_file: str,
    organization_id: str,
    workspace_id: str,
    now: datetime,
) -> CandidateRepair | None:
    organization_id = canonical_uuid(organization_id, field="organization_id")
    workspace_id = canonical_uuid(workspace_id, field="workspace_id")
    directory = Path(drain_proof_file).parent / "candidate-receipts"
    if not directory.is_absolute() or directory.is_symlink():
        raise ValueError(
            "candidate repair requires the shared absolute receipt directory"
        )
    path = directory / f"repair-{organization_id}-{workspace_id}.json"
    if not directory.exists():
        return None
    with _lock(path):
        raw = _read(path)
        claim_path = Path(str(path) + ".claim")
        claim = _read(claim_path)
        ack = _read(Path(str(path) + ".ack"))
        docs = {
            label: _parse(
                value, organization_id=organization_id, workspace_id=workspace_id
            )
            for label, value in (("request", raw), ("claim", claim), ("ack", ack))
            if value is not None
        }
        if not docs:
            return None
        if (
            "request" not in docs
            or len({doc["candidate_topic"] for doc in docs.values()}) != 1
        ):
            raise ValueError("candidate repair request missing or cross-topic")
        if any(
            doc["generation"] > docs["request"]["generation"] for doc in docs.values()
        ):
            raise ValueError("candidate repair request regressed behind durable claim")
        for doc in docs.values():
            if any(
                other["generation"] == doc["generation"]
                and other["candidate_id"] != doc["candidate_id"]
                for other in docs.values()
            ):
                raise ValueError(
                    "candidate repair generation has conflicting identities"
                )
        pending = []
        if claim is not None and claim != ack:
            pending.append(docs["claim"])
        if raw != ack and (
            claim is None or docs["request"]["generation"] > docs["claim"]["generation"]
        ):
            pending.append(docs["request"])
        if not pending:
            return None
        doc = dict(max(pending, key=lambda value: value["generation"]))
        doc["first_seen_us"] = min(value["first_seen_us"] for value in pending)
        doc["last_seen_us"] = max(value["last_seen_us"] for value in pending)
        encoded = (
            json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        # Claim under the same short filesystem lock as the Go publisher. No
        # source scan/DB work runs under this lock. At most one claimed interval
        # and one new pending interval are retained, independent of event rate.
        _publish(claim_path, encoded)
        return CandidateRepair(
            path, encoded, doc["first_seen_us"], doc["last_seen_us"], now
        )
