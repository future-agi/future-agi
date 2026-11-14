"""Live-store retirement and physical purge. Completed snapshots are untouched.

Every retention and erasure checkpoint rewrites the whole retained head, so
its bulk copies use checkpoint-sized pages (MAX_COMPACTION_ROWS, 4 MiB), as
compaction does: 500-row pages re-ran the head's collapse once per page
(364 us a row at 20k rows a table, 528 at 100k; 71-76 with checkpoint pages).

Retention is based on unit_start, not the list window. Anonymous PRE-FOLD
statistics survive expiry, but user-level metrics have no such history. Erasure
is different: without unit lineage in retired parts we conservatively discard
project aggregate history. This is the design's safe fallback, not subtraction.

Callers schedule expiry independently of arrivals and retry failed mutations.
Purge planning uses the same short serialization as publication/pin capture;
native mutations use immutable, generation-bounded namespaces and write receipts.
The original manifest is revoked before any physical deletion. Audit hashes are
never rewritten; terminal private replay/preimage chunks can be deleted whole.
"""

import json
from contextlib import ExitStack, nullcontext
from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.db import connection
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetricVersion,
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
    MAX_COMPACTION_ROWS,
    committed_rows_select,
    pinned_read,
)
from tracer.services.derived_metrics.queries import utc_timestamp
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_VALUES,
    LIST_CURRENT_TABLES,
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


DEFAULT_EXPIRY_INTERVAL_SECONDS = 86_400
_CURRENT_HEAD = object()


def expiry_interval():
    """How far physical expiry may trail the policy (METRICS_EXPIRY_INTERVAL_SECONDS).

    Default one day: like raw spans (dropped by daily parts after their TTL),
    expired metric evidence is physically retired at most one interval late.
    Reads always apply the exact cutoff (retention_cutoff); erasure is prompt.
    """
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    value = getattr(
        settings, "METRICS_EXPIRY_INTERVAL_SECONDS", DEFAULT_EXPIRY_INTERVAL_SECONDS
    )
    if type(value) is not int or not 60 <= value <= 604_800:
        raise ImproperlyConfigured(
            "METRICS_EXPIRY_INTERVAL_SECONDS must be an integer from 60 to 604800"
        )
    return timedelta(seconds=value)


def physical_cutoff(writer, *, now, head=_CURRENT_HEAD, ceilings=None):
    """The cutoff writers enforce physically: batched once per expiry interval.

    Within one interval of the last logical expiry (retention_floor_set_at on
    the head) it holds that expiry's floor, so a continuous cutoff no longer
    rewrites the whole head on nearly every pass. After the interval it is the
    exact cutoff again, and the next pass retires everything past it. Reads
    keep the exact cutoff, so no reader sees evidence past the policy.
    """
    days = writer.retention_days() if writer.retention_days is not None else 0
    exact = retention_cutoff(days, now=now)
    if exact is None:
        return None
    # Whole seconds: clickhouse-driver binds a datetime parameter to seconds,
    # so a sub-second cutoff retained a unit that this process then counted as
    # expired (MetricRetentionChanged on every busy project's expiry). The
    # writers' cutoff is at most a second earlier; reads stay exact.
    exact = exact.replace(microsecond=0)
    if ceilings is None:
        if head is _CURRENT_HEAD:
            head = MetricProjectRefresh.for_project(
                writer.lease.project_id
            ).head_manifest
        ceilings = head.published_by.source_ceilings if head is not None else {}
    floor, stamped, stamped_days = (
        ceilings.get("retention_floor"),
        ceilings.get("retention_floor_set_at"),
        ceilings.get("retention_floor_days"),
    )
    # A shortened policy is a retention CHANGE, not the cutoff moving: it
    # applies at once, never batched.
    if (
        floor
        and stamped
        and type(stamped_days) is int
        and days >= stamped_days
        and now - datetime.fromisoformat(str(stamped)) < expiry_interval()
    ):
        return min(exact, datetime.fromisoformat(str(floor)))
    return exact


def retained_rows_select(sql, cutoff):
    """Wrap AFTER current-row collapse. No expiry predicate may select an old row."""
    if cutoff is None:
        return sql, {}
    return (
        f"SELECT * FROM ({sql}) WHERE unit_start >= toDateTime64(%(retention_cutoff)s, 6, 'UTC')",
        {"retention_cutoff": utc_timestamp(cutoff)},
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
            {
                **params,
                # Microseconds kept: a datetime binds to whole seconds.
                "retention_cutoff": None if cutoff is None else utc_timestamp(cutoff),
            },
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
    # Legacy writers duplicated private repair keysets outside the purgeable
    # ledger. Privacy cleanup cannot wait for those audit rows' history TTL.
    MetricRefreshAttempt.scrub_private_cursor_metadata(project_id)
    from tracer.models.derived_metric import MetricCompactionRange

    ranges = MetricCompactionRange.objects.filter(
        attempt__project_id=project_id, attempt__state__in=["committed", "aborted"]
    )
    if through_generation is not None:
        ranges = ranges.filter(attempt__generation__lte=through_generation)
    # Bounds can identify erased subjects. The independent nonidentifying
    # submission outbox survives this deletion and still fences late writes.
    removed = ranges.delete()[0]
    return (
        removed
        + _terminal_payloads(
            project_id,
            recovery_attempt=recovery_attempt,
            through_generation=through_generation,
        ).delete()[0]
    )


def _delete(store, table, predicate, params):
    store.execute(
        f"ALTER TABLE {table.name} DELETE WHERE {predicate} SETTINGS mutations_sync = 2",
        params,
    )


class ProposalCaptures:
    """Pass-scoped session captures of one PREPARED proposal (SessionCapture).

    Only BOUNDED inputs are materialized: a draft's frozen batches (one batch
    per INSERT) and the retained-owner set. The committed head side of every
    proposal stays an inline committed_rows_select over immutable allowlisted
    attempts: it is the project's whole retained history, so copying it would
    put a hard ceiling on every finite-retention refresh. Later statements,
    including each 500-row freeze page, name the captured relations instead of
    repeating inline RowBinary bytes or the owner subquery, so per-statement
    SQL is bounded independently of the proposal's size.
    """

    def __init__(self, writer, draft, head):
        self.writer, self.draft, self.head = writer, draft, head
        self._stack = ExitStack()
        self._captures = {}
        self._inline_owners = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)

    def _capture(self, key, schema, sources, role):
        from tracer.services.derived_metrics.frozen_relations import SessionCapture

        if key not in self._captures:
            self._captures[key] = self._stack.enter_context(
                SessionCapture(
                    self.writer.store, schema=schema, sources=sources, role=role
                )
            )
        return self._captures[key]

    def overlay(self, table, attempt=None):
        """One attempt's frozen batches for one table, never a mutable read."""
        from tracer.services.derived_metrics.publication import FrozenRows
        from tracer.services.derived_metrics.refresh import TABLE_KIND, payload_select

        attempt = attempt or self.draft
        writer = self.writer

        def sources():
            for number, plan in enumerate(
                writer.plans(attempt, kind=TABLE_KIND[table.name])
            ):
                yield payload_select(
                    table, [FrozenRows.from_plan(plan)], prefix=f"capture_{number}"
                )

        capture = self._capture(
            ("overlay", str(attempt.id), table.name), table.schema, sources, "overlay"
        )
        return capture.select, {}

    def proposal(self, table):
        """Committed head collapsed with this draft's overlay (one generation)."""
        writer = self.writer
        current, params = committed_rows_select(
            table, self.head, org_id=writer.org_id, project_id=writer.lease.project_id
        )
        overlay, _ = self.overlay(table)
        return (
            f"SELECT * FROM (({current}) UNION ALL ({overlay})) "
            f"ORDER BY generation DESC, {table.version_column} DESC "
            f"LIMIT 1 BY {', '.join(table.logical_key)}",
            params,
        )

    def fields(self):
        """The proposed FIELD_VALUES relation: committed head inline, overlay captured.

        Never a session copy of the head: retained history (every definition,
        version and tombstone) is unbounded, while the overlay is one pass.
        """
        return self.proposal(FIELD_VALUES)

    def owners(self, cutoff, parameter="retire_cutoff"):
        """Conversations whose earliest proposed anchor is retained (or all).

        Exactly _owner_relation with the caller's cutoff BINDING (the same
        parameter every other retention predicate of the pass compares with),
        captured once when it fits the capture budget. A larger owner set is
        that relation inline: callers bind the cutoff and the head bindings
        with their fields bindings. Slower, never a refresh that cannot publish.
        """
        from tracer.services.derived_metrics.frozen_relations import (
            FrozenRelationOverflow,
        )

        key = ("owners", str(cutoff), parameter)
        if key in self._inline_owners:
            return self._inline_owners[key]
        fields, params = self.fields()
        if cutoff is None:
            sql = (
                f"SELECT unit_id FROM ({fields}) WHERE level = 'conversation' "
                "GROUP BY unit_id"
            )
        else:
            sql = _owner_relation(fields, parameter)
            params = {**params, parameter: cutoff}
        try:
            capture = self._capture(
                key, (("unit_id", "String"),), lambda: [(sql, params)], "owners"
            )
        except FrozenRelationOverflow:
            self._inline_owners[key] = sql
            return sql
        return capture.select


def _proposal_overlay(writer, attempt, table, prefix):
    """Raw frozen rows, so callers can bound keys BEFORE proposal collapse."""
    from tracer.services.derived_metrics.publication import FrozenRows
    from tracer.services.derived_metrics.refresh import TABLE_KIND, payload_select

    return payload_select(
        table,
        [
            FrozenRows.from_plan(plan)
            for plan in writer.plans(attempt, kind=TABLE_KIND[table.name])
        ],
        prefix=prefix,
    )


def _proposal_rows(writer, attempt, head, table, prefix):
    """Logical proposal, never an INSERT of an expired private payload."""
    current, params = committed_rows_select(
        table, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    overlay, bound = _proposal_overlay(writer, attempt, table, prefix)
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


def _owner_relation(fields, cutoff_parameter):
    return (
        f"SELECT unit_id FROM ({fields}) WHERE level = 'conversation' "
        "GROUP BY unit_id HAVING min(unit_start) >= "
        f"toDateTime64(%({cutoff_parameter})s, 6, 'UTC')"
    )


def _current_retained(fields, cutoff_parameter, owners=None):
    # Facts do not confer new anchor authority. Retain private payload only for
    # a proven retained conversation, and never keep an expired member itself.
    # A captured owner set is this exact relation, executed once per pass.
    owners = owners or _owner_relation(fields, cutoff_parameter)
    return (
        f"unit_start >= toDateTime64(%({cutoff_parameter})s, 6, 'UTC') "
        f"AND canonical_session_id IN ({owners})"
    )


def _retained_fields(fields, predicate, cutoff_parameter, owners=None):
    """A retained span may not keep an expired/unproven owner's private IDs."""
    owner_retained = (
        f"session_id IN ({owners or _owner_relation(fields, cutoff_parameter)})"
    )
    return (
        "SELECT * REPLACE("
        f"if(level = 'span' AND NOT ({owner_retained}), '', session_id) AS session_id, "
        f"if(level = 'span' AND NOT ({owner_retained}), '', user_id) AS user_id) "
        f"FROM ({fields}) WHERE {predicate}"
    )


def _current_tables(writer, head, draft=None):
    """Skip absent indices, not coverage checks, using verified batch metadata.

    Empty source stores must not cause an otherwise unrelated metric refresh to
    execute their full field-membership subqueries. An omitted table in a normal
    delta inherits its parent's receipts; compaction has its own complete list.
    """
    published = (
        {receipt["table"] for receipt in head.visibility_receipts.get("batches", [])}
        if head is not None
        else set()
    )
    return tuple(
        table
        for table in LIST_CURRENT_TABLES
        if table.name in published
        or (
            draft is not None
            and draft.ledger_chunks.filter(
                part="cleanup_intent",
                payload__0__kind="frozen_batch",
                payload__0__table=table.name,
            ).exists()
        )
    )


def _current_expiry_required(
    writer, head, fields, params, *, draft=None, cutoff_parameter, captures=None
):
    owners = None
    for table in _current_tables(writer, head, draft):
        if captures is not None and owners is None:
            owners = captures.owners(params[cutoff_parameter], cutoff_parameter)
        if draft is not None and captures is not None:
            sql, bound = captures.proposal(table)
        elif draft is not None:
            sql, bound = _proposal_rows(
                writer, draft, head, table, "expiry_" + table.name
            )
        else:
            sql, bound = committed_rows_select(
                table, head, org_id=writer.org_id, project_id=writer.lease.project_id
            )
        retained = _current_retained(fields, cutoff_parameter, owners)
        if writer.store.execute(
            f"SELECT count() FROM (SELECT 1 FROM ({sql}) WHERE NOT ({retained}) LIMIT 1)",
            {**params, **bound},
        )[0][0]:
            return True
    return False


def _require_retention_definitions(writer, definitions):
    for definition in definitions:
        definition_id = getattr(definition, "id", None)
        if (
            definition_id is not None
            and not DerivedMetricVersion.objects.filter(
                id=definition_id,
                metric_id=definition.metric_id,
                version=definition.version,
                metric__project_id=writer.lease.project_id,
                metric__organization_id=writer.org_id,
                metric__deleted=False,
                pins_revoked_at__isnull=True,
            ).exists()
        ):
            raise MetricStateConflict("retention definition is expired or deleted")


OWNER_KEYS = (
    "unit_id",
    "canonical_session_id",
    "raw_session_id",
    "canonical_user_id",
    "raw_user_id",
)


def _owner_entries(ledger):
    """Ledger entries that carry a private owner identity, as comparable bytes."""
    return {
        part: sorted(
            json.dumps(entry, sort_keys=True, default=str)
            for entry in entries
            if any(entry.get(key) for key in OWNER_KEYS)
        )
        for part, entries in ledger.items()
    }


def _span_proposal_cleanup_required(
    writer, captures, fields, params, cutoff, ledger, definitions
):
    """Whether the span checkpoint would change ANY private byte of the proposal.

    The checkpoint transform is an identity exactly when: no proposed (committed
    or draft) dependency outside a retained conversation keeps an owner link or
    an expired start; every proposed lifecycle row still has a field; and the
    draft ledger's owner-bearing entries all classify as retained, unchanged.
    Span fields with unproven owners and expired units are already part of
    logical expiry. Anything else keeps the complete privacy checkpoint.
    """
    owners = captures.owners(cutoff)
    dependencies, bound = captures.proposal(SOURCE_DEPENDENCIES)
    owner_columns = (
        "raw_session_id",
        "canonical_session_id",
        "raw_user_id",
        "canonical_user_id",
    )
    if writer.store.execute(
        f"SELECT count() FROM (SELECT 1 FROM ({dependencies}) "
        f"WHERE canonical_session_id NOT IN ({owners}) AND ("
        "unit_start < toDateTime64(%(retire_cutoff)s, 6, 'UTC') OR "
        + " OR ".join(f"notEmpty({c})" for c in owner_columns)
        + ") LIMIT 1)",
        {**params, **bound},
    )[0][0]:
        return True
    states, bound = captures.proposal(UNIT_STATE)
    if writer.store.execute(
        f"SELECT count() FROM (SELECT 1 FROM ({states}) WHERE (level, unit_id) "
        f"NOT IN (SELECT level, unit_id FROM ({fields})) LIMIT 1)",
        {**params, **bound},
    )[0][0]:
        return True
    classified, blocked = _classify_recovery(
        writer,
        ledger,
        fields,
        params,
        cutoff,
        spans=any(d.level == "span" for d in definitions),
        strict=False,
    )
    return blocked or _owner_entries(classified) != _owner_entries(ledger)


def retirement_plan(writer, draft, head, definitions, cutoff, *, captures=None):
    """Convert proposed anchor changes to one complete privacy checkpoint.

    Old and new anchors are considered BEFORE publication. Expiring proposals
    contribute anonymous pre-fold parts at their NEW anchor, and every former
    bucket is rebuilt without the old field. No reader can observe an interim
    expired field or both its live and retired contribution. The draft is PG-only
    and its private bytes are purged with the predecessor revisions after publish.
    The draft overlay and the retained-owner set are captured once per pass;
    the committed head side of the proposal stays inline (never a copy).
    """
    if captures is None:
        with ProposalCaptures(writer, draft, head) as owned:
            return _retirement_plan(writer, draft, head, definitions, cutoff, owned)
    return _retirement_plan(writer, draft, head, definitions, cutoff, captures)


class _PolicyWriter:
    """A writer seen through an explicit retention policy (expire_project)."""

    def __init__(self, writer, retention_days):
        self.lease, self.retention_days = writer.lease, retention_days


def _published_live_expiry(writer, head, cutoff, span_dirty):
    """Whether this expiry retires LIVE unit evidence a previous head published.

    Only that creates the R02 admission ambiguity (TH-8975): a unit whose
    committed evidence was retired could later reappear looking new. A first
    publication has no published evidence; tombstones carry no contribution;
    owner-link, current-fact and payload scrubs retire no unit.
    """
    if head is None:
        return False
    if span_dirty:
        return True
    fields, params = committed_rows_select(
        FIELD_VALUES, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    return bool(
        writer.store.execute(
            f"SELECT count() FROM ({fields}) WHERE is_deleted = 0 "
            "AND unit_start < toDateTime64(%(live_expiry_cutoff)s, 6, 'UTC')",
            {**params, "live_expiry_cutoff": cutoff},
        )[0][0]
    )


def _floor_ceilings(writer, head, cutoff, span_dirty):
    """The retention floor (stamped exactly as before) and its admission terms.

    retention_retired_live: whether any purge so far retired live published
    unit evidence. Only then can a unit without anchor proof be retired
    evidence reappearing, so only then does admission require proof.

    retention_floor_at: a span created after it cannot be evidence a purge
    retired. A purge retires only what its head captured, and every capture
    reads created_at <= its as_of, so this is the purged head's capture
    high-water less discovery's late-arrival OVERLAP (a span created just
    before a capture but inserted after it was not captured). None when that
    high-water is unknown: then nothing is exempt.
    """
    from tracer.services.derived_metrics.changes import OVERLAP
    from tracer.services.derived_metrics.refresh import captured_through

    inherited = head.published_by.source_ceilings if head is not None else {}
    prior = inherited.get(
        "retention_retired_live", bool(inherited.get("retention_floor"))
    )
    # When this logical expiry ran, under which policy: physical_cutoff holds
    # its floor for one METRICS_EXPIRY_INTERVAL_SECONDS before expiring again,
    # unless the policy is shortened meanwhile.
    ceilings = {
        "retention_floor": cutoff,
        "retention_floor_set_at": writer.lease.clock().isoformat(),
        "retention_floor_days": writer.retention_days()
        if writer.retention_days is not None
        else None,
    }
    if not _published_live_expiry(writer, head, cutoff, span_dirty):
        ceilings["retention_retired_live"] = bool(prior)
        ceilings["retention_floor_at"] = inherited.get("retention_floor_at")
        return ceilings
    captured = captured_through(inherited)
    ceilings["retention_retired_live"] = True
    ceilings["retention_floor_at"] = (
        None if captured is None else (captured - OVERLAP).isoformat()
    )
    return ceilings


def _retirement_plan(writer, draft, head, definitions, cutoff, captures):
    from tracer.services.derived_metrics.span_evidence import (
        require_retirement_evidence,
    )
    from tracer.services.derived_metrics.unit_reads import field_recipe

    fields, params = captures.fields()
    params = dict(params)
    require_retirement_evidence(
        writer,
        head,
        definitions,
        draft=draft,
        cutoff=cutoff,
        proposed=(fields, dict(params)),
    )
    params["retire_cutoff"] = cutoff
    expired = (
        f"SELECT DISTINCT level, unit_id FROM ({fields}) "
        "WHERE unit_start < toDateTime64(%(retire_cutoff)s, 6, 'UTC')"
    )
    span_definitions = [d for d in definitions if d.level == "span"]
    span_dirty = (
        _expired_span_buckets(writer, head, cutoff) if span_definitions else set()
    )
    current_cleanup = _current_expiry_required(
        writer,
        head,
        fields,
        params,
        draft=draft,
        cutoff_parameter="retire_cutoff",
        captures=captures,
    )
    logical_expiry = bool(
        span_dirty
        or _current_expiry_required(
            writer,
            head,
            fields,
            params,
            cutoff_parameter="retire_cutoff",
            captures=captures,
        )
        or writer.store.execute(f"SELECT count() FROM ({expired})", params)[0][0]
        or (
            span_definitions
            and _span_owner_cleanup_required(
                writer, head, fields, params, cutoff, captures.owners(cutoff)
            )
        )
    )
    draft_ledger = {}
    if span_definitions and not logical_expiry and not current_cleanup:
        for chunk in draft.ledger_chunks.exclude(part="cleanup_intent"):
            draft_ledger.setdefault(chunk.part, []).extend(chunk.payload)
    if (
        not logical_expiry
        and not current_cleanup
        and not (
            span_definitions
            and _span_proposal_cleanup_required(
                writer, captures, fields, params, cutoff, draft_ledger, definitions
            )
        )
    ):
        # Provably an identity transform: no expired unit, no current-fact or
        # lifecycle cleanup, and (with span recipes) no proposed owner link or
        # private preimage the span checkpoint would drop or blank. Publishing
        # the draft itself is then byte-for-byte the same retained state; its
        # consumed ledger is purged after publication exactly like a successor's.
        overlay, overlay_params = captures.overlay(FIELD_VALUES)
        for definition in definitions:
            if definition.level != "user":
                writer._freeze_buckets(draft, head, definition, overlay, overlay_params)
        return draft
    # Span checkpoints also scrub unproven conversation/user owner links from
    # retained span dependencies and PG preimages. Without an actual privacy
    # transition this is an ordinary checkpoint: prior safe pins stay usable.
    # Span retirement uses the same frozen proposal as evidence and totals.
    # Re-reading raw spans here could retire a newer value than the pinned KPI.
    if span_dirty:
        writer.append(
            draft,
            "dirty_before",
            [{"level": "span", "bucket_start": value} for value in sorted(span_dirty)],
        )
    owners = captures.owners(cutoff)
    retained = (
        _retained_fields(
            fields, f"(level, unit_id) NOT IN ({expired})", "retire_cutoff", owners
        )
        if span_definitions
        else f"SELECT * FROM ({fields}) WHERE (level, unit_id) NOT IN ({expired})"
    )
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
    # Native reads stay outside the lifecycle lock below.
    floor_terms = (
        _floor_ceilings(writer, head, cutoff, span_dirty) if logical_expiry else {}
    )
    with serialized_project(writer.lease.project_id):
        # Classification copied private bytes without a lifecycle lock. A
        # revocation need not change the lease or the head id. Recheck both the
        # base and draft before registering ANY durable successor/private chunks.
        current = MetricProjectRefresh.for_project(
            writer.lease.project_id
        ).head_manifest
        if (current.id if current else None) != (head.id if head else None) or (
            current is not None and current.revoked_at is not None
        ):
            raise MetricStateConflict("retention base changed or expired")
        MetricRefreshAttempt._locked_open(
            draft.id, state=MetricRefreshAttempt.State.PREPARED, **writer.lease.check()
        )
        _require_retention_definitions(writer, definitions)
        # Registration and recovery transfer commit together. A subsequent
        # revocation now sees and aborts this successor before it can append.
        attempt = writer.begin(
            purpose="compaction",
            ceilings={
                **draft.source_ceilings,
                **floor_terms,
                "logical_expiry": logical_expiry,
                # GC must scrub consumed PG-only drafts even when safe pins stay.
                "private_cleanup_through": draft.generation,
            },
            versions=draft.metric_versions,
            ledger=ledger,
        )
    writer.freeze(
        attempt,
        FIELD_VALUES,
        retained,
        params,
        collect_payloads=False,
        row_limit=MAX_COMPACTION_ROWS,
    )
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
        formula = field_recipe(definition).formula
        if formula is not None:
            p.update(formula.parameters)
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
    writer.freeze(
        attempt,
        RETIRED_PARTS,
        untouched,
        params,
        collect_payloads=False,
        row_limit=MAX_COMPACTION_ROWS,
    )
    for table in (UNIT_STATE, SOURCE_DEPENDENCIES):
        sql, bound = captures.proposal(table)
        # Retained conversation rows are exactly the captured owner set: a
        # conversation not in expired has no anchor before the cutoff.
        predicate = (
            f"(level, unit_id) IN (SELECT level, unit_id FROM ({retained}))"
            if table == UNIT_STATE
            else f"canonical_session_id IN ({owners})"
        )
        selected = f"SELECT * FROM ({sql}) WHERE {predicate}"
        if table == SOURCE_DEPENDENCIES and any(d.level == "span" for d in definitions):
            selected = _span_dependencies(sql, predicate, "retire_cutoff")
        writer.freeze(
            attempt,
            table,
            selected,
            {**params, **bound},
            collect_payloads=False,
            row_limit=MAX_COMPACTION_ROWS,
        )
    # Pruning a PREPARED auxiliary proposal does not revoke unrelated retained
    # captures: only previously published private evidence sets logical_expiry.
    for table in _current_tables(writer, head, draft):
        sql, bound = captures.proposal(table)
        writer.freeze(
            attempt,
            table,
            f"SELECT * FROM ({sql}) WHERE "
            f"{_current_retained(fields, 'retire_cutoff', owners)}",
            {**params, **bound},
            collect_payloads=False,
            row_limit=MAX_COMPACTION_ROWS,
        )
    # Carry anonymous history plus this pass's direct span replacements.
    buckets, bp = captures.proposal(BUCKET_TOTALS)
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
    writer.freeze(
        attempt,
        BUCKET_TOTALS,
        buckets,
        bp,
        collect_payloads=False,
        row_limit=MAX_COMPACTION_ROWS,
    )
    # Exactly the successor's frozen live fields and anonymous parts, captured
    # once (one batch per INSERT) rather than inlined into every bucket page.
    live, lp = captures.overlay(FIELD_VALUES, attempt=attempt)
    new_retired, nrp = captures.overlay(RETIRED_PARTS, attempt=attempt)
    for definition in definitions:
        if definition.level != "user":
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


def finish_pending_expiry(writer, *, now=None, expiry_id=None):
    """Drain obligations independently of ledger/manifest GC and horizon changes.

    A successful early DELETE is NOT a completion receipt. Unknown submission
    outcomes remain pending, even after a timeout, lost lease or server restart.
    Once every submission has a synchronous response, a SECOND verified scrub is
    mandatory. A crash before its durable receipt safely repeats that scrub.
    """
    from tracer.models.derived_metric import MetricErasureRequest

    project_id = writer.lease.project_id
    pending = MetricErasureRequest.objects.filter(
        project_key=project_id, store_payload__protocol="metric_expiry"
    ).exclude(status="done")
    if expiry_id is not None:
        if connection.in_atomic_block:
            raise MetricStateConflict(
                "physical purge requires an outer commit boundary"
            )
        pending = pending.filter(
            id=expiry_id,
            organization_key=writer.org_id,
            scope_kind="history",
            status__in=("queued", "failed"),
            completed_at__isnull=True,
        )
    with serialized_project(project_id):
        writer.lease.check()
        state = MetricProjectRefresh.objects.get(project_id=project_id)
        head = state.head_manifest
        if head is None or head.revoked_at:
            raise MetricStateConflict("retention cleanup requires a safe checkpoint")
        if expiry_id is not None:
            intent = pending.first()
            through = intent.store_payload.get("through_generation") if intent else None
            if (
                intent is None
                or type(through) is not int
                or not 0 <= through <= state.generation_high_water
                or intent.deletion_key
                != f"expiry:{intent.store_payload.get('checkpoint_attempt')}"
            ):
                raise MetricStateConflict(
                    "cleanup requires a durable expiry obligation"
                )
        boundaries = [
            payload["through_generation"]
            for payload in pending.values_list("store_payload", flat=True)
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
        obligations = list(pending.order_by("requested_at"))
    # Safe roots were frozen under serialization. New attempts have generations
    # above every obligation; new captures can only pin these roots or their
    # descendants. Revoked old namespaces cannot register new submissions.
    writer.store.assert_visible(head)
    shard = writer.store.identity()
    complete = True
    for obligation in obligations:
        # Recovery can enter here without purge_checkpoint, including a
        # crash immediately after logical publication. Attempt TTL is not
        # permission to retain expired PG preimages or frozen replay bytes.
        recovery_attempt = (
            head.published_by
            if head.published_by.source_ceilings.get("recovery_pending")
            else None
        )
        through = obligation.store_payload["through_generation"]
        with serialized_project(project_id):
            writer.lease.check()
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
        # Preserve the existing core receipt shape; current-list stores get
        # their own verification count under the same quiescence barrier.
        core_table_count = len(tables)
        tables = (*tables, *LIST_CURRENT_TABLES)
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
        # Logical publication already aborted old writers under the lifecycle
        # key. Their dispatch registration revalidates that state, so no new old-
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
                    "verified_tables": core_table_count,
                    "remaining": 0,
                    "private_payloads_remaining": 0,
                },
                "current_facts_scrub": {
                    "verified_tables": len(LIST_CURRENT_TABLES),
                    "remaining": 0,
                },
            },
        )
        # Omission debts are discharged only by this verified all-revisions
        # scrub, never just by losing their last row in the current head.
        MetricErasureRequest.objects.filter(
            project_key=project_id,
            store_payload__protocol="metric_omission",
            store_payload__through_generation__lte=through,
        ).exclude(status="done").update(
            status="done",
            completed_at=now or writer.lease.clock(),
            receipts={"expiry_scrub": {"through_generation": through}},
        )
        # Namespace receipts have served their proof; no private rows or
        # unbounded per-write history is required after completion.
        submissions.delete()
    with serialized_project(project_id):
        writer.lease.check()
        replan = (
            MetricErasureRequest.objects.filter(
                project_key=project_id, store_payload__protocol="metric_policy_replan"
            )
            .exclude(status="done")
            .exists()
        )
        remaining = (
            MetricErasureRequest.objects.filter(
                project_key=project_id, store_payload__protocol="metric_expiry"
            )
            .exclude(status="done")
            .exists()
        )
        MetricProjectRefresh.objects.filter(project_id=project_id).update(
            pending_expiry=not complete or replan or remaining
        )
        return complete and not replan and not remaining


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


def _span_owner_cleanup_required(
    writer, head, fields, field_params, cutoff, owners=None
):
    """A retained span does not prove retention of its private owner links."""
    inline_owners = (
        f"SELECT unit_id FROM ({fields}) WHERE level = 'conversation' "
        "GROUP BY unit_id HAVING min(unit_start) >= %(span_owner_cutoff)s"
    )
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
        or writer.store.execute(
            f"SELECT count() FROM ({fields}) WHERE level = 'span' "
            "AND (notEmpty(session_id) OR notEmpty(user_id)) "
            # A pass's captured owner set IS this relation (exact cutoff):
            # one collapse of the proposal per statement, not two.
            f"AND session_id NOT IN ({owners or inline_owners})",
            {**field_params, "span_owner_cutoff": cutoff},
        )[0][0]
    )


def _expired_span_buckets(writer, head, cutoff):
    """Private span membership, not the mere presence of a span definition."""
    from tracer.services.derived_metrics.bounded_reads import windowed_rows_select

    dependencies, params = windowed_rows_select(
        SOURCE_DEPENDENCIES,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
        predicate="unit_start < toDateTime64(%(span_expiry_cutoff)s, 6, 'UTC')",
        params={"span_expiry_cutoff": cutoff},
        param_prefix="expired_span_dependencies",
    )
    return {
        row[0]
        for row in writer.store.execute(
            f"SELECT DISTINCT toStartOfFifteenMinutes(unit_start) FROM ({dependencies}) "
            "WHERE unit_start < toDateTime64(%(span_expiry_cutoff)s, 6, 'UTC')",
            {**params, "span_expiry_cutoff": cutoff},
        )
    }


def _expired_physical_payload(writer, head, cutoff):
    """Head omission/compaction must not hide an all-revisions privacy deadline.

    Detect only; deletion still goes through the normal safe replacement,
    revocation, retained-recovery handoff and write-quiescence protocol below.
    Never use an audit attempt TTL as a grace period for private evidence.
    Only namespaces the head does NOT reach are probed: head rows are judged by
    the logical-unit rule above, and rows it deliberately keeps (for example a
    deleted earliest member of a retained conversation) must not re-trigger
    expiry on every call. Unregistered late writes are reaped by scrub_orphans.
    """
    reachable = {a for _, a in head.allowed_attempts()}
    others = tuple(
        MetricRefreshAttempt.objects.filter(project_id=writer.lease.project_id)
        .exclude(id__in=reachable)
        .values_list("id", flat=True)
    )
    if not others:
        return False
    # Lifecycle rows have no unit_start; their owners are nominated by field
    # and dependency anchors, then scrubbed in the same expiry checkpoint.
    for table in (
        FIELD_VALUES,
        TABLES["metric_field_values_checkpoint"],
        SOURCE_DEPENDENCIES,
        *LIST_CURRENT_TABLES,
    ):
        if writer.store.execute(
            f"SELECT count() FROM (SELECT 1 FROM {table.name} PREWHERE org_id = toUUID(%(expiry_org)s) "
            "AND project_id = toUUID(%(expiry_project)s) "
            "WHERE attempt_id IN %(expiry_unreachable)s "
            "AND unit_start < toDateTime64(%(expiry_physical_cutoff)s, 6, 'UTC') LIMIT 1)",
            {
                "expiry_org": str(writer.org_id),
                "expiry_project": str(writer.lease.project_id),
                "expiry_unreachable": others,
                "expiry_physical_cutoff": cutoff,
            },
        )[0][0]:
            return True
    return False


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
    if retention_cutoff(retention_days(), now=now) is None:
        return {"unlimited": True, "purged": False}
    # Physical expiry is batched (expiry_interval); reads stay exact.
    cutoff = physical_cutoff(_PolicyWriter(writer, retention_days), now=now)
    project_id = writer.lease.project_id
    with serialized_project(project_id):
        writer.lease.check()
        from tracer.services.derived_metrics.backfill import scrub_expired_progress

        scrub_expired_progress(project_id, cutoff=cutoff, now=now)
        head = MetricProjectRefresh.for_project(project_id).head_manifest
        if head is None:
            return {"unlimited": False, "purged": False}
        if head.revoked_at:
            raise MetricStateConflict("expired base requires source rebuild")
        # Classification below performs ClickHouse reads without a PG
        # transaction. Keep predecessors open (and therefore GC-rooted) until
        # their complete, policy-safe handoff is durable in the successor.
        predecessors = list(
            MetricRefreshAttempt.objects.filter(
                project_id=project_id,
                state__in=MetricRefreshAttempt.OPEN_STATES,
                fencing_epoch__lte=writer.lease.grant.fencing_epoch,
            ).values_list("id", flat=True)
        )
        recovery = writer.abort_stale(abort=False)
    writer.store.assert_visible(head)
    from tracer.services.derived_metrics.span_evidence import (
        require_retirement_evidence,
    )

    require_retirement_evidence(writer, head, definitions, cutoff=cutoff)
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
        and not writer.store.execute(f"SELECT count() FROM ({expired_units})", params)[
            0
        ][0]
        and not _current_expiry_required(
            writer, head, fields, params, cutoff_parameter="expiry_cutoff"
        )
        and not _expired_physical_payload(writer, head, cutoff)
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
    # Native reads stay outside the lifecycle lock below.
    floor_terms = _floor_ceilings(writer, head, cutoff, span_dirty)
    with serialized_project(project_id):
        # Classification ran without the lifecycle lock. Revocation need not
        # change the head id or lease when there were no open predecessors, so
        # neither begin() nor its publication CAS can validate this copied base.
        current = MetricProjectRefresh.for_project(project_id).head_manifest
        if current is None or current.id != head.id or current.revoked_at is not None:
            raise MetricStateConflict("retention base changed or expired")
        _require_retention_definitions(writer, definitions)
        # Register the successor under the same lock as these fresh checks:
        # subsequent revocations must abort it before any copy can publish.
        attempt = writer.begin(
            purpose="compaction",
            ceilings={
                "retention_cutoff": cutoff,
                **floor_terms,
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
        # No ClickHouse work inside this short atomic handoff. An interrupted
        # append rolls it all back, leaving every predecessor and its ledger
        # available to the next fenced writer.
        for predecessor in predecessors:
            MetricRefreshAttempt.abort(
                predecessor, reason="retention_replan", **writer.lease.check()
            )
        # Yielded passes' ledgers are in this checkpoint's handoff too.
        writer.mark_yields_carried()
    if span_definitions:
        writer.freeze(
            attempt,
            FIELD_VALUES,
            _retained_fields(
                fields,
                f"(level, unit_id) NOT IN ({expired_units})",
                "expiry_cutoff",
            ),
            params,
            collect_payloads=False,
            row_limit=MAX_COMPACTION_ROWS,
        )
    else:
        writer.freeze_current(
            attempt,
            FIELD_VALUES,
            head,
            where=f"(level, unit_id) NOT IN ({expired_units})",
            params=params,
            large_pages=True,
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
        from tracer.services.derived_metrics.unit_reads import field_recipe

        formula = field_recipe(definition).formula
        if formula is not None:
            p.update(formula.parameters)
        if definition.level == "span":
            span_dirty.update(
                row[0]
                for row in writer.store.execute(
                    f"SELECT DISTINCT bucket_start FROM ({expiring})", p
                )
            )
            writer.append(
                attempt,
                "dirty_before",
                [
                    {"level": "span", "bucket_start": value}
                    for value in sorted(span_dirty)
                ],
            )
        from tracer.services.derived_metrics.span_evidence import (
            retiring_span_parts,
        )

        capture = (
            retiring_span_parts(
                writer,
                attempt,
                head,
                definition,
                expiring,
                p,
                cutoff=cutoff,
                as_of=as_of or now,
            )
            if definition.level == "span"
            else nullcontext((live_parts_select(definition, expiring), p))
        )
        with capture as (live, p):
            if definition.level == "span":
                span_dirty.update(
                    row[0]
                    for row in writer.store.execute(
                        f"SELECT DISTINCT bucket_start FROM ({live})", p
                    )
                )
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
        raise MetricStateConflict("retirement requires every stored field definition")
    untouched = f"SELECT * FROM ({retired})"
    if supplied:
        untouched += " WHERE (metric_id, metric_version) NOT IN %(expiry_handled)s"
        params["expiry_handled"] = tuple((UUID(m), v) for m, v in sorted(supplied))
    writer.freeze(
        attempt,
        RETIRED_PARTS,
        untouched,
        params,
        collect_payloads=False,
        row_limit=MAX_COMPACTION_ROWS,
    )
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
        if table == SOURCE_DEPENDENCIES and any(d.level == "span" for d in definitions):
            sql, bound = committed_rows_select(
                table, head, org_id=writer.org_id, project_id=project_id
            )
            writer.freeze(
                attempt,
                table,
                _span_dependencies(sql, where, "expiry_cutoff"),
                {**params, **bound},
                collect_payloads=False,
                row_limit=MAX_COMPACTION_ROWS,
            )
        else:
            writer.freeze_current(
                attempt, table, head, where=where, params=params, large_pages=True
            )
    for table in _current_tables(writer, head):
        writer.freeze_current(
            attempt,
            table,
            head,
            where=_current_retained(fields, "expiry_cutoff"),
            params=params,
            large_pages=True,
        )
    if span_definitions:
        # The checkpoint's complete live fields and anonymous parts, captured
        # once (one frozen batch per INSERT), never inlined into bucket pages.
        with ProposalCaptures(writer, attempt, head) as captures:
            new_live, nlp = captures.overlay(FIELD_VALUES, attempt=attempt)
            new_retired, nrp = captures.overlay(RETIRED_PARTS, attempt=attempt)
            for definition in span_definitions:
                writer._freeze_buckets(
                    attempt,
                    head,
                    definition,
                    new_live,
                    nlp,
                    complete_fields=True,
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
            "metric_checkpoint_layout",
            "metric_policy_replan",
            "metric_omission",
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
            attempt = writer.begin(
                purpose="compaction", ceilings={"erasure": str(request.id)}
            )
        store.assert_visible(head)
        ids = tuple(request.subject_ids)
        params["erase_subjects"] = ids
        field_predicate = (
            "user_id IN %(erase_subjects)s"
            if kind == "users"
            else "(session_id IN %(erase_subjects)s OR unit_id IN %(erase_subjects)s)"
        )
        # Retention can scrub an unproven span-owner link while retaining
        # the younger span. Without that lineage, subject erasure must drop
        # unknown-owner span evidence conservatively, never guess it is safe.
        owner = "user_id" if kind == "users" else "session_id"
        field_predicate = f"({field_predicate} OR (level = 'span' AND {owner} = ''))"
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
            f"SELECT DISTINCT unit_id FROM ({fields}) WHERE {field_predicate} "
            f"UNION DISTINCT SELECT canonical_session_id FROM ({deps}) WHERE {dep_predicate} "
            f"UNION DISTINCT SELECT toJSONString(tuple(trace_id, span_id)) FROM ({deps}) WHERE {dep_predicate}",
            params,
        )
        for table in LIST_CURRENT_TABLES:
            facts, bound = committed_rows_select(
                table,
                head,
                org_id=request.organization_key,
                project_id=request.project_key,
            )
            victims.extend(
                store.execute(
                    f"SELECT DISTINCT canonical_session_id FROM ({facts}) WHERE {dep_predicate}",
                    {**params, **bound},
                )
            )
        params["erase_units"] = tuple(row[0] for row in victims) or ("",)
        writer.freeze(
            attempt,
            FIELD_VALUES,
            f"SELECT * FROM ({fields}) WHERE NOT ({field_predicate}) AND unit_id NOT IN %(erase_units)s",
            params,
            collect_payloads=False,
            row_limit=MAX_COMPACTION_ROWS,
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
                collect_payloads=False,
                row_limit=MAX_COMPACTION_ROWS,
            )
        for table in LIST_CURRENT_TABLES:
            writer.freeze_current(
                attempt,
                table,
                head,
                where=f"NOT ({dep_predicate}) AND canonical_session_id NOT IN %(erase_units)s",
                params=params,
                large_pages=True,
            )
        writer.seal(attempt)
        manifest = writer.stage_and_publish(attempt)
        with serialized_project(request.project_key):
            old = list(
                MetricReadManifest.objects.filter(project_id=request.project_key)
                .exclude(id=manifest.id)
                .values_list("id", flat=True)
            )
            writer.lease.check()
            current = MetricProjectRefresh.for_project(
                request.project_key
            ).head_manifest
            if current is None or current.id != manifest.id or current.revoked_at:
                raise MetricStateConflict("subject erasure checkpoint changed")
            revoke_generations(request.project_key, reason="erased", manifest_ids=old)
        writer.lease.check()
        for table in TABLES.values():
            if table.name.startswith("metric_field_values") or table in (
                SOURCE_DEPENDENCIES,
                UNIT_STATE,
                *LIST_CURRENT_TABLES,
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
                base + " AND generation <= %(erase_through)s AND (" + predicate + ")",
                {
                    **params,
                    "erase_new": str(attempt.id),
                    "erase_through": attempt.generation,
                },
            )
        with serialized_project(request.project_key):
            writer.lease.check()
            _purge_terminal_payloads(
                request.project_key, through_generation=attempt.generation
            )
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
                # Discovery happens BEFORE an attempt exists and may already
                # hold private dependency preimages in memory. Abort/revoke alone
                # cannot fence that writer (including a project with NO head).
                # Invalidate its durable lease in this same lifecycle boundary;
                # registration/append require the old epoch+token and must fail.
                # A later worker takes a new epoch and rediscovers current source.
                MetricProjectRefresh.objects.filter(project_id=project).update(
                    lease_token=None, lease_expires_at=None, lease_holder=""
                )
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
