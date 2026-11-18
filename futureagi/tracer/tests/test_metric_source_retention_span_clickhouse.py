# ruff: noqa: F811 -- shared isolated native fixtures
"""Cross-version span expiry, starting only with source revisions and empty stores."""

from datetime import timedelta
from itertools import count
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import DerivedMetric, MetricProjectRefresh
from tracer.services.derived_metrics.changes import Dependency
from tracer.services.derived_metrics.deletion_protocol import capture_pin
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.retention import expire_project
from tracer.services.derived_metrics.storage import FIELD_VALUES, SOURCE_DEPENDENCIES
from tracer.tests.test_derived_metric_backfill import make_version
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    change_for,
    metric_bucket_rows,
    seed_span_revision,
)

pytestmark = pytest.mark.django_db(transaction=True)


class SpanHistory:
    def __init__(self, writer, project):
        self.writer = writer
        self.scope = Scope(writer.org_id, writer.workspace_id, project.pk)
        self.trace, self.session = uuid4(), uuid4()
        self.now = timezone.now().replace(minute=0, second=0, microsecond=0)
        self.old = self.now - timedelta(days=3)
        self.quarter = self.now - timedelta(hours=2)
        self.cursors = count(1)

    def revision(self, name, at, value, revision):
        seed_span_revision(
            self.writer,
            self.scope,
            self.trace,
            name,
            self.session,
            at,
            value,
            revision=revision,
            arrival=self.now - timedelta(minutes=10 - revision),
        )
        owner = str(self.session)
        return Dependency(str(self.trace), name, owner, owner, "", "", at)

    def refresh(self, version, *dependencies, prior=(), purpose="incremental"):
        return self.writer.refresh(
            change_for(
                self.session,
                dependencies=dependencies,
                prior=prior,
                cursor=next(self.cursors),
            ),
            [version],
            self.scope,
            start=self.old - timedelta(hours=1),
            end=self.now,
            as_of=self.now,
            purpose=purpose,
        )

    def expire(self, versions):
        self.writer.retention_days = lambda: 1
        outcome = expire_project(
            self.writer,
            versions,
            retention_days=self.writer.retention_days,
            now=self.now,
        )
        head = MetricProjectRefresh.for_project(self.scope.project).head_manifest
        assert outcome["purged"] and outcome["manifest"] == str(head.pk)
        return head

    def source_values(self):
        # Independent oracle: choose the winning physical revision BEFORE the
        # retention/quarter predicates. An old revision or tombstone is not live.
        return self.writer.store.execute(
            "SELECT id, winner.2 FROM (SELECT id, "
            "argMax(tuple(start_time, attrs_number['resolved'], is_deleted), _version) AS winner "
            "FROM spans WHERE project_id = toUUID(%(project_id)s) AND trace_id = %(trace)s "
            "GROUP BY id) WHERE winner.3 = 0 AND winner.1 >= %(cutoff)s "
            "AND toStartOfFifteenMinutes(winner.1) = %(quarter)s ORDER BY id",
            {
                **self.scope.parameters(),
                "trace": str(self.trace),
                "quarter": self.quarter,
                "cutoff": self.now - timedelta(days=1),
            },
        )

    def field_values(self, head, version, quarter):
        sql, params = committed_rows_select(
            FIELD_VALUES,
            head,
            org_id=self.scope.organization,
            project_id=self.scope.project,
            key_in={
                "field_id": (version.metric_id,),
                "field_version": (version.version,),
                "level": ("span",),
            },
        )
        return self.writer.store.execute(
            f"SELECT JSONExtractString(unit_id, 2) AS span, value_number FROM ({sql}) "
            "WHERE is_deleted = 0 AND status != 'open' AND bucket_start = %(quarter)s ORDER BY span",
            {**params, "quarter": quarter},
        )

    def totals(self, head, version):
        return [
            r[2:]
            for r in metric_bucket_rows(self.writer, head, version)
            if r[0] == self.quarter
        ]


def expected_totals(values):
    return [(len(values), sum(value for _, value in values), len(values))]


@pytest.fixture
def span_versions(live_writer, workspace, observe_project):
    case = SpanHistory(live_writer, observe_project)
    recipe = {
        "active": True,
        "level": "span",
        "selection_unit": "span",
        "formula": "mean(resolved)",
        "inputs": [
            {
                "alias": "resolved",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
    }
    first = make_version(observe_project, workspace, **recipe)
    old = case.revision("victim", case.old, 13, 1)
    head = case.refresh(first, old)
    assert case.field_values(head, first, case.old) == [("victim", 13)]
    # A reader pins v1 before the recipe switch; omission must not discard it
    # just because the next refresh computes only the new active version.
    capture_pin(
        observe_project.pk,
        metric_id=first.metric_id,
        consumer_kind="request",
        ttl=timedelta(hours=1),
    )
    second = make_version(observe_project, workspace, metric=first.metric, **recipe)
    return case, first, second, old


@pytest.mark.parametrize("pruned", [False, True], ids=["live-source", "no-source"])
def test_corrected_span_follows_source_at_expiry_and_on_refresh(span_versions, pruned):
    case, first, second, old = span_versions
    corrected = case.revision("victim", case.quarter, 11, 2)
    keep = case.revision("keep", case.quarter, 7, 1)
    base = case.refresh(second, corrected, keep, prior=[old])
    assert case.field_values(base, first, case.old) == [("victim", 13)]
    assert case.field_values(base, second, case.quarter) == [
        ("keep", 7),
        ("victim", 11),
    ]
    assert case.totals(base, second) == [(2, 18, 2)]
    if pruned:
        victim_rows = "project_id = toUUID(%(project_id)s) AND trace_id = %(trace)s AND id = 'victim'"
        bound = {**case.scope.parameters(), "trace": str(case.trace)}
        case.writer.store.execute(
            f"ALTER TABLE spans DELETE WHERE {victim_rows} SETTINGS mutations_sync=2",
            bound,
        )
        assert case.writer.store.execute(
            f"SELECT count() FROM spans WHERE {victim_rows}", bound
        ) == [(0,)]
    truth = case.source_values()
    assert truth == ([("keep", 7)] if pruned else [("keep", 7), ("victim", 11)])
    checkpoint = case.expire([first, second])
    assert checkpoint.published_by.source_ceilings["retention_retired_live"]
    assert case.source_values() == truth
    sql, params = committed_rows_select(
        SOURCE_DEPENDENCIES,
        checkpoint,
        org_id=case.scope.organization,
        project_id=case.scope.project,
    )
    assert case.writer.store.execute(
        f"SELECT unit_start FROM ({sql}) WHERE trace_id = %(trace)s AND span_id = 'victim' AND is_deleted = 0",
        {**params, "trace": str(case.trace)},
    ) == [(case.quarter,)]
    assert case.field_values(checkpoint, first, case.old) == []
    at_expiry = (
        case.field_values(checkpoint, second, case.quarter),
        case.totals(checkpoint, second),
    )

    # Dirty the quarter through its *other* member. No new victim dependency
    # or revision is supplied. Exercise replay before asserting the intermediate
    # state, so a correct later refresh cannot mask an incorrect checkpoint.
    later = case.refresh(second, keep)
    expected = truth, expected_totals(truth)
    assert (
        case.field_values(later, second, case.quarter),
        case.totals(later, second),
    ) == expected
    assert at_expiry == expected


def uncovered_span_head(span_versions):
    case, first, second, old = span_versions
    victim = case.revision("victim", case.quarter, 11, 2)
    keep = case.revision("keep", case.quarter, 7, 1)
    mobile = case.revision("mobile", case.quarter, 5, 1)
    base = case.refresh(second, victim, keep, mobile, prior=[old])
    assert case.totals(base, second) == [(3, 23, 3)]

    # A bounded source page nominates only 'trigger'. The whole destination
    # quarter is captured, so mobile moves as a quarter-mate. Its old quarter
    # was not nominated by this page. No legacy/fabricated checkpoint is needed.
    elsewhere = case.quarter + timedelta(hours=1)
    case.revision("mobile", elsewhere, 5, 2)
    trigger = case.revision("trigger", elsewhere, 19, 1)
    partial = case.refresh(second, trigger, purpose="backfill")
    assert case.field_values(partial, second, elsewhere) == [
        ("mobile", 5),
        ("trigger", 19),
    ]
    assert case.field_values(partial, second, case.quarter) == [
        ("keep", 7),
        ("victim", 11),
    ]
    assert case.totals(partial, second) == [(3, 23, 3)]

    # Reactivate the still-pinned, completed v1. It processes *both* terminal
    # corrections. The victim genuinely expires (unlike the live-source case
    # above); mobile is back in the original quarter and has no expired anchor.
    DerivedMetric.activate_version(
        first.metric_id, first.pk, expected_active_id=second.pk
    )
    expired = case.revision("victim", case.old, 13, 3)
    returned = case.revision("mobile", case.quarter, 5, 3)
    head = case.refresh(first, expired, returned, prior=[victim, mobile])
    truth = case.source_values()
    assert truth == [("keep", 7), ("mobile", 5)]
    assert case.field_values(head, first, case.old) == [("victim", 13)]
    assert case.field_values(head, first, case.quarter) == truth
    assert case.field_values(head, second, case.quarter) == [
        ("keep", 7),
        ("victim", 11),
    ]
    assert case.totals(head, second) == [(3, 23, 3)]

    return case, first, second, head, truth, elsewhere


def test_cross_version_expiry_preserves_uncovered_span_survivor(span_versions):
    case, first, second, _, truth, elsewhere = uncovered_span_head(span_versions)
    checkpoint = case.expire([first, second])
    assert case.totals(checkpoint, first) == expected_totals(truth)
    assert case.totals(checkpoint, second) == expected_totals(truth)
    assert case.field_values(checkpoint, second, case.quarter) == truth
    assert case.field_values(checkpoint, second, elsewhere) == [("trigger", 19)]
    assert [
        row[2:]
        for row in metric_bucket_rows(case.writer, checkpoint, second)
        if row[0] == elsewhere
    ] == [(1, 19, 1)]


def test_unproven_arrival_cannot_fill_cross_version_span_gap(span_versions):
    from tracer.models.derived_metric import MetricRefreshAttempt
    from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven

    case, first, second, head, truth, elsewhere = uncovered_span_head(span_versions)
    case.revision("mobile", elsewhere, 5, 4)
    case.revision("unknown", case.quarter, 42, 1)
    # Two source rows would satisfy a mere count check, but one has never
    # belonged to this version and cannot establish the missing identity.
    assert case.source_values() == [("keep", 7), ("unknown", 42)]
    with pytest.raises(MetricAdmissionUnproven, match="requires backfill"):
        case.expire([first, second])
    state = MetricProjectRefresh.for_project(case.scope.project)
    assert state.head_manifest_id == head.pk and state.head_manifest.revoked_at is None
    assert not MetricRefreshAttempt.objects.filter(
        project_id=state.project_id, state__in=MetricRefreshAttempt.OPEN_STATES
    ).exists()

    case.revision("mobile", case.quarter, 5, 5)
    seed_span_revision(
        case.writer,
        case.scope,
        case.trace,
        "unknown",
        case.session,
        case.quarter,
        42,
        revision=2,
        deleted=1,
        arrival=case.now,
    )
    assert case.source_values() == truth
    retry = case.expire([first, second])
    assert case.field_values(retry, second, case.quarter) == truth
    assert case.totals(retry, second) == expected_totals(truth)
