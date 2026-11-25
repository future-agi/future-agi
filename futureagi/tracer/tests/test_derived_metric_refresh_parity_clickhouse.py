# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Saved filter membership is identical in native preview and committed refresh."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricVersion,
    MetricProjectRefresh,
)
from tracer.services.derived_metrics import refresh as refresh_module
from tracer.services.derived_metrics import service
from tracer.services.derived_metrics.admission import NativeMetricAnalytics
from tracer.services.derived_metrics.changes import (
    ChangeBatch,
    UnitKey,
    discover_changes,
)
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.storage import BUCKET_TOTALS, FIELD_VALUES
from tracer.tests.test_derived_metric_preview import NativePreviewAdapter, client_patch
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    definition_for,
    metric_bucket_rows,
)


def saved_version(base, filters, *, version=2):
    return DerivedMetricVersion.objects.create(
        metric=base.metric,
        version=version,
        **{
            **base.semantics(),
            "cohort_key": "",
            "filters": deepcopy(filters),
            "definition_hash": str(version) * 64,
        },
        backfill_status="complete",
    )


def seed_sources(writer, scope, now):
    sessions = []
    for value, model, provider in [(2, "m1", "p1"), (6, "m1", "p2"), (10, "m2", "p1")]:
        session = uuid4()
        sessions.append(str(session))
        writer.store.execute(
            """INSERT INTO spans (id, project_id, org_id, trace_id, trace_session_id,
            start_time, created_at, updated_at, model, provider, attrs_number)
            VALUES (%(id)s, %(project_id)s, %(org_id)s, %(trace)s, %(session)s,
                %(at)s, %(at)s, %(at)s, %(model)s, %(provider)s, map('resolved', %(value)s))""",
            {
                **scope.parameters(),
                "id": str(uuid4()),
                "trace": str(uuid4()),
                "session": str(session),
                "at": now - timedelta(hours=2),
                "model": model,
                "provider": provider,
                "value": value,
            },
        )
    return sessions


def assert_preview_refresh_parity(writer, definition, scope, sessions, now, expected):
    cache.clear()
    start, end = now - timedelta(days=1), now - timedelta(seconds=1)
    with client_patch(NativePreviewAdapter(writer.store.client)):
        preview = service.preview(
            definition,
            scope,
            start,
            end,
            [],
            "",
            actor_id=uuid4(),
            include_samples=True,
        )
    change = ChangeBatch(
        (), tuple(UnitKey("conversation", unit) for unit in sessions), (), (), (), {}
    )
    head = writer.refresh(change, [definition], scope, start=start, end=end, as_of=now)
    sql, params = committed_rows_select(
        FIELD_VALUES, head, org_id=scope.organization, project_id=scope.project
    )
    params.update(metric=str(definition.metric_id), version=definition.version)
    rows = writer.store.execute(
        f"SELECT unit_id, value_number FROM ({sql}) WHERE field_id = toUUID(%(metric)s) "
        "AND field_version = %(version)s AND is_deleted = 0 ORDER BY unit_id",
        params,
    )
    assert sorted(row[1] for row in rows) == expected
    samples = preview["sample_units"]
    assert {r[0]: r[1] for r in rows} == {
        r["unit_id"]: r["value_number"] for r in samples
    }
    sql, params = committed_rows_select(
        BUCKET_TOTALS, head, org_id=scope.organization, project_id=scope.project
    )
    params.update(metric=str(definition.metric_id), version=definition.version)
    totals = writer.store.execute(
        f"SELECT sum(unit_count), sum(weighted_n), sum(weighted_d) FROM ({sql}) "
        "WHERE metric_id = toUUID(%(metric)s) AND metric_version = %(version)s AND is_deleted = 0",
        params,
    )[0]
    assert totals == (len(expected), float(sum(expected)), float(len(expected)))
    if expected:
        result = preview["results"][0]
        assert result["unit_count"] == totals[0]
        assert result["value"] == pytest.approx(totals[1] / totals[2])
    else:
        assert not preview["results"] or preview["results"][0]["unit_count"] == 0
    cache.clear()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "filters,expected",
    [
        ([{"field": "model", "value": "m1"}], [2.0, 6.0]),
        ([{"field": "model", "value": "no-match"}], []),
        (
            [{"field": "model", "value": "m1"}, {"field": "provider", "value": "p1"}],
            [2.0],
        ),
    ],
)
def test_live_saved_filters_match_preview_and_refresh(
    live_writer, workspace, observe_project, monkeypatch, filters, expected
):
    writer = live_writer
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    now = timezone.now().replace(microsecond=0)
    sessions = seed_sources(writer, scope, now)
    definition = saved_version(
        definition_for(writer, workspace, observe_project), filters
    )
    compiler, calls = refresh_module.unit_values_select, []

    def compile_saved(definition, *args, **kwargs):
        assert kwargs["filters"] == definition.filters
        calls.append(deepcopy(kwargs["filters"]))
        return compiler(definition, *args, **kwargs)

    monkeypatch.setattr(refresh_module, "unit_values_select", compile_saved)
    before = deepcopy(definition.semantics())
    assert_preview_refresh_parity(writer, definition, scope, sessions, now, expected)
    definition.refresh_from_db()
    assert definition.semantics() == before and calls == [filters]


@pytest.mark.django_db(transaction=True)
def test_live_active_and_draft_versions_keep_distinct_immutable_filter_membership(
    live_writer, workspace, observe_project
):
    writer = live_writer
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    now = timezone.now().replace(microsecond=0)
    sessions = seed_sources(writer, scope, now)
    base = definition_for(writer, workspace, observe_project)
    active = saved_version(base, [{"field": "model", "value": "m1"}])
    draft = saved_version(base, [{"field": "provider", "value": "p1"}], version=3)
    DerivedMetric.activate_version(base.metric_id, active.id, expected_active_id=None)
    assert_preview_refresh_parity(writer, active, scope, sessions, now, [2.0, 6.0])
    assert_preview_refresh_parity(writer, draft, scope, sessions, now, [2.0, 10.0])
    base.metric.refresh_from_db()
    assert base.metric.active_version_id == active.id
    assert active.filters != draft.filters


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("anchor_age_hours", [18, 72], ids=["retained", "expired"])
@pytest.mark.parametrize("still_matches", [True, False])
def test_saved_filters_never_supply_the_conversation_anchor_or_arrival_clock(
    live_writer, workspace, observe_project, still_matches, anchor_age_hours
):
    from tracer.tests.test_derived_metric_retention_clickhouse import anonymous_parts

    writer, now = live_writer, timezone.now().replace(microsecond=0)
    writer.retention_days = lambda: 1
    definition = saved_version(
        definition_for(writer, workspace, observe_project),
        [{"field": "model", "value": "included"}],
    )
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    session, trace = uuid4(), uuid4()

    def seed(name, at, model, arrival, *, created=None):
        writer.store.execute(
            "INSERT INTO spans (id, trace_id, project_id, org_id, trace_session_id, "
            "start_time, created_at, updated_at, _version, model, attrs_number) "
            "VALUES (%(name)s, %(trace)s, %(project_id)s, %(org_id)s, %(session)s, "
            "%(at)s, %(created)s, %(arrival)s, %(revision)s, %(model)s, map('resolved', 4.))",
            {
                **scope.parameters(),
                "name": name,
                "trace": str(trace),
                "session": str(session),
                "at": at,
                "created": created or arrival,
                "arrival": arrival,
                "model": model,
                "revision": int(arrival.timestamp()) * 10**9,
            },
        )

    def refresh(cut, changed_ids):
        state = MetricProjectRefresh.for_project(scope.project)
        head = state.head_manifest
        attempts = [str(a) for _, a in head.allowed_attempts()] if head else []
        change = discover_changes(
            NativeMetricAnalytics(writer.store.client),
            org_id=str(scope.organization),
            project_id=str(scope.project),
            committed_attempts=attempts,
            watermark_ns=state.watermark_ns,
            eval_watermark_ns=state.eval_watermark_ns,
            remap_cursor_ns=state.remap_cursor_ns,
            ceiling=cut,
        )
        assert {key.span_id for key in change.source_keys} == changed_ids
        assert change.units == (UnitKey("conversation", str(session)),)
        head = writer.refresh(
            change,
            [definition],
            scope,
            start=now - timedelta(days=4),
            end=cut,
            as_of=cut,
        )
        state.refresh_from_db()
        assert state.watermark_ns == int(cut.timestamp()) * 10**9
        return head

    # Distinct quarters; the span's event time is not its revision's arrival.
    hour = now.replace(minute=0, second=0)
    old_bucket = hour - timedelta(hours=12)
    new_bucket = hour - timedelta(hours=anchor_age_hours)
    recent = old_bucket + timedelta(minutes=7)
    earlier = new_bucket + timedelta(minutes=11)
    seed("recent", recent, "included", recent)
    initial = refresh(now - timedelta(hours=3), {"recent"})
    assert metric_bucket_rows(writer, initial, definition) == [
        (old_bucket, "", 1, 4.0, 1.0)
    ]

    changed_ids = {"earlier-filtered-out"}
    if not still_matches:
        seed("recent", recent, "excluded", now - timedelta(hours=2), created=recent)
        changed_ids.add("recent")
    # Settled by the second cut, but newer than every matching row.
    latest_arrival = now - timedelta(hours=1)
    seed("earlier-filtered-out", earlier, "excluded", latest_arrival)
    # Independent source truth: filter membership cannot move the unit's anchor
    # or last arrival. An expired conversation must not survive anonymously.
    anchor, arrival_ns, members, total = writer.store.execute(
        "SELECT min(start_time), toUnixTimestamp64Nano(max(updated_at)), countIf(model = 'included'), "
        "sumIf(attrs_number['resolved'], model = 'included') FROM spans FINAL "
        "WHERE project_id = %(project_id)s AND org_id = %(org_id)s "
        "AND trace_session_id = toUUID(%(session)s)",
        {**scope.parameters(), "session": str(session)},
    )[0]
    assert anchor == earlier
    assert arrival_ns == int(latest_arrival.timestamp()) * 10**9
    assert members == int(still_matches)
    retained = anchor >= now - timedelta(days=1)
    head = refresh(now, changed_ids)
    sql, params = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=scope.organization,
        project_id=scope.project,
        key_in={
            "field_id": (definition.metric_id,),
            "field_version": (definition.version,),
        },
    )
    fields = writer.store.execute(
        f"SELECT unit_id, value_number, unit_start, last_arrival_ns, is_deleted FROM ({sql})",
        params,
    )
    if retained:
        value = total / members if members else None
        assert fields == [(str(session), value, anchor, arrival_ns, int(not members))]
    else:
        assert fields == []
    assert anonymous_parts(writer, head) == []
    expected_buckets = [(new_bucket, "", 1, total, 1.0)] if retained and members else []
    assert metric_bucket_rows(writer, head, definition) == expected_buckets


@pytest.mark.django_db(transaction=True)
def test_filter_excluded_definition_cannot_tombstone_another_fields_due_settle(
    live_writer, workspace, observe_project
):
    from tracer.services.derived_metrics.storage import UNIT_STATE

    writer, now = live_writer, timezone.now().replace(microsecond=0)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    base = definition_for(writer, workspace, observe_project)
    excluded = saved_version(base, [{"field": "model", "value": "no-match"}])
    session = uuid4()
    writer.store.execute(
        "INSERT INTO spans (id, trace_id, project_id, org_id, trace_session_id, start_time, created_at, updated_at, attrs_number) "
        "VALUES ('arrival', %(trace)s, %(project_id)s, %(org_id)s, %(session)s, %(event)s, %(event)s, %(arrival)s, map('resolved', 4.))",
        {
            **scope.parameters(),
            "trace": str(uuid4()),
            "session": str(session),
            "event": now - timedelta(hours=2),
            "arrival": now,
        },
    )
    proposal = ChangeBatch((), (UnitKey("conversation", str(session)),), (), (), (), {})
    kwargs = {"start": now - timedelta(days=1), "end": now + timedelta(days=1)}
    head = writer.refresh(proposal, [base, excluded], scope, as_of=now, **kwargs)
    sql, params = committed_rows_select(
        UNIT_STATE, head, org_id=writer.org_id, project_id=scope.project
    )
    assert writer.store.execute(f"SELECT due_at, is_deleted FROM ({sql})", params) == [
        (now + timedelta(minutes=30), 0)
    ]
    head = writer.refresh(
        proposal, [base, excluded], scope, as_of=now + timedelta(minutes=30), **kwargs
    )
    sql, params = committed_rows_select(
        FIELD_VALUES, head, org_id=writer.org_id, project_id=scope.project
    )
    rows = writer.store.execute(
        f"SELECT field_version, value_number, is_deleted FROM ({sql}) ORDER BY field_version",
        params,
    )
    assert rows == [(1, 4.0, 0), (2, None, 1)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("merged", [False, True])
def test_saved_filters_cannot_turn_multi_user_membership_into_a_single_user(
    live_writer, workspace, observe_project, merged
):
    writer, now = live_writer, timezone.now().replace(microsecond=0)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    definition = saved_version(
        definition_for(writer, workspace, observe_project),
        [{"field": "model", "value": "included"}],
    )
    session, first, second = uuid4(), uuid4(), uuid4()
    for index, user in enumerate((first, second)):
        writer.store.execute(
            "INSERT INTO spans (id, trace_id, project_id, org_id, trace_session_id, end_user_id, start_time, created_at, updated_at, model, attrs_number) "
            "VALUES (%(id)s, %(trace)s, %(project_id)s, %(org_id)s, %(session)s, %(user)s, %(at)s, %(at)s, %(at)s, %(model)s, map('resolved', 4.))",
            {
                **scope.parameters(),
                "id": f"member-{index}",
                "trace": str(uuid4()),
                "session": str(session),
                "user": str(user),
                "at": now - timedelta(hours=2 + index),
                "model": "included" if index == 0 else "excluded",
            },
        )
    if merged:
        writer.store.execute(
            "INSERT INTO end_user_id_remap (old_id, new_id) VALUES (%(old)s, %(new)s)",
            {"old": str(first), "new": str(second)},
        )
    proposal = ChangeBatch((), (UnitKey("conversation", str(session)),), (), (), (), {})
    head = writer.refresh(
        proposal, [definition], scope, start=now - timedelta(days=1), end=now, as_of=now
    )
    sql, params = committed_rows_select(
        FIELD_VALUES, head, org_id=writer.org_id, project_id=scope.project
    )
    assert writer.store.execute(
        f"SELECT user_id, value_number, unit_start FROM ({sql}) WHERE is_deleted = 0",
        params,
    ) == [(str(first) if merged else "", 4.0, now - timedelta(hours=3))]
