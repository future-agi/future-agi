import io
import json
from uuid import uuid4

from django.core.management import call_command

from accounts.services.onboarding.support_state import (
    mask_email,
    mask_text,
    summarize_activation_state,
    support_readiness_checks,
)


def test_mask_email_keeps_address_unusable():
    masked = mask_email("nikhilpareekiitr@gmail.com")

    assert masked == "n***r@g***.com"
    assert "nikhilpareekiitr" not in masked
    assert "gmail.com" not in masked


def test_mask_text_keeps_only_outer_characters():
    assert mask_text("Future AGI") == "F***I"
    assert mask_text("AB") == "**"
    assert mask_text("") is None


def test_summarize_activation_state_keeps_support_fields_without_raw_payload():
    summary = summarize_activation_state(
        {
            "schema_version": "activation-state.v1",
            "request_id": "req_1",
            "server_time": "2026-05-31T00:00:00Z",
            "organization_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "user_id": str(uuid4()),
            "goal": "observe_quality",
            "persona": "engineer",
            "primary_path": "observe",
            "stage": "waiting_for_first_trace",
            "home_mode": "first_run",
            "is_activated": False,
            "recommended_action": {
                "id": "send_first_trace",
                "label": "Send first trace",
                "href": "/dashboard/observe?source=onboarding",
                "event_name": "trace_received",
            },
            "fallback_action": {
                "id": "open_get_started",
                "href": "/dashboard/get-started",
            },
            "sample_project": {
                "available": True,
                "created": False,
                "status": "not_created",
                "href": "/dashboard/home?sample=true",
                "artifact_refs": {"trace_id": "trace-private"},
                "health": {"route_ready": True},
            },
            "permissions": {"can_write": True},
            "email_eligibility": {
                "eligible": True,
                "suppressed": False,
                "next_email_key": "observe_waiting_for_first_trace_v1",
            },
            "lifecycle": {
                "status": "eligible",
                "campaign_key": "observe_waiting_for_first_trace",
                "template_key": "observe_waiting_for_first_trace_v1",
                "target_success_event": "trace_received",
            },
            "route_availability": {
                "send_first_trace": {
                    "is_available": True,
                    "href": "/dashboard/observe?source=onboarding",
                },
                "blocked_route": {
                    "is_available": False,
                    "reason": "missing_permission",
                },
            },
            "signals": {"trace_private": "not included"},
            "warnings": [],
        }
    )

    assert summary["status"] == "resolved"
    assert summary["stage"] == "waiting_for_first_trace"
    assert summary["recommended_action"]["id"] == "send_first_trace"
    assert summary["current_resolved_recommended_route"] == (
        "/dashboard/observe?source=onboarding"
    )
    assert summary["sample_project"]["artifact_ref_keys"] == ["trace_id"]
    assert "raw_activation_state" not in summary
    assert "signals" not in summary


def test_support_readiness_checks_require_the_beta_triage_fields():
    checks = support_readiness_checks(
        flags={"onboarding_activation_state_api": True},
        activation_summary={
            "status": "resolved",
            "stage": "waiting_for_first_trace",
            "recommended_action": {
                "id": "send_first_trace",
                "href": "/dashboard/observe?source=onboarding",
            },
            "sample_project": {"status": "not_created"},
            "lifecycle": {"status": "eligible"},
            "current_resolved_recommended_route": (
                "/dashboard/observe?source=onboarding"
            ),
        },
        latest_sample=None,
        latest_lifecycle_evaluation=None,
        latest_lifecycle_send=None,
        latest_notification_delivery=None,
    )

    assert checks == {
        "ready": True,
        "checks": {
            "flag_state": True,
            "activation_stage": True,
            "recommendation": True,
            "sample_state": True,
            "delivery_log_context": True,
        },
        "missing": [],
    }


def test_support_readiness_checks_report_missing_fields():
    checks = support_readiness_checks(
        flags={},
        activation_summary={
            "status": "error",
            "recommended_action": None,
            "sample_project": None,
        },
        latest_sample=None,
        latest_lifecycle_evaluation=None,
        latest_lifecycle_send=None,
        latest_notification_delivery=None,
    )

    assert checks["ready"] is False
    assert checks["missing"] == [
        "activation_stage",
        "delivery_log_context",
        "flag_state",
        "recommendation",
        "sample_state",
    ]


def test_inspect_onboarding_support_state_command_renders_mocked_json(monkeypatch):
    from accounts.management.commands import inspect_onboarding_support_state as command

    payload = {
        "schema_version": "onboarding-support-state-2026-05-31.v1",
        "source": "support_state_inspection",
        "identity": {
            "user": {"id": str(uuid4()), "email_masked": "n***r@g***.com"},
            "workspace": {"id": str(uuid4())},
        },
        "activation_state": {"stage": "waiting_for_first_trace"},
        "support_readiness": {"ready": True, "missing": []},
    }
    called = {}

    def fake_build(**kwargs):
        called.update(kwargs)
        return payload

    monkeypatch.setattr(command, "build_onboarding_support_state", fake_build)

    stdout = io.StringIO()
    call_command(
        "inspect_onboarding_support_state",
        workspace_id=str(uuid4()),
        user_email="nikhilpareekiitr@gmail.com",
        pretty=True,
        stdout=stdout,
    )

    result = json.loads(stdout.getvalue())
    assert result == payload
    assert called["user_email"] == "nikhilpareekiitr@gmail.com"
    assert called["include_raw_activation_state"] is False
