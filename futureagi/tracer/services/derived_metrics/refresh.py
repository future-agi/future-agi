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
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

import structlog
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.db.models import BigIntegerField, Count, Max, Q, Sum
from django.db.models.functions import Cast

from tracer.models.derived_metric import (
    LEDGER_CHUNK_MAX_KEYS,
    MAX_MANIFEST_DELTAS,
    MetricAttemptBatch,
    MetricAttemptLedgerChunk,
    MetricLedgerConflict,
    MetricProjectRefresh,
    MetricReadManifest,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.derived_metrics.bounded_reads import (
    BUCKET_INPUT_ROWS,
    MAX_KEY_IN_ITEMS,
    BucketFieldPlan,
    BucketRangeOverflow,
    capture_source_output,
    committed_key_pages,
    committed_key_ranges,
    field_window_requires_subdivision,
    iter_key_membership_batches,
    merge_bindings,
)
from tracer.services.derived_metrics.buckets import (
    KEYS,
    replacement_select,
)
from tracer.services.derived_metrics.deletion_protocol import (
    collect_history,
    serialized_project,
    unreachable_closed_attempts,
)
from tracer.services.derived_metrics.lease import MetricPassYield
from tracer.services.derived_metrics.publication import (
    MAX_BATCH_ROWS,
    MAX_COMPACTION_ROWS,
    FrozenRows,
    MetricCheckpointLayoutPending,
    MetricVisibilityError,
    committed_rows_select,
    physical_table,
)
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_CHECKPOINT,
    FIELD_VALUES,
    LIST_CURRENT_MEMBERS,
    LIST_CURRENT_ROOTS,
    LIST_USER_ACTIVITY,
    RETIRED_PARTS,
    SOURCE_DEPENDENCIES,
    UNIT_STATE,
)
from tracer.services.derived_metrics.unit_reads import (
    source_identity_relation,
    unit_first_created_select,
    unit_identity_relation,
    unit_source_floor_select,
    unit_source_guard_select,
    unit_source_version_select,
    unit_values_select,
)

logger = structlog.get_logger(__name__)

MAX_PASS_UNITS = 50_000
# A pass that stops at its soft budget aborts its attempt with this reason;
# the next pass carries its ledger and marks it carried (abort_stale).
PASS_YIELD = "pass_yield"
PASS_YIELD_CARRIED = "pass_yield_carried"
DEFAULT_PASS_MAX_CAPTURES = 48
DEFAULT_PASS_SOFT_MARGIN_SECONDS = 200
# A head carries at most this many deltas: a pass compacts once it reaches it,
# before beginning its own attempt, so that attempt publishes at most this many.
# Every delta publication passes that trigger (retention and erasure publish
# checkpoints), so 19 keeps one spare under MAX_MANIFEST_DELTAS.
DEFAULT_DELTAS_BEFORE_COMPACTION = 19


def pass_max_captures():
    """Most capture statements a pass plans (METRICS_PASS_MAX_CAPTURES).

    10M: one capture took about 3.5 s, so 48 is about 170 s of a 540 s pass.
    A project that yields lowers its own budget below this (pass_capture_budget).
    """
    value = getattr(settings, "METRICS_PASS_MAX_CAPTURES", DEFAULT_PASS_MAX_CAPTURES)
    if type(value) is not int or not 1 <= value <= 10_000:
        raise ImproperlyConfigured(
            "METRICS_PASS_MAX_CAPTURES must be an integer from 1 to 10000"
        )
    return value


def pass_soft_margin_seconds():
    """Seconds before the pass deadline at which a pass stops cleanly."""
    value = getattr(
        settings, "METRICS_PASS_SOFT_MARGIN_SECONDS", DEFAULT_PASS_SOFT_MARGIN_SECONDS
    )
    if type(value) is not int or not 30 <= value <= 500:
        raise ImproperlyConfigured(
            "METRICS_PASS_SOFT_MARGIN_SECONDS must be an integer from 30 to 500"
        )
    return value


def capture_budget(state):
    """Capture statements this project's next pass may plan."""
    configured = pass_max_captures()
    learned = getattr(state, "pass_capture_budget", None)
    return configured if learned is None else min(configured, learned)


def estimated_captures(definitions, unit_count):
    """Capture statements a pass over ``unit_count`` conversations runs.

    One per conversation-backed definition per 500-unit batch.
    """
    fields = sum(1 for d in definitions if getattr(d, "level", "") != "span")
    return fields * -(-unit_count // MAX_BATCH_ROWS)


def server_side_compaction():
    """Whether compaction may copy checkpoint ranges inside ClickHouse.

    METRICS_SERVER_SIDE_COMPACTION: 1 (default) or 0, which always keeps the
    legacy batch copy. RefreshWriter.compaction_path decides per compaction.
    """
    value = getattr(settings, "METRICS_SERVER_SIDE_COMPACTION", 1)
    if type(value) is not int or value not in (0, 1):
        raise ImproperlyConfigured("METRICS_SERVER_SIDE_COMPACTION must be 0 or 1")
    return value == 1


DEFAULT_SERVER_SIDE_MIN_ROWS = 300_000


def server_side_min_rows():
    """Head rows from which server-side ranges beat the legacy copy.

    Measured: equal at about 100k rows a table (3 tables), and about 2.5x
    faster at 300k a table. METRICS_SERVER_SIDE_MIN_ROWS, 0 to 10^10.
    """
    value = getattr(
        settings, "METRICS_SERVER_SIDE_MIN_ROWS", DEFAULT_SERVER_SIDE_MIN_ROWS
    )
    if type(value) is not int or not 0 <= value <= 10_000_000_000:
        raise ImproperlyConfigured(
            "METRICS_SERVER_SIDE_MIN_ROWS must be an integer from 0 to 10000000000"
        )
    return value


DEFAULT_HISTORY_GC_GRACE_SECONDS = 1800


def history_gc_grace():
    """How long a closed, unreachable attempt keeps its ClickHouse rows.

    Longer than a lease (5 minutes), a request pin (5 minutes) and a statement
    (60 seconds). Configurable: METRICS_HISTORY_GC_GRACE_SECONDS, 600 to 604800.
    """
    value = getattr(
        settings, "METRICS_HISTORY_GC_GRACE_SECONDS", DEFAULT_HISTORY_GC_GRACE_SECONDS
    )
    if type(value) is not int or not 600 <= value <= 604_800:
        raise ImproperlyConfigured(
            "METRICS_HISTORY_GC_GRACE_SECONDS must be an integer from 600 to 604800"
        )
    return timedelta(seconds=value)


def deltas_before_compaction():
    """Deltas a head may carry before the next pass compacts it.

    Configurable: METRICS_MAX_DELTAS_BEFORE_COMPACTION, 1 to 19.
    """
    value = getattr(
        settings,
        "METRICS_MAX_DELTAS_BEFORE_COMPACTION",
        DEFAULT_DELTAS_BEFORE_COMPACTION,
    )
    maximum = MAX_MANIFEST_DELTAS - 1
    if type(value) is not int or not 1 <= value <= maximum:
        raise ImproperlyConfigured(
            f"METRICS_MAX_DELTAS_BEFORE_COMPACTION must be an integer from 1 to {maximum}"
        )
    return value


# Reserve two memberships for the fixed metric/version. Existing physical
# capture caps remain unchanged; overflowing sets split before any output.
BUCKET_WINDOWS_PER_FOLD = MAX_KEY_IN_ITEMS - 2
TABLE_KINDS = {
    "field_values": FIELD_VALUES,
    "bucket_totals": BUCKET_TOTALS,
    "unit_state": UNIT_STATE,
    "source_dependencies": SOURCE_DEPENDENCIES,
    "bucket_retired_parts": RETIRED_PARTS,
    "list_current_root_facts": LIST_CURRENT_ROOTS,
    "list_current_members": LIST_CURRENT_MEMBERS,
    "list_current_user_activity": LIST_USER_ACTIVITY,
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


def captured_through(ceilings):
    """An attempt's capture high-water: its own as_of or the inherited one."""
    values = [
        datetime.fromisoformat(str(value))
        for value in (ceilings.get("captured_through"), ceilings.get("as_of"))
        if value
    ]
    if not values:
        return None
    latest = max(v if v.tzinfo else v.replace(tzinfo=UTC) for v in values)
    return latest


def admission_requires_proof(head):
    """Whether units without committed anchors must prove they are not retired.

    Only once a purge retired live published unit evidence (R02, TH-8975). A
    floor recorded before that was tracked may have, so it still requires it.
    """
    if head is None:
        return False
    ceilings = head.published_by.source_ceilings
    return bool(ceilings.get("retention_floor")) and bool(
        ceilings.get("retention_retired_live", True)
    )


class RefreshWriter:
    def __init__(self, lease, store, *, org_id, workspace_id=None, retention_days=None):
        self.lease, self.store = lease, store
        self.org_id, self.workspace_id = org_id, workspace_id
        # Discovery refresh MUST receive a live organization-policy loader.
        # Low-level replay/compaction may omit it; neither discovers new evidence.
        self.retention_days = retention_days
        self._batch_numbers = {}
        self._batch_context = None

    def _next_batch_number(self, attempt, kind):
        # A rolled-back preparation must not reuse its in-memory counter. Django
        # removes exited atomic blocks on both commit and rollback; retaining the
        # objects (rather than their ids) also prevents identity reuse.
        context = tuple(connection.atomic_blocks)
        if context != self._batch_context:
            self._batch_numbers.clear()
            self._batch_context = context
        key = (attempt.id, kind)
        if key not in self._batch_numbers:
            # One JSON scan per (attempt, table, preparation), NOT per page. No
            # migration is needed; restart derives the durable maximum, not COUNT
            # (which would reuse a number if historical numbering has gaps).
            last = MetricAttemptLedgerChunk.objects.filter(
                attempt=attempt,
                part="cleanup_intent",
                payload__0__kind="frozen_batch",
                payload__0__table=kind,
            ).aggregate(last=Max(Cast("payload__0__batch_no", BigIntegerField())))[
                "last"
            ]
            self._batch_numbers[key] = 0 if last is None else last + 1
        return self._batch_numbers[key]

    def begin(self, *, purpose="incremental", ceilings=None, versions=(), ledger=None):
        with serialized_project(self.lease.project_id):
            self.lease.check()
            from tracer.models.derived_metric import MetricErasureRequest

            # No new attempt can race a whole-project physical purge. Erasure
            # also fences leases at completion, invalidating discovery performed
            # by workers that acquired a fresh lease while cleanup was pending.
            if (
                MetricErasureRequest.objects.filter(organization_key=self.org_id)
                .filter(
                    Q(scope_kind="organization")
                    | Q(scope_kind="project", project_key=self.lease.project_id)
                )
                .exclude(status="done")
                .exists()
            ):
                raise MetricStateConflict("project erasure is in progress")
            ceilings = dict(ceilings or {})
            state = MetricProjectRefresh.for_project(self.lease.project_id)
            head = state.head_manifest
            # A checkpoint may advance no cursor of its own. Preserve the
            # copied head's consumed cut so an old pin never borrows a later
            # publication's watermark when another writer advances the project.
            ceilings["read_watermark_ns"] = state.watermark_ns
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
            if head and (
                head.published_by.source_ceilings.get("recovery_blocked")
                or MetricRefreshAttempt.blocked_expiry_handoffs(head).exists()
            ):
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
            inherited_ceilings = head.published_by.source_ceilings if head else {}
            captured = [
                value
                for value in (
                    captured_through(inherited_ceilings),
                    captured_through(ceilings),
                )
                if value is not None
            ]
            if captured:
                # Capture high-water (every capture reads created_at <= as_of),
                # carried through compactions for retention_floor_at.
                ceilings["captured_through"] = max(captured).isoformat()
            if floors:
                # Nonidentifying, immutable privacy boundary follows every new
                # head/checkpoint, so GC and a larger org horizon cannot restore
                # purged unit evidence via an ordinary incremental refresh.
                ceilings["retention_floor"] = max(floors).isoformat()
                # Admission terms (retention._floor_ceilings): a purge that
                # stamps them decides them; otherwise they are inherited. A
                # floor recorded without them (stamped before they existed, or
                # supplied directly) may have retired live units: it requires
                # proof and exempts nothing.
                for key, default in (
                    ("retention_retired_live", True),
                    ("retention_floor_at", None),
                    ("retention_floor_set_at", None),
                    ("retention_floor_days", None),
                ):
                    if key not in ceilings:
                        ceilings[key] = inherited_ceilings.get(key, default)
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
        last = MetricAttemptLedgerChunk.objects.filter(
            attempt=attempt, part=part
        ).aggregate(last=Max("chunk_no"))["last"]
        count = 0 if last is None else last + 1
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
        row_limit=MAX_BATCH_ROWS,
    ):
        """Freeze SELECT output while PREPARED, with bounded sequence allocation.

        SQL/identifiers are compiler-owned, never client SQL. Non-transient
        discovery pages are at most 500 rows. Checkpoint and anonymous pages have
        separate row/byte bounds. Only non-transient output is durable and
        stageable; anonymous pages are never staged. Empty tables need no INSERT.
        """
        if transient and table != RETIRED_PARTS:
            raise ValueError("only anonymous bucket parts may be transient")
        if row_limit != MAX_BATCH_ROWS and (
            row_limit != MAX_COMPACTION_ROWS
            or (not transient and attempt.purpose != "compaction")
        ):
            raise ValueError(
                "large metric pages require a checkpoint or transient attempt"
            )
        physical = physical_table(table, checkpoint=attempt.purpose == "compaction")
        after, result = None, []
        kind = TABLE_KIND[table.name]
        batch_no = 0 if transient else self._next_batch_number(attempt, kind)
        while True:
            self.lease.keepalive()
            first, _ = MetricProjectRefresh.allocate_row_sequences(
                self.lease.project_id, count=row_limit, **self.lease.fence()
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
                "stamp_limit": row_limit,
            }
            payload = self.store.freeze_select(
                physical, stamped, bound, key_columns=keys, row_limit=row_limit
            )
            if not payload.rows:
                break
            last = first + len(payload.rows) - 1
            entry = {
                "kind": "frozen_batch",
                "table": kind,
                "batch_no": batch_no,
                "rows": list(payload.rows),
                "row_limit": row_limit,
                "sha256": payload.digest,
                "first": first,
                "last": last,
            }
            if not transient:
                try:
                    self.append(attempt, "cleanup_intent", [entry])
                except Exception:
                    # An acknowledgement can be lost after a durable append.
                    # Re-derive from storage if this writer is retried in-process.
                    self._batch_numbers.pop((attempt.id, kind), None)
                    raise
                self._batch_numbers[attempt.id, kind] = batch_no + 1
            if collect_payloads:
                result.append(payload)
            batch_no += 1
            if row_limit == MAX_BATCH_ROWS and len(payload.rows) < row_limit:
                break
            if payload.last_key is None:
                raise MetricLedgerConflict("full frozen page lacks a keyset cursor")
            after = dict(zip(keys, payload.last_key, strict=True))
        return result

    def freeze_current(
        self, attempt, table, manifest, *, where="1", params=None, large_pages=False
    ):
        """Nominate finite full keys, then collapse every revision of those keys.

        The nominated cursor is independent of the surviving output. A page
        filtered entirely by an obsolete-version or retention predicate still
        advances to the next nominated key rather than ending the checkpoint.
        """
        keys = tuple(c for c in table.logical_key if c not in {"org_id", "project_id"})
        nominate = committed_key_ranges if large_pages else committed_key_pages
        row_limit = MAX_COMPACTION_ROWS if large_pages else MAX_BATCH_ROWS
        for nominated in nominate(
            self.store,
            table,
            manifest,
            org_id=self.org_id,
            project_id=self.lease.project_id,
            target_rows=row_limit,
        ):
            restriction = (
                {"key_relation": nominated}
                if large_pages
                else {"key_in": {keys: nominated}}
            )
            sql, bound = committed_rows_select(
                table,
                manifest,
                org_id=self.org_id,
                project_id=self.lease.project_id,
                **restriction,
            )
            self.freeze(
                attempt,
                table,
                f"SELECT * FROM ({sql}) WHERE {where}",
                merge_bindings(bound, params or {}),
                collect_payloads=False,
                row_limit=row_limit,
                extra={"workspace_id": "workspace_id"}
                if "workspace_id" in table.columns
                else None,
            )
        return []

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
        metadata = MetricAttemptLedgerChunk.objects.filter(attempt=attempt).aggregate(
            chunks=Count("id"), keys=Sum("key_count")
        )
        MetricRefreshAttempt.seal_ledger(
            attempt.id,
            expected_chunks=metadata["chunks"],
            expected_keys=metadata["keys"] or 0,
            **self.lease.check(),
        )

    def stage_and_publish(self, attempt, *, source_cut=None):
        """Do not publish a frozen plan that violates the CURRENT policy."""
        try:
            manifest = self._stage_and_publish(attempt, source_cut=source_cut)
            if (
                attempt.purpose == "compaction"
                and "manifest" in attempt.source_ceilings
            ):
                try:
                    from tracer.models.derived_metric import MetricErasureRequest

                    # Qualify only controls already present before maintenance.
                    # A concurrent later publication must keep its own work.
                    controls = list(
                        MetricErasureRequest.objects.filter(
                            project_key=attempt.project_id,
                            store_payload__protocol="metric_checkpoint_layout",
                            store_payload__kind="checkpoint",
                        )
                        .exclude(status="done")
                        .values_list("id", flat=True)
                    )
                    self.lease.keepalive()
                    self.store.consolidate_checkpoint(
                        attempt.project_id,
                        deadline=getattr(self, "_compaction_deadline", None),
                    )
                    MetricErasureRequest.objects.filter(id__in=controls).update(
                        status="done",
                        completed_at=self.lease.clock(),
                        receipts={"layout": {"drained": 1}},
                    )
                except Exception as exc:
                    # The head switch has committed. Never report this as a
                    # failed publication or allocate a new attempt on replay.
                    raise MetricCheckpointLayoutPending() from exc
            return manifest
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
                MetricRefreshAttempt.abort(
                    attempt.id, reason=PASS_YIELD, **self.lease.check()
                )
            raise

    def _stage_and_publish(self, attempt, *, source_cut=None):
        """Idempotent after partial staging OR a lost publish acknowledgement."""
        attempt.refresh_from_db()
        if source_cut is not None or "a01_source_cut" in attempt.source_ceilings:
            from tracer.services.derived_metrics.base_list_current_source_publication import (
                source_cut_ceilings,
            )

            if (
                attempt.source_ceilings.get("a01_source_cut")
                != source_cut_ceilings(source_cut)["a01_source_cut"]
            ):
                raise MetricLedgerConflict("frozen source cut changed on replay")
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
        range_receipts = []
        if attempt.source_ceilings.get("range_compaction"):
            from tracer.services.derived_metrics.compaction_ranges import (
                stage_ranges,
                visibility_receipts,
            )

            if attempt.state != "verified":
                stage_ranges(self, attempt)
            range_receipts = visibility_receipts(attempt)
        receipts = []
        for plan in self.plans(attempt):
            self.lease.keepalive()
            if (
                plan.get("row_limit", MAX_BATCH_ROWS) != MAX_BATCH_ROWS
                and attempt.purpose != "compaction"
            ):
                raise MetricLedgerConflict("large frozen page requires compaction")
            payload = FrozenRows.from_plan(plan)
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
        oldest = self.retention_evidence(attempt)
        shard = self.store.identity()
        if attempt.purpose != "compaction" and attempt.parent_manifest_id:
            self.store.assert_visible(attempt.parent_manifest)
        with serialized_project(attempt.project_id):
            self.check_current_retention(attempt, oldest)
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
                from tracer.services.derived_metrics.compaction_ranges import (
                    RECEIPT_BYTES,
                    canonical,
                )

                # Bound inherited metadata BEFORE allocating concatenated lists.
                if (
                    sum(
                        len(canonical(value))
                        for value in (parent_receipts, receipts, range_receipts)
                    )
                    > RECEIPT_BYTES
                ):
                    raise MetricLedgerConflict(
                        "metric visibility receipt budget exceeded"
                    )
                receipts = parent_receipts["batches"] + receipts
                range_receipts = parent_receipts.get("ranges", []) + range_receipts
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
                source_cut=source_cut,
                visibility_receipts={
                    "shard": shard,
                    "batches": receipts,
                    **(
                        {"format": 2, "ranges": range_receipts}
                        if range_receipts
                        else {}
                    ),
                },
                **self.lease.check(),
            )
            apply_publication(self, attempt, controls[0])
            if (
                attempt.purpose == "compaction"
                and "manifest" in attempt.source_ceilings
            ):
                from tracer.services.derived_metrics.deletion_protocol import _intent

                # Commit the remaining maintenance work WITH the head switch.
                # A crash between publication and its first OPTIMIZE must not
                # turn the next compact() call into a second publication.
                _intent(
                    organization_id=self.org_id,
                    project_id=attempt.project_id,
                    scope="history",
                    deletion_key=f"checkpoint-layout:{attempt.id}",
                    store_payload={
                        "protocol": "metric_checkpoint_layout",
                        "kind": "checkpoint",
                        "attempt": str(attempt.id),
                    },
                )
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

    def retention_evidence(self, attempt):
        """Read immutable proposal minima outside all lifecycle/policy locks.

        The attempt roots its parent against GC. Revocation aborts the attempt;
        the final publication CAS rejects observations made across erasure.
        """
        if attempt.source_ceilings.get("range_compaction"):
            from tracer.services.derived_metrics.compaction_ranges import (
                range_retention_evidence,
            )

            return range_retention_evidence(self, attempt)
        if self.retention_days is None or (
            attempt.purpose == "compaction"
            and not attempt.source_ceilings.get("logical_expiry")
            and "as_of" not in attempt.source_ceilings
        ):
            return None
        from tracer.services.derived_metrics.retention import (
            ProposalCaptures,
            _current_tables,
            retention_cutoff,
        )

        if (
            not attempt.source_ceilings.get("retention_floor")
            and retention_cutoff(self.retention_days(), now=self.lease.clock()) is None
        ):
            # The final locked policy read must still reject a concurrent shrink.
            return "unlimited"
        with ProposalCaptures(self, attempt, attempt.parent_manifest) as captures:
            return self._retention_minimum(attempt, captures, _current_tables)

    def _retention_minimum(self, attempt, captures, current_tables):
        tables = [FIELD_VALUES, *current_tables(self, attempt.parent_manifest, attempt)]
        if any(v.get("level") == "span" for v in attempt.metric_versions):
            tables.append(SOURCE_DEPENDENCIES)
        starts = (
            [
                datetime.fromisoformat(str(entry["unit_start"]))
                for chunk in attempt.ledger_chunks.filter(part="membership_preimage")
                for entry in chunk.payload
                if entry.get("unit_start")
            ]
            if any(v.get("level") == "span" for v in attempt.metric_versions)
            else []
        )
        if attempt.parent_manifest_id is None and not any(self.plans(attempt)):
            # An initial empty, sealed proposal has neither a committed source
            # nor frozen physical rows. Its durable ledger proves emptiness;
            # still validate any span recovery preimages against the final policy.
            return min(starts) if starts else None
        private_relations = []
        for table in tables:
            if attempt.purpose == "compaction":
                # A checkpoint's frozen rows ARE its complete relation: the
                # minimum of per-batch minima, one bounded batch per statement.
                private_relations.extend(
                    payload_select(
                        table,
                        [FrozenRows.from_plan(plan)],
                        prefix=f"policy_{number}",
                    )
                    for number, plan in enumerate(
                        self.plans(attempt, kind=TABLE_KIND[table.name])
                    )
                )
            else:
                # A delta's collapsed proposal, its overlay captured once.
                private_relations.append(captures.proposal(table))
        for sql, bound in private_relations:
            oldest = self.store.execute(
                f"SELECT minOrNull(unit_start) FROM ({sql})", bound
            )[0][0]
            if oldest is not None:
                starts.append(oldest)
        return min(starts) if starts else None

    def check_current_retention(self, attempt, oldest):
        """Revalidate current policy/clock in the final short publication transaction."""
        if attempt.source_ceilings.get("range_compaction"):
            from tracer.services.derived_metrics.compaction_ranges import (
                check_range_policy,
            )

            return check_range_policy(self, attempt, oldest)
        if self.retention_days is None or (
            attempt.purpose == "compaction"
            and not attempt.source_ceilings.get("logical_expiry")
            and "as_of" not in attempt.source_ceilings
        ):
            return
        from django.apps import apps

        from tracer.services.derived_metrics.retention import physical_cutoff

        apps.get_model(
            "accounts", "Organization"
        )._base_manager.select_for_update().get(id=self.org_id)
        # The attempt carries its head's floor and when it was set: within one
        # expiry interval the batched (physical) cutoff holds that floor.
        cutoff = physical_cutoff(
            self, now=self.lease.clock(), ceilings=attempt.source_ceilings
        )
        floors = [
            datetime.fromisoformat(str(value))
            for value in (attempt.source_ceilings.get("retention_floor"), cutoff)
            if value
        ]
        if floors and (
            oldest == "unlimited" or (oldest is not None and oldest < max(floors))
        ):
            raise MetricRetentionChanged("retention policy changed; replan required")

    def submit(self, attempt, batch, table, payload):
        """Register BEFORE sending; only a synchronous server response drains it.

        The receipt is deliberately independent of private ledger/attempt GC. A
        lost connection or worker crash leaves an unresolved obligation, not a
        false cancellation receipt. A retry's success cannot acknowledge an
        earlier ambiguous submission. Exclusion fences all later registrations.
        """
        from tracer.models.derived_metric import MetricErasureRequest
        from tracer.services.derived_metrics.compaction import (
            checkpoint_gate,
            require_layout_drained,
        )
        from tracer.services.derived_metrics.deletion_protocol import _intent

        shard = self.store.identity()
        group = (
            self.store.checkpoint_group(attempt.project_id)
            if table == FIELD_CHECKPOINT
            else None
        )
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
                    "shard": shard,
                    "checkpoint_group": group,
                },
            )
        if group is None:
            self.store.insert(table, payload)
        else:
            submitted = False
            try:
                with checkpoint_gate(group, shard):
                    require_layout_drained(group, shard)
                    submitted = True
                    self.store.insert(table, payload)
            finally:
                if not submitted:
                    # This process knows it never called the transport. A crash
                    # before here remains conservatively unresolved instead.
                    MetricErasureRequest.objects.filter(id=submission.id).delete()
        # No lease requirement: this acknowledges transport completion, NOT
        # permission to publish. It remains valid after writer exclusion.
        MetricErasureRequest.objects.filter(id=submission.id).update(
            status="done",
            completed_at=self.lease.clock(),
            receipts={"synchronous_response": {"drained": 1}},
        )

    def abort_stale(self, *, abort=True):
        """Return original work, including a retention checkpoint's durable handoff.

        With abort=True the caller holds project serialization until its successor
        ledger is durable. Retention uses abort=False while classifying outside
        that transaction, then atomically transfers the handoff and aborts its
        predecessors. A crash must never leave only an in-memory copy.
        """
        entries = {part: [] for part in MetricAttemptLedgerChunk.Part.values}
        head = MetricProjectRefresh.for_project(self.lease.project_id).head_manifest
        if head and head.published_by.source_ceilings.get("recovery_pending"):
            for chunk in head.published_by.ledger_chunks.exclude(part="cleanup_intent"):
                entries[chunk.part].extend(chunk.payload)
        # A pass that stopped at its soft budget aborted its own attempt
        # (yield_if_late): carry its ledger, which already holds every earlier
        # handoff, exactly as for an open one. Marked carried with the
        # successor's ledger, in the caller's transaction, so it is taken once.
        yielded = list(
            MetricRefreshAttempt.objects.filter(
                project_id=self.lease.project_id,
                state=MetricRefreshAttempt.State.ABORTED,
                abort_reason=PASS_YIELD,
                fencing_epoch__lte=self.lease.grant.fencing_epoch,
            ).values_list("id", flat=True)
        )
        for chunk in MetricAttemptLedgerChunk.objects.filter(
            attempt_id__in=yielded
        ).order_by("attempt__generation", "part", "chunk_no"):
            if chunk.part != "cleanup_intent":
                entries[chunk.part].extend(chunk.payload)
        self.carried_yields = yielded
        if abort:
            self.mark_yields_carried()
        for attempt in MetricRefreshAttempt.objects.filter(
            project_id=self.lease.project_id,
            state__in=MetricRefreshAttempt.OPEN_STATES,
            fencing_epoch__lte=self.lease.grant.fencing_epoch,
        ):
            for chunk in attempt.ledger_chunks.order_by("part", "chunk_no"):
                # Output payload is never source truth for a replacement writer.
                if chunk.part != "cleanup_intent":
                    entries[chunk.part].extend(chunk.payload)
            if abort:
                MetricRefreshAttempt.abort(
                    attempt.id, reason="lease_takeover", **self.lease.check()
                )
        return entries

    def mark_yields_carried(self):
        """Record that the yielded attempts abort_stale returned were carried."""
        MetricRefreshAttempt.objects.filter(
            id__in=getattr(self, "carried_yields", ()), abort_reason=PASS_YIELD
        ).update(abort_reason=PASS_YIELD_CARRIED)
        self.carried_yields = []

    def yield_if_late(self, attempt):
        """Stop the pass cleanly while its lease is still valid.

        Called before each capture statement. With less than the soft margin
        left before the pass deadline, the attempt is aborted (its ledger is
        carried to the next pass by abort_stale), the project's capture budget
        drops to half of what this pass managed, and MetricPassYield ends the
        pass. Never a takeover of an open attempt, never a partial publication.
        """
        from tracer.services.derived_metrics.busy_guard import counts

        work = counts(self)
        left = self.lease.budget_seconds()
        if left is None or left >= pass_soft_margin_seconds():
            return
        done = work["captures"]
        with transaction.atomic():
            fence = self.lease.check()
            MetricRefreshAttempt.abort(attempt.id, reason=PASS_YIELD, **fence)
            state = MetricProjectRefresh.for_project(self.lease.project_id)
            # Counted work can exceed the plan after a recovery handoff (or
            # several published burst pages). Never learn a larger budget from
            # that overrun: min(configured, learned) would otherwise keep the
            # next failed plan identical forever.
            next_budget = max(1, min(capture_budget(state), done) // 2)
            MetricProjectRefresh.objects.filter(
                MetricProjectRefresh.lease_q(**fence), project_id=self.lease.project_id
            ).update(pass_capture_budget=next_budget)
        raise MetricPassYield()

    def _publish_compaction(self, attempt, *, deadline):
        # Preserve stage_and_publish's replay/interleaving API. Scope the total
        # wall budget to this invocation; a later maintenance retry gets a new
        # budget rather than inheriting an expired clock from the original job.
        previous = getattr(self, "_compaction_deadline", None)
        self._compaction_deadline = deadline
        try:
            return self.stage_and_publish(attempt)
        finally:
            self._compaction_deadline = previous

    def compact(self, *, server_side=None):
        """Checkpoint every store, including tombstones; never copy a revoked base.

        compaction_path() chooses server-side ranges or the legacy batch copy
        (server_side forces one). Both need local ReplacingMergeTree tables and
        refuse any other topology; the legacy copy keeps its exact payload
        receipt semantics.
        """
        if server_side is None:
            server_side = self.compaction_path() == "server"
        if server_side:
            from tracer.services.derived_metrics.compaction_ranges import compact_ranges

            return compact_ranges(self)
        deadline = monotonic() + 600
        self.store.assert_checkpoint_schema()
        from tracer.models.derived_metric import MetricErasureRequest

        with serialized_project(self.lease.project_id):
            previous = MetricProjectRefresh.objects.get(
                project_id=self.lease.project_id
            ).head_manifest
            resume = (
                previous is not None
                and previous.revoked_at is None
                and MetricErasureRequest.objects.filter(
                    deletion_key=f"checkpoint-layout:{previous.checkpoint_attempt_id}",
                )
                .exclude(status="done")
                .exists()
            )
        if resume:
            try:
                self._publish_compaction(previous.checkpoint_attempt, deadline=deadline)
                if previous.published_by_id == previous.checkpoint_attempt_id:
                    return previous
                # A descendant delta still needs normal compaction after its
                # checkpoint's maintenance has been reconciled.
            finally:
                transaction.on_commit(self.collect_garbage)
        with serialized_project(self.lease.project_id):
            head = MetricProjectRefresh.objects.get(
                project_id=self.lease.project_id
            ).head_manifest
            if head is None:
                return None
            if head.revoked_at is not None:
                raise MetricStateConflict("expired base requires source rebuild")
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
        self.store.assert_visible(head)
        for table in TABLE_KINDS.values():
            prefix = "field" if table == FIELD_VALUES else "metric"
            versioned = table in {FIELD_VALUES, BUCKET_TOTALS, RETIRED_PARTS}
            self.freeze_current(
                attempt,
                table,
                head,
                large_pages=True,
                where=f"({prefix}_id, {prefix}_version) NOT IN %(obsolete_versions)s"
                if obsolete and versioned
                else "1",
                params={"obsolete_versions": obsolete}
                if obsolete and versioned
                else None,
            )
        self.seal(attempt, publication={"retire_versions": obsolete})
        try:
            return self._publish_compaction(attempt, deadline=deadline)
        finally:
            # Consolidation runs after manifest publication and may reject a
            # shared partition. A committed checkpoint still needs its durable
            # history collection scheduled even when layout maintenance fails.
            # Never delete CH history from a transaction that might roll PG back.
            if MetricReadManifest.objects.filter(published_by=attempt).exists():
                transaction.on_commit(self.collect_garbage)

    def compaction_path(self):
        """ "server" or why compaction keeps the legacy copy; never silent.

        Server-side ranges copy inside ClickHouse with fixed per-range costs
        (about 25-30 s in all), so a head below METRICS_SERVER_SIDE_MIN_ROWS
        copies faster through the legacy path. They need system.query_log
        (lost-acknowledgement proof). And they never copy a row past the
        physical retention cutoff: while the head holds one (an expiry is due),
        the legacy copy is used and that pass's expiry retires it.
        """
        from tracer.services.derived_metrics.compaction_ranges import (
            query_log_available,
        )
        from tracer.services.derived_metrics.size_cap import head_rows

        if not server_side_compaction():
            return "disabled"
        head = MetricProjectRefresh.for_project(self.lease.project_id).head_manifest
        if head is None:
            return "empty"
        if head_rows(head) < server_side_min_rows():
            return "small"
        if not query_log_available(self.store):
            logger.warning(
                "metric_server_side_compaction_unavailable",
                project_id=str(self.lease.project_id),
                reason="query_log",
            )
            return "query_log"
        if self._head_holds_expired_rows(head):
            return "expiry_due"
        return "server"

    def _head_holds_expired_rows(self, head):
        """Whether the head holds a row past the physical retention cutoff.

        Raw revisions are probed, not collapsed rows, so a superseded old
        revision also keeps the legacy copy (conservative).
        """
        from tracer.services.derived_metrics.retention import physical_cutoff

        if self.retention_days is None:
            return False
        cutoff = physical_cutoff(self, now=self.lease.clock(), head=head)
        floor = head.published_by.source_ceilings.get("retention_floor")
        if floor:
            floor = datetime.fromisoformat(str(floor))
            cutoff = max(cutoff, floor) if cutoff else floor
        if cutoff is None:
            return False
        from tracer.services.derived_metrics.retention import _expired_aggregates

        if _expired_aggregates(self, head, cutoff):
            return True
        attempts = tuple(UUID(str(a)) for _, a in head.allowed_attempts())
        for table in (
            FIELD_VALUES,
            FIELD_CHECKPOINT,
            SOURCE_DEPENDENCIES,
            LIST_CURRENT_ROOTS,
            LIST_CURRENT_MEMBERS,
            LIST_USER_ACTIVITY,
        ):
            if self.store.execute(
                f"SELECT 1 FROM {table.name} PREWHERE org_id = toUUID(%(old_org)s) "
                "AND project_id = toUUID(%(old_project)s) "
                "WHERE attempt_id IN %(old_attempts)s "
                "AND unit_start < toDateTime64(%(old_cutoff)s, 6, 'UTC') LIMIT 1",
                {
                    "old_org": str(self.org_id),
                    "old_project": str(self.lease.project_id),
                    "old_attempts": attempts,
                    "old_cutoff": cutoff,
                },
            ):
                return True
        return False

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

        head = MetricProjectRefresh.for_project(self.lease.project_id).head_manifest
        # The first complete publication may be a delta, not a checkpoint. It is
        # readable, but cannot authorize detaching history. Keep every PG root
        # until ordinary compaction supplies the existing GC prerequisite.
        intent = (
            collect_history(self.lease.project_id, now=self.lease.clock())
            if head is not None and head.checkpoint_attempt_id is not None
            else None
        )
        if intent is not None:
            purge_by_scope(self.store, intent)
        self.collect_unreachable_rows()
        self.scrub_orphans()
        # Unregistered attempts' rows are gone now, under ANY retention policy.
        discharge_omission_debts(self.lease.project_id, now=self.lease.clock())
        finish_pending_expiry(self)
        return intent

    def collect_unreachable_rows(self, *, limit=100):
        """Delete the ClickHouse rows of closed attempts no reader can reach.

        Every compaction leaves a full copy behind; collect_history removes it
        only with the attempt's PG row after ATTEMPT_LEDGER_RETENTION (7 days),
        so physical rows grew about 30x past logical rows at 10M. Selection is
        by PG reachability under the project lock (unreachable_closed_attempts);
        the DELETEs run outside it, and each attempt is marked collected once.
        A late INSERT of an aborted attempt is never readable and is reaped with
        its PG row by collect_history and scrub_orphans, as before. The DELETE
        is lightweight: rows stop being read at once (masked) and their bytes
        return when a merge, or apply_deleted_masks in the scheduled metric
        cleanup, rewrites the part. Returns the number of attempts collected.
        """
        from tracer.services.derived_metrics.storage import TABLES

        now = self.lease.clock()
        ids = unreachable_closed_attempts(
            self.lease.project_id, now=now, grace=history_gc_grace(), limit=limit
        )
        if not ids:
            return 0
        params = {
            "gc_org": str(self.org_id),
            "gc_project": str(self.lease.project_id),
            "gc_attempts": tuple(ids),
        }
        predicate = (
            "org_id = toUUID(%(gc_org)s) AND project_id = toUUID(%(gc_project)s) "
            "AND attempt_id IN %(gc_attempts)s"
        )
        for table in TABLES.values():
            if self.store.execute(
                f"SELECT 1 FROM {table.name} WHERE {predicate} LIMIT 1", params
            ):
                self.store.execute(
                    f"DELETE FROM {table.name} WHERE {predicate} "
                    "SETTINGS lightweight_deletes_sync = 2",
                    params,
                )
        with serialized_project(self.lease.project_id):
            MetricRefreshAttempt.objects.filter(
                id__in=ids, rows_collected_at__isnull=True
            ).update(rows_collected_at=now)
        return len(ids)

    def scrub_orphans(self):
        """Reap late writes to attempts PG has already collected.

        A one-shot DELETE is insufficient: an old INSERT can arrive afterward.
        Repeating this maintenance pass also under unlimited retention makes
        those forever-excluded physical payloads collectible without retaining
        their informational ancestry. The project lock freezes both the registered
        attempts and allocation high-water; native mutations cannot touch a later
        allocation. B10 must schedule maintenance even on idle data.
        """
        from tracer.services.derived_metrics.storage import TABLES

        with serialized_project(self.lease.project_id):
            registered = tuple(
                MetricRefreshAttempt.objects.filter(project_id=self.lease.project_id)
                .exclude(abort_reason=MetricRefreshAttempt.GC_BLOCKED_HANDOFF)
                .values_list("id", flat=True)
            ) or (UUID(int=0),)
            through = MetricProjectRefresh.objects.get(
                project_id=self.lease.project_id
            ).generation_high_water
        params = {
            "gc_org": str(self.org_id),
            "gc_project": str(self.lease.project_id),
            "gc_registered": registered,
            "gc_through": through,
        }
        predicate = (
            "org_id = toUUID(%(gc_org)s) AND project_id = toUUID(%(gc_project)s) "
            "AND generation <= %(gc_through)s AND attempt_id NOT IN %(gc_registered)s"
        )
        for table in TABLES.values():
            # An empty namespace needs no synchronous mutation/part rewrite.
            # This is only a no-op optimization, never a drained-write proof:
            # late excluded inserts are still reaped on the next scheduled pass.
            if self.store.execute(
                f"SELECT 1 FROM {table.name} WHERE {predicate} LIMIT 1", params
            ):
                self.store.execute(
                    f"ALTER TABLE {table.name} DELETE WHERE {predicate} SETTINGS mutations_sync=2",
                    params,
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
        keys = [
            (e["trace_id"], e["span_id"])
            for e in entries
            if e.get("trace_id") and e.get("span_id")
        ]
        with source_identity_relation(self.store, keys) as source_relation:
            for offset in range(0, len(ids), MAX_BATCH_ROWS):
                page = tuple(ids[offset : offset + MAX_BATCH_ROWS])
                anchors = {}
                # Split only the pinned lookup. Keep the original source-capture
                # page and its subsequent admission checks unchanged.
                for membership in iter_key_membership_batches(
                    FIELD_VALUES,
                    fixed={},
                    columns="unit_id",
                    values=page,
                    max_items=MAX_BATCH_ROWS,
                ):
                    fields, fp = committed_rows_select(
                        FIELD_VALUES,
                        head,
                        org_id=self.org_id,
                        project_id=self.lease.project_id,
                        key_in=membership,
                        param_prefix="takeover_fields",
                    )
                    anchors.update(
                        self.store.execute(
                            f"SELECT unit_id, min(unit_start) FROM ({fields}) WHERE unit_id IN %(takeover_ids)s GROUP BY unit_id",
                            {**fp, "takeover_ids": membership["unit_id"]},
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
                        source_identities=source_relation,
                        start_floor=self._unit_source_floor(scope, page, head),
                    )
                    if self.store.execute(
                        f"SELECT count() FROM ({sql}) WHERE unit_start < toDateTime64(%(takeover_cutoff)s, 6, 'UTC')",
                        {**params, "takeover_cutoff": cutoff},
                    )[0][0]:
                        return False
        return True

    def _unit_source_floor(self, scope, units, head):
        """Exact physical start_time floor of the source capture for these units."""

        def scalar(sql, params, stage):
            rows = self.store.execute(
                sql, params, query_id=f"metric-source-{stage}-" + uuid4().hex
            )
            return rows[0][0] if rows else None

        floor = scalar(*unit_source_floor_select(scope, units, head), "floor")
        if floor is None:
            return None
        version = scalar(
            *unit_source_version_select(scope, units, head, floor=floor), "version"
        )
        if version is not None:
            moved = scalar(
                *unit_source_guard_select(
                    scope, units, head, floor=floor, version=version
                ),
                "guard",
            )
            if moved is not None:
                floor = min(floor, moved)
        return floor if floor.tzinfo else floor.replace(tzinfo=UTC)

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
        # A discovery batch is valid only for the lease epoch that read it.
        # Project/organization erasure invalidates that lease even without a head
        # or an open attempt. Never inspect/transfer cached private recovery first.
        self.lease.check()
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
        from tracer.services.derived_metrics.retention import expire_project

        if self.retention_days is None:
            raise MetricStateConflict(
                "refresh requires current organization retention policy"
            )
        refresh_now = self.lease.clock()
        # Physical expiry is batched once per expiry interval (E1); reads keep
        # the exact cutoff, so nothing past the policy is ever served.
        from tracer.services.derived_metrics.retention import physical_cutoff

        cutoff = physical_cutoff(self, now=refresh_now)
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
            # retained-only history until a later pass omits it.
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
        threshold = getattr(self, "deltas_before_compaction", None)
        if head and len(head.delta_attempt_ids) >= (
            threshold or deltas_before_compaction()
        ):
            self.compact()
        with serialized_project(self.lease.project_id):
            self.lease.check()
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
            if head and MetricRefreshAttempt.blocked_expiry_handoffs(head).exists():
                # abort_stale would consume a sanitized handoff as ordinary
                # work, but the missing owner cannot be rediscovered from it.
                raise MetricAdmissionUnproven("metric_admission_unproven")
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
        captured_dependencies = {}
        ids.update(
            d.canonical_session_id
            for d in recovered_dependencies
            if d.canonical_session_id
        )
        if len(ids) > MAX_PASS_UNITS:
            raise ValueError("replacement owner set exceeds pass cap")
        from tracer.services.derived_metrics.backfill import forward_coverage_start
        from tracer.services.derived_metrics.busy_guard import (
            counts,
            record_mixed_closure,
        )

        self._busy_span_start = getattr(self, "pass_span_captures", 0)
        counts(self, owners=len(ids), definitions=definitions)
        if any(d.level == "span" for d in definitions):
            from tracer.services.derived_metrics.span_evidence import (
                freeze_span_fields,
            )
            from tracer.services.derived_metrics.span_sampling import (
                SpanSamplingContext,
            )

            self._span_dirty_keys(attempt, change, head, source_ids)
            # One frozen quarter-wide context for the entire publication, shared
            # by definitions with the same filters and every identity page.
            with SpanSamplingContext(
                self, attempt, definitions, as_of=as_of
            ) as sampling:
                for definition in definitions:
                    if definition.level == "span":
                        coverage = forward_coverage_start(definition)
                        freeze_span_fields(
                            self,
                            attempt,
                            head,
                            definition,
                            scope,
                            as_of=as_of,
                            sampling=sampling,
                            # A forward-only span version owns no span that starts
                            # before its coverage start (nor beyond retention).
                            cutoff=cutoff
                            if coverage is None
                            else max(v for v in (cutoff, coverage) if v is not None),
                        )
            if any(d.level == "conversation" for d in definitions):
                owners = self._unowned_span_sessions(attempt, head, ids)
                if len(ids | owners) <= MAX_PASS_UNITS:
                    ids |= owners
                record_mixed_closure(self, attempt, definitions, ids | owners)
        # Every capture statement names the pass's source identities in a
        # session table instead of inlining them (bug 7: ~1 MB statements).
        with ExitStack() as capture_relations:
            source_relation = capture_relations.enter_context(
                source_identity_relation(self.store, source_ids)
            )
            floors, candidate_relations = {}, {}
            for definition in definitions:
                if definition.level == "span":
                    continue
                for offset in range(0, len(ids), MAX_BATCH_ROWS):
                    candidates = tuple(sorted(ids))[offset : offset + MAX_BATCH_ROWS]
                    if candidates not in floors:
                        candidate_relations[candidates] = (
                            capture_relations.enter_context(
                                unit_identity_relation(self.store, candidates)
                            )
                        )
                        floors[candidates] = self._unit_source_floor(
                            scope, candidates, head
                        )
                    where = (
                        "field_id = toUUID(%(field)s) AND field_version = %(version)s"
                    )
                    candidate_params_fixed = {
                        "field": str(definition.metric_id),
                        "version": definition.version,
                    }
                    # Dirty-before is indexed by raw candidate owners. Keep its
                    # membership separate from the remap-aware output-domain
                    # readers below: their canonical unit_id can differ from
                    # every candidate supplied to unit_values_select.
                    before = []
                    for membership in iter_key_membership_batches(
                        FIELD_VALUES,
                        fixed={
                            "field_id": (definition.metric_id,),
                            "field_version": (definition.version,),
                        },
                        columns="unit_id",
                        values=candidates,
                    ):
                        candidate_sql, candidate_params = committed_rows_select(
                            FIELD_VALUES,
                            head,
                            org_id=self.org_id,
                            project_id=scope.project,
                            key_in=membership,
                            param_prefix="dirty_candidate",
                        )
                        before.extend(
                            self.store.execute(
                                f"SELECT unit_id, bucket_start, group_key, sampling_scope, sampling_stratum FROM ({candidate_sql}) WHERE {where}",
                                merge_bindings(
                                    candidate_params, candidate_params_fixed
                                ),
                            )
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
                        source_identities=source_relation,
                        start_floor=floors[candidates],
                        unit_identities=candidate_relations[candidates],
                        include_dependencies=True,
                    )
                    self.yield_if_late(attempt)
                    captured = capture_source_output(
                        self.store,
                        query,
                        params,
                        field_id=definition.metric_id,
                        field_version=definition.version,
                        candidate_count=len(candidates),
                        include_dependencies=True,
                    )
                    from tracer.services.derived_metrics.changes import MAX_KEYS

                    for dependency in captured.dependencies:
                        previous = captured_dependencies.setdefault(
                            dependency.key, dependency
                        )
                        if previous != dependency:
                            raise MetricStateConflict(
                                "source membership changed between field captures"
                            )
                        if len(captured_dependencies) > MAX_KEYS:
                            raise MetricStateConflict(
                                "metric publication dependency cap exceeded"
                            )
                    self.pass_captures = getattr(self, "pass_captures", 0) + 1
                    counts(self)
                    for source_page in captured.pages():
                        query, params = source_page.select()
                        old_sql, old_params = committed_rows_select(
                            FIELD_VALUES,
                            head,
                            org_id=self.org_id,
                            project_id=scope.project,
                            key_in={
                                "field_id": (definition.metric_id,),
                                "field_version": (definition.version,),
                                "unit_id": source_page.unit_ids,
                            },
                            param_prefix="source_old_fields",
                        )
                        old_params = merge_bindings(old_params, candidate_params_fixed)
                        # The raw candidate dirty-before/has_before guard above is
                        # unchanged. Also invalidate prior buckets of emitted survivors
                        # outside that domain; this must NOT change bool(before).
                        supplemental = self.store.execute(
                            f"SELECT unit_id, bucket_start, group_key, sampling_scope, sampling_stratum "
                            f"FROM ({old_sql}) WHERE unit_id NOT IN %(raw_candidates)s",
                            {**old_params, "raw_candidates": candidates},
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
                                for row in supplemental
                            ],
                        )
                        states, state_params = committed_rows_select(
                            UNIT_STATE,
                            head,
                            org_id=self.org_id,
                            project_id=scope.project,
                            key_in={
                                "level": ("conversation",),
                                "unit_id": source_page.unit_ids,
                            },
                            param_prefix="state_floor",
                        )
                        # A deleted latest span can lower the live-source arrival.
                        # Durable committed knowledge is a floor, never settle early.
                        floor = (
                            "greatest(f.last_arrival_ns, ifNull(p.last_arrival_ns, 0))"
                        )
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
                        params = merge_bindings(params, state_params)
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
                            params = merge_bindings(params, old_params)
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
                                raise MetricAdmissionUnproven(
                                    "metric_admission_unproven"
                                )
                        coverage = forward_coverage_start(definition)
                        if coverage is not None:
                            # Start with no history: a forward-only version owns
                            # only units starting at or after its coverage start.
                            # An EXISTING field still gets its replacement (moved
                            # or deleted), so no committed value goes stale.
                            query = (
                                f"SELECT * FROM ({query}) WHERE unit_start >= "
                                "toDateTime64(%(forward_coverage_start)s, 6, 'UTC') "
                                f"OR unit_id IN (SELECT unit_id FROM ({old_sql}) WHERE {where})"
                            )
                            params = merge_bindings(
                                params, old_params, {"forward_coverage_start": coverage}
                            )
                        if (
                            inherited_floor
                            and admission_requires_proof(head)
                            and self._unproven_source_admission(
                                head,
                                source_page,
                                query,
                                params,
                                old_sql,
                                old_params,
                                cutoff or inherited_floor,
                            )
                        ):
                            # Unknown or corrected anchors cannot be proven from a
                            # recent raw row alone (TH-8975 remains held).
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
        # Discovery is only nomination. Every member read by a value capture
        # must have a dependency in that same publication, including members
        # beyond this discovery page. The capture wins over stale nominations.
        dependencies.update(captured_dependencies)
        self.freeze_dependencies(attempt, dependencies.values(), cutoff=cutoff)
        # BL01-Q must qualify and byte-bound current-fact maintenance before
        # it joins production refreshes. Copying full message inputs here can
        # exceed the batch cap even when the scalar metric payload is small.
        from tracer.services.derived_metrics.retention import ProposalCaptures

        with ProposalCaptures(self, attempt, head) as captures:
            attempt = self._finish_proposal(
                attempt,
                head,
                definitions,
                captures,
                cutoff=cutoff,
                as_of=as_of,
                settle_minutes=settle_minutes,
            )
        self.seal(attempt, cursors=dict(change.cursor_updates), publication=publication)
        manifest = self.stage_and_publish(attempt)
        if cutoff is not None:
            from tracer.services.derived_metrics.retention import purge_checkpoint

            purge_checkpoint(self, attempt, manifest)
        return manifest

    def _finish_proposal(
        self, attempt, head, definitions, captures, *, cutoff, as_of, settle_minutes
    ):
        """Lifecycle, buckets and retention over ONE captured field overlay."""
        has_fields = any(True for _ in self.plans(attempt, kind="field_values"))
        # The draft's frozen field batches, captured once (one batch per INSERT).
        overlay, overlay_params = captures.overlay(FIELD_VALUES)
        if has_fields:
            # Retain ALL existing checked proposals, including losers and
            # both open predicates. R01 changes only the prior lookup domain;
            # a separate all-proposal scratch stream is deferred to R03.
            lifecycle_keys = self.store.execute(
                f"SELECT DISTINCT level, unit_id FROM ({overlay}) WHERE level != 'span' ORDER BY level, unit_id",
                overlay_params,
            )
            for membership in iter_key_membership_batches(
                UNIT_STATE,
                fixed={},
                columns=("level", "unit_id"),
                values=lifecycle_keys,
                max_items=MAX_BATCH_ROWS,
            ):
                prior_states, prior_params = committed_rows_select(
                    UNIT_STATE,
                    head,
                    org_id=self.org_id,
                    project_id=self.lease.project_id,
                    key_in=membership,
                    param_prefix="prior_states",
                )
                state = f"""SELECT f.level AS level, f.unit_id AS unit_id, max(f.last_arrival_ns) AS last_arrival_ns,
                    if(countIf(p.unit_id != '') > 0, min(p.first_seen), toDateTime64(%(state_first_seen)s, 6, 'UTC')) AS first_seen,
                    toUInt8(max(p.reopened) OR (max(p.is_deleted) AND countIf(f.status = 'open') > 0)) AS reopened,
                    toUInt8(countIf(f.is_deleted = 0 AND f.status = 'open') = 0) AS is_deleted,
                    fromUnixTimestamp64Nano(toInt64(last_arrival_ns), 'UTC') + toIntervalMinute(%(settle)s) AS due_at
                    FROM (SELECT * FROM ({overlay}) WHERE (level, unit_id) IN %(lifecycle_keys)s) f LEFT JOIN ({prior_states}) p ON f.level = p.level AND f.unit_id = p.unit_id
                    GROUP BY f.level, f.unit_id"""
                self.freeze(
                    attempt,
                    UNIT_STATE,
                    state,
                    {
                        **overlay_params,
                        **prior_params,
                        "lifecycle_keys": membership[("level", "unit_id")],
                        "settle": settle_minutes,
                        "state_first_seen": as_of,
                    },
                )
        for definition in definitions:
            if definition.level != "user" and cutoff is None:
                self._freeze_buckets(attempt, head, definition, overlay, overlay_params)
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
                captures=captures,
            )
        return attempt

    def _unproven_source_admission(
        self, head, source_page, query, params, old_sql, old_params, floor
    ):
        """Positive anchor matches across ALL versions, then (and only then) absence.

        Only immutable emitted keys enter committed collapse. Deletion, retention
        and mutable unit_start equality are evaluated AFTER every winning revision
        (including tombstones) is selected. Raw candidate IDs are not this domain.
        """
        bound = merge_bindings(params, old_params, {"admission_floor": floor})
        # Rows of one source ID are identical; matching any retained exact
        # (unit_id, unit_start) anchor proves it. The set is <= one source page,
        # never all versions or the entire project's payloads.
        remaining = {
            row[0]
            for row in self.store.execute(
                f"SELECT unit_id FROM ({query}) WHERE is_deleted = 0 AND unit_id NOT IN "
                f"(SELECT unit_id FROM ({old_sql}) WHERE is_deleted = 0)",
                bound,
            )
        }
        if not remaining:
            return False
        keys = tuple(
            c for c in FIELD_VALUES.logical_key if c not in {"org_id", "project_id"}
        )
        for nominated in committed_key_pages(
            self.store,
            FIELD_VALUES,
            head,
            org_id=self.org_id,
            project_id=self.lease.project_id,
            target_rows=MAX_BATCH_ROWS,
            key_in={"level": ("conversation",), "unit_id": source_page.unit_ids},
            param_prefix="anchor_keys",
        ):
            anchors, anchor_params = committed_rows_select(
                FIELD_VALUES,
                head,
                org_id=self.org_id,
                project_id=self.lease.project_id,
                key_in={keys: nominated},
                param_prefix="admission_anchors",
            )
            matched = self.store.execute(
                f"SELECT DISTINCT f.unit_id FROM ({query}) f INNER JOIN "
                f"(SELECT unit_id, unit_start FROM ({anchors}) WHERE is_deleted = 0 "
                "AND level = 'conversation' AND unit_start >= "
                "toDateTime64(%(admission_floor)s, 6, 'UTC')) a "
                "ON f.unit_id = a.unit_id AND f.unit_start = a.unit_start "
                "WHERE f.is_deleted = 0",
                merge_bindings(bound, anchor_params),
            )
            remaining.difference_update(row[0] for row in matched)
            if not remaining:
                return False
        return not self._created_after_purge(head, remaining)

    def _created_after_purge(self, head, units):
        """Every span in these conversations was created after the latest purge.

        Such a unit cannot be retired evidence reappearing (R02, TH-8975): every
        member it has was created after the purge that set the retention floor,
        so none contributed to what that purge retired. A unit with any member
        created before it still needs anchor proof. Unknown when the floor
        predates recording its purge time, so nothing is exempt then.
        """
        if head is None or not units:
            return False
        at = head.published_by.source_ceilings.get("retention_floor_at")
        if not at:
            return False
        scope = Scope(self.org_id, self.workspace_id, self.lease.project_id)
        units = tuple(sorted(units))
        floor = self._unit_source_floor(scope, units, head)
        if floor is None:
            return False
        sql, params = unit_first_created_select(scope, units, head, floor=floor)
        first = self.store.execute(
            sql, params, query_id="metric-admission-created-" + uuid4().hex
        )[0][0]
        if first is None:
            return False
        first = first if first.tzinfo else first.replace(tzinfo=UTC)
        return first > datetime.fromisoformat(str(at))

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

    def _unowned_span_sessions(self, attempt, head, ids):
        """Owners of this pass's span fields that no conversation row anchors.

        Span fields are rebuilt for WHOLE dirty quarters, so a pass also emits
        spans of conversations it never touched: e.g. a quarter-mate just past
        an initial-sweep page boundary. Neither the head nor the draft then has
        a conversation row for that owner, so retention cannot prove the owner
        link retained and forces a privacy checkpoint (then the next page can
        no longer prove its span evidence, and the definitions never
        activate). Such owners are computed in this same pass instead: their
        conversation values read every span of the session from source.
        Owners with ANY committed conversation row (live, deleted or expired)
        keep their existing retention semantics.
        """
        sessions = set()
        # The draft's frozen field batches (PG ledger, not yet physical), one
        # bounded batch per statement.
        for number, plan in enumerate(self.plans(attempt, kind="field_values")):
            batch, params = payload_select(
                FIELD_VALUES,
                [FrozenRows.from_plan(plan)],
                prefix=f"span_owner_{number}",
            )
            sessions.update(
                row[0]
                for row in self.store.execute(
                    f"SELECT DISTINCT session_id FROM ({batch}) "
                    "WHERE level = 'span' AND notEmpty(session_id)",
                    params,
                )
            )
        sessions = sorted(sessions - set(ids))
        if not sessions:
            return set()
        anchored = set()
        for membership in iter_key_membership_batches(
            FIELD_VALUES,
            fixed={"level": ("conversation",)},
            columns="unit_id",
            values=sessions,
            max_items=MAX_BATCH_ROWS,
        ):
            fields, fp = committed_rows_select(
                FIELD_VALUES,
                head,
                org_id=self.org_id,
                project_id=self.lease.project_id,
                key_in=membership,
                param_prefix="span_owner_head",
            )
            anchored.update(
                row[0]
                for row in self.store.execute(
                    f"SELECT DISTINCT unit_id FROM ({fields})", fp
                )
            )
        return set(sessions) - anchored

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
        for offset in range(0, len(source_ids), MAX_BATCH_ROWS):
            page = tuple(source_ids[offset : offset + MAX_BATCH_ROWS])
            for membership in iter_key_membership_batches(
                FIELD_VALUES,
                fixed={"level": ("span",)},
                columns="unit_id",
                values=[json.dumps(key, separators=(",", ":")) for key in page],
                max_items=MAX_BATCH_ROWS,
            ):
                fields, fp = committed_rows_select(
                    FIELD_VALUES,
                    head,
                    org_id=self.org_id,
                    project_id=self.lease.project_id,
                    key_in=membership,
                    param_prefix="span_dirty_fields",
                )
                starts.update(
                    row[0]
                    for row in self.store.execute(
                        f"SELECT DISTINCT unit_start FROM ({fields})", fp
                    )
                )
            for membership in iter_key_membership_batches(
                SOURCE_DEPENDENCIES,
                fixed={},
                columns=("trace_id", "span_id"),
                values=page,
                max_items=MAX_BATCH_ROWS,
            ):
                deps, dp = committed_rows_select(
                    SOURCE_DEPENDENCIES,
                    head,
                    org_id=self.org_id,
                    project_id=self.lease.project_id,
                    key_in=membership,
                    param_prefix="span_dirty_dependencies",
                )
                starts.update(
                    row[0]
                    for row in self.store.execute(
                        f"SELECT DISTINCT unit_start FROM ({deps}) WHERE (trace_id, span_id) IN %(span_sources)s",
                        {**dp, "span_sources": membership[("trace_id", "span_id")]},
                    )
                )
            starts.update(
                row[0]
                for row in self.store.execute(
                    "SELECT DISTINCT start_time FROM spans PREWHERE project_id = toUUID(%(span_project)s) "
                    "AND (trace_id, id) IN %(span_sources)s WHERE org_id = toUUID(%(span_org)s) OR isNull(org_id)",
                    {
                        "span_sources": page,
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

    @contextmanager
    def _anonymous_bucket_parts(self, attempt, definition, fields, retired, params):
        """Capture anonymous children and the final global fold exactly once.

        Only final replacement batches enter the sealed replay ledger. Scratch
        handles are connection-owned, independently checked and nonstageable;
        every use (including vanished keys and output pagination) stays inside
        their lifetime. No carried RowBinary accumulator is composed into SQL.
        """
        from clickhouse_driver.errors import ServerException

        from tracer.services.derived_metrics.buckets import live_parts_select
        from tracer.services.derived_metrics.frozen_relations import FrozenRelations
        from tracer.services.derived_metrics.unit_reads import field_recipe

        formula = field_recipe(definition).formula
        formula_params = formula.parameters if formula is not None else {}
        target_rows = BUCKET_INPUT_ROWS
        deadline = getattr(self, "_compaction_deadline", None) or monotonic() + 600
        with self.store.compaction_budget(deadline):
            while True:
                scan = (
                    fields.scan(self.store, target_rows=target_rows)
                    if isinstance(fields, BucketFieldPlan)
                    else None
                )
                pages = (
                    scan
                    if scan is not None
                    else ((fields,) if isinstance(fields, str) else fields)
                )
                retry = False
                next_target = max(1, target_rows // 2)
                with FrozenRelations(
                    self.store,
                    org_id=self.org_id,
                    project_id=self.lease.project_id,
                    attempt_id=attempt.id,
                    source=[
                        attempt.parent_manifest_sha256,
                        str(definition.metric_id),
                        definition.version,
                    ],
                ) as relations:
                    relations.materialize(
                        f"SELECT * FROM ({retired}) WHERE is_deleted = 0",
                        params,
                        role="retired",
                    )
                    children = 0
                    try:
                        for page in pages:
                            self.lease.keepalive()
                            sql, bound = (
                                (page, params) if isinstance(page, str) else page
                            )
                            relations.materialize(
                                live_parts_select(definition, sql),
                                merge_bindings(bound, formula_params),
                                role="child",
                            )
                            children += 1
                    except (BucketRangeOverflow, ServerException) as exc:
                        # Only replan the causal INPUT path, never a failed final
                        # consumer/fold. The previous context must drop every
                        # scratch table before a fresh namespace is allocated.
                        if (
                            scan is None
                            or target_rows == 1
                            or (
                                isinstance(exc, BucketRangeOverflow) and exc.indivisible
                            )
                            or (
                                isinstance(exc, ServerException)
                                and exc.code not in {159, 241}
                            )
                        ):
                            raise
                        if isinstance(exc, BucketRangeOverflow):
                            next_target = min(next_target, exc.target_rows)
                        retry = True
                    if not retry:
                        if scan is not None and not scan.complete:
                            raise MetricVisibilityError(
                                "bucket field coverage is incomplete"
                            )
                        coverage = (
                            (scan.ranges, scan.exhausted_routes)
                            if scan is not None
                            else None
                        )
                        handle = relations.finish(
                            expected_children=children, coverage=coverage
                        )
                        # Outside the retry exception handler: a consumer failure
                        # cannot replay or duplicate already-frozen ledger rows.
                        yield relations.select(handle, role="complete-folded")
                        return
                target_rows = next_target

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
        _dirty_starts=None,
    ):
        # Complete fields are already frozen final rows, not a SQL relation with
        # a hidden broad collapse. Proposal callers supply the raw overlay plus
        # head instead, so every physical branch can receive candidate membership.
        params = dict(overlay_params)
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
        if _dirty_starts is not None:
            dirty = set(_dirty_starts)
        floor = attempt.source_ceilings.get("retention_floor")
        if floor:
            floor = datetime.fromisoformat(str(floor))
            dirty = {
                value for value in dirty if datetime.fromisoformat(str(value)) >= floor
            }
        # The exhaustive physical-key scan and frozen/digest ceremony are per
        # bounded SET of dirty quarters, not per quarter. The same pre-fold
        # statistics keep bucket_start in their key, so folding remains exact.
        # Physical row/byte caps and subdivision still apply to every input page.
        dirty = sorted(dirty)
        for offset in range(0, len(dirty), BUCKET_WINDOWS_PER_FOLD):
            starts = tuple(
                str(value) for value in dirty[offset : offset + BUCKET_WINDOWS_PER_FOLD]
            )
            p = {**params, "dirty_starts": list(starts)}
            predicate = "bucket_start IN (SELECT parseDateTimeBestEffort(arrayJoin(%(dirty_starts)s), 'UTC'))"
            self.lease.keepalive()
            subdivide = not complete_fields and field_window_requires_subdivision(
                self.store,
                head,
                org_id=self.org_id,
                project_id=self.lease.project_id,
                field_id=definition.metric_id,
                field_version=definition.version,
                bucket_start=starts,
                overlay=(overlay, overlay_params),
            )
            fields = BucketFieldPlan(
                manifest=head,
                org_id=self.org_id,
                project_id=self.lease.project_id,
                field_id=definition.metric_id,
                field_version=definition.version,
                predicate=predicate,
                params=p,
                overlay=(overlay, overlay_params),
                subdivide=subdivide,
                complete_fields=complete_fields,
            )
            moments = tuple(datetime.fromisoformat(start) for start in starts)
            membership = {
                "metric_id": (definition.metric_id,),
                "metric_version": (definition.version,),
                "bucket_start": tuple(
                    at.replace(tzinfo=UTC) if at.tzinfo is None else at
                    for at in moments
                ),
            }
            retired, rp = committed_rows_select(
                RETIRED_PARTS,
                head,
                org_id=self.org_id,
                project_id=self.lease.project_id,
                key_in=membership,
                param_prefix="bucket_retired",
            )
            old, op = committed_rows_select(
                BUCKET_TOTALS,
                head,
                org_id=self.org_id,
                project_id=self.lease.project_id,
                key_in=membership,
                param_prefix="bucket_previous",
            )
            metric_where = "metric_id = toUUID(%(bucket_metric)s) AND metric_version = %(bucket_metric_version)s"
            retired = f"SELECT * FROM ({retired}) WHERE {metric_where} AND {predicate}"
            old = f"SELECT * FROM ({old}) WHERE {metric_where} AND {predicate}"
            if retired_override is not None:
                retired, rp = retired_override
                retired = (
                    f"SELECT * FROM ({retired}) WHERE {metric_where} AND {predicate}"
                )
            p = merge_bindings(p, rp, op)
            from tracer.services.derived_metrics.frozen_relations import (
                FrozenRelationOverflow,
            )

            captured = False
            try:
                with self._anonymous_bucket_parts(
                    attempt, definition, fields, retired, p
                ) as parts:
                    captured = True
                    replacement = replacement_select(parts, old)
                    if complete_fields and attempt.purpose == "compaction":
                        # A complete checkpoint has no older keys to mask. Drop
                        # departed group labels instead of preserving tombstones.
                        replacement = (
                            f"SELECT * FROM ({replacement}) WHERE is_deleted = 0"
                        )
                    vanished = (
                        []
                        if complete_fields and attempt.purpose == "compaction"
                        else self.store.execute(
                            f"SELECT {', '.join(KEYS)} FROM ({old}) WHERE ({', '.join(KEYS)}) NOT IN (SELECT {', '.join(KEYS)} FROM ({parts}))",
                            p,
                        )
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
                        collect_payloads=False,
                        extra={
                            "metric_id": "toUUID(%(bucket_metric)s)",
                            "metric_version": "toUInt32(%(bucket_metric_version)s)",
                        },
                    )
            except FrozenRelationOverflow:
                # Only an INPUT capture can be retried. Once the consumer may
                # have appended output, repeating the group would duplicate keys.
                if captured or len(starts) == 1:
                    raise
                middle = len(starts) // 2
                for half in (starts[:middle], starts[middle:]):
                    self._freeze_buckets(
                        attempt,
                        head,
                        definition,
                        overlay,
                        overlay_params,
                        complete_fields=complete_fields,
                        retired_override=retired_override,
                        _dirty_starts=half,
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
