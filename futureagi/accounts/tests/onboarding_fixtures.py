from copy import deepcopy

from accounts.services.onboarding.constants import ACTIVATION_SCHEMA_VERSION


def activation_action(**overrides):
    action = {
        "id": "create_observe_project",
        "kind": "setup",
        "title": "Connect observability",
        "description": "Create an observability project and send one request.",
        "href": "/dashboard/observe?setup=true&source=onboarding",
        "cta_label": "Connect observability",
        "estimated_minutes": 5,
        "priority": 100,
        "blocked": False,
        "blocked_reason": None,
        "requires_permission": "observe:write",
        "completion_event": "observe_project_created",
        "is_sample": False,
        "route_available": True,
        "fallback_href": "/dashboard/get-started",
        "analytics": {
            "event_name": "onboarding_recommended_action_clicked",
            "source": "home",
            "target_path": "observe",
        },
    }
    action.update(overrides)
    return action


def fallback_action(**overrides):
    action = activation_action(
        id="open_get_started",
        kind="fallback",
        title="Open Get Started",
        description="Use the existing setup checklist.",
        href="/dashboard/get-started",
        cta_label="Open Get Started",
        estimated_minutes=None,
        priority=10,
        requires_permission=None,
        completion_event=None,
        route_available=True,
        analytics={
            "event_name": "onboarding_recommended_action_clicked",
            "source": "fallback",
            "target_path": None,
        },
    )
    action.update(overrides)
    return action


def activation_state_payload(**overrides):
    payload = {
        "schema_version": ACTIVATION_SCHEMA_VERSION,
        "request_id": "req_onboarding_contract",
        "server_time": "2026-05-26T15:00:00Z",
        "workspace_id": "wrk_contract",
        "organization_id": "org_contract",
        "user_id": "usr_contract",
        "goal": "monitor_production_ai_app",
        "persona": "developer",
        "primary_path": "observe",
        "stage": "connect_observability",
        "home_mode": "first_run",
        "is_activated": False,
        "activated_at": None,
        "recommended_action": activation_action(),
        "fallback_action": fallback_action(),
        "progress": {
            "build": "selected",
            "test": "available",
            "observe": "not_started",
            "ship": "available",
            "improve": "available",
        },
        "signals": {
            "provider_keys": 1,
            "datasets": 0,
            "evals": 0,
            "eval_runs": 0,
            "prompt_templates": 0,
            "prompt_versions": 0,
            "prompt_comparisons": 0,
            "agents": 0,
            "agent_prototype_runs": 0,
            "observe_projects": 0,
            "traces": 0,
            "trace_reviews": 0,
            "gateway_keys": 0,
            "gateway_requests": 0,
            "gateway_policies": 0,
            "voice_agents": 0,
            "voice_simulations": 0,
            "voice_calls": 0,
            "voice_reviews": 0,
            "team_invites": 0,
            "dashboards": 0,
            "alerts": 0,
            "first_trace_id": None,
            "first_observe_id": None,
        },
        "available_paths": [
            {
                "id": "observe",
                "label": "Monitor a production AI app",
                "description": "Connect traces and inspect quality signals.",
                "status": "selected",
                "href": "/dashboard/home?path=observe",
                "is_available": True,
                "blocked_reason": None,
                "requires_permission": "observe:write",
                "first_action_id": "create_observe_project",
            },
            {
                "id": "sample",
                "label": "Explore with sample data",
                "description": "Use a sample workspace while real data is pending.",
                "status": "available",
                "href": "/dashboard/home?path=sample",
                "is_available": True,
                "blocked_reason": None,
                "requires_permission": None,
                "first_action_id": "open_sample_project",
            },
        ],
        "sample_project": {
            "available": True,
            "created": False,
            "status": "available",
            "href": "/dashboard/home?sample=true",
            "version": "sample-observe-v1",
            "is_hidden": False,
            "hidden_reason": None,
            "entry_routes": [],
            "missing_artifacts": [],
            "last_opened_at": None,
        },
        "email_eligibility": {
            "eligible": True,
            "suppressed": False,
            "suppression_reason": None,
            "next_email_key": "observe_connect_first",
            "next_email_after": "2026-05-26T15:00:00Z",
            "digest_eligible": False,
            "last_email_sent_at": None,
            "frequency_cap_remaining": 2,
            "dry_run_only": True,
        },
        "permissions": {
            "role": "admin",
            "can_read": True,
            "can_write": True,
            "can_manage_workspace": True,
            "missing_permissions": [],
            "request_access_href": "/dashboard/settings/user-management",
            "permission_limited": False,
        },
        "feature_flags": {
            "onboarding_home_enabled": True,
            "onboarding_observe_mvp_enabled": True,
            "onboarding_sample_project_enabled": True,
            "onboarding_lifecycle_dry_run_enabled": True,
            "onboarding_lifecycle_send_enabled": False,
            "daily_quality_home_enabled": False,
            "activation_state_debug_enabled": False,
        },
        "route_availability": {
            "home": {
                "href": "/dashboard/home",
                "is_available": True,
                "reason": None,
            },
            "observe_setup": {
                "href": "/dashboard/observe?setup=true&source=onboarding",
                "is_available": True,
                "reason": None,
            },
            "get_started": {
                "href": "/dashboard/get-started",
                "is_available": True,
                "reason": None,
            },
        },
        "email_context": None,
        "last_meaningful_event": None,
        "diagnostics": None,
        "warnings": [],
    }
    payload.update(overrides)
    return deepcopy(payload)
