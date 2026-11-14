"""Simulation admission must cover the same unresolved findings as diagnosis."""

import pytest

from tracer.constants.grouping_versions import (
    GROUPING_GATE_VERSION,
    GROUPING_POLICY_VERSION,
    SIMULATION_GROUPING_POLICY_VERSION,
)
from tracer.models.trace_grouping import TraceGroupingScope
from tracer.models.trace_investigation import InvestigationWorkload
from tracer.queries.grouping import GroupingSnapshotError, groupable_findings
from tracer.tests.test_grouping_snapshot import _saved_report

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("outcome", ["success", "failure"])
@pytest.mark.parametrize("policy", [None, SIMULATION_GROUPING_POLICY_VERSION])
def test_simulation_behavior_without_requirement_is_groupable(
    observe_project, outcome, policy
):
    report = _saved_report(observe_project)
    finding = report.findings.get()
    finding.requirement = None
    finding.save(update_fields=["requirement"])
    # Exercise admission independently of snapshot scope serialization.
    report.workload_type = InvestigationWorkload.SIMULATION_TEST_EXECUTION
    report.outcome = outcome
    if policy:
        TraceGroupingScope.no_workspace_objects.create(
            project=observe_project,
            organization_id=observe_project.organization_id,
            workspace_id=observe_project.workspace_id,
            policy_version=policy,
        )
    assert list(groupable_findings(report).values_list("id", flat=True)) == [finding.id]
    finding.recovery = "recovered"
    finding.save(update_fields=["recovery"])
    assert not groupable_findings(report).exists()


def test_existing_registry_keeps_its_original_admission_until_migrated(observe_project):
    report = _saved_report(observe_project)
    finding = report.findings.get()
    finding.requirement = None
    finding.save(update_fields=["requirement"])
    report.workload_type = InvestigationWorkload.SIMULATION_TEST_EXECUTION
    scope = TraceGroupingScope.no_workspace_objects.create(
        project=observe_project,
        organization_id=observe_project.organization_id,
        workspace_id=observe_project.workspace_id,
        policy_version=GROUPING_POLICY_VERSION,
    )
    assert not groupable_findings(report).exists()
    scope.policy_version = SIMULATION_GROUPING_POLICY_VERSION
    scope.save(update_fields=["policy_version"])
    assert groupable_findings(report).get().id == finding.id


def test_legacy_trace_admission_requires_failed_task_and_violated_requirement(
    observe_project,
):
    report = _saved_report(observe_project)
    assert groupable_findings(report).count() == 1
    report.outcome = "success"
    assert not groupable_findings(report).exists()
    report.outcome = "failure"
    finding = report.findings.get()
    finding.requirement = None
    finding.save(update_fields=["requirement"])
    assert not groupable_findings(report).exists()


def test_incomplete_simulation_report_cannot_enter_grouping(observe_project):
    report = _saved_report(observe_project)
    report.workload_type = InvestigationWorkload.SIMULATION_TEST_EXECUTION
    report.execution_status = "failed"
    assert not groupable_findings(report).exists()


@pytest.mark.parametrize(
    "policy", [GROUPING_POLICY_VERSION, SIMULATION_GROUPING_POLICY_VERSION]
)
def test_current_report_gate_admits_behavior_regardless_of_registry_policy(
    observe_project, policy
):
    report = _saved_report(observe_project)
    report.workload_type = InvestigationWorkload.SIMULATION_TEST_EXECUTION
    report.grouping_gate = GROUPING_GATE_VERSION
    report.outcome = "success"
    finding = report.findings.get()
    finding.requirement = None
    finding.save(update_fields=["requirement"])
    TraceGroupingScope.no_workspace_objects.create(
        project=observe_project,
        organization_id=observe_project.organization_id,
        workspace_id=observe_project.workspace_id,
        policy_version=policy,
    )
    assert groupable_findings(report).get().id == finding.id


def test_simulation_upgrade_does_not_bypass_unknown_report_gate(observe_project):
    report = _saved_report(observe_project)
    report.workload_type = InvestigationWorkload.SIMULATION_TEST_EXECUTION
    report.grouping_gate = "unknown/v9"
    with pytest.raises(GroupingSnapshotError, match="unknown grouping gate"):
        groupable_findings(report)
