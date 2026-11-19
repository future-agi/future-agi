"""What the harness writes about a trial's evidence reaches the call as it said it: the baseline a start was compared
with, a notice that evidence never arrived, and a check the trial could not measure, read as unmeasured (never failed)
with its reason shown as written."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from simulate.models import CallExecution
from simulate.services.hosted_harness import (
    canonical_digest,
    create_hosted_job,
    register_attempt,
)
from simulate.services.hosted_harness_ingestion import (
    TRIAL_STATE_KEY,
    _world_change_fields,
    sub_goal_result,
)
from simulate.tests.test_harness_call_actors import _ingest
from simulate.tests.test_hosted_harness_channels import BASE, _headers, _payload


@pytest.mark.parametrize(
    ("against", "kept"),
    [("sealed_seed", True), ("first_trial", True), ("guess", False)],
)
def test_a_start_keeps_the_baseline_it_was_compared_with(against, kept):
    start = _world_change_fields(
        {"start": {"state": "differs", "tables": ["orders"], "against": against}}
    )["start"]

    assert start.get("against") == (against if kept else None)


@pytest.mark.django_db
def test_a_notice_that_evidence_never_arrived_keeps_its_count(organization, workspace):
    metadata: dict = {}
    _ingest(
        organization,
        workspace,
        "evidence-incomplete",
        CallExecution.SimulationCallType.TEXT,
        {
            "messages": [],
            "notices": [
                {
                    "code": "evidence_incomplete",
                    "count": 2,
                    "message": "Two pieces never arrived.",
                }
            ],
        },
        metadata,
    )

    assert metadata[TRIAL_STATE_KEY]["notices"] == [
        {
            "code": "evidence_incomplete",
            "message": "Two pieces never arrived.",
            "count": 2,
        }
    ]


def test_a_check_s_result_is_its_verdict_never_its_reason_s_wording():
    assert (
        sub_goal_result(
            {"held": None, "reason": "Unmeasured: x", "verdict": "unmeasured"}
        )
        == "unmeasured"
    )
    assert (
        sub_goal_result(
            {"held": None, "reason": "Unmeasured: the action log could not be read"}
        )
        is None
    )


@pytest.mark.django_db
def test_a_trial_whose_evidence_is_unmeasured_is_errored_and_each_check_shows_its_reason(
    organization,
):
    from simulate.services.run_results_v3 import build_call_rows

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="evidence-unmeasured"
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client, headers = APIClient(), _headers(capability)
    provisioned = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Unmeasured evidence",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "refund",
                    "name": "Rep",
                    "situation": "Asks",
                    "outcome": "Refunded",
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
            "scenario_keys": ["refund"],
        },
        format="json",
        **headers,
    )
    why = "Unmeasured: the world's action log could not be read"
    receipt = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "scenario_key": "refund",
        "scenario_id": provisioned["scenarios"][0]["scenario_id"],
        "scenario_attempt": 1,
        "world_index": 0,
        "status": "errored",
        "sub_goals": [
            {
                "name": "refunds_once",
                "held": None,
                "reason": why,
                "judged": False,
                "verdict": "unmeasured",
                "evidence": ["world_state"],
            },
            {
                "name": "tells_the_customer",
                "verdict": "unmeasured",
                "held": None,
                "reason": why,
                "judged": False,
            },
        ],
        "evaluations": [],
        "call": None,
        "failure": {
            "domain": "simulator",
            "stage": "grading",
            "code": "evidence_unmeasured",
            "message": "the trial's evidence could not be read",
        },
    }
    receipt["digest"] = canonical_digest(receipt)
    posted = client.post(
        f"{BASE}/{capability.attempt.id}/results/", receipt, format="json", **headers
    )
    assert posted.status_code == 200, posted.content

    call = (
        job.scenario_registrations.select_related("call_execution").get().call_execution
    )
    assert call.call_metadata["harness_outcome_status"] == "errored"
    # Never decided, never failed: both stay requested, neither is published as a failed eval.
    assert call.call_metadata["harness_eval_coverage"]["unmeasured"] == 2
    assert call.call_metadata["harness_eval_coverage"]["failed"] == 0
    assert call.call_metadata["harness_evaluations"] == []
    [row], _ = build_call_rows(call.test_execution, [call])
    assert [
        (one["name"], one["passed"], one.get("result"), one.get("reason"))
        for one in row["sub_goal_results"]
    ] == [
        ("refunds_once", None, "unmeasured", why),
        ("tells_the_customer", None, "unmeasured", why),
    ]
