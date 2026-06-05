"""DB test for simulation alerting metric reader (TH-5642).

UserAlertMonitor gains SIM_EVAL_SCORE / SIM_FAILURE_RATE metric types that read
CallExecution data (not CH traces), so users can alert on sim quality regressions /
failure spikes (the Slack/email/webhook plumbing already exists). This pins the
metric computation.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from model_hub.models.choices import StatusType
from simulate.models import AgentDefinition, CallExecution, RunTest, Scenarios
from simulate.models.test_execution import TestExecution
from tracer.models.monitor import MonitorMetricTypeChoices


def _call(organization, workspace, *, overall_score=None, status=None):
    ad = AgentDefinition.objects.create(
        agent_name="Alert Agent", agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True, description="alert", organization=organization,
        workspace=workspace, provider="vapi", languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name="Alert Scenario", description="a", source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET, organization=organization,
        workspace=workspace, agent_definition=ad, status=StatusType.COMPLETED.value,
    )
    rt = RunTest.objects.create(
        name="Alert Run", description="a", agent_definition=ad,
        organization=organization, workspace=workspace,
    )
    te = TestExecution.objects.create(
        run_test=rt, status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1, total_calls=1, agent_definition=ad,
    )
    return CallExecution.objects.create(
        test_execution=te, scenario=scenario,
        status=status or CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        overall_score=overall_score,
    )


@pytest.mark.unit
@pytest.mark.django_db
def test_sim_eval_score_avg(organization, workspace):
    _call(organization, workspace, overall_score=0.8)
    _call(organization, workspace, overall_score=0.4)
    _call(organization, workspace, overall_score=None)  # excluded from the average

    from tracer.utils.monitor import _get_sim_metric_value

    monitor = SimpleNamespace(
        metric_type=MonitorMetricTypeChoices.SIM_EVAL_SCORE,
        project=SimpleNamespace(organization_id=organization.id),
    )
    now = timezone.now()
    value = _get_sim_metric_value(monitor, now - timedelta(hours=1), now + timedelta(hours=1))
    assert value == pytest.approx(0.6)  # (0.8 + 0.4) / 2, None excluded


@pytest.mark.unit
@pytest.mark.django_db
def test_sim_failure_rate(organization, workspace):
    _call(organization, workspace, status=CallExecution.CallStatus.COMPLETED)
    _call(organization, workspace, status=CallExecution.CallStatus.COMPLETED)
    _call(organization, workspace, status=CallExecution.CallStatus.FAILED)

    from tracer.utils.monitor import _get_sim_metric_value

    monitor = SimpleNamespace(
        metric_type=MonitorMetricTypeChoices.SIM_FAILURE_RATE,
        project=SimpleNamespace(organization_id=organization.id),
    )
    now = timezone.now()
    value = _get_sim_metric_value(monitor, now - timedelta(hours=1), now + timedelta(hours=1))
    assert value == pytest.approx(1 / 3)  # 1 failed of 3


@pytest.mark.unit
@pytest.mark.django_db
def test_sim_metric_window_excludes_out_of_range(organization, workspace):
    _call(organization, workspace, overall_score=0.9)

    from tracer.utils.monitor import _get_sim_metric_value

    monitor = SimpleNamespace(
        metric_type=MonitorMetricTypeChoices.SIM_EVAL_SCORE,
        project=SimpleNamespace(organization_id=organization.id),
    )
    # A window in the future contains no calls → None.
    now = timezone.now()
    value = _get_sim_metric_value(monitor, now + timedelta(hours=1), now + timedelta(hours=2))
    assert value is None
