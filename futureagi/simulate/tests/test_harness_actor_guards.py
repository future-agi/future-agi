"""The platform side of the actors plan that needs no database: scenario guards, unmeasured coverage."""

from __future__ import annotations

import pytest

from simulate.services.harness_scenarios import (
    actor_reference_problems,
    call_actor_entries,
    with_sessions_to_rewrite,
)
from simulate.services.hosted_harness_ingestion import _receipt_evaluation_coverage


def test_a_scenario_naming_an_actor_the_environment_does_not_list_is_refused_wherever_it_names_it():
    doc = {
        "name": "acme",
        "actors": [{"id": "manager"}, {"id": "ghost"}],
        "pre_run": [{"actors": ["priya"], "thread": [{"actor": "omar"}]}],
        "starts_with": {"actor": "bot"},
    }
    assert actor_reference_problems(doc, {"manager", "priya"}) == [
        "acme: bot is not an actor in this environment",
        "acme: ghost is not an actor in this environment",
        "acme: omar is not an actor in this environment",
    ]
    assert actor_reference_problems({"actors": [{"id": "manager"}]}, {"manager"}) == []


def test_a_changed_pre_run_situation_clears_its_thread_and_an_unchanged_one_keeps_it():
    before = {
        "pre_run": [
            {
                "actors": ["a"],
                "about": "Acme wants 20%",
                "thread": [{"text": "hi"}],
                "fingerprint": "f",
            }
        ]
    }
    same = with_sessions_to_rewrite(before, before)
    changed = with_sessions_to_rewrite(
        {"pre_run": [{**before["pre_run"][0], "about": "Acme wants 30%"}]}, before
    )
    assert same["pre_run"][0]["thread"] == [{"text": "hi"}]
    assert (
        changed["pre_run"][0]["thread"] == []
        and changed["pre_run"][0]["fingerprint"] == ""
    )


def test_a_scenario_actor_without_a_trigger_keeps_none_and_keeps_its_knowledge():
    [entry] = call_actor_entries(
        [{"id": "manager", "knowledge": [{"kind": "facts", "text": "x"}]}]
    )
    assert "trigger" not in entry and entry["knowledge"] == [
        {"kind": "facts", "text": "x"}
    ]


def test_an_unmeasured_sub_goal_stays_requested_and_is_never_counted_as_evaluated():
    coverage = _receipt_evaluation_coverage(
        {
            "sub_goals": [
                {"name": "a", "verdict": "pass"},
                {"name": "b", "verdict": "unmeasured"},
                {"name": "c", "held": None},
            ]
        }
    )
    assert coverage == {
        "expected": 3,
        "executed": 1,
        "failed": 1,
        "unmeasured": 1,
        "complete": False,
    }


def test_an_unmeasured_sub_goal_is_never_published_as_an_eval():
    from simulate.services.hosted_harness_ingestion import _receipt_evaluations

    published = _receipt_evaluations(
        {
            "sub_goals": [
                {"name": "a", "verdict": "pass"},
                {"name": "b", "verdict": "unmeasured"},
            ]
        }
    )
    # Only what was decided becomes an eval; an unmeasured one would read as a Failed output.
    assert [one["name"] for one in published] == ["a"]


@pytest.mark.django_db
def test_a_receipt_with_unmeasured_and_not_applicable_sub_goals_and_their_evidence_is_taken_by_the_real_endpoint(
    organization,
):
    """The harness sends `unmeasured` verdicts and each sub-goal's evidence; the endpoint must take both and keep them,
    not answer 400 or drop them before ingestion."""
    from rest_framework.test import APIClient

    from simulate.services.hosted_harness import (
        canonical_digest,
        create_hosted_job,
        register_attempt,
    )
    from simulate.tests.test_hosted_harness_channels import BASE, _headers, _payload

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="unmeasured-receipt"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client, headers = APIClient(), _headers(capability)
    provisioned = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Unmeasured",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "approval",
                    "name": "Rep",
                    "situation": "Asks",
                    "outcome": "Policy",
                }
            ],
        },
        format="json",
        **headers,
    ).json()["result"]
    client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "begin",
            "run_test_id": provisioned["run_test_id"],
            "scenario_keys": ["approval"],
        },
        format="json",
        **headers,
    )
    receipt = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "scenario_key": "approval",
        "scenario_id": provisioned["scenarios"][0]["scenario_id"],
        "scenario_attempt": 1,
        "world_index": 0,
        "status": "passed",
        "sub_goals": [
            {
                "name": "applies_approved_discount",
                "held": True,
                "reason": "",
                "judged": False,
                "verdict": "pass",
                "evidence": ["world_state"],
            },
            {
                "name": "waits_for_manager",
                "held": None,
                "reason": "the check could not read the log",
                "judged": False,
                "verdict": "unmeasured",
                "evidence": ["tool_calls"],
            },
            {
                "name": "answers_lead",
                "held": None,
                "reason": "the lead never came in",
                "judged": False,
                "verdict": "not_applicable",
            },
        ],
        "evaluations": [],
        "call": None,
        "failure": None,
    }
    receipt["digest"] = canonical_digest(receipt)
    result = client.post(
        f"{BASE}/{capability.attempt.id}/results/", receipt, format="json", **headers
    )
    assert result.status_code == 200, result.content

    call = (
        job.scenario_registrations.select_related("call_execution").get().call_execution
    )
    kept = {
        one["name"]: one
        for one in call.call_metadata["hosted_harness_receipt"]["sub_goals"]
    }
    assert kept["waits_for_manager"]["verdict"] == "unmeasured"
    assert kept["waits_for_manager"]["evidence"] == ["tool_calls"]
    assert kept["applies_approved_discount"]["evidence"] == ["world_state"]
    assert call.call_metadata["harness_eval_coverage"]["unmeasured"] == 1
