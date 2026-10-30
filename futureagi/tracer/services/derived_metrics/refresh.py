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
from datetime import UTC, datetime
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
    ):
        """Freeze SELECT output while PREPARED, with bounded sequence allocation.

        SQL/identifiers are compiler-owned, never client SQL. Each page is at
        most 500 rows; no UPDATE ever copies a payload. All output pages are
        durable before seal() permits staging. Empty tables need no INSERT.
        """
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

    def seal(self, attempt, *, cursors=None):
        self.append(
            attempt,
            "cleanup_intent",
            [{"kind": "publication", "cursors": _json(cursors or {})}],
        )
        chunks = MetricAttemptLedgerChunk.objects.filter(attempt=attempt)
        MetricRefreshAttempt.seal_ledger(
            attempt.id,
            expected_chunks=chunks.count(),
            expected_keys=sum(chunks.values_list("key_count", flat=True)),
            **self.lease.check(),
        )

    def stage_and_publish(self, attempt):
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
                    self.store.insert(table, payload)
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
            return MetricReadManifest.publish(
                attempt.id,
                cursors=controls[0]["cursors"],
                visibility_receipts={
                    "shard": self.store.identity(),
                    "batches": receipts,
                },
                **self.lease.check(),
            )

    def abort_stale(self):
        """Return the ORIGINAL dirty ledger for replacement discovery, then abort."""
        entries = {part: [] for part in MetricAttemptLedgerChunk.Part.values}
        for attempt in MetricRefreshAttempt.objects.filter(
            project_id=self.lease.project_id,
            state__in=MetricRefreshAttempt.OPEN_STATES,
            fencing_epoch__lt=self.lease.grant.fencing_epoch,
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
            attempt = self.begin(
                purpose="compaction", ceilings={"manifest": str(head.id)}
            )
            for table in TABLE_KINDS.values():
                self.freeze_current(attempt, table, head)
            self.seal(attempt)
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
        from tracer.services.derived_metrics.retention import purge_by_scope

        intent = collect_history(self.lease.project_id, now=self.lease.clock())
        if intent is not None:
            purge_by_scope(self.store, intent)
        self.scrub_orphans()
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
        self, change, definitions, scope, *, start, end, as_of, settle_minutes=30
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
        if any(unit.level != "conversation" for unit in change.units):
            raise ValueError("trace and span metric refresh are disabled")
        definitions = list(definitions)
        if any(d.level not in {"conversation", "user"} for d in definitions):
            raise ValueError("only conversation-backed fields are enabled")
        from tracer.services.derived_metrics.retention import (
            expire_project,
            retention_cutoff,
        )

        if self.retention_days is None:
            raise MetricStateConflict(
                "refresh requires current organization retention policy"
            )
        cutoff = retention_cutoff(self.retention_days(), now=self.lease.clock())
        if cutoff is not None:
            expire_project(self, definitions, retention_days=self.retention_days)
        head = MetricProjectRefresh.for_project(self.lease.project_id).head_manifest
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
            ids = {unit.unit_id for unit in change.units} | recovered
            if len(ids) > MAX_PASS_UNITS:
                raise ValueError("replacement dirty set exceeds pass cap")
            attempt = self.begin(
                ceilings={"as_of": as_of, "cursor_updates": change.cursor_updates},
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
                        asdict(d) for d in change.prior_dependencies
                    ]
                },
            )
            source_ids = [(k.trace_id, k.span_id) for k in change.source_keys]
            for definition in definitions:
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
                        query = f"SELECT * FROM ({query}) WHERE unit_start >= toDateTime64(%(refresh_cutoff)s, 6, 'UTC')"
                        params["refresh_cutoff"] = cutoff
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
            self.freeze_dependencies(attempt, change.dependencies, cutoff=cutoff)
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
                    toUInt8(max(f.is_deleted) OR countIf(f.status = 'open') = 0) AS is_deleted,
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
                if definition.level == "user":
                    continue
                self._freeze_buckets(attempt, head, definition, overlay, overlay_params)
            self.seal(attempt, cursors=dict(change.cursor_updates))
        return self.stage_and_publish(attempt)

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

    def _freeze_buckets(self, attempt, head, definition, overlay, overlay_params):
        field, params = committed_rows_select(
            FIELD_VALUES, head, org_id=self.org_id, project_id=self.lease.project_id
        )
        combined = f"SELECT * FROM (({field}) UNION ALL ({overlay})) ORDER BY generation DESC, _version DESC LIMIT 1 BY {', '.join(FIELD_VALUES.logical_key)}"
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
            p.update(rp)
            p.update(op)
            parts = rebuilt_parts_select(definition, fields, retired)
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
