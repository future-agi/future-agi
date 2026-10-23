"""TH-8696 hardening: monitor delivery retry, weighted-input validation, evaluator
listing, time-ordered pagination and bounded previews.

Most tests drive the real HTTP API, PostgreSQL and (via ``metric_ch``) a real
ClickHouse. The scheduler edge cases build committed run rows directly and patch
only ``read_results`` so a stored overall value can be chosen per run.
"""

import time
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from django.core.cache import cache
from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricMonitorDelivery,
    DerivedMetricRun,
    DerivedMetricVersion,
)
from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.services.derived_metrics import integrations, service
from tracer.services.derived_metrics.errors import MetricBusyError
from tracer.services.derived_metrics.integrations import (
    DELIVERY_RETRY_LOOKBACK,
    process_derived_monitor,
)
from tracer.services.derived_metrics.queries import (
    PREVIEW_QUERY_SETTINGS,
    QUERY_SETTINGS,
    Scope,
)
from tracer.services.derived_metrics.service import execute_run, preview_admission
from tracer.tests import test_derived_metric_clickhouse as metric_fixtures

metric_ch = metric_fixtures.metric_ch
seed_span = metric_fixtures.seed_span
seed_eval = metric_fixtures.seed_eval

ROOT = "/tracer/derived-metrics/"
RUNS = "/tracer/derived-metric-runs/"
RATIO = "ratio(resolved == 1 and escalated == 0, resolved >= 0)"


def span_body(project, **overrides):
    return {
        "project_id": str(project.id),
        "name": "Resolution ratio",
        "description": "Hardening test metric",
        "formula": RATIO,
        "selection_unit": "session",
        "weighting": "none",
        "cohort_key": "",
        "filters": [],
        "inputs": [
            {
                "alias": key,
                "source": "span",
                "field": "attribute_number",
                "attribute_key": key,
                "reducer": "latest",
            }
            for key in ["resolved", "escalated"]
        ],
        **overrides,
    }


def eval_inputs(configs, hashes=("a" * 64, "a" * 64)):
    return [
        {
            "alias": alias,
            "source": "eval",
            "config_id": str(config.id),
            "config_hash": config_hash,
            "reducer": "latest",
        }
        for alias, config, config_hash in zip(
            ["resolved", "escalated"], configs, hashes, strict=True
        )
    ]


def window():
    now = timezone.now().replace(microsecond=0)
    return {
        "window_start": (now - timedelta(days=1)).isoformat(),
        "window_end": now.isoformat(),
        "filters": [],
        "idempotency_key": str(uuid4()),
    }


@pytest.fixture(autouse=True)
def clean_admission_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def saved(auth_client, observe_project):
    response = auth_client.post(ROOT, span_body(observe_project), format="json")
    assert response.status_code == 201, response.data
    return response.data


@pytest.fixture
def scope(workspace, observe_project):
    return Scope(workspace.organization_id, workspace.id, observe_project.id)


def make_monitor(workspace, project, metric_id, **overrides):
    return UserAlertMonitor.objects.create(
        **{
            "name": "Hardening monitor",
            "organization": workspace.organization,
            "workspace": workspace,
            "project": project,
            "metric_type": "derived_metric",
            "metric": str(metric_id),
            "threshold_operator": "less_than",
            "threshold_type": "static",
            "critical_threshold_value": 0.75,
            "notification_emails": [],
            "alert_frequency": 60,
            **overrides,
        }
    )


def make_run(metric_id, user, *, window_end, completed_at="now", committed=True):
    """A committed run captured at its window end. By default it commits now, i.e.
    after any monitor the test already created, so its completion-time delivery
    was owed to that monitor."""
    version = DerivedMetric.objects.get(pk=metric_id).current_version
    return DerivedMetricRun.objects.create(
        definition=DerivedMetricVersion.objects.get(
            metric_id=metric_id, version=version
        ),
        requested_by=user,
        window_start=window_end - timedelta(minutes=5),
        window_end=window_end,
        as_of=window_end,
        completed_at=timezone.now() if completed_at == "now" else completed_at,
        fingerprint=uuid4().hex,
        snapshot_id=uuid4(),
        result_id=uuid4() if committed else None,
        status="complete",
    )


def delivered(monitor):
    return list(
        DerivedMetricMonitorDelivery.objects.filter(monitor=monitor)
        .order_by("delivered_at", "id")
        .values_list("run_id", flat=True)
    )


def stored_values(values):
    """A read_results double returning the chosen overall value for each run."""

    def read(run, *args, **kwargs):
        value = values[run.id]
        return {"results": [] if value == "no-row" else [{"value": value}]}

    return read


def field_error(response, field):
    """The messages for one field from the public API error envelope.

    Errors use ``tfc.utils.api_errors.build_error_envelope``: field messages are
    under ``details`` and the field is named by ``attr``, never top-level keys.
    """
    assert response.data["status"] is False, response.data
    assert response.data["attr"] == field, response.data
    assert list(response.data["details"]) == [field], response.data
    return " ".join(response.data["details"][field])


def seed_two_sessions(client, scope, when):
    for index in range(2):
        seed_span(
            client,
            scope,
            f"s-{index}",
            uuid4(),
            uuid4(),
            when,
            escalated=index,
        )


# --- MET-2: a failed delivery is retried by the scheduler -------------------------


@pytest.mark.django_db
def test_failed_delivery_is_retried_after_the_alert_window_and_stays_deduplicated(
    auth_client, observe_project, workspace, saved, scope, metric_ch
):
    seed_two_sessions(metric_ch, scope, timezone.now() - timedelta(hours=1))
    monitor = make_monitor(workspace, observe_project, saved["id"])
    with (
        patch("tracer.views.derived_metric._enqueue"),
        patch(
            "tracer.services.derived_metrics.service.get_clickhouse_client",
            return_value=metric_ch,
        ),
    ):
        run_id = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", window(), format="json"
        ).data["id"]
        # The transport fails while the run completes: the run still commits and
        # execute_run only logs the notification failure.
        with patch(
            "tracer.utils.monitor._send_slack_notification",
            side_effect=RuntimeError("delivery unavailable"),
        ):
            execute_run(run_id)
        run = DerivedMetricRun.objects.get(pk=run_id)
        assert run.status == "complete" and run.result_id is not None
        assert not DerivedMetricMonitorDelivery.objects.filter(monitor=monitor).exists()
        assert not UserAlertMonitorLog.objects.filter(alert=monitor).exists()

        # Hours later the run's window is far outside alert_frequency (60 min).
        later = timezone.now() + timedelta(hours=5)
        with (
            patch(
                "tracer.utils.monitor._send_slack_notification",
                side_effect=RuntimeError("delivery unavailable"),
            ),
            pytest.raises(RuntimeError, match="delivery unavailable"),
        ):
            process_derived_monitor(monitor, later)
        # Still unconsumed: the failed tick rolled back its log and ledger row.
        assert not DerivedMetricMonitorDelivery.objects.filter(monitor=monitor).exists()
        assert not UserAlertMonitorLog.objects.filter(alert=monitor).exists()

        process_derived_monitor(monitor, later)
        process_derived_monitor(monitor, later + timedelta(hours=1))
    delivery = DerivedMetricMonitorDelivery.objects.get(monitor=monitor)
    assert delivery.run_id == run.id and delivery.alert_type == "critical"
    assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == 1
    monitor.refresh_from_db()
    assert monitor.last_derived_run_id == run.id


@pytest.mark.django_db
def test_scheduler_does_not_reemit_an_earlier_run_after_a_later_one(
    auth_client, observe_project, workspace, saved, scope, metric_ch
):
    seed_two_sessions(metric_ch, scope, timezone.now() - timedelta(hours=1))
    monitor = make_monitor(workspace, observe_project, saved["id"])
    with (
        patch("tracer.views.derived_metric._enqueue"),
        patch(
            "tracer.services.derived_metrics.service.get_clickhouse_client",
            return_value=metric_ch,
        ),
    ):
        first = auth_client.post(f"{ROOT}{saved['id']}/runs/", window(), format="json")
        execute_run(first.data["id"])
        second = auth_client.post(f"{ROOT}{saved['id']}/runs/", window(), format="json")
        execute_run(second.data["id"])
        # A -> B -> recompute A: neither the replay nor any later scheduler tick
        # may emit A again.
        assert (
            auth_client.post(
                f"{RUNS}{first.data['id']}/recompute/", {}, format="json"
            ).status_code
            == 202
        )
        execute_run(first.data["id"])
        for hours in (2, 6, 30):
            process_derived_monitor(monitor, timezone.now() + timedelta(hours=hours))
    assert UserAlertMonitor.objects.get(pk=monitor.pk).last_derived_run_id == UUID(
        second.data["id"]
    )
    assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == 2
    assert DerivedMetricMonitorDelivery.objects.filter(monitor=monitor).count() == 2


@pytest.mark.django_db
def test_retry_is_bounded_by_the_commit_time_lookback(
    saved, user, workspace, observe_project
):
    monitor = make_monitor(workspace, observe_project, saved["id"])
    now = timezone.now()
    # The monitor existed long before the run committed, so the completion-time
    # delivery was owed to it.
    UserAlertMonitor.objects.filter(pk=monitor.pk).update(
        created_at=now - timedelta(days=10)
    )
    monitor.refresh_from_db()
    stale = make_run(
        saved["id"],
        user,
        window_end=now - timedelta(hours=100),
        completed_at=now - DELIVERY_RETRY_LOOKBACK - timedelta(hours=1),
    )
    values = {stale.id: 0.5}
    with patch.object(integrations, "read_results", side_effect=stored_values(values)):
        process_derived_monitor(monitor, now)
        assert delivered(monitor) == []

        # A recompute commits again, so a failed replay delivery is retryable.
        DerivedMetricRun.objects.filter(pk=stale.pk).update(
            completed_at=now - timedelta(hours=1)
        )
        process_derived_monitor(monitor, now)
    assert delivered(monitor) == [stale.id]


@pytest.mark.django_db
def test_a_run_committed_before_the_monitor_existed_is_not_a_failed_delivery(
    saved, user, workspace, observe_project
):
    now = timezone.now()
    earlier = make_run(
        saved["id"],
        user,
        window_end=now - timedelta(hours=3),
        completed_at=now - timedelta(hours=2),
    )
    monitor = make_monitor(workspace, observe_project, saved["id"])
    values = {earlier.id: 0.5}
    with patch.object(integrations, "read_results", side_effect=stored_values(values)):
        # The run committed before this monitor existed and its window ended long
        # before the alert window: nothing was owed, so nothing is replayed.
        process_derived_monitor(monitor, now)
        assert delivered(monitor) == []
        # The original rule is unchanged: a window that ended inside the alert
        # window is evaluated even if it committed before the monitor existed.
        recent = make_run(
            saved["id"],
            user,
            window_end=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=1),
        )
        values[recent.id] = 0.5
        process_derived_monitor(monitor, now)
    assert delivered(monitor) == [recent.id]


@pytest.mark.django_db
def test_a_missing_newest_value_never_falls_back_to_an_older_run(
    saved, user, workspace, observe_project
):
    monitor = make_monitor(workspace, observe_project, saved["id"])
    now = timezone.now()
    failed = make_run(saved["id"], user, window_end=now - timedelta(hours=6))
    missing = make_run(saved["id"], user, window_end=now - timedelta(hours=3))
    values = {failed.id: 0.5, missing.id: None}
    with patch.object(
        integrations, "read_results", side_effect=stored_values(values)
    ) as read:
        for _ in range(3):
            process_derived_monitor(monitor, now)
        # The newest observation has no value: no alert, no healthy zero and no
        # older number standing in for it (as in build_metric_value_query).
        assert delivered(monitor) == []
        assert [call.args[0].id for call in read.call_args_list] == [missing.id] * 3
        # Once the newest run has a value (e.g. after a recompute), it is consumed.
        values[missing.id] = 0.9
        process_derived_monitor(monitor, now)
        process_derived_monitor(monitor, now)
    assert delivered(monitor) == [missing.id]
    assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == 0


@pytest.mark.django_db
def test_each_tick_reads_at_most_the_newest_run(
    saved, user, workspace, observe_project
):
    monitor = make_monitor(workspace, observe_project, saved["id"])
    now = timezone.now()
    runs = [
        make_run(saved["id"], user, window_end=now - timedelta(hours=hours))
        for hours in range(30, 0, -1)
    ]
    values = {run.id: None for run in runs}
    with patch.object(
        integrations, "read_results", side_effect=stored_values(values)
    ) as read:
        process_derived_monitor(monitor, now)
    assert [call.args[0].id for call in read.call_args_list] == [runs[-1].id]
    assert delivered(monitor) == []


@pytest.mark.django_db
def test_an_older_undelivered_run_never_steps_the_monitor_back(
    saved, user, workspace, observe_project
):
    monitor = make_monitor(workspace, observe_project, saved["id"])
    now = timezone.now()
    older = make_run(saved["id"], user, window_end=now - timedelta(hours=6))
    newer = make_run(saved["id"], user, window_end=now - timedelta(hours=3))
    # The older run breached (0.5 < 0.75); the newer one is healthy.
    values = {older.id: 0.5, newer.id: 0.9}
    with patch.object(integrations, "read_results", side_effect=stored_values(values)):
        for hours in (0, 1, 5):
            process_derived_monitor(monitor, now + timedelta(hours=hours))
        assert delivered(monitor) == [newer.id]
        # The completion path follows the same rule: recomputing a run this
        # monitor never evaluated, after a newer one, emits nothing.
        assert integrations.notify_run_monitors(older, monitor_id=monitor.id) == 0
        assert integrations.notify_run_monitors(older) == 0
    assert delivered(monitor) == [newer.id]
    assert not UserAlertMonitorLog.objects.filter(alert=monitor).exists()
    monitor.refresh_from_db()
    assert monitor.last_derived_run_id == newer.id
    with patch.object(integrations, "read_results") as read:
        process_derived_monitor(monitor, now)
    read.assert_not_called()  # a consumed head costs no result read


@pytest.mark.django_db
def test_a_newer_run_is_still_delivered_after_an_older_one(
    saved, user, workspace, observe_project
):
    monitor = make_monitor(workspace, observe_project, saved["id"])
    now = timezone.now()
    older = make_run(saved["id"], user, window_end=now - timedelta(hours=6))
    values = {older.id: 0.5}
    with patch.object(integrations, "read_results", side_effect=stored_values(values)):
        process_derived_monitor(monitor, now)
        newer = make_run(saved["id"], user, window_end=now - timedelta(hours=3))
        values[newer.id] = 0.4
        process_derived_monitor(monitor, now)
        process_derived_monitor(monitor, now)
    assert delivered(monitor) == [older.id, newer.id]
    assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == 2
    monitor.refresh_from_db()
    assert monitor.last_derived_run_id == newer.id


@pytest.mark.django_db
def test_retry_ignores_other_versions_muted_monitors_and_uncommitted_runs(
    auth_client, saved, user, workspace, observe_project
):
    monitor = make_monitor(workspace, observe_project, saved["id"])
    now = timezone.now()
    old_version = make_run(saved["id"], user, window_end=now - timedelta(hours=6))
    edited = auth_client.put(
        f"{ROOT}{saved['id']}/",
        {
            **span_body(observe_project),
            "formula": "mean(resolved + escalated)",
            "expected_version": 1,
        },
        format="json",
    )
    assert edited.status_code == 200, edited.data
    uncommitted = make_run(
        saved["id"], user, window_end=now - timedelta(hours=5), committed=False
    )
    current = make_run(saved["id"], user, window_end=now - timedelta(hours=4))
    values = {old_version.id: 0.1, uncommitted.id: 0.1, current.id: 0.1}
    with patch.object(integrations, "read_results", side_effect=stored_values(values)):
        UserAlertMonitor.objects.filter(pk=monitor.pk).update(is_mute=True)
        process_derived_monitor(UserAlertMonitor.objects.get(pk=monitor.pk), now)
        assert not DerivedMetricMonitorDelivery.objects.exists()
        UserAlertMonitor.objects.filter(pk=monitor.pk).update(is_mute=False)
        process_derived_monitor(UserAlertMonitor.objects.get(pk=monitor.pk), now)
        process_derived_monitor(UserAlertMonitor.objects.get(pk=monitor.pk), now)
    assert list(
        DerivedMetricMonitorDelivery.objects.values_list("run_id", flat=True)
    ) == [current.id]


# --- MET-3: weighted metrics cannot be authored with data they can never read ----


@pytest.fixture
def evaluators(observe_project, custom_eval_config):
    custom_eval_config.project = observe_project
    custom_eval_config.save()
    second = CustomEvalConfig.objects.create(
        name="Escalation evaluator",
        project=observe_project,
        eval_template=custom_eval_config.eval_template,
        config={"threshold": 0.2},
    )
    return [custom_eval_config, second]


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["validate/", ""])
def test_weighted_metric_without_an_evaluator_hash_is_rejected_precisely(
    auth_client, observe_project, evaluators, path
):
    data = span_body(
        observe_project,
        weighting="inverse_probability",
        inputs=eval_inputs(evaluators, hashes=("a" * 64, "")),
    )
    response = auth_client.post(f"{ROOT}{path}", data, format="json")
    assert response.status_code == 400, response.data
    message = field_error(response, "inputs")
    assert "config_hash" in message and "escalated" in message
    assert "resolved" not in message  # only the unversioned input is named
    # A field omitted entirely defaults to blank and is rejected the same way.
    omitted = [dict(item) for item in data["inputs"]]
    omitted[1].pop("config_hash")
    assert (
        auth_client.post(
            f"{ROOT}{path}", {**data, "inputs": omitted}, format="json"
        ).status_code
        == 400
    )
    assert DerivedMetric.objects.count() == 0


@pytest.mark.django_db
def test_hashed_weighted_and_unweighted_legacy_inputs_remain_valid(
    auth_client, observe_project, evaluators
):
    weighted = span_body(
        observe_project,
        weighting="inverse_probability",
        inputs=eval_inputs(evaluators),
    )
    assert (
        auth_client.post(f"{ROOT}validate/", weighted, format="json").status_code == 200
    )
    # An empty hash is the deliberate legacy selector for UNWEIGHTED metrics.
    legacy = span_body(observe_project, inputs=eval_inputs(evaluators, ("", "")))
    assert (
        auth_client.post(f"{ROOT}validate/", legacy, format="json").status_code == 200
    )
    assert auth_client.post(ROOT, legacy, format="json").status_code == 201


@pytest.mark.django_db
def test_contradictory_exact_filters_can_only_select_nothing_and_are_rejected(
    auth_client, observe_project, saved
):
    contradictory = [
        {"field": "model", "value": "a"},
        {"field": "provider", "value": "x"},
        {"field": "model", "value": "b"},
    ]
    response = auth_client.post(
        f"{ROOT}validate/",
        span_body(observe_project, filters=contradictory),
        format="json",
    )
    assert response.status_code == 400
    assert "'model'" in field_error(response, "filters")
    repeated = [{"field": "model", "value": "a"}, {"field": "model", "value": "a"}]
    assert (
        auth_client.post(
            f"{ROOT}validate/",
            span_body(observe_project, filters=repeated),
            format="json",
        ).status_code
        == 200
    )
    # A run filter that contradicts the saved version's own filter is rejected for
    # both persisted runs and previews.
    pinned = auth_client.put(
        f"{ROOT}{saved['id']}/",
        {
            **span_body(observe_project),
            "filters": [{"field": "model", "value": "a"}],
            "expected_version": 1,
        },
        format="json",
    )
    assert pinned.status_code == 200, pinned.data
    conflicting = {**window(), "filters": [{"field": "model", "value": "b"}]}
    with patch("tracer.views.derived_metric._enqueue"):
        run = auth_client.post(f"{ROOT}{saved['id']}/runs/", conflicting, format="json")
        shown = auth_client.post(
            f"{ROOT}{saved['id']}/preview/", conflicting, format="json"
        )
        agreeing = auth_client.post(
            f"{ROOT}{saved['id']}/runs/",
            {**window(), "filters": [{"field": "model", "value": "a"}]},
            format="json",
        )
    assert run.status_code == shown.status_code == 400
    assert "'model'" in field_error(run, "filters")
    assert "'model'" in field_error(shown, "filters")
    assert agreeing.status_code == 202


@pytest.mark.django_db
def test_evaluator_listing_is_project_scoped_searchable_and_bounded(
    auth_client, observe_project, evaluators
):
    from tracer.services.eval_tasks.config_hash import resolved_config_hash

    response = auth_client.get(f"{ROOT}evaluators/", {"project_id": observe_project.id})
    assert response.status_code == 200, response.data
    assert response.data["truncated"] is False
    listed = {row["id"]: row for row in map(dict, response.data["results"])}
    assert set(listed) == {str(config.id) for config in evaluators}
    for config in evaluators:
        row = listed[str(config.id)]
        assert row["name"] == config.name
        assert row["config_hash"] == resolved_config_hash(config)
        assert len(row["config_hash"]) == 64
    assert len({row["config_hash"] for row in listed.values()}) == 2

    searched = auth_client.get(
        f"{ROOT}evaluators/", {"project_id": observe_project.id, "search": "escal"}
    )
    assert [row["name"] for row in searched.data["results"]] == ["Escalation evaluator"]
    limited = auth_client.get(
        f"{ROOT}evaluators/", {"project_id": observe_project.id, "limit": 1}
    )
    assert len(limited.data["results"]) == 1 and limited.data["truncated"] is True

    CustomEvalConfig.objects.filter(pk=evaluators[1].pk).update(deleted=True)
    remaining = auth_client.get(
        f"{ROOT}evaluators/", {"project_id": observe_project.id}
    )
    assert [row["id"] for row in remaining.data["results"]] == [str(evaluators[0].id)]

    # The hash tracks the definition: editing the evaluator changes what new
    # results are stored under, and the listing must follow it.
    before = remaining.data["results"][0]["config_hash"]
    CustomEvalConfig.objects.filter(pk=evaluators[0].pk).update(
        config={"threshold": 0.9}
    )
    after = auth_client.get(f"{ROOT}evaluators/", {"project_id": observe_project.id})
    assert after.data["results"][0]["config_hash"] != before


@pytest.mark.django_db
@pytest.mark.parametrize(
    "query", [{}, {"project_id": "invalid"}, {"limit": 0}, {"limit": 101}, {"x": "y"}]
)
def test_evaluator_listing_query_is_strict(auth_client, observe_project, query):
    if "limit" in query or "x" in query:
        query = {"project_id": observe_project.id, **query}
    assert auth_client.get(f"{ROOT}evaluators/", query).status_code == 400


@pytest.mark.django_db
def test_evaluator_listing_cannot_cross_workspace_or_write_permissions(
    auth_client, observe_project, evaluators, user, workspace
):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import Workspace, WorkspaceMembership

    foreign = Workspace.objects.create(
        name="Elsewhere", organization=user.organization, created_by=user
    )
    observe_project.workspace = foreign
    observe_project.save()
    assert (
        auth_client.get(
            f"{ROOT}evaluators/", {"project_id": observe_project.id}
        ).status_code
        == 404
    )
    observe_project.workspace = workspace
    observe_project.save()
    OrganizationMembership.no_workspace_objects.filter(user=user).update(level=1)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=1)
    # A viewer may read the picker list: it is part of reading a definition.
    assert (
        auth_client.get(
            f"{ROOT}evaluators/", {"project_id": observe_project.id}
        ).status_code
        == 200
    )


@pytest.mark.django_db
def test_picker_output_authors_a_weighted_metric_that_reads_real_sampled_results(
    auth_client, observe_project, evaluators, scope, metric_ch
):
    """The listed (id, config_hash) pair is exactly what a sampled run persists."""
    listing = auth_client.get(f"{ROOT}evaluators/", {"project_id": observe_project.id})
    by_id = {row["id"]: row["config_hash"] for row in listing.data["results"]}
    picked = [(str(config.id), by_id[str(config.id)]) for config in evaluators]
    data = span_body(
        observe_project,
        weighting="inverse_probability",
        inputs=[
            {
                "alias": alias,
                "source": "eval",
                "config_id": config_id,
                "config_hash": config_hash,
                "reducer": "latest",
            }
            for alias, (config_id, config_hash) in zip(
                ["resolved", "escalated"], picked, strict=True
            )
        ],
    )
    created = auth_client.post(ROOT, data, format="json")
    assert created.status_code == 201, created.data
    now = timezone.now().replace(microsecond=0)
    sessions = [uuid4(), uuid4()]
    for session, probability, escalated in [
        (sessions[0], 0.5, 0),
        (sessions[1], 0.25, 1),
    ]:
        seed_span(
            metric_ch,
            scope,
            f"sp-{session}",
            session,
            uuid4(),
            now - timedelta(hours=1),
        )
        for (config_id, config_hash), score in zip(picked, [1, escalated], strict=True):
            seed_eval(
                metric_ch,
                config_id,
                session,
                now - timedelta(minutes=10),
                score,
                probability,
                config_hash=config_hash,
            )
    with patch(
        "tracer.services.derived_metrics.service.get_clickhouse_client",
        return_value=metric_ch,
    ):
        shown = auth_client.post(
            f"{ROOT}{created.data['id']}/preview/", window(), format="json"
        )
    assert shown.status_code == 200, shown.data
    row = shown.data["results"][0]
    assert row["status"] == "ok"
    assert row["value"] == pytest.approx(1 / 3)
    assert row["valid_count"] == 2 and row["missing_count"] == 0


# --- MET-4: lists are time ordered with a stable cursor ---------------------------


def page_through(client, url, params, interleave=None):
    """Follow next_cursor; ``interleave(page_number)`` may create rows between pages."""
    seen, cursor, pages = [], None, 0
    while True:
        query = {**params, **({"before": cursor} if cursor else {})}
        response = client.get(url, query)
        assert response.status_code == 200, response.data
        seen.extend(row["id"] for row in response.data["results"])
        pages += 1
        cursor = response.data["next_cursor"]
        if interleave:
            interleave(pages)
        if cursor is None:
            return seen, pages


@pytest.mark.django_db
def test_metric_pages_are_chronological_and_stable_while_rows_are_created(
    auth_client, observe_project
):
    created = []
    for index in range(5):
        response = auth_client.post(
            ROOT,
            span_body(observe_project, name=f"Metric {index}"),
            format="json",
        )
        assert response.status_code == 201, response.data
        created.append(response.data["id"])
    # Make chronological order the exact reverse of random-UUID order, so the old
    # ordering by id can never coincide with the correct one.
    by_id = sorted(created)
    base = timezone.now() - timedelta(days=1)
    for position, metric_id in enumerate(reversed(by_id)):
        DerivedMetric.objects.filter(pk=metric_id).update(
            created_at=base + timedelta(minutes=position)
        )
    expected = list(by_id)  # newest first

    newest = []

    def interleave(page_number):
        if page_number == 1:  # a row created between page fetches
            response = auth_client.post(
                ROOT, span_body(observe_project, name="Late metric"), format="json"
            )
            newest.append(response.data["id"])

    seen, pages = page_through(auth_client, ROOT, {"limit": 2}, interleave)
    assert seen == expected and pages == 3
    fresh = auth_client.get(ROOT, {"limit": 2}).data["results"]
    assert fresh[0]["id"] == newest[0]


@pytest.mark.django_db
def test_equal_creation_times_still_page_without_skips_or_repeats(
    auth_client, observe_project
):
    ids = [
        auth_client.post(
            ROOT, span_body(observe_project, name=f"Tie {index}"), format="json"
        ).data["id"]
        for index in range(4)
    ]
    DerivedMetric.objects.filter(pk__in=ids).update(created_at=timezone.now())
    seen, pages = page_through(auth_client, ROOT, {"limit": 1})
    assert pages == 4 and len(set(seen)) == 4 and set(seen) == set(ids)
    assert seen == sorted(ids, reverse=True)  # id is only the tie-break


@pytest.mark.django_db
def test_run_pages_are_chronological_and_stable_while_rows_are_created(
    auth_client, saved, user
):
    definition = DerivedMetricVersion.objects.get(metric_id=saved["id"], version=1)

    def new_run():
        return DerivedMetricRun.objects.create(
            definition=definition,
            requested_by=user,
            window_start=timezone.now() - timedelta(hours=2),
            window_end=timezone.now() - timedelta(hours=1),
            as_of=timezone.now(),
            fingerprint=uuid4().hex,
        )

    runs = [new_run() for _ in range(5)]
    by_id = sorted(run.id for run in runs)
    base = timezone.now() - timedelta(days=1)
    for position, run_id in enumerate(reversed(by_id)):
        DerivedMetricRun.objects.filter(pk=run_id).update(
            created_at=base + timedelta(minutes=position)
        )
    late = []

    def interleave(page_number):
        if page_number == 1:
            late.append(new_run())

    seen, pages = page_through(
        auth_client, f"{ROOT}{saved['id']}/runs/", {"limit": 2}, interleave
    )
    assert seen == [str(run_id) for run_id in by_id] and pages == 3
    first = auth_client.get(f"{ROOT}{saved['id']}/runs/", {"limit": 1}).data["results"]
    assert first[0]["id"] == str(late[0].id)


@pytest.mark.django_db
def test_unknown_or_out_of_scope_cursor_is_rejected_not_guessed(
    auth_client, observe_project, saved
):
    other = auth_client.post(
        ROOT, span_body(observe_project, name="Elsewhere"), format="json"
    ).data
    rejected = [
        auth_client.get(ROOT, {"before": str(uuid4())}),
        auth_client.get(f"{ROOT}{saved['id']}/runs/", {"before": str(uuid4())}),
        # A real metric id is not a run cursor.
        auth_client.get(f"{ROOT}{saved['id']}/runs/", {"before": other["id"]}),
    ]
    for response in rejected:
        assert response.status_code == 400, response.data
        assert "Cursor is unknown in this scope" in field_error(response, "before")


# --- MET-5: preview is bounded ------------------------------------------------------


class SpyClient:
    """Delegates to the real ClickHouse client and records each statement's settings."""

    server_enforced_readonly = False

    def __init__(self, inner, on_first_execute=None):
        self.inner = inner
        self.settings = []
        self.on_first_execute = on_first_execute

    def execute(self, query, params=None, with_column_types=False, settings=None):
        self.settings.append(settings)
        if self.on_first_execute and len(self.settings) == 1:
            self.on_first_execute()
        return self.inner.execute(
            query, params, with_column_types=with_column_types, settings=settings
        )


def using(client):
    return patch(
        "tracer.services.derived_metrics.service.get_clickhouse_client",
        return_value=client,
    )


@pytest.mark.django_db
def test_preview_uses_a_tighter_server_side_budget_than_a_persisted_run(
    auth_client, saved, scope, metric_ch
):
    seed_two_sessions(metric_ch, scope, timezone.now() - timedelta(hours=1))
    spy = SpyClient(metric_ch)
    with using(spy):
        response = auth_client.post(
            f"{ROOT}{saved['id']}/preview/", window(), format="json"
        )
    assert response.status_code == 200, response.data
    assert [row["value"] for row in response.data["results"]] == [0.5]
    assert spy.settings == [PREVIEW_QUERY_SETTINGS]
    for name in (
        "max_execution_time",
        "max_memory_usage",
        "max_rows_to_read",
        "max_bytes_to_read",
        "max_rows_to_group_by",
        "max_result_rows",
    ):
        assert PREVIEW_QUERY_SETTINGS[name] < QUERY_SETTINGS[name], name
    for name in (
        "read_overflow_mode",
        "group_by_overflow_mode",
        "result_overflow_mode",
    ):
        assert PREVIEW_QUERY_SETTINGS[name] == "throw"
    assert PREVIEW_QUERY_SETTINGS["timeout_overflow_mode"] == "throw"
    assert service.PREVIEW_LEASE_SECONDS > PREVIEW_QUERY_SETTINGS["max_execution_time"]


@pytest.mark.django_db
def test_persisted_runs_keep_their_own_budget(auth_client, saved, scope, metric_ch):
    seed_two_sessions(metric_ch, scope, timezone.now() - timedelta(hours=1))
    spy = SpyClient(metric_ch)
    with patch("tracer.views.derived_metric._enqueue"), using(spy):
        run = auth_client.post(f"{ROOT}{saved['id']}/runs/", window(), format="json")
        execute_run(run.data["id"])
    assert spy.settings and PREVIEW_QUERY_SETTINGS not in spy.settings
    assert all(
        used in (QUERY_SETTINGS, {**QUERY_SETTINGS, "max_threads": 1})
        for used in spy.settings
    )


@pytest.mark.django_db
def test_second_concurrent_preview_by_the_same_actor_is_throttled_then_recovers(
    auth_client, saved, scope, metric_ch
):
    seed_two_sessions(metric_ch, scope, timezone.now() - timedelta(hours=1))
    nested = []

    def second_request_while_the_first_is_running():
        nested.append(
            auth_client.post(f"{ROOT}{saved['id']}/preview/", window(), format="json")
        )

    spy = SpyClient(
        metric_ch, on_first_execute=second_request_while_the_first_is_running
    )
    with using(spy):
        first = auth_client.post(
            f"{ROOT}{saved['id']}/preview/", window(), format="json"
        )
    assert first.status_code == 200, first.data
    assert len(nested) == 1 and nested[0].status_code == 429
    assert int(nested[0]["Retry-After"]) == service.PREVIEW_LEASE_SECONDS
    assert len(spy.settings) == 1  # the refused request never reached ClickHouse
    with using(SpyClient(metric_ch)):
        again = auth_client.post(
            f"{ROOT}{saved['id']}/preview/", window(), format="json"
        )
    assert again.status_code == 200, again.data


@pytest.mark.django_db
def test_slot_is_released_after_a_failed_preview(auth_client, saved):
    class Broken:
        server_enforced_readonly = False

        def execute(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

    with using(Broken()):
        failed = auth_client.post(
            f"{ROOT}{saved['id']}/preview/", window(), format="json"
        )
    assert failed.status_code == 503
    assert "database unavailable" not in str(failed.data)
    # The guard did not leak a lease: the next preview is admitted (and fails on
    # the same dead client, not with a 429).
    with using(Broken()):
        retry = auth_client.post(
            f"{ROOT}{saved['id']}/preview/", window(), format="json"
        )
    assert retry.status_code == 503


@pytest.mark.django_db
@pytest.mark.parametrize(
    "limit", ["max_rows_to_read", "max_bytes_to_read", "max_memory_usage"]
)
def test_preview_budget_is_enforced_by_the_server_and_never_truncates(
    auth_client, saved, scope, metric_ch, monkeypatch, limit
):
    seed_two_sessions(metric_ch, scope, timezone.now() - timedelta(hours=1))
    monkeypatch.setitem(PREVIEW_QUERY_SETTINGS, limit, 1)
    with using(metric_ch):
        response = auth_client.post(
            f"{ROOT}{saved['id']}/preview/", window(), format="json"
        )
    assert response.status_code == 400, response.data
    assert "interactive read budget" in field_error(response, "window")
    # An aborted read returns only the error envelope, never a partial value.
    assert "results" not in response.data
    monkeypatch.undo()
    with using(metric_ch):
        recovered = auth_client.post(
            f"{ROOT}{saved['id']}/preview/", window(), format="json"
        )
    assert recovered.status_code == 200 and recovered.data["results"][0]["value"] == 0.5


def test_admission_is_per_actor_and_per_organization_and_always_released():
    organization = uuid4()
    with preview_admission("actor-a", organization):
        with pytest.raises(MetricBusyError, match="for you"):
            with preview_admission("actor-a", organization):
                pytest.fail("a second slot for the same actor must not be granted")
        # Another actor in the same organization is still admitted.
        with preview_admission("actor-b", organization):
            pass
    with ExitStack() as stack:
        for index in range(service.PREVIEW_ORG_SLOTS):
            stack.enter_context(preview_admission(f"actor-{index}", organization))
        with pytest.raises(MetricBusyError, match="organization"):
            with preview_admission("late-actor", organization):
                pytest.fail("the organization cap must hold")
        # The refused actor's own slot was released, and other organizations are
        # unaffected by this one's cap.
        with preview_admission("late-actor", uuid4()):
            pass
    with preview_admission("late-actor", organization):
        pass


def test_a_crashed_workers_lease_expires_instead_of_blocking_forever(monkeypatch):
    monkeypatch.setattr(service, "PREVIEW_LEASE_SECONDS", 1)
    organization = uuid4()
    # A worker killed mid-query: it took the actor slot and never released it.
    assert service._acquire_slot("derived-metric-preview:actor:crashed", 1, "dead")
    with pytest.raises(MetricBusyError):
        with preview_admission("crashed", organization):
            pytest.fail("the dead worker's lease is still valid")
    time.sleep(1.3)
    with preview_admission("crashed", organization):
        pass
