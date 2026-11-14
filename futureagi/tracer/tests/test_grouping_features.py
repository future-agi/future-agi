"""Publication-to-feature-queue behavior; no embedding/provider calls."""

from unittest.mock import patch

import pytest
from django.db import transaction
from django.test import override_settings
from django.utils import timezone

from tracer.constants.grouping_versions import FEATURE_POLICY_VERSION
from tracer.models.trace_grouping import GroupingFeatureState, TraceGroupingFeatureJob
from tracer.models.trace_investigation import (
    TraceInvestigationFinding,
    TraceInvestigationReport,
)
from tracer.services.grouping_features import enqueue_grouping_features
from tracer.services.trace_investigation import (
    claim_due_investigations,
    record_trace_notifications,
)
from tracer.tests.test_trace_investigation_control import (
    _configure,
    _delivery,
    _publish,
    _result,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def grouping_test_budgets(settings):
    settings.ERROR_FEED_GROUPING_PROJECT_BUDGET_USD = "10"
    settings.ERROR_FEED_GROUPING_WORK_BUDGET_USD = "10"
    settings.ERROR_FEED_GROUPING_TENANT_BUDGET_USD = "10"


@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
)
def test_unconfigured_budgets_and_oss_do_not_enqueue_paid_work(observe_project):
    _configure(observe_project)
    with override_settings(ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="0"):
        report, _, _ = publish(observe_project)
        assert enqueue_grouping_features(report=report) is None
    with patch("tracer.services.grouping.control.is_oss", return_value=True):
        assert enqueue_grouping_features(report=report) is None
    assert not TraceGroupingFeatureJob.no_workspace_objects.exists()


@pytest.mark.parametrize("deleted_record", ["report", "finding"])
@override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0)
def test_managers_exclude_soft_deleted_preparation_inputs(
    observe_project, deleted_record
):
    _configure(observe_project)
    with override_settings(
        ERROR_FEED_GROUPING_ENABLED=True,
        ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ):
        report, _, _ = publish(observe_project)
        if deleted_record == "report":
            TraceInvestigationReport.no_workspace_objects.filter(pk=report.pk).update(
                deleted=True
            )
        else:
            TraceInvestigationFinding.no_workspace_objects.filter(report=report).update(
                deleted=True
            )
        assert enqueue_grouping_features(report=report) is None


def publish(project, *, trace_id=None, findings=True, failed=False):
    record_trace_notifications(deliveries=[_delivery(project, trace_id=trace_id)])
    claim = claim_due_investigations(
        worker_id="feature-test", engine_version="omega-v1", limit=1
    )["claims"][0]
    result = _result(claim, findings=findings)
    if failed:
        result["execution_status"] = "failed"
        from tracer.services.trace_investigation import canonical_wire_result_digest

        result["result_digest"] = canonical_wire_result_digest(result)
    receipt = _publish(
        idempotency_key=claim["attempt_id"],
        lease_token=claim["lease_token"],
        result=result,
    )
    return (
        TraceInvestigationReport.no_workspace_objects.get(pk=receipt["report_id"]),
        claim,
        result,
    )


@override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0)
def test_publication_enqueues_once_immediately_and_in_same_transaction(observe_project):
    _configure(observe_project)
    with override_settings(
        ERROR_FEED_GROUPING_ENABLED=True,
        ERROR_FEED_GROUPING_PROJECT_IDS=[str(observe_project.id)],
    ):
        before = timezone.now()
        report, claim, result = publish(observe_project)
        job = TraceGroupingFeatureJob.no_workspace_objects.get(report=report)
        assert job.state == GroupingFeatureState.PENDING
        assert job.policy_version == FEATURE_POLICY_VERSION
        assert job.publication_result_digest == report.result_digest
        assert before <= job.not_before <= timezone.now()
        original_deadline = job.not_before
        duplicate = _publish(
            idempotency_key=claim["attempt_id"],
            lease_token=claim["lease_token"],
            result=result,
        )
        assert str(duplicate["report_id"]) == str(report.id)
        assert enqueue_grouping_features(report=report).id == job.id
        assert TraceGroupingFeatureJob.no_workspace_objects.count() == 1
        job.refresh_from_db()
        assert job.not_before == original_deadline


@override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0)
def test_feature_enqueue_failure_rolls_back_new_report(observe_project):
    _configure(observe_project)
    with patch(
        "tracer.services.trace_investigation.enqueue_grouping_features",
        side_effect=RuntimeError("queue unavailable"),
    ):
        with pytest.raises(RuntimeError, match="queue unavailable"):
            publish(observe_project)
    assert not TraceInvestigationReport.no_workspace_objects.exists()


@override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0)
def test_outer_rollback_does_not_leave_feature_work(observe_project):
    _configure(observe_project)
    with override_settings(
        ERROR_FEED_GROUPING_ENABLED=True,
        ERROR_FEED_GROUPING_PROJECT_IDS=[str(observe_project.id)],
    ):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                publish(observe_project)
                raise RuntimeError("rollback")
    assert not TraceGroupingFeatureJob.no_workspace_objects.exists()


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("all_projects", [False, True])
@pytest.mark.parametrize("allowed", [False, True])
@override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0)
def test_feature_work_rollout_gates(observe_project, enabled, all_projects, allowed):
    _configure(observe_project)
    with override_settings(
        ERROR_FEED_GROUPING_ENABLED=enabled,
        ERROR_FEED_GROUPING_ALL_PROJECTS=all_projects,
        ERROR_FEED_GROUPING_PROJECT_IDS=[str(observe_project.id)] if allowed else [],
    ):
        publish(observe_project)
    assert TraceGroupingFeatureJob.no_workspace_objects.exists() == (
        enabled and (all_projects or allowed)
    )


@pytest.mark.parametrize("findings,failed", [(False, False), (True, True)])
@pytest.mark.parametrize("all_projects", [False, True])
@override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0)
def test_no_feature_work_for_empty_or_failed_investigation(
    observe_project, findings, failed, all_projects
):
    _configure(observe_project)
    with override_settings(
        ERROR_FEED_GROUPING_ENABLED=True,
        ERROR_FEED_GROUPING_ALL_PROJECTS=all_projects,
        ERROR_FEED_GROUPING_PROJECT_IDS=[]
        if all_projects
        else [str(observe_project.id)],
    ):
        publish(observe_project, findings=findings, failed=failed)
    assert not TraceGroupingFeatureJob.no_workspace_objects.exists()


@override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0)
def test_empty_replacement_supersedes_old_features_and_stale_object_cannot_enqueue(
    observe_project,
):
    _configure(observe_project)
    with override_settings(
        ERROR_FEED_GROUPING_ENABLED=True,
        ERROR_FEED_GROUPING_PROJECT_IDS=[str(observe_project.id)],
    ):
        old_report, _, _ = publish(observe_project)
        publish(observe_project, trace_id=old_report.trace_id, findings=False)
        assert enqueue_grouping_features(report=old_report) is None
        job = TraceGroupingFeatureJob.no_workspace_objects.get(report=old_report)
        assert job.state == GroupingFeatureState.SUPERSEDED
        assert TraceGroupingFeatureJob.no_workspace_objects.count() == 1
