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
from tracer.services.derived_metrics.refresh import payload_select
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
        writer.lease.project_id, consumer_kind="snapshot", ttl=timedelta(minutes=5)
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
    # Snapshot store has old unit evidence; live retention must never touch it.
    writer.store.execute(
        "INSERT INTO derived_metric_inputs (org_id, workspace_id, project_id, snapshot_id, unit_id, input_values) VALUES (%(org)s, %(workspace)s, %(project)s, %(snapshot)s, %(unit)s, map('y', 4.))",
        {
            "org": str(writer.org_id),
            "workspace": str(writer.workspace_id),
            "project": str(writer.lease.project_id),
            "snapshot": str(uuid4()),
            "unit": unit_old,
        },
    )
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
    assert writer.store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE unit_id = %(unit)s",
        {"unit": unit_old},
    ) == [(1,)]
    retired_sql, rp = committed_rows_select(
        RETIRED_PARTS, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    retired_rows = writer.store.execute(
        f"SELECT unit_count, weighted_n FROM ({retired_sql}) WHERE is_deleted = 0", rp
    )
    if level == "conversation":
        assert retired_rows == [(1, 4.0)]
        assert sum(r[2] for r in bucket_rows(writer, head) if not r[-1]) == 2
        newer = seed_fields_and_buckets(
            writer, definition, [(unit_live, retained, 8, user_live)]
        )
        live = [r for r in bucket_rows(writer, newer) if not r[-1]]
        assert len(live) == 1 and live[0][2:5] == (2, 12.0, 2.0)
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
    assert sum(r[2] for r in bucket_rows(writer, head) if not r[-1]) == 1


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
def test_expired_active_conversation_is_not_reanchored_after_source_retention(
    live_writer, workspace, observe_project
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
    # member as a new anchor restores unit evidence and duplicates its retired
    # contribution in a newer bucket.
    assert (
        len(current_fields(writer, head)),
        sum(r[2] for r in bucket_rows(writer, head) if not r[-1]),
    ) == (0, 1)
