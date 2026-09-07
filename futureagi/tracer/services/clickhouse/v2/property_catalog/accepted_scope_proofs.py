"""Optional, fail-closed canonical accepted-scope re-audits.

This is not a merge classifier or a source commit fence. Only an equal fresh
canonical audit can suppress an inventory-only notice. Positive repair notices
live in source_repair.py and are NEVER acknowledged here. File records use the
existing shared mutation volume; no source/catalog table is written.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from .codec import canonical_json, canonical_json_sha256, canonical_uuid
from .coordinator import FileSupersessionJournal
from .repair_files import locked_file, utc_micros

MAX_COMPONENTS = 4096
_FORMAT = "futureagi.property-catalog.accepted-scope-proofs.v1"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _digest(value):
    return canonical_json_sha256(canonical_json({"value": value}, max_bytes=2_097_152))


def _uint(value, *, positive=False):
    if type(value) is not int or not int(positive) <= value < 2**64:
        raise ValueError("invalid accepted-scope integer")
    return value


def _sha(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("invalid accepted-scope digest")
    return value


def _time(value):
    return _EPOCH + timedelta(microseconds=_uint(value, positive=True))


@dataclass(frozen=True)
class SourceIdentity:
    """Exact table incarnation and responding node/replication identity.

    NativeSourceClient.source_identity(timeout_ms=...) must return exactly this
    mapping, from bounded read-only metadata for its configured spans table.
    Standalone engines require empty replication fields; replicated engines
    require both. Each re-audit additionally checks its responding hostName().
    """

    table_uuid: str
    hostname: str
    engine: str
    replica_name: str
    zookeeper_path: str

    def __post_init__(self):
        if (
            canonical_uuid(self.table_uuid, field="table_uuid") != self.table_uuid
            or UUID(self.table_uuid).int == 0
        ):
            raise ValueError("source table incarnation is missing")
        if (
            not isinstance(self.hostname, str)
            or re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", self.hostname) is None
        ):
            raise ValueError("source node identity is invalid")
        if self.engine not in {"ReplacingMergeTree", "ReplicatedReplacingMergeTree"}:
            raise ValueError("source engine cannot support canonical audit reuse")
        for value in (self.replica_name, self.zookeeper_path):
            if (
                not isinstance(value, str)
                or len(value.encode()) > 1024
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError("source replication identity is invalid")
        if self.engine == "ReplicatedReplacingMergeTree":
            if not self.replica_name or not self.zookeeper_path.startswith("/"):
                raise ValueError("source replication identity is missing")
        elif self.replica_name or self.zookeeper_path:
            raise ValueError("standalone source has replication identity")

    @classmethod
    def from_document(cls, document):
        if not isinstance(document, dict) or set(document) != set(
            cls.__dataclass_fields__
        ):
            raise ValueError("source identity metadata shape changed")
        return cls(**document)


def _component_document(component):
    proof = component.proof
    return {
        "project_id": component.project_id,
        "since_us": utc_micros(component.since),
        "until_us": utc_micros(component.until),
        "count": proof.count,
        "xor": list(proof.xor),
        "total": list(proof.total),
        "state_conflict_count": proof.state_conflict_count,
        "audit_generation": component.audit_generation,
    }


def _components(documents):
    from .span_source import SpanAggregateProof, SpanAuditComponent

    if not isinstance(documents, list) or len(documents) > MAX_COMPONENTS:
        raise ValueError("accepted component inventory exceeds its bound")
    result = []
    previous = {}
    keys = []
    for doc in documents:
        if not isinstance(doc, dict) or set(doc) != {
            "project_id",
            "since_us",
            "until_us",
            "count",
            "xor",
            "total",
            "state_conflict_count",
            "audit_generation",
        }:
            raise ValueError("accepted component shape changed")
        project = canonical_uuid(doc["project_id"], field="project_id")
        start, end = _time(doc["since_us"]), _time(doc["until_us"])
        for field in ("xor", "total"):
            if not isinstance(doc[field], list) or len(doc[field]) != 4:
                raise ValueError("accepted audit component width changed")
            for value in doc[field]:
                _uint(value)
        proof = SpanAggregateProof(
            _uint(doc["count"]),
            tuple(doc["xor"]),
            tuple(doc["total"]),
            _uint(doc["state_conflict_count"]),
        )
        # The digest deliberately excludes conflicts. Never use it alone.
        if proof.state_conflict_count:
            raise ValueError("conflicted source cannot establish accepted coverage")
        if project in previous and start != previous[project]:
            raise ValueError("accepted scopes overlap or have a coverage gap")
        previous[project] = end
        keys.append((project, start))
        result.append(
            SpanAuditComponent(
                project,
                start,
                end,
                proof,
                _uint(doc["audit_generation"], positive=True),
            )
        )
    if keys != sorted(keys):
        raise ValueError("accepted scopes are not canonical")
    return tuple(result)


def _ordered(documents):
    result = sorted(
        documents, key=lambda value: (value["project_id"], value["since_us"])
    )
    _components(result)
    return result


def active_head(active, epoch):
    """Called only with the runtime's positively matched durable ACTIVE read."""
    result = {
        "catalog_epoch": _uint(epoch, positive=True),
        "catalog_revision": _uint(active.catalog_revision, positive=True),
        "build_token": canonical_uuid(active.build_token, field="build_token"),
        "projection_version": _uint(active.projection_version, positive=True),
        "activation_sequence": _uint(active.activation_sequence, positive=True),
        "activation_sha256": _sha(active.activation_sha256),
        "source_manifest_sha256": _sha(active.source_manifest_sha256),
        "build_plan_sha256": _sha(active.build_plan.sha256),
    }
    return result


def build_document(execution):
    lease = execution.lease
    source = lease.build_plan.source_scope
    prior = execution.prepared.prior_active
    return {
        "organization_id": canonical_uuid(
            lease.organization_id, field="organization_id"
        ),
        "workspace_id": canonical_uuid(lease.workspace_id, field="workspace_id"),
        "catalog_epoch": _uint(lease.catalog_epoch, positive=True),
        "catalog_revision": _uint(lease.catalog_revision, positive=True),
        "build_token": canonical_uuid(lease.build_token, field="build_token"),
        "projection_version": _uint(lease.projection_version, positive=True),
        "build_plan_sha256": _sha(lease.build_lease_sha256),
        "project_ids": list(source.project_ids),
        "since_us": source.span_since_us,
        "until_us": source.span_until_us,
        "audit_generation": execution.prepared.cutoffs.span_audit_generation,
        "replacement": execution.prepared.mode.value != "incremental",
        "prior_head": active_head(prior, lease.catalog_epoch)
        if prior is not None
        else None,
    }


def _check_head(head):
    if not isinstance(head, dict) or set(head) != {
        "catalog_epoch",
        "catalog_revision",
        "build_token",
        "projection_version",
        "activation_sequence",
        "activation_sha256",
        "source_manifest_sha256",
        "build_plan_sha256",
    }:
        raise ValueError("invalid accepted ACTIVE binding")
    for key in (
        "catalog_epoch",
        "catalog_revision",
        "projection_version",
        "activation_sequence",
    ):
        _uint(head[key], positive=True)
    if head["catalog_epoch"] >= 65536 or head["projection_version"] >= 65536:
        raise ValueError("invalid accepted catalog version")
    canonical_uuid(head["build_token"], field="build_token")
    for key in ("activation_sha256", "source_manifest_sha256", "build_plan_sha256"):
        _sha(head[key])


def _head_matches_build(head, build):
    return all(
        head[key] == build[key]
        for key in (
            "catalog_epoch",
            "catalog_revision",
            "build_token",
            "projection_version",
            "build_plan_sha256",
        )
    )


def _revision(value):
    return value["catalog_epoch"], value["catalog_revision"]


def _check_build(build, identity):
    if not isinstance(build, dict) or set(build) != {
        "organization_id",
        "workspace_id",
        "catalog_epoch",
        "catalog_revision",
        "build_token",
        "projection_version",
        "build_plan_sha256",
        "project_ids",
        "since_us",
        "until_us",
        "audit_generation",
        "replacement",
        "prior_head",
    }:
        raise ValueError("invalid accepted build binding")
    if any(build[k] != identity[k] for k in ("organization_id", "workspace_id")):
        raise ValueError("accepted build belongs to another tenant")
    for key in ("catalog_epoch", "catalog_revision", "projection_version"):
        _uint(build[key], positive=True)
    if build["catalog_epoch"] >= 65536 or build["projection_version"] >= 65536:
        raise ValueError("invalid accepted build version")
    canonical_uuid(build["build_token"], field="build_token")
    _sha(build["build_plan_sha256"])
    _uint(build["audit_generation"], positive=True)
    if type(build["replacement"]) is not bool or _time(build["since_us"]) >= _time(
        build["until_us"]
    ):
        raise ValueError("invalid accepted build window/mode")
    projects = build["project_ids"]
    if (
        not isinstance(projects, list)
        or not projects
        or projects != sorted({canonical_uuid(p, field="project_id") for p in projects})
    ):
        raise ValueError("invalid explicit accepted project scope")
    if build["prior_head"] is not None:
        _check_head(build["prior_head"])
        if _revision(build["prior_head"]) >= _revision(build):
            raise ValueError("accepted build does not advance its prior ACTIVE")
    if not build["replacement"] and build["prior_head"] is None:
        raise ValueError("incremental proof lacks prior ACTIVE")


@dataclass(frozen=True)
class CapturedAudit:
    source: SourceIdentity
    contract: str
    parts: tuple
    components: tuple


def capture_final_audit(reader, frozen):
    """Keep final audit mandatory; optional metadata/cache failure cannot skip it."""
    from .span_source import CANONICAL_SPAN_SCAN_WINDOW_HOURS, combine_audit_components

    if not getattr(frozen, "project_ids", None):
        return reader.audit(frozen), None
    size = len(frozen.project_ids) * math.ceil(
        (frozen.until - frozen.since).total_seconds()
        / (3600 * CANONICAL_SPAN_SCAN_WINDOW_HOURS)
    )
    if size > MAX_COMPONENTS:
        # This optional cache must not impose its metadata cap on the mandatory
        # streaming audit, nor retain an arbitrary subset of accepted history.
        return reader.audit(frozen), None
    try:
        identity = reader.source_identity()
        if not isinstance(identity, SourceIdentity):
            raise ValueError("source identity is not typed")
        parts = reader.parts_snapshot()
        contract = reader.audit_contract_sha256()
    except Exception:
        return reader.audit(frozen), None
    components = reader.audit_components(frozen, expected_hostname=identity.hostname)
    proof = combine_audit_components(components)
    try:
        if parts != reader.parts_snapshot() or identity != reader.source_identity():
            return proof, None
        return proof, CapturedAudit(identity, contract, parts, components)
    except Exception:
        return proof, None


class ScopeProofJournal:
    """Two bounded proof slots plus a generation-CAS dirty slot per workspace."""

    def __init__(
        self, *, state_directory, organization_id, workspace_id, source_database
    ):
        org = canonical_uuid(organization_id, field="organization_id")
        workspace = canonical_uuid(workspace_id, field="workspace_id")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source_database) is None:
            raise ValueError("accepted source database is invalid")
        root = Path(state_directory)
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ValueError("accepted proofs require the shared control directory")
        self.identity = {
            "organization_id": org,
            "workspace_id": workspace,
            "source_database": source_database,
            "source_table": "spans",
        }
        self.key = f"accepted-scopes:{org}:{workspace}:{source_database}"
        self.lock = root / f"accepted-scopes-{org}-{workspace}"
        self.journal = FileSupersessionJournal(state_directory)

    def _load(self, kind):
        doc = self.journal.load_record(f"{self.key}:{kind}")
        if doc is None:
            return None
        expected = {
            "format",
            "identity",
            "source",
            "contract",
            "components",
            "build",
            "inventory_sha256",
        }
        if kind == "accepted":
            expected.add("head")
        if kind == "dirty":
            expected = {
                "format",
                "identity",
                "generation",
                "clean",
                "inventory_sha256",
                "accepted_sha256",
                "build_sha256",
                "source",
            }
        if (
            set(doc) != expected
            or doc["format"] != _FORMAT
            or doc["identity"] != self.identity
        ):
            raise ValueError("accepted proof record changed shape or scope")
        SourceIdentity.from_document(doc["source"])
        _sha(doc["inventory_sha256"])
        if kind == "dirty":
            _uint(doc["generation"], positive=True)
            if type(doc["clean"]) is not bool:
                raise ValueError("invalid dirty state")
            _sha(doc["accepted_sha256"])
            _sha(doc["build_sha256"])
        else:
            _sha(doc["contract"])
            _components(doc["components"])
            _check_build(doc["build"], self.identity)
            if kind == "accepted":
                _check_head(doc["head"])
                if not _head_matches_build(doc["head"], doc["build"]):
                    raise ValueError("accepted head differs from its qualified build")
        return doc

    def _save(self, kind, doc):
        self.journal.save_record(f"{self.key}:{kind}", doc)

    def stage(self, *, build, capture, expected_count, expected_digest):
        from .source_parts import checked_parts
        from .span_source import (
            CANONICAL_SPAN_SCAN_WINDOW_HOURS,
            combine_audit_components,
        )

        if capture is None:
            return False
        if not isinstance(capture.source, SourceIdentity):
            raise ValueError("staged source identity is not typed")
        parts = checked_parts(capture.parts)
        _check_build(build, self.identity)
        docs = [_component_document(c) for c in capture.components]
        components = _components(docs)
        proof = combine_audit_components(components)
        if proof.count != expected_count or proof.digest != expected_digest:
            raise ValueError("staged components differ from qualified value evidence")
        if any(c.audit_generation != build["audit_generation"] for c in components):
            raise ValueError("staged audit generation differs from the qualified build")
        expected = []
        for project in build["project_ids"]:
            start, until = _time(build["since_us"]), _time(build["until_us"])
            while start < until:
                end = min(
                    start + timedelta(hours=CANONICAL_SPAN_SCAN_WINDOW_HOURS), until
                )
                expected.append((project, start, end))
                start = end
                if len(expected) > MAX_COMPONENTS:
                    raise ValueError("staged scopes exceed bounded inventory")
        if [(c.project_id, c.since, c.until) for c in components] != expected:
            raise ValueError("staged scopes differ from the exact build windows")
        document = {
            "format": _FORMAT,
            "identity": self.identity,
            "source": asdict(capture.source),
            "contract": _sha(capture.contract),
            "components": docs,
            "build": build,
            "inventory_sha256": _digest(parts),
        }
        with locked_file(self.lock):
            previous = self._load("staged")
            if previous is not None:
                if _revision(previous["build"]) > _revision(build):
                    return False
                if _revision(previous["build"]) == _revision(build) and any(
                    previous[key] != document[key]
                    for key in ("source", "contract", "components", "build")
                ):
                    raise ValueError("qualified build has conflicting staged evidence")
            self._save("staged", document)
        return True

    def register_active(self, *, build, active):
        _check_build(build, self.identity)
        head = active_head(active, build["catalog_epoch"])
        _check_head(head)
        if not _head_matches_build(head, build):
            raise ValueError("durable ACTIVE does not match staged build")
        with locked_file(self.lock):
            staged = self._load("staged")
            accepted = self._load("accepted")
            if accepted is not None and accepted["head"] == head:
                return accepted["build"] == build
            if accepted is not None and (
                _revision(accepted["head"]) >= _revision(head)
                or (
                    accepted["head"]["catalog_epoch"] == head["catalog_epoch"]
                    and accepted["head"]["activation_sequence"]
                    >= head["activation_sequence"]
                )
            ):
                return False
            if staged is None or staged["build"] != build:
                return False
            docs = staged["components"]
            if not build["replacement"]:
                if (
                    accepted is None
                    or accepted["head"] != build["prior_head"]
                    or any(accepted[k] != staged[k] for k in ("source", "contract"))
                ):
                    return False
                docs = _ordered([*accepted["components"], *docs])
            self._save("accepted", {**staged, "head": head, "components": docs})
        return True

    def verify_dirty(self, *, reader, build, current, previous, started, since, until):
        """None: use old detector. False: repair. True: proved unchanged.

        A qualified current attempt may join prior ACCEPTED coverage here, but
        is never promoted to accepted until the post-ACTIVE matching hook.
        Every retained scope is re-read, not just scopes with current rows.
        """
        from .source_parts import checked_parts
        from .span_source import FrozenSpanSource

        _check_build(build, self.identity)
        current = checked_parts(current)
        with locked_file(self.lock):
            dirty = self._load("dirty")
            if (
                previous == current
                and started == current
                and (dirty is None or dirty["clean"])
            ):
                return None
            accepted, staged = self._load("accepted"), self._load("staged")
            if staged is None or staged["build"] != build:
                return False if dirty is not None and not dirty["clean"] else None
            if build["replacement"]:
                # Full repair already rewrote catalog coverage. Keep its existing
                # watcher; only its matched ACTIVE can replace retained scopes.
                return False if dirty is not None and not dirty["clean"] else None
            if accepted is None or accepted["head"] != build["prior_head"]:
                # A staged attempt is qualified evidence, not an accepted root.
                # Bootstrap or a missing durable ACTIVE binding uses normal repair.
                return False if dirty is not None and not dirty["clean"] else None
            if any(accepted[k] != staged[k] for k in ("source", "contract")):
                return False if dirty is not None and not dirty["clean"] else None
            docs = _ordered([*accepted["components"], *staged["components"]])
            components = _components(docs)
            # Exact accepted windows may extend past a requested watch bound;
            # re-read them intact. Never fabricate a clipped component digest.
            projects = build["project_ids"]
            if {c.project_id for c in components} != set(projects):
                return False
            for project in projects:
                selected = [c for c in components if c.project_id == project]
                if (
                    not selected
                    or selected[0].since > since
                    or selected[-1].until < until
                ):
                    return False
            identity = SourceIdentity.from_document(staged["source"])
            dirty = {
                "format": _FORMAT,
                "identity": self.identity,
                "generation": (dirty["generation"] if dirty else 0) + 1,
                "clean": False,
                "inventory_sha256": _digest(current),
                "accepted_sha256": _digest(accepted),
                "build_sha256": _digest(build),
                "source": asdict(identity),
            }
            self._save("dirty", dirty)
        if (
            reader.source_identity() != identity
            or checked_parts(reader.parts_snapshot()) != current
            or reader.audit_contract_sha256() != staged["contract"]
        ):
            return False
        for component in components:
            actual = reader.audit_components(
                FrozenSpanSource(
                    (component.project_id,),
                    component.since,
                    component.until,
                    component.audit_generation,
                ),
                expected_hostname=identity.hostname,
            )
            if (
                len(actual) != 1
                or actual[0] != component
                or actual[0].proof.state_conflict_count
            ):
                return False
        if (
            checked_parts(reader.parts_snapshot()) != current
            or reader.source_identity() != identity
        ):
            return False
        with locked_file(self.lock):
            if (
                self._load("dirty") != dirty
                or self._load("accepted") != accepted
                or self._load("staged") != staged
            ):
                return False
            self._save("dirty", {**dirty, "clean": True})
        return True


def stage_final_audit(*, runtime, execution, capture, proof):
    """Cache is optional: a failed stage cannot authorize later reuse."""
    try:
        return ScopeProofJournal(**runtime._source_repair_scope()).stage(
            build=build_document(execution),
            capture=capture,
            expected_count=proof.count,
            expected_digest=proof.digest,
        )
    except Exception:
        return False


def register_active_proof(*, runtime, execution, active):
    try:
        return ScopeProofJournal(**runtime._source_repair_scope()).register_active(
            build=build_document(execution), active=active
        )
    except Exception:
        return False


def proof_checker(*, scope, execution, reader):
    """Invoked only at the existing qualified pre-activation source watch."""

    def check(**kwargs):
        if not callable(getattr(reader, "audit_components", None)) or not callable(
            getattr(reader, "source_identity", None)
        ):
            return None
        try:
            return ScopeProofJournal(**scope).verify_dirty(
                reader=reader, build=build_document(execution), **kwargs
            )
        except Exception:
            return False

    return check
