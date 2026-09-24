"""Editing and reading a finished run's authored suite."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from simulate.models import HostedHarnessJob, HostedHarnessStageOutput

pytestmark = pytest.mark.django_db


def _job(user, workspace, scenarios):
    job = HostedHarnessJob.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest=uuid.uuid4().hex,
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
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


def _client(user, job):
    """A caller scoped to the job's workspace, as the UI is."""
    client = APIClient()
    client.force_authenticate(user=user)
    client.credentials(HTTP_X_WORKSPACE_ID=str(job.workspace_id))
    return client


def _post(user, job, changes, rework=True):
    return _client(user, job).post(
        f"/simulate/api/harness-jobs/{job.id}/scenarios/amend/",
        {"changes": changes, "rework": rework},
        format="json",
    )


def test_a_descriptive_edit_lands_on_the_suite(user, workspace):
    job = _job(user, workspace, SUITE)
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


def test_a_field_that_is_proved_rather_than_described_is_refused(user, workspace):
    job = _job(user, workspace, SUITE)
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


def test_a_field_outside_the_agents_contract_is_refused_even_with_a_reproof(user, workspace):
    job = _job(user, workspace, SUITE)
    job.payload = {**job.payload, "agent": {"connector": "phone"}}
    job.save(update_fields=["payload"])
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ), patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        return_value=None,
    ):
        response = _post(user, job, [
            {"op": "set_field", "scenario": "one", "field": "max_turns", "value": 20}
        ], rework=True)
    receipt = response.json()["receipts"][0]
    assert receipt["outcome"] == "refused"
    assert "does not apply" in receipt["why"]
    output = HostedHarnessStageOutput.no_workspace_objects.get(job=job, kind="scenarios")
    assert output.data[0]["max_turns"] == 10


def test_a_chat_persona_takes_no_accent(user, workspace):
    job = _job(user, workspace, SUITE)
    job.payload = {**job.payload, "agent": {"connector": "http"}}
    job.save(update_fields=["payload"])
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ), patch(
        "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
        return_value=None,
    ):
        response = _post(user, job, [
            {"op": "set_persona", "scenario": "two", "persona": {"accent": "Indian"}}
        ], rework=True)
    receipt = response.json()["receipts"][0]
    assert receipt["outcome"] == "refused"
    assert "accent" in receipt["why"]


def test_declining_a_reproof_keeps_the_run_unchanged(user, workspace):
    job = _job(user, workspace, SUITE)
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


def test_an_edit_reaching_a_live_guest_is_queued_not_applied(user, workspace):
    job = _job(user, workspace, SUITE)
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


def test_the_suite_is_read_a_page_at_a_time(user, workspace):
    job = _job(user, workspace, SUITE)
    response = _client(user, job).get(
        f"/simulate/api/harness-jobs/{job.id}/scenarios/?limit=1&page=1"
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert set(body) >= {
        "count",
        "next",
        "previous",
        "results",
        "total_pages",
        "current_page",
    }
    assert body["current_page"] == 1


def test_a_text_filter_matches_part_of_the_value(user, workspace):
    job = _job(user, workspace, [
        {"name": "refund_after_deadline", "tests": "t"},
        {"name": "booking_change", "tests": "t"},
    ])
    client = _client(user, job)
    base = f"/simulate/api/harness-jobs/{job.id}/scenarios/"

    found = client.get(f"{base}?name_contains=DEADLINE").json()
    missed = client.get(f"{base}?name_not_contains=deadline").json()
    exact = client.get(f"{base}?name=deadline").json()

    assert [row["name"] for row in found["results"]] == ["refund_after_deadline"]
    assert [row["name"] for row in missed["results"]] == ["booking_change"]
    assert exact["count"] == 0


def test_coverage_names_the_attacks_every_suite_covers(user, workspace):
    job = _job(user, workspace, SUITE)
    body = _client(user, job).get(
        f"/simulate/api/harness-jobs/{job.id}/scenarios/coverage/"
    ).json()

    required = {one["value"]: one["label"] for one in body["required_overlays"]}
    assert required["privacy_pii"] == "Personal data"
    assert "none" not in required


class TestScenarioNumbersSurviveADeletion:
    """A scenario keeps its number across deletions."""

    def _numbers(self, job):
        from simulate.models import HostedHarnessScenario

        return {
            row.scenario_key: row.number
            for row in HostedHarnessScenario.no_workspace_objects.filter(job=job)
        }

    def test_survivors_keep_their_number(self, user, workspace):
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": name, "tests": "t"} for name in ("one", "two", "three", "four")]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        assert self._numbers(job) == {"one": 1, "two": 2, "three": 3, "four": 4}

        index_scenarios(job, [one for one in suite if one["name"] != "two"], prune=True)
        assert self._numbers(job) == {"one": 1, "three": 3, "four": 4}

    def test_a_dropped_scenario_leaves_the_table(self, user, workspace):
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": name, "tests": "t"} for name in ("one", "two")]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        index_scenarios(job, [suite[0]], prune=True)
        assert set(self._numbers(job)) == {"one"}

    def test_a_new_scenario_takes_the_next_free_number(self, user, workspace):
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": name, "tests": "t"} for name in ("one", "two", "three")]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        index_scenarios(job, [suite[0], suite[2], {"name": "four", "tests": "t"}], prune=True)
        assert self._numbers(job) == {"one": 1, "three": 3, "four": 4}

    def test_a_number_means_the_row_the_table_shows(self, user, workspace):
        from simulate.services.harness_provider import scenarios_meant
        from simulate.services.harness_scenarios import index_scenarios

        suite = [{"name": name, "tests": "t"} for name in ("one", "two", "three", "four")]
        job = _job(user, workspace, suite)
        index_scenarios(job, suite)
        left = [one for one in suite if one["name"] != "two"]
        index_scenarios(job, left, prune=True)

        numbering = self._numbers(job)
        by_number = {number: key for key, number in numbering.items()}
        assert scenarios_meant("4", left, by_number) == ["four"]
        assert scenarios_meant("3-4", left, by_number) == ["three", "four"]


def test_a_short_suite_on_a_poll_never_deletes_a_row(user, workspace):
    from simulate.models import HostedHarnessScenario
    from simulate.services.harness_scenarios import index_scenarios

    suite = [{"name": name, "tests": "t"} for name in ("one", "two", "three")]
    job = _job(user, workspace, suite)
    index_scenarios(job, suite)
    index_scenarios(job, suite[:1])
    assert HostedHarnessScenario.no_workspace_objects.filter(job=job).count() == 3


def test_a_scenario_with_no_attack_reads_as_no_attack_on_every_attack_axis():
    from simulate.services.harness_scenarios import level_label

    assert level_label("none") == "No attack"
    assert level_label("absent") == "No attack"
    assert level_label("prompt_injection") == "Injected instruction"
