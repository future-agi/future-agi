import json
from decimal import Decimal

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils import timezone

from accounts.models import OnboardingPaidCloudActivationExportLog
from accounts.services.onboarding.activation_export_delivery import (
    activation_export_delivery_config,
    run_onboarding_activation_export_delivery,
)
from accounts.services.onboarding.activation_export_registry import (
    activation_export_paid_plan_values,
    get_activation_export_config,
    matching_activation_export_cohorts,
)
from accounts.services.onboarding.activation_exporter import (
    assert_activation_export_payload_safe,
    export_activation_fact,
)
from accounts.services.onboarding.activation_plane import (
    ActivationExportDecision,
    activation_export_decision,
)
from ee.usage.models.usage import (
    OrganizationStatusChoices,
    OrganizationSubscription,
    PlanChoices,
    SubscriptionTier,
    SubscriptionTierChoices,
)


def _activation_state(now=None):
    now = now or timezone.now()
    return {
        "schema_version": "activation-state-test.v1",
        "goal": "monitor_production_ai_app",
        "persona": "developer",
        "primary_path": "observe",
        "stage": "waiting_for_first_trace",
        "home_mode": "setup",
        "is_activated": False,
        "activated_at": None,
        "progress": {"completed": 1, "total": 4},
        "recommended_action": {
            "id": "send_first_trace",
            "kind": "primary",
            "label": "Send first trace",
            "href": "/dashboard/observe/project-1/llm-tracing",
            "completion_event": "first_trace_received",
            "is_sample": False,
            "token": "unsafe",
        },
        "fallback_action": {
            "id": "open_observe_dashboard_fallback",
            "kind": "secondary",
            "href": "/dashboard/observe/project-1",
        },
        "permissions": {
            "can_read": True,
            "can_write": True,
            "can_manage_workspace": True,
            "permission_limited": False,
        },
        "signals": {
            "provider_keys": 1,
            "observe_projects": 1,
            "traces": 0,
            "first_trace_id": "trace-1",
            "first_observe_id": "project-1",
            "gateway_key_prefix": "secret-prefix",
            "sample_trace_available": True,
        },
        "route_availability": {
            "path_observe": {
                "is_available": True,
                "reason": None,
                "href": "/dashboard/observe",
            }
        },
        "lifecycle": {
            "next_campaign_key": "first_trace_recovery",
            "campaign_group": "observe",
            "template_key": "observe_first_trace",
            "template_version": "v1",
            "status": "eligible",
            "suppression_reason": None,
        },
        "email_eligibility": {
            "eligible": True,
            "suppressed": False,
            "suppression_reason": None,
            "next_email_key": "observe_first_trace",
            "digest_eligible": False,
            "frequency_cap_remaining": 1,
            "dry_run_only": True,
        },
        "warnings": [],
        "last_meaningful_event": {
            "name": "onboarding_home_viewed",
            "occurred_at": now,
            "is_sample": False,
            "path": "observe",
            "metadata": {"prompt": "unsafe"},
        },
    }


class _DeliveryResponse:
    status_code = 202

    def raise_for_status(self):
        return None


@pytest.fixture
def paid_decision():
    return ActivationExportDecision(
        allowed=True,
        deployment_mode="cloud",
        deployment_region="us",
        plan_tier=PlanChoices.PAYG.value,
        subscription_status=OrganizationStatusChoices.ACTIVE.value,
    )


@pytest.mark.django_db
def test_dry_run_does_not_write_log(
    monkeypatch, organization, workspace, user, paid_decision
):
    monkeypatch.setattr(
        "accounts.services.onboarding.activation_exporter.activation_export_decision",
        lambda organization: paid_decision,
    )

    result = export_activation_fact(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=_activation_state(),
        write=False,
    )

    assert result.status == OnboardingPaidCloudActivationExportLog.STATUS_READY
    assert result.written is False
    assert OnboardingPaidCloudActivationExportLog.no_workspace_objects.count() == 0


@pytest.mark.django_db
def test_paid_cloud_export_write_is_idempotent_and_sanitized(
    monkeypatch,
    organization,
    workspace,
    user,
    paid_decision,
):
    monkeypatch.setattr(
        "accounts.services.onboarding.activation_exporter.activation_export_decision",
        lambda organization: paid_decision,
    )
    state = {
        **_activation_state(),
        "stage": "waiting_for_first_trace_sample_available",
    }

    first = export_activation_fact(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=state,
        source="test_export",
        write=True,
    )
    second = export_activation_fact(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=state,
        source="test_export",
        write=True,
    )

    assert first.idempotency_key == second.idempotency_key
    assert OnboardingPaidCloudActivationExportLog.no_workspace_objects.count() == 1

    log = OnboardingPaidCloudActivationExportLog.no_workspace_objects.get()
    assert log.status == OnboardingPaidCloudActivationExportLog.STATUS_READY
    assert log.deployment_mode == "cloud"
    assert log.region == "us"
    assert log.plan_tier == PlanChoices.PAYG.value
    assert log.metadata == {"source": "test_export"}

    payload = log.fact_payload
    assert payload["activation"]["recommended_action"] == {
        "completion_event": "first_trace_received",
        "id": "send_first_trace",
        "is_sample": False,
        "kind": "primary",
        "label": "Send first trace",
    }
    assert payload["signals"] == {
        "observe_projects": 1,
        "provider_keys": 1,
        "sample_trace_available": True,
        "traces": 0,
    }
    assert "sample_reviewed_no_real_trace" in {
        cohort["cohort_key"] for cohort in payload["journey"]["cohorts"]
    }
    assert "href" not in payload["route_availability"]["path_observe"]
    assert "last_meaningful_event" not in payload


@pytest.mark.django_db
def test_suppressed_export_write_records_reason(
    monkeypatch, organization, workspace, user
):
    decision = ActivationExportDecision(
        allowed=False,
        deployment_mode="cloud",
        deployment_region="us",
        plan_tier=PlanChoices.FREE.value,
        subscription_status=OrganizationStatusChoices.ACTIVE.value,
        suppression_reason="subscription_not_paid",
    )
    monkeypatch.setattr(
        "accounts.services.onboarding.activation_exporter.activation_export_decision",
        lambda organization: decision,
    )

    result = export_activation_fact(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=_activation_state(),
        write=True,
    )

    assert result.status == OnboardingPaidCloudActivationExportLog.STATUS_SUPPRESSED
    log = OnboardingPaidCloudActivationExportLog.no_workspace_objects.get()
    assert log.suppression_reason == "subscription_not_paid"
    assert log.fact_payload["suppressed"] == {"reason": "subscription_not_paid"}


def test_export_payload_rejects_sensitive_keys():
    with pytest.raises(ValidationError):
        assert_activation_export_payload_safe({"token": "unsafe"})


def test_activation_export_config_drives_paid_plans_and_cohorts():
    config = get_activation_export_config()

    assert config["schema_version"] == (
        "onboarding-activation-export-config-2026-05-30.v1"
    )
    assert PlanChoices.PAYG.value in activation_export_paid_plan_values()

    state = {
        **_activation_state(),
        "primary_path": "sample",
        "stage": "connect_real_data",
    }
    cohorts = matching_activation_export_cohorts(state)

    assert [cohort["cohort_key"] for cohort in cohorts] == [
        "sample_reviewed_no_real_trace"
    ]


@pytest.mark.django_db
def test_delivery_dry_run_does_not_send_or_update(
    monkeypatch,
    organization,
    workspace,
    user,
    paid_decision,
):
    monkeypatch.setattr(
        "accounts.services.onboarding.activation_exporter.activation_export_decision",
        lambda organization: paid_decision,
    )
    export_activation_fact(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=_activation_state(),
        write=True,
    )

    post_calls = []
    monkeypatch.setattr(
        "accounts.services.onboarding.activation_export_delivery.requests.post",
        lambda *args, **kwargs: post_calls.append((args, kwargs)),
    )

    result = run_onboarding_activation_export_delivery(dry_run=True)

    assert result.evaluated == 1
    assert result.delivered == 0
    assert result.status_counts == {"dry_run": 1}
    assert post_calls == []
    log = OnboardingPaidCloudActivationExportLog.no_workspace_objects.get()
    assert log.status == OnboardingPaidCloudActivationExportLog.STATUS_READY


@pytest.mark.django_db
def test_delivery_sends_ready_rows_with_signed_payload(
    monkeypatch,
    organization,
    workspace,
    user,
    paid_decision,
):
    monkeypatch.setattr(
        "accounts.services.onboarding.activation_exporter.activation_export_decision",
        lambda organization: paid_decision,
    )
    export_activation_fact(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=_activation_state(),
        write=True,
    )
    post_calls = []

    def _post(url, *, data, headers, timeout):
        post_calls.append(
            {
                "url": url,
                "data": json.loads(data.decode("utf-8")),
                "headers": headers,
                "timeout": timeout,
            }
        )
        return _DeliveryResponse()

    monkeypatch.setattr(
        "accounts.services.onboarding.activation_export_delivery.requests.post",
        _post,
    )

    result = run_onboarding_activation_export_delivery(
        dry_run=False,
        endpoint_url="https://activation.example.test/exports",
        shared_secret="test-secret",
        timeout_seconds=3,
    )

    assert result.delivered == 1
    assert result.failed == 0
    assert len(post_calls) == 1
    sent = post_calls[0]
    assert sent["url"] == "https://activation.example.test/exports"
    assert sent["timeout"] == 3
    assert sent["data"]["type"] == "onboarding_activation_fact"
    assert sent["data"]["fact"]["journey"]["cohorts"]
    assert sent["headers"]["x-futureagi-activation-export-signature"].startswith(
        "sha256="
    )
    assert (
        sent["headers"]["x-futureagi-activation-export-key"]
        == sent["data"]["idempotency_key"]
    )

    log = OnboardingPaidCloudActivationExportLog.no_workspace_objects.get()
    assert log.status == OnboardingPaidCloudActivationExportLog.STATUS_EXPORTED
    assert log.exported_at is not None
    assert log.metadata["delivery"]["status"] == "exported"


@pytest.mark.django_db
def test_delivery_failed_rows_require_retry_flag(
    monkeypatch,
    organization,
    workspace,
    user,
    paid_decision,
):
    monkeypatch.setattr(
        "accounts.services.onboarding.activation_exporter.activation_export_decision",
        lambda organization: paid_decision,
    )
    export_activation_fact(
        user=user,
        organization=organization,
        workspace=workspace,
        activation_state=_activation_state(),
        write=True,
    )

    def _failing_post(*args, **kwargs):
        raise RuntimeError("delivery unavailable")

    monkeypatch.setattr(
        "accounts.services.onboarding.activation_export_delivery.requests.post",
        _failing_post,
    )
    failed = run_onboarding_activation_export_delivery(
        dry_run=False,
        endpoint_url="https://activation.example.test/exports",
        shared_secret="test-secret",
    )
    assert failed.failed == 1

    skipped = run_onboarding_activation_export_delivery(
        dry_run=False,
        endpoint_url="https://activation.example.test/exports",
        shared_secret="test-secret",
    )
    assert skipped.evaluated == 0

    monkeypatch.setattr(
        "accounts.services.onboarding.activation_export_delivery.requests.post",
        lambda *args, **kwargs: _DeliveryResponse(),
    )
    retried = run_onboarding_activation_export_delivery(
        dry_run=False,
        retry_failed=True,
        endpoint_url="https://activation.example.test/exports",
        shared_secret="test-secret",
    )

    assert retried.delivered == 1
    log = OnboardingPaidCloudActivationExportLog.no_workspace_objects.get()
    assert log.status == OnboardingPaidCloudActivationExportLog.STATUS_EXPORTED


def test_delivery_config_requires_https_and_secret():
    with pytest.raises(ImproperlyConfigured):
        activation_export_delivery_config(
            endpoint_url="http://activation.example.test/exports",
            shared_secret="test-secret",
        )
    with pytest.raises(ImproperlyConfigured):
        activation_export_delivery_config(
            endpoint_url="https://activation.example.test/exports",
            shared_secret="",
        )


@pytest.mark.django_db
def test_activation_plane_allows_only_active_paid_cloud_plan(
    monkeypatch,
    settings,
    organization,
):
    monkeypatch.setattr(
        "accounts.services.onboarding.activation_plane._deployment_mode",
        lambda: "cloud",
    )
    settings.CLOUD_DEPLOYMENT = "US"
    tier, _created = SubscriptionTier.no_workspace_objects.get_or_create(
        name=SubscriptionTierChoices.BUSINESS.value,
        description="Business tier",
    )

    OrganizationSubscription.no_workspace_objects.create(
        organization=organization,
        subscription_tier=tier,
        status=OrganizationStatusChoices.ACTIVE.value,
        plan=PlanChoices.FREE.value,
        wallet_balance=Decimal("0"),
    )

    free_decision = activation_export_decision(organization)
    assert free_decision.allowed is False
    assert free_decision.suppression_reason == "subscription_not_paid"

    OrganizationSubscription.no_workspace_objects.filter(
        organization=organization
    ).update(plan=PlanChoices.PAYG.value)

    paid_decision = activation_export_decision(organization)
    assert paid_decision.allowed is True
    assert paid_decision.plan_tier == PlanChoices.PAYG.value
