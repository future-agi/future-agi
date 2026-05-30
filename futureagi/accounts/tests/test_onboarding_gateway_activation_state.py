from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.context import OnboardingContext
from accounts.services.onboarding.signal_resolver import collect_onboarding_signals
from accounts.tests.onboarding_model_factories import (
    create_gateway_key,
    create_gateway_provider,
    create_gateway_request_log,
    create_gateway_routing_policy,
)


def _flags(**overrides):
    flags = {
        "onboarding_activation_state_api": True,
        "onboarding_goal_picker": True,
        "onboarding_path_cards": True,
        "onboarding_sample_project": False,
        "onboarding_daily_quality_home": False,
        "onboarding_prompt_path": True,
        "onboarding_prompt_route_modes": True,
        "onboarding_agent_path": True,
        "onboarding_agent_route_modes": True,
        "onboarding_gateway_path": True,
        "onboarding_gateway_route_modes": True,
        "onboarding_lifecycle_email_dry_run": False,
        "onboarding_email_welcome_enabled": False,
        "onboarding_email_first_action_recovery_enabled": False,
        "onboarding_email_first_signal_enabled": False,
        "onboarding_email_next_loop_enabled": False,
        "onboarding_email_sample_bridge_enabled": False,
        "onboarding_email_daily_digest_enabled": False,
        "onboarding_email_prompt_enabled": False,
        "onboarding_email_agent_enabled": False,
        "onboarding_email_agent": False,
        "onboarding_email_gateway_enabled": False,
        "onboarding_email_gateway": False,
        "onboarding_home_enabled": True,
        "onboarding_observe_mvp_enabled": True,
        "onboarding_sample_project_enabled": False,
        "onboarding_lifecycle_dry_run_enabled": False,
        "onboarding_lifecycle_send_enabled": False,
        "daily_quality_home_enabled": False,
        "activation_state_debug_enabled": False,
    }
    flags.update(overrides)
    return flags


def _context(user, organization, workspace):
    return OnboardingContext(
        user=user,
        organization=organization,
        workspace=workspace,
        organization_role="Owner",
        workspace_role="workspace_admin",
        organization_level=15,
        workspace_level=8,
        selected_goal="control_model_traffic",
        primary_path="gateway",
        persona="developer",
        source="test",
        email_context=None,
        permissions={
            "role": "Owner",
            "can_read": True,
            "can_write": True,
            "can_manage_workspace": True,
            "missing_permissions": [],
            "request_access_href": "/dashboard/settings/user-management",
            "permission_limited": False,
        },
        warnings=[],
    )


def _gateway_state(user, organization, workspace, *, flags=None):
    return resolve_activation_state(
        context=_context(user, organization, workspace),
        flags=flags or _flags(),
        signals=collect_onboarding_signals(
            user=user,
            organization=organization,
            workspace=workspace,
        ),
    )


@pytest.mark.django_db
def test_gateway_without_provider_returns_add_provider(organization, workspace, user):
    payload = _gateway_state(user, organization, workspace)

    assert payload["stage"] == "configure_gateway_provider"
    assert payload["recommended_action"]["id"] == "gateway_add_provider"
    assert payload["recommended_action"]["href"] == (
        "/dashboard/gateway/providers?source=onboarding"
    )
    assert payload["gateway"]["has_provider"] is False


@pytest.mark.django_db
def test_gateway_provider_without_key_returns_create_key(
    organization,
    workspace,
    user,
):
    provider = create_gateway_provider(
        organization=organization,
        workspace=workspace,
    )

    payload = _gateway_state(user, organization, workspace)

    assert payload["stage"] == "create_gateway_key"
    assert payload["recommended_action"]["id"] == "gateway_create_key"
    assert payload["gateway"]["provider_credential_id"] == str(provider.id)
    assert payload["gateway"]["has_provider"] is True


@pytest.mark.django_db
def test_gateway_key_without_real_request_returns_send_request(
    organization,
    workspace,
    user,
):
    create_gateway_provider(organization=organization, workspace=workspace)
    key = create_gateway_key(organization=organization, workspace=workspace, user=user)

    payload = _gateway_state(user, organization, workspace)

    assert payload["stage"] == "run_gateway_request"
    assert payload["recommended_action"]["id"] == "gateway_send_first_request"
    assert payload["gateway"]["gateway_key_id"] == key.gateway_key_id
    assert payload["gateway"]["has_request"] is False


@pytest.mark.django_db
def test_gateway_real_request_returns_review_log(organization, workspace, user):
    create_gateway_provider(organization=organization, workspace=workspace)
    key = create_gateway_key(organization=organization, workspace=workspace, user=user)
    request_log = create_gateway_request_log(
        organization=organization,
        workspace=workspace,
        gateway_key=key,
    )

    payload = _gateway_state(user, organization, workspace)

    assert payload["stage"] == "review_gateway_log"
    assert payload["recommended_action"]["id"] == "gateway_review_request"
    assert request_log.request_id in payload["recommended_action"]["href"]
    assert payload["gateway"]["has_request"] is True


@pytest.mark.django_db
def test_gateway_failed_request_routes_to_failure_review(
    organization,
    workspace,
    user,
):
    create_gateway_provider(organization=organization, workspace=workspace)
    key = create_gateway_key(organization=organization, workspace=workspace, user=user)
    create_gateway_request_log(
        organization=organization,
        workspace=workspace,
        gateway_key=key,
        status_code=500,
        is_error=True,
        fallback_used=True,
    )

    payload = _gateway_state(user, organization, workspace)

    assert payload["stage"] == "fix_gateway_failure"
    assert payload["recommended_action"]["id"] == "gateway_fix_failed_request"
    assert "onboarding=fix-failure" in payload["recommended_action"]["href"]
    assert payload["gateway"]["request_is_error"] is True


@pytest.mark.django_db
def test_gateway_reviewed_request_without_policy_returns_add_policy(
    organization,
    workspace,
    user,
):
    create_gateway_provider(organization=organization, workspace=workspace)
    key = create_gateway_key(organization=organization, workspace=workspace, user=user)
    request_log = create_gateway_request_log(
        organization=organization,
        workspace=workspace,
        gateway_key=key,
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="gateway_request_reviewed",
        source="test",
        product_path="gateway",
        activation_stage="review_gateway_log",
        metadata={
            "request_log_id": str(request_log.id),
            "request_id": request_log.request_id,
        },
    )

    payload = _gateway_state(user, organization, workspace)

    assert payload["stage"] == "add_gateway_policy"
    assert payload["recommended_action"]["id"] == "gateway_add_policy"
    assert payload["gateway"]["has_review"] is True
    assert payload["gateway"]["has_policy"] is False


@pytest.mark.django_db
def test_gateway_guardrail_review_routes_to_guardrail_policy(
    organization,
    workspace,
    user,
):
    create_gateway_provider(organization=organization, workspace=workspace)
    key = create_gateway_key(organization=organization, workspace=workspace, user=user)
    request_log = create_gateway_request_log(
        organization=organization,
        workspace=workspace,
        gateway_key=key,
        guardrail_triggered=True,
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="gateway_request_reviewed",
        source="test",
        product_path="gateway",
        activation_stage="review_gateway_log",
        metadata={
            "request_log_id": str(request_log.id),
            "request_id": request_log.request_id,
        },
    )

    payload = _gateway_state(user, organization, workspace)

    assert payload["stage"] == "add_gateway_policy"
    assert payload["recommended_action"]["href"].startswith(
        "/dashboard/gateway/guardrails/configuration?"
    )


@pytest.mark.django_db
def test_gateway_policy_after_review_activates(organization, workspace, user):
    create_gateway_provider(organization=organization, workspace=workspace)
    key = create_gateway_key(organization=organization, workspace=workspace, user=user)
    request_log = create_gateway_request_log(
        organization=organization,
        workspace=workspace,
        gateway_key=key,
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="gateway_log_opened",
        source="test",
        product_path="gateway",
        activation_stage="review_gateway_log",
        metadata={
            "request_log_id": str(request_log.id),
            "request_id": request_log.request_id,
        },
    )
    create_gateway_routing_policy(organization=organization, user=user)

    payload = _gateway_state(user, organization, workspace)

    assert payload["stage"] == "activated"
    assert payload["is_activated"] is True
    assert payload["recommended_action"]["id"] == "open_gateway_logs"
    assert payload["gateway"]["has_policy"] is True


@pytest.mark.django_db
def test_sample_gateway_request_does_not_count_as_real_activation(
    organization,
    workspace,
    user,
):
    create_gateway_provider(organization=organization, workspace=workspace)
    key = create_gateway_key(organization=organization, workspace=workspace, user=user)
    create_gateway_request_log(
        organization=organization,
        workspace=workspace,
        gateway_key=key,
        metadata={"is_sample": True},
        started_at=timezone.now() - timedelta(minutes=5),
    )
    create_gateway_request_log(
        organization=organization,
        workspace=workspace,
        gateway_key=key,
        metadata={"sample": True},
    )

    payload = _gateway_state(user, organization, workspace)

    assert payload["stage"] == "run_gateway_request"
    assert payload["is_activated"] is False
    assert payload["gateway"]["has_request"] is False
    assert payload["gateway"]["is_sample"] is True


@pytest.mark.django_db
def test_gateway_path_flag_off_returns_selected_path_unavailable(
    organization,
    workspace,
    user,
):
    payload = _gateway_state(
        user,
        organization,
        workspace,
        flags=_flags(onboarding_gateway_path=False),
    )

    assert payload["stage"] == "selected_path_unavailable"
    assert payload["recommended_action"]["id"] == "choose_available_path"
