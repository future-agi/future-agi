"""Live-store retirement and physical purge. Completed snapshots are untouched.

Retention is based on unit_start, not the list window. Anonymous PRE-FOLD
statistics survive expiry, but user-level metrics have no such history. Erasure
is different: without unit lineage in retired parts we conservatively discard
project aggregate history. This is the design's safe fallback, not subtraction.

Callers schedule expiry independently of arrivals and retry failed mutations.
A purge holds the same project synchronization as publication and pin capture.
The original manifest is revoked before any physical deletion. Audit hashes are
never rewritten; terminal private replay/preimage chunks can be deleted whole.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.db import connection
from django.utils import timezone

from tracer.models.derived_metric import (
    MetricAttemptLedgerChunk,
    MetricErasureRequest,
    MetricGenerationPin,
    MetricProjectRefresh,
    MetricReadManifest,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.derived_metrics.buckets import (
    KEYS,
    STATS,
    live_parts_select,
    merge_parts_select,
)
from tracer.services.derived_metrics.deletion_protocol import (
    revoke_generations,
    serialized_project,
)
from tracer.services.derived_metrics.publication import (
    committed_rows_select,
    pinned_read,
)
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_VALUES,
    RETIRED_PARTS,
    SOURCE_DEPENDENCIES,
    TABLES,
    UNIT_STATE,
)


def retention_cutoff(days, *, now=None):
    if type(days) is not int:
        raise ValueError("retention days must be an integer")
    return (
        None
        if days <= 0
        else (now or timezone.now()).astimezone(UTC) - timedelta(days=days)
    )


def retained_rows_select(sql, cutoff):
    """Wrap AFTER current-row collapse. No expiry predicate may select an old row."""
    if cutoff is None:
        return sql, {}
    return (
        f"SELECT * FROM ({sql}) WHERE unit_start >= toDateTime64(%(retention_cutoff)s, 6, 'UTC')",
        {"retention_cutoff": cutoff},
    )


def read_field_cells(
    store, scope, pin, field_id, field_version, unit_ids, *, retention_days, now=None
):
    """Hydration guard with live org policy; expired evidence never leaves CH.

    retention_days is a callable, deliberately not a pin-time cached horizon.
    A queued snapshot must call assert_capture_retained in its locked capture
    path, rather than interpreting these explicit expired cells as an empty result.
    """
    from tracer.services.derived_metrics.unit_reads import committed_unit_values_select

    with pinned_read(store, scope.project, pin=pin, now=now) as (manifest, _):
        cutoff = retention_cutoff(retention_days(), now=now)
        sql, params = committed_unit_values_select(
            scope, field_id, field_version, unit_ids, manifest
        )
        expired = (
            "0"
            if cutoff is None
            else "unit_start < toDateTime64(%(retention_cutoff)s, 6, 'UTC')"
        )
        replacements = [
            f"if({expired}, NULL, {c}) AS {c}"
            for c in (
                "value_bool",
                "value_number",
                "value_text",
                "part_n",
                "part_d",
                "sample_probability",
                "population_size",
            )
        ]
        replacements += [
            f"if({expired}, map(), {c}) AS {c}"
            for c in ("inputs", "inputs_bool", "inputs_text")
        ]
        replacements += [
            f"if({expired}, '', {c}) AS {c}"
            for c in (
                "session_id",
                "user_id",
                "group_key",
                "sampling_scope",
                "sampling_stratum",
                "sampling_design",
            )
        ]
        replacements += [
            f"if({expired}, NULL, settled_at) AS settled_at",
            f"if({expired}, toUInt64(0), last_arrival_ns) AS last_arrival_ns",
            f"if({expired}, 'no_value', status) AS status",
            f"if({expired}, 'retention_expired', no_value_reason) AS no_value_reason",
        ]
        rows, columns = store.execute(
            f"SELECT * REPLACE({', '.join(replacements)}) FROM ({sql}) WHERE is_deleted = 0",
            {**params, "retention_cutoff": cutoff},
            with_column_types=True,
        )
        cells = [dict(zip((c[0] for c in columns), row, strict=True)) for row in rows]
        final_cutoff = retention_cutoff(retention_days(), now=now)
        if final_cutoff and any(
            cell["unit_start"] < final_cutoff
            and cell["no_value_reason"] != "retention_expired"
            for cell in cells
        ):
            # A policy change while CH was reading cannot deliver already-
            # hydrated private evidence, even before the expiry worker runs.
            raise MetricStateConflict("retention_expired")
        return cells


def assert_capture_retained(pin, unit_starts, *, retention_days, now=None):
    cutoff = retention_cutoff(retention_days(), now=now)
    if not pin.is_usable(now) or (cutoff and any(s < cutoff for s in unit_starts)):
        raise MetricStateConflict("retention_expired")


def _terminal_payloads(project_id, *, recovery_attempt=None, through_generation=None):
    # Committed output bytes are unnecessary, but the newest retention checkpoint
    # may be the ONLY durable nomination of unconsumed, retained failed work.
    # Preserve just that handoff, not its frozen output or predecessor revisions.
    chunks = MetricAttemptLedgerChunk.objects.filter(
        attempt__project_id=project_id,
        attempt__state__in=["committed", "aborted"],
    )
    if through_generation is not None:
        chunks = chunks.filter(attempt__generation__lte=through_generation)
    if recovery_attempt is not None:
        from django.db.models import Q

        chunks = chunks.exclude(Q(attempt=recovery_attempt) & ~Q(part="cleanup_intent"))
    return chunks


def _purge_terminal_payloads(
    project_id, *, recovery_attempt=None, through_generation=None
):
    return _terminal_payloads(
        project_id,
        recovery_attempt=recovery_attempt,
        through_generation=through_generation,
    ).delete()[0]


def _delete(store, table, predicate, params):
    store.execute(
        f"ALTER TABLE {table.name} DELETE WHERE {predicate} SETTINGS mutations_sync = 2",
        params,
    )


def _proposal_rows(writer, attempt, head, table, prefix):
    """Logical proposal, never an INSERT of an expired private payload."""
    from tracer.services.derived_metrics.publication import FrozenRows
    from tracer.services.derived_metrics.refresh import TABLE_KIND, payload_select

    current, params = committed_rows_select(
        table, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    payloads = [
        FrozenRows(tuple(plan["rows"]))
        for plan in writer.plans(attempt, kind=TABLE_KIND[table.name])
    ]
    overlay, bound = payload_select(table, payloads, prefix=prefix)
    params.update(bound)
    return (
        f"SELECT * FROM (({current}) UNION ALL ({overlay})) "
        f"ORDER BY generation DESC, {table.version_column} DESC "
        f"LIMIT 1 BY {', '.join(table.logical_key)}",
        params,
    )


def _classify_recovery(
    writer, recovery, fields, params, cutoff, *, spans=False, strict=True
):
    """Classify each owner, never infer conversation age from a member's start."""
    result, blocked = {}, False
    for part, entries in recovery.items():
        result[part] = []
        for offset in range(0, len(entries), 500):
            page = entries[offset : offset + 500]
            identities = tuple(
                {
                    str(e.get("unit_id") or e.get("canonical_session_id") or "")
                    for e in page
                }
            )
            anchors = dict(
                writer.store.execute(
                    f"SELECT unit_id, min(unit_start) FROM ({fields}) "
                    "WHERE unit_id IN %(recovery_ids)s GROUP BY unit_id",
                    {**params, "recovery_ids": identities},
                )
            )
            for original in page:
                entry = dict(original)
                identity = str(
                    entry.get("unit_id") or entry.get("canonical_session_id") or ""
                )
                anchor = anchors.get(identity)
                if identity and anchor is not None and anchor >= cutoff:
                    result[part].append(entry)
                elif (
                    part == "membership_preimage" and spans and entry.get("unit_start")
                ):
                    if datetime.fromisoformat(str(entry["unit_start"])) >= cutoff:
                        # A retained immutable span keeps its start, not an old
                        # conversation/user's erased membership or aliases.
                        for key in (
                            "raw_session_id",
                            "canonical_session_id",
                            "raw_user_id",
                            "canonical_user_id",
                        ):
                            if key in entry:
                                entry[key] = ""
                        result[part].append(entry)
                elif not identity:
                    # Only anonymous dirty bucket nominations may lack owners.
                    if set(entry) <= {
                        "bucket_start",
                        "group_key",
                        "scope_key",
                        "stratum_key",
                        "level",
                        "is_deleted",
                    }:
                        result[part].append(entry)
                elif anchor is None and strict:
                    # Unknown is not retained. Persist only the blocked-work bit.
                    blocked = True
                elif "bucket_start" in entry:
                    result[part].append(
                        {
                            k: v
                            for k, v in entry.items()
                            if k
                            in {"bucket_start", "group_key", "scope_key", "stratum_key"}
                        }
                    )
    return result, blocked


def retirement_plan(writer, draft, head, definitions, cutoff):
    """Convert proposed anchor changes to one complete privacy checkpoint.

    Old and new anchors are considered BEFORE publication. Expiring proposals
    contribute anonymous pre-fold parts at their NEW anchor, and every former
    bucket is rebuilt without the old field. No reader can observe an interim
    expired field or both its live and retired contribution. The draft is PG-only
    and its private bytes are purged with the predecessor revisions after publish.
    """
    from tracer.services.derived_metrics.publication import FrozenRows
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.refresh import payload_select

    fields, params = _proposal_rows(writer, draft, head, FIELD_VALUES, "retire_fields")
    params["retire_cutoff"] = cutoff
    expired = (
        f"SELECT DISTINCT level, unit_id FROM ({fields}) "
        "WHERE unit_start < toDateTime64(%(retire_cutoff)s, 6, 'UTC')"
    )
    span_definitions = [d for d in definitions if d.level == "span"]
    span_dirty = (
        _expired_span_buckets(writer, head, cutoff) if span_definitions else set()
    )
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    as_of = datetime.fromisoformat(str(draft.source_ceilings["as_of"]))
    logical_expiry = bool(
        span_dirty
        or writer.store.execute(f"SELECT count() FROM ({expired})", params)[0][0]
        or (
            span_definitions
            and _span_owner_cleanup_required(writer, head, fields, params, cutoff)
        )
    )
    if not logical_expiry and not span_definitions:
        for definition in definitions:
            if definition.level != "user":
                writer._freeze_buckets(
                    draft, head, definition, fields, params, complete_fields=True
                )
        return draft
    # Span checkpoints also scrub unproven conversation/user owner links from
    # retained span dependencies and PG preimages. Without an actual privacy
    # transition this is an ordinary checkpoint: prior safe pins stay usable.
    span_proposals = {}
    for definition in span_definitions:
        if span_dirty:
            sql, bound = _expiring_span_fields(writer, head, definition, cutoff, as_of)
            span_proposals[(str(definition.metric_id), definition.version)] = (
                sql,
                bound,
            )
            span_dirty.update(
                row[0]
                for row in writer.store.execute(
                    f"SELECT DISTINCT bucket_start FROM ({sql})", bound
                )
            )
    if span_dirty:
        writer.append(
            draft,
            "dirty_before",
            [{"level": "span", "bucket_start": value} for value in sorted(span_dirty)],
        )
    retained = f"SELECT * FROM ({fields}) WHERE (level, unit_id) NOT IN ({expired})"
    ledger = {}
    for chunk in draft.ledger_chunks.exclude(part="cleanup_intent"):
        ledger.setdefault(chunk.part, []).extend(chunk.payload)
    ledger, blocked = _classify_recovery(
        writer,
        ledger,
        fields,
        params,
        cutoff,
        spans=any(d.level == "span" for d in definitions),
        # This is consumed work, not a takeover handoff. Admission of the new
        # fields was proved before preparing this checkpoint; vanished owners'
        # private preimages are unnecessary, but their anonymous dirty keys stay.
        strict=False,
    )
    if blocked:
        raise MetricStateConflict("retention proposal lacks complete membership")
    attempt = writer.begin(
        purpose="compaction",
        ceilings={
            **draft.source_ceilings,
            **({"retention_floor": cutoff} if logical_expiry else {}),
            "logical_expiry": logical_expiry,
            # PG-only drafts can contain owner preimages that the safe span
            # checkpoint no longer needs. GC must finish their cleanup after a
            # publication-before-purge crash, without revoking retained pins or
            # deleting newer failed work that this publication never consumed.
            "private_cleanup_through": draft.generation,
        },
        versions=draft.metric_versions,
        ledger=ledger,
    )
    live_payloads = writer.freeze(attempt, FIELD_VALUES, retained, params)
    retired, rp = committed_rows_select(
        RETIRED_PARTS, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    params.update(rp)
    supplied = {(str(d.metric_id), d.version) for d in definitions}
    missing = writer.store.execute(
        f"SELECT DISTINCT field_id, field_version FROM ({fields}) "
        f"WHERE is_deleted = 0 AND (level, unit_id) IN ({expired})",
        params,
    )
    if any((str(m), v) not in supplied for m, v in missing):
        raise MetricStateConflict("retirement requires every stored field definition")
    for definition in definitions:
        if definition.level == "user":
            continue
        p = {
            **params,
            "retire_metric": str(definition.metric_id),
            "retire_version": definition.version,
        }
        expiring = (
            f"SELECT * FROM ({fields}) WHERE field_id = toUUID(%(retire_metric)s) "
            f"AND field_version = %(retire_version)s AND (level, unit_id) IN ({expired})"
        )
        if definition.level == "span":
            expiring, bound = span_proposals.get(
                (str(definition.metric_id), definition.version),
                (f"SELECT * FROM {FIELD_VALUES.name} WHERE 0", {}),
            )
            p.update(bound)
        previous = (
            f"SELECT {', '.join(KEYS + STATS)} FROM ({retired}) "
            "WHERE metric_id = toUUID(%(retire_metric)s) AND metric_version = %(retire_version)s AND is_deleted = 0"
        )
        parts = merge_parts_select(
            f"({live_parts_select(definition, expiring)}) UNION ALL ({previous})"
        )
        writer.freeze(
            attempt,
            RETIRED_PARTS,
            f"SELECT *, toUInt8(0) AS is_deleted FROM ({parts})",
            p,
            extra={
                "metric_id": "toUUID(%(retire_metric)s)",
                "metric_version": "toUInt32(%(retire_version)s)",
            },
        )
    untouched = f"SELECT * FROM ({retired})"
    if supplied:
        untouched += " WHERE (metric_id, metric_version) NOT IN %(retire_handled)s"
        params["retire_handled"] = tuple((UUID(m), v) for m, v in sorted(supplied))
    writer.freeze(attempt, RETIRED_PARTS, untouched, params)
    for table in (UNIT_STATE, SOURCE_DEPENDENCIES):
        sql, bound = _proposal_rows(writer, draft, head, table, "retire_" + table.name)
        predicate = (
            f"(level, unit_id) IN (SELECT level, unit_id FROM ({retained}))"
            if table == UNIT_STATE
            else f"canonical_session_id IN (SELECT unit_id FROM ({retained}) WHERE level = 'conversation')"
        )
        selected = f"SELECT * FROM ({sql}) WHERE {predicate}"
        if table == SOURCE_DEPENDENCIES and any(d.level == "span" for d in definitions):
            selected = _span_dependencies(sql, predicate, "retire_cutoff")
        writer.freeze(attempt, table, selected, {**params, **bound})
    # Carry anonymous history plus this pass's direct span replacements.
    buckets, bp = _proposal_rows(writer, draft, head, BUCKET_TOTALS, "retire_buckets")
    recomputed = tuple(
        (UUID(str(d.metric_id)), d.version) for d in definitions if d.level != "user"
    )
    dirty = tuple(
        sorted(
            {
                str(entry["bucket_start"])
                for entries in ledger.values()
                for entry in entries
                if "bucket_start" in entry
            }
        )
    )
    if recomputed and dirty:
        # Never freeze the same logical key twice in one attempt: merges would
        # invalidate the earlier batch's physical visibility receipt.
        buckets = (
            f"SELECT * FROM ({buckets}) WHERE NOT ((metric_id, metric_version) IN %(retire_recomputed)s "
            "AND bucket_start IN (SELECT parseDateTimeBestEffort(arrayJoin(%(retire_dirty)s), 'UTC')))"
        )
        bp.update(retire_recomputed=recomputed, retire_dirty=list(dirty))
    writer.freeze(attempt, BUCKET_TOTALS, buckets, bp)
    live, lp = payload_select(FIELD_VALUES, live_payloads, prefix="retire_live")
    retired_payloads = [
        FrozenRows(tuple(p["rows"]))
        for p in writer.plans(attempt, kind="bucket_retired_parts")
    ]
    new_retired, nrp = payload_select(
        RETIRED_PARTS, retired_payloads, prefix="retire_anonymous"
    )
    for definition in definitions:
        if definition.level == "span":
            writer._freeze_span_buckets(
                attempt,
                head,
                definition,
                scope,
                as_of=as_of,
                cutoff=cutoff,
                retired_override=(new_retired, nrp),
            )
        elif definition.level != "user":
            writer._freeze_buckets(
                attempt,
                head,
                definition,
                live,
                lp,
                complete_fields=True,
                retired_override=(new_retired, nrp),
            )
    MetricRefreshAttempt.abort(
        draft.id, reason="retention_replan", **writer.lease.check()
    )
    return attempt


def purge_checkpoint(writer, attempt, manifest, *, recovery_pending=False, now=None):
    """Retry cleanup from a logically safe head; revocation happened at publish."""
    if connection.in_atomic_block:
        raise MetricStateConflict("physical purge requires an outer commit boundary")
    with serialized_project(writer.lease.project_id):
        writer.lease.check()
        head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
        if head is None or head.revoked_at:
            raise MetricStateConflict("retention cleanup requires a safe checkpoint")
        _purge_terminal_payloads(
            writer.lease.project_id,
            recovery_attempt=head.published_by
            if head.published_by.source_ceilings.get("recovery_pending")
            else None,
            through_generation=head.generation,
        )
    return finish_pending_expiry(writer, now=now)


def finish_pending_expiry(writer, *, now=None):
    """Drain obligations independently of ledger/manifest GC and horizon changes.

    A successful early DELETE is NOT a completion receipt. Unknown submission
    outcomes remain pending, even after a timeout, lost lease or server restart.
    Once every submission has a synchronous response, a SECOND verified scrub is
    mandatory. A crash before its durable receipt safely repeats that scrub.
    """
    from tracer.models.derived_metric import MetricErasureRequest

    project_id = writer.lease.project_id
    with serialized_project(project_id):
        writer.lease.check()
        head = MetricProjectRefresh.for_project(project_id).head_manifest
        if head is None or head.revoked_at:
            raise MetricStateConflict("retention cleanup requires a safe checkpoint")
        boundaries = [
            payload["through_generation"]
            for payload in MetricErasureRequest.objects.filter(
                project_key=project_id, store_payload__protocol="metric_expiry"
            )
            .exclude(status="done")
            .values_list("store_payload", flat=True)
        ]
        private_cleanup = head.published_by.source_ceilings.get(
            "private_cleanup_through"
        )
        if private_cleanup is not None:
            boundaries.append(private_cleanup)
        if boundaries:
            # Commit PG privacy cleanup independently of fallible CH mutations.
            # A normal retained-only span checkpoint can also leave a PG-only
            # predecessor to scrub, without requiring old safe pins to be revoked.
            _purge_terminal_payloads(
                project_id,
                recovery_attempt=head.published_by
                if head.published_by.source_ceilings.get("recovery_pending")
                else None,
                through_generation=max(boundaries),
            )
    with serialized_project(project_id):
        writer.lease.check()
        head = MetricProjectRefresh.for_project(project_id).head_manifest
        if head is None or head.revoked_at:
            raise MetricStateConflict("retention cleanup requires a safe checkpoint")
        writer.store.assert_visible(head)
        shard = writer.store.identity()
        keep = {a for _, a in head.allowed_attempts()}
        # Publication-time revocation removed every affected old pin. Preserve
        # safe POST-expiry states that a later compaction superseded while a new
        # request/snapshot still holds them; scrubbing only against the head
        # would otherwise turn a valid retained capture into an empty result.
        roots = set(
            MetricGenerationPin.objects.filter(
                project_id=project_id,
                invalidated_at__isnull=True,
                expires_at__gt=writer.lease.clock(),
                manifest__revoked_at__isnull=True,
                definition_version__pins_revoked_at__isnull=True,
            ).values_list("manifest_id", flat=True)
        )
        roots.update(
            MetricRefreshAttempt.objects.filter(
                project_id=project_id,
                state__in=MetricRefreshAttempt.OPEN_STATES,
                expires_at__gt=writer.lease.clock(),
                parent_manifest__revoked_at__isnull=True,
            )
            .exclude(parent_manifest_id=None)
            .values_list("parent_manifest_id", flat=True)
        )
        for retained in MetricReadManifest.objects.filter(id__in=roots):
            keep.update(a for _, a in retained.allowed_attempts())
        keep = tuple(keep) or (UUID(int=0),)
        pending = MetricErasureRequest.objects.filter(
            project_key=project_id,
            store_payload__protocol="metric_expiry",
        ).exclude(status="done")
        complete = True
        for obligation in pending.order_by("requested_at"):
            # Recovery can enter here without purge_checkpoint, including a
            # crash immediately after logical publication. Attempt TTL is not
            # permission to retain expired PG preimages or frozen replay bytes.
            recovery_attempt = (
                head.published_by
                if head.published_by.source_ceilings.get("recovery_pending")
                else None
            )
            through = obligation.store_payload["through_generation"]
            _purge_terminal_payloads(
                project_id,
                recovery_attempt=recovery_attempt,
                through_generation=through,
            )
            if _terminal_payloads(
                project_id,
                recovery_attempt=recovery_attempt,
                through_generation=through,
            ).exists():
                raise MetricStateConflict("physical expiry private scrub is incomplete")
            if obligation.store_payload.get("shard") != shard:
                raise MetricStateConflict("physical expiry shard is not proven")
            params = {
                "purge_org": str(writer.org_id),
                "purge_project": str(project_id),
                "purge_through": through,
                "purge_keep": keep,
            }
            predicate = (
                "org_id = toUUID(%(purge_org)s) AND project_id = toUUID(%(purge_project)s) "
                "AND generation <= %(purge_through)s AND attempt_id NOT IN %(purge_keep)s"
            )
            tables = (
                FIELD_VALUES,
                TABLES["metric_field_values_checkpoint"],
                SOURCE_DEPENDENCIES,
                UNIT_STATE,
            )
            # Prompt scrub, even while an old submission may still land.
            for table in tables:
                _delete(writer.store, table, predicate, params)
            submissions = MetricErasureRequest.objects.filter(
                project_key=project_id,
                store_payload__protocol="metric_submission",
                store_payload__generation__lte=through,
            )
            if submissions.exclude(
                status="done", receipts__synchronous_response__drained=1
            ).exists():
                complete = False
                continue
            # Registration is fenced under the same project lock: no new old-
            # namespace submission can appear after this barrier observation.
            for table in tables:
                _delete(writer.store, table, predicate, params)
                if writer.store.execute(
                    f"SELECT count() FROM {table.name} WHERE {predicate}", params
                ) != [(0,)]:
                    raise MetricStateConflict("physical expiry scrub is incomplete")
            MetricErasureRequest.objects.filter(id=obligation.id).update(
                status="done",
                completed_at=now or writer.lease.clock(),
                receipts={
                    "write_barrier": {"drained": submissions.count()},
                    "final_scrub": {
                        "verified_tables": len(tables),
                        "remaining": 0,
                        "private_payloads_remaining": 0,
                    },
                },
            )
            # Namespace receipts have served their proof; no private rows or
            # unbounded per-write history is required after completion.
            submissions.delete()
        replan = (
            MetricErasureRequest.objects.filter(
                project_key=project_id, store_payload__protocol="metric_policy_replan"
            )
            .exclude(status="done")
            .exists()
        )
        MetricProjectRefresh.objects.filter(project_id=project_id).update(
            pending_expiry=not complete or replan
        )
        return complete and not replan


def _span_dependencies(sql, retained_conversation, cutoff_parameter):
    # Retained span identities need their old start, not an expired conversation's
    # or user's private membership. Do not preserve those IDs just to track spans.
    replacements = ", ".join(
        f"if({retained_conversation}, {column}, '') AS {column}"
        for column in (
            "raw_session_id",
            "canonical_session_id",
            "raw_user_id",
            "canonical_user_id",
        )
    )
    return (
        f"SELECT * REPLACE({replacements}) FROM ({sql}) WHERE ({retained_conversation}) "
        f"OR unit_start >= toDateTime64(%({cutoff_parameter})s, 6, 'UTC')"
    )


def _span_owner_cleanup_required(writer, head, fields, field_params, cutoff):
    """A retained span does not prove retention of its private owner links."""
    dependencies, params = committed_rows_select(
        SOURCE_DEPENDENCIES,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    owner_columns = (
        "raw_session_id",
        "canonical_session_id",
        "raw_user_id",
        "canonical_user_id",
    )
    return bool(
        writer.store.execute(
            f"SELECT count() FROM ({dependencies}) WHERE "
            f"({' OR '.join(f'notEmpty({c})' for c in owner_columns)}) "
            f"AND canonical_session_id NOT IN (SELECT unit_id FROM ({fields}) "
            "WHERE level = 'conversation' AND unit_start >= toDateTime64(%(span_owner_cutoff)s, 6, 'UTC'))",
            {**field_params, **params, "span_owner_cutoff": cutoff},
        )[0][0]
    )


def _expired_span_buckets(writer, head, cutoff):
    """Private span membership, not the mere presence of a span definition."""
    dependencies, params = committed_rows_select(
        SOURCE_DEPENDENCIES,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    return {
        row[0]
        for row in writer.store.execute(
            f"SELECT DISTINCT toStartOfFifteenMinutes(unit_start) FROM ({dependencies}) "
            "WHERE unit_start < toDateTime64(%(span_expiry_cutoff)s, 6, 'UTC')",
            {**params, "span_expiry_cutoff": cutoff},
        )
    }


def _expiring_span_fields(writer, head, definition, cutoff, now):
    """Freeze only the newly retired source interval, never count it twice."""
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.unit_reads import span_values_select

    floor = head.published_by.source_ceilings.get("retention_floor")
    if floor:
        lower = datetime.fromisoformat(floor)
    else:
        buckets, params = committed_rows_select(
            BUCKET_TOTALS,
            head,
            org_id=writer.org_id,
            project_id=writer.lease.project_id,
        )
        rows = writer.store.execute(
            f"SELECT minOrNull(bucket_start) FROM ({buckets}) WHERE metric_id = toUUID(%(retiring_metric)s) "
            "AND metric_version = %(retiring_version)s AND is_deleted = 0",
            {
                **params,
                "retiring_metric": str(definition.metric_id),
                "retiring_version": definition.version,
            },
        )
        lower = rows[0][0] or cutoff
    fields, params = span_values_select(
        definition,
        Scope(writer.org_id, writer.workspace_id, writer.lease.project_id),
        start=lower,
        end=max(lower, cutoff),
        as_of=now,
    )
    dependencies, dp = committed_rows_select(
        SOURCE_DEPENDENCIES,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    params.update(dp)
    if writer.store.execute(
        f"SELECT count() FROM ({fields}) WHERE unit_id NOT IN "
        f"(SELECT toJSONString(tuple(trace_id, span_id)) FROM ({dependencies}) WHERE is_deleted = 0)",
        params,
    )[0][0]:
        from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven

        raise MetricAdmissionUnproven("metric_admission_unproven")
    return fields, params


def expire_project(writer, definitions, *, retention_days, now=None, as_of=None):
    """Freeze retirement, publish a complete replacement, revoke, then purge.

    A failed physical purge leaves safe reads (the old pins are revoked) and is
    idempotently retried; it must not be reported as a completed physical erase.
    All old physical revisions are deleted, not merely the current allowlist.
    """
    if connection.in_atomic_block:
        raise MetricStateConflict("physical purge requires an outer commit boundary")
    definitions = list(definitions)
    now = now or writer.lease.clock()
    cutoff = retention_cutoff(retention_days(), now=now)
    if cutoff is None:
        return {"unlimited": True, "purged": False}
    project_id = writer.lease.project_id
    with serialized_project(project_id):
        writer.lease.check()
        head = MetricProjectRefresh.for_project(project_id).head_manifest
        if head is None:
            return {"unlimited": False, "purged": False}
        writer.store.assert_visible(head)
        if head.revoked_at:
            raise MetricStateConflict("expired base requires source rebuild")
        recovery = writer.abort_stale()
        for opened in MetricRefreshAttempt.objects.filter(
            project_id=project_id, state__in=MetricRefreshAttempt.OPEN_STATES
        ):
            for chunk in opened.ledger_chunks.exclude(part="cleanup_intent"):
                recovery[chunk.part].extend(chunk.payload)
            MetricRefreshAttempt.abort(
                opened.id, reason="retention_replan", **writer.lease.check()
            )
        params = {
            "expiry_cutoff": cutoff,
            "expiry_project": str(project_id),
            "expiry_org": str(writer.org_id),
        }
        fields, p = committed_rows_select(
            FIELD_VALUES, head, org_id=writer.org_id, project_id=project_id
        )
        params.update(p)
        retired, rp = committed_rows_select(
            RETIRED_PARTS, head, org_id=writer.org_id, project_id=project_id
        )
        params.update(rp)
        # Expiry belongs to the logical conversation, not a physical revision
        # or one member span. A stored earlier anchor expires every field version
        # and every dependency of that unit, even if another version/member has
        # a newer timestamp. These predicates are AFTER committed-row collapse.
        expired_units = (
            f"SELECT DISTINCT level, unit_id FROM ({fields}) "
            "WHERE unit_start < toDateTime64(%(expiry_cutoff)s, 6, 'UTC')"
        )
        retained_units = (
            f"SELECT DISTINCT level, unit_id FROM ({fields}) "
            f"WHERE (level, unit_id) NOT IN ({expired_units})"
        )
        span_definitions = [d for d in definitions if d.level == "span"]
        span_dirty = (
            _expired_span_buckets(writer, head, cutoff) if span_definitions else set()
        )
        if (
            not any(recovery.values())
            and not span_dirty
            and not (
                span_definitions
                and _span_owner_cleanup_required(writer, head, fields, params, cutoff)
            )
            and not writer.store.execute(
                f"SELECT count() FROM ({expired_units})", params
            )[0][0]
        ):
            pending = (
                MetricErasureRequest.objects.filter(
                    project_key=project_id, store_payload__protocol="metric_expiry"
                )
                .exclude(status="done")
                .exists()
            )
            return {
                "unlimited": False,
                "purged": finish_pending_expiry(writer, now=now) if pending else False,
            }
        recovery, recovery_blocked = _classify_recovery(
            writer,
            recovery,
            fields,
            params,
            cutoff,
            spans=any(d.level == "span" for d in definitions),
        )
        recovery_pending = any(recovery.values())
        attempt = writer.begin(
            purpose="compaction",
            ceilings={
                "retention_cutoff": cutoff,
                "retention_floor": cutoff,
                "recovery_pending": recovery_pending,
                # Expired handoffs are discharged without retaining their ids
                # or advancing discovery; blocked work keeps only its flag.
                "consumes_recovery": str(head.id)
                if head.published_by.source_ceilings.get("recovery_pending")
                and not recovery_pending
                else None,
                "recovery_blocked": recovery_blocked
                or head.published_by.source_ceilings.get("recovery_blocked", False),
                "logical_expiry": True,
            },
            versions=[
                {
                    "id": getattr(d, "id", None),
                    "metric_id": d.metric_id,
                    "version": d.version,
                    "level": d.level,
                }
                for d in definitions
            ],
            ledger=recovery,
        )
        writer.freeze_current(
            attempt,
            FIELD_VALUES,
            head,
            where=f"(level, unit_id) NOT IN ({expired_units})",
            params=params,
        )
        for definition in definitions:
            if definition.level == "user":
                continue
            p = {
                **params,
                "expiry_metric": str(definition.metric_id),
                "expiry_version": definition.version,
            }
            predicate = "field_id = toUUID(%(expiry_metric)s) AND field_version = %(expiry_version)s"
            expiring = f"SELECT * FROM ({fields}) WHERE {predicate} AND (level, unit_id) IN ({expired_units})"
            if definition.level == "span":
                expiring, sp = _expiring_span_fields(
                    writer, head, definition, cutoff, as_of or now
                )
                p.update(sp)
                span_dirty.update(
                    row[0]
                    for row in writer.store.execute(
                        f"SELECT DISTINCT bucket_start FROM ({expiring})", p
                    )
                )
            live = live_parts_select(definition, expiring)
            previous = f"SELECT {', '.join(KEYS + STATS)} FROM ({retired}) WHERE metric_id = toUUID(%(expiry_metric)s) AND metric_version = %(expiry_version)s AND is_deleted = 0"
            merged = merge_parts_select(f"({live}) UNION ALL ({previous})")
            writer.freeze(
                attempt,
                RETIRED_PARTS,
                f"SELECT *, toUInt8(0) AS is_deleted FROM ({merged})",
                p,
                extra={
                    "metric_id": "toUUID(%(expiry_metric)s)",
                    "metric_version": "toUInt32(%(expiry_version)s)",
                },
            )
        # Versions outside the supplied backfill/active set keep already anonymous
        # history; no fresh stats may be manufactured without their definition.
        missing = writer.store.execute(
            f"SELECT DISTINCT field_id, field_version FROM ({fields}) WHERE is_deleted = 0 "
            f"AND (level, unit_id) IN ({expired_units})",
            params,
        )
        supplied = {(str(d.metric_id), d.version) for d in definitions}
        if any((str(m), v) not in supplied for m, v in missing):
            raise MetricStateConflict(
                "retirement requires every stored field definition"
            )
        untouched = f"SELECT * FROM ({retired})"
        if supplied:
            untouched += " WHERE (metric_id, metric_version) NOT IN %(expiry_handled)s"
            params["expiry_handled"] = tuple((UUID(m), v) for m, v in sorted(supplied))
        writer.freeze(attempt, RETIRED_PARTS, untouched, params)
        writer.append(
            attempt,
            "dirty_before",
            [{"level": "span", "bucket_start": value} for value in sorted(span_dirty)],
        )
        for table in (BUCKET_TOTALS, UNIT_STATE, SOURCE_DEPENDENCIES):
            where = "1"
            if table == BUCKET_TOTALS and span_definitions and span_dirty:
                where = (
                    "NOT ((metric_id, metric_version) IN %(expiry_span_metrics)s "
                    "AND bucket_start IN %(expiry_span_buckets)s)"
                )
                params.update(
                    expiry_span_metrics=tuple(
                        (UUID(str(d.metric_id)), d.version) for d in span_definitions
                    ),
                    expiry_span_buckets=tuple(sorted(span_dirty)),
                )
            if table == SOURCE_DEPENDENCIES:
                # B04's unit_start is the SPAN start. A recent member does not
                # make an expired conversation's private membership retainable.
                where = (
                    "canonical_session_id IN (SELECT unit_id FROM "
                    f"({retained_units}) WHERE level = 'conversation')"
                )
            elif table == UNIT_STATE:
                where = f"(level, unit_id) IN ({retained_units})"
            if table == SOURCE_DEPENDENCIES and any(
                d.level == "span" for d in definitions
            ):
                sql, bound = committed_rows_select(
                    table, head, org_id=writer.org_id, project_id=project_id
                )
                writer.freeze(
                    attempt,
                    table,
                    _span_dependencies(sql, where, "expiry_cutoff"),
                    {**params, **bound},
                )
            else:
                writer.freeze_current(attempt, table, head, where=where, params=params)
        if span_definitions:
            from tracer.services.derived_metrics.publication import FrozenRows
            from tracer.services.derived_metrics.queries import Scope
            from tracer.services.derived_metrics.refresh import payload_select

            new_retired, nrp = payload_select(
                RETIRED_PARTS,
                [
                    FrozenRows(tuple(p["rows"]))
                    for p in writer.plans(attempt, kind="bucket_retired_parts")
                ],
                prefix="expiry_anonymous",
            )
            for definition in span_definitions:
                writer._freeze_span_buckets(
                    attempt,
                    head,
                    definition,
                    Scope(writer.org_id, writer.workspace_id, project_id),
                    as_of=as_of or now,
                    cutoff=cutoff,
                    retired_override=(new_retired, nrp),
                )
        writer.seal(attempt, cursors={"pending_expiry": False})
    manifest = writer.stage_and_publish(attempt)
    # Revocations and cleanup intent are ALREADY committed with this manifest.
    # Never roll PG back to evidence removed by a physical mutation.
    complete = purge_checkpoint(
        writer, attempt, manifest, recovery_pending=recovery_pending, now=now
    )
    return {"unlimited": False, "purged": complete, "manifest": str(manifest.id)}


def purge_by_scope(store, request, *, writer=None):
    """B18 live-row entry point; the caller owns snapshot cleanup and receipts.

    Durable request keys work after PG parent deletion. User/session erasure
    requires a writer while a project still exists, so unrelated live field rows
    get a complete checkpoint. Anonymous project history is conservatively lost.
    This function NEVER marks the whole erasure request done.
    """
    if connection.in_atomic_block:
        raise MetricStateConflict("physical purge requires an outer commit boundary")
    kind = request.scope_kind
    if kind == "version" and type(request.store_payload.get("version")) is not int:
        raise MetricStateConflict("version erasure lacks physical ordinal")
    if kind == "run":
        return {"live_rows": "not_applicable"}
    params = {
        "erase_org": str(request.organization_key),
        "erase_project": str(request.project_key),
    }
    base = "org_id = toUUID(%(erase_org)s)"
    if request.project_key:
        base += " AND project_id = toUUID(%(erase_project)s)"
    if kind == "history":
        if request.store_payload.get("protocol") in {
            "metric_expiry",
            "metric_submission",
            "metric_policy_replan",
        }:
            raise MetricStateConflict(
                "writer quiescence receipt requires the expiry protocol"
            )
        ids = tuple(UUID(a["id"]) for a in request.store_payload.get("attempts", []))
        if ids:
            for table in TABLES.values():
                _delete(
                    store,
                    table,
                    base + " AND attempt_id IN %(erase_attempts)s",
                    {**params, "erase_attempts": ids},
                )
        return {"attempt_count": len(ids)}
    if kind in {"users", "sessions"}:
        if writer is None or str(writer.lease.project_id) != str(request.project_key):
            raise MetricStateConflict("subject erasure requires a project writer")
        with serialized_project(request.project_key):
            head = MetricProjectRefresh.for_project(request.project_key).head_manifest
            if head is None or head.revoked_at:
                raise MetricStateConflict(
                    "subject erasure requires a usable source manifest"
                )
            store.assert_visible(head)
            ids = tuple(request.subject_ids)
            params["erase_subjects"] = ids
            field_predicate = (
                "user_id IN %(erase_subjects)s"
                if kind == "users"
                else "(session_id IN %(erase_subjects)s OR unit_id IN %(erase_subjects)s)"
            )
            dep_predicate = (
                "(raw_user_id IN %(erase_subjects)s OR canonical_user_id IN %(erase_subjects)s)"
                if kind == "users"
                else "(raw_session_id IN %(erase_subjects)s OR canonical_session_id IN %(erase_subjects)s)"
            )
            fields, fp = committed_rows_select(
                FIELD_VALUES,
                head,
                org_id=request.organization_key,
                project_id=request.project_key,
            )
            deps, dp = committed_rows_select(
                SOURCE_DEPENDENCIES,
                head,
                org_id=request.organization_key,
                project_id=request.project_key,
            )
            params.update(fp)
            params.update(dp)
            # Unassignable multi-user conversations still carry the erased user's
            # evidence: remove that conversation, rather than leaking its maps.
            victims = store.execute(
                f"SELECT DISTINCT unit_id FROM ({fields}) WHERE {field_predicate} UNION DISTINCT SELECT canonical_session_id FROM ({deps}) WHERE {dep_predicate}",
                params,
            )
            params["erase_units"] = tuple(row[0] for row in victims) or ("",)
            attempt = writer.begin(
                purpose="compaction", ceilings={"erasure": str(request.id)}
            )
            writer.freeze(
                attempt,
                FIELD_VALUES,
                f"SELECT * FROM ({fields}) WHERE NOT ({field_predicate}) AND unit_id NOT IN %(erase_units)s",
                params,
            )
            for table in (SOURCE_DEPENDENCIES, UNIT_STATE):
                sql, p = committed_rows_select(
                    table,
                    head,
                    org_id=request.organization_key,
                    project_id=request.project_key,
                )
                predicate = (
                    dep_predicate
                    if table == SOURCE_DEPENDENCIES
                    else "unit_id IN %(erase_units)s"
                )
                writer.freeze(
                    attempt,
                    table,
                    f"SELECT * FROM ({sql}) WHERE NOT ({predicate})",
                    {**p, **params},
                )
            writer.seal(attempt)
        manifest = writer.stage_and_publish(attempt)
        with serialized_project(request.project_key):
            old = list(
                MetricReadManifest.objects.filter(project_id=request.project_key)
                .exclude(id=manifest.id)
                .values_list("id", flat=True)
            )
            revoke_generations(request.project_key, reason="erased", manifest_ids=old)
        with serialized_project(request.project_key):
            writer.lease.check()
            for table in TABLES.values():
                if table.name.startswith("metric_field_values") or table in (
                    SOURCE_DEPENDENCIES,
                    UNIT_STATE,
                ):
                    # Every old generation was revoked, and the new complete
                    # checkpoint contains ALL unrelated live rows. Collect ALL
                    # older evidence, not just a rediscovered id list: this also
                    # makes interruption after field purge restart-safe for state
                    # and dependency rows that have no surviving field lineage.
                    predicate = "attempt_id != toUUID(%(erase_new)s)"
                else:
                    predicate = "1"  # no surviving per-unit lineage in anonymous parts
                _delete(
                    store,
                    table,
                    base + " AND (" + predicate + ")",
                    {**params, "erase_new": str(attempt.id)},
                )
            _purge_terminal_payloads(request.project_key)
            return {"manifest": str(manifest.id), "anonymous_history_dropped": True}
    if kind not in {"organization", "project", "metric", "version"}:
        raise ValueError("unsupported live erasure scope")
    # Parent-independent intents may reach this path after the project vanished.
    projects = (
        [request.project_key]
        if request.project_key
        else list(
            MetricProjectRefresh.objects.filter(
                project__organization_id=request.organization_key
            ).values_list("project_id", flat=True)
        )
    )
    for project in projects:
        with serialized_project(project):
            if kind in {"metric", "version"}:
                from tracer.models.derived_metric import DerivedMetricVersion

                versions = DerivedMetricVersion.objects.filter(
                    metric_id=request.metric_key
                )
                if kind == "version":
                    versions = versions.filter(id=request.version_key)
                revoke_generations(
                    project,
                    reason="erased",
                    definition_version_ids=list(versions.values_list("id", flat=True)),
                )
            else:
                revoke_generations(project, reason="erased")
            _purge_terminal_payloads(project)
    for table in TABLES.values():
        predicate = base
        if kind in {"metric", "version"}:
            key = (
                "field_id"
                if table.name.startswith("metric_field_values")
                else "metric_id"
            )
            if key not in table.columns:
                continue
            predicate += f" AND {key} = toUUID(%(erase_metric)s)"
            params["erase_metric"] = str(request.metric_key)
            if kind == "version":
                # The durable version UUID needs its physical ordinal in B18's
                # intent; never guess it after a parent cascade deleted the row.
                ordinal = request.store_payload.get("version")
                if not ordinal:
                    raise MetricStateConflict("version erasure lacks physical ordinal")
                version_col = "field_version" if key == "field_id" else "metric_version"
                predicate += f" AND {version_col} = %(erase_version)s"
                params["erase_version"] = ordinal
        _delete(store, table, predicate, params)
    return {"live_rows": "purged"}
