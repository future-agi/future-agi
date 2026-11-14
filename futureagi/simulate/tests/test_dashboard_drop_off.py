"""Caller drop-off requires an unfinished, failed caller-side ending."""

import pytest

from simulate.models.test_execution import CallExecution

pytest_plugins = ["simulate.tests.test_analytics_functional"]


def _metric(response):
    assert response.status_code == 200
    return next(
        item
        for item in response.json()["dashboard"]["metrics"]
        if item["key"] == "drop_off"
    )


def test_drop_off_shares_endings_category_and_requires_completion_evidence(
    auth_client, test_execution, scenario
):
    # The shared chart category accepts caller aliases beyond a fixed allowlist.
    caller_reasons = [
        "customer-ended-call",
        "customer_end_call",
        "CUSTOMER_ENDED_CALL",
        "caller-ended-call",
        "caller_hangup",
        "client-disconnected",
        "human-ended",
        "hangup-by-user",
    ]
    simulator_reasons = [
        "simulator_end_call",
        "SIMULATOR_END_CALL",
        "simulator-ended-call",
    ]
    excluded_reasons = [
        "target_disconnected",
        "participant_disconnected",
        "session_closed",
        "closed",
        "customer-error",
        "user-declined",
        "some-new-provider-reason",
        "voicemail",
    ]
    for reason in caller_reasons + simulator_reasons + excluded_reasons:
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1230001111",
            status="completed",
            ended_reason=reason,
            call_metadata={
                "harness_outcome_status": "failed",
                "hosted_harness_receipt": {
                    "call": {
                        "stop_reason": reason,
                        "script_completed": False,
                        "target_metrics": {
                            "provider_end_reason": "customer-ended-call"
                        },
                    }
                },
            },
        )

    # Only explicitly unfinished scripts with evaluated failures enter the numerator.
    extra_cases = [
        (True, "failed", "completed"),
        (False, "passed", "completed"),
        (False, "error", "failed"),
        (False, None, "completed"),
        (None, "failed", "completed"),
    ]
    for completed, outcome, status in extra_cases:
        receipt_call = {"stop_reason": "customer-ended-call"}
        if completed is not None:
            receipt_call["script_completed"] = completed
        metadata = {"hosted_harness_receipt": {"call": receipt_call}}
        if outcome is not None:
            metadata["harness_outcome_status"] = outcome
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1230001111",
            status=status,
            ended_reason="customer-ended-call",
            call_metadata=metadata,
        )

    response = auth_client.get(
        f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
    )
    metric = _metric(response)
    counted = len(caller_reasons) + len(simulator_reasons)
    measured = counted + len(excluded_reasons) + 2
    assert metric["measured"] == measured
    assert metric["value"] == round(counted * 100 / measured, 2)
    endings = next(
        chart
        for chart in response.json()["dashboard"]["breakdowns"]
        if chart["key"] == "disconnection"
    )
    caller_segment = next(
        segment
        for segment in endings["segments"]
        if segment["label"] == "Caller hung up"
    )
    assert caller_segment["count"] == len(caller_reasons) + len(extra_cases)
    simulator_segment = next(
        segment
        for segment in endings["segments"]
        if segment["label"] == "Simulator ended"
    )
    assert simulator_segment["count"] == len(simulator_reasons)


@pytest.mark.parametrize("reason", ["customer-ended-call", "simulator_end_call"])
def test_drop_off_is_unmeasured_without_explicit_script_completion(
    auth_client, test_execution, scenario, reason
):
    CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1230001111",
        status="completed",
        ended_reason=reason,
        call_metadata={
            "harness_outcome_status": "failed",
            "hosted_harness_receipt": {"call": {"stop_reason": reason}},
        },
    )
    metric = _metric(
        auth_client.get(f"/simulate/v3/test-executions/{test_execution.id}/analytics/")
    )
    assert metric["measured"] == 0
    assert metric["value"] is None
