# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Compare the old and new second retirement probes on the same immutable inputs."""

from collections import Counter
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import MetricRefreshAttempt
from tracer.services.derived_metrics import frozen_relations, retention
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.retention import (
    ProposalCaptures,
    _span_owner_cleanup_required,
    retirement_plan,
)
from tracer.services.derived_metrics.span_evidence import _freeze_span_page
from tracer.services.derived_metrics.storage import FIELD_VALUES, SOURCE_DEPENDENCIES
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    default_row_select,
    field_query,
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import span_definition_for

pytestmark = pytest.mark.django_db(transaction=True)
CUTOFF = datetime(
    2026, 1, 31, 12, tzinfo=UTC
)  # Physical cutoffs bind at whole seconds.


def _field(
    writer,
    attempt,
    metric,
    unit,
    session,
    *,
    user="",
    level="span",
    version=1,
    start=CUTOFF,
    deleted=0,
    copies=1,
):
    sql, params = field_query(
        unit, metric=metric, start=start, version=version, user=user, deleted=deleted
    )
    return writer.freeze(
        attempt,
        FIELD_VALUES,
        f"SELECT * REPLACE(%(level)s AS level, %(owner)s AS session_id) "
        f"FROM ({sql} FROM numbers(%(copies)s))",
        {**params, "level": level, "owner": session, "copies": copies},
    )


def _publish(writer, attempt):
    writer.seal(attempt)
    return writer.stage_and_publish(attempt)


def _dependency(writer, attempt, session):
    writer.freeze(
        attempt,
        SOURCE_DEPENDENCIES,
        default_row_select(
            SOURCE_DEPENDENCIES,
            {
                "trace_id": "'trace'",
                "span_id": "'span'",
                "canonical_session_id": "%(session)s",
                "raw_user_id": "'linked'",
                "unit_start": "toDateTime64(%(start)s, 6, 'UTC')",
            },
        ),
        {"session": session, "start": CUTOFF},
    )


def _assert_witnesses(captures, cutoff, expected, *, inline=False):
    """Observe actual counts, then project the exact same WHERE onto logical keys.

    Both forms must match the oracle *per key*, including zero occurrences for
    missing keys. Equal totals from two different winning witnesses cannot pass.
    """
    writer, head = captures.writer, captures.head
    fields, params = (
        captures.fields()
    )  # Build the real typed overlay before stressing owners.
    budget = (
        patch.object(frozen_relations, "CAPTURE_ROWS", 0) if inline else nullcontext()
    )
    with budget:
        owners = captures.owners(cutoff)
    assert ("GROUP BY unit_id HAVING min(unit_start)" in owners) is inline
    params = {**params, "retire_cutoff": cutoff}
    execute = writer.store.execute
    wanted = Counter(
        (writer.org_id, writer.lease.project_id, metric, version, "span", unit)
        for metric, version, unit in expected
    )

    def check(select):
        counts = []
        witnesses = Counter()

        def observe(sql, bound=None, **kwargs):
            rows = execute(sql, bound, **kwargs)
            if bound and "span_owner_cutoff" in bound:
                counts.append(rows[0][0])
                if len(counts) == 2:
                    prefix = "SELECT count() FROM "
                    assert sql.startswith(prefix)
                    projection = ", ".join(FIELD_VALUES.logical_key)
                    witnesses.update(
                        execute(
                            f"SELECT {projection} FROM " + sql[len(prefix) :], bound
                        )
                    )
            return rows

        with patch.object(writer.store, "execute", observe):
            result = _span_owner_cleanup_required(
                writer, head, fields, params, cutoff, owners, span_fields=select
            )
        assert result is bool(expected)
        assert counts == [0, len(expected)]
        assert witnesses == wanted

    check(None)  # Original full proposal on both sides.
    check(captures.span_owner_fields)


def test_span_winners_and_all_owner_recipe_anchors(live_writer):
    writer, metric, other = live_writer, uuid4(), uuid4()
    old = CUTOFF - timedelta(seconds=1)
    base = writer.begin(purpose="compaction")
    for unit, version, at in (
        ("equal", 1, CUTOFF),
        ("split", 1, old),
        ("split", 2, CUTOFF),
        ("restored", 1, old),
        ("restored", 2, CUTOFF),
        ("lost", 1, CUTOFF),
        ("lost", 2, CUTOFF),
    ):
        _field(
            writer,
            base,
            metric,
            unit,
            unit,
            level="conversation",
            version=version,
            start=at,
        )
    for unit, owner in (
        ("move-out", "equal"),
        ("move-in", "absent"),
        ("empty-shadow", "absent"),
        ("split-span", "split"),
        ("restored-span", "restored"),
        ("lost-span", "lost"),
    ):
        _field(writer, base, metric, unit, owner)
    _publish(writer, base)
    delta = writer.begin()
    _field(writer, delta, metric, "move-out", "equal")
    _field(writer, delta, metric, "move-in", "absent")
    # Dep membership is individual-anchor membership, not the field probe's group min.
    _dependency(writer, delta, "split")
    _publish(writer, delta)
    # Distinct allowed attempts keep all of these revisions in the raw head
    # even after physical merges. The draft never mentions this key.
    for revision in range(8):
        latest = writer.begin()
        owner = "absent" if revision % 2 else "equal"
        (retry,) = _field(writer, latest, metric, "delta-only", owner)
        head = _publish(writer, latest)
    writer.store.insert(
        FIELD_VALUES, retry
    )  # Valid byte-identical maximum-rank replay.
    assert head.checkpoint_attempt_id == base.id
    assert len(head.allowed_attempts()) == 10

    draft = writer.begin()
    # Many distinct writer-assigned ranks at one key, including a later move
    # at the same generation. These are PG-frozen revisions, not fabricated ties.
    _field(writer, draft, metric, "move-in", "absent", copies=64)
    _field(writer, draft, metric, "move-in", "equal", user="still-linked")
    _field(writer, draft, metric, "move-out", "absent")
    _field(writer, draft, metric, "empty-shadow", "", deleted=1)
    _field(writer, draft, metric, "user-only", "", user="linked")
    _field(writer, draft, metric, "empty-live", "")
    _field(writer, draft, metric, "restored", "restored", level="conversation")
    _field(
        writer,
        draft,
        metric,
        "lost",
        "lost",
        level="conversation",
        version=2,
        start=old,
    )
    # Neither field_id nor field_version may be dropped from the winner key.
    _field(writer, draft, metric, "move-in", "absent", version=2)
    _field(writer, draft, other, "move-in", "absent")
    expected = [
        (metric, 1, "delta-only"),
        (metric, 1, "move-out"),
        (metric, 1, "split-span"),
        (metric, 1, "lost-span"),
        (metric, 1, "user-only"),
        (metric, 2, "move-in"),
        (other, 1, "move-in"),
    ]
    for inline in (False, True):
        with ProposalCaptures(writer, draft, head) as captures:
            _assert_witnesses(captures, CUTOFF, expected, inline=inline)


def test_typed_empty_and_draft_only_inputs(live_writer):
    writer, metric = live_writer, uuid4()
    draft = writer.begin()
    with ProposalCaptures(writer, draft, None) as captures:
        _assert_witnesses(captures, CUTOFF, [])
    _field(writer, draft, metric, "draft-only", "missing")
    with ProposalCaptures(writer, draft, None) as captures:
        _assert_witnesses(captures, CUTOFF, [(metric, 1, "draft-only")])
    head = _publish(writer, draft)
    empty = writer.begin()
    with ProposalCaptures(writer, empty, head) as captures:
        _assert_witnesses(captures, CUTOFF, [(metric, 1, "draft-only")])


def test_manifest_scope_pairs_and_late_rows_do_not_supply_spans_or_owners(live_writer):
    writer, metric = live_writer, uuid4()
    base = writer.begin(purpose="compaction")
    _field(writer, base, metric, "needs-owner", "missing")
    _publish(writer, base)
    first, retained = writer.begin(), "retained"
    _field(writer, first, metric, retained, retained, level="conversation")
    _publish(writer, first)
    second = writer.begin()
    _field(writer, second, metric, "stable", retained)
    head = _publish(writer, second)
    draft = writer.begin()
    separate = writer.begin()
    _field(writer, separate, metric, "other-prepared", "absent")
    _field(writer, separate, metric, "missing", "missing", level="conversation")
    late = writer.begin()
    payloads = _field(writer, late, metric, "stable", "absent")
    payloads += _field(writer, late, metric, "missing", "missing", level="conversation")
    writer.seal(late)
    MetricRefreshAttempt.abort(
        late.id, reason="injected_failure", **writer.lease.check()
    )
    for payload in payloads:
        writer.store.insert(
            FIELD_VALUES, payload
        )  # Arrives after head; ranks above it.

    # Synthetic rank control: an allowed *older generation* carries a larger
    # _version for the same span key. Neither baseline nor candidate can pick
    # it ahead of the actually newer generation (no conflicting equal ranks).
    writer.store.execute(
        f"INSERT INTO {FIELD_VALUES.name} SELECT * REPLACE(toUInt64(%(gen)s) AS generation, "
        f"toUUID(%(id)s) AS attempt_id) FROM (SELECT * FROM {FIELD_VALUES.name} "
        "WHERE level = 'span' AND attempt_id = toUUID(%(late_id)s))",
        {"gen": first.generation, "id": str(first.id), "late_id": str(late.id)},
    )
    # Copy those two contrary answers with one broken fence at a time. A
    # per-column IN of the two *listed* delta generations and IDs is not enough.
    for offset, (org, project, generation, attempt) in enumerate(
        (
            (uuid4(), writer.lease.project_id, second.generation, second.id),
            (writer.org_id, uuid4(), second.generation, second.id),
            (writer.org_id, writer.lease.project_id, second.generation, first.id),
            (writer.org_id, writer.lease.project_id, second.generation, late.id),
            (writer.org_id, writer.lease.project_id, late.generation, second.id),
        ),
        start=1,
    ):
        bound = {
            "org": str(org),
            "project": str(project),
            "gen": generation,
            "id": str(attempt),
            "late_gen": late.generation,
            "late_id": str(late.id),
            "offset": offset,
        }
        writer.store.execute(
            f"INSERT INTO {FIELD_VALUES.name} SELECT * REPLACE(toUUID(%(org)s) AS org_id, "
            "toUUID(%(project)s) AS project_id, toUInt64(%(gen)s) AS generation, "
            "toUUID(%(id)s) AS attempt_id, _version + %(offset)s AS _version, "
            "row_sequence + %(offset)s AS row_sequence) "
            f"FROM (SELECT * FROM {FIELD_VALUES.name} WHERE generation = %(late_gen)s "
            "AND attempt_id = toUUID(%(late_id)s))",
            bound,
        )
        assert writer.store.execute(
            f"SELECT count() FROM {FIELD_VALUES.name} WHERE org_id = toUUID(%(org)s) "
            "AND project_id = toUUID(%(project)s) AND generation = %(gen)s "
            "AND attempt_id = toUUID(%(id)s)",
            bound,
        ) == [(2,)]
    with ProposalCaptures(writer, draft, head) as captures:
        _assert_witnesses(captures, CUTOFF, [(metric, 1, "needs-owner")])


def test_dependency_witness_never_requests_span_input(live_writer):
    writer = live_writer
    delta = writer.begin()
    _dependency(writer, delta, "absent")
    head = _publish(writer, delta)
    with ProposalCaptures(writer, writer.begin(), head) as captures:
        fields, params = captures.fields()
        owners = captures.owners(CUTOFF)
        with patch.object(
            captures, "span_owner_fields", side_effect=AssertionError
        ) as select:
            assert _span_owner_cleanup_required(
                writer, head, fields, params, CUTOFF, owners, span_fields=select
            )
            select.assert_not_called()


def test_standalone_expiry_uses_only_committed_fields(
    live_writer, workspace, observe_project
):
    writer, now = live_writer, timezone.now().replace(microsecond=0)
    definition = span_definition_for(writer, workspace, observe_project)
    metric, version = definition.metric_id, definition.version
    base = writer.begin()
    _field(writer, base, metric, "span", "kept", version=version, start=now)
    _field(writer, base, metric, "kept", "kept", level="conversation", start=now)
    head = _publish(writer, base)
    with patch.object(
        retention, "_span_owner_cleanup_required", wraps=_span_owner_cleanup_required
    ) as probe:
        assert not retention.expire_project(
            writer, [definition], retention_days=lambda: 1, now=now
        )["purged"]
    probe.assert_called_once()
    args, kwargs = probe.call_args
    assert (
        len(args) == 5 and not kwargs
    )  # No proposal selector or captured-owner override.
    cutoff = args[-1]
    draft = writer.begin()
    _field(writer, draft, metric, "span", "unproven", version=version, start=now)
    with ProposalCaptures(writer, draft, head) as captures:
        _assert_witnesses(captures, cutoff, [(metric, version, "span")])
        assert not _span_owner_cleanup_required(
            *args
        )  # Same cutoff and head, no draft.


@pytest.mark.parametrize("committed", [False, True])
def test_nonempty_link_tombstone_is_the_only_witness(live_writer, committed):
    writer, metric = live_writer, uuid4()
    base = writer.begin()
    _field(writer, base, metric, "tombstone-only", "")
    head = _publish(writer, base)
    draft = writer.begin()
    with ProposalCaptures(writer, draft, head) as captures:
        _assert_witnesses(captures, CUTOFF, [])

    # A direct frozen-input control: dropping the tombstone leaves only the
    # empty-link predecessor. The real copied-link retraction is tested below.
    _field(writer, draft, metric, "tombstone-only", "absent", user="linked", deleted=1)
    if committed:
        head = _publish(writer, draft)
        draft = writer.begin()
    with ProposalCaptures(writer, draft, head) as captures:
        _assert_witnesses(captures, CUTOFF, [(metric, 1, "tombstone-only")])


def test_copied_link_retraction_triggers_privacy_checkpoint(
    live_writer, workspace, observe_project
):
    writer, now = live_writer, timezone.now().replace(microsecond=0)
    writer.lease.clock = lambda: now
    writer.lease.renew()
    definition = span_definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    at = (now - timedelta(hours=2)).replace(minute=0, second=0)
    cutoff = now - timedelta(days=1)
    metric, version = definition.metric_id, definition.version
    unit = '["trace","vanished"]'
    base = writer.begin()
    _field(
        writer, base, metric, unit, "absent", user="linked", start=at, version=version
    )
    head = _publish(writer, base)
    writer.retention_days = lambda: 1
    draft = writer.begin()
    # Run the real span writer: the source vanished from this quarter but the
    # committed span was retained. Do not manufacture a tombstone with empty links.
    _freeze_span_page(
        writer,
        draft,
        head,
        definition,
        scope,
        as_of=now,
        cutoff=cutoff,
        unit_range=("", None),
        floors={},
        dirty=[at],
    )
    with ProposalCaptures(writer, draft, head) as captures:
        overlay, bound = captures.overlay(FIELD_VALUES)
        assert writer.store.execute(
            f"SELECT is_deleted, session_id, user_id, value_number FROM ({overlay})",
            bound,
        ) == [(1, "absent", "linked", None)]
        _assert_witnesses(captures, cutoff, [(metric, version, unit)])
        with patch.object(
            retention,
            "_span_owner_cleanup_required",
            wraps=_span_owner_cleanup_required,
        ) as cleanup:
            successor = retirement_plan(
                writer, draft, head, [definition], cutoff, captures=captures
            )
        cleanup.assert_called_once()
        selector = cleanup.call_args.kwargs["span_fields"]
        assert selector.__self__ is captures
        assert selector.__func__ is ProposalCaptures.span_owner_fields
    assert (
        successor.purpose == "compaction"
        and successor.source_ceilings["logical_expiry"]
    )
    draft.refresh_from_db()
    assert draft.abort_reason == "retention_replan"
    cleaned = _publish(writer, successor)
    fields, params = committed_rows_select(
        FIELD_VALUES, cleaned, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    assert writer.store.execute(
        f"SELECT unit_id, is_deleted, session_id, user_id FROM ({fields})", params
    ) == [(unit, 1, "", "")]
