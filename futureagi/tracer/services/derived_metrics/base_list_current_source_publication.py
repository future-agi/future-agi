"""Narrow A01 -> existing publication adapter; never producer/coverage authority.

A bounded tip receipt binds a settled *registered* source cut to one publication.
It is NOT a retained bootstrap or proof of all previous deltas. Only the existing
writer may supply it, after independently consuming the complete frozen interval.
No new writer, Collector wrapper, compactor, schema or serving flag is installed.
A scalar publication does not inherit this evidence from its parent.
"""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from uuid import UUID

from django.db import connection

from tracer.models.metric_source_intent import (
    MetricSourceDelivery,
    MetricSourceIntent,
    MetricSourcePage,
    MetricSourceScope,
)
from tracer.services.derived_metrics.base_list_current_source import (
    MAX_PAGES,
    MAX_TARGETS,
    SourceScope,
    SourceUnavailable,
    _locked,
    canonical_bytes,
)


@dataclass(frozen=True)
class SourcePublicationCut:
    scope: SourceScope
    epoch: UUID
    revision: int
    tip_sha256: str
    server_id: UUID
    database: str


def _read_cut(scope, server_id, database):
    """Caller holds the existing lifecycle lock; state row follows that lock.

    Publication already owns lifecycle -> refresh/attempt. A01 never acquires a
    refresh/attempt lock, so adding this state-row read creates no reverse edge.
    All metadata scans are bounded to ONE tip; no unbounded history hashing.
    """
    state = (
        MetricSourceScope.objects.using("default")
        .select_for_update()
        .filter(
            organization_id=scope.organization_id,
            project_id=scope.project_id,
        )
        .first()
    )
    if state is None or not state.revision or state.revision != state.closed_revision:
        raise SourceUnavailable("source cut absent or pending")
    tip = (
        MetricSourceIntent.objects.using("default")
        .filter(
            scope=state,
            revision=state.revision,
            complete=True,
        )
        .first()
    )
    if tip is None or not (
        1 <= tip.page_count <= MAX_PAGES and 1 <= tip.target_count <= MAX_TARGETS
    ):
        raise SourceUnavailable("source tip incomplete")
    pages = list(
        MetricSourcePage.objects.using("default")
        .filter(intent=tip)
        .order_by("ordinal")[: MAX_PAGES + 1]
    )
    deliveries = list(
        MetricSourceDelivery.objects.using("default")
        .filter(intent=tip)
        .order_by("server_id", "database", "store")[: MAX_TARGETS + 1]
    )
    if (
        len(pages) != tip.page_count
        or [p.ordinal for p in pages] != list(range(tip.page_count))
        or any(sha256(bytes(p.payload)).hexdigest() != p.sha256 for p in pages)
        or len(deliveries) != tip.target_count
        or any(
            d.state != "acknowledged" or not d.acknowledgement_sha256
            for d in deliveries
        )
    ):
        raise SourceUnavailable("source tip receipts incomplete")
    # Direct-local traces only. Replicas, remaps, helpers and external producers
    # have no enrolled receipt adapter here; never translate a local ACK to them.
    if any(
        d.server_id != server_id or d.database != database or d.store != "traces"
        for d in deliveries
    ):
        raise SourceUnavailable("source selected server or store mismatch")
    body = {
        "epoch": state.epoch,
        "revision": state.revision,
        "intent": tip.id,
        "operation": tip.operation_key,
        "manifest": tip.manifest_sha256,
        "pages": [p.sha256 for p in pages],
        "deliveries": [
            {
                "receipt": d.receipt_id,
                "server": d.server_id,
                "database": d.database,
                "store": d.store,
                "schema": d.schema_sha256,
                "payload": d.payload_sha256,
                "ack": d.acknowledgement_sha256,
            }
            for d in deliveries
        ],
    }
    return SourcePublicationCut(
        scope,
        state.epoch,
        state.revision,
        sha256(canonical_bytes(body)).hexdigest(),
        server_id,
        database,
    )


def capture_source_publication_cut(scope, *, server_id, database):
    """Capture before writer entry; the publish transaction rechecks this cut."""
    with _locked(scope):
        return _read_cut(scope, UUID(str(server_id)), database)


def source_cut_ceilings(cut):
    """Persist at attempt preparation, BEFORE freezing; replay must match exactly."""
    if type(cut) is not SourcePublicationCut:
        raise SourceUnavailable("source publication cut required")
    return {"a01_source_cut": json.loads(canonical_bytes(asdict(cut)))}


def bind_source_publication_cut(cut, *, attempt, visibility_receipts):
    """Called ONLY by MetricReadManifest.publish after its lifecycle acquisition."""
    from tracer.models.project import Project

    if type(cut) is not SourcePublicationCut or not connection.in_atomic_block:
        raise SourceUnavailable("source publication requires the owned transaction")
    with connection.cursor() as cursor:
        cursor.execute("SHOW transaction_isolation")
        if cursor.fetchone()[0] != "read committed":
            raise SourceUnavailable("source publication requires READ COMMITTED")
    if (
        attempt.source_ceilings.get("a01_source_cut")
        != source_cut_ceilings(cut)["a01_source_cut"]
        or str(attempt.project_id) != str(cut.scope.project_id)
        or not Project.no_workspace_objects.using("default")
        .filter(
            pk=attempt.project_id,
            organization_id=cut.scope.organization_id,
            deleted=False,
        )
        .exists()
        or visibility_receipts.get("shard") != [str(cut.server_id), cut.database]
        or _read_cut(cut.scope, cut.server_id, cut.database) != cut
    ):
        raise SourceUnavailable("source publication identity or cut changed")
    return {
        "format": "a01-local-tip-v1",
        "organization": str(cut.scope.organization_id),
        "project": str(cut.scope.project_id),
        "epoch": str(cut.epoch),
        "revision": cut.revision,
        "tip_sha256": cut.tip_sha256,
        "attempt": str(attempt.pk),
        "generation": attempt.generation,
        "shard": [str(cut.server_id), cut.database],
        "producer_complete": False,
        "bootstrap_complete": False,
    }


def verify_source_manifest(manifest, cut, *, server_id, database):
    """Validate an exact bound manifest, without granting a serving capability."""
    from tracer.models.derived_metric import _sha256_json

    receipts = manifest.visibility_receipts
    evidence = receipts.get("source_cut", {})
    body = {
        "generation": manifest.generation,
        "checkpoint": [
            str(manifest.checkpoint_attempt_id)
            if manifest.checkpoint_attempt_id
            else None,
            manifest.checkpoint_generation,
        ],
        "deltas": [
            [str(a), g]
            for a, g in zip(
                manifest.delta_attempt_ids, manifest.delta_generations, strict=True
            )
        ],
        "visibility_receipts": receipts,
    }
    expected = {
        "format": "a01-local-tip-v1",
        "organization": str(cut.scope.organization_id),
        "project": str(cut.scope.project_id),
        "epoch": str(cut.epoch),
        "revision": cut.revision,
        "tip_sha256": cut.tip_sha256,
        "attempt": str(manifest.published_by_id),
        "generation": manifest.generation,
        "shard": [str(cut.server_id), cut.database],
        "producer_complete": False,
        "bootstrap_complete": False,
    }
    if (
        manifest.revoked_at is not None
        or manifest.project_id != cut.scope.project_id
        or evidence != expected
        or receipts.get("shard") != expected["shard"]
        or expected["shard"] != [str(server_id), database]
        or manifest.manifest_sha256 != _sha256_json(body)
    ):
        raise SourceUnavailable("source manifest identity or hash mismatch")
    # Fresh source check, outside an ambient publication/pin transaction. A reader
    # owner extending this inside its pin must use a separately reviewed seam.
    if (
        capture_source_publication_cut(
            cut.scope, server_id=server_id, database=database
        )
        != cut
    ):
        raise SourceUnavailable("source manifest cut is stale")
