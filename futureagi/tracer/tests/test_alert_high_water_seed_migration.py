"""An occurrences alert that fired before the high-water mark existed fires once.

Alert state rows written before ``occurrences_high_water`` existed hold its
default of 0, and an eval recount can lower an issue's count below the
threshold. The seed migration copies the count of the last queued alert into
the mark, so a count that falls and crosses the same threshold again queues
nothing.

Rows are shaped as the release without the mark writes them: the state row
points at the event whose delivery it queued, that event's ``before`` and
``after`` are ``issue_snapshot`` dicts, and the mark holds its default. Each
test rewinds tracer to the migration before the seed, writes those rows, then
migrates forward to every leaf of the migration graph.
"""

from datetime import UTC, datetime

import pytest
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from tracer.models.feed_alert import (
    ErrorFeedAlertDelivery,
    ErrorFeedAlertIssueState,
    ErrorFeedAlertRule,
    ErrorFeedIssueEvent,
    FeedAlertDeliveryStatus,
    FeedAlertTrigger,
)
from tracer.models.observation_span import EvalTargetType
from tracer.models.trace_error_analysis import ClusterSource, TraceErrorGroup
from tracer.services.feed_alerts import events

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE_SEED = ("tracer", "0131_merge_c26_dev_1109")
THRESHOLD = 100
RULE_CREATED_AT = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
ALERTED_AT = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
# Two hours after the alert: well past the rule's five-minute cooldown.
RECROSSED_AT = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def _rewind_to_before_seed():
    executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
    leaves = executor.loader.graph.leaf_nodes()
    executor.migrate([BEFORE_SEED])
    return leaves


def _migrate_to(targets):
    MigrationExecutor(connections[DEFAULT_DB_ALIAS]).migrate(targets)


@pytest.fixture
def leaves(transactional_db):
    """Tracer rewound to just before the seed, restored to every leaf afterwards."""
    targets = _rewind_to_before_seed()
    yield targets
    _migrate_to(targets)


@pytest.fixture
def slack_connection(organization, workspace, user):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.SLACK,
        display_name="Engineering Slack",
        host_url="https://slack.com",
        encrypted_credentials=b"test-credentials",
        external_project_name="T-ALERTS",
        status=ConnectionStatus.ACTIVE,
        backfill_completed=True,
    )


@pytest.fixture
def make_rule(organization, workspace, user, observe_project, slack_connection):
    def make(
        trigger_type=FeedAlertTrigger.OCCURRENCES_CROSSED, trigger_value=THRESHOLD
    ):
        rule = ErrorFeedAlertRule.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            created_by=user,
            project=observe_project,
            name="Eval failures",
            trigger_type=trigger_type,
            trigger_value=trigger_value,
            slack_connection=slack_connection,
            slack_channel_id="C0123456789",
            slack_channel_name="production-alerts",
            cooldown_seconds=300,
        )
        # A rule only matches events created after it.
        ErrorFeedAlertRule.no_workspace_objects.filter(pk=rule.pk).update(
            created_at=RULE_CREATED_AT
        )
        rule.refresh_from_db()
        return rule

    return make


@pytest.fixture
def make_issue(observe_project):
    def make(cluster_id):
        # The recount has already lowered the count to one below the threshold.
        count = THRESHOLD - 1
        return TraceErrorGroup.objects.create(
            project=observe_project,
            cluster_id=cluster_id,
            error_type="Answer relevance",
            source=ClusterSource.EVAL,
            eval_target_type=EvalTargetType.TRACE,
            title="Answers miss the question",
            error_count=count,
            total_events=count,
            unique_traces=count,
            severity_assessment_status="assessed",
            severity_source="model",
        )

    return make


def _snapshot(issue, occurrences):
    return {**events.issue_snapshot(issue), "occurrences": occurrences}


def _event(issue, source_key, *, before, after, created_at):
    event = ErrorFeedIssueEvent.no_workspace_objects.create(
        organization_id=issue.project.organization_id,
        workspace_id=issue.project.workspace_id,
        project=issue.project,
        cluster=issue,
        event_kind="issue_changed",
        source_key=source_key,
        before=before,
        after=after,
    )
    ErrorFeedIssueEvent.no_workspace_objects.filter(pk=event.pk).update(
        created_at=created_at
    )
    event.refresh_from_db()
    return event


def _alerted_state(rule, issue, after, *, high_water=0):
    """The rows an alert queued at ``after`` leaves behind: event, delivery, state."""
    event = _event(
        issue,
        f"alerted:{rule.pk}:{issue.pk}",
        before=_snapshot(issue, THRESHOLD - 1),
        after=after,
        created_at=ALERTED_AT,
    )
    ErrorFeedIssueEvent.no_workspace_objects.filter(pk=event.pk).update(
        processed_at=ALERTED_AT
    )
    ErrorFeedAlertDelivery.no_workspace_objects.create(
        rule=rule,
        event=event,
        cluster=issue,
        channel_id=rule.slack_channel_id,
        status=FeedAlertDeliveryStatus.SENT,
        attempts=1,
        sent_at=ALERTED_AT,
    )
    return ErrorFeedAlertIssueState.no_workspace_objects.create(
        rule=rule,
        cluster=issue,
        last_event=event,
        last_queued_at=ALERTED_AT,
        occurrences_high_water=high_water,
    )


def _marks():
    states = ErrorFeedAlertIssueState.no_workspace_objects.all()
    return dict(states.values_list("pk", "occurrences_high_water"))


def test_an_alert_queued_before_the_mark_existed_does_not_fire_again_at_its_threshold(
    leaves, make_rule, make_issue
):
    rule = make_rule()
    issue = make_issue("E-RECOUNT")
    state = _alerted_state(rule, issue, _snapshot(issue, THRESHOLD))
    alerted_event_id = state.last_event_id

    _migrate_to(leaves)
    recrossed = _event(
        issue,
        "recount-recrossed",
        before=_snapshot(issue, THRESHOLD - 1),
        after=_snapshot(issue, THRESHOLD),
        created_at=RECROSSED_AT,
    )
    events._queue_event(recrossed)

    queued = ErrorFeedAlertDelivery.no_workspace_objects.filter(event=recrossed)
    assert not queued.exists()
    state.refresh_from_db()
    assert state.occurrences_high_water == THRESHOLD
    assert state.last_event_id == alerted_event_id
    recrossed.refresh_from_db()
    assert recrossed.processed_at is not None


def test_a_row_without_a_usable_alert_count_stays_at_zero(
    leaves, make_rule, make_issue
):
    rule = make_rule()
    seeded_issue = make_issue("E-SEEDED")
    seeded = _alerted_state(rule, seeded_issue, _snapshot(seeded_issue, THRESHOLD))

    unusable = []
    missing_issue = make_issue("E-NO-COUNT")
    without_count = _snapshot(missing_issue, THRESHOLD)
    del without_count["occurrences"]
    unusable.append(_alerted_state(rule, missing_issue, without_count))
    for cluster_id, count in (
        ("E-NULL", None),
        ("E-TEXT", "100"),
        ("E-FRACTION", 100.5),
        ("E-FLAG", True),
        ("E-NEGATIVE", -3),
    ):
        issue = make_issue(cluster_id)
        unusable.append(_alerted_state(rule, issue, _snapshot(issue, count)))
    # The event that queued this row's alert was deleted, so last_event is NULL.
    unusable.append(
        ErrorFeedAlertIssueState.no_workspace_objects.create(
            rule=rule,
            cluster=make_issue("E-NO-EVENT"),
            last_event=None,
            last_queued_at=ALERTED_AT,
        )
    )

    _migrate_to(leaves)

    marks = _marks()
    assert marks[seeded.pk] == THRESHOLD
    assert [marks[state.pk] for state in unusable] == [0] * len(unusable)


def test_a_mark_already_above_zero_is_left_as_it_is(leaves, make_rule, make_issue):
    rule = make_rule()
    seeded_issue = make_issue("E-SEEDED")
    seeded = _alerted_state(rule, seeded_issue, _snapshot(seeded_issue, THRESHOLD))
    higher_issue = make_issue("E-HIGHER")
    higher = _alerted_state(
        rule, higher_issue, _snapshot(higher_issue, THRESHOLD), high_water=150
    )
    lower_issue = make_issue("E-LOWER")
    lower = _alerted_state(
        rule, lower_issue, _snapshot(lower_issue, THRESHOLD), high_water=40
    )

    _migrate_to(leaves)

    marks = _marks()
    assert (marks[seeded.pk], marks[higher.pk], marks[lower.pk]) == (THRESHOLD, 150, 40)


def test_a_row_for_another_trigger_type_stays_at_zero(leaves, make_rule, make_issue):
    occurrences_rule = make_rule()
    severity_rule = make_rule(
        trigger_type=FeedAlertTrigger.SEVERITY_REACHED, trigger_value="high"
    )
    issue = make_issue("E-TWO-RULES")
    occurrences_state = _alerted_state(
        occurrences_rule, issue, _snapshot(issue, THRESHOLD)
    )
    severity_state = _alerted_state(severity_rule, issue, _snapshot(issue, THRESHOLD))

    _migrate_to(leaves)

    marks = _marks()
    assert (marks[occurrences_state.pk], marks[severity_state.pk]) == (THRESHOLD, 0)


def test_running_the_seed_twice_gives_the_same_marks(leaves, make_rule, make_issue):
    rule = make_rule()
    seeded_issue = make_issue("E-SEEDED")
    seeded = _alerted_state(rule, seeded_issue, _snapshot(seeded_issue, THRESHOLD))
    higher_issue = make_issue("E-HIGHER")
    _alerted_state(
        rule, higher_issue, _snapshot(higher_issue, THRESHOLD), high_water=150
    )
    text_issue = make_issue("E-TEXT")
    _alerted_state(rule, text_issue, _snapshot(text_issue, "100"))

    _migrate_to(leaves)
    first = _marks()
    _rewind_to_before_seed()
    _migrate_to(leaves)

    assert first[seeded.pk] == THRESHOLD
    assert _marks() == first
