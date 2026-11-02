"""Bounded, replayable project refreshes using the Postgres publication ledger.

All payload preparation (including complete old/new/vanished keys and source
preimages) precedes the FIRST INSERT. RowBinary output is kept in bounded,
append-only cleanup-intent chunks; MetricAttemptBatch references those chunks.
No attempt transition recopies that private data. A failed staging pass resumes
these exact bytes, never re-queries merged source data to reconstruct its plan.

One delta is a conservative operational cap, NOT a measured performance claim.
M2/T16 still require representative scale measurements before deployment.
"""

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.db import transaction

from tracer.models.derived_metric import (
    LEDGER_CHUNK_MAX_KEYS,
    MetricAttemptBatch,
    MetricAttemptLedgerChunk,
    MetricLedgerConflict,
    MetricProjectRefresh,
    MetricReadManifest,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.derived_metrics.buckets import (
    KEYS,
    rebuilt_parts_select,
    replacement_select,
)
from tracer.services.derived_metrics.deletion_protocol import (
    collect_history,
    serialized_project,
)
from tracer.services.derived_metrics.publication import (
    MAX_BATCH_ROWS,
    FrozenRows,
    committed_rows_select,
    physical_table,
)
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_VALUES,
    LIST_CURRENT_MEMBERS,
    LIST_CURRENT_ROOTS,
    RETIRED_PARTS,
    SOURCE_DEPENDENCIES,
    UNIT_STATE,
)
from tracer.services.derived_metrics.unit_reads import unit_values_select

MAX_PASS_UNITS = 50_000
MAX_DELTAS_BEFORE_COMPACTION = 1
TABLE_KINDS = {
    "field_values": FIELD_VALUES,
    "bucket_totals": BUCKET_TOTALS,
    "unit_state": UNIT_STATE,
    "source_dependencies": SOURCE_DEPENDENCIES,
    "bucket_retired_parts": RETIRED_PARTS,
    "list_current_root_facts": LIST_CURRENT_ROOTS,
    "list_current_members": LIST_CURRENT_MEMBERS,
}
TABLE_KIND = {table.name: kind for kind, table in TABLE_KINDS.items()}


def _json(value):
    return json.loads(json.dumps(value, default=str))


def payload_select(table, payloads, *, prefix):
    """Frozen input relation: no dependency on mutable telemetry or CH merges."""
    structure = ", ".join(f"{name} {kind}" for name, kind in table.schema)
    data = "".join(row for p in payloads for row in p.rows)
    if not data:
        return f"SELECT {', '.join(table.columns)} FROM {table.name} WHERE 0", {}
    return (
        f"SELECT * FROM format(RowBinary, %({prefix}_structure)s, unhex(%({prefix}_data)s))",
        {f"{prefix}_structure": structure, f"{prefix}_data": data},
    )


class MetricAdmissionUnproven(MetricStateConflict):
    code = "metric_admission_unproven"


class MetricRetentionChanged(MetricStateConflict):
    code = "metric_retention_replan"


class RefreshWriter:
    def __init__(self, lease, store, *, org_id, workspace_id=None, retention_days=None):
        self.lease, self.store = lease, store
        self.org_id, self.workspace_id = org_id, workspace_id
        # Discovery refresh MUST receive a live organization-policy loader.
        # Low-level replay/compaction may omit it; neither discovers new evidence.
        self.retention_days = retention_days

    def begin(self, *, purpose="incremental", ceilings=None, versions=(), ledger=None):
        with serialized_project(self.lease.project_id):
            ceilings = dict(ceilings or {})
            head = MetricProjectRefresh.for_project(self.lease.project_id).head_manifest
            inherited = (
                head.published_by.source_ceilings.get("retention_floor")
                if head
                else None
            )
            floors = [
                datetime.fromisoformat(str(v))
                for v in (inherited, ceilings.get("retention_floor"))
                if v
            ]
            if head and head.published_by.source_ceilings.get("recovery_blocked"):
                ceilings["recovery_blocked"] = True
            private_cleanup = [
                value
                for value in (
                    head.published_by.source_ceilings.get("private_cleanup_through")
                    if head
                    else None,
                    ceilings.get("private_cleanup_through"),
                )
                if value is not None
            ]
            if private_cleanup:
                ceilings["private_cleanup_through"] = max(private_cleanup)
            if floors:
                # Nonidentifying, immutable privacy boundary follows every new
                # head/checkpoint, so GC and a larger org horizon cannot restore
                # purged unit evidence via an ordinary incremental refresh.
                ceilings["retention_floor"] = max(floors).isoformat()
            attempt = MetricRefreshAttempt.prepare(
                self.lease.project_id,
                purpose=purpose,
                source_ceilings=_json(ceilings),
                metric_versions=_json(list(versions)),
                **self.lease.check(),
            )
            for part, values in (ledger or {}).items():
                self.append(attempt, part, values)
            return attempt

    def append(self, attempt, part, entries):
        count = MetricAttemptLedgerChunk.objects.filter(
            attempt=attempt, part=part
        ).count()
        entries = _json(list(entries))
        for offset in range(0, len(entries), LEDGER_CHUNK_MAX_KEYS):
            MetricRefreshAttempt.append_ledger_chunk(
                attempt.id,
                part=part,
                chunk_no=count,
                keys=entries[offset : offset + LEDGER_CHUNK_MAX_KEYS],
                **self.lease.check(),
            )
            count += 1

    def freeze(
        self,
        attempt,
        table,
        sql,
        params=None,
        *,
        extra=None,
        source_page=None,
        collect_payloads=True,
        transient=False,
    ):
        """Freeze SELECT output while PREPARED, with bounded sequence allocation.

        SQL/identifiers are compiler-owned, never client SQL. Each page is at
        most 500 rows; no UPDATE ever copies a payload. All output pages are
        durable before seal() permits staging. Empty tables need no INSERT.
        """
        if transient and table != RETIRED_PARTS:
            raise ValueError("only anonymous bucket parts may be transient")
        physical = physical_table(table, checkpoint=attempt.purpose == "compaction")
        after, result = None, []
        kind = TABLE_KIND[table.name]
        # Count metadata, never hydrate all preceding private payloads to allocate
        # a batch number. Each frozen batch has exactly one ledger chunk.
        batch_no = MetricAttemptLedgerChunk.objects.filter(
            attempt=attempt,
            part="cleanup_intent",
            payload__0__kind="frozen_batch",
            payload__0__table=kind,
        ).count()
        while True:
            self.lease.keepalive()
            first, _ = MetricProjectRefresh.allocate_row_sequences(
                self.lease.project_id, count=MAX_BATCH_ROWS, **self.lease.fence()
            )
            metadata = {
                "org_id": "toUUID(%(stamp_org)s)",
                "project_id": "toUUID(%(stamp_project)s)",
                "workspace_id": "toUUID(%(stamp_workspace)s)",
                "generation": "toUInt64(%(stamp_generation)s)",
                "attempt_id": "toUUID(%(stamp_attempt)s)",
                "fencing_epoch": "toUInt32(%(stamp_epoch)s)",
                "row_sequence": "toUInt32(%(stamp_first)s + pub_row_no - 1)",
                table.version_column: "toUInt64(%(stamp_epoch)s) * 4294967296 + %(stamp_first)s + pub_row_no - 1",
                **(extra or {}),
            }
            columns = ", ".join(
                f"{metadata.get(c, c)} AS {c}" for c in physical.columns
            )
            # The input page is stable: committed manifest + frozen fields, or a
            # finite source candidate query with a frozen as_of ceiling.
            # Metadata keys can be absent from a single-field/metric input.
            keys = tuple(c for c in table.logical_key if c not in metadata)
            key = ", ".join(keys) or "tuple()"
            page_sql, page_params = (
                source_page(after) if source_page else (sql, dict(params or {}))
            )
            predicate = "1"
            if after:
                from tracer.services.derived_metrics.changes import keyset_after

                predicate, page_bound = keyset_after(
                    keys, tuple(after[c] for c in keys), "freeze_after"
                )
                page_params.update(page_bound)
            # LIMIT precedes row_number: sequence allocation is per page. No
            # OFFSET or full-history row numbering on every checkpoint batch.
            page = f"SELECT * FROM ({page_sql}) WHERE {predicate} ORDER BY {key} LIMIT %(stamp_limit)s"
            stamped = f"SELECT {columns} FROM (SELECT *, row_number() OVER (ORDER BY {key}) AS pub_row_no FROM ({page}))"
            bound = {
                **page_params,
                "stamp_org": str(self.org_id),
                "stamp_project": str(self.lease.project_id),
                "stamp_workspace": str(self.workspace_id or UUID(int=0)),
                "stamp_generation": attempt.generation,
                "stamp_attempt": str(attempt.id),
                "stamp_epoch": attempt.fencing_epoch,
                "stamp_first": first,
                "stamp_limit": MAX_BATCH_ROWS,
            }
            payload = self.store.freeze_select(
                physical, stamped, bound, key_columns=keys
            )
            if not payload.rows:
                break
            last = first + len(payload.rows) - 1
            entry = {
                "kind": "frozen_batch",
                "table": kind,
                "batch_no": batch_no,
                "rows": list(payload.rows),
                "sha256": payload.digest,
                "first": first,
                "last": last,
            }
            if not transient:
                self.append(attempt, "cleanup_intent", [entry])
            if collect_payloads:
                result.append(payload)
            batch_no += 1
            if len(payload.rows) < MAX_BATCH_ROWS:
                break
            if payload.last_key is None:
                raise MetricLedgerConflict("full frozen page lacks a keyset cursor")
            after = dict(zip(keys, payload.last_key, strict=True))
        return result

    def freeze_current(self, attempt, table, manifest, *, where="1", params=None):
        """Stream a checkpoint with keyset predicates INSIDE the allowed collapse."""

        def page(after):
            sql, bound = committed_rows_select(
                table,
                manifest,
                org_id=self.org_id,
                project_id=self.lease.project_id,
                key_after=after,
            )
            return f"SELECT * FROM ({sql}) WHERE {where}", {**bound, **(params or {})}

        return self.freeze(
            attempt,
            table,
            "",
            source_page=page,
            collect_payloads=False,
            extra={"workspace_id": "workspace_id"}
            if "workspace_id" in table.columns
            else None,
        )

    def plans(self, attempt, *, kind=None):
        for chunk in (
            MetricAttemptLedgerChunk.objects.filter(
                attempt=attempt, part="cleanup_intent"
            )
            .order_by("chunk_no")
            .iterator(chunk_size=1)
        ):
            for entry in chunk.payload:
                if entry.get("kind") == "frozen_batch" and (
                    kind is None or entry["table"] == kind
                ):
                    yield entry

    def seal(self, attempt, *, cursors=None, publication=None):
        self.append(
            attempt,
            "cleanup_intent",
            [
                {
                    "kind": "publication",
                    "cursors": _json(cursors or {}),
                    **_json(publication or {}),
                }
            ],
        )
        chunks = MetricAttemptLedgerChunk.objects.filter(attempt=attempt)
        MetricRefreshAttempt.seal_ledger(
            attempt.id,
            expected_chunks=chunks.count(),
            expected_keys=sum(chunks.values_list("key_count", flat=True)),
            **self.lease.check(),
        )

    def stage_and_publish(self, attempt):
        """Do not publish a frozen plan that violates the CURRENT policy."""
        try:
            return self._stage_and_publish(attempt)
        except MetricRetentionChanged:
            from tracer.services.derived_metrics.deletion_protocol import _intent

            with serialized_project(self.lease.project_id):
                _intent(
                    organization_id=self.org_id,
                    project_id=self.lease.project_id,
                    scope="history",
                    deletion_key=f"retention-replan:{attempt.id}",
                    store_payload={"protocol": "metric_policy_replan"},
                )
                MetricProjectRefresh.objects.filter(
                    project_id=self.lease.project_id
                ).update(
                    pending_expiry=True, last_error_code=MetricRetentionChanged.code
                )
            raise

    def _stage_and_publish(self, attempt):
        """Idempotent after partial staging OR a lost publish acknowledgement."""
        attempt.refresh_from_db()
        if attempt.state == "committed":
            return MetricReadManifest.objects.get(published_by=attempt)
        self.lease.check()
        if attempt.expires_at <= self.lease.clock():
            raise MetricStateConflict("attempt expired")
        if attempt.state == "prepared":
            MetricRefreshAttempt.transition(
                attempt.id,
                from_state="prepared",
                to_state="staging",
                **self.lease.fence(),
            )
        elif attempt.state not in {"staging", "verified"}:
            raise MetricStateConflict("attempt is not recoverable")
        receipts = []
        for plan in self.plans(attempt):
            self.lease.keepalive()
            payload = FrozenRows(tuple(plan["rows"]))
            if payload.digest != plan["sha256"]:
                raise MetricLedgerConflict("frozen replay hash mismatch")
            table = physical_table(
                TABLE_KINDS[plan["table"]], checkpoint=attempt.purpose == "compaction"
            )
            if attempt.state != "verified":
                batch = MetricAttemptBatch.freeze(
                    attempt.id,
                    table_name=plan["table"],
                    batch_no=plan["batch_no"],
                    row_count=len(payload.rows),
                    payload_sha256=payload.digest,
                    payload_ref=f"ledger:{plan['table']}:{plan['batch_no']}",
                    row_sequences=(plan["first"], plan["last"]),
                    **self.lease.check(),
                )
                if batch.verified_at is None:
                    # A late INSERT after this check is harmless to logical reads:
                    # its revoked attempt is never in a replacement manifest.
                    self.submit(attempt, batch, table, payload)
                    actual = self.store.verify(table, attempt, batch)
                    MetricAttemptBatch.record_receipt(
                        batch.id,
                        visible_row_count=actual["count"],
                        visible_sha256=actual["sha256"],
                        **self.lease.check(),
                    )
            receipts.append(
                {
                    "table": table.name,
                    "batch": plan["batch_no"],
                    "attempt": str(attempt.id),
                    "count": len(payload.rows),
                    "sha256": payload.digest,
                }
            )
        with serialized_project(attempt.project_id):
            self.check_current_retention(attempt)
            if attempt.state != "verified":
                MetricRefreshAttempt.transition(
                    attempt.id,
                    from_state="staging",
                    to_state="verified",
                    **self.lease.check(),
                )
            controls = [
                entry
                for chunk in MetricAttemptLedgerChunk.objects.filter(
                    attempt=attempt,
                    part="cleanup_intent",
                    payload__0__kind="publication",
                )
                for entry in chunk.payload
                if entry.get("kind") == "publication"
            ]
            if len(controls) != 1:
                raise MetricLedgerConflict("missing frozen publication intent")
            parent_receipts = (
                attempt.parent_manifest.visibility_receipts
                if attempt.parent_manifest_id
                else {}
            )
            if attempt.purpose != "compaction" and parent_receipts:
                self.store.assert_visible(attempt.parent_manifest)
                receipts = parent_receipts["batches"] + receipts
            from tracer.services.derived_metrics.backfill import (
                apply_publication,
                check_backfill_publication,
                check_repair_publication,
                publication_horizon,
                retire_omitted_versions,
                strip_private_positions,
            )

            check_backfill_publication(self, controls[0])
            check_repair_publication(self, controls[0])
            retire_omitted_versions(self, attempt, controls[0])
            cursors = controls[0]["cursors"]
            strip, horizon = publication_horizon(self, attempt)
            if strip and cursors.get("reconciliation_cursor") is not None:
                # A frozen repair proposal must not publish a keyset that aged
                # out after planning; its non-identifying progress still lands.
                cursors = {
                    **cursors,
                    "reconciliation_cursor": strip_private_positions(
                        cursors["reconciliation_cursor"], cutoff=horizon
                    )[0],
                }
            # Manifest, coverage/progress and active semantics share this outer
            # transaction/project lock. Failure rolls the WHOLE switch back.
            manifest = MetricReadManifest.publish(
                attempt.id,
                cursors=cursors,
                visibility_receipts={
                    "shard": self.store.identity(),
                    "batches": receipts,
                },
                **self.lease.check(),
            )
            apply_publication(self, attempt, controls[0])
            if self.retention_days is not None and (
                attempt.source_ceilings.get("logical_expiry")
                or "as_of" in attempt.source_ceilings
            ):
                from tracer.models.derived_metric import MetricErasureRequest

                # A plain checkpoint cannot discharge policy replanning. Only
                # a fresh, current-policy-verified publication consumes this root.
                MetricErasureRequest.objects.filter(
                    project_key=attempt.project_id,
                    store_payload__protocol="metric_policy_replan",
                ).exclude(status="done").update(
                    status="done",
                    completed_at=self.lease.clock(),
                    receipts={"publication": {"verified": 1}},
                )
            return manifest

    def check_current_retention(self, attempt):
        """Revalidate proposed fields under the publication/capture lock.

        Policy reads are not a cached pin-time value. A shrink or a clock crossing
        requires a fresh expiry plan, never exact-byte publication of expired
        evidence. Ordinary compaction is a copy of the old state, not discovery;
        its successor refresh performs the integrated expiry transition.
        """
        if self.retention_days is None or (
            attempt.purpose == "compaction"
            and not attempt.source_ceilings.get("logical_expiry")
            and "as_of" not in attempt.source_ceilings
        ):
            return
        from django.apps import apps

        from tracer.services.derived_metrics.retention import (
            _current_tables,
            _proposal_rows,
            retention_cutoff,
        )

        # Serialize real org-policy UPDATEs through the final head switch, while
        # retaining the project's established capture/publication lock order.
        apps.get_model(
            "accounts", "Organization"
        )._base_manager.select_for_update().get(id=self.org_id)
        cutoff = retention_cutoff(self.retention_days(), now=self.lease.clock())
        floors = [
            datetime.fromisoformat(str(value))
            for value in (
                attempt.source_ceilings.get("retention_floor"),
                cutoff,
            )
            if value
        ]
        if not floors:
            return
        cutoff = max(floors)
        if attempt.purpose == "compaction":
            fields, params = payload_select(
                FIELD_VALUES,
                [
                    FrozenRows(tuple(p["rows"]))
                    for p in self.plans(attempt, kind="field_values")
                ],
                prefix="policy_fields",
            )
        else:
            fields, params = _proposal_rows(
                self, attempt, attempt.parent_manifest, FIELD_VALUES, "policy_fields"
            )
        private_relations = [(fields, params)]
        for table in _current_tables(self, attempt.parent_manifest, attempt):
            if attempt.purpose == "compaction":
                facts, bound = payload_select(
                    table,
                    [
                        FrozenRows(tuple(p["rows"]))
                        for p in self.plans(attempt, kind=TABLE_KIND[table.name])
                    ],
                    prefix="policy_" + table.name,
                )
            else:
                facts, bound = _proposal_rows(
                    self,
                    attempt,
                    attempt.parent_manifest,
                    table,
                    "policy_" + table.name,
                )
            private_relations.append((facts, bound))
        if any(v.get("level") == "span" for v in attempt.metric_versions):
            if attempt.purpose == "compaction":
                dependencies, bound = payload_select(
                    SOURCE_DEPENDENCIES,
                    [
                        FrozenRows(tuple(p["rows"]))
                        for p in self.plans(attempt, kind="source_dependencies")
                    ],
                    prefix="policy_dependencies",
                )
            else:
                dependencies, bound = _proposal_rows(
                    self,
                    attempt,
                    attempt.parent_manifest,
                    SOURCE_DEPENDENCIES,
                    "policy_dependencies",
                )
            private_relations.append((dependencies, bound))
            # Spans intentionally have no stored fields. Their old memberships
            # in the private recovery ledger must obey the same current horizon
            # as the proposed dependency relation, even after a start correction.
            if any(
                entry.get("unit_start")
                and datetime.fromisoformat(str(entry["unit_start"])) < cutoff
                for chunk in attempt.ledger_chunks.filter(part="membership_preimage")
                for entry in chunk.payload
            ):
                raise MetricRetentionChanged(
                    "retention policy changed; replan required"
                )
        for sql, bound in private_relations:
            if self.store.execute(
                f"SELECT count() FROM ({sql}) WHERE unit_start < toDateTime64(%(policy_cutoff)s, 6, 'UTC')",
                {**bound, "policy_cutoff": cutoff},
            )[0][0]:
                raise MetricRetentionChanged(
                    "retention policy changed; replan required"
                )

    def submit(self, attempt, batch, table, payload):
        """Register BEFORE sending; only a synchronous server response drains it.

        The receipt is deliberately independent of private ledger/attempt GC. A
        lost connection or worker crash leaves an unresolved obligation, not a
        false cancellation receipt. A retry's success cannot acknowledge an
        earlier ambiguous submission. Exclusion fences all later registrations.
        """
        from tracer.models.derived_metric import MetricErasureRequest
        from tracer.services.derived_metrics.deletion_protocol import _intent

        with transaction.atomic(durable=True), serialized_project(attempt.project_id):
            MetricRefreshAttempt._locked_open(
                attempt.id, **self.lease.check(), state="staging"
            )
            submission = _intent(
                organization_id=self.org_id,
                project_id=attempt.project_id,
                scope="history",
                store_payload={
                    "protocol": "metric_submission",
                    "attempt": str(attempt.id),
                    "generation": attempt.generation,
                    "batch": batch.batch_no,
                    "table": table.name,
                    "shard": self.store.identity(),
                },
            )
        self.store.insert(table, payload)
        # No lease requirement: this acknowledges transport completion, NOT
        # permission to publish. It remains valid after writer exclusion.
        MetricErasureRequest.objects.filter(id=submission.id).update(
            status="done",
            completed_at=self.lease.clock(),
            receipts={"synchronous_response": {"drained": 1}},
        )

    def abort_stale(self):
        """Return original work, including a retention checkpoint's durable handoff.

        The caller holds project serialization until its successor ledger is
        durable. Retention transfers these entries into its checkpoint BEFORE
        aborting/purging predecessors; a crash cannot leave only an in-memory
        copy. A normal refresh consumes the handoff in its own published plan.
        """
        entries = {part: [] for part in MetricAttemptLedgerChunk.Part.values}
        head = MetricProjectRefresh.for_project(self.lease.project_id).head_manifest
        if head and head.published_by.source_ceilings.get("recovery_pending"):
            for chunk in head.published_by.ledger_chunks.exclude(part="cleanup_intent"):
                entries[chunk.part].extend(chunk.payload)
        for attempt in MetricRefreshAttempt.objects.filter(
            project_id=self.lease.project_id,
            state__in=MetricRefreshAttempt.OPEN_STATES,
            fencing_epoch__lte=self.lease.grant.fencing_epoch,
        ):
            for chunk in attempt.ledger_chunks.order_by("part", "chunk_no"):
                # Output payload is never source truth for a replacement writer.
                if chunk.part != "cleanup_intent":
                    entries[chunk.part].extend(chunk.payload)
            MetricRefreshAttempt.abort(
                attempt.id, reason="lease_takeover", **self.lease.check()
            )
        return entries

    def compact(self):
        """Checkpoint every store, including tombstones; never copy a revoked base."""
        with serialized_project(self.lease.project_id):
            head = MetricProjectRefresh.objects.get(
                project_id=self.lease.project_id
            ).head_manifest
            if head is None:
                return None
            if head.revoked_at is not None:
                raise MetricStateConflict("expired base requires source rebuild")
            self.store.assert_visible(head)
            pending = head.published_by.source_ceilings.get("recovery_pending", False)
            handoff = {}
            if pending:
                for chunk in head.published_by.ledger_chunks.exclude(
                    part="cleanup_intent"
                ):
                    handoff.setdefault(chunk.part, []).extend(chunk.payload)
            attempt = self.begin(
                purpose="compaction",
                ceilings={"manifest": str(head.id), "recovery_pending": pending},
                ledger=handoff,
            )
            from tracer.services.derived_metrics.backfill import obsolete_versions

            obsolete = obsolete_versions(self.lease.project_id, now=self.lease.clock())
            for table in TABLE_KINDS.values():
                prefix = "field" if table == FIELD_VALUES else "metric"
                versioned = table in {FIELD_VALUES, BUCKET_TOTALS, RETIRED_PARTS}
                self.freeze_current(
                    attempt,
                    table,
                    head,
                    where=f"({prefix}_id, {prefix}_version) NOT IN %(obsolete_versions)s"
                    if obsolete and versioned
                    else "1",
                    params={"obsolete_versions": obsolete}
                    if obsolete and versioned
                    else None,
                )
            self.seal(attempt, publication={"retire_versions": obsolete})
        manifest = self.stage_and_publish(attempt)
        # Never delete CH history from a transaction that might roll PG back.
        transaction.on_commit(self.collect_garbage)
        return manifest

    def collect_garbage(self):
        """Collect PG reachability first; its durable outbox survives CH failures.

        B18 can retry the live-store portion of the queued history intent. We do
        not mark the multi-store request done or claim snapshot cleanup here.
        Tombstones in the current checkpoint are never selected for deletion.
        """
        from tracer.services.derived_metrics.backfill import discharge_omission_debts
        from tracer.services.derived_metrics.retention import (
            finish_pending_expiry,
            purge_by_scope,
        )

        intent = collect_history(self.lease.project_id, now=self.lease.clock())
        if intent is not None:
            purge_by_scope(self.store, intent)
        self.scrub_orphans()
        # Unregistered attempts' rows are gone now, under ANY retention policy.
        discharge_omission_debts(self.lease.project_id, now=self.lease.clock())
        finish_pending_expiry(self)
        return intent

    def scrub_orphans(self):
        """Reap late writes to attempts PG has already collected.

        A one-shot DELETE is insufficient: an old INSERT can arrive afterward.
        Repeating this maintenance pass also under unlimited retention makes
        those forever-excluded physical payloads collectible without retaining
        their informational ancestry. The project lock prevents a new valid
        attempt from being allocated during the registered-attempt snapshot and
        synchronous mutations. B10 must schedule maintenance even on idle data.
        """
        from tracer.services.derived_metrics.storage import TABLES

        with serialized_project(self.lease.project_id):
            registered = tuple(
                MetricRefreshAttempt.objects.filter(
                    project_id=self.lease.project_id
                ).values_list("id", flat=True)
            ) or (UUID(int=0),)
            for table in TABLES.values():
                self.store.execute(
                    f"ALTER TABLE {table.name} DELETE WHERE org_id = toUUID(%(gc_org)s) "
                    "AND project_id = toUUID(%(gc_project)s) AND attempt_id NOT IN %(gc_registered)s SETTINGS mutations_sync=2",
                    {
                        "gc_org": str(self.org_id),
                        "gc_project": str(self.lease.project_id),
                        "gc_registered": registered,
                    },
                )

    def refresh(
        self,
        change,
        definitions,
        scope,
        *,
        start,
        end,
        as_of,
        settle_minutes=30,
        purpose="incremental",
        publication=None,
    ):
        """Never consume ambiguous discovery; retain only a nonidentifying flag."""
        try:
            return self._refresh(
                change,
                definitions,
                scope,
                start=start,
                end=end,
                as_of=as_of,
                settle_minutes=settle_minutes,
                purpose=purpose,
                publication=publication,
            )
        except MetricAdmissionUnproven:
            MetricProjectRefresh.objects.filter(
                project_id=self.lease.project_id
            ).update(
                pending_repair=True,
                pending_expiry=True,
                last_error_code=MetricAdmissionUnproven.code,
            )
            raise

    def retained_takeover(
        self, definitions, scope, *, cutoff, start, end, as_of, settle_minutes
    ):
        """A retention-only handoff may copy ONLY provably retained owners.

        If failed work discovers an older anchor or an already-expired owner,
        recompute it in the integrated publication instead. Publishing a copy of
        its formerly recent field first would create an unsafe intermediate head.
        """
        head = MetricProjectRefresh.for_project(self.lease.project_id).head_manifest
        if head is None:
            return True
        chunks = MetricAttemptLedgerChunk.objects.filter(
            attempt__project_id=self.lease.project_id,
            attempt__state__in=MetricRefreshAttempt.OPEN_STATES,
        ).exclude(part="cleanup_intent")
        entries = [e for chunk in chunks for e in chunk.payload]
        if head.published_by.source_ceilings.get("recovery_pending"):
            entries.extend(
                e
                for c in head.published_by.ledger_chunks.exclude(part="cleanup_intent")
                for e in c.payload
            )
        ids = sorted(
            {
                str(e.get("unit_id") or e.get("canonical_session_id"))
                for e in entries
                if e.get("unit_id") or e.get("canonical_session_id")
            }
        )
        if not ids:
            return True
        fields, fp = committed_rows_select(
            FIELD_VALUES, head, org_id=self.org_id, project_id=self.lease.project_id
        )
        keys = [
            (e["trace_id"], e["span_id"])
            for e in entries
            if e.get("trace_id") and e.get("span_id")
        ]
        for offset in range(0, len(ids), MAX_BATCH_ROWS):
            page = tuple(ids[offset : offset + MAX_BATCH_ROWS])
            anchors = dict(
                self.store.execute(
                    f"SELECT unit_id, min(unit_start) FROM ({fields}) WHERE unit_id IN %(takeover_ids)s GROUP BY unit_id",
                    {**fp, "takeover_ids": page},
                )
            )
            if any(unit not in anchors or anchors[unit] < cutoff for unit in page):
                return False
            for definition in definitions:
                if definition.level == "span":
                    continue
                sql, params = unit_values_select(
                    definition,
                    scope,
                    page,
                    start=start,
                    end=end,
                    as_of=as_of,
                    settle_minutes=settle_minutes,
                    manifest=head,
                    source_identities=keys,
                )
                if self.store.execute(
                    f"SELECT count() FROM ({sql}) WHERE unit_start < toDateTime64(%(takeover_cutoff)s, 6, 'UTC')",
                    {**params, "takeover_cutoff": cutoff},
                )[0][0]:
                    return False
        return True

    def _refresh(
        self,
        change,
        definitions,
        scope,
        *,
        start,
        end,
        as_of,
        settle_minutes=30,
        purpose="incremental",
        publication=None,
    ):
        """Compile B03/B04 proposals into one complete, durable refresh plan."""
        if (
            self.workspace_id is None
            or str(scope.project) != str(self.lease.project_id)
            or str(scope.organization) != str(self.org_id)
            or str(scope.workspace) != str(self.workspace_id)
        ):
            raise MetricStateConflict("refresh scope mismatch")
        if len(change.units) > MAX_PASS_UNITS:
            raise ValueError("metric refresh pass exceeds candidate cap")
        if any(unit.level not in {"conversation", "span"} for unit in change.units):
            raise ValueError("trace metric refresh is disabled")
        if purpose not in {"incremental", "backfill", "repair"}:
            raise ValueError("invalid refresh purpose")
        from tracer.services.derived_metrics.backfill import working_definitions

        definitions = list(definitions)
        from tracer.services.derived_metrics.spec import require_computable

        # Native jobs bypass HTTP admission. Reject explicit legacy recipes
        # before loading stored versions or making any lifecycle changes.
        for definition in definitions:
            require_computable(definition)
        definitions = working_definitions(self.lease.project_id, definitions)
        for definition in definitions:
            require_computable(definition)
        if any(d.level not in {"conversation", "user", "span"} for d in definitions):
            raise ValueError("trace metric refresh is disabled")
        from tracer.services.derived_metrics.retention import (
            expire_project,
            retention_cutoff,
        )

        if self.retention_days is None:
            raise MetricStateConflict(
                "refresh requires current organization retention policy"
            )
        refresh_now = self.lease.clock()
        cutoff = retention_cutoff(self.retention_days(), now=refresh_now)
        from tracer.services.derived_metrics.backfill import pending_omissions

        head = MetricProjectRefresh.for_project(self.lease.project_id).head_manifest
        if (
            head
            and head.revoked_at is None
            and not head.published_by.source_ceilings.get("recovery_blocked")
            and pending_omissions(self.lease.project_id, now=refresh_now)
        ):
            # Retention checkpoints reset the delta chain but copy every version
            # forward, so obsolete/abandoned payload needs its own trigger. Omit
            # BEFORE this pass's expiry: that checkpoint then needs fewer stored
            # recipes, and a version whose pin this expiry invalidates keeps the
            # anonymous history the expiry produces until a later pass omits it.
            self.compact()
        if (
            cutoff is not None
            and not (change.units or change.source_keys)
            and self.retained_takeover(
                definitions,
                scope,
                cutoff=cutoff,
                start=start,
                end=end,
                as_of=as_of,
                settle_minutes=settle_minutes,
            )
        ):
            # Conversation proposals must be integrated BEFORE expiry. In
            # particular a fresh arrival can reopen an already-old field: its
            # former settled value must be dropped, not prematurely retired.
            # Empty takeover passes first persist their retained recovery work.
            from tracer.services.derived_metrics.backfill import retirement_definitions

            expire_project(
                self,
                retirement_definitions(
                    self,
                    MetricProjectRefresh.for_project(
                        self.lease.project_id
                    ).head_manifest,
                    definitions,
                ),
                retention_days=self.retention_days,
                now=refresh_now,
                as_of=as_of,
            )
        head = MetricProjectRefresh.for_project(self.lease.project_id).head_manifest
        if head and head.published_by.source_ceilings.get("recovery_blocked"):
            raise MetricAdmissionUnproven("metric_admission_unproven")
        if head and len(head.delta_attempt_ids) >= MAX_DELTAS_BEFORE_COMPACTION:
            self.compact()
        with serialized_project(self.lease.project_id):
            head = MetricProjectRefresh.for_project(self.lease.project_id).head_manifest
            inherited_floor = (
                head.published_by.source_ceilings.get("retention_floor")
                if head
                else None
            )
            if inherited_floor:
                floor = datetime.fromisoformat(inherited_floor)
                cutoff = max(cutoff, floor) if cutoff is not None else floor
            from tracer.models.derived_metric import DerivedMetricVersion

            for definition in definitions:
                if not DerivedMetricVersion.objects.filter(
                    id=definition.id,
                    metric__project_id=self.lease.project_id,
                    metric__organization_id=self.org_id,
                    metric__deleted=False,
                    pins_revoked_at__isnull=True,
                ).exists():
                    raise MetricStateConflict(
                        "refresh definition is expired or outside project"
                    )
            prior = self.abort_stale()
            # A replacement MUST rediscover old memberships, including candidates
            # unique to the failed pass, rather than silently dropping that work.
            recovered = {
                str(e.get("unit_id"))
                for part in ("dirty_before", "dirty_after", "vanished")
                for e in prior[part]
                if e.get("unit_id")
            }
            ids = {
                unit.unit_id for unit in change.units if unit.level == "conversation"
            } | recovered
            if len(ids) > MAX_PASS_UNITS:
                raise ValueError("replacement dirty set exceeds pass cap")
            attempt = self.begin(
                purpose=purpose,
                ceilings={
                    "as_of": as_of,
                    # Private keysets belong only to the purgeable publication
                    # ledger, never the immutable attempt/audit metadata.
                    "cursor_updates": {
                        key: value
                        for key, value in change.cursor_updates.items()
                        if key != "reconciliation_cursor"
                    },
                    "consumes_recovery": str(head.id)
                    if head
                    and head.published_by.source_ceilings.get("recovery_pending")
                    else None,
                },
                versions=[
                    {
                        "id": d.id,
                        "metric_id": d.metric_id,
                        "version": d.version,
                        "definition_hash": d.definition_hash,
                        "level": d.level,
                    }
                    for d in definitions
                ],
                ledger={
                    "membership_preimage": [
                        asdict(d)
                        for d in (*change.prior_dependencies, *change.dependencies)
                    ]
                },
            )
            # Preserve the complete preimage and dirty keys, not just recovered
            # unit IDs. Source merges can remove their only physical nominee.
            for part, entries in prior.items():
                if part != "cleanup_intent" and entries:
                    self.append(attempt, part, entries)
            source_ids = [(k.trace_id, k.span_id) for k in change.source_keys]
            source_ids.extend(
                (entry["trace_id"], entry["span_id"])
                for entry in prior["membership_preimage"]
                if entry.get("trace_id") and entry.get("span_id")
            )
            recovered_dependencies = self.recover_dependencies(
                prior["membership_preimage"], as_of=as_of
            )
            dependencies = {d.key: d for d in recovered_dependencies}
            dependencies.update({d.key: d for d in change.dependencies})
            ids.update(
                d.canonical_session_id
                for d in recovered_dependencies
                if d.canonical_session_id
            )
            if len(ids) > MAX_PASS_UNITS:
                raise ValueError("replacement owner set exceeds pass cap")
            if any(d.level == "span" for d in definitions):
                self._span_dirty_keys(attempt, change, head, source_ids)
            for definition in definitions:
                if definition.level == "span":
                    continue
                for offset in range(0, len(ids), MAX_BATCH_ROWS):
                    candidates = tuple(sorted(ids))[offset : offset + MAX_BATCH_ROWS]
                    old_sql, old_params = committed_rows_select(
                        FIELD_VALUES, head, org_id=self.org_id, project_id=scope.project
                    )
                    where = (
                        "field_id = toUUID(%(field)s) AND field_version = %(version)s"
                    )
                    old_params.update(
                        field=str(definition.metric_id),
                        version=definition.version,
                        ids=candidates,
                    )
                    before = self.store.execute(
                        f"SELECT unit_id, bucket_start, group_key, sampling_scope, sampling_stratum FROM ({old_sql}) WHERE {where} AND unit_id IN %(ids)s",
                        old_params,
                    )
                    self.append(
                        attempt,
                        "dirty_before",
                        [
                            dict(
                                zip(
                                    (
                                        "unit_id",
                                        "bucket_start",
                                        "group_key",
                                        "scope_key",
                                        "stratum_key",
                                    ),
                                    row,
                                    strict=True,
                                )
                            )
                            for row in before
                        ],
                    )
                    query, params = unit_values_select(
                        definition,
                        scope,
                        candidates,
                        start=start,
                        end=end,
                        as_of=as_of,
                        filters=definition.filters,
                        settle_minutes=settle_minutes,
                        manifest=head,
                        source_identities=source_ids,
                    )
                    states, state_params = committed_rows_select(
                        UNIT_STATE, head, org_id=self.org_id, project_id=scope.project
                    )
                    # A deleted latest span can lower the live-source arrival.
                    # Durable committed knowledge is a floor, never settle early.
                    floor = "greatest(f.last_arrival_ns, ifNull(p.last_arrival_ns, 0))"
                    due = f"fromUnixTimestamp64Nano(toInt64({floor}), 'UTC') + toIntervalMinute(%(floor_settle)s)"
                    still_open = f"f.is_deleted = 0 AND {due} > toDateTime64(%(floor_as_of)s, 6, 'UTC')"
                    provisional = ", ".join(
                        f"if({still_open}, NULL, f.{c}) AS {c}"
                        for c in ("value_bool", "value_number", "value_text")
                    )
                    query = (
                        f"SELECT f.* REPLACE({floor} AS last_arrival_ns, "
                        f"if({still_open}, 'open', f.status) AS status, "
                        f"{provisional}, if({still_open}, NULL, f.settled_at) AS settled_at) "
                        f"FROM ({query}) f LEFT JOIN ({states}) p ON f.level = p.level AND f.unit_id = p.unit_id"
                    )
                    params.update(state_params)
                    params.update(floor_settle=settle_minutes, floor_as_of=as_of)
                    if cutoff is not None:
                        # Keep an expired proposal for an EXISTING logical field:
                        # the same publication must retire it and invalidate its
                        # former bucket, not silently leave that field current.
                        # Previously purged/unknown old evidence is not a backfill.
                        query = (
                            f"SELECT * FROM ({query}) WHERE unit_start >= "
                            "toDateTime64(%(refresh_cutoff)s, 6, 'UTC') "
                            f"OR unit_id IN (SELECT unit_id FROM ({old_sql}) WHERE {where})"
                        )
                        params.update(old_params)
                        params["refresh_cutoff"] = cutoff
                        if (
                            before
                            and self.store.execute(
                                f"SELECT count() FROM ({query}) f INNER JOIN "
                                f"(SELECT unit_id, unit_start AS previous_start FROM ({old_sql}) WHERE {where}) o "
                                "ON f.unit_id = o.unit_id WHERE f.is_deleted = 0 "
                                "AND o.previous_start < toDateTime64(%(refresh_cutoff)s, 6, 'UTC') "
                                "AND f.unit_start >= toDateTime64(%(refresh_cutoff)s, 6, 'UTC')",
                                params,
                            )[0][0]
                        ):
                            # Surviving young members alone cannot distinguish
                            # physical pruning from an authoritative deletion.
                            raise MetricAdmissionUnproven("metric_admission_unproven")
                    if (
                        inherited_floor
                        and self.store.execute(
                            f"SELECT count() FROM ({query}) WHERE is_deleted = 0 AND unit_id NOT IN "
                            f"(SELECT unit_id FROM ({old_sql}) WHERE {where} AND is_deleted = 0) "
                            f"AND (unit_id, unit_start) NOT IN (SELECT unit_id, unit_start FROM ({old_sql}) "
                            "WHERE is_deleted = 0 AND level = 'conversation' "
                            "AND unit_start >= toDateTime64(%(admission_floor)s, 6, 'UTC'))",
                            {**params, "admission_floor": cutoff or inherited_floor},
                        )[0][0]
                    ):
                        # A new version may reuse a still-retained, identical
                        # committed anchor. Unknown or corrected anchors cannot
                        # be proven by a recent raw row (TH-8975 remains held).
                        # No identity/hash tombstone is created to guess here.
                        raise MetricAdmissionUnproven("metric_admission_unproven")
                    extra = {
                        "field_id": "toUUID(%(metric)s)",
                        "field_version": "toUInt32(%(metric_version)s)",
                    }
                    frozen = self.freeze(
                        attempt,
                        FIELD_VALUES,
                        query,
                        {
                            **params,
                            "metric": str(definition.metric_id),
                            "metric_version": definition.version,
                        },
                        extra=extra,
                    )
                    new_sql, new_params = payload_select(
                        FIELD_VALUES, frozen, prefix="new_fields"
                    )
                    after = self.store.execute(
                        f"SELECT unit_id, bucket_start, group_key, sampling_scope, sampling_stratum, is_deleted FROM ({new_sql})",
                        new_params,
                    )
                    self.append(
                        attempt,
                        "dirty_after",
                        [
                            dict(
                                zip(
                                    (
                                        "unit_id",
                                        "bucket_start",
                                        "group_key",
                                        "scope_key",
                                        "stratum_key",
                                        "is_deleted",
                                    ),
                                    row,
                                    strict=True,
                                )
                            )
                            for row in after
                        ],
                    )
                    self.append(
                        attempt,
                        "vanished",
                        [{"unit_id": row[0]} for row in after if row[-1]],
                    )
            # Dependencies/lifecycle are part of the SAME generation. B04 carries
            # tombstones as well as old memberships; neither is an independent write.
            self.freeze_dependencies(attempt, dependencies.values(), cutoff=cutoff)
            # BL01-Q must qualify and byte-bound current-fact maintenance before
            # it joins production refreshes. Copying full message inputs here can
            # exceed the batch cap even when the scalar metric payload is small.
            field_payloads = [
                FrozenRows(tuple(p["rows"]))
                for p in self.plans(attempt, kind="field_values")
            ]
            overlay, overlay_params = payload_select(
                FIELD_VALUES, field_payloads, prefix="overlay"
            )
            if field_payloads:
                prior_states, prior_params = committed_rows_select(
                    UNIT_STATE,
                    head,
                    org_id=self.org_id,
                    project_id=self.lease.project_id,
                )
                state = f"""SELECT f.level AS level, f.unit_id AS unit_id, max(f.last_arrival_ns) AS last_arrival_ns,
                    if(countIf(p.unit_id != '') > 0, min(p.first_seen), toDateTime64(%(state_first_seen)s, 6, 'UTC')) AS first_seen,
                    toUInt8(max(p.reopened) OR (max(p.is_deleted) AND countIf(f.status = 'open') > 0)) AS reopened,
                    toUInt8(countIf(f.is_deleted = 0 AND f.status = 'open') = 0) AS is_deleted,
                    fromUnixTimestamp64Nano(toInt64(last_arrival_ns), 'UTC') + toIntervalMinute(%(settle)s) AS due_at
                    FROM ({overlay}) f LEFT JOIN ({prior_states}) p ON f.level = p.level AND f.unit_id = p.unit_id
                    GROUP BY f.level, f.unit_id"""
                self.freeze(
                    attempt,
                    UNIT_STATE,
                    state,
                    {
                        **overlay_params,
                        **prior_params,
                        "settle": settle_minutes,
                        "state_first_seen": as_of,
                    },
                )
            for definition in definitions:
                if definition.level == "span" and cutoff is None:
                    self._freeze_span_buckets(
                        attempt, head, definition, scope, as_of=as_of, cutoff=cutoff
                    )
                elif definition.level not in {"user", "span"} and cutoff is None:
                    self._freeze_buckets(
                        attempt, head, definition, overlay, overlay_params
                    )
            if cutoff is not None:
                from tracer.services.derived_metrics.backfill import (
                    retirement_definitions,
                )
                from tracer.services.derived_metrics.retention import retirement_plan

                attempt = retirement_plan(
                    self,
                    attempt,
                    head,
                    retirement_definitions(self, head, definitions),
                    cutoff,
                )
            self.seal(
                attempt, cursors=dict(change.cursor_updates), publication=publication
            )
        manifest = self.stage_and_publish(attempt)
        if cutoff is not None:
            from tracer.services.derived_metrics.retention import purge_checkpoint

            purge_checkpoint(self, attempt, manifest)
        return manifest

    def recover_dependencies(self, entries, *, as_of):
        """Recompute frozen preimages, including new owners, before consuming work."""
        from tracer.services.clickhouse.v2.id_remap_sql import (
            remap_left_join,
            resolved_id_expr,
        )
        from tracer.services.derived_metrics.changes import Dependency

        keys = sorted(
            {
                (e["trace_id"], e["span_id"])
                for e in entries
                if e.get("trace_id") and e.get("span_id")
            }
        )
        result = {}
        for offset in range(0, len(keys), MAX_BATCH_ROWS):
            rows = self.store.execute(
                "SELECT s.trace_id, s.id, toString(ifNull(s.trace_session_id, toUUID(%(nil)s))), "
                f"toString(ifNull({resolved_id_expr('s.trace_session_id', 'sm')}, toUUID(%(nil)s))), "
                "toString(ifNull(s.end_user_id, toUUID(%(nil)s))), "
                f"toString(ifNull({resolved_id_expr('s.end_user_id', 'um')}, toUUID(%(nil)s))), "
                "s.start_time, s.is_deleted FROM (SELECT * FROM spans "
                "PREWHERE project_id = toUUID(%(project)s) "
                "WHERE (trace_id, id) IN %(keys)s AND (org_id = toUUID(%(org)s) OR isNull(org_id)) "
                "AND created_at <= %(as_of)s AND updated_at <= %(as_of)s "
                "ORDER BY _version DESC LIMIT 1 BY project_id, trace_id, id) s "
                f"{remap_left_join('s.trace_session_id', 'trace_session_id_remap', 'sm')} "
                f"{remap_left_join('s.end_user_id', 'end_user_id_remap', 'um')}",
                {
                    "project": str(self.lease.project_id),
                    "org": str(self.org_id),
                    "keys": tuple(keys[offset : offset + MAX_BATCH_ROWS]),
                    "as_of": as_of,
                    "nil": str(UUID(int=0)),
                },
            )
            for row in rows:
                normalized = ["" if v == str(UUID(int=0)) else v for v in row]
                dependency = Dependency(
                    *normalized[:-1], is_deleted=bool(normalized[-1])
                )
                result[(dependency.trace_id, dependency.span_id)] = dependency
        for entry in entries:
            key = entry.get("trace_id"), entry.get("span_id")
            if key not in result and all(
                name in entry
                for name in (
                    "trace_id",
                    "span_id",
                    "raw_session_id",
                    "canonical_session_id",
                    "raw_user_id",
                    "canonical_user_id",
                    "unit_start",
                )
            ):
                dependency = Dependency(
                    *(
                        entry[name]
                        for name in (
                            "trace_id",
                            "span_id",
                            "raw_session_id",
                            "canonical_session_id",
                            "raw_user_id",
                            "canonical_user_id",
                        )
                    ),
                    unit_start=datetime.fromisoformat(str(entry["unit_start"])),
                    is_deleted=True,
                )
                result[key] = dependency
        return tuple(result.values())

    def _span_dirty_keys(self, attempt, change, head, source_ids):
        """Nominate old/new starts without treating mutable starts as identity."""
        for unit in change.units:
            if unit.level == "span":
                identity = json.loads(unit.unit_id)
                if not isinstance(identity, list) or len(identity) != 2:
                    raise ValueError("span candidates require [trace_id, span_id]")
                source_ids.append(tuple(map(str, identity)))
        source_ids = sorted(set(source_ids))
        self.append(
            attempt,
            "membership_preimage",
            [{"trace_id": trace, "span_id": span} for trace, span in source_ids],
        )
        starts = {
            d.unit_start for d in (*change.prior_dependencies, *change.dependencies)
        }
        deps, dp = committed_rows_select(
            SOURCE_DEPENDENCIES,
            head,
            org_id=self.org_id,
            project_id=self.lease.project_id,
        )
        for offset in range(0, len(source_ids), MAX_BATCH_ROWS):
            bound = {
                **dp,
                "span_sources": tuple(source_ids[offset : offset + MAX_BATCH_ROWS]),
            }
            starts.update(
                row[0]
                for row in self.store.execute(
                    f"SELECT DISTINCT unit_start FROM ({deps}) WHERE (trace_id, span_id) IN %(span_sources)s",
                    bound,
                )
            )
            starts.update(
                row[0]
                for row in self.store.execute(
                    "SELECT DISTINCT start_time FROM spans PREWHERE project_id = toUUID(%(span_project)s) "
                    "AND (trace_id, id) IN %(span_sources)s WHERE org_id = toUUID(%(span_org)s) OR isNull(org_id)",
                    {
                        **bound,
                        "span_project": str(self.lease.project_id),
                        "span_org": str(self.org_id),
                    },
                )
            )
        self.append(
            attempt,
            "dirty_after",
            [
                {
                    "level": "span",
                    "bucket_start": at.replace(
                        minute=at.minute // 15 * 15, second=0, microsecond=0
                    ),
                }
                for at in sorted(starts)
            ],
        )

    def _freeze_span_buckets(
        self, attempt, head, definition, scope, *, as_of, cutoff, retired_override=None
    ):
        """Direct deduped source totals. No span field or lifecycle rows are stored."""
        from tracer.services.derived_metrics.unit_reads import span_values_select

        dirty = {
            entry["bucket_start"]
            for chunk in attempt.ledger_chunks.filter(
                part__in=["dirty_before", "dirty_after"]
            )
            for entry in chunk.payload
            if "bucket_start" in entry
        }
        old, op = committed_rows_select(
            BUCKET_TOTALS, head, org_id=self.org_id, project_id=self.lease.project_id
        )
        retired, rp = retired_override or committed_rows_select(
            RETIRED_PARTS, head, org_id=self.org_id, project_id=self.lease.project_id
        )
        for value in sorted(dirty):
            at = datetime.fromisoformat(str(value))
            until = at + timedelta(minutes=15)
            # A wholly expired bucket still needs a replacement when this
            # publication freezes its final anonymous parts. Its live interval
            # is empty; source deltas cannot alter already-retired observations.
            fields, params = span_values_select(
                definition,
                scope,
                start=min(max(at, cutoff), until) if cutoff is not None else at,
                end=until,
                as_of=as_of,
            )
            params.update(op)
            params.update(rp)
            if head and head.published_by.source_ceilings.get("retention_floor"):
                dependencies, dp = committed_rows_select(
                    SOURCE_DEPENDENCIES,
                    head,
                    org_id=self.org_id,
                    project_id=self.lease.project_id,
                )
                params.update(dp)
                if self.store.execute(
                    f"SELECT count() FROM ({fields}) WHERE unit_id NOT IN "
                    f"(SELECT toJSONString(tuple(trace_id, span_id)) FROM ({dependencies}) WHERE is_deleted = 0)",
                    params,
                )[0][0]:
                    raise MetricAdmissionUnproven("metric_admission_unproven")
            params.update(
                span_metric=str(definition.metric_id),
                span_version=definition.version,
                span_bucket=at,
            )
            predicate = "metric_id = toUUID(%(span_metric)s) AND metric_version = %(span_version)s AND bucket_start = toDateTime(%(span_bucket)s, 'UTC')"
            previous = f"SELECT * FROM ({old}) WHERE {predicate}"
            anonymous = f"SELECT * FROM ({retired}) WHERE {predicate}"
            parts = self._anonymous_bucket_parts(
                attempt, definition, fields, anonymous, params
            )
            self.freeze(
                attempt,
                BUCKET_TOTALS,
                replacement_select(parts, previous),
                params,
                extra={
                    "metric_id": "toUUID(%(span_metric)s)",
                    "metric_version": "toUInt32(%(span_version)s)",
                },
            )

    def _anonymous_bucket_parts(self, attempt, definition, fields, retired, params):
        """Freeze pre-fold parts once, before ranking and vanished-key expansion.

        Only anonymous opaque parts cross this boundary. In particular spans
        never acquire persisted field rows, and folding cannot duplicate the
        full private field/source compiler tree in every replacement branch.
        The final whole-bucket bytes, not this transient intermediate, are the
        durable replay plan prepared before the first INSERT.
        """
        from tracer.services.derived_metrics.buckets import fold_parts_select

        params.update(
            parts_metric=str(definition.metric_id), parts_version=definition.version
        )
        raw = rebuilt_parts_select(definition, fields, retired, fold_groups=False)
        payloads = self.freeze(
            attempt,
            RETIRED_PARTS,
            f"SELECT *, toUInt8(0) AS is_deleted FROM ({raw})",
            params,
            transient=True,
            extra={
                "metric_id": "toUUID(%(parts_metric)s)",
                "metric_version": "toUInt32(%(parts_version)s)",
            },
        )
        sql, bound = payload_select(RETIRED_PARTS, payloads, prefix="anonymous_parts")
        params.update(bound)
        return fold_parts_select(sql)

    def freeze_dependencies(self, attempt, dependencies, *, cutoff=None):
        """Bound the INPUT relation before ClickHouse parses the SELECT.

        The output LIMIT in freeze() cannot bound a UNION's input AST. Discovery
        may nominate 10,000 sources, so each finite relation is constructed from
        at most one publication batch, and ALL batches are frozen before seal.
        """
        dependencies = list(dependencies)
        for offset in range(0, len(dependencies), MAX_BATCH_ROWS):
            sql, params = dependency_select(
                dependencies[offset : offset + MAX_BATCH_ROWS]
            )
            if cutoff is not None:
                sql = f"SELECT * FROM ({sql}) WHERE unit_start >= toDateTime64(%(refresh_cutoff)s, 6, 'UTC')"
                params["refresh_cutoff"] = cutoff
            self.freeze(attempt, SOURCE_DEPENDENCIES, sql, params)

    def _freeze_buckets(
        self,
        attempt,
        head,
        definition,
        overlay,
        overlay_params,
        *,
        complete_fields=False,
        retired_override=None,
    ):
        field, params = committed_rows_select(
            FIELD_VALUES, head, org_id=self.org_id, project_id=self.lease.project_id
        )
        combined = (
            overlay
            if complete_fields
            else f"SELECT * FROM (({field}) UNION ALL ({overlay})) ORDER BY generation DESC, _version DESC LIMIT 1 BY {', '.join(FIELD_VALUES.logical_key)}"
        )
        filter_sql = "field_id = toUUID(%(bucket_metric)s) AND field_version = %(bucket_metric_version)s"
        params.update(overlay_params)
        params.update(
            bucket_metric=str(definition.metric_id),
            bucket_metric_version=definition.version,
        )
        # Dirty BEFORE keys include starts lost after source/field OPTIMIZE.
        dirty = set()
        for chunk in attempt.ledger_chunks.filter(
            part__in=["dirty_before", "dirty_after"]
        ):
            dirty.update(
                entry["bucket_start"]
                for entry in chunk.payload
                if "bucket_start" in entry
            )
        for start in sorted(dirty):
            p = {**params, "dirty_start": start}
            predicate = "bucket_start = parseDateTimeBestEffort(%(dirty_start)s, 'UTC')"
            fields = f"SELECT * FROM ({combined}) WHERE {filter_sql} AND {predicate}"
            retired, rp = committed_rows_select(
                RETIRED_PARTS,
                head,
                org_id=self.org_id,
                project_id=self.lease.project_id,
            )
            old, op = committed_rows_select(
                BUCKET_TOTALS,
                head,
                org_id=self.org_id,
                project_id=self.lease.project_id,
            )
            metric_where = "metric_id = toUUID(%(bucket_metric)s) AND metric_version = %(bucket_metric_version)s"
            retired = f"SELECT * FROM ({retired}) WHERE {metric_where} AND {predicate}"
            old = f"SELECT * FROM ({old}) WHERE {metric_where} AND {predicate}"
            if retired_override is not None:
                retired, rp = retired_override
                retired = (
                    f"SELECT * FROM ({retired}) WHERE {metric_where} AND {predicate}"
                )
            p.update(rp)
            p.update(op)
            parts = self._anonymous_bucket_parts(
                attempt, definition, fields, retired, p
            )
            replacement = replacement_select(parts, old)
            vanished = self.store.execute(
                f"SELECT {', '.join(KEYS)} FROM ({old}) WHERE ({', '.join(KEYS)}) NOT IN (SELECT {', '.join(KEYS)} FROM ({parts}))",
                p,
            )
            self.append(
                attempt,
                "vanished",
                [dict(zip(KEYS, row, strict=True)) for row in vanished],
            )
            self.freeze(
                attempt,
                BUCKET_TOTALS,
                replacement,
                p,
                extra={
                    "metric_id": "toUUID(%(bucket_metric)s)",
                    "metric_version": "toUInt32(%(bucket_metric_version)s)",
                },
            )


def dependency_select(dependencies):
    if not 0 < len(dependencies) <= MAX_BATCH_ROWS:
        raise ValueError("dependency input must fit one publication batch")
    # Typed format data is one bound string, not one parsed SELECT per source.
    # Keep its input bounded too: freeze's output LIMIT is not a parser bound.
    rows = []
    for dependency in dependencies:
        payload = dependency.payload()
        payload["unit_start"] = dependency.unit_start.astimezone(UTC).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )
        payload["is_deleted"] = int(dependency.is_deleted)
        rows.append(json.dumps(payload))
    structure = ", ".join(
        f"{name} {kind}" for name, kind in SOURCE_DEPENDENCIES.schema if name in payload
    )
    return (
        "SELECT * FROM format(JSONEachRow, %(dependency_structure)s, %(dependency_data)s)",
        {"dependency_structure": structure, "dependency_data": "\n".join(rows)},
    )
