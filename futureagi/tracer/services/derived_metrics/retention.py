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

from datetime import UTC, timedelta
from uuid import UUID

from django.db import connection
from django.utils import timezone

from tracer.models.derived_metric import (
    MetricAttemptLedgerChunk,
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
        return [dict(zip((c[0] for c in columns), row, strict=True)) for row in rows]


def assert_capture_retained(pin, unit_starts, *, retention_days, now=None):
    cutoff = retention_cutoff(retention_days(), now=now)
    if not pin.is_usable(now) or (cutoff and any(s < cutoff for s in unit_starts)):
        raise MetricStateConflict("retention_expired")


def _purge_terminal_payloads(project_id):
    # Retaining a committed manifest does NOT require private attempt preimages.
    # Keep frozen audit hashes/counts; no normal UPDATE rewrites a sealed payload.
    return MetricAttemptLedgerChunk.objects.filter(
        attempt__project_id=project_id,
        attempt__state__in=["committed", "aborted"],
    ).delete()[0]


def _delete(store, table, predicate, params):
    store.execute(
        f"ALTER TABLE {table.name} DELETE WHERE {predicate} SETTINGS mutations_sync = 2",
        params,
    )


def expire_project(writer, definitions, *, retention_days, now=None):
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
        writer.abort_stale()
        for opened in MetricRefreshAttempt.objects.filter(
            project_id=project_id, state__in=MetricRefreshAttempt.OPEN_STATES
        ):
            MetricRefreshAttempt.abort(
                opened.id, reason="retention_replan", **writer.lease.check()
            )
        attempt = writer.begin(
            purpose="compaction",
            ceilings={"retention_cutoff": cutoff, "retention_floor": cutoff},
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
        for table in (BUCKET_TOTALS, UNIT_STATE, SOURCE_DEPENDENCIES):
            where = "1"
            if table == SOURCE_DEPENDENCIES:
                # B04's unit_start is the SPAN start. A recent member does not
                # make an expired conversation's private membership retainable.
                where = (
                    "canonical_session_id IN (SELECT unit_id FROM "
                    f"({retained_units}) WHERE level = 'conversation')"
                )
            elif table == UNIT_STATE:
                where = f"(level, unit_id) IN ({retained_units})"
            writer.freeze_current(attempt, table, head, where=where, params=params)
        writer.seal(attempt, cursors={"pending_expiry": False})
        manifest = writer.stage_and_publish(attempt)
    # Commit publication and revocation BEFORE issuing mutations: an external
    # failure must never roll PG back to a manifest whose evidence was deleted.
    with serialized_project(project_id):
        old_manifests = list(
            MetricReadManifest.objects.filter(project_id=project_id)
            .exclude(id=manifest.id)
            .values_list("id", flat=True)
        )
        revoke_generations(
            project_id, reason="retention_expired", manifest_ids=old_manifests, now=now
        )
    with serialized_project(project_id):
        writer.lease.check()
        scope = "org_id = toUUID(%(expiry_org)s) AND project_id = toUUID(%(expiry_project)s)"
        # The new checkpoint contains ALL retained private evidence and every
        # old pin is revoked. Drop every other revision rather than checking its
        # own (possibly newer) start or rediscovering victims from deleted fields.
        # This stays bounded in Python and remains safe/retryable if any mutation
        # fails after a preceding table's evidence has already been removed.
        for table in (
            FIELD_VALUES,
            TABLES["metric_field_values_checkpoint"],
            SOURCE_DEPENDENCIES,
            UNIT_STATE,
        ):
            _delete(
                writer.store,
                table,
                scope + " AND attempt_id != toUUID(%(expiry_attempt)s)",
                {**params, "expiry_attempt": str(attempt.id)},
            )
        _purge_terminal_payloads(project_id)
        return {"unlimited": False, "purged": True, "manifest": str(manifest.id)}


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
