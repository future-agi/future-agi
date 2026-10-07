from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from simulate.models import CallExecution
from simulate.services import alk_simulate_ingestion, hosted_harness_ingestion


@pytest.fixture
def projected_call(monkeypatch):
    monkeypatch.setattr(
        hosted_harness_ingestion,
        "_resolve_scenario_modality",
        lambda *_: CallExecution.SimulationCallType.TEXT,
    )
    artifacts = MagicMock()
    artifacts.order_by.return_value.last.return_value = None
    monkeypatch.setattr(
        hosted_harness_ingestion.HostedHarnessArtifact.no_workspace_objects,
        "filter",
        lambda **_: artifacts,
    )
    monkeypatch.setattr(
        alk_simulate_ingestion, "_apply_harness_evaluation_outputs", lambda _: None
    )
    monkeypatch.setattr(
        hosted_harness_ingestion.transaction, "on_commit", lambda _: None
    )
    return SimpleNamespace(
        call_metadata={},
        conversation_metrics_data={},
        ended_reason="old-reason",
        test_execution=SimpleNamespace(run_test_id=None),
        id="call-id",
        save=MagicMock(),
    )


def apply_receipt(call, *, stop_reason=None, provider_reason=None, no_call=False):
    call_data = (
        None
        if no_call
        else {
            "started_at": "2026-09-30T10:00:00Z",
            "ended_at": "2026-09-30T10:01:00Z",
            "duration_ms": 60000,
        }
    )
    if call_data is not None:
        if stop_reason is not None:
            call_data["stop_reason"] = stop_reason
        if provider_reason is not None:
            call_data["target_metrics"] = {"provider_end_reason": provider_reason}
    body = {
        "digest": str((stop_reason, provider_reason, no_call)),
        "status": "skipped" if no_call else "passed",
        "call": call_data,
    }
    hosted_harness_ingestion._apply_receipt_to_call(
        SimpleNamespace(call_execution=call, job=SimpleNamespace(), scenario_key="s"),
        body,
    )
    return body


@pytest.mark.parametrize(
    ("stop_reason", "provider_reason"),
    [
        ("simulator_end_call", "customer-ended-call"),
        ("customer-ended-call", "customer-ended-call"),
        ("target_disconnected", "customer-ended-call"),
        (None, "customer-ended-call"),
    ],
)
def test_provider_end_reason_cannot_replace_harness_stop_reason(
    projected_call, stop_reason, provider_reason
):
    body = apply_receipt(
        projected_call, stop_reason=stop_reason, provider_reason=provider_reason
    )

    assert projected_call.ended_reason == (stop_reason or "")
    assert projected_call.call_metadata["hosted_harness_receipt"] == body
    assert (
        projected_call.call_metadata["hosted_harness_receipt"]["call"][
            "target_metrics"
        ]["provider_end_reason"]
        == provider_reason
    )
    assert "ended_reason" in projected_call.save.call_args.kwargs["update_fields"]


@pytest.mark.parametrize("no_call", [False, True])
def test_replacement_receipt_clears_prior_provider_and_harness_end_reason(
    projected_call, no_call
):
    apply_receipt(
        projected_call,
        stop_reason="simulator_end_call",
        provider_reason="customer-ended-call",
    )
    body = apply_receipt(projected_call, no_call=no_call)

    assert projected_call.ended_reason == ""
    assert projected_call.call_metadata["hosted_harness_receipt"] == body
    receipt_call = projected_call.call_metadata["hosted_harness_receipt"]["call"]
    assert receipt_call is None or "target_metrics" not in receipt_call
