# ruff: noqa: F811 -- shared isolated fixtures
"""Real physical revision erasure, retirement and immediate read-guard proofs."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    MetricAttemptLedgerChunk,
    MetricProjectRefresh,
    MetricStateConflict,
)
from tracer.services.derived_metrics.deletion_protocol import capture_pin
from tracer.services.derived_metrics.publication import (
    committed_rows_select,
)
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.refresh import (
    MetricAdmissionUnproven,
    payload_select,
)
from tracer.services.derived_metrics.retention import (
    assert_capture_retained,
    expire_project,
    purge_by_scope,
    read_field_cells,
)
from tracer.services.derived_metrics.storage import (
    FIELD_VALUES,
    RETIRED_PARTS,
    SOURCE_DEPENDENCIES,
    UNIT_STATE,
)
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    current_fields,
    default_row_select,
    field_query,
    live_writer,
    metric_ch,
    prepare_field,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    bucket_rows,
    change_for,
    definition_for,
)


def numeric_definition(metric, *, level="conversation"):
    return SimpleNamespace(
        metric_id=metric,
        version=1,
        level=level,
        kind="custom",
        formula="mean(y)",
        inputs=[
            {
                "alias": "y",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "y",
                "reducer": "latest",
            }
        ],
        weighting="none",
        cohort_key="region",
        group_dims=[],
    )


def seed_fields_and_buckets(writer, definition, specs):
    attempt = writer.begin(
        ledger={
            "dirty_after": [
                {
                    "unit_id": unit,
                    "bucket_start": at.replace(
                        minute=at.minute // 15 * 15, second=0, microsecond=0
                    ),
                }
                for unit, at, n, user in specs
            ]
        }
    )
    payloads = []
    for unit, at, n, user in specs:
        sql, params = field_query(
            unit, n=n, start=at, metric=definition.metric_id, user=user
        )
        payloads += writer.freeze(attempt, FIELD_VALUES, sql, params)
        bound = {"seed_unit": unit, "seed_user": user, "seed_at": at}
        writer.freeze(
            attempt,
            UNIT_STATE,
            default_row_select(
                UNIT_STATE,
                {
                    "level": "'conversation'",
                    "unit_id": "%(seed_unit)s",
                    "first_seen": "toDateTime64(%(seed_at)s, 6, 'UTC')",
                    "due_at": "toDateTime64(%(seed_at)s, 6, 'UTC')",
                },
            ),
            bound,
        )
        writer.freeze(
            attempt,
            SOURCE_DEPENDENCIES,
            default_row_select(
                SOURCE_DEPENDENCIES,
                {
                    "trace_id": "%(seed_unit)s",
                    "span_id": "%(seed_unit)s",
                    "raw_session_id": "%(seed_unit)s",
                    "canonical_session_id": "%(seed_unit)s",
                    "raw_user_id": "%(seed_user)s",
                    "canonical_user_id": "%(seed_user)s",
                    "unit_start": "toDateTime64(%(seed_at)s, 6, 'UTC')",
                },
            ),
            bound,
        )
    overlay, p = payload_select(FIELD_VALUES, payloads, prefix="seed")
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    if definition.level != "user":
        writer._freeze_buckets(attempt, head, definition, overlay, p)
    writer.seal(attempt)
    return writer.stage_and_publish(attempt)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("level", ["conversation", "user"])
def test_partial_cutoff_all_revisions_and_horizon_expand_never_resurrects(
    live_writer, level
):
    writer = live_writer
    metric = uuid4()
    definition = numeric_definition(metric, level=level)
    now = timezone.now().replace(hour=12, minute=7, second=0, microsecond=0)
    old = now - timedelta(days=1, minutes=5)
    retained = now - timedelta(days=1) + timedelta(minutes=3)
    unit_old, unit_live = str(uuid4()), str(uuid4())
    user_old, user_live = str(uuid4()), str(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [(unit_old, old, 4, user_old), (unit_live, retained, 6, user_live)],
    )
    writer.compact()  # same sensitive payload in checkpoint AND an older delta
    pin = capture_pin(
        writer.lease.project_id,
        consumer_kind="snapshot",
        ttl=timedelta(minutes=5),
        now=now,
    )
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    cells = read_field_cells(
        writer.store,
        scope,
        pin,
        metric,
        1,
        [unit_old, unit_live],
        retention_days=lambda: 1,
        now=now,
    )
    expired = next(c for c in cells if c["unit_id"] == unit_old)
    assert (
        expired["value_number"] is None
        and expired["inputs"] == {}
        and expired["no_value_reason"] == "retention_expired"
    )
    with pytest.raises(MetricStateConflict, match="retention_expired"):
        assert_capture_retained(pin, [old, retained], retention_days=lambda: 1, now=now)
    result = expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    assert result["purged"] and not pin.is_usable()
    head = MetricProjectRefresh.objects.get(
        project_id=writer.lease.project_id
    ).head_manifest
    assert [r[0] for r in current_fields(writer, head)] == [unit_live]
    for table in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {table} WHERE unit_id = %(unit)s", {"unit": unit_old}
        ) == [(0,)]
    assert writer.store.execute(
        "SELECT count() FROM metric_source_dependencies WHERE canonical_session_id = %(unit)s",
        {"unit": unit_old},
    ) == [(0,)]
    assert writer.store.execute(
        "SELECT count() FROM metric_unit_state WHERE unit_id = %(unit)s",
        {"unit": unit_old},
    ) == [(0,)]
    assert not MetricAttemptLedgerChunk.objects.filter(
        attempt__project_id=writer.lease.project_id
    ).exists()
    retired_sql, rp = committed_rows_select(
        RETIRED_PARTS, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    retired_rows = writer.store.execute(
        f"SELECT unit_count, weighted_n FROM ({retired_sql}) WHERE is_deleted = 0", rp
    )
    if level == "conversation":
        assert retired_rows == []
        assert bucket_rows(writer, head) == []
        newer = seed_fields_and_buckets(
            writer, definition, [(unit_live, retained, 8, user_live)]
        )
        live = [r for r in bucket_rows(writer, newer) if not r[-1]]
        assert live == []  # boundary bucket is unavailable, not stale total
    else:
        assert retired_rows == [] and bucket_rows(writer, head) == []
    # An expanded horizon/unlimited mode is NOT restoration from old revisions.
    assert expire_project(writer, [definition], retention_days=lambda: 0, now=now)[
        "unlimited"
    ]
    assert all(
        row[0] != unit_old
        for row in current_fields(
            writer,
            MetricProjectRefresh.objects.get(
                project_id=writer.lease.project_id
            ).head_manifest,
        )
    )


@pytest.mark.django_db(transaction=True)
def test_expiry_purges_newer_anchor_revisions_of_the_expired_logical_unit(live_writer):
    writer = live_writer
    definition = numeric_definition(uuid4())
    unit, retained = str(uuid4()), str(uuid4())
    now = timezone.now()
    seed_fields_and_buckets(
        writer, definition, [(unit, now - timedelta(hours=12), 4, "user")]
    )
    writer.compact()
    seed_fields_and_buckets(
        writer,
        definition,
        [
            (unit, now - timedelta(days=3), 8, "user"),
            (retained, now - timedelta(hours=6), 6, "other"),
        ],
    )
    expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    for table in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {table} WHERE unit_id = %(unit)s", {"unit": unit}
        ) == [(0,)]
    head = MetricProjectRefresh.objects.get(
        project_id=writer.lease.project_id
    ).head_manifest
    assert [r[0] for r in current_fields(writer, head)] == [retained]


@pytest.mark.django_db(transaction=True)
def test_expiry_purges_recent_member_dependencies_of_an_old_conversation(live_writer):
    from tracer.services.derived_metrics.changes import Dependency

    writer = live_writer
    definition = numeric_definition(uuid4())
    unit, retained = str(uuid4()), str(uuid4())
    now = timezone.now()
    seed_fields_and_buckets(
        writer,
        definition,
        [
            (unit, now - timedelta(days=3), 4, "user"),
            (retained, now - timedelta(hours=6), 6, "other"),
        ],
    )
    attempt = writer.begin()
    writer.freeze_dependencies(
        attempt,
        [
            Dependency(
                "trace", "recent", unit, unit, "user", "user", now - timedelta(hours=12)
            ),
            Dependency(
                "trace",
                "unrelated",
                retained,
                retained,
                "other",
                "other",
                now - timedelta(hours=6),
            ),
        ],
    )
    writer.seal(attempt)
    writer.stage_and_publish(attempt)
    writer.compact()
    expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    assert writer.store.execute(
        "SELECT count() FROM metric_source_dependencies WHERE canonical_session_id = %(unit)s",
        {"unit": unit},
    ) == [(0,)]
    head = MetricProjectRefresh.objects.get(
        project_id=writer.lease.project_id
    ).head_manifest
    deps, params = committed_rows_select(
        SOURCE_DEPENDENCIES,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    assert writer.store.execute(
        f"SELECT DISTINCT canonical_session_id FROM ({deps})", params
    ) == [(retained,)]


@pytest.mark.django_db(transaction=True)
def test_purge_failure_does_not_rollback_to_deleted_evidence(live_writer):
    writer = live_writer
    definition = numeric_definition(uuid4())
    now = timezone.now()
    old, current = str(uuid4()), str(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [(old, now - timedelta(days=3), 4, "a"), (current, now, 6, "b")],
    )
    pin = capture_pin(
        writer.lease.project_id, consumer_kind="snapshot", ttl=timedelta(minutes=5)
    )
    original, failed = writer.store.execute, []

    def fail(sql, *args, **kwargs):
        if sql.startswith("ALTER TABLE metric_field_values_checkpoint") and not failed:
            failed.append(True)
            raise RuntimeError("injected purge interruption")
        return original(sql, *args, **kwargs)

    writer.store.execute = fail
    with pytest.raises(RuntimeError):
        expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    writer.store.execute = original
    assert not pin.is_usable()
    safe_head = MetricProjectRefresh.objects.get(
        project_id=writer.lease.project_id
    ).head_manifest
    assert [r[0] for r in current_fields(writer, safe_head)] == [current]
    assert expire_project(writer, [definition], retention_days=lambda: 1, now=now)[
        "purged"
    ]
    assert writer.store.execute(
        "SELECT count() FROM metric_field_values WHERE unit_id = %(unit)s",
        {"unit": old},
    ) == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_one_user_erasure_keeps_other_live_rows_and_drops_anonymous_history(
    live_writer,
):
    writer = live_writer
    definition = numeric_definition(uuid4(), level="user")
    first, second, user1, user2 = (str(uuid4()) for _ in range(4))
    now = timezone.now()
    seed_fields_and_buckets(
        writer,
        definition,
        [
            (first, now - timedelta(hours=2), 4, user1),
            (second, now - timedelta(hours=2), 6, user2),
        ],
    )
    writer.compact()
    request = SimpleNamespace(
        id=uuid4(),
        organization_key=writer.org_id,
        project_key=writer.lease.project_id,
        scope_kind="users",
        subject_ids=[user1],
    )
    result = purge_by_scope(writer.store, request, writer=writer)
    assert result["anonymous_history_dropped"]
    head = MetricProjectRefresh.objects.get(
        project_id=writer.lease.project_id
    ).head_manifest
    assert [r[0] for r in current_fields(writer, head)] == [second]
    for table in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {table} WHERE user_id = %(user)s", {"user": user1}
        ) == [(0,)]
    assert bucket_rows(writer, head) == []


@pytest.mark.django_db(transaction=True)
def test_a_paused_project_still_retires_expired_units(
    live_writer, workspace, observe_project
):
    """Privacy work never pauses: a project paused as too large gets an
    expiry-only pass, which retires expired evidence and calculates nothing."""
    from tracer.models.derived_metric import DerivedMetricVersion
    from tracer.services.derived_metrics import admission

    writer = live_writer
    definition = definition_for(writer, workspace, observe_project)
    template = numeric_definition(definition.metric_id)
    DerivedMetricVersion.objects.filter(id=definition.id).update(
        formula=template.formula, inputs=template.inputs
    )
    definition.refresh_from_db()
    now = timezone.now()
    old, live = str(uuid4()), str(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [
            (old, now - timedelta(days=2), 4, ""),
            (live, now - timedelta(hours=1), 6, ""),
        ],
    )
    writer.compact()
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        paused_reason="too_large", paused_at=now, paused_rows=10
    )
    before = MetricProjectRefresh.for_project(observe_project.id)
    writer.retention_days = lambda: 1
    admission.run_expiry_pass(writer)
    head = MetricProjectRefresh.for_project(observe_project.id).head_manifest
    assert head.id != before.head_manifest_id
    assert head.published_by.source_ceilings.get("logical_expiry")
    assert [r[0] for r in current_fields(writer, head) if not r[2]] == [live]
    # Nothing new was calculated: the watermark did not move.
    assert (
        MetricProjectRefresh.for_project(observe_project.id).watermark_ns
        == before.watermark_ns
    )


@pytest.mark.django_db(transaction=True)
def test_a_due_expiry_runs_as_its_own_pass_and_only_when_due(
    live_writer, workspace, observe_project
):
    """A scheduled pass whose head holds a row past the physical cutoff does
    the expiry rewrite and nothing else; once retired, it is no longer due."""
    from tracer.models.derived_metric import DerivedMetricVersion
    from tracer.services.derived_metrics import admission

    writer = live_writer
    definition = definition_for(writer, workspace, observe_project)
    template = numeric_definition(definition.metric_id)
    DerivedMetricVersion.objects.filter(id=definition.id).update(
        formula=template.formula, inputs=template.inputs
    )
    definition.refresh_from_db()
    now = timezone.now()
    old, live = str(uuid4()), str(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [
            (old, now - timedelta(days=2), 4, ""),
            (live, now - timedelta(hours=1), 6, ""),
        ],
    )

    def head():
        return MetricProjectRefresh.for_project(observe_project.id).head_manifest

    writer.retention_days = lambda: 0
    assert admission.run_due_expiry(writer, head()) is False  # unlimited
    writer.retention_days = lambda: 1
    assert admission.run_due_expiry(writer, head()) is True
    assert [r[0] for r in current_fields(writer, head()) if not r[2]] == [live]
    assert admission.run_due_expiry(writer, head()) is False


@pytest.mark.django_db(transaction=True)
def test_reads_mask_a_unit_that_expired_inside_the_cutoffs_second(live_writer):
    """A datetime parameter binds to whole seconds, so a read kept a unit that
    started within the cutoff's second but before the exact cutoff, then
    refused the whole read (retention_expired) on its own final check. Reads
    bind the exact cutoff: that unit is masked and the read succeeds."""
    writer = live_writer
    metric = uuid4()
    definition = numeric_definition(metric)
    now = timezone.now().replace(microsecond=750_000)
    inside = (now - timedelta(days=1)).replace(microsecond=250_000)
    unit_old, unit_live = str(uuid4()), str(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [(unit_old, inside, 4, ""), (unit_live, now - timedelta(hours=1), 6, "")],
    )
    pin = capture_pin(
        writer.lease.project_id,
        consumer_kind="snapshot",
        ttl=timedelta(minutes=5),
        now=now,
    )
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    cells = read_field_cells(
        writer.store,
        scope,
        pin,
        metric,
        1,
        [unit_old, unit_live],
        retention_days=lambda: 1,
        now=now,
    )
    by_unit = {c["unit_id"]: c for c in cells}
    assert by_unit[unit_old]["no_value_reason"] == "retention_expired"
    assert by_unit[unit_old]["value_number"] is None
    assert by_unit[unit_live]["value_number"] == 6


@pytest.mark.django_db(transaction=True)
def test_expiry_publishes_when_a_unit_starts_inside_the_cutoffs_second(
    live_writer,
):
    """clickhouse-driver binds a datetime parameter to whole seconds. With a
    sub-second cutoff, ClickHouse kept a unit that started earlier in the
    cutoff's second while publication counted it as expired, so the expiry
    failed (MetricRetentionChanged) on every busy project. Writers use a
    whole-second cutoff: the unit is kept (at most a second younger than the
    policy) and the expiry publishes; reads still mask it at the exact cutoff.
    """
    writer = live_writer
    definition = numeric_definition(uuid4())
    now = timezone.now().replace(microsecond=750_000)
    boundary = (now - timedelta(days=1)).replace(microsecond=250_000)
    old, kept = str(uuid4()), str(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [
            (old, now - timedelta(days=2), 4, ""),
            (kept, boundary, 6, ""),
        ],
    )
    writer.compact()
    result = expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    assert result["purged"]
    head = MetricProjectRefresh.objects.get(
        project_id=writer.lease.project_id
    ).head_manifest
    assert [r[0] for r in current_fields(writer, head) if not r[2]] == [kept]


@pytest.mark.django_db(transaction=True)
def test_physical_expiry_waits_its_interval_while_reads_and_erasure_stay_exact(
    live_writer, workspace, observe_project
):
    """E1: a continuous cutoff no longer rewrites the head on every pass.

    Physical expiry runs at most once per METRICS_EXPIRY_INTERVAL_SECONDS
    (default one day): within the interval the head keeps the last expiry's
    floor, a unit that has since aged out stays stored but every read masks it
    at the exact cutoff, and a session erasure still removes its rows at once.
    After the interval the next pass retires it.
    """
    from datetime import UTC, datetime

    from tracer.tests.test_derived_metric_refresh_clickhouse import seed_span_revision

    writer = live_writer
    start = datetime(2026, 10, 28, 12, tzinfo=UTC)
    clock = [start]
    writer.lease.clock = lambda: clock[0]
    writer.lease.renew()
    writer.retention_days = lambda: 1
    definition = definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    kwargs = {"start": start - timedelta(days=2), "end": start + timedelta(days=3)}
    sessions = {}

    def seed(name, at, value):
        sessions[name] = uuid4()
        seed_span_revision(
            writer, scope, uuid4(), name, sessions[name], at, value, revision=1
        )

    def run(at, name, cursor):
        if at - clock[0] >= timedelta(minutes=4):
            # Hours pass between these passes: the last lease has expired, so
            # the next pass takes a new one, as the scheduler would.
            grant = MetricProjectRefresh.take_lease(
                scope.project, holder="e1", ttl=timedelta(minutes=5), now=at
            )
            # In place: the lifecycle lock budget holds this lease object.
            writer.lease.grant, writer.lease.expires_at = grant, grant.expires_at
        clock[0] = at
        writer.lease.renew()
        return writer.refresh(
            change_for(sessions[name], cursor=cursor),
            [definition],
            scope,
            as_of=at,
            **kwargs,
        )

    def live(head):
        return {row[0] for row in current_fields(writer, head) if not row[2]}

    seed("first", start - timedelta(hours=23), 4)
    seed("second", start - timedelta(hours=20), 6)
    run(start, "first", 1)
    head = run(start, "second", 2)
    assert live(head) == {str(sessions["first"]), str(sessions["second"])}

    # +2 h: "first" is past the cutoff and nothing has expired yet: retired now.
    seed("third", start + timedelta(hours=1), 8)
    head = run(start + timedelta(hours=2), "third", 3)
    assert head.published_by.source_ceilings.get("logical_expiry")
    floor = head.published_by.source_ceilings["retention_floor"]
    assert live(head) == {str(sessions["second"]), str(sessions["third"])}

    # +6 h: "second" is past the exact cutoff, but within one interval of the
    # last expiry: stored as is (no rewrite), masked for every read.
    seed("fourth", start + timedelta(hours=5), 2)
    head = run(start + timedelta(hours=6), "fourth", 4)
    assert not head.published_by.source_ceilings.get("logical_expiry")
    assert head.published_by.source_ceilings["retention_floor"] == floor
    assert str(sessions["second"]) in live(head)
    pin = capture_pin(
        scope.project, consumer_kind="request", ttl=timedelta(minutes=5), now=clock[0]
    )
    (cell,) = read_field_cells(
        writer.store,
        scope,
        pin,
        definition.metric_id,
        definition.version,
        [str(sessions["second"])],
        retention_days=lambda: 1,
        now=clock[0],
    )
    assert cell["no_value_reason"] == "retention_expired"
    assert cell["value_number"] is None

    # Erasure is not batched: a session erasure inside the interval is immediate.
    purge_by_scope(
        writer.store,
        SimpleNamespace(
            id=uuid4(),
            organization_key=writer.org_id,
            project_key=writer.lease.project_id,
            scope_kind="sessions",
            subject_ids=[str(sessions["fourth"])],
        ),
        writer=writer,
    )
    head = MetricProjectRefresh.for_project(scope.project).head_manifest
    assert str(sessions["fourth"]) not in live(head)
    for table in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {table} WHERE unit_id = %(unit)s",
            {"unit": str(sessions["fourth"])},
        ) == [(0,)]

    # One interval after the last expiry, the next pass retires "second".
    seed("fifth", start + timedelta(hours=25), 1)
    head = run(start + timedelta(hours=26, minutes=1), "fifth", 5)
    assert head.published_by.source_ceilings.get("logical_expiry")
    assert head.published_by.source_ceilings["retention_floor"] > floor
    assert str(sessions["second"]) not in live(head)
    assert str(sessions["fifth"]) in live(head)


@pytest.mark.django_db(transaction=True)
def test_larger_horizon_and_incremental_refresh_cannot_restore_purged_evidence(
    live_writer, workspace, observe_project
):
    from tracer.tests.test_derived_metric_clickhouse import seed_span

    writer = live_writer
    definition = definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    session, user = uuid4(), uuid4()
    now = timezone.now()
    at = now - timedelta(days=3)
    seed_span(writer.store.client, scope, "old-evidence", session, user, at, resolved=4)
    writer.refresh(
        change_for(session),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=now,
        as_of=now,
    )
    expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    # Sources intentionally still exist. Restoration must be an explicit fresh
    # backfill with a reviewed anonymous-history policy, not an ordinary delta.
    writer.retention_days = lambda: 0
    head = writer.refresh(
        change_for(session, cursor=2),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=now,
        as_of=now,
    )
    assert current_fields(writer, head) == []
    assert head.published_by.source_ceilings["retention_floor"]
    assert writer.store.execute(
        "SELECT count() FROM spans WHERE id = 'old-evidence'"
    ) == [(1,)]
    assert bucket_rows(writer, head) == []


@pytest.mark.django_db(transaction=True)
def test_late_earlier_span_expires_the_previously_retained_conversation(
    live_writer, workspace, observe_project
):
    from tracer.tests.test_derived_metric_clickhouse import seed_span

    writer = live_writer
    writer.retention_days = lambda: 1
    definition = definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    session, user = uuid4(), uuid4()
    now = timezone.now()
    recent, old = now - timedelta(hours=12), now - timedelta(days=3)
    seed_span(writer.store.client, scope, "recent", session, user, recent, resolved=4)
    writer.refresh(
        change_for(session),
        [definition],
        scope,
        start=old - timedelta(days=1),
        end=now,
        as_of=now,
    )
    seed_span(
        writer.store.client, scope, "late-earlier", session, user, old, resolved=6
    )
    head = writer.refresh(
        change_for(session, cursor=2),
        [definition],
        scope,
        start=old - timedelta(days=1),
        end=now,
        as_of=now,
    )
    assert current_fields(writer, head) == []
    assert writer.store.execute(
        "SELECT count() FROM metric_field_values_checkpoint WHERE unit_id = %(unit)s",
        {"unit": str(session)},
    ) == [(0,)]
    again = writer.refresh(
        change_for(session, cursor=3),
        [definition],
        scope,
        start=old - timedelta(days=1),
        end=now,
        as_of=now,
    )
    assert current_fields(writer, again) == []


@pytest.mark.django_db(transaction=True)
def test_finite_retention_takeover_keeps_candidates_exclusive_to_the_failed_attempt(
    live_writer, workspace, observe_project
):
    from tracer.services.derived_metrics.changes import ChangeBatch
    from tracer.services.derived_metrics.lease import ProjectLease
    from tracer.services.derived_metrics.refresh import RefreshWriter
    from tracer.tests.test_derived_metric_clickhouse import seed_span

    writer = live_writer
    definition = definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    session, user = uuid4(), uuid4()
    now = timezone.now()
    at = now - timedelta(hours=12)
    seed_span(writer.store.client, scope, "first", session, user, at, resolved=4)
    writer.refresh(
        change_for(session),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=now,
        as_of=now,
    )
    seed_span(
        writer.store.client,
        scope,
        "later",
        session,
        user,
        at + timedelta(hours=1),
        resolved=8,
        trace_id="trace",
    )
    failed = writer.begin(
        ledger={
            "dirty_before": [{"unit_id": str(session)}],
            "membership_preimage": [
                {
                    "trace_id": "trace",
                    "span_id": "later",
                    "canonical_session_id": str(session),
                }
            ],
        }
    )
    writer.seal(failed)
    future = now + timedelta(minutes=6)
    grant = MetricProjectRefresh.take_lease(
        writer.lease.project_id, holder="takeover", ttl=timedelta(minutes=5), now=future
    )
    replacement = RefreshWriter(
        ProjectLease(grant, clock=lambda: future),
        writer.store,
        org_id=writer.org_id,
        workspace_id=writer.workspace_id,
        retention_days=lambda: 1,
    )
    head = replacement.refresh(
        ChangeBatch((), (), (), (), (), {"watermark_ns": 2}),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=now,
        as_of=now,
    )
    assert current_fields(replacement, head)[0][1] == 8.0


@pytest.mark.django_db(transaction=True)
@pytest.mark.xfail(
    strict=True,
    raises=MetricAdmissionUnproven,
    reason="TH-8975: retained conversation admission raises MetricAdmissionUnproven",
)
@pytest.mark.parametrize("prune_before_expiry", [False, True])
def test_expired_active_conversation_is_not_reanchored_after_source_retention(
    live_writer, workspace, observe_project, prune_before_expiry
):
    from tracer.tests.test_derived_metric_clickhouse import seed_span

    writer = live_writer
    definition = definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    session, user = uuid4(), uuid4()
    now = timezone.now()
    at = now - timedelta(days=3)
    seed_span(
        writer.store.client, scope, "expired-anchor", session, user, at, resolved=4
    )
    seed_span(
        writer.store.client,
        scope,
        "retained-member",
        session,
        user,
        now - timedelta(hours=12),
        resolved=6,
    )
    writer.refresh(
        change_for(session),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=now,
        as_of=now,
    )
    if not prune_before_expiry:
        expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    writer.store.execute(
        "ALTER TABLE spans DELETE WHERE id = 'expired-anchor' SETTINGS mutations_sync=2"
    )
    writer.retention_days = lambda: 1
    head = writer.refresh(
        change_for(session, cursor=2),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=now,
        as_of=now,
    )
    # The same old-start conversation is still expired. Treating its remaining
    # member as a new anchor would restore expired evidence in a newer bucket.
    assert (
        len(current_fields(writer, head)),
        sum(r[2] for r in bucket_rows(writer, head) if not r[-1]),
    ) == (0, 0)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("interruption", ["during_transfer", "after_purge"])
def test_retained_recovery_handoff_survives_interruption_compaction_and_gc(
    live_writer, workspace, observe_project, monkeypatch, interruption
):
    from tracer.services.derived_metrics import retention
    from tracer.services.derived_metrics.changes import ChangeBatch
    from tracer.services.derived_metrics.lease import ProjectLease
    from tracer.services.derived_metrics.refresh import RefreshWriter
    from tracer.tests.test_derived_metric_clickhouse import seed_span

    writer = live_writer
    definition = definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    session, user = uuid4(), uuid4()
    now = timezone.now()
    at = now - timedelta(hours=12)
    seed_span(writer.store.client, scope, "first", session, user, at, resolved=4)
    kwargs = {"start": at - timedelta(days=1), "end": now, "as_of": now}
    writer.refresh(change_for(session), [definition], scope, **kwargs)
    seed_span(
        writer.store.client,
        scope,
        "exclusive",
        session,
        user,
        at + timedelta(hours=1),
        resolved=8,
        trace_id="trace",
    )
    failed = writer.begin(
        ledger={
            "dirty_before": [
                {
                    "unit_id": str(session),
                    "bucket_start": at.replace(
                        minute=at.minute // 15 * 15, second=0, microsecond=0
                    ),
                }
            ],
            "membership_preimage": [
                {
                    "trace_id": "trace",
                    "span_id": "exclusive",
                    "canonical_session_id": str(session),
                }
            ],
        }
    )
    writer.seal(failed)
    future = now + timedelta(minutes=6)
    grant = MetricProjectRefresh.take_lease(
        writer.lease.project_id,
        holder="handoff-takeover",
        ttl=timedelta(minutes=5),
        now=future,
    )
    replacement = RefreshWriter(
        ProjectLease(grant, clock=lambda: future),
        writer.store,
        org_id=writer.org_id,
        workspace_id=writer.workspace_id,
        retention_days=lambda: 1,
    )
    raised = []
    original_append = replacement.append
    original_purge = retention._purge_terminal_payloads

    def interrupt_append(attempt, part, entries):
        original_append(attempt, part, entries)
        if attempt.source_ceilings.get("recovery_pending") and not raised:
            raised.append(True)
            raise RuntimeError("injected durable handoff interruption")

    def interrupt_purge(*args, **kwargs):
        result = original_purge(*args, **kwargs)
        if not raised:
            raised.append(True)
            raise RuntimeError("injected durable handoff interruption")
        return result

    if interruption == "during_transfer":
        monkeypatch.setattr(replacement, "append", interrupt_append)
    else:
        monkeypatch.setattr(retention, "_purge_terminal_payloads", interrupt_purge)
    empty = ChangeBatch((), (), (), (), (), {"watermark_ns": 2})
    with pytest.raises(RuntimeError, match="durable handoff interruption"):
        replacement.refresh(empty, [definition], scope, **kwargs)
    monkeypatch.setattr(replacement, "append", original_append)
    monkeypatch.setattr(retention, "_purge_terminal_payloads", original_purge)
    if interruption == "during_transfer":
        failed.refresh_from_db()
        assert failed.state == "prepared" and failed.ledger_chunks.exists()
    else:
        # Checkpoint/GC must carry the private handoff until the writer consumes
        # it, rather than assuming publication alone means its work was done.
        replacement.compact()
        replacement.collect_garbage()
        pending = MetricProjectRefresh.objects.get(
            project_id=scope.project
        ).head_manifest.published_by
        assert pending.source_ceilings["recovery_pending"]
        assert pending.ledger_chunks.filter(part="membership_preimage").exists()
    assert MetricProjectRefresh.objects.get(project_id=scope.project).watermark_ns == 1
    head = replacement.refresh(empty, [definition], scope, **kwargs)
    assert current_fields(replacement, head)[0][1] == 8.0
    assert MetricProjectRefresh.objects.get(project_id=scope.project).watermark_ns == 2
    assert not MetricAttemptLedgerChunk.objects.filter(
        attempt__project_id=scope.project
    ).exists()


def history_fixture(writer, workspace, project, *, finite=True):
    """Event, arrival, evaluation and lease clocks are explicit and independent."""
    from datetime import UTC, datetime

    from tracer.tests.test_derived_metric_refresh_clickhouse import seed_span_revision

    now = datetime(2026, 10, 28, 12, tzinfo=UTC)
    writer.lease.clock = lambda: now
    writer.lease.renew()
    writer.retention_days = lambda: 1 if finite else 0
    definition = definition_for(writer, workspace, project)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    session, trace = uuid4(), uuid4()
    recent, old = now - timedelta(hours=12), now - timedelta(days=3)
    seed_span_revision(writer, scope, trace, "recent", session, recent, 4, revision=1)
    kwargs = {"start": old - timedelta(days=1), "end": now + timedelta(days=1)}
    head = writer.refresh(
        change_for(session),
        [definition],
        scope,
        as_of=now - timedelta(minutes=1),
        **kwargs,
    )
    return definition, scope, session, trace, now, recent, old, kwargs, head


def anonymous_parts(writer, head):
    sql, params = committed_rows_select(
        RETIRED_PARTS, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    return writer.store.execute(
        f"SELECT bucket_start, unit_count, weighted_n, weighted_d FROM ({sql}) WHERE is_deleted = 0 ORDER BY bucket_start",
        params,
    )


@pytest.mark.django_db(transaction=True)
def test_expiry_publication_revokes_recent_anchor_pin_and_queued_snapshot_before_purge(
    live_writer, workspace, observe_project, monkeypatch
):
    from tracer.services.derived_metrics import retention
    from tracer.services.derived_metrics.publication import pinned_read
    from tracer.tests.test_derived_metric_refresh_clickhouse import seed_span_revision

    writer = live_writer
    definition, scope, session, trace, now, recent, old, kwargs, original = (
        history_fixture(writer, workspace, observe_project)
    )
    pins = [
        capture_pin(scope.project, consumer_kind=kind, ttl=timedelta(hours=1), now=now)
        for kind in ("request", "snapshot")
    ]
    stale, _ = prepare_field(
        writer, metric=definition.metric_id, unit_id=str(session), start=recent
    )
    seed_span_revision(writer, scope, trace, "earlier", session, old, 6, revision=1)
    original_purge, paused = retention.purge_checkpoint, []

    def before_purge(w, attempt, manifest, **options):
        if not current_fields(w, manifest):
            paused.append(manifest)
            # The previous PRIVATE row still exists with its misleading recent
            # anchor, but cannot be delivered or used to complete a queued copy.
            # It is a delta row or, once a compaction ran, a checkpoint row.
            assert (
                w.store.execute(
                    "SELECT (SELECT count() FROM metric_field_values WHERE unit_id = %(unit)s)"
                    " + (SELECT count() FROM metric_field_values_checkpoint WHERE unit_id = %(unit)s)",
                    {"unit": str(session)},
                )[0][0]
                > 0
            )
            for pin in pins:
                assert not pin.is_usable(now)
                with pytest.raises(MetricStateConflict, match="expired"):
                    with pinned_read(w.store, scope.project, pin=pin, now=now):
                        pytest.fail("revoked result delivered")
            fresh = capture_pin(
                scope.project,
                consumer_kind="request",
                ttl=timedelta(minutes=1),
                now=now,
            )
            assert fresh.manifest_id == manifest.id != original.id
            with pytest.raises(MetricStateConflict):
                w.stage_and_publish(stale)
            raise RuntimeError("crash after atomic expiry commit")
        return original_purge(w, attempt, manifest, **options)

    monkeypatch.setattr(retention, "purge_checkpoint", before_purge)
    with pytest.raises(RuntimeError, match="atomic expiry commit"):
        writer.refresh(
            change_for(session, cursor=2), [definition], scope, as_of=now, **kwargs
        )
    assert paused
    safe = paused[0]
    assert current_fields(writer, safe) == []
    assert anonymous_parts(writer, safe) == []
    assert all(
        r[-1] for r in bucket_rows(writer, safe) if r[0] == recent.replace(minute=0)
    )
    monkeypatch.setattr(retention, "purge_checkpoint", original_purge)
    retention.purge_checkpoint(writer, safe.published_by, safe)
    for table, key in [
        (FIELD_VALUES, "unit_id"),
        (UNIT_STATE, "unit_id"),
        (SOURCE_DEPENDENCIES, "canonical_session_id"),
    ]:
        assert writer.store.execute(
            f"SELECT count() FROM {table.name} WHERE {key} = %(unit)s",
            {"unit": str(session)},
        ) == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_shrink_then_expansion_never_reactivates_expiry_revoked_pins(
    live_writer, monkeypatch
):
    from tracer.services.derived_metrics import retention
    from tracer.services.derived_metrics.publication import pinned_read

    writer, now = live_writer, timezone.now()
    definition, unit = numeric_definition(uuid4()), str(uuid4())
    original = seed_fields_and_buckets(
        writer, definition, [(unit, now - timedelta(days=3), 7, "user")]
    )
    pins = [
        capture_pin(
            writer.lease.project_id, consumer_kind=kind, ttl=timedelta(hours=1), now=now
        )
        for kind in ("request", "snapshot")
    ]
    deletion = retention._delete
    monkeypatch.setattr(
        retention,
        "_delete",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("before physical purge")),
    )
    with pytest.raises(RuntimeError, match="before physical purge"):
        expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    # Extending the policy cannot bypass an already-durable physical debt.
    with pytest.raises(RuntimeError, match="before physical purge"):
        expire_project(writer, [definition], retention_days=lambda: 0, now=now)
    original.refresh_from_db()
    assert original.revoked_at is not None
    for pin in pins:
        assert not pin.is_usable(now)
        with pytest.raises(MetricStateConflict, match="expired"):
            with pinned_read(writer.store, writer.lease.project_id, pin=pin, now=now):
                pytest.fail("horizon expansion restored an old capture")
    monkeypatch.setattr(retention, "_delete", deletion)
    assert expire_project(writer, [definition], retention_days=lambda: 0, now=now)[
        "unlimited"
    ]
    writer.compact()
    writer.collect_garbage()
    assert all(not pin.is_usable(now) for pin in pins)


@pytest.mark.django_db(transaction=True)
def test_fresh_earlier_arrival_expires_reopened_conversation_without_retirement(
    live_writer, workspace, observe_project
):
    from tracer.tests.test_derived_metric_refresh_clickhouse import seed_span_revision

    writer = live_writer
    definition, scope, session, trace, now, recent, old, kwargs, _ = history_fixture(
        writer, workspace, observe_project
    )
    seed_span_revision(
        writer, scope, trace, "earlier", session, old, 6, revision=1, arrival=now
    )
    head = writer.refresh(
        change_for(session, cursor=2), [definition], scope, as_of=now, **kwargs
    )
    assert current_fields(writer, head) == []
    assert anonymous_parts(writer, head) == []
    assert all(r[-1] for r in bucket_rows(writer, head))
    for table in (
        "metric_field_values",
        "metric_field_values_checkpoint",
        "metric_unit_state",
    ):
        assert writer.store.execute(
            f"SELECT count() FROM {table} WHERE unit_id = %(unit)s",
            {"unit": str(session)},
        ) == [(0,)]
    assert not MetricAttemptLedgerChunk.objects.filter(
        attempt__project_id=scope.project
    ).exists()
    head = writer.refresh(
        change_for(session, cursor=3),
        [definition],
        scope,
        as_of=now + timedelta(minutes=30),
        **kwargs,
    )
    assert current_fields(writer, head) == [] and anonymous_parts(writer, head) == []
    assert all(r[-1] for r in bucket_rows(writer, head))


@pytest.mark.django_db(transaction=True)
def test_fresh_arrival_takeover_stays_in_progress_until_due_settle(
    live_writer, workspace, observe_project
):
    from tracer.models.derived_metric import MetricRefreshAttempt
    from tracer.services.derived_metrics.changes import ChangeBatch, Dependency
    from tracer.services.derived_metrics.lease import ProjectLease
    from tracer.services.derived_metrics.refresh import RefreshWriter
    from tracer.tests.test_derived_metric_refresh_clickhouse import seed_span_revision

    writer = live_writer
    definition, scope, session, trace, now, recent, old, kwargs, _ = history_fixture(
        writer, workspace, observe_project, finite=False
    )
    later = recent + timedelta(hours=1)
    seed_span_revision(
        writer, scope, trace, "later", session, later, 8, revision=1, arrival=now
    )
    failed = writer.begin(
        ledger={
            "dirty_before": [{"unit_id": str(session), "bucket_start": recent}],
            "membership_preimage": [
                Dependency(
                    str(trace), "later", str(session), str(session), "", "", later
                ).payload()
            ],
        }
    )
    writer.seal(failed)
    lease_clock = now + timedelta(minutes=6)
    grant = MetricProjectRefresh.take_lease(
        scope.project,
        holder="fresh-takeover",
        ttl=timedelta(minutes=5),
        now=lease_clock,
    )
    replacement = RefreshWriter(
        ProjectLease(grant, clock=lambda: lease_clock),
        writer.store,
        org_id=writer.org_id,
        workspace_id=writer.workspace_id,
        retention_days=lambda: 1,
    )
    empty = ChangeBatch((), (), (), (), (), {"watermark_ns": 2})
    head = replacement.refresh(empty, [definition], scope, as_of=lease_clock, **kwargs)
    assert current_fields(replacement, head)[0][1] is None
    assert all(r[-1] for r in bucket_rows(replacement, head))
    state, p = committed_rows_select(
        UNIT_STATE, head, org_id=writer.org_id, project_id=scope.project
    )
    assert writer.store.execute(f"SELECT due_at, is_deleted FROM ({state})", p) == [
        (now + timedelta(minutes=30), 0)
    ]
    for tick, cursor in [
        (now + timedelta(minutes=30) - timedelta(seconds=1), 3),
        (now + timedelta(minutes=30), 4),
    ]:
        head = replacement.refresh(
            change_for(session, cursor=cursor),
            [definition],
            scope,
            as_of=tick,
            **kwargs,
        )
        if cursor == 3:
            assert current_fields(replacement, head)[0][1] is None
            assert all(r[-1] for r in bucket_rows(replacement, head))
        else:
            assert current_fields(replacement, head)[0][1] == 8.0
            assert [r[2:5] for r in bucket_rows(replacement, head) if not r[-1]] == [
                (1, 8.0, 1.0)
            ]
    failed.refresh_from_db()
    assert failed.state == MetricRefreshAttempt.State.ABORTED


@pytest.mark.django_db(transaction=True)
def test_mixed_expiry_handoff_preserves_only_retained_owner_preimages(live_writer):
    from tracer.services.derived_metrics.changes import Dependency

    writer, now = live_writer, timezone.now()
    expired, retained, trace = str(uuid4()), str(uuid4()), str(uuid4())
    definition = numeric_definition(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [
            (expired, now - timedelta(days=3), 4, "old-user"),
            (retained, now - timedelta(hours=12), 6, "new-user"),
        ],
    )
    failed = writer.begin(
        ledger={
            "dirty_before": [{"unit_id": expired}, {"unit_id": retained}],
            "membership_preimage": [
                Dependency(
                    trace,
                    "expired-owner",
                    expired,
                    expired,
                    "old-user",
                    "old-user",
                    now - timedelta(hours=12),
                ).payload(),
                Dependency(
                    trace,
                    "retained-owner",
                    retained,
                    retained,
                    "new-user",
                    "new-user",
                    now - timedelta(hours=12),
                ).payload(),
            ],
        }
    )
    writer.seal(failed)
    expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    assert head.published_by.source_ceilings["recovery_pending"]
    chunks = list(head.published_by.ledger_chunks.exclude(part="cleanup_intent"))
    assert any(retained in str(c.payload) for c in chunks)
    assert all(
        expired not in str(c.payload) and "old-user" not in str(c.payload)
        for c in chunks
    )


@pytest.mark.django_db(transaction=True)
def test_physical_expiry_waits_for_delayed_insert_drain_and_final_scrub(
    live_writer, monkeypatch
):
    from threading import Event, Thread

    from django.db import close_old_connections

    from conftest import _ch_test_native_client
    from tracer.models.derived_metric import (
        MetricAttemptBatch,
        MetricErasureRequest,
        MetricRefreshAttempt,
    )
    from tracer.services.derived_metrics.deletion_protocol import collect_history
    from tracer.services.derived_metrics.lease import ProjectLease
    from tracer.services.derived_metrics.publication import ClickHousePublicationStore
    from tracer.services.derived_metrics.refresh import RefreshWriter
    from tracer.services.derived_metrics.retention import finish_pending_expiry

    writer, now = live_writer, timezone.now()
    expired, retained = str(uuid4()), str(uuid4())
    definition = numeric_definition(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [
            (expired, now - timedelta(days=3), 4, "old"),
            (retained, now - timedelta(hours=12), 6, "live"),
        ],
    )
    pin = capture_pin(
        writer.lease.project_id, consumer_kind="snapshot", ttl=timedelta(hours=1)
    )
    attempt, payloads = prepare_field(
        writer,
        metric=definition.metric_id,
        unit_id=expired,
        start=now - timedelta(days=3),
    )
    MetricRefreshAttempt.transition(
        attempt.id, from_state="prepared", to_state="staging", **writer.lease.check()
    )
    plan = next(writer.plans(attempt))
    batch = MetricAttemptBatch.freeze(
        attempt.id,
        table_name="field_values",
        batch_no=0,
        row_count=len(payloads[0].rows),
        payload_sha256=payloads[0].digest,
        payload_ref="ledger:field_values:0",
        row_sequences=(plan["first"], plan["last"]),
        **writer.lease.check(),
    )
    database = writer.store.execute("SELECT currentDatabase()")[0][0]
    query_id, entered, drained, failures = str(uuid4()), Event(), Event(), []

    def delayed_submission():
        close_old_connections()
        try:
            with _ch_test_native_client(database=database) as client:
                store = ClickHousePublicationStore(client)

                def delayed_insert(table, payload):
                    entered.set()
                    # A REAL server-side INSERT is running before expiry. One
                    # row per block permits a controlled 40-second server delay;
                    # the private payload lands AFTER the first successful scrub.
                    client.execute(
                        f"INSERT INTO {table.name} ({', '.join(table.columns)}) "
                        "SELECT f.* FROM format(RowBinary, %(structure)s, unhex(%(data)s)) f "
                        "CROSS JOIN (SELECT sleepEachRow(2) AS pause FROM numbers(20)) d WHERE d.pause = 0",
                        {
                            "structure": ", ".join(f"{n} {t}" for n, t in table.schema),
                            "data": "".join(payload.rows),
                        },
                        query_id=query_id,
                        settings={
                            "max_block_size": 1,
                            "max_threads": 1,
                            "max_execution_time": 120,
                        },
                    )

                store.insert = delayed_insert
                submitting = RefreshWriter(
                    ProjectLease(writer.lease.grant),
                    store,
                    org_id=writer.org_id,
                    workspace_id=writer.workspace_id,
                )
                submitting.submit(attempt, batch, FIELD_VALUES, payloads[0])
        except Exception as exc:
            failures.append(exc)
        finally:
            drained.set()
            close_old_connections()

    thread = Thread(target=delayed_submission)
    thread.start()
    try:
        assert entered.wait(10)
        for _ in range(100):
            if writer.store.execute(
                "SELECT count() FROM system.processes WHERE query_id = %(id)s",
                {"id": query_id},
            ) == [(1,)]:
                break
            assert not drained.wait(0.05), failures
        else:
            pytest.fail("delayed INSERT was not observed on the server")
        result = expire_project(writer, [definition], retention_days=lambda: 1, now=now)
        assert not result["purged"] and not drained.is_set()
        assert not pin.is_usable()
        assert not MetricAttemptLedgerChunk.objects.filter(attempt=attempt).exists()
        obligation = MetricErasureRequest.objects.get(
            project_key=writer.lease.project_id, store_payload__protocol="metric_expiry"
        )
        assert obligation.status != "done" and expired not in str(
            obligation.store_payload
        )
        safe_pin = capture_pin(
            writer.lease.project_id, consumer_kind="snapshot", ttl=timedelta(hours=1)
        )
        writer.compact()
        writer.collect_garbage()
        # Collect the ACTUAL excluded attempt, not just call a no-op GC pass.
        MetricRefreshAttempt.objects.filter(id=attempt.id).update(
            expires_at=attempt.created_at + timedelta(seconds=1)
        )
        history = collect_history(
            writer.lease.project_id, now=timezone.now() + timedelta(minutes=10)
        )
        if history is not None:
            purge_by_scope(writer.store, history)
        assert not MetricRefreshAttempt.objects.filter(id=attempt.id).exists()
        obligation.refresh_from_db()
        assert obligation.status != "done"
        assert drained.wait(150), "server-side INSERT did not drain"
        assert not failures, [type(e).__name__ for e in failures]
        assert (
            writer.store.execute(
                "SELECT count() FROM metric_field_values WHERE unit_id = %(unit)s",
                {"unit": expired},
            )[0][0]
            > 0
        )
        safe = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
        assert [r[0] for r in current_fields(writer, safe)] == [retained]
        execute, crashed = writer.store.execute, []

        def crash_before_receipt(sql, *args, **options):
            if (
                sql.startswith("SELECT count() FROM metric_field_values WHERE org_id")
                and not crashed
            ):
                crashed.append(True)
                raise RuntimeError("crash before final scrub receipt")
            return execute(sql, *args, **options)

        monkeypatch.setattr(writer.store, "execute", crash_before_receipt)
        with pytest.raises(RuntimeError, match="final scrub receipt"):
            finish_pending_expiry(writer)
        obligation.refresh_from_db()
        assert obligation.status != "done"
        monkeypatch.setattr(writer.store, "execute", execute)
        assert finish_pending_expiry(writer)
        obligation.refresh_from_db()
        assert obligation.status == "done"
        assert obligation.receipts["final_scrub"] == {
            "remaining": 0,
            "verified_tables": 4,
            "private_payloads_remaining": 0,
        }
        assert safe_pin.is_usable()
        cells = read_field_cells(
            writer.store,
            Scope(writer.org_id, writer.workspace_id, writer.lease.project_id),
            safe_pin,
            definition.metric_id,
            1,
            [retained],
            retention_days=lambda: 1,
        )
        assert len(cells) == 1 and cells[0]["value_number"] == 6.0
        assert writer.store.execute(
            "SELECT count() FROM metric_field_values WHERE unit_id = %(unit)s",
            {"unit": expired},
        ) == [(0,)]
    finally:
        thread.join(160)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "prior_retirement",
    [
        # A genuinely new S after an expiry that retired no live unit: admitted
        # (no retired evidence exists that it could duplicate).
        False,
        pytest.param(
            True,
            marks=pytest.mark.xfail(
                strict=True,
                raises=MetricAdmissionUnproven,
                reason="TH-8975: retired versus new span admission raises "
                "MetricAdmissionUnproven",
            ),
        ),
    ],
)
def test_retired_and_new_spans_with_identical_surviving_rows_require_admission_proof(
    live_writer, workspace, observe_project, prior_retirement
):
    """HELD C02 product assertion, never skipped or converted to a green guard.

    The same surviving recent S must be admitted iff it was not retired before.
    Source authority for making that distinction has deliberately not been built.
    """
    from tracer.services.derived_metrics.changes import Dependency
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        metric_bucket_rows,
        seed_span_revision,
        span_definition_for,
    )

    writer, now = live_writer, timezone.now()
    definition = span_definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    session, trace, baseline_trace = uuid4(), uuid4(), uuid4()
    old, recent = now - timedelta(days=3), now - timedelta(hours=12)
    # Both histories publish an explicit non-retired baseline. Only one includes S.
    seed_span_revision(
        writer, scope, baseline_trace, "baseline", session, recent, 3, revision=1
    )
    dependencies = [
        Dependency(
            str(baseline_trace), "baseline", str(session), str(session), "", "", recent
        )
    ]
    if prior_retirement:
        seed_span_revision(writer, scope, trace, "S", session, old, 7, revision=1)
        dependencies.append(
            Dependency(str(trace), "S", str(session), str(session), "", "", old)
        )
    kwargs = {"start": old - timedelta(days=1), "end": now, "as_of": now}
    writer.refresh(
        change_for(session, dependencies=dependencies), [definition], scope, **kwargs
    )
    expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    writer.compact()
    writer.collect_garbage()
    writer.store.execute(
        "ALTER TABLE spans DELETE WHERE id = 'S' SETTINGS mutations_sync=2"
    )
    seed_span_revision(writer, scope, trace, "S", session, recent, 7, revision=2)
    writer.retention_days = lambda: 0  # expansion is not non-retirement authority
    head = writer.refresh(
        change_for(
            session,
            cursor=2,
            dependencies=[
                Dependency(str(trace), "S", str(session), str(session), "", "", recent)
            ],
        ),
        [definition],
        scope,
        **kwargs,
    )
    assert sum(r[2] for r in metric_bucket_rows(writer, head, definition)) == (
        1 if prior_retirement else 2
    )
    # This assertion requires genuinely NEW S live in one history, but not the
    # corrected/reused previously retired S in the indistinguishable other one.
    deps, p = committed_rows_select(
        SOURCE_DEPENDENCIES, head, org_id=writer.org_id, project_id=scope.project
    )
    assert writer.store.execute(
        f"SELECT count() FROM ({deps}) WHERE span_id = 'S' AND is_deleted = 0", p
    )[0][0] == (0 if prior_retirement else 1)


@pytest.mark.django_db(transaction=True)
def test_policy_shrink_during_staging_requires_replan_before_publication(
    live_writer, workspace, observe_project
):
    from tracer.services.derived_metrics.refresh import MetricRetentionChanged
    from tracer.tests.test_derived_metric_refresh_clickhouse import seed_span_revision

    writer, now = live_writer, timezone.now().replace(microsecond=0)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    definition = definition_for(writer, workspace, observe_project)
    session, trace = uuid4(), uuid4()
    at = now - timedelta(days=3)
    seed_span_revision(writer, scope, trace, "old", session, at, 4, revision=1)
    kwargs = {"start": at - timedelta(days=1), "end": now, "as_of": now}
    writer.refresh(change_for(session), [definition], scope, **kwargs)
    before = writer.compact()
    pin = capture_pin(scope.project, consumer_kind="snapshot", ttl=timedelta(hours=1))
    execute = writer.store.execute

    def shrink_after_hydration(sql, *args, **options):
        result = execute(sql, *args, **options)
        if options.get("with_column_types") and sql.startswith("SELECT * REPLACE"):
            writer.retention_days = lambda: 1
        return result

    writer.store.execute = shrink_after_hydration
    try:
        with pytest.raises(MetricStateConflict, match="retention_expired"):
            read_field_cells(
                writer.store,
                scope,
                pin,
                definition.metric_id,
                1,
                [str(session)],
                retention_days=lambda: writer.retention_days(),
            )
    finally:
        writer.store.execute = execute
        writer.retention_days = lambda: 0
    seed_span_revision(writer, scope, trace, "old", session, at, 8, revision=2)
    insert = writer.store.insert

    def shrink_after_insert(table, payload):
        insert(table, payload)
        writer.retention_days = lambda: 1

    writer.store.insert = shrink_after_insert
    try:
        with pytest.raises(MetricRetentionChanged, match="replan required"):
            writer.refresh(change_for(session, cursor=2), [definition], scope, **kwargs)
    finally:
        writer.store.insert = insert
    state = MetricProjectRefresh.for_project(scope.project)
    assert state.head_manifest_id == before.id and state.watermark_ns == 1
    assert state.pending_expiry
    writer.compact()
    assert MetricProjectRefresh.for_project(scope.project).pending_expiry
    with pytest.raises(MetricStateConflict, match="retention_expired"):
        assert_capture_retained(
            pin, [at], retention_days=writer.retention_days, now=now
        )
    head = writer.refresh(change_for(session, cursor=2), [definition], scope, **kwargs)
    assert current_fields(writer, head) == [] and not pin.is_usable()
    assert anonymous_parts(writer, head) == []


def known_expired_metric_update(writer, workspace, project, *, mixed, group="west"):
    from tracer.services.derived_metrics.changes import Dependency
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        seed_span_revision,
        span_definition_for,
    )

    now = timezone.now()
    writer.lease.clock = lambda: now
    writer.lease.renew()
    conversation = definition_for(writer, workspace, project)
    conversation.metric.name = "Conversation expiry fixture"
    conversation.metric.save(update_fields=["name"])
    span = span_definition_for(writer, workspace, project) if mixed else None
    definitions = [conversation, span] if mixed else [conversation]
    scope = Scope(writer.org_id, writer.workspace_id, project.id)
    session, trace = uuid4(), uuid4()
    old = now - timedelta(days=3)
    dependency = Dependency(
        str(trace), "known", str(session), str(session), "", "", old
    )
    seed_span_revision(writer, scope, trace, "known", session, old, 4, revision=1)
    kwargs = {"start": old - timedelta(days=1), "end": now + timedelta(days=1)}
    writer.refresh(
        change_for(session, dependencies=(dependency,)),
        definitions,
        scope,
        as_of=now - timedelta(minutes=1),
        **kwargs,
    )
    seed_span_revision(
        writer,
        scope,
        trace,
        "known",
        session,
        old,
        8,
        revision=2,
        arrival=now,
        group=group,
    )
    writer.retention_days = lambda: 1
    head = writer.refresh(
        change_for(session, cursor=2, dependencies=(dependency,), prior=(dependency,)),
        definitions,
        scope,
        as_of=now,
        **kwargs,
    )
    return head, conversation, span


def metric_retired_statistics(writer, head, definition):
    from tracer.services.derived_metrics.buckets import STATS

    sql, params = committed_rows_select(
        RETIRED_PARTS,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    columns = [
        f"finalizeAggregation({name})"
        if name in {"uniq_state", "quantile_state"}
        else name
        for name in STATS
    ]
    return writer.store.execute(
        f"SELECT bucket_start, group_key, {', '.join(columns)} FROM ({sql}) "
        "WHERE metric_id = toUUID(%(metric)s) AND metric_version = %(version)s "
        "AND is_deleted = 0 ORDER BY bucket_start, group_key",
        {**params, "metric": str(definition.metric_id), "version": definition.version},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mixed", [False, True])
def test_known_expired_conversation_reopens_before_retirement(
    live_writer, workspace, observe_project, mixed
):
    from tracer.tests.test_derived_metric_refresh_clickhouse import metric_bucket_rows

    head, conversation, _ = known_expired_metric_update(
        live_writer, workspace, observe_project, mixed=mixed
    )
    assert metric_retired_statistics(live_writer, head, conversation) == []
    assert metric_bucket_rows(live_writer, head, conversation) == []
    assert current_fields(live_writer, head) == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("group", ["west", "east"])
def test_span_expiry_removes_expired_bucket_and_retired_parts(
    live_writer, workspace, observe_project, group
):
    from tracer.services.derived_metrics.buckets import STATS
    from tracer.services.derived_metrics.storage import BUCKET_TOTALS

    writer = live_writer
    head, _, span = known_expired_metric_update(
        writer, workspace, observe_project, mixed=True, group=group
    )
    retired = metric_retired_statistics(writer, head, span)
    sql, params = committed_rows_select(
        BUCKET_TOTALS,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    columns = [
        f"finalizeAggregation({name})"
        if name in {"uniq_state", "quantile_state"}
        else name
        for name in STATS
    ]
    totals = writer.store.execute(
        f"SELECT bucket_start, group_key, {', '.join(columns)} FROM ({sql}) "
        "WHERE metric_id = toUUID(%(metric)s) AND metric_version = %(version)s "
        "AND is_deleted = 0 ORDER BY bucket_start, group_key",
        {**params, "metric": str(span.metric_id), "version": span.version},
    )
    assert retired == []
    assert totals == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("resume", ["collect_garbage", "finish_pending_expiry"])
def test_cleanup_recovery_does_not_certify_erasure_with_private_ledgers(
    live_writer, workspace, observe_project, monkeypatch, resume
):
    from tracer.models.derived_metric import MetricErasureRequest
    from tracer.services.derived_metrics import retention
    from tracer.tests.test_derived_metric_refresh_clickhouse import seed_span_revision

    writer = live_writer
    definition, scope, session, trace, now, _, old, kwargs, _ = history_fixture(
        writer, workspace, observe_project
    )
    seed_span_revision(writer, scope, trace, "earlier", session, old, 6, revision=1)

    def crash_before_purge(*args, **options):
        raise RuntimeError("crash after publication before private payload purge")

    with monkeypatch.context() as patch:
        patch.setattr(retention, "purge_checkpoint", crash_before_purge)
        with pytest.raises(RuntimeError, match="crash after publication"):
            writer.refresh(
                change_for(session, cursor=2), [definition], scope, as_of=now, **kwargs
            )
    chunks = MetricAttemptLedgerChunk.objects.filter(attempt__project_id=scope.project)
    assert any(str(session) in str(chunk.payload) for chunk in chunks)
    obligations = MetricErasureRequest.objects.filter(
        project_key=scope.project, store_payload__protocol="metric_expiry"
    )
    assert obligations.exists() and not obligations.filter(status="done").exists()
    with monkeypatch.context() as patch:
        patch.setattr(retention, "_purge_terminal_payloads", lambda *a, **kw: 0)
        with pytest.raises(MetricStateConflict, match="private scrub is incomplete"):
            retention.finish_pending_expiry(writer)
    assert not obligations.filter(status="done").exists() and chunks.exists()

    def fail_external_scrub(*args, **options):
        raise RuntimeError("external scrub unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(retention, "_delete", fail_external_scrub)
        with pytest.raises(RuntimeError, match="external scrub unavailable"):
            retention.finish_pending_expiry(writer)
    assert not chunks.all().exists() and not obligations.filter(status="done").exists()
    if resume == "collect_garbage":
        writer.collect_garbage()
    else:
        assert retention.finish_pending_expiry(writer)
    assert not obligations.exclude(status="done").exists()
    assert not chunks.all().exists()
    for table in (
        FIELD_VALUES,
        retention.TABLES["metric_field_values_checkpoint"],
        SOURCE_DEPENDENCIES,
        UNIT_STATE,
    ):
        assert writer.store.execute(
            f"SELECT count() FROM {table.name} WHERE project_id = toUUID(%(project)s)",
            {"project": str(scope.project)},
        ) == [(0,)]
    assert not MetricProjectRefresh.for_project(scope.project).pending_expiry


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("initial_days", [0, 4])
def test_span_policy_shrink_is_revalidated_at_publication(
    live_writer, workspace, observe_project, mixed, initial_days
):
    from tracer.services.derived_metrics.changes import Dependency
    from tracer.services.derived_metrics.refresh import MetricRetentionChanged
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        seed_span_revision,
        span_definition_for,
    )

    writer, now = live_writer, timezone.now()
    writer.lease.clock = lambda: now
    writer.lease.renew()
    writer.retention_days = lambda: initial_days
    definitions = []
    if mixed:
        conversation = definition_for(writer, workspace, observe_project)
        conversation.metric.name = "Mixed policy fixture"
        conversation.metric.save(update_fields=["name"])
        definitions.append(conversation)
    span = span_definition_for(writer, workspace, observe_project)
    definitions.append(span)
    scope = Scope(writer.org_id, writer.workspace_id, observe_project.id)
    session, trace = uuid4(), uuid4()
    at = now - timedelta(days=3)
    dependency = Dependency(str(trace), "old", str(session), str(session), "", "", at)
    seed_span_revision(writer, scope, trace, "old", session, at, 4, revision=1)
    kwargs = {"start": at - timedelta(days=1), "end": now, "as_of": now}
    writer.refresh(
        change_for(session, dependencies=(dependency,)), definitions, scope, **kwargs
    )
    before = writer.compact()
    seed_span_revision(writer, scope, trace, "old", session, at, 8, revision=2)
    insert = writer.store.insert

    def shrink_after_insert(table, payload):
        insert(table, payload)
        writer.retention_days = lambda: 1

    writer.store.insert = shrink_after_insert
    try:
        with pytest.raises(MetricRetentionChanged, match="replan required"):
            writer.refresh(
                change_for(
                    session, cursor=2, dependencies=(dependency,), prior=(dependency,)
                ),
                definitions,
                scope,
                **kwargs,
            )
    finally:
        writer.store.insert = insert
    state = MetricProjectRefresh.for_project(scope.project)
    assert state.head_manifest_id == before.id and state.watermark_ns == 1
    assert state.pending_expiry
    head = writer.refresh(
        change_for(session, cursor=2, dependencies=(dependency,), prior=(dependency,)),
        definitions,
        scope,
        **kwargs,
    )
    assert MetricProjectRefresh.for_project(scope.project).watermark_ns == 2
    assert not MetricProjectRefresh.for_project(scope.project).pending_expiry
    assert metric_retired_statistics(writer, head, span) == []
    assert not MetricAttemptLedgerChunk.objects.filter(
        attempt__project_id=scope.project
    ).exists()
    for table in (FIELD_VALUES, SOURCE_DEPENDENCIES, UNIT_STATE):
        assert writer.store.execute(
            f"SELECT count() FROM {table.name} WHERE project_id = toUUID(%(project)s)",
            {"project": str(scope.project)},
        ) == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_finite_mixed_refresh_preserves_nonexpired_snapshot_pin(
    live_writer, workspace, observe_project, monkeypatch
):
    from tracer.models.derived_metric import MetricRefreshAttempt
    from tracer.services.derived_metrics import retention
    from tracer.services.derived_metrics.changes import Dependency
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        metric_bucket_rows,
        seed_span_revision,
        span_definition_for,
    )

    writer, now = live_writer, timezone.now()
    writer.lease.clock = lambda: now
    writer.lease.renew()
    writer.retention_days = lambda: 1
    conversation = definition_for(writer, workspace, observe_project)
    conversation.metric.name = "Retained snapshot fixture"
    conversation.metric.save(update_fields=["name"])
    span = span_definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, observe_project.id)
    session, trace = uuid4(), uuid4()
    at = now - timedelta(hours=12)
    dependency = Dependency(
        str(trace), "retained", str(session), str(session), "", "", at
    )
    seed_span_revision(writer, scope, trace, "retained", session, at, 4, revision=1)
    kwargs = {"start": now - timedelta(days=2), "end": now, "as_of": now}
    writer.refresh(
        change_for(session, dependencies=(dependency,)),
        [conversation, span],
        scope,
        **kwargs,
    )
    pin = capture_pin(
        scope.project, consumer_kind="snapshot", ttl=timedelta(hours=1), now=now
    )
    assert not expire_project(
        writer, [conversation, span], retention_days=writer.retention_days, now=now
    )["purged"]
    assert pin.is_usable(now)
    seed_span_revision(writer, scope, trace, "retained", session, at, 8, revision=2)

    def crash_before_private_cleanup(*args, **options):
        raise RuntimeError("retained checkpoint published before PG cleanup")

    with monkeypatch.context() as patch:
        patch.setattr(retention, "purge_checkpoint", crash_before_private_cleanup)
        with pytest.raises(RuntimeError, match="retained checkpoint published"):
            writer.refresh(
                change_for(
                    session, cursor=2, dependencies=(dependency,), prior=(dependency,)
                ),
                [conversation, span],
                scope,
                **kwargs,
            )
    head = MetricProjectRefresh.for_project(scope.project).head_manifest
    draft = head.published_by
    # Nothing expires and every private owner link is proven retained, so the
    # integrated retention plan publishes the draft itself: no replacement
    # privacy checkpoint, no retention_replan abort, no revoked safe pin.
    assert draft.purpose == "incremental"
    assert not draft.source_ceilings.get("logical_expiry")
    assert "private_cleanup_through" not in draft.source_ceilings
    assert not MetricRefreshAttempt.objects.filter(
        project_id=scope.project, abort_reason="retention_replan"
    ).exists()
    predecessors = MetricAttemptLedgerChunk.objects.filter(
        attempt__project_id=scope.project, attempt__generation__lte=draft.generation
    )
    assert predecessors.exists() and pin.is_usable(now)
    later = writer.begin(ledger={"dirty_before": [{"unit_id": str(session)}]})
    MetricRefreshAttempt.abort(
        later.id, reason="injected_failure", **writer.lease.check()
    )
    # The crashed post-publication cleanup is retried (as the next retention
    # pass does): every consumed private predecessor goes, newer work stays.
    retention.purge_checkpoint(writer, draft, head)
    assert not predecessors.exists()
    head = writer.compact()
    writer.collect_garbage()
    assert not predecessors.exists()
    assert (
        later.ledger_chunks.exists()
    )  # newer, unconsumed work is not in that boundary
    assert metric_bucket_rows(writer, head, conversation)[0][2:] == (1, 8.0, 1.0)
    assert metric_retired_statistics(writer, head, conversation) == []
    assert pin.is_usable(now)
    assert not head.published_by.source_ceilings.get("logical_expiry")
    assert (
        read_field_cells(
            writer.store,
            scope,
            pin,
            conversation.metric_id,
            conversation.version,
            [str(session)],
            retention_days=writer.retention_days,
            now=now,
        )[0]["value_number"]
        == 4
    )


@pytest.mark.django_db(transaction=True)
def test_finite_mixed_refresh_computes_an_unowned_quarter_mate(
    live_writer, workspace, observe_project
):
    """A dirty quarter's span fields include an UNTOUCHED conversation's span.

    Span fields are rebuilt for whole dirty quarters, so this pass emits the
    quarter-mate's span although only the first conversation changed. The mate
    has no conversation row in the head or the draft: it was never processed
    (as when it lies just past an initial-sweep page boundary). Its owner link
    must not force a privacy checkpoint (which then left the next sweep page
    unable to prove its span evidence, so definitions never activated): the
    pass computes the mate's conversation in the same publication.
    """
    from tracer.models.derived_metric import MetricRefreshAttempt
    from tracer.services.derived_metrics.changes import Dependency
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        seed_span_revision,
        span_definition_for,
    )

    writer, now = live_writer, timezone.now()
    writer.lease.clock = lambda: now
    writer.lease.renew()
    writer.retention_days = lambda: 90
    conversation = definition_for(writer, workspace, observe_project)
    conversation.metric.name = "Quarter-mate fixture"
    conversation.metric.save(update_fields=["name"])
    span = span_definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, observe_project.id)
    kwargs = {"start": now - timedelta(days=2), "end": now, "as_of": now}

    def seed(session, trace, name, at, value):
        seed_span_revision(writer, scope, trace, name, session, at, value, revision=1)
        return Dependency(str(trace), name, str(session), str(session), "", "", at)

    # A head already exists (an earlier conversation, another quarter).
    earlier = uuid4()
    dependency = seed(earlier, uuid4(), "earlier", now - timedelta(days=1), 2)
    writer.refresh(
        change_for(earlier, dependencies=(dependency,)),
        [conversation, span],
        scope,
        **kwargs,
    )
    quarter = (now - timedelta(hours=12)).replace(minute=0, second=0, microsecond=0)
    first, mate = uuid4(), uuid4()
    dependency = seed(first, uuid4(), "first", quarter + timedelta(minutes=2), 4)
    seed(mate, uuid4(), "mate", quarter + timedelta(minutes=9), 8)
    manifest = writer.refresh(
        change_for(first, cursor=2, dependencies=(dependency,)),
        [conversation, span],
        scope,
        **kwargs,
    )
    draft = manifest.published_by
    assert draft.purpose == "incremental"
    assert not draft.source_ceilings.get("logical_expiry")
    assert not MetricRefreshAttempt.objects.filter(
        project_id=scope.project, abort_reason="retention_replan"
    ).exists()
    sql, params = committed_rows_select(
        FIELD_VALUES, manifest, org_id=writer.org_id, project_id=scope.project
    )
    rows = writer.store.execute(
        f"SELECT level, session_id, value_number FROM ({sql}) WHERE is_deleted = 0 "
        "ORDER BY level, session_id",
        params,
    )
    assert sorted(rows) == sorted(
        [
            ("conversation", str(earlier), 2.0),
            ("conversation", str(first), 4.0),
            ("conversation", str(mate), 8.0),
            ("span", str(earlier), 2.0),
            ("span", str(first), 4.0),
            ("span", str(mate), 8.0),
        ]
    )


@pytest.mark.django_db(transaction=True)
def test_idle_span_expiry_replaces_final_bucket_statistics(
    live_writer, workspace, observe_project
):
    from tracer.services.derived_metrics.changes import Dependency
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        metric_bucket_rows,
        seed_span_revision,
        span_definition_for,
    )

    writer, now = live_writer, timezone.now()
    writer.lease.clock = lambda: now
    writer.lease.renew()
    definition = span_definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, observe_project.id)
    session, trace = uuid4(), uuid4()
    old = now - timedelta(days=3)
    dependency = Dependency(str(trace), "old", str(session), str(session), "", "", old)
    seed_span_revision(writer, scope, trace, "old", session, old, 4, revision=1)
    writer.refresh(
        change_for(session, dependencies=(dependency,)),
        [definition],
        scope,
        start=old - timedelta(days=1),
        end=now,
        as_of=now,
    )
    seed_span_revision(writer, scope, trace, "old", session, old, 8, revision=2)
    writer.retention_days = lambda: 1
    assert expire_project(
        writer, [definition], retention_days=writer.retention_days, now=now
    )["purged"]
    head = MetricProjectRefresh.for_project(scope.project).head_manifest
    parts = metric_retired_statistics(writer, head, definition)
    assert parts == []
    assert metric_bucket_rows(writer, head, definition) == []


def _created_span(writer, scope, trace, span, session, start, created, value):
    """A span revision whose physical creation is ``created``, not its start."""
    from tracer.services.derived_metrics.changes import ns

    writer.store.execute(
        "INSERT INTO spans (id, trace_id, project_id, org_id, trace_session_id, "
        "start_time, end_time, created_at, updated_at, _version, attrs_number, "
        "attrs_string) VALUES (%(span)s, %(trace)s, %(project_id)s, %(org_id)s, "
        "%(session)s, %(start)s, %(end)s, %(created)s, %(created)s, %(version)s, "
        "map('resolved', %(value)s), map('region', 'west'))",
        {
            **scope.parameters(),
            "span": span,
            "trace": str(trace),
            "session": str(session),
            "start": start,
            "end": start + timedelta(seconds=1),
            "created": created,
            "version": ns(created),
            "value": value,
        },
    )


def _admission_fixture(writer, workspace, project, level):
    from tracer.services.derived_metrics.changes import Dependency
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        span_definition_for,
    )

    definition = (
        definition_for(writer, workspace, project)
        if level == "conversation"
        else span_definition_for(writer, workspace, project)
    )
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    now = timezone.now()

    def refresh(name, session, trace, start, *, cursor, as_of=now):
        dependency = Dependency(
            str(trace), name, str(session), str(session), "", "", start
        )
        return writer.refresh(
            change_for(session, cursor=cursor, dependencies=[dependency]),
            [definition],
            scope,
            start=now - timedelta(days=4),
            end=as_of,
            as_of=as_of,
        )

    return definition, scope, now, refresh


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("level", ["conversation", "span"])
def test_units_arriving_around_a_live_purge_are_admitted_without_proof(
    live_writer, workspace, observe_project, level
):
    """10M B5: after a purge that retired LIVE published evidence, every head
    inherits a retention floor and refused every unit without a committed
    anchor, so a project stopped admitting new units for good, and a purge in
    steady ingest refused every unit still in flight.

    A purge retires only what its head captured, and a capture reads spans
    created at or before its as_of. So a unit whose spans were all created
    after that capture (less discovery's 5-minute late-arrival overlap) cannot
    be retired evidence: units in flight when the purge ran, units created a
    little before the capture but inserted after it, and fresh units are all
    admitted. One created well before the capture yet never captured is the
    R02 shape (TH-8975) and still needs proof."""
    writer = live_writer
    definition, scope, now, refresh = _admission_fixture(
        writer, workspace, observe_project, level
    )
    old = now - timedelta(days=3)
    capture, purge = now - timedelta(hours=2), now - timedelta(hours=1)
    expired_session, expired_trace = uuid4(), uuid4()
    _created_span(writer, scope, expired_trace, "expired", expired_session, old, old, 4)
    refresh("expired", expired_session, expired_trace, old, cursor=1, as_of=capture)
    expire_project(writer, [definition], retention_days=lambda: 1, now=purge)
    ceilings = MetricProjectRefresh.for_project(
        observe_project.id
    ).head_manifest.published_by.source_ceilings
    assert ceilings["retention_floor"]
    writer.retention_days = lambda: 1

    def unit(session, trace, name):
        return str(session) if level == "conversation" else f'["{trace}","{name}"]'

    admitted = []
    for name, start, created in (
        # In flight: created after the capture, before the purge.
        ("in-flight", capture + timedelta(minutes=20), capture + timedelta(minutes=30)),
        # Created just before the capture, inserted after it (late arrival).
        ("late", capture - timedelta(minutes=3), capture - timedelta(minutes=2)),
        # Created after the purge.
        ("fresh", now - timedelta(minutes=50), now - timedelta(minutes=30)),
    ):
        session, trace = uuid4(), uuid4()
        _created_span(writer, scope, trace, name, session, start, created, 8)
        head = refresh(name, session, trace, start, cursor=2 + len(admitted))
        admitted.append(unit(session, trace, name))
        live = {row[0] for row in current_fields(writer, head) if not row[2]}
        assert admitted[-1] in live, name
    ceilings = head.published_by.source_ceilings
    assert ceilings["retention_retired_live"] is True
    assert (
        ceilings["retention_floor_at"] == (capture - timedelta(minutes=5)).isoformat()
    )

    # Created well before the capture and never captured: it may be retired
    # evidence reappearing (R02), so it still needs proof.
    held_session, held_trace = uuid4(), uuid4()
    held = capture - timedelta(minutes=20)
    _created_span(writer, scope, held_trace, "held", held_session, held, held, 6)
    with pytest.raises(MetricAdmissionUnproven):
        refresh("held", held_session, held_trace, held, cursor=9)


@pytest.mark.django_db(transaction=True)
def test_an_expiry_that_retires_no_live_published_evidence_requires_no_proof(
    live_writer, workspace, observe_project
):
    """10M B5: BIG's first publication was an expiry compaction that retired
    nothing published, yet its retention floor made every later unit prove it
    was not retired evidence, so BIG never admitted another unit. The floor is
    stamped as before, but admission needs proof only once a purge retired
    LIVE published units: here only a tombstone expires, so a later unit is
    admitted."""
    writer = live_writer
    definition, scope, now, refresh = _admission_fixture(
        writer, workspace, observe_project, "conversation"
    )
    old = now - timedelta(days=3)
    gone_session, gone_trace = uuid4(), uuid4()
    _created_span(writer, scope, gone_trace, "gone", gone_session, old, old, 4)
    refresh(
        "gone", gone_session, gone_trace, old, cursor=1, as_of=now - timedelta(hours=2)
    )
    writer.store.execute(
        "ALTER TABLE spans DELETE WHERE id = 'gone' SETTINGS mutations_sync=2"
    )
    head = refresh(
        "gone", gone_session, gone_trace, old, cursor=2, as_of=now - timedelta(hours=2)
    )
    assert [row[2] for row in current_fields(writer, head)] == [1]  # a tombstone
    expire_project(
        writer, [definition], retention_days=lambda: 1, now=now - timedelta(hours=1)
    )
    writer.retention_days = lambda: 1
    later_session, later_trace = uuid4(), uuid4()
    started = now - timedelta(minutes=50)
    _created_span(
        writer, scope, later_trace, "later", later_session, started, started, 8
    )
    head = refresh("later", later_session, later_trace, started, cursor=3)
    assert str(later_session) in {row[0] for row in current_fields(writer, head)}
    ceilings = head.published_by.source_ceilings
    assert ceilings["retention_floor"] and ceilings["retention_retired_live"] is False
