from copy import deepcopy

from accounts.serializers.onboarding import (
    ActivationGoalRequestSerializer,
    ActivationStateResponseSerializer,
    SampleProjectRequestSerializer,
    SampleProjectStateSerializer,
)
from accounts.services.onboarding.constants import (
    ACTIVATION_STAGES,
    ONBOARDING_GOALS,
    PRODUCT_PATHS,
    canonical_goal,
    canonical_path,
)
from accounts.tests.onboarding_fixtures import (
    activation_action,
    activation_state_payload,
)


def test_onboarding_constants_include_release_0_contract_values():
    assert "monitor_production_ai_app" in ONBOARDING_GOALS
    assert "connect_voice_ai_agent" in ONBOARDING_GOALS
    assert "gateway" in PRODUCT_PATHS
    assert "voice" in PRODUCT_PATHS
    assert "review_first_trace" in ACTIVATION_STAGES
    assert "run_gateway_request" in ACTIVATION_STAGES
    assert "voice_monitor_calls" in ACTIVATION_STAGES
    assert canonical_goal("test_and_improve_prompts") == "improve_prompts"
    assert canonical_path("evaluations") == "evals"


def test_activation_state_response_serializer_accepts_observe_first_run_fixture():
    serializer = ActivationStateResponseSerializer(data=activation_state_payload())

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["stage"] == "connect_observability"
    assert serializer.validated_data["recommended_action"]["id"] == (
        "create_observe_project"
    )


def test_activation_state_response_serializer_accepts_open_daily_action():
    payload = activation_state_payload(
        stage="daily_review",
        home_mode="daily_quality",
        is_activated=True,
        activated_at="2026-05-27T08:00:00Z",
        recommended_action=activation_action(
            id="continue_quality_action",
            kind="daily_quality",
            title="Continue quality action",
            description="Return to the unresolved action from the last review.",
            href="/dashboard/observe/observe-1",
            cta_label="Continue action",
            requires_permission=None,
            completion_event="daily_quality_action_completed",
        ),
        last_meaningful_event={
            "name": "first_quality_loop_completed",
            "occurred_at": "2026-05-27T08:00:00Z",
            "is_sample": False,
            "path": "observe",
            "metadata": {},
        },
    )
    payload["route_availability"]["observe_project"] = {
        "href": "/dashboard/observe/observe-1",
        "is_available": True,
        "reason": None,
    }
    payload["email_eligibility"].update(
        {
            "eligible": True,
            "suppressed": False,
            "suppression_reason": None,
            "next_email_key": "daily_quality_open_actions_v1",
            "next_email_after": "2026-05-27T09:00:00Z",
            "digest_eligible": True,
        }
    )
    payload["daily_quality"] = {
        "mode": "open_action",
        "last_reviewed_at": "2026-05-27T08:00:00Z",
        "window": {
            "start_at": "2026-05-27T08:00:00Z",
            "end_at": "2026-05-27T10:00:00Z",
        },
        "top_signal": None,
        "primary_action": {
            "id": "continue_quality_action",
            "label": "Continue action",
            "body": "Return to the unresolved action from the last review.",
            "route": "/dashboard/observe/observe-1",
            "fallback_route": "/dashboard/get-started",
            "route_available": True,
            "source_type": "project",
            "source_id": "observe-1",
            "success_event": "daily_quality_action_completed",
            "is_primary": True,
            "is_sample": False,
            "requires_permission": None,
            "activation_kind": "daily_quality",
        },
        "action_cards": [],
        "product_cards": [
            {
                "path": "observe",
                "status": "needs_review",
                "label": "Observe",
                "summary": "1 unresolved action",
                "metric": "1",
                "change": "Open from last review",
                "route": "/dashboard/observe/observe-1",
            }
        ],
        "weekly_review": {
            "due": True,
            "status": "due",
            "route": "/dashboard/home?mode=weekly-review",
            "window": {
                "start_at": "2026-05-20T10:00:00Z",
                "end_at": "2026-05-27T10:00:00Z",
            },
            "summary": "Review unresolved quality work with your team.",
            "unresolved_count": 1,
            "completed_count": 0,
            "last_completed_at": None,
            "action_label": "Open weekly review",
        },
        "digest_eligible": True,
        "digest_suppression_reason": None,
        "diagnostics": ["open_action"],
    }

    serializer = ActivationStateResponseSerializer(data=payload)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["daily_quality"]["mode"] == "open_action"


def test_activation_state_response_rejects_unknown_stage():
    payload = activation_state_payload(stage="unknown_stage")
    serializer = ActivationStateResponseSerializer(data=payload)

    assert not serializer.is_valid()
    assert "stage" in serializer.errors


def test_activation_state_response_requires_all_progress_keys():
    payload = activation_state_payload()
    del payload["progress"]["observe"]
    serializer = ActivationStateResponseSerializer(data=payload)

    assert not serializer.is_valid()
    assert "observe" in serializer.errors["progress"]


def test_activation_state_response_rejects_unavailable_action_route():
    payload = activation_state_payload()
    payload["route_availability"].pop("observe_setup")
    serializer = ActivationStateResponseSerializer(data=payload)

    assert not serializer.is_valid()
    assert "Recommended action href must appear" in str(serializer.errors)


def test_activation_state_response_rejects_sample_only_activation():
    payload = activation_state_payload(
        is_activated=True,
        activated_at="2026-05-26T15:05:00Z",
        last_meaningful_event={
            "name": "sample_trace_reviewed",
            "occurred_at": "2026-05-26T15:04:00Z",
            "is_sample": True,
            "path": "sample",
        },
    )
    serializer = ActivationStateResponseSerializer(data=payload)

    assert not serializer.is_valid()
    assert "Sample-only events cannot activate" in str(serializer.errors)


def test_activation_state_response_rejects_write_cta_for_read_only_user():
    payload = activation_state_payload()
    payload["permissions"]["can_write"] = False
    payload["permissions"]["permission_limited"] = True
    payload["permissions"]["missing_permissions"] = ["observe:write"]
    serializer = ActivationStateResponseSerializer(data=payload)

    assert not serializer.is_valid()
    assert "write-only CTAs" in str(serializer.errors)


def test_action_serializer_rejects_sample_action_with_real_completion_event():
    payload = activation_state_payload(
        recommended_action=activation_action(
            id="open_sample_project",
            kind="sample_project",
            href="/dashboard/home?sample=true",
            completion_event="trace_reviewed",
            is_sample=True,
        )
    )
    payload["route_availability"]["sample"] = {
        "href": "/dashboard/home?sample=true",
        "is_available": True,
        "reason": None,
    }
    serializer = ActivationStateResponseSerializer(data=payload)

    assert not serializer.is_valid()
    assert "Sample actions cannot use real activation" in str(serializer.errors)


def test_sample_project_state_requires_ready_entry_routes():
    sample = deepcopy(activation_state_payload()["sample_project"])
    sample.update({"created": True, "status": "ready"})
    serializer = SampleProjectStateSerializer(data=sample)

    assert not serializer.is_valid()
    assert "Ready samples must list entry_routes" in str(serializer.errors)


def test_goal_request_accepts_alias_and_emits_canonical_goal():
    serializer = ActivationGoalRequestSerializer(
        data={
            "goal": "route_llm_traffic_safely",
            "persona": "platform_engineer",
            "source": "goal_picker",
            "reason": "first_selection",
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["goal"] == "control_model_traffic"


def test_goal_request_rejects_unknown_goal():
    serializer = ActivationGoalRequestSerializer(data={"goal": "ship_magic"})

    assert not serializer.is_valid()
    assert "goal" in serializer.errors


def test_sample_project_request_accepts_path_alias():
    serializer = SampleProjectRequestSerializer(
        data={
            "path": "observability",
            "manifest_id": "futureagi_sample_ai_support_workspace",
            "manifest_version": "2026-05-26.1",
            "source": "onboarding_home",
            "reason": "waiting_for_first_trace",
            "open_after_create": True,
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["path"] == "observe"
