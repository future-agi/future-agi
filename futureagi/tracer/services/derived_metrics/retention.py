"""Source-retained live-store checkpoints and physical purge.

Every retention and erasure checkpoint rewrites the whole retained head, so
its bulk copies use checkpoint-sized pages (MAX_COMPACTION_ROWS, 4 MiB), as
compaction does: 500-row pages re-ran the head's collapse once per page
(364 us a row at 20k rows a table, 528 at 100k; 71-76 with checkpoint pages).

Retention is based on unit_start. Expired statistics and identifying group labels
are discarded, never converted into indefinitely surviving retired history.
Frozen results use the same source policy (source_retention.py).

Callers schedule expiry independently of arrivals and retry failed mutations.
Purge planning uses the same short serialization as publication/pin capture;
native mutations use immutable, generation-bounded namespaces and write receipts.
The original manifest is revoked before any physical deletion. Audit hashes are
never rewritten; terminal private replay/preimage chunks can be deleted whole.
"""

import json
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from functools import wraps
from itertools import groupby
from operator import itemgetter
from uuid import UUID

from django.db import connection
from django.db.models import Q
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
from tracer.models.project import Project
from tracer.services.derived_metrics.bounded_reads import (
    committed_branches,
    merge_bindings,
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
    MetricTable,
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
    """Batch scheduling cadence, NOT a physical-deletion completion SLA.

    Reads enforce the exact cutoff. Failed/outstanding cleanup remains durable
    and incomplete until its write barrier and final scrub have been verified.
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


def _verified_delete(store, table, predicate, params):
    _delete(store, table, predicate, params)
    if store.execute(f"SELECT count() FROM {table.name} WHERE {predicate}", params) != [
        (0,)
    ]:
        raise MetricStateConflict("physical scrub is incomplete")


def _frozen_batch_sources(writer, attempt, table):
    from tracer.services.derived_metrics.publication import FrozenRows
    from tracer.services.derived_metrics.refresh import TABLE_KIND, payload_select

    for number, plan in enumerate(writer.plans(attempt, kind=TABLE_KIND[table.name])):
        yield payload_select(
            table, [FrozenRows.from_plan(plan)], prefix=f"capture_{number}"
        )


_RECIPE_QUARTER_SCHEMA = (
    ("field_id", "UUID"),
    ("field_version", "UInt32"),
    ("bucket_start", "DateTime('UTC')"),
)


class ProposalCaptures:
    """Pass-scoped session captures of one PREPARED proposal (SessionCapture).

    Only BOUNDED inputs are materialized: a draft's frozen batches (one batch
    per INSERT) and the retained-owner set. The committed head side of every
    proposal stays inline over immutable allowlisted attempts: it is the
    project's whole retained history, so copying it would
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
        attempt = attempt or self.draft
        capture = self._capture(
            ("overlay", str(attempt.id), table.name),
            table.schema,
            lambda: _frozen_batch_sources(self.writer, attempt, table),
            "overlay",
        )
        return capture.select, {}

    def former_span_quarters(self, definitions):
        """Fold-only recipe/quarter keys, derived afresh on retries, never source work."""
        from tracer.services.derived_metrics.bounded_reads import KeyRelation

        if not self.head or not any(d.level == "span" for d in definitions):
            return None
        overlay, bound = self.overlay(FIELD_VALUES)
        emitted = f"SELECT * FROM ({overlay}) WHERE level = 'span' AND is_deleted = 0"
        keys = FIELD_VALUES.logical_key
        previous, params = committed_rows_select(
            FIELD_VALUES,
            self.head,
            org_id=self.writer.org_id,
            project_id=self.writer.lease.project_id,
            key_relation=KeyRelation(
                keys, f"SELECT {', '.join(keys)} FROM ({emitted})", bound
            ),
            param_prefix="span_emitted_before",
        )
        # At most one former key per emitted identity. Keep it relational for
        # checkpoint key paging; never cross these starts with all recipes.
        columns = ", ".join(name for name, _ in _RECIPE_QUARTER_SCHEMA)
        sql = (
            f"SELECT DISTINCT {columns} FROM ({previous}) WHERE is_deleted = 0 "
            f"AND ({columns}) NOT IN (SELECT {columns} FROM ({emitted}))"
        )
        capture = self._capture(
            "former_span_quarters",
            _RECIPE_QUARTER_SCHEMA,
            lambda: [(sql, params)],
            "former_span_quarters",
        )
        return capture.select if capture.digest[0] else None

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

    def span_owner_fields(self):
        """One winner per span key across committed revisions and the frozen draft."""
        keys = FIELD_VALUES.logical_key
        version = FIELD_VALUES.version_column
        columns = (*keys, "generation", version, "session_id", "user_id")
        projection = ", ".join(columns)
        branches, params = committed_branches(
            FIELD_VALUES,
            self.head,
            org_id=self.writer.org_id,
            project_id=self.writer.lease.project_id,
            columns=columns,
            key_in={"level": ("span",)},
            param_prefix="span_owner_fields",
        )
        overlay, bound = self.overlay(FIELD_VALUES)
        # Level is part of the key; a newer empty link or retraction must
        # still compete with every older linked row for that key.
        branches.append(f"SELECT {projection} FROM ({overlay}) WHERE level = 'span'")
        return (
            f"SELECT {projection} FROM ({' UNION ALL '.join(branches)}) AS span_owner_revisions "
            f"ORDER BY generation DESC, {version} DESC LIMIT 1 BY {', '.join(keys)}",
            merge_bindings(params, bound),
        )

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
    result: dict[str, list] = {}
    blocked = False
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
                        result[part].append(
                            {
                                key: value
                                for key, value in entry.items()
                                if key in {"bucket_start", "level"}
                            }
                        )
                elif anchor is None and strict:
                    # Unknown is not retained. Persist only the blocked-work bit.
                    blocked = True
                elif "bucket_start" in entry:
                    result[part].append(
                        {
                            k: v
                            for k, v in entry.items()
                            if k in {"bucket_start", "level"}
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


def _field_expiry(writer, stack, fields, params, parameter, expired_units, *, spans):
    """The immutable removal predicate shared by fields, state and totals.

    A conversation's oldest member is still its anchor. A span, however, has
    one versioned source row: two recipe versions may have observed opposite
    sides of a start correction. Consult the terminal source revision ONLY for
    those disputed identities. Absence, deletion and an expired winner prove
    no exemption. A live winner never rescues an expired *field* anchor.
    """
    unit_expired = f"(level, unit_id) IN ({expired_units})"
    if not spans:
        return unit_expired, False
    from tracer.services.derived_metrics.frozen_relations import (
        CAPTURE_BYTES,
        CAPTURE_ROWS,
    )
    from tracer.services.derived_metrics.unit_reads import source_identity_relation

    cutoff = f"toDateTime64(%({parameter})s, 6, 'UTC')"
    disputed = (
        f"SELECT unit_id FROM ({fields}) WHERE level = 'span' GROUP BY unit_id "
        f"HAVING min(unit_start) < {cutoff} AND max(unit_start) >= {cutoff}"
    )
    if not writer.store.execute(f"SELECT 1 FROM ({disputed}) LIMIT 1", params):
        return unit_expired, False
    # One bounded read of mutable source metadata (no value/label payload).
    # Reconnects replay the frozen IDs, never reclassify between copy and fold.
    winners = (
        "SELECT trace_id, id, argMax(tuple(start_time, is_deleted), _version) AS winner "
        "FROM spans PREWHERE project_id = toUUID(%(disputed_project)s) AND (trace_id, id) IN "
        f"(SELECT JSONExtractString(unit_id, 1), JSONExtractString(unit_id, 2) FROM ({disputed})) "
        "WHERE org_id = toUUID(%(disputed_org)s) OR isNull(org_id) GROUP BY trace_id, id "
        f"HAVING winner.2 = 0 AND winner.1 >= {cutoff}"
    )
    pairs = writer.store.execute(
        "SELECT trace_id, id FROM (SELECT trace_id, id, count() OVER () AS n, "
        "sum(length(formatRow('RowBinary', trace_id, id))) OVER () AS bytes "
        f"FROM ({winners} LIMIT {CAPTURE_ROWS + 1})) "
        f"WHERE throwIf(n > {CAPTURE_ROWS} OR bytes > {CAPTURE_BYTES}, 'metric_span_identity_budget') = 0",
        {
            **params,
            "disputed_project": str(writer.lease.project_id),
            "disputed_org": str(writer.org_id),
        },
    )
    if not pairs:
        return unit_expired, True
    proven = stack.enter_context(source_identity_relation(writer.store, pairs))
    return (
        f"({unit_expired} AND (level != 'span' OR unit_start < {cutoff} OR unit_id NOT IN "
        f"(SELECT toJSONString(tuple(trace_id, span_id)) FROM ({proven.select}))))",
        True,
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


def _close_failed_attempts(function):
    @wraps(function)
    def guarded(writer, *args, **kwargs):
        prior = set(
            MetricRefreshAttempt.objects.filter(
                project_id=writer.lease.project_id,
                state__in=MetricRefreshAttempt.OPEN_STATES,
            ).values_list("id", flat=True)
        )
        if function.__name__ == "retirement_plan":
            # This PG-only draft belongs to this invocation too; closing only
            # the successor leaves the failed original PREPARED indefinitely.
            draft = args[0] if args else kwargs["draft"]
            prior.discard(draft.id)
        try:
            return function(writer, *args, **kwargs)
        except Exception:
            from tracer.services.derived_metrics.refresh import PASS_YIELD

            # The next fenced writer carries this ledger, but no failed PREPARED
            # attempt remains eligible for publication or pins private state.
            for attempt_id in (
                MetricRefreshAttempt.objects.filter(
                    project_id=writer.lease.project_id,
                    fencing_epoch=writer.lease.grant.fencing_epoch,
                    state__in=MetricRefreshAttempt.OPEN_STATES,
                )
                .exclude(id__in=prior)
                .values_list("id", flat=True)
            ):
                MetricRefreshAttempt.abort(
                    attempt_id, reason=PASS_YIELD, **writer.lease.check()
                )
            raise

    return guarded


def _expired_aggregates(writer, head, cutoff):
    if head is None:
        return False
    for table in (RETIRED_PARTS, BUCKET_TOTALS):
        sql, params = committed_rows_select(
            table, head, org_id=writer.org_id, project_id=writer.lease.project_id
        )
        where = (
            "1"
            if table == RETIRED_PARTS
            else "bucket_start < toDateTime64(%(aggregate_cutoff)s, 6, 'UTC')"
        )
        if writer.store.execute(
            f"SELECT count() FROM ({sql}) WHERE {where}",
            {**params, "aggregate_cutoff": cutoff},
        )[0][0]:
            return True
    return False


@_close_failed_attempts
def retirement_plan(writer, draft, head, definitions, cutoff, *, captures=None):
    """Convert proposed anchor changes to one complete privacy checkpoint.

    Old and new anchors are considered BEFORE publication. Expired fields and
    their contributions are dropped together; a wholly retained quarter that
    loses a contributor is rebuilt from the surviving fields. The draft is PG-only
    and its private bytes are purged with the predecessor revisions after publish.
    The draft overlay and the retained-owner set are captured once per pass;
    the committed head side of the proposal stays inline (never a copy).
    """
    with ExitStack() as scratch:
        if captures is None:
            captures = scratch.enter_context(ProposalCaptures(writer, draft, head))
        return _retirement_plan(
            writer, draft, head, definitions, cutoff, captures, scratch
        )


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


def _floor_ceilings(writer, head, cutoff, span_dirty, *, unproven_retirement=False):
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
    if not unproven_retirement and not _published_live_expiry(
        writer, head, cutoff, span_dirty
    ):
        ceilings["retention_retired_live"] = bool(prior)
        ceilings["retention_floor_at"] = inherited.get("retention_floor_at")
        return ceilings
    captured = captured_through(inherited)
    ceilings["retention_retired_live"] = True
    ceilings["retention_floor_at"] = (
        None if captured is None else (captured - OVERLAP).isoformat()
    )
    return ceilings


def _retirement_plan(writer, draft, head, definitions, cutoff, captures, scratch):
    from tracer.services.derived_metrics.span_evidence import (
        require_retirement_evidence,
    )

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
        _expired_aggregates(writer, head, cutoff)
        or span_dirty
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
                writer,
                head,
                fields,
                params,
                cutoff,
                captures.owners(cutoff),
                span_fields=captures.span_owner_fields,
            )
        )
    )
    former = captures.former_span_quarters(span_definitions)
    draft_ledger: dict[str, list] = {}
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
                writer._freeze_buckets(
                    draft, head, definition, overlay, overlay_params, former=former
                )
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
    removal, _ = _field_expiry(
        writer,
        scratch,
        fields,
        params,
        "retire_cutoff",
        expired,
        spans=span_definitions,
    )
    retain = f"NOT ({removal})"
    retained = (
        _retained_fields(fields, retain, "retire_cutoff", owners)
        if span_definitions
        else f"SELECT * FROM ({fields}) WHERE {retain}"
    )
    ledger: dict[str, list] = {}
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
    # A checkpoint replaces the complete retired namespace with an empty one.
    # Do not carry legacy retired labels or manufacture new expired statistics.
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
    _freeze_retained_buckets(
        writer,
        attempt,
        head,
        definitions,
        cutoff,
        fields=fields,
        removal=removal,
        retained=retained,
        params=params,
        proposal_totals=captures.proposal(BUCKET_TOTALS),
        extra_keys=former,
        dirty_entries=(
            entry
            for part in ("dirty_before", "dirty_after")
            for entry in ledger.get(part, ())
            if "bucket_start" in entry
        ),
    )
    MetricRefreshAttempt.abort(
        draft.id, reason="retention_replan", **writer.lease.check()
    )
    return attempt


def _checkpoint_bucket_windows(store, affected):
    """Page distinct recipe/quarter keys; never round-trip the whole capture."""
    from tracer.services.derived_metrics.changes import keyset_after, utc
    from tracer.services.derived_metrics.refresh import BUCKET_WINDOWS_PER_FOLD

    order = ", ".join(affected.columns)
    remaining = affected.digest[0]
    after = "1"
    bound: dict[str, object] = {}
    while rows := store.execute(
        f"{affected.select} WHERE {after} ORDER BY {order} LIMIT {BUCKET_WINDOWS_PER_FOLD}",
        bound,
    ):
        remaining -= len(rows)
        for (metric, version), group in groupby(rows, key=itemgetter(0, 1)):
            yield (str(metric), version), [utc(start) for _, _, start in group]
        if remaining <= 0 or len(rows) < BUCKET_WINDOWS_PER_FOLD:
            break
        after, bound = keyset_after(affected.columns, rows[-1], "checkpoint_after")
    if remaining:
        raise MetricStateConflict("retention bucket-key coverage changed")


def _checkpoint_key_pages(store, keys, params):
    """Partition the entire total-key space, not just the keys to rebuild."""
    from tracer.services.derived_metrics.changes import keyset_after
    from tracer.services.derived_metrics.frozen_relations import (
        CAPTURE_BYTES,
        CAPTURE_ROWS,
        SessionCapture,
    )

    schema = _RECIPE_QUARTER_SCHEMA
    fields = tuple(name for name, _ in schema)
    totals = ("metric_id", "metric_version", "bucket_start")
    # These three RowBinary types are fixed width (16 + 4 + 4). Let the
    # checked capture reject even a singleton if its configured cap is smaller.
    limit = max(1, min(CAPTURE_ROWS, CAPTURE_BYTES // 24))
    after = lower = "1"
    lo: dict[str, object] = {}
    while True:
        sql = (
            f"SELECT * FROM ({keys}) WHERE {after} "
            f"ORDER BY {', '.join(fields)} LIMIT {limit}"
        )
        with SessionCapture(
            store,
            schema=schema,
            sources=lambda sql=sql, bound={**params, **lo}: [(sql, bound)],
            role="checkpoint_bucket_keys",
        ) as page:
            upper, hi = "1", {}
            if page.digest[0] == limit:
                last = store.execute(
                    f"{page.select} ORDER BY field_id DESC, field_version DESC, "
                    "bucket_start DESC LIMIT 1"
                )[0]
                beyond, hi = keyset_after(totals, last, "checkpoint_hi")
                upper = f"NOT ({beyond})"
            # Even an empty last page owns the untouched tail of the key space.
            yield page, f"({lower}) AND ({upper})", {**lo, **hi}
            if not hi:
                return
        after, lo = keyset_after(fields, last, "checkpoint_lo")
        lower, _ = keyset_after(totals, last, "checkpoint_lo")


def _checkpoint_window_rows(writer, attempt, predicate, window_params):
    """Select this window from the successive *already retained* ledger batches.

    Both checkpoint writers finish freezing FIELD_VALUES before bucket folds.
    Thus removal (across ALL versions), owner scrubs and span repairs have
    already been applied. Read each immutable batch once per oversized window,
    not the original proposal and expired-unit subquery once per child range.
    """
    from tracer.services.derived_metrics.publication import FrozenRows
    from tracer.services.derived_metrics.refresh import payload_select

    for plan in writer.plans(attempt, kind="field_values"):
        payload = FrozenRows.from_plan(plan)
        batch, bound = payload_select(
            FIELD_VALUES, [payload], prefix="checkpoint_batch"
        )
        keys = writer.store.execute(
            f"SELECT level, unit_id, row_sequence FROM ({batch}) WHERE {predicate} "
            "ORDER BY level, unit_id",
            {**bound, **window_params},
        )
        # Freeze allocated contiguous row sequences in the payload's order.
        # Select offsets, not a second serialization/copy of every wide field.
        for level, unit, sequence in keys:
            index = sequence - plan["first"]
            if not 0 <= index < len(payload.rows):
                raise MetricStateConflict("checkpoint field sequence is outside batch")
            yield (level, unit), payload.rows[index]


def _checkpoint_live_windows(writer, attempt, retained, params, recipe, starts):
    """Bisect only a rejected INPUT, before a fold could append any output."""
    from tracer.services.derived_metrics.bounded_reads import FrozenCheckpointWindow
    from tracer.services.derived_metrics.frozen_relations import (
        FrozenRelationOverflow,
        SessionCapture,
    )

    predicate = (
        "is_deleted = 0 AND status != 'open' AND field_id = toUUID(%(checkpoint_metric)s) "
        "AND field_version = %(checkpoint_version)s AND bucket_start IN %(checkpoint_starts)s"
    )
    sql = f"SELECT * FROM ({retained}) WHERE {predicate}"
    window_params = {
        "checkpoint_metric": recipe[0],
        "checkpoint_version": recipe[1],
        "checkpoint_starts": tuple(starts),
    }
    capture = SessionCapture(
        writer.store,
        schema=FIELD_VALUES.schema,
        sources=lambda: [(sql, {**params, **window_params})],
        role="checkpoint_bucket_fields",
    )
    with ExitStack() as scratch:
        try:
            live = scratch.enter_context(capture)
        except FrozenRelationOverflow:
            if capture.overflow_rows is None:
                raise  # an upstream capture failed, not this window's input
            if len(starts) == 1:
                # This single-source capture measured the complete quarter
                # before rejecting it. Its count must match the ledger stream.
                window = FrozenCheckpointWindow(
                    capture.overflow_rows,
                    lambda: _checkpoint_window_rows(
                        writer, attempt, predicate, window_params
                    ),
                )
                yield starts, window, {}
                return
        else:
            yield starts, live.select, {}
            return
    mid = len(starts) // 2
    for half in (starts[:mid], starts[mid:]):
        yield from _checkpoint_live_windows(
            writer, attempt, retained, params, recipe, half
        )


def _uncovered_quarters_select(writer, head, fields, *, where, having=None):
    """Committed totals not accounted for by live fields, including missing quarters."""
    totals, bp = committed_rows_select(
        BUCKET_TOTALS,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
        param_prefix="coverage_totals",
    )
    total = (
        "SELECT metric_id AS field_id, metric_version AS field_version, bucket_start, "
        f"sum(unit_count) AS n FROM ({totals}) WHERE is_deleted = 0 AND ({where}) "
        "GROUP BY field_id, field_version, bucket_start"
    )
    live = (
        f"SELECT field_id, field_version, bucket_start, count() AS n FROM ({fields}) "
        f"WHERE is_deleted = 0 AND status != 'open' AND ({where}) "
        "GROUP BY field_id, field_version, bucket_start"
    )
    if having:
        live += f" HAVING {having}"
    join = "INNER" if having else "LEFT"
    return (
        f"SELECT t.field_id, t.field_version, t.bucket_start FROM ({total}) t "
        f"{join} JOIN ({live}) l ON t.field_id = l.field_id AND "
        "t.field_version = l.field_version AND t.bucket_start = l.bucket_start "
        "WHERE t.n > ifNull(l.n, 0)",
        bp,
    )


def _uncovered_quarters(writer, head, fields, params, *, where, having=None):
    """Never truncate an over-budget source backfill nomination."""
    from tracer.services.derived_metrics.changes import utc
    from tracer.services.derived_metrics.publication import MAX_BATCH_ROWS
    from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven

    sql, bp = _uncovered_quarters_select(
        writer, head, fields, where=where, having=having
    )
    rows = writer.store.execute(f"{sql} LIMIT {MAX_BATCH_ROWS + 1}", {**params, **bp})
    if len(rows) > MAX_BATCH_ROWS:
        raise MetricAdmissionUnproven(
            "too many uncovered quarters; bounded backfill required"
        )
    return {(str(metric), version, utc(at)) for metric, version, at in rows}


def _source_free_quarters(writer, head, fields, expired_units):
    """Keys to discard, not a bounded list of candidates to repair from source.

    Include the retained side of every disputed anchor and even a total with
    no surviving field at all. Legacy retired parts have no owner proof either.
    All inputs are collapsed rows of the *same* immutable metric manifest.
    """
    deficient, bp = _uncovered_quarters_select(writer, head, fields, where="1")
    retired, rp = committed_rows_select(
        RETIRED_PARTS,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
        param_prefix="unproven_retired",
    )
    return (
        f"SELECT DISTINCT field_id, field_version, bucket_start FROM ({fields}) "
        "WHERE (unit_start >= %(expiry_cutoff)s OR bucket_start >= %(expiry_cutoff)s) "
        f"AND (level, unit_id) IN ({expired_units}) "
        f"UNION ALL {deficient} UNION ALL "
        f"SELECT metric_id, metric_version, bucket_start FROM ({retired}) WHERE is_deleted = 0",
        {**bp, **rp},
    )


def _require_bucket_coverage(
    writer, head, recipe, starts, fields, params, source_backed
):
    """A complete parent can legitimately shrink. An incomplete one needs a source backfill."""
    from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven

    if head is None:
        return
    previous, pp = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
        key_in={"field_id": (recipe[0],), "field_version": (recipe[1],)},
        param_prefix="coverage_parent",
    )
    proof = {
        "coverage_metric": recipe[0],
        "coverage_version": recipe[1],
        "coverage_starts": tuple(starts),
    }
    scope = (
        "field_id = toUUID(%(coverage_metric)s) AND field_version = %(coverage_version)s "
        "AND bucket_start IN %(coverage_starts)s"
    )
    deficient = _uncovered_quarters(
        writer, head, previous, {**pp, **proof}, where=scope
    )
    if deficient and (
        deficient - source_backed
        or _uncovered_quarters(
            writer,
            head,
            fields,
            {**params, **proof, "coverage_starts": tuple(q[2] for q in deficient)},
            where=scope,
        )
    ):
        raise MetricAdmissionUnproven("uncovered retention quarter requires backfill")


def _repair_span_quarters(
    writer, scratch, head, definitions, fields, params, cutoff, removal, as_of
):
    """Backfill deficient retained quarters losing a span, without an intermediate head.

    The PG-only drafts use ordinary bounded source capture, sampling, forward
    coverage and admission. Do not admit a new identity on a privacy pass.
    """
    from tracer.services.derived_metrics.backfill import forward_coverage_start
    from tracer.services.derived_metrics.frozen_relations import SessionCapture
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven
    from tracer.services.derived_metrics.span_evidence import freeze_span_fields
    from tracer.services.derived_metrics.span_sampling import SpanSamplingContext

    missing = _uncovered_quarters(
        writer,
        head,
        fields,
        params,
        where="bucket_start >= %(expiry_cutoff)s",
        having=f"countIf(level = 'span' AND ({removal})) > 0",
    )
    if not missing:
        return fields, None, set(), []
    recipes = {(str(d.metric_id), d.version): d for d in definitions}
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    drafts = []
    for recipe, group in groupby(sorted(missing), key=itemgetter(0, 1)):
        definition = recipes.get(recipe)
        if definition is None:
            raise MetricStateConflict("retention definition is unavailable")
        with serialized_project(scope.project):
            current = MetricProjectRefresh.for_project(scope.project).head_manifest
            if current is None or current.pk != head.pk or current.revoked_at:
                raise MetricStateConflict("retention base changed or expired")
            _require_retention_definitions(writer, [definition])
            draft = writer.begin(
                purpose="backfill",
                versions=[
                    {
                        "id": getattr(definition, "id", None),
                        "metric_id": definition.metric_id,
                        "version": definition.version,
                        "level": definition.level,
                    }
                ],
            )
        coverage = forward_coverage_start(definition)
        with SpanSamplingContext(
            writer,
            draft,
            [definition],
            as_of=as_of,
            # Scratch windows are derived afresh from the base on every retry.
            # Only real predecessor work belongs in the durable recovery ledger.
            dirty=tuple(at for _, _, at in group),
        ) as sampling:
            freeze_span_fields(
                writer,
                draft,
                head,
                definition,
                scope,
                as_of=as_of,
                cutoff=max(cutoff, coverage) if coverage else cutoff,
                sampling=sampling,
            )
        drafts.append(draft)
    # One aggregate input budget, not a separate 128 MiB allowance for every
    # deficient recipe. Reconnects replay immutable ledger batches only.
    overlay = scratch.enter_context(
        SessionCapture(
            writer.store,
            schema=FIELD_VALUES.schema,
            sources=lambda: (
                source
                for draft in drafts
                for source in _frozen_batch_sources(writer, draft, FIELD_VALUES)
            ),
            role="checkpoint_span_backfill",
        )
    ).select
    key = ", ".join(FIELD_VALUES.logical_key)
    # A source-only arrival cannot fill a missing historical identity. Nor can
    # we silently drop a previously retained member which now needs another
    # destination backfilled. Leave these ambiguous cases to ordinary backfill.
    eligible = f"SELECT {key} FROM ({fields}) WHERE is_deleted = 0 AND NOT ({removal})"
    if writer.store.execute(
        f"SELECT 1 FROM ({overlay}) WHERE (is_deleted = 0) != (({key}) IN ({eligible})) LIMIT 1",
        params,
    ):
        raise MetricAdmissionUnproven("uncovered span membership requires backfill")
    replacements = f"SELECT * FROM ({overlay}) WHERE is_deleted = 0"
    # Both this quarter and the former quarter of EVERY displaced identity
    # must be folded from the very same repaired fields. Never copy a stale
    # total at the other end of the move.
    former = (
        f"SELECT DISTINCT field_id, field_version, bucket_start FROM ({fields}) "
        f"WHERE ({key}) IN (SELECT {key} FROM ({replacements}))"
    )
    repaired = (
        f"SELECT * FROM (({fields}) UNION ALL ({replacements})) "
        f"ORDER BY generation DESC, {FIELD_VALUES.version_column} DESC LIMIT 1 BY {key}"
    )
    return repaired, former, missing, [draft.pk for draft in drafts]


def _freeze_retained_buckets(
    writer,
    attempt,
    head,
    definitions,
    cutoff,
    *,
    fields,
    removal,
    retained,
    params,
    proposal_totals=None,
    extra_keys=None,
    repaired=(),
    dirty_entries=(),
):
    """Copy and rebuild disjoint quarters of one immutable checkpoint proposal."""
    from tracer.services.derived_metrics.changes import utc
    from tracer.services.derived_metrics.publication import MAX_BATCH_ROWS

    recipes = {(str(d.metric_id), d.version): d for d in definitions}
    # Ordinary dirty quarters belong to the recipes actually computed by the
    # draft, not every old recipe loaded only to retire a pinned contribution.
    draft_versions = {
        (str(v["metric_id"]), v["version"]) for v in attempt.metric_versions
    }
    computed = [
        key
        for key, d in recipes.items()
        if d.level != "user" and (not draft_versions or key in draft_versions)
    ]
    dirty_set, span_starts = set(), set()
    if computed:
        for entry in dirty_entries:
            at = utc(datetime.fromisoformat(str(entry["bucket_start"])))
            if at >= cutoff:
                dirty_set.add(at)
                if entry.get("level") == "span":
                    span_starts.add(at)
    dirty = sorted(dirty_set)
    empty_retired: tuple[str, dict] = (
        f"SELECT * FROM {RETIRED_PARTS.name} WHERE 0",
        {},
    )
    # Partition by time only when ordinary dirty nominations exceed one bind
    # page. The first and last intervals also cover wholly untouched quarters.
    for i in range(0, max(1, len(dirty)), MAX_BATCH_ROWS):
        window = "bucket_start >= toDateTime64(%(checkpoint_floor)s, 6, 'UTC')"
        wp = {"checkpoint_floor": dirty[i] if i else cutoff}
        if i + MAX_BATCH_ROWS < len(dirty):
            window += " AND bucket_start < %(checkpoint_until)s"
            wp["checkpoint_until"] = dirty[i + MAX_BATCH_ROWS]
        # Use the very same expired-unit relation as the field copy, AFTER
        # collapse. Span and conversation versions can both disagree on starts.
        keys = (
            f"SELECT DISTINCT field_id, field_version, bucket_start FROM ({fields}) "
            f"WHERE level IN ('span', 'conversation') AND is_deleted = 0 "
            f"AND status != 'open' AND {window} AND ({removal})"
        )
        if extra_keys:
            keys += f" UNION DISTINCT SELECT * FROM ({extra_keys}) WHERE {window}"
        bound = {**params, **wp}
        if dirty:
            keys += (
                " UNION DISTINCT SELECT toUUID(recipe.1) AS field_id, "
                "toUInt32(recipe.2) AS field_version, bucket_start FROM "
                "(SELECT toDateTime(arrayJoin(%(quarter_page)s), 'UTC') AS bucket_start) AS quarters "
                "CROSS JOIN (SELECT arrayJoin(%(checkpoint_recipes)s) AS recipe) AS recipes"
            )
            bound.update(
                quarter_page=dirty[i : i + MAX_BATCH_ROWS], checkpoint_recipes=computed
            )
        for page, key_range, bounds in _checkpoint_key_pages(writer.store, keys, bound):
            where = (
                f"is_deleted = 0 AND {window} AND {key_range} AND "
                f"(metric_id, metric_version, bucket_start) NOT IN ({page.select})"
            )
            if proposal_totals is None:
                writer.freeze_current(
                    attempt,
                    BUCKET_TOTALS,
                    head,
                    where=where,
                    params={**wp, **bounds},
                    large_pages=True,
                )
            else:
                totals, bp = proposal_totals
                writer.freeze(
                    attempt,
                    BUCKET_TOTALS,
                    f"SELECT * FROM ({totals}) WHERE {where}",
                    {**bp, **wp, **bounds},
                    collect_payloads=False,
                    row_limit=MAX_COMPACTION_ROWS,
                )
            if not page.digest[0]:
                continue
            for recipe, starts in _checkpoint_bucket_windows(writer.store, page):
                definition = recipes.get(recipe)
                if definition is None:
                    raise MetricStateConflict("retention definition is unavailable")
                if definition.level == "user":
                    continue
                source_backed = set(repaired)
                if definition.level == "span" and recipe in computed:
                    source_backed.update(
                        (*recipe, at) for at in starts if at in span_starts
                    )
                _require_bucket_coverage(
                    writer, head, recipe, starts, fields, params, source_backed
                )
                for batch, live, live_params in _checkpoint_live_windows(
                    writer, attempt, retained, params, recipe, starts
                ):
                    writer._freeze_buckets(
                        attempt,
                        head,
                        definition,
                        live,
                        live_params,
                        complete_fields=True,
                        retired_override=empty_retired,
                        _dirty_starts=batch,
                    )


def purge_checkpoint(writer, attempt, manifest, *, recovery_pending=False):
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
    return finish_pending_expiry(writer)


def finish_pending_expiry(writer, *, expiry_id=None):
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
        kept: set[UUID] = {a for _, a in head.allowed_attempts()}
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
            kept.update(a for _, a in retained.allowed_attempts())
        keep: tuple[UUID, ...] = tuple(kept) or (UUID(int=0),)
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
        tables: tuple[MetricTable, ...] = (
            FIELD_VALUES,
            TABLES["metric_field_values_checkpoint"],
            SOURCE_DEPENDENCIES,
            UNIT_STATE,
        )
        # Preserve the existing core receipt shape; current-list stores get
        # their own verification count under the same quiescence barrier.
        core_table_count = len(tables)
        tables = (
            *tables,
            RETIRED_PARTS,
            BUCKET_TOTALS,
            TABLES["metric_bucket_totals_checkpoint"],
            *LIST_CURRENT_TABLES,
        )
        # Prompt scrub, even while an old submission may still land.
        for table in tables:
            _delete(writer.store, table, predicate, params)
        submissions = MetricErasureRequest.objects.filter(
            project_key=project_id,
            store_payload__protocol="metric_submission",
            store_payload__generation__lte=through,
        )
        if (
            submissions.count()
            != submissions.filter(
                status="done", receipts__synchronous_response__drained=1
            ).count()
        ):
            complete = False
            continue
        # Logical publication already aborted old writers under the lifecycle
        # key. Their dispatch registration revalidates that state, so no new old-
        # namespace submission can appear after this barrier observation.
        for table in tables:
            _verified_delete(writer.store, table, predicate, params)
        # Saved SQL jobs can overlap the scrub. The pass start is a cutoff
        # input, not evidence that the final verification had finished.
        completed_at = writer.lease.clock()
        MetricErasureRequest.objects.filter(id=obligation.id).update(
            status="done",
            completed_at=completed_at,
            receipts={
                "write_barrier": {"drained": submissions.count()},
                "final_scrub": {
                    "verified_tables": core_table_count,
                    "remaining": 0,
                    "private_payloads_remaining": 0,
                },
                "aggregate_scrub": {"verified_tables": 3, "remaining": 0},
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
            completed_at=completed_at,
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
    writer, head, fields, field_params, cutoff, owners=None, *, span_fields=None
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
    if writer.store.execute(
        f"SELECT count() FROM ({dependencies}) WHERE "
        f"({' OR '.join(f'notEmpty({c})' for c in owner_columns)}) "
        f"AND canonical_session_id NOT IN (SELECT unit_id FROM ({fields}) "
        "WHERE level = 'conversation' AND unit_start >= toDateTime64(%(span_owner_cutoff)s, 6, 'UTC'))",
        {**field_params, **params, "span_owner_cutoff": cutoff},
    )[0][0]:
        return True
    # Standalone expiry has only committed fields. Refresh can combine the raw
    # head and draft once on the left; owner evidence still needs all recipes.
    left, bound = span_fields() if span_fields is not None else (fields, {})
    return bool(
        writer.store.execute(
            f"SELECT count() FROM ({left}) WHERE level = 'span' "
            "AND (notEmpty(session_id) OR notEmpty(user_id)) "
            f"AND session_id NOT IN ({owners or inline_owners})",
            merge_bindings(field_params, bound, {"span_owner_cutoff": cutoff}),
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


def finish_unpublished_replan(writer, *, discard_drafts=False):
    """Erase failed first-publication copies without creating an admission head.

    The durable policy debt authorizes cleanup, not calculation. Fence every
    existing draft, scrub the generation-bounded namespace, wait for synchronous
    submission receipts, and verify a second scrub before discharging the debt.
    Without a head there is no retained replacement checkpoint: finite-policy
    expiry and subject erasure conservatively discard ALL unpublished copies,
    including retained ones. Repair rediscovers source; no cursor is consumed.
    """
    project_id = writer.lease.project_id
    with serialized_project(project_id):
        writer.lease.check()
        state = MetricProjectRefresh.for_project(project_id)
        debts = MetricErasureRequest.objects.filter(
            project_key=project_id,
            organization_key=writer.org_id,
            scope_kind="history",
            deletion_key__startswith="retention-replan:",
            store_payload__protocol="metric_policy_replan",
        ).exclude(status="done")
        if state.head_manifest_id:
            return False
        if not debts.exists() and (
            discard_drafts
            or retention_cutoff(writer.retention_days(), now=writer.lease.clock())
            is not None
        ):
            # A pass_yield is not a policy-replan failure, but its frozen bytes
            # have the same source lifetime. Persist the normal cleanup debt
            # BEFORE deleting their only ledger, so interrupted scrubs retry.
            latest = (
                MetricRefreshAttempt.objects.filter(project_id=project_id)
                .order_by("-generation")
                .first()
            )
            if latest is not None:
                from tracer.services.derived_metrics.deletion_protocol import _intent

                _intent(
                    organization_id=writer.org_id,
                    project_id=project_id,
                    scope="history",
                    deletion_key=f"retention-replan:{latest.id}",
                    store_payload={"protocol": "metric_policy_replan"},
                )
        ids = list(debts.values_list("id", flat=True))
        if not ids:
            # No draft, or this exact generation has already been scrubbed.
            return discard_drafts
        if MetricGenerationPin.objects.filter(
            project_id=project_id,
            invalidated_at__isnull=True,
            expires_at__gt=writer.lease.clock(),
            manifest__revoked_at__isnull=True,
        ).exists():
            raise MetricStateConflict("unpublished cleanup has a readable generation")
        through = state.generation_high_water
        MetricRefreshAttempt.objects.filter(
            project_id=project_id,
            generation__lte=through,
            state__in=MetricRefreshAttempt.OPEN_STATES,
        ).update(
            state="aborted",
            aborted_at=writer.lease.clock(),
            abort_reason="retention_replan",
        )
        _purge_terminal_payloads(project_id, through_generation=through)
        MetricProjectRefresh.objects.filter(project_id=project_id).update(
            pending_repair=True, pending_expiry=True
        )
    submissions = MetricErasureRequest.objects.filter(
        project_key=project_id,
        organization_key=writer.org_id,
        store_payload__protocol="metric_submission",
        store_payload__generation__lte=through,
    )
    if (
        submissions.count()
        != submissions.filter(store_payload__shard=writer.store.identity()).count()
    ):
        raise MetricStateConflict("unpublished cleanup shard is not proven")
    params = {
        "erase_org": str(writer.org_id),
        "erase_project": str(project_id),
        "erase_through": through,
    }
    predicate = "org_id = toUUID(%(erase_org)s) AND project_id = toUUID(%(erase_project)s) AND generation <= %(erase_through)s"
    for table in TABLES.values():
        _delete(writer.store, table, predicate, params)
    if (
        submissions.count()
        != submissions.filter(
            status="done", receipts__synchronous_response__drained=1
        ).count()
    ):
        return False
    # Registration checks the attempt's open state under the lifecycle lock;
    # no old writer can register after the abort above. Late ACKs are not enough
    # without this second physical scrub of every old native namespace.
    for table in TABLES.values():
        _verified_delete(writer.store, table, predicate, params)
    with serialized_project(project_id):
        writer.lease.check()
        _purge_terminal_payloads(project_id, through_generation=through)
        if _terminal_payloads(project_id, through_generation=through).exists():
            raise MetricStateConflict("unpublished private scrub is incomplete")
        MetricErasureRequest.objects.filter(id__in=ids).update(
            status="done",
            completed_at=writer.lease.clock(),
            receipts={
                "final_scrub": {
                    "through_generation": through,
                    "verified_tables": len(TABLES),
                    "remaining": 0,
                }
            },
        )
        submissions.delete()
        MetricProjectRefresh.objects.filter(project_id=project_id).update(
            pending_expiry=MetricErasureRequest.objects.filter(
                project_key=project_id,
                store_payload__protocol__in=["metric_expiry", "metric_policy_replan"],
            )
            .exclude(status="done")
            .exists(),
            pending_repair=True,
        )
    return True


def _finish_unlimited_replan(writer, head, retention_days):
    """Revalidate obsolete policy debt without claiming an erase or consuming work.

    No permanent floor may be removed here. The failed attempts and submission
    receipts stay durable for ordinary repair/drainage; this receipt establishes
    only that the current unlimited policy no longer requires their expiry.
    """
    from django.apps import apps

    project_id = writer.lease.project_id
    if (
        head is None
        or not MetricErasureRequest.objects.filter(
            project_key=project_id, store_payload__protocol="metric_policy_replan"
        )
        .exclude(status="done")
        .exists()
    ):
        return False
    writer.store.assert_visible(head)
    with serialized_project(project_id):
        writer.lease.check()
        state = MetricProjectRefresh.for_project(project_id)
        current = state.head_manifest
        if (
            current is None
            or current.id != head.id
            or current.revoked_at is not None
            or current.published_by.source_ceilings.get("retention_floor")
        ):
            raise MetricStateConflict("policy replan base changed or expired")
        apps.get_model(
            "accounts", "Organization"
        )._base_manager.select_for_update().get(id=writer.org_id)
        if retention_cutoff(retention_days(), now=writer.lease.clock()) is not None:
            raise MetricStateConflict("policy changed during unlimited revalidation")
        debts = MetricErasureRequest.objects.filter(
            project_key=project_id,
            organization_key=writer.org_id,
            store_payload__protocol="metric_policy_replan",
        ).exclude(status="done")
        selected = list(
            debts.order_by("requested_at", "id").values_list("id", "deletion_key")[:100]
        )
        if not selected:
            return False
        attempts = []
        for _, key in selected:
            if key is None or not key.startswith("retention-replan:"):
                raise MetricStateConflict("policy replan attempt is not proven")
            try:
                attempts.append(UUID(key.split(":", 1)[1]))
            except ValueError:
                raise MetricStateConflict(
                    "policy replan attempt is not proven"
                ) from None
        # Only the actual aborted proposals qualify. Never turn missing history
        # or an unknown still-running sender into a completion receipt.
        if MetricRefreshAttempt.objects.filter(
            project_id=project_id, id__in=attempts, state="aborted"
        ).count() != len(set(attempts)):
            raise MetricStateConflict("policy replan attempts are not fenced")
        debts.filter(id__in=[key for key, _ in selected]).update(
            status="done",
            completed_at=writer.lease.clock(),
            receipts={
                "policy_revalidated": {"unlimited": True, "erasure_performed": False}
            },
        )
        MetricProjectRefresh.objects.filter(project_id=project_id).update(
            pending_repair=True,
            pending_expiry=MetricErasureRequest.objects.filter(
                project_key=project_id,
                store_payload__protocol__in=["metric_expiry", "metric_policy_replan"],
            )
            .exclude(status="done")
            .exists(),
        )
    return True


@_close_failed_attempts
def expire_project(writer, definitions, *, retention_days, now=None, as_of=None):
    """Freeze retirement, publish a complete replacement, revoke, then purge.

    A failed physical purge leaves safe reads (the old pins are revoked) and is
    idempotently retried; it must not be reported as a completed physical erase.
    All old physical revisions are deleted, not merely the current allowlist.
    """
    with ExitStack() as scratch:
        return _expire_project(writer, definitions, retention_days, now, as_of, scratch)


def _expire_project(writer, definitions, retention_days, now, as_of, scratch):
    if connection.in_atomic_block:
        raise MetricStateConflict("physical purge requires an outer commit boundary")
    definitions = list(definitions)
    now = now or writer.lease.clock()
    unlimited = retention_cutoff(retention_days(), now=now) is None
    # An extension cannot restore old retired values. Migrate legacy checkpoints
    # at their permanent floor even if the current source policy is unlimited.
    project_id = writer.lease.project_id
    head = MetricProjectRefresh.for_project(project_id).head_manifest
    inherited = (
        head.published_by.source_ceilings.get("retention_floor") if head else None
    )
    cutoff = physical_cutoff(_PolicyWriter(writer, retention_days), now=now)
    if inherited:
        inherited = datetime.fromisoformat(str(inherited))
        cutoff = max(cutoff, inherited) if cutoff else inherited
    if cutoff is None:
        revalidated = _finish_unlimited_replan(writer, head, retention_days)
        return {"unlimited": unlimited, "purged": False, "revalidated": revalidated}
    with serialized_project(project_id):
        writer.lease.check()
        from tracer.services.derived_metrics.backfill import scrub_expired_progress

        scrub_expired_progress(project_id, cutoff=cutoff, now=now)
        head = MetricProjectRefresh.for_project(project_id).head_manifest
        if head is None:
            return {"unlimited": unlimited, "purged": False}
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

    source_allowed = writer.lease.expiry_source_allowed and (
        Project.no_workspace_objects.filter(pk=project_id).exists()
    )
    if source_allowed:
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
    # An old anchor nominates the unit after collapse. It is conclusive for
    # a conversation; _field_expiry resolves conflicting span starts before
    # deciding which versioned fields and contributions to remove.
    expired_units = (
        f"SELECT DISTINCT level, unit_id FROM ({fields}) "
        "WHERE unit_start < toDateTime64(%(expiry_cutoff)s, 6, 'UTC')"
    )
    span_definitions = [d for d in definitions if d.level == "span"]
    span_dirty = (
        _expired_span_buckets(writer, head, cutoff) if span_definitions else set()
    )
    discard_quarters = None
    if not source_allowed:
        unproven, bound = _source_free_quarters(writer, head, fields, expired_units)
        params.update(bound)
        if writer.store.execute(f"SELECT 1 FROM ({unproven}) LIMIT 1", params):
            discard_quarters = unproven
    if (
        not MetricErasureRequest.objects.filter(
            project_key=project_id, store_payload__protocol="metric_policy_replan"
        )
        .exclude(status="done")
        .exists()
        and not any(recovery.values())
        and not (
            not head.published_by.source_ceilings.get("recovery_blocked")
            and MetricRefreshAttempt.blocked_expiry_handoffs(head).exists()
        )
        and not _expired_aggregates(writer, head, cutoff)
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
        and discard_quarters is None
    ):
        pending = (
            MetricErasureRequest.objects.filter(
                project_key=project_id, store_payload__protocol="metric_expiry"
            )
            .exclude(status="done")
            .exists()
        )
        return {
            "unlimited": unlimited,
            "purged": finish_pending_expiry(writer) if pending else False,
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
    floor_terms = _floor_ceilings(
        writer,
        head,
        cutoff,
        span_dirty,
        unproven_retirement=discard_quarters is not None,
    )
    extra_keys, repaired, repair_drafts = None, set(), []
    parent_fields = fields
    if not source_allowed:
        removal = "unit_start < %(expiry_cutoff)s"
        if discard_quarters is not None:
            removal += (
                f" OR (field_id, field_version, bucket_start) IN ({discard_quarters})"
            )
    else:
        removal, disputed = _field_expiry(
            writer,
            scratch,
            fields,
            params,
            "expiry_cutoff",
            expired_units,
            spans=span_definitions,
        )
        if disputed:
            fields, extra_keys, repaired, repair_drafts = _repair_span_quarters(
                writer,
                scratch,
                head,
                span_definitions,
                fields,
                params,
                cutoff,
                removal,
                as_of or now,
            )
    with serialized_project(project_id):
        # Classification ran without the lifecycle lock. Revocation need not
        # change the head id or lease when there were no open predecessors, so
        # neither begin() nor its publication CAS can validate this copied base.
        current = MetricProjectRefresh.for_project(project_id).head_manifest
        if current is None or current.id != head.id or current.revoked_at is not None:
            raise MetricStateConflict("retention base changed or expired")
        for draft_id in repair_drafts:
            MetricRefreshAttempt._locked_open(
                draft_id,
                state=MetricRefreshAttempt.State.PREPARED,
                **writer.lease.check(),
            )
        _require_retention_definitions(writer, definitions)
        # Register the successor under the same lock as these fresh checks:
        # subsequent revocations must abort it before any copy can publish.
        attempt = writer.begin(
            purpose="compaction",
            ceilings={
                "retention_cutoff": cutoff,
                # A repair read only its own quarters. It cannot advance the
                # inherited project-wide capture clock used for later admission.
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
        for predecessor in (*predecessors, *repair_drafts):
            MetricRefreshAttempt.abort(
                predecessor, reason="retention_replan", **writer.lease.check()
            )
        # Yielded passes' ledgers are in this checkpoint's handoff too.
        writer.mark_yields_carried()
    retain = f"NOT ({removal})"
    retained_units = f"SELECT DISTINCT level, unit_id FROM ({fields}) WHERE {retain}"
    owners = _owner_relation(parent_fields, "expiry_cutoff")
    retained = f"SELECT * FROM ({fields}) WHERE {retain}"
    if span_definitions:
        retained = _retained_fields(fields, retain, "expiry_cutoff", owners)
        writer.freeze(
            attempt,
            FIELD_VALUES,
            retained,
            params,
            collect_payloads=False,
            row_limit=MAX_COMPACTION_ROWS,
        )
    else:
        writer.freeze_current(
            attempt,
            FIELD_VALUES,
            head,
            where=retain,
            params=params,
            large_pages=True,
        )
    # No retired contributions survive this replacement checkpoint.
    writer.append(
        attempt,
        "dirty_before",
        [{"level": "span", "bucket_start": value} for value in sorted(span_dirty)],
    )
    for table in (UNIT_STATE, SOURCE_DEPENDENCIES):
        if table == SOURCE_DEPENDENCIES:
            # B04's unit_start is the SPAN start. A recent member does not
            # make an expired conversation's private membership retainable.
            where = f"canonical_session_id IN ({owners})"
        else:
            where = f"(level, unit_id) IN ({retained_units})"
        if table == SOURCE_DEPENDENCIES and span_definitions:
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
            where=_current_retained(parent_fields, "expiry_cutoff", owners),
            params=params,
            large_pages=True,
        )
    # A deletion-only checkpoint carries no anonymous totals. Even matching
    # counts cannot prove a prior total used the *same* surviving identities
    # (a contributor may have moved). Keep proven fields, not an unproven sum.
    if source_allowed:
        # Only removal and source-repaired keys need new totals. Carried recovery
        # nominations still belong to the next refresh; do not consume them here.
        _freeze_retained_buckets(
            writer,
            attempt,
            head,
            definitions,
            cutoff,
            fields=fields,
            removal=removal,
            retained=retained,
            params=params,
            extra_keys=extra_keys,
            repaired=repaired,
        )
    # The successor ledger now owns every output byte. Release source/repair
    # scratch before publication can revoke the parent and scrub those drafts.
    scratch.close()
    writer.seal(attempt, cursors={"pending_expiry": False})
    manifest = writer.stage_and_publish(attempt)
    # Revocations and cleanup intent are ALREADY committed with this manifest.
    # Never roll PG back to evidence removed by a physical mutation.
    complete = purge_checkpoint(
        writer, attempt, manifest, recovery_pending=recovery_pending
    )
    return {"unlimited": unlimited, "purged": complete, "manifest": str(manifest.id)}


def _erasure_handoff(store, handoff, request, erased_units):
    """Transfer unconsumed work, excluding the erased owners and their aliases.

    Preimages can be the only remaining owner link for a failed pass. Include
    them in the same victim closure used to copy native fields, rather than
    preserving an erased alias or dropping every unrelated recovery nomination.
    """

    def identified(value):
        return isinstance(value, str) and bool(value.strip())

    # Absence is not an identity: sessionless owners share empty/blank slots.
    # Keep exact nonblank IDs (including their whitespace), never normalize
    # distinct identities into an alias while computing the victim closure.
    subjects = {value for value in request.subject_ids if identified(value)}
    erased_units = {value for value in erased_units if identified(value)}
    if request.scope_kind == "sessions":
        erased_units.update(subjects)
    entries = [entry for values in handoff.values() for entry in values]
    owner_keys = ("unit_id", "raw_session_id", "canonical_session_id")

    keys = list(
        {
            (entry["trace_id"], entry["span_id"])
            for entry in entries
            if entry.get("trace_id") and entry.get("span_id")
        }
    )
    identities: dict[tuple, str] = {}
    for offset in range(0, len(keys), 500):
        # Use the native key encoding, including Unicode/slash escaping; a
        # Python JSON spelling is not necessarily the stored span identity.
        identities.update(
            (tuple(pair), identity)
            for pair, identity in store.execute(
                "SELECT pair, toJSONString(pair) FROM "
                "(SELECT arrayJoin(%(erasure_pairs)s) AS pair)",
                {"erasure_pairs": keys[offset : offset + 500]},
            )
        )

    def span_identity(entry):
        return identities.get((entry.get("trace_id"), entry.get("span_id")))

    def erased(entry):
        return (
            any(entry.get(key) in erased_units for key in owner_keys)
            or span_identity(entry) in erased_units
            or (
                request.scope_kind == "users"
                and any(
                    entry.get(key) in subjects
                    for key in ("raw_user_id", "canonical_user_id")
                )
            )
        )

    while True:
        previous = len(erased_units)
        for entry in entries:
            if erased(entry):
                erased_units.update(
                    entry[key] for key in owner_keys if identified(entry.get(key))
                )
                identity = span_identity(entry)
                if identity:
                    erased_units.add(identity)
        if len(erased_units) == previous:
            break
    retained: dict[str, list] = {}
    for part, values in handoff.items():
        retained[part] = []
        for entry in values:
            if erased(entry):
                continue
            if (
                not any(entry.get(key) for key in OWNER_KEYS)
                and "bucket_start" in entry
            ):
                # Anonymous bucket nominations need no private group labels.
                entry = {
                    k: v for k, v in entry.items() if k in {"bucket_start", "level"}
                }
            retained[part].append(entry)
    return retained, erased_units


def _scrub_erasure_rows(store, request, predicates, params, **namespace):
    """Predicates address fenced namespaces. Only a post-drain scrub is final."""
    submissions = MetricErasureRequest.objects.filter(
        organization_key=request.organization_key,
        store_payload__protocol="metric_submission",
        **namespace,
    )
    if request.project_key:
        submissions = submissions.filter(project_key=request.project_key)
    drained = submissions.filter(
        Q(receipts__synchronous_response__drained=1)
        | Q(receipts__terminal_query__drained=1),
        status="done",
        store_payload__shard=store.identity(),
    )
    # NOT IN a positive proof query: a missing JSON receipt is not a drain.
    pending = submissions.exclude(pk__in=drained.values("pk"))
    if pending.exists():
        # Promptly remove existing rows even when a dispatched write is stuck.
        for table, predicate in predicates:
            _delete(store, table, predicate, params)
        if pending.exists():
            raise MetricStateConflict("live write drainage is pending")
    for table, predicate in predicates:
        _verified_delete(store, table, predicate, params)


def _finish_subject_erasure(writer, store, request, manifest, base, params):
    attempt = manifest.published_by
    with serialized_project(request.project_key):
        writer.lease.check()
        current = MetricProjectRefresh.for_project(request.project_key).head_manifest
        if current is None or current.id != manifest.id or current.revoked_at:
            raise MetricStateConflict("subject erasure checkpoint changed")
        old = list(
            MetricReadManifest.objects.filter(project_id=request.project_key)
            .exclude(id=manifest.id)
            .values_list("id", flat=True)
        )
        revoke_generations(request.project_key, reason="erased", manifest_ids=old)
        # A pre-checkpoint attempt may have started without a parent manifest.
        MetricRefreshAttempt.objects.filter(
            project_id=request.project_key,
            generation__lte=attempt.generation,
            state__in=MetricRefreshAttempt.OPEN_STATES,
        ).update(
            state="aborted",
            aborted_at=writer.lease.clock(),
            abort_reason="generation_revoked",
        )
    with serialized_project(request.project_key):
        writer.lease.check()
        _purge_terminal_payloads(
            request.project_key,
            recovery_attempt=attempt
            if attempt.source_ceilings.get("recovery_pending")
            else None,
            through_generation=attempt.generation,
        )
    # Keep the new subject-safe copies, not anonymous history without lineage.
    predicates = []
    for table in TABLES.values():
        predicate = base + " AND generation <= %(erase_through)s"
        if table.name.startswith("metric_field_values") or table in (
            SOURCE_DEPENDENCIES,
            UNIT_STATE,
            *LIST_CURRENT_TABLES,
        ):
            predicate += " AND attempt_id != toUUID(%(erase_new)s)"
        predicates.append((table, predicate))
    _scrub_erasure_rows(
        store,
        request,
        predicates,
        {**params, "erase_new": str(attempt.id), "erase_through": attempt.generation},
        store_payload__generation__lte=attempt.generation,
    )
    writer.lease.check()
    return {"manifest": str(manifest.id), "anonymous_history_dropped": True}


def purge_by_scope(store, request, *, writer=None):
    if writer is not None:
        return _purge_with_writer(writer, store, request)
    return _purge_by_scope(store, request)


@_close_failed_attempts
def _purge_with_writer(writer, store, request):
    return _purge_by_scope(store, request, writer=writer)


def _purge_by_scope(store, request, *, writer=None):
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
    params: dict[str, object] = {
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
            predicate = base + " AND attempt_id IN %(erase_attempts)s"
            params["erase_attempts"] = ids
            if request.claim_token is None:
                # collect_garbage runs an early, best-effort purge. It doesn't
                # issue receipts or wait for a previously dispatched write.
                for table in TABLES.values():
                    _delete(store, table, predicate, params)
            else:
                _scrub_erasure_rows(
                    store,
                    request,
                    [(table, predicate) for table in TABLES.values()],
                    params,
                    store_payload__attempt__in=[str(attempt) for attempt in ids],
                )
        return {"attempt_count": len(ids)}
    if kind in {"users", "sessions"}:
        if writer is None or str(writer.lease.project_id) != str(request.project_key):
            raise MetricStateConflict("subject erasure requires a project writer")
        with serialized_project(request.project_key):
            head = MetricProjectRefresh.for_project(request.project_key).head_manifest
            attempt = None
            if head is not None and head.revoked_at:
                raise MetricStateConflict(
                    "subject erasure requires a usable source manifest"
                )
            # Remaps can expand the subject set between claims. Never reuse a
            # checkpoint merely because an earlier claim carried this request ID.
            scope_digest = MetricErasureRequest._subject_digest(
                request.subject_ids, str(request.id)
            )
            if head is not None and (
                head.published_by.source_ceilings.get("erasure") != str(request.id)
                or head.published_by.source_ceilings.get("erasure_scope_digest")
                != scope_digest
            ):
                recovery_pending = head.published_by.source_ceilings.get(
                    "recovery_pending", False
                )
                handoff: dict[str, list] = {}
                if recovery_pending:
                    for chunk in head.published_by.ledger_chunks.exclude(
                        part="cleanup_intent"
                    ):
                        handoff.setdefault(chunk.part, []).extend(chunk.payload)
                attempt = writer.begin(
                    purpose="compaction",
                    ceilings={
                        "erasure": str(request.id),
                        "erasure_scope_digest": scope_digest,
                        "recovery_pending": recovery_pending,
                    },
                )
        if head is None:
            # No committed values exist to preserve. Scrub all unpublished
            # copies under the same generation fence and late-write barrier,
            # rather than leaving a paused first publication uneraseable.
            if not finish_unpublished_replan(writer, discard_drafts=True):
                raise MetricStateConflict("unpublished erasure drainage is pending")
            return {
                "unpublished_drafts_dropped": True,
                "anonymous_history_dropped": True,
            }
        store.assert_visible(head)
        if attempt is None:
            # A retry after publication needs only the physical phase.
            return _finish_subject_erasure(writer, store, request, head, base, params)
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
        handoff, erased_units = _erasure_handoff(
            store, handoff, request, {row[0] for row in victims}
        )
        # Persist the retained nominations BEFORE sealing the successor. No
        # source cursor is consumed by this copy-only privacy checkpoint.
        for part, entries in handoff.items():
            writer.append(attempt, part, entries)
        # An empty native array is a nonmatch; an empty-string sentinel would
        # instead match every unrelated sessionless dependency.
        params["erase_units"] = sorted(erased_units)
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
                f"({dep_predicate} OR canonical_session_id IN %(erase_units)s "
                "OR toJSONString(tuple(trace_id, span_id)) IN %(erase_units)s)"
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
        return _finish_subject_erasure(writer, store, request, manifest, base, params)
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
            # Discovery can precede attempt registration. Invalidate the old
            # lease before any native deletes; begin() fences new attempts
            # while the request runs, including copies from a still-live head.
            MetricProjectRefresh.objects.filter(project_id=project).update(
                lease_token=None, lease_expires_at=None, lease_holder=""
            )
            if kind in {"metric", "version"}:
                versions = DerivedMetricVersion.objects.filter(
                    metric_id=request.metric_key
                )
                if kind == "version":
                    versions = versions.filter(id=request.version_key)
                version_ids = list(versions.values_list("id", flat=True))
                revoke_generations(
                    project, reason="erased", definition_version_ids=version_ids
                )
                if not version_ids:
                    # After hard deletion there is no definition left to revoke.
                    # A writer may already have frozen its bytes from the head.
                    MetricRefreshAttempt.objects.filter(
                        project_id=project, state__in=MetricRefreshAttempt.OPEN_STATES
                    ).update(
                        state="aborted",
                        aborted_at=timezone.now(),
                        abort_reason="generation_revoked",
                    )
            else:
                revoke_generations(project, reason="erased")
            _purge_terminal_payloads(project)
    predicates = []
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
        predicates.append((table, predicate))
    _scrub_erasure_rows(store, request, predicates, params)
    return {"live_rows": "purged"}
