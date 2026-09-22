"""Editing and reading a finished run's authored suite."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from simulate.models import HostedHarnessJob, HostedHarnessStageOutput

pytestmark = pytest.mark.django_db


def _job(user, scenarios):
    job = HostedHarnessJob.no_workspace_objects.create(
        organization=user.organization,
        workspace=user.workspace,
        scenario_count=len(scenarios),
        payload={"metadata": {}, "runtime": {"max_duration_seconds": 600}},
    )
    HostedHarnessStageOutput.no_workspace_objects.create(
        job=job,
        title="Scenarios",
        summary=f"{len(scenarios)} pre-authored scenarios",
        kind="scenarios",
        data=scenarios,
    )
    return job


SUITE = [
    {"name": "one", "tests": "the agent holds the line", "max_turns": 10},
    {"name": "two", "tests": "the agent asks", "persona": {"accent": "American"}},
]


def _post(user, job, changes, rework=True):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.post(
        f"/simulate/api/harness-jobs/{job.id}/scenarios/amend/",
        {"changes": changes, "rework": rework},
        format="json",
    )


def test_a_descriptive_edit_lands_on_the_suite(user):
    job = _job(user, SUITE)
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ), patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        return_value=None,
    ):
        response = _post(user, job, [
            {"op": "set_field", "scenario": "one", "field": "tests", "value": "reworded"}
        ])
    assert response.status_code == 200, response.content
    assert response.json()["receipts"][0]["outcome"] == "applied"
    output = HostedHarnessStageOutput.no_workspace_objects.get(job=job, kind="scenarios")
    assert output.data[0]["tests"] == "reworded"


def test_a_field_that_is_proved_rather_than_described_is_refused(user):
    job = _job(user, SUITE)
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ), patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        return_value=None,
    ):
        response = _post(user, job, [
            {"op": "set_field", "scenario": "one", "field": "instruction", "value": "x"}
        ])
    receipt = response.json()["receipts"][0]
    assert receipt["outcome"] == "refused"
    assert "not editable" in receipt["why"]


def test_declining_a_reproof_keeps_the_run_unchanged(user):
    job = _job(user, SUITE)
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ), patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        return_value=None,
    ):
        response = _post(
            user, job, [{"op": "drop", "scenario": "two"}], rework=False
        )
    assert response.json()["receipts"][0]["outcome"] == "refused"
    output = HostedHarnessStageOutput.no_workspace_objects.get(job=job, kind="scenarios")
    assert len(output.data) == 2


def test_an_edit_reaching_a_live_guest_is_queued_not_applied(user):
    job = _job(user, SUITE)
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=True,
    ) as pushed, patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        return_value=None,
    ) as sealed:
        response = _post(user, job, [
            {"op": "set_field", "scenario": "one", "field": "tests", "value": "reworded"}
        ])
    assert response.json()["receipts"][0]["outcome"] == "queued"
    pushed.assert_called_once()
    sealed.assert_called_once()


def test_the_suite_is_read_a_page_at_a_time(user):
    job = _job(user, SUITE)
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(f"/simulate/api/harness-jobs/{job.id}/scenarios/?limit=1&page=1")
    assert response.status_code == 200, response.content
    body = response.json()
    # The platform's own page shape, so the table reads this the way it reads every other list.
    assert set(body) >= {
        "count",
        "next",
        "previous",
        "results",
        "total_pages",
        "current_page",
    }
    assert body["current_page"] == 1
