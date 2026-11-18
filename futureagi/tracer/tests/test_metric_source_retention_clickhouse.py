# ruff: noqa: F811 -- shared isolated native fixtures
"""Physical source-retention tests against PostgreSQL and ClickHouse."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import MetricProjectRefresh, MetricRefreshAttempt
from tracer.services.derived_metrics import retention
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.storage import BUCKET_TOTALS, RETIRED_PARTS
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    current_fields,
    default_row_select,
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_retention_clickhouse import (
    numeric_definition,
    seed_fields_and_buckets,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_expiry_fences_saved_metric_sql_through_verified_scrub(
    live_writer, user, observe_project, monkeypatch
):
    from tracer.models.derived_metric import MetricErasureRequest
    from tracer.services.derived_metrics import source_retention
    from tracer.sql_workbench.access import Actor
    from tracer.sql_workbench.service import snapshot
    from tracer.tests.test_metric_source_retention import metric_job

    writer = live_writer
    actor = Actor(user.pk, writer.org_id, writer.workspace_id)
    monkeypatch.setattr(source_retention, "current_cutoff", lambda *a, **k: None)
    definition = numeric_definition(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [("old", timezone.now() - timedelta(days=10), 9, "old-user")],
    )
    result = {"rows": [[9]]}

    def saved():
        return metric_job(actor, observe_project, state="succeeded", result=result)

    # Insert real SQL job rows on either side of the expiry intent. Both hooks
    # delegate: publication, native deletes and final verification all run.
    publish, execute = writer.stage_and_publish, writer.store.execute
    jobs, verified = [], []

    def before_intent(attempt):
        jobs.append(saved())
        return publish(attempt)

    def observe_verification(sql, params=None, **kwargs):
        rows = execute(sql, params, **kwargs)
        if "purge_through" in (params or {}) and sql.startswith("SELECT count() FROM"):
            if not verified:
                jobs.append(saved())
            verified.append(writer.lease.clock())
        return rows

    monkeypatch.setattr(writer, "stage_and_publish", before_intent)
    monkeypatch.setattr(writer.store, "execute", observe_verification)
    outcome = retention.expire_project(writer, [definition], retention_days=lambda: 7)
    assert outcome["purged"]
    receipt = MetricErasureRequest.objects.get(
        project_key=observe_project.pk, store_payload__protocol="metric_expiry"
    )
    before, during = jobs
    assert before.created_at <= receipt.requested_at <= during.created_at
    assert (
        receipt.status == "done" and receipt.receipts["final_scrub"]["remaining"] == 0
    )
    assert during.created_at <= verified[-1] <= receipt.completed_at
    assert snapshot(before)["result"] == snapshot(during)["result"] == {}
    assert snapshot(saved())["result"] == result


@pytest.mark.parametrize("scheduled", [False, True], ids=["refresh", "scheduled"])
def test_writers_preserve_an_empty_retired_namespace(
    live_writer, workspace, observe_project, scheduled
):
    from tracer.models.derived_metric import MetricAttemptBatch
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        bucket_rows,
        change_for,
        definition_for,
        seed_span_revision,
    )

    writer = live_writer
    scope = Scope(writer.org_id, writer.workspace_id, observe_project.pk)
    definition = definition_for(writer, workspace, observe_project)
    session, trace = uuid4(), uuid4()
    now = timezone.now()
    old = now - timedelta(days=3)
    window = {"start": old - timedelta(days=1), "end": now}
    kind = MetricAttemptBatch.Table.BUCKET_RETIRED_PARTS

    def assert_empty(head):
        # Check staging receipts as well as physical rows. A transient fold is
        # legitimate; a staged batch/range with even one retired row is not.
        attempt = head.published_by
        assert not attempt.batches.filter(table_name=kind, row_count__gt=0).exists()
        assert not attempt.compaction_ranges.filter(
            table_kind=kind, row_count__gt=0
        ).exists()
        assert writer.store.execute(f"SELECT count() FROM {RETIRED_PARTS.name}") == [
            (0,)
        ]

    seed_span_revision(writer, scope, trace, "span", session, old, 4, revision=1)
    head = writer.refresh(
        change_for(session),
        [definition],
        scope,
        as_of=now - timedelta(minutes=1),
        **window,
    )
    assert bucket_rows(writer, head)  # The ordinary refresh aggregated real fields.
    assert_empty(head)
    # Exercise both the batch-copy and server-side range-copy implementations.
    assert_empty(writer.compact(server_side=scheduled))

    writer.retention_days = lambda: 1
    if scheduled:
        assert retention.expire_project(
            writer, [definition], retention_days=writer.retention_days, now=now
        )["purged"]
        head = MetricProjectRefresh.for_project(observe_project.pk).head_manifest
    else:
        seed_span_revision(
            writer, scope, trace, "span", session, old, 8, revision=2, arrival=now
        )
        head = writer.refresh(
            change_for(session, cursor=2), [definition], scope, as_of=now, **window
        )
    assert head.published_by.source_ceilings["logical_expiry"]
    assert current_fields(writer, head) == []
    assert_empty(head)


def test_expiry_removes_cross_version_contribution_from_retained_bucket(live_writer):
    from tracer.tests.test_derived_metric_refresh_clickhouse import metric_bucket_rows

    writer = live_writer
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    first = numeric_definition(uuid4())
    second = numeric_definition(first.metric_id)
    second.version = 2
    recent = now - timedelta(hours=2)
    # Expiry is per logical conversation; recipe versions have separate fields.
    seed_fields_and_buckets(
        writer, first, [("cross-version", now - timedelta(days=3), 4, "a")]
    )
    head = seed_fields_and_buckets(
        writer, second, [("cross-version", recent, 11, "a"), ("keep", recent, 7, "b")]
    )

    def stats(manifest):
        return [row[2:] for row in metric_bucket_rows(writer, manifest, second)]

    assert stats(head) == [(2, 18, 2)]
    assert retention.expire_project(
        writer, [first, second], retention_days=lambda: 1, now=now
    )["purged"]
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    assert [row[:2] for row in current_fields(writer, head)] == [("keep", 7)]
    # The surviving quarter is wholly retained, but its old total included a
    # now-removed field. The other conversation in that same group must remain.
    assert stats(head) == [(1, 7, 1)]


def test_expiry_rebuilds_cross_version_span_bucket(live_writer):
    from tracer.tests.test_derived_metric_refresh_clickhouse import metric_bucket_rows

    writer = live_writer
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    recent = now - timedelta(hours=1)
    first = numeric_definition(uuid4(), level="span")
    second = numeric_definition(first.metric_id, level="span")
    second.version = 2
    trace = uuid4()
    victim, mate = (f'["{trace}","{span}"]' for span in ("moved", "keep"))
    seed_fields_and_buckets(writer, first, [(victim, now - timedelta(days=3), 13, "")])
    base = seed_fields_and_buckets(
        writer, second, [(victim, recent, 11, ""), (mate, recent, 7, "")]
    )

    def stats(head, definition):
        return [r[2:] for r in metric_bucket_rows(writer, head, definition)]

    assert stats(base, first) == [(1, 13, 1)] and stats(base, second) == [(2, 18, 2)]
    assert retention.expire_project(
        writer, [first, second], retention_days=lambda: 1, now=now
    )["purged"]
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    assert [r[:2] for r in current_fields(writer, head)] == [(mate, 7)]
    assert stats(head, first) == [] and stats(head, second) == [(1, 7, 1)]


@pytest.mark.parametrize("budget", ["rows", "bytes"])
def test_small_expiry_keys_publish_together_under_unchanged_capture_cap(
    live_writer, monkeypatch, budget
):
    from tracer.services.derived_metrics import frozen_relations
    from tracer.tests.test_derived_metric_refresh_clickhouse import metric_bucket_rows

    writer = live_writer
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    first = numeric_definition(uuid4())
    second = numeric_definition(first.metric_id)
    second.version = 2
    times = [now - timedelta(hours=i) for i in (8, 6, 4)]
    seed_fields_and_buckets(
        writer, first, [(f"gone-{i}", now - timedelta(days=3), 4, "") for i in range(3)]
    )
    recent_rows = [
        (f"{name}-{i}", at, value, "")
        for i, at in enumerate(times)
        for name, value in (("gone", 11), ("keep", 7), ("mate", 3))
    ]
    # Untouched keys on either side of a page boundary and in the final tail.
    recent_rows += [
        (f"untouched-{i}", at + timedelta(minutes=15), 21, "")
        for i, at in enumerate(times)
    ]
    base = seed_fields_and_buckets(writer, second, recent_rows)
    assert [r[2:] for r in metric_bucket_rows(writer, base, second)] == [
        (3, 21, 3),
        (1, 21, 1),
    ] * 3
    # Three distinct keys, six surviving fields, but only two per key. Do
    # not raise the limit, change the fold width, or retry the publication.
    if budget == "rows":
        monkeypatch.setattr(frozen_relations, "CAPTURE_ROWS", 2)
    else:
        table = retention.FIELD_VALUES
        fields, params = committed_rows_select(
            table, base, org_id=writer.org_id, project_id=writer.lease.project_id
        )
        sizes = [
            size
            for (size,) in writer.store.execute(
                f"SELECT sum(length(formatRow('RowBinary', {', '.join(table.columns)}))) "
                f"FROM ({fields}) WHERE startsWith(unit_id, 'keep-') OR "
                "startsWith(unit_id, 'mate-') GROUP BY bucket_start",
                params,
            )
        ]
        assert len(sizes) == 3 and sum(sizes) > max(sizes) >= 3 * 24
        monkeypatch.setattr(frozen_relations, "CAPTURE_BYTES", max(sizes))
    assert retention.expire_project(
        writer, [first, second], retention_days=lambda: 1, now=now
    )["purged"]
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    assert head.generation == base.generation + 1
    assert [r[2:] for r in metric_bucket_rows(writer, head, second)] == [
        (2, 10, 2),
        (1, 21, 1),
    ] * 3
    assert all(not r[0].startswith("gone-") for r in current_fields(writer, head))


@pytest.mark.parametrize("collect", [False, True], ids=["retry", "after-ledger-ttl"])
def test_failed_classified_expiry_keeps_blocked_recovery(
    live_writer, monkeypatch, collect
):
    from tracer.services.derived_metrics.deletion_protocol import collect_history
    from tracer.services.derived_metrics.frozen_relations import (
        FrozenRelationOverflow,
        SessionCapture,
    )
    from tracer.services.derived_metrics.refresh import RefreshWriter

    writer = live_writer
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    definition = numeric_definition(uuid4())
    seed_fields_and_buckets(
        writer, definition, [("keep", now - timedelta(hours=1), 7, "")]
    )
    base = writer.compact()
    unknown = "unproven-owner"
    predecessor = writer.begin(ledger={"dirty_before": [{"unit_id": unknown}]})
    writer.seal(predecessor)

    def expire():
        return retention.expire_project(
            writer, [definition], retention_days=lambda: 1, now=now
        )

    build = SessionCapture._build

    def interrupted(capture):
        if capture.role == "checkpoint_bucket_keys":
            raise FrozenRelationOverflow("interrupted after classification")
        return build(capture)

    with monkeypatch.context() as fault:
        fault.setattr(SessionCapture, "_build", interrupted)
        with pytest.raises(FrozenRelationOverflow, match="after classification"):
            expire()
    state = MetricProjectRefresh.for_project(writer.lease.project_id)
    assert state.head_manifest_id == base.pk
    failed = MetricRefreshAttempt.objects.get(
        project_id=state.project_id, generation=state.generation_high_water
    )
    assert failed.state == "aborted" and failed.source_ceilings["recovery_blocked"]
    assert not failed.ledger_chunks.exclude(part="cleanup_intent").exists()
    predecessor.refresh_from_db()
    assert (
        predecessor.state == "aborted"
        and predecessor.abort_reason == "retention_replan"
    )
    if collect:
        assert failed.ledger_chunks.exists()
        intent = collect_history(
            state.project_id, now=failed.expires_at + timedelta(seconds=1)
        )
        assert str(failed.pk) in {
            item["id"] for item in intent.store_payload["attempts"]
        }
        assert not failed.ledger_chunks.exists()
        failed.refresh_from_db()
        assert failed.abort_reason == MetricRefreshAttempt.GC_BLOCKED_HANDOFF
        execute = writer.store.execute
        orphan_probes = []

        def registered(sql, params=None, **kwargs):
            if "gc_registered" in (params or {}):
                orphan_probes.append(failed.pk in params["gc_registered"])
            return execute(sql, params, **kwargs)

        with monkeypatch.context() as probe:
            probe.setattr(writer.store, "execute", registered)
            writer.scrub_orphans()
        assert orphan_probes and not any(orphan_probes)
    writer = RefreshWriter(
        writer.lease,
        writer.store,
        org_id=writer.org_id,
        workspace_id=writer.workspace_id,
        retention_days=writer.retention_days,
    )
    assert expire()["purged"]
    head = MetricProjectRefresh.for_project(state.project_id).head_manifest
    assert (
        head.pk != base.pk
        and head.published_by.source_ceilings["recovery_blocked"] is True
    )
    assert not head.published_by.source_ceilings["recovery_pending"]
    assert [r[:2] for r in current_fields(writer, head)] == [("keep", 7)]
    assert writer.compact().published_by.source_ceilings["recovery_blocked"] is True


def test_refresh_rebuilds_only_the_cross_version_quarter(
    live_writer, workspace, observe_project, monkeypatch
):
    from tracer.models.derived_metric import DerivedMetric, DerivedMetricVersion
    from tracer.services.derived_metrics import frozen_relations, refresh
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        change_for,
        metric_bucket_rows,
        seed_span_revision,
    )

    writer = live_writer
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    recent, other = now - timedelta(hours=2), now - timedelta(hours=1)
    victim = str(uuid4())
    scope = Scope(writer.org_id, writer.workspace_id, observe_project.pk)
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="Cross-version expiry",
    )
    first, second = [
        DerivedMetricVersion.objects.create(
            metric=metric,
            version=version,
            kind="average",
            level="conversation",
            formula="mean(y)",
            inputs=[
                {**numeric_definition(metric.pk).inputs[0], "attribute_key": "resolved"}
            ],
            selection_unit="session",
            weighting="none",
            cohort_key="region",
        )
        for version in (1, 2)
    ]
    seed_fields_and_buckets(
        writer,
        first,
        [
            (victim, now - timedelta(hours=6), 4, "a"),
            ("other-version", recent, 23, "b"),
        ],
    )
    base = seed_fields_and_buckets(
        writer,
        second,
        [
            (victim, recent, 11, "a"),
            ("keep", recent, 7, "b"),
            ("other-quarter", other, 29, "c"),
            ("other-quarter-mate", other, 5, "d"),
        ],
    )

    def stats(head, recipe):
        return [row[2:] for row in metric_bucket_rows(writer, head, recipe)]

    assert stats(base, second) == [(2, 18, 2), (2, 34, 2)]
    # Only the incoming v1 proposal has an expired anchor. Its ordinary dirty
    # ledger knows the former v1 quarter, not the independent v2/recent one.
    seed_span_revision(
        writer,
        scope,
        uuid4(),
        "earlier",
        victim,
        now - timedelta(days=3),
        4,
        revision=1,
    )
    writer.retention_days = lambda: 1
    # Record actual folds as well as final values: over-rebuilding an untouched
    # version can produce identical output while doing arbitrarily more work.
    folds = set()
    freeze_buckets = writer._freeze_buckets

    def record_folds(attempt, head, definition, *args, **kwargs):
        if kwargs.get("complete_fields"):
            folds.update((definition.version, at) for at in kwargs["_dirty_starts"])
        return freeze_buckets(attempt, head, definition, *args, **kwargs)

    monkeypatch.setattr(writer, "_freeze_buckets", record_folds)
    monkeypatch.setattr(frozen_relations, "CAPTURE_ROWS", 3)
    monkeypatch.setattr(refresh, "BUCKET_WINDOWS_PER_FOLD", 1)
    head = writer.refresh(
        change_for(victim),
        [first],
        scope,
        start=now - timedelta(days=4),
        end=now,
        as_of=now,
    )
    assert head.published_by.source_ceilings["logical_expiry"]
    assert {r[0] for r in current_fields(writer, head)} == {
        "other-version",
        "keep",
        "other-quarter",
        "other-quarter-mate",
    }
    assert stats(head, first) == [(1, 23, 1)]
    assert stats(head, second) == [(1, 7, 1), (2, 34, 2)]
    former = now - timedelta(hours=6)
    assert (1, former) in folds and (2, recent) in folds
    assert (2, former) not in folds and (1, recent) not in folds


@pytest.mark.parametrize("budget", ["rows", "bytes"])
def test_oversized_retained_quarter_folds_identity_ranges_in_one_checkpoint(
    live_writer, monkeypatch, budget
):
    from tracer.services.derived_metrics import frozen_relations
    from tracer.tests.test_derived_metric_refresh_clickhouse import metric_bucket_rows

    writer = live_writer
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    quarter = now - timedelta(hours=2)
    first = numeric_definition(uuid4())
    second = numeric_definition(first.metric_id)
    second.version = 2
    seed_fields_and_buckets(
        writer, first, [("victim", now - timedelta(days=3), 41, "")]
    )
    mates = [(f"mate-{i}", quarter, i, "") for i in range(1, 5)]
    base = seed_fields_and_buckets(
        writer, second, [("victim", quarter, 17, ""), *mates]
    )
    assert [r[2:] for r in metric_bucket_rows(writer, base, second)] == [(5, 27, 5)]
    if budget == "rows":
        monkeypatch.setattr(frozen_relations, "CAPTURE_ROWS", 1)
    else:
        table = retention.FIELD_VALUES
        fields, params = committed_rows_select(
            table, base, org_id=writer.org_id, project_id=writer.lease.project_id
        )
        sizes = writer.store.execute(
            f"SELECT length(formatRow('RowBinary', {', '.join(table.columns)})) FROM ({fields}) WHERE startsWith(unit_id, 'mate-')",
            params,
        )
        assert len(sizes) == 4
        monkeypatch.setattr(
            frozen_relations, "CAPTURE_BYTES", max(row[0] for row in sizes)
        )
    ranges = []
    finish = frozen_relations.FrozenRelations.finish

    def record_fold(relations, *, expected_children, coverage=None):
        result = finish(
            relations, expected_children=expected_children, coverage=coverage
        )
        ranges.append(coverage[0])
        return result

    monkeypatch.setattr(frozen_relations.FrozenRelations, "finish", record_fold)
    assert retention.expire_project(
        writer, [first, second], retention_days=lambda: 1, now=now
    )["purged"]
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    assert head.generation == base.generation + 1
    assert len(ranges) == 1 and len(ranges[0]) > 1
    assert sum(n for _, _, _, n, _ in ranges[0]) == len(mates)
    assert all(
        n <= frozen_relations.CAPTURE_ROWS and size <= frozen_relations.CAPTURE_BYTES
        for _, _, _, n, size in ranges[0]
    )
    assert sorted(row[:2] for row in current_fields(writer, head)) == [
        (unit, value) for unit, _, value, _ in mates
    ]
    assert [r[2:] for r in metric_bucket_rows(writer, head, second)] == [(4, 10, 4)]


def test_expiry_bucket_capture_overflow_preserves_head_for_retry(
    live_writer, monkeypatch
):
    from tracer.services.derived_metrics import frozen_relations
    from tracer.tests.test_derived_metric_refresh_clickhouse import metric_bucket_rows

    writer = live_writer
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    recent = now - timedelta(hours=2)
    first = numeric_definition(uuid4())
    second = numeric_definition(first.metric_id)
    second.version = 2
    seed_fields_and_buckets(
        writer, first, [("victim", now - timedelta(days=3), 4, "a")]
    )
    base = seed_fields_and_buckets(
        writer,
        second,
        [
            ("victim", recent, 11, "a"),
            ("keep", recent, 7, "b"),
            ("keep-too", recent, 3, "c"),
        ],
    )

    def expire():
        return retention.expire_project(
            writer, [first, second], retention_days=lambda: 1, now=now
        )

    for value in (1, 24):
        # First reject the fixed-width key, then admit the key but not even
        # one indivisible field. A quarter of multiple fitting rows can split.
        with monkeypatch.context() as caps:
            caps.setattr(frozen_relations, "CAPTURE_BYTES", value)
            with pytest.raises(frozen_relations.FrozenRelationOverflow):
                expire()
        state = MetricProjectRefresh.for_project(writer.lease.project_id)
        assert (
            state.head_manifest_id == base.pk and state.head_manifest.revoked_at is None
        )
        assert not MetricRefreshAttempt.objects.filter(
            project_id=writer.lease.project_id,
            state__in=MetricRefreshAttempt.OPEN_STATES,
        ).exists()
    assert expire()["purged"]
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    assert [r[2:] for r in metric_bucket_rows(writer, head, second)] == [(2, 10, 2)]


def test_expiry_without_expired_fields_or_totals_keeps_the_head(live_writer):
    from tracer.tests.test_derived_metric_refresh_clickhouse import metric_bucket_rows

    writer = live_writer
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    definition = numeric_definition(uuid4())
    base = seed_fields_and_buckets(
        writer, definition, [("keep", now - timedelta(hours=2), 7, "b")]
    )
    before = metric_bucket_rows(writer, base, definition)
    assert [r[2:] for r in before] == [(1, 7, 1)]
    assert not retention.expire_project(
        writer, [definition], retention_days=lambda: 1, now=now
    )["purged"]
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    assert head.pk == base.pk and metric_bucket_rows(writer, head, definition) == before


def test_expiry_deletes_legacy_retired_labels_and_all_aggregate_revisions(live_writer):
    writer = live_writer
    now = timezone.now().replace(hour=12, minute=7, second=0, microsecond=0)
    definition = numeric_definition(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [
            ("expired", now - timedelta(days=3), 4, "private-user"),
            (
                "boundary",
                now - timedelta(days=1) + timedelta(minutes=1),
                6,
                "boundary-user",
            ),
            ("retained", now - timedelta(hours=1), 9, "retained-user"),
        ],
    )
    # Migration fixture: identifier-bearing retired history from an old binary.
    attempt = writer.begin()
    for table in (RETIRED_PARTS, BUCKET_TOTALS):
        writer.freeze(
            attempt,
            table,
            default_row_select(
                table,
                {
                    "metric_id": "toUUID(%(metric)s)",
                    "metric_version": "toUInt32(1)",
                    "bucket_start": "toDateTime(%(old)s, 'UTC')",
                    "group_key": "'private-legacy-label'",
                    "unit_count": "toUInt64(1)",
                    "valid_count": "toUInt64(1)",
                    "weighted_n": "toFloat64(42)",
                    "weighted_d": "toFloat64(1)",
                },
            ),
            {"metric": str(definition.metric_id), "old": now - timedelta(days=10)},
        )
    writer.seal(attempt)
    writer.stage_and_publish(attempt)
    writer.compact()
    assert retention.expire_project(
        writer, [definition], retention_days=lambda: 1, now=now
    )["purged"]
    head = MetricProjectRefresh.objects.get(
        project_id=writer.lease.project_id
    ).head_manifest
    assert {row[0] for row in current_fields(writer, head)} == {"boundary", "retained"}
    for table in (RETIRED_PARTS, BUCKET_TOTALS):
        sql, params = committed_rows_select(
            table, head, org_id=writer.org_id, project_id=writer.lease.project_id
        )
        rows = writer.store.execute(
            f"SELECT group_key, bucket_start FROM ({sql})", params
        )
        assert all(row[0] != "private-legacy-label" for row in rows)
        if table == RETIRED_PARTS:
            assert rows == []
        else:
            assert rows and all(
                row[1].replace(tzinfo=now.tzinfo) >= now - timedelta(days=1)
                for row in rows
            )
    for table in (
        "metric_bucket_retired_parts",
        "metric_bucket_totals",
        "metric_bucket_totals_checkpoint",
    ):
        assert writer.store.execute(
            f"SELECT count() FROM {table} WHERE group_key = 'private-legacy-label'"
        ) == [(0,)]
    assert retention.expire_project(
        writer, [definition], retention_days=lambda: -1, now=now
    )["unlimited"]
    assert writer.store.execute("SELECT count() FROM metric_bucket_retired_parts") == [
        (0,)
    ]


def test_failed_expiry_closes_prepared_attempt_and_retry_keeps_progress(
    live_writer, monkeypatch
):
    writer = live_writer
    definition = numeric_definition(uuid4())
    now = timezone.now()
    seed_fields_and_buckets(
        writer,
        definition,
        [
            ("old", now - timedelta(days=3), 4, "old-user"),
            ("keep", now - timedelta(hours=1), 7, "keep-user"),
        ],
    )
    native = writer.freeze_current

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic interrupted expiry")

    monkeypatch.setattr(writer, "freeze_current", fail)
    with pytest.raises(RuntimeError, match="interrupted expiry"):
        retention.expire_project(
            writer, [definition], retention_days=lambda: 1, now=now
        )
    assert not MetricRefreshAttempt.objects.filter(
        project_id=writer.lease.project_id, state__in=MetricRefreshAttempt.OPEN_STATES
    ).exists()
    monkeypatch.setattr(writer, "freeze_current", native)
    assert retention.expire_project(
        writer, [definition], retention_days=lambda: 1, now=now
    )["purged"]


def test_history_kpi_and_trend_have_honest_native_finality_and_policy_guard(
    live_writer,
    workspace,
    observe_project,
    settings,
    monkeypatch,
):
    from tracer.models.derived_metric import DerivedMetric, DerivedMetricVersion
    from tracer.services.derived_metrics import reads, source_retention
    from tracer.services.derived_metrics.service import MetricUnavailableError
    from tracer.services.derived_metrics.storage import FIELD_VALUES
    from tracer.tests.test_derived_metric_publication_clickhouse import field_query

    settings.METRICS_ENABLED = 1
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    floor = {"at": now - timedelta(days=30)}
    monkeypatch.setattr(source_retention, "current_cutoff", lambda *a, **k: floor["at"])
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="Retained history read",
    )
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="custom",
        level="conversation",
        formula="mean(y)",
        inputs=numeric_definition(metric.pk).inputs,
        selection_unit="session",
        weighting="none",
        cohort_key="region",
        backfill_status="complete",
        history_mode="history",
    )
    writer = live_writer
    seed_fields_and_buckets(
        writer,
        definition,
        [
            ("final-unit", now - timedelta(hours=2), 7, "retained-user"),
        ],
    )
    attempt = writer.begin(versions=[{"id": definition.pk, "metric_id": metric.pk}])
    # The prior period has an open unit. Its proof must share the current read
    # without contributing that unit to the current disclosure or comparison.
    sql, params = field_query(
        "previous-open", metric=metric.pk, start=now - timedelta(hours=3)
    )
    sql = sql.replace("'ok' AS status", "'open' AS status")
    sql = sql.replace(
        "defaultValueOfTypeName('UInt64') AS last_arrival_ns",
        "toUInt64(%(arrival)s) AS last_arrival_ns",
    )
    writer.freeze(
        attempt, FIELD_VALUES, sql, {**params, "arrival": int(now.timestamp()) * 10**9}
    )
    writer.seal(
        attempt,
        cursors={"watermark_ns": int((now - timedelta(hours=1)).timestamp()) * 10**9},
    )
    writer.stage_and_publish(attempt)
    DerivedMetric.activate_version(metric.pk, definition.pk, expected_active_id=None)
    metric.refresh_from_db()
    window = {"window_start": now - timedelta(hours=2), "window_end": now}
    kpi = reads.read_kpi(metric, window, store=writer.store)
    assert kpi["partial"] and kpi["current"]["value"] == 7
    assert kpi["current"]["eligible_count"] == 1
    assert kpi["previous"] is None and kpi["open_units"] == 0
    assert "retention_floor" not in kpi["provenance"]
    trend = reads.read_trend(metric, {**window, "points": 8}, store=writer.store)
    assert trend["partial"] and trend["complete_through"] < now
    assert any(row["point_status"] == "not_calculated" for row in trend["results"])
    assert sum(row["eligible_count"] for row in trend["results"]) == 1
    floor["at"] = window["window_start"] + timedelta(microseconds=1)
    for reader in (reads.read_kpi, reads.read_trend):
        with pytest.raises(MetricUnavailableError, match="outside current retention"):
            reader(metric, window, store=writer.store)


def test_no_head_replan_scrubs_late_writes_without_admitting_a_project(live_writer):
    from tracer.models.derived_metric import (
        MetricAttemptLedgerChunk,
        MetricErasureRequest,
    )
    from tracer.services.derived_metrics.admission import run_expiry_pass
    from tracer.services.derived_metrics.deletion_protocol import _intent
    from tracer.services.derived_metrics.refresh import MetricRetentionChanged
    from tracer.tests.test_derived_metric_publication_clickhouse import prepare_field

    writer, now = live_writer, timezone.now()
    attempt, _ = prepare_field(
        writer, unit_id="unpublished-private", start=now - timedelta(days=3)
    )
    writer.retention_days = lambda: 1
    with pytest.raises(MetricRetentionChanged):
        writer.stage_and_publish(attempt)
    debt = MetricErasureRequest.objects.get(
        deletion_key=f"retention-replan:{attempt.pk}"
    )
    submission = _intent(
        organization_id=writer.org_id,
        project_id=writer.lease.project_id,
        scope="history",
        store_payload={
            "protocol": "metric_submission",
            "generation": attempt.generation,
            "attempt": str(attempt.pk),
            "shard": writer.store.identity(),
        },
    )
    params = {
        "org": str(writer.org_id),
        "project": str(writer.lease.project_id),
        "attempt": str(attempt.pk),
        "generation": attempt.generation,
    }
    insert = "INSERT INTO metric_field_values (org_id, project_id, attempt_id, generation, unit_id) VALUES (%(org)s, %(project)s, %(attempt)s, %(generation)s, 'late-private')"
    writer.store.execute(insert, params)
    # Neither another namespace nor a later generation belongs to this debt.
    for control in (
        {**params, "project": str(uuid4())},
        {**params, "org": str(uuid4())},
        {**params, "generation": attempt.generation + 1},
    ):
        writer.store.execute(insert, control)
    run_expiry_pass(writer)
    debt.refresh_from_db()
    assert debt.status != "done"
    assert not MetricAttemptLedgerChunk.objects.filter(attempt=attempt).exists()
    # A done row without its synchronous response is NOT a drainage receipt.
    MetricErasureRequest.objects.filter(pk=submission.pk).update(
        status="done", completed_at=now
    )
    writer.store.execute(insert, params)
    run_expiry_pass(writer)
    debt.refresh_from_db()
    assert debt.status != "done"
    MetricErasureRequest.objects.filter(pk=submission.pk).update(
        receipts={"synchronous_response": {"drained": 1}}
    )
    writer.store.execute(insert, params)
    run_expiry_pass(writer)
    debt.refresh_from_db()
    assert debt.status == "done"
    state = MetricProjectRefresh.for_project(writer.lease.project_id)
    assert state.head_manifest_id is None  # the no-head admission guard still applies
    assert state.pending_repair and not state.pending_expiry
    assert writer.store.execute(
        "SELECT count() FROM metric_field_values WHERE unit_id = 'late-private' "
        "AND org_id = %(org)s AND project_id = %(project)s AND generation <= %(generation)s",
        params,
    ) == [(0,)]
    assert writer.store.execute(
        "SELECT count() FROM metric_field_values WHERE unit_id = 'late-private'"
    ) == [(3,)]


def test_pinned_checkpoint_never_borrows_a_newer_heads_finality(live_writer):
    from tracer.services.derived_metrics import reads
    from tracer.tests.test_derived_metric_publication_clickhouse import prepare_field

    writer = live_writer
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    old_cut = now - timedelta(hours=2)
    definition = numeric_definition(uuid4())
    attempt, _ = prepare_field(writer, metric=definition.metric_id)
    # prepare_field seals with its generation as a synthetic cursor. Publish a
    # real source cut before the checkpoint whose own cursor updates are empty.
    writer.stage_and_publish(attempt)
    advance = writer.begin()
    writer.seal(advance, cursors={"watermark_ns": int(old_cut.timestamp()) * 10**9})
    writer.stage_and_publish(advance)
    pinned = writer.compact()
    advance = writer.begin()
    writer.seal(advance, cursors={"watermark_ns": int(now.timestamp()) * 10**9})
    writer.stage_and_publish(advance)
    assert reads.cut_terms(pinned, definition)[0] == old_cut


def test_open_bucket_nomination_does_not_revive_closed_moved_or_deleted_rows(
    live_writer,
):
    from types import SimpleNamespace

    from tracer.services.derived_metrics import reads
    from tracer.services.derived_metrics.storage import FIELD_VALUES
    from tracer.tests.test_derived_metric_publication_clickhouse import field_query

    writer = live_writer
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    before = now - timedelta(hours=1)
    definition = numeric_definition(uuid4())
    metric = SimpleNamespace(
        organization_id=writer.org_id, project_id=writer.lease.project_id
    )

    def publish(start, *, opened, deleted=False):
        attempt = writer.begin()
        sql, params = field_query(
            "moving-unit", metric=definition.metric_id, start=start, deleted=deleted
        )
        if opened:
            sql = sql.replace("'ok' AS status", "'open' AS status")
        writer.freeze(attempt, FIELD_VALUES, sql, params)
        writer.seal(attempt)
        return writer.stage_and_publish(attempt)

    head = publish(before, opened=True)
    assert reads.open_buckets(writer.store, metric, definition, head, before, now) == {
        before: 1
    }
    head = publish(before, opened=False)
    assert reads.open_buckets(writer.store, metric, definition, head, before, now) == {}
    head = publish(now, opened=True)
    assert reads.open_buckets(writer.store, metric, definition, head, before, now) == {}
    assert reads.open_buckets(
        writer.store, metric, definition, head, now, now + timedelta(hours=1)
    ) == {now: 1}
    head = publish(now, opened=True, deleted=True)
    assert (
        reads.open_buckets(
            writer.store, metric, definition, head, now, now + timedelta(hours=1)
        )
        == {}
    )


def test_finality_overflow_is_unavailable_not_a_truncated_proof(live_writer):
    from types import SimpleNamespace

    from tracer.services.derived_metrics import reads
    from tracer.services.derived_metrics.service import MetricUnavailableError
    from tracer.services.derived_metrics.storage import FIELD_VALUES

    writer = live_writer
    end = timezone.now().replace(minute=0, second=0, microsecond=0)
    count = reads.MAX_OPEN_BUCKETS + 1
    start = end - timedelta(minutes=15 * count)
    definition = numeric_definition(uuid4())
    attempt = writer.begin()
    sql = (
        default_row_select(
            FIELD_VALUES,
            {
                "field_id": "toUUID(%(metric)s)",
                "field_version": "toUInt32(1)",
                "level": "'conversation'",
                "unit_id": "concat('open-', toString(number))",
                "bucket_start": "toDateTime(%(start)s, 'UTC') + toIntervalMinute(15 * number)",
                "unit_start": "toDateTime64(%(start)s, 6, 'UTC') + toIntervalMinute(15 * number)",
                "last_arrival_ns": "toUInt64(%(arrival)s)",
                "status": "'open'",
            },
        )
        + " FROM numbers(%(count)s)"
    )
    writer.freeze(
        attempt,
        FIELD_VALUES,
        sql,
        {
            "metric": str(definition.metric_id),
            "start": start,
            "count": count,
            "arrival": int(timezone.now().timestamp()) * 10**9,
        },
    )
    writer.seal(attempt)
    head = writer.stage_and_publish(attempt)
    metric = SimpleNamespace(
        organization_id=writer.org_id, project_id=writer.lease.project_id
    )
    with pytest.raises(MetricUnavailableError, match="Too many unsettled buckets"):
        reads.open_buckets(writer.store, metric, definition, head, start, end)
