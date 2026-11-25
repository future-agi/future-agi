# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Real current-fact inserts, immutable replay, replacement and privacy lifecycle."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tracer.models.derived_metric import MetricProjectRefresh, MetricRefreshAttempt
from tracer.services.derived_metrics.base_list_current_clickhouse import freeze_changes
from tracer.services.derived_metrics.deletion_protocol import capture_pin
from tracer.services.derived_metrics.publication import (
    MetricVisibilityError,
    committed_rows_select,
)
from tracer.services.derived_metrics.retention import expire_project, purge_by_scope
from tracer.services.derived_metrics.storage import (
    LIST_CURRENT_MEMBERS,
    LIST_CURRENT_ROOTS,
    LIST_CURRENT_TABLES,
    LIST_USER_ACTIVITY,
)
from tracer.tests._base_list_parity import (
    FixtureAuthority,
    fixture_current_facts,
    native_source_schema,
    source_row,
)
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)

AT = datetime(2026, 10, 20, 12, tzinfo=UTC)


@pytest.fixture
def current_writer(live_writer):
    native_source_schema(live_writer.store.client)
    return live_writer


def prepare_current(writer, keys, *, as_of=AT + timedelta(days=1)):
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    attempt = writer.begin(ceilings={"as_of": as_of})
    maintained = freeze_changes(writer, attempt, head, keys=keys, as_of=as_of)
    writer.seal(attempt)
    return attempt, maintained


def publish_current(writer, keys, **kwargs):
    attempt, maintained = prepare_current(writer, keys, **kwargs)
    assert maintained
    return writer.stage_and_publish(attempt)


def facts(writer, manifest, table=LIST_CURRENT_ROOTS):
    sql, params = committed_rows_select(
        table,
        manifest,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    return writer.store.execute(
        f"SELECT span_id, canonical_session_id, canonical_user_id, unit_start, is_deleted, input, cost FROM ({sql}) ORDER BY span_id",
        params,
    )


def authority(manifest, **changes):
    return FixtureAuthority(
        **{
            "manifest_id": str(manifest.id),
            "remap_revision": manifest.remap_revision,
            "complete_retention_coverage": True,
            "fresh": True,
            "unique_physical_versions": True,
            "anchor_authoritative": True,
            **changes,
        }
    )


@pytest.mark.django_db(transaction=True)
def test_current_batches_replay_retractions_and_pinned_compaction(current_writer):
    writer = current_writer
    session, moved = uuid4(), uuid4()
    source_row(writer, session=session, at=AT, input="first", cost=0.125)
    first = publish_current(writer, [("trace-a", "root")])
    pin = capture_pin(
        writer.lease.project_id, consumer_kind="snapshot", ttl=timedelta(minutes=5)
    )
    source_row(
        writer,
        session=moved,
        at=AT,
        version=2,
        input="replacement",
        parent_span_id="parent",
    )
    source_row(
        writer,
        session=moved,
        at=AT + timedelta(seconds=1),
        span="late-child",
        version=3,
        parent_span_id="root",
    )
    second = publish_current(writer, [("trace-a", "root"), ("trace-a", "late-child")])
    assert facts(writer, first)[0][1:3] == (str(session), "")
    assert [(r[0], r[4]) for r in facts(writer, second)] == [("root", 1)]
    # A never-root child belongs only to the member index, not a root tombstone.
    assert [r[0] for r in facts(writer, second, LIST_CURRENT_MEMBERS)] == [
        "late-child",
        "root",
    ]
    assert all(r[4] == 0 for r in facts(writer, second, LIST_CURRENT_MEMBERS))
    assert set(second.published_by.batches.values_list("table_name", flat=True)) == {
        t.name for t in LIST_CURRENT_TABLES
    }
    assert second.published_by.batches.filter(verified_at__isnull=True).count() == 0
    compacted = writer.compact()
    assert compacted.delta_attempt_ids == []
    assert facts(writer, compacted) == facts(writer, second)
    with fixture_current_facts(
        writer.store,
        project_id=writer.lease.project_id,
        org_id=writer.org_id,
        pin=pin,
        authority=authority,
    ) as read:
        assert read.permits(writer.lease.project_id)
        assert facts(writer, read.manifest)[0][-2:] == ("first", 0.125)
    assert not read.permits(writer.lease.project_id)


@pytest.mark.django_db(transaction=True)
def test_current_publication_waits_for_receipts_and_replays_frozen_bytes(
    current_writer, monkeypatch
):
    writer = current_writer
    session = uuid4()
    source_row(writer, session=session, at=AT, input="frozen")
    attempt, maintained = prepare_current(writer, [("trace-a", "root")])
    assert maintained
    original = writer.store.verify

    def invisible(*args):
        raise MetricVisibilityError("not visible")

    monkeypatch.setattr(writer.store, "verify", invisible)
    with pytest.raises(MetricVisibilityError):
        writer.stage_and_publish(attempt)
    assert (
        MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest_id
        is None
    )
    source_row(writer, session=session, at=AT, version=99, input="not the replay")
    monkeypatch.setattr(writer.store, "verify", original)
    head = writer.stage_and_publish(attempt)
    assert facts(writer, head)[0][-2] == "frozen"
    assert writer.stage_and_publish(attempt).id == head.id


@pytest.mark.django_db(transaction=True)
def test_vanished_physical_row_and_remap_only_updates(current_writer):
    writer = current_writer
    old, canonical, user_old, user_new = [uuid4() for _ in range(4)]
    # Identity-row remap has an unambiguous canonical target.
    source_row(writer, session=old, at=AT, end_user_id=user_new)
    first = publish_current(writer, [("trace-a", "root")])
    writer.store.execute(
        "INSERT INTO trace_session_id_remap (old_id, new_id, version) VALUES",
        [(canonical, old, AT)],
    )
    writer.store.execute(
        "INSERT INTO end_user_id_remap (old_id, new_id, version) VALUES",
        [(user_old, user_new, AT)],
    )
    second = publish_current(writer, [("trace-a", "root")])
    assert facts(writer, first)[0][1] == str(old)
    assert facts(writer, second)[0][1:3] == (str(canonical), str(user_old))
    writer.store.execute(
        "ALTER TABLE spans DELETE WHERE id = 'root' SETTINGS mutations_sync=2"
    )
    third = publish_current(writer, [("trace-a", "root")])
    assert facts(writer, third)[0][4] == 1
    assert facts(writer, third, LIST_CURRENT_MEMBERS)[0][4] == 1


@pytest.mark.django_db(transaction=True)
def test_equal_version_divergence_does_not_grant_current_coverage(current_writer):
    writer = current_writer
    writer.store.execute("SYSTEM STOP MERGES spans")
    source_row(writer, session=uuid4(), at=AT, input="a")
    source_row(writer, session=uuid4(), at=AT, input="b")
    attempt, maintained = prepare_current(writer, [("trace-a", "root")])
    assert not maintained
    assert list(writer.plans(attempt)) == []
    head = writer.stage_and_publish(attempt)
    with fixture_current_facts(
        writer.store,
        project_id=writer.lease.project_id,
        org_id=writer.org_id,
        authority=lambda m: authority(m, unique_physical_versions=False),
    ) as read:
        assert read is None
    assert "base_list_current" not in head.visibility_receipts


@pytest.mark.django_db(transaction=True)
def test_aborted_late_current_insert_is_excluded_and_collected(current_writer):
    writer = current_writer
    session = uuid4()
    source_row(writer, session=session, at=AT, input="before")
    head = publish_current(writer, [("trace-a", "root")])
    source_row(writer, session=session, at=AT, version=2, input="aborted")
    stale, _ = prepare_current(writer, [("trace-a", "root")])
    plans = list(writer.plans(stale))
    MetricRefreshAttempt.abort(stale.id, reason="test_abort", **writer.lease.check())
    from tracer.services.derived_metrics.publication import FrozenRows
    from tracer.services.derived_metrics.refresh import TABLE_KINDS

    for plan in plans:
        writer.store.insert(TABLE_KINDS[plan["table"]], FrozenRows(tuple(plan["rows"])))
    assert facts(writer, head)[0][-2] == "before"
    # Compaction collects the aborted namespace. Even a write arriving AFTER
    # that collection remains excluded and the recurring orphan scrub reaps it.
    writer.compact()
    from tracer.services.derived_metrics.deletion_protocol import collect_history

    intent = collect_history(
        writer.lease.project_id, now=stale.expires_at + timedelta(seconds=1)
    )
    assert intent is not None
    purge_by_scope(writer.store, intent)
    assert not MetricRefreshAttempt.objects.filter(id=stale.id).exists()
    for plan in plans:
        writer.store.insert(TABLE_KINDS[plan["table"]], FrozenRows(tuple(plan["rows"])))
    writer.scrub_orphans()
    for table in LIST_CURRENT_TABLES:
        assert writer.store.execute(
            f"SELECT count() FROM {table.name} WHERE attempt_id = toUUID(%(a)s)",
            {"a": str(stale.id)},
        ) == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_current_only_subject_erasure_preserves_unrelated_sessions(current_writer):
    writer = current_writer
    session, other, user = uuid4(), uuid4(), uuid4()
    source_row(writer, session=session, at=AT, input="private", end_user_id=user)
    source_row(writer, session=other, at=AT, span="other", input="retained")
    publish_current(writer, [("trace-a", "root"), ("trace-a", "other")])
    from tracer.services.derived_metrics.deletion_protocol import _intent

    request = _intent(
        organization_id=writer.org_id,
        project_id=writer.lease.project_id,
        scope="users",
        subject_ids=[str(user)],
        subject_count=1,
    )
    purge_by_scope(writer.store, request, writer=writer)
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    assert [r[-2] for r in facts(writer, head)] == ["retained"]
    for table in LIST_CURRENT_TABLES:
        assert writer.store.execute(
            f"SELECT count() FROM {table.name} WHERE raw_user_id = %(erased)s "
            "OR canonical_user_id = %(erased)s",
            {"erased": str(user)},
        ) == [(0,)]
    for table in (LIST_CURRENT_ROOTS, LIST_CURRENT_MEMBERS):
        assert writer.store.execute(
            f"SELECT count() FROM {table.name} WHERE input = 'private'"
        ) == [(0,)]
    assert "input" not in LIST_USER_ACTIVITY.columns


@pytest.mark.django_db(transaction=True)
def test_current_facts_without_retained_anchor_are_scrubbed_on_finite_policy(
    current_writer,
):
    writer = current_writer
    source_row(writer, session=uuid4(), at=AT, input="unproved anchor")
    old = publish_current(writer, [("trace-a", "root")])
    result = expire_project(
        writer, [], retention_days=lambda: 1, now=AT + timedelta(days=3)
    )
    assert result["purged"]
    old.refresh_from_db()
    assert old.revoked_at is not None
    for table in LIST_CURRENT_TABLES:
        assert writer.store.execute(f"SELECT count() FROM {table.name}") == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_root_payload_uses_frozen_member_even_when_source_changes_mid_prepare(
    current_writer, monkeypatch
):
    writer = current_writer
    session = uuid4()
    source_row(writer, session=session, at=AT, input="frozen member")
    original = writer.store.freeze_select
    changed = False

    def freeze(table, *args, **kwargs):
        nonlocal changed
        payload = original(table, *args, **kwargs)
        if table == LIST_CURRENT_MEMBERS and payload.rows and not changed:
            changed = True
            source_row(
                writer,
                session=session,
                at=AT,
                version=10,
                input="later child",
                parent_span_id="parent",
            )
        return payload

    monkeypatch.setattr(writer.store, "freeze_select", freeze)
    head = publish_current(writer, [("trace-a", "root")])
    assert changed
    for table in (LIST_CURRENT_ROOTS, LIST_CURRENT_MEMBERS):
        assert facts(writer, head, table)[0][4:6] == (0, "frozen member")
    activity, params = committed_rows_select(
        LIST_USER_ACTIVITY,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    assert writer.store.execute(
        f"SELECT is_deleted, source_version, canonical_session_id FROM ({activity})",
        params,
    ) == [(0, 1, str(session))]


@pytest.mark.django_db(transaction=True)
def test_production_scalar_refresh_large_inputs_progress_replay_and_cleanup(
    current_writer, workspace, observe_project, monkeypatch
):
    from tracer.services.derived_metrics.changes import Dependency
    from tracer.services.derived_metrics.deletion_protocol import _intent
    from tracer.services.derived_metrics.publication import MAX_BATCH_BYTES
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_publication_clickhouse import current_fields
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        change_for,
        definition_for,
    )

    writer = current_writer
    session = uuid4()
    at = writer.lease.clock() - timedelta(hours=2)
    definition = definition_for(writer, workspace, observe_project)
    # Valid native source: 500 distinct traces in one conversation, each with a
    # 34 KiB input. The scalar field needs only resolved, not these message bytes.
    writer.store.execute(
        "INSERT INTO spans (project_id, org_id, trace_id, id, trace_session_id, "
        "start_time, end_time, created_at, updated_at, _version, input, attrs_number) "
        "SELECT toUUID(%(project)s), toUUID(%(org)s), concat('trace-', toString(number)), "
        "'root', toUUID(%(session)s), %(at)s, %(at)s, %(at)s, %(at)s, 1, "
        "repeat('x', 34 * 1024), map('resolved', toFloat64(4)) FROM numbers(500)",
        {
            "project": str(writer.lease.project_id),
            "org": str(writer.org_id),
            "session": str(session),
            "at": at,
        },
    )
    assert writer.store.execute("SELECT count(), sum(length(input)) FROM spans") == [
        (500, 17_408_000)
    ]
    assert 17_408_000 > MAX_BATCH_BYTES
    dependencies = [
        Dependency(f"trace-{i}", "root", str(session), str(session), "", "", at)
        for i in range(500)
    ]
    scope = Scope(writer.org_id, workspace.id, observe_project.id)

    def refresh(cursor):
        return writer.refresh(
            change_for(session, cursor=cursor, dependencies=dependencies),
            [definition],
            scope,
            start=at - timedelta(days=1),
            end=at + timedelta(days=1),
            as_of=at + timedelta(hours=1),
        )

    first = refresh(1)
    assert current_fields(writer, first)[0][1] == 4.0
    assert MetricProjectRefresh.for_project(scope.project).watermark_ns == 1
    # Lose the acknowledgement after staging a normal metric batch. Recovery
    # must replay its frozen scalar bytes, without copying the inactive index.
    verify = writer.store.verify

    def invisible(table, attempt, batch):
        if attempt.source_ceilings.get("cursor_updates", {}).get("watermark_ns") == 2:
            raise MetricVisibilityError("injected missing acknowledgement")
        return verify(table, attempt, batch)

    monkeypatch.setattr(writer.store, "verify", invisible)
    with pytest.raises(MetricVisibilityError):
        refresh(2)
    assert MetricProjectRefresh.for_project(scope.project).watermark_ns == 1
    pending = MetricRefreshAttempt.objects.get(
        project_id=scope.project, source_ceilings__cursor_updates__watermark_ns=2
    )
    source_row(
        writer,
        session=session,
        trace="trace-0",
        at=at,
        version=2,
        updated_at=at + timedelta(seconds=1),
        input="changed after freeze",
        attrs_number={"resolved": 99.0},
    )
    monkeypatch.setattr(writer.store, "verify", verify)
    second = writer.stage_and_publish(pending)
    assert writer.stage_and_publish(pending).id == second.id
    assert MetricProjectRefresh.for_project(scope.project).watermark_ns == 2
    assert current_fields(writer, second)[0][1] == 4.0
    assert second.published_by.batches.filter(verified_at__isnull=True).count() == 0
    assert not MetricRefreshAttempt.objects.filter(
        project_id=scope.project,
        batches__table_name__in=[table.name for table in LIST_CURRENT_TABLES],
    ).exists()
    for table in LIST_CURRENT_TABLES:
        assert writer.store.execute(f"SELECT count() FROM {table.name}") == [(0,)]
    # Normal project erasure still clears scalar rows and any frozen payloads;
    # large message inputs were never copied into auxiliary metric stores.
    request = _intent(
        organization_id=writer.org_id, project_id=scope.project, scope="project"
    )
    purge_by_scope(writer.store, request, writer=writer)
    assert writer.store.execute("SELECT count() FROM metric_field_values") == [(0,)]
    assert not MetricRefreshAttempt.objects.filter(
        project_id=scope.project, ledger_chunks__isnull=False
    ).exists()
    for table in LIST_CURRENT_TABLES:
        assert writer.store.execute(f"SELECT count() FROM {table.name}") == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_pruning_unpublished_unproven_facts_does_not_revoke_retained_metric_pin(
    current_writer,
):
    from tracer.services.derived_metrics.retention import (
        purge_checkpoint,
        retirement_plan,
    )
    from tracer.tests.test_derived_metric_publication_clickhouse import prepare_field

    writer = current_writer
    now = writer.lease.clock()
    at = now - timedelta(hours=2)
    initial, _ = prepare_field(writer, unit_id="retained-owner", start=at)
    head = writer.stage_and_publish(initial)
    pin = capture_pin(
        writer.lease.project_id, consumer_kind="snapshot", ttl=timedelta(minutes=5)
    )
    source_row(writer, session=uuid4(), at=at, input="no authoritative owner")
    draft = writer.begin(ceilings={"as_of": now})
    assert freeze_changes(
        writer,
        draft,
        head,
        keys=[("trace-a", "root")],
        as_of=now,
        cutoff=now - timedelta(days=1),
    )
    final = retirement_plan(writer, draft, head, [], now - timedelta(days=1))
    assert final.id != draft.id
    assert final.source_ceilings["logical_expiry"] is False
    writer.seal(final)
    new = writer.stage_and_publish(final)
    purge_checkpoint(writer, final, new)
    assert pin.is_usable()
    head.refresh_from_db()
    assert head.revoked_at is None
    for table in LIST_CURRENT_TABLES:
        assert writer.store.execute(f"SELECT count() FROM {table.name}") == [(0,)]
