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


@pytest.mark.django_db
def test_a_receipt_with_an_actor_transcript_through_the_real_endpoints_is_served_on_the_call_detail(
    organization, workspace, auth_client
):
    """What the harness uploads for a trial with actors (who spoke, who took part, what each did, the trial's notices)
    goes through the artifact and result endpoints and comes back on the call detail the web app reads.
    """
    import hashlib
    import json
    from datetime import UTC, datetime, timedelta
    from unittest.mock import MagicMock, patch

    from rest_framework.test import APIClient

    from simulate.services.hosted_harness import (
        canonical_digest,
        create_hosted_job,
        register_attempt,
    )
    from simulate.tests.test_hosted_harness_channels import BASE, _headers, _payload

    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="actor-transcript-receipt",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client, headers = APIClient(), _headers(capability)
    provisioned = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Actors",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "nego",
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
            "scenario_keys": ["nego"],
        },
        format="json",
        **headers,
    )
    document = {
        "messages": [
            {"role": "user", "content": "Can I get 25% on deal 7?"},
            {
                "role": "actor",
                "actor": "manager",
                "name": "Alex Vance",
                "content": "25% is too high; 18% is my limit.",
            },
            {"role": "assistant", "content": "Applied 18% once Alex approved it."},
        ],
        "call_actors": {
            "resolved": [
                {
                    "id": "manager",
                    "name": "Alex Vance",
                    "role": "Sales manager",
                    "channel": "chat_message",
                }
            ],
            "summary": [{"actor": "manager", "triggered": True, "delivered": 1}],
            "events": [
                {
                    "kind": "delivered",
                    "actor": "manager",
                    "name": "Alex Vance",
                    "text": "25% is too high",
                }
            ],
        },
        "world_actors": {
            "actions": [
                {
                    "actor": "manager",
                    "action": "approvals.change",
                    "outcome": "committed",
                }
            ],
            "decisions": [
                {
                    "play": "live",
                    "actor": "manager",
                    "action": "approvals.change",
                    "outcome": "committed",
                }
            ],
        },
        "notices": [
            {
                "code": "fault_not_applied",
                "message": "The timeout on apply_discount never fired.",
            }
        ],
    }
    content = json.dumps(document).encode()
    digest = hashlib.sha256(content).hexdigest()
    storage = MagicMock()
    stored = MagicMock()
    stored.read.return_value = content
    storage.get_object.return_value = stored
    with patch(
        "simulate.services.hosted_harness_ingestion.get_storage_client",
        return_value=storage,
    ):
        uploaded = client.generic(
            "PUT",
            f"{BASE}/{capability.attempt.id}/artifacts/{digest}/",
            content,
            content_type="application/json",
            HTTP_X_ARTIFACT_KIND="transcript",
            HTTP_X_ARTIFACT_SIZE=str(len(content)),
            HTTP_X_SCENARIO_KEY="nego",
            **headers,
        )
        assert uploaded.status_code == 201, uploaded.content
        started = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
        receipt = {
            "schema_version": "futureagi.harness-result.v1",
            "job_id": str(job.id),
            "attempt_id": str(capability.attempt.id),
            "attempt_number": 1,
            "scenario_key": "nego",
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
                }
            ],
            "evaluations": [],
            "call": {
                "started_at": started.isoformat(),
                "ended_at": (started + timedelta(seconds=30)).isoformat(),
                "duration_ms": 30000,
                "turns": 3,
                "transcript_artifact": f"sha256:{digest}",
                "recording_artifacts": [],
            },
            "failure": None,
        }
        receipt["digest"] = canonical_digest(receipt)
        result = client.post(
            f"{BASE}/{capability.attempt.id}/results/",
            receipt,
            format="json",
            **headers,
        )
    assert result.status_code == 200, result.content

    call = (
        job.scenario_registrations.select_related("call_execution").get().call_execution
    )
    detail = auth_client.get(f"/simulate/v3/call-executions/{call.id}/")
    assert detail.status_code == 200, detail.content
    body = detail.json()
    body = body.get("result", body)
    assert body["call_actors"] == [
        {
            "id": "manager",
            "name": "Alex Vance",
            "role": "Sales manager",
            "channel": "chat_message",
            "delivery": "delivered",
        }
    ]
    assert body["trial_state"]["decisions"][0]["actor"] == "manager"
    assert (
        body["trial_state"]["notices"][0]["message"]
        == "The timeout on apply_discount never fired."
    )
    spoken = json.dumps(body)
    assert "25% is too high; 18% is my limit." in spoken and "Alex Vance" in spoken


@pytest.mark.django_db
def test_a_run_its_own_task_started_is_served_with_the_task_s_turn_and_no_customer(
    organization, workspace, auth_client
):
    """What the harness uploads for a trial with actors (who spoke, who took part, what each did, the trial's notices)
    goes through the artifact and result endpoints and comes back on the call detail the web app reads.
    """
    import hashlib
    import json
    from datetime import UTC, datetime, timedelta
    from unittest.mock import MagicMock, patch

    from rest_framework.test import APIClient

    from simulate.services.hosted_harness import (
        canonical_digest,
        create_hosted_job,
        register_attempt,
    )
    from simulate.tests.test_hosted_harness_channels import BASE, _headers, _payload

    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="task-started-receipt",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client, headers = APIClient(), _headers(capability)
    provisioned = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Actors",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "nego",
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
            "scenario_keys": ["nego"],
        },
        format="json",
        **headers,
    )
    document = {
        "messages": [
            {
                "role": "task",
                "content": "Nightly reconciliation: post deal 9 in #sales.",
            },
            {"role": "assistant", "content": "Posted deal 9."},
        ]
    }
    content = json.dumps(document).encode()
    digest = hashlib.sha256(content).hexdigest()
    storage = MagicMock()
    stored = MagicMock()
    stored.read.return_value = content
    storage.get_object.return_value = stored
    with patch(
        "simulate.services.hosted_harness_ingestion.get_storage_client",
        return_value=storage,
    ):
        uploaded = client.generic(
            "PUT",
            f"{BASE}/{capability.attempt.id}/artifacts/{digest}/",
            content,
            content_type="application/json",
            HTTP_X_ARTIFACT_KIND="transcript",
            HTTP_X_ARTIFACT_SIZE=str(len(content)),
            HTTP_X_SCENARIO_KEY="nego",
            **headers,
        )
        assert uploaded.status_code == 201, uploaded.content
        started = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
        receipt = {
            "schema_version": "futureagi.harness-result.v1",
            "job_id": str(job.id),
            "attempt_id": str(capability.attempt.id),
            "attempt_number": 1,
            "scenario_key": "nego",
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
                }
            ],
            "evaluations": [],
            "call": {
                "started_at": started.isoformat(),
                "ended_at": (started + timedelta(seconds=30)).isoformat(),
                "duration_ms": 30000,
                "turns": 3,
                "transcript_artifact": f"sha256:{digest}",
                "recording_artifacts": [],
            },
            "failure": None,
        }
        receipt["digest"] = canonical_digest(receipt)
        result = client.post(
            f"{BASE}/{capability.attempt.id}/results/",
            receipt,
            format="json",
            **headers,
        )
    assert result.status_code == 200, result.content

    call = (
        job.scenario_registrations.select_related("call_execution").get().call_execution
    )
    detail = auth_client.get(f"/simulate/v3/call-executions/{call.id}/")
    assert detail.status_code == 200, detail.content
    body = detail.json()
    body = body.get("result", body)
    assert body["persona"] is None and body["persona_details"] is None
    assert "task" in json.dumps(body) and "Nightly reconciliation" in json.dumps(body)
    # The stored chat row keeps the task's own role on its message, which the web app reads.
    assert '"role": "task"' in json.dumps(body.get("transcript"))


def test_a_worker_s_failed_check_never_turns_a_task_with_no_verdict_into_a_scored_call_at_the_real_endpoint(
    organization,
):
    """A worker's own check is reported beside the task's, never in the call's outcome: a task whose only check is not
    applicable reads no score, whatever its workers' checks say."""
    from rest_framework.test import APIClient

    from simulate.services.hosted_harness import (
        canonical_digest,
        create_hosted_job,
        register_attempt,
    )
    from simulate.tests.test_hosted_harness_channels import BASE, _headers, _payload

    job, _ = create_hosted_job(organization, _payload(), idempotency_key="worker-outcome")
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    client, headers = APIClient(), _headers(capability)
    provisioned = client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {
            "operation": "provision",
            "name": "Worker outcome",
            "modality": "text",
            "personas": [
                {"scenario_key": "returns", "name": "Rep", "situation": "Asks", "outcome": "Policy"}
            ],
        },
        format="json",
        **headers,
    ).json()["result"]
    client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        {"operation": "begin", "run_test_id": provisioned["run_test_id"], "scenario_keys": ["returns"]},
        format="json",
        **headers,
    )
    receipt = {
        "schema_version": "futureagi.harness-result.v1",
        "job_id": str(job.id),
        "attempt_id": str(capability.attempt.id),
        "attempt_number": 1,
        "scenario_key": "returns",
        "scenario_id": provisioned["scenarios"][0]["scenario_id"],
        "scenario_attempt": 1,
        "world_index": 0,
        "status": "passed",
        "sub_goals": [
            {
                "name": "refund_issued",
                "held": None,
                "reason": "no refund was asked for",
                "judged": False,
                "verdict": "not_applicable",
                "subject": "task",
            },
            {
                "name": "lookup_stays_read_only",
                "held": False,
                "reason": "the lookup worker tried to write",
                "judged": False,
                "verdict": "fail",
                "subject": "worker:order_lookup",
            },
        ],
        "evaluations": [],
        "call": None,
        "failure": None,
    }
    receipt["digest"] = canonical_digest(receipt)
    result = client.post(f"{BASE}/{capability.attempt.id}/results/", receipt, format="json", **headers)
    assert result.status_code == 200, result.content
    call = job.scenario_registrations.select_related("call_execution").get().call_execution
    assert call.call_metadata["harness_outcome_status"] == "no_score"
