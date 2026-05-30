import copy
import json
import uuid

import pytest
from django.core.serializers.json import DjangoJSONEncoder
from django.test import override_settings
from django.utils import timezone
from rest_framework import status

from accounts.models import (
    OnboardingActivationFactReceipt,
    OnboardingActivationFactReceiptRejection,
)
from accounts.services.onboarding.activation_exporter import (
    ACTIVATION_EXPORT_SCHEMA_VERSION,
)
from accounts.services.onboarding.activation_fact_receipts import (
    RECEIPT_TYPE,
    activation_fact_signature,
)

SECRET = "receipt-secret"
PATH = "/accounts/onboarding/activation-facts/"


def _body(payload):
    return json.dumps(
        payload,
        cls=DjangoJSONEncoder,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _headers(payload, body, signature=None):
    return {
        "HTTP_X_FUTUREAGI_ACTIVATION_EXPORT_ID": str(payload["export_log_id"]),
        "HTTP_X_FUTUREAGI_ACTIVATION_EXPORT_KEY": payload["idempotency_key"],
        "HTTP_X_FUTUREAGI_ACTIVATION_EXPORT_SCHEMA": payload["schema_version"],
        "HTTP_X_FUTUREAGI_ACTIVATION_EXPORT_SIGNATURE": signature
        or activation_fact_signature(body=body, shared_secret=SECRET),
    }


def _post(api_client, payload, headers=None):
    body = _body(payload)
    return api_client.post(
        PATH,
        data=body,
        content_type="application/json",
        **(headers or _headers(payload, body)),
    )


def _payload(organization, workspace, user):
    evaluated_at = timezone.now()
    fact = {
        "schema_version": ACTIVATION_EXPORT_SCHEMA_VERSION,
        "evaluated_at": evaluated_at,
        "organization": {"id": str(organization.id)},
        "workspace": {"id": str(workspace.id)},
        "user": {"id": str(user.id)},
        "deployment": {"mode": "cloud", "region": "us"},
        "subscription": {"plan_tier": "payg", "status": "active"},
        "activation": {
            "primary_path": "observe",
            "stage": "waiting_for_first_trace",
            "is_activated": False,
        },
        "signals": {"observe_projects": 1, "traces": 0},
        "route_availability": {"path_observe": {"is_available": True, "reason": None}},
        "lifecycle": {
            "campaign": {
                "next_campaign_key": "first_trace_recovery",
                "template_key": "observe_first_trace",
                "status": "eligible",
            },
            "email_eligibility": {
                "eligible": True,
                "suppressed": False,
                "next_email_key": "observe_first_trace",
            },
        },
        "journey": {
            "config_schema_version": "onboarding-activation-export-config-2026-05-30.v1",
            "cohorts": [
                {
                    "cohort_key": "observe_waiting_first_trace",
                    "target_action_id": "send_first_trace",
                    "target_success_event": "trace_received",
                    "priority": 95,
                }
            ],
        },
    }
    return {
        "type": RECEIPT_TYPE,
        "export_log_id": str(uuid.uuid4()),
        "idempotency_key": f"{workspace.id}:activation:waiting",
        "schema_version": ACTIVATION_EXPORT_SCHEMA_VERSION,
        "event_cursor": evaluated_at.isoformat(),
        "evaluated_at": evaluated_at,
        "fact": fact,
    }


@pytest.mark.django_db
@override_settings(ONBOARDING_ACTIVATION_FACT_RECEIVER_SHARED_SECRET=SECRET)
def test_activation_fact_receiver_accepts_signed_payload_once(
    api_client,
    organization,
    workspace,
    user,
):
    payload = _payload(organization, workspace, user)

    response = _post(api_client, payload)

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["created"] is True
    assert result["cohort_keys"] == ["observe_waiting_first_trace"]
    receipt = OnboardingActivationFactReceipt.no_workspace_objects.get()
    assert str(receipt.workspace_id_value) == str(workspace.id)
    assert str(receipt.user_id_value) == str(user.id)
    assert receipt.deployment_region == "us"
    assert receipt.plan_tier == "payg"
    assert receipt.activation_stage == "waiting_for_first_trace"
    assert receipt.primary_path == "observe"
    assert receipt.lifecycle_campaign_key == "first_trace_recovery"
    assert receipt.email_next_key == "observe_first_trace"
    assert receipt.email_eligible is True
    assert receipt.primary_cohort_key == "observe_waiting_first_trace"
    assert receipt.journey_cohorts[0]["target_action_id"] == "send_first_trace"
    assert receipt.payload["fact"]["journey"]["cohorts"][0]["cohort_key"] == (
        "observe_waiting_first_trace"
    )
    assert OnboardingActivationFactReceiptRejection.no_workspace_objects.count() == 0


@pytest.mark.django_db
@override_settings(ONBOARDING_ACTIVATION_FACT_RECEIVER_SHARED_SECRET=SECRET)
def test_activation_fact_receiver_is_idempotent(
    api_client,
    organization,
    workspace,
    user,
):
    payload = _payload(organization, workspace, user)

    first = _post(api_client, payload)
    second = _post(api_client, payload)

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert first.json()["result"]["receipt_id"] == second.json()["result"]["receipt_id"]
    assert first.json()["result"]["created"] is True
    assert second.json()["result"]["created"] is False
    assert OnboardingActivationFactReceipt.no_workspace_objects.count() == 1


@pytest.mark.django_db
@override_settings(ONBOARDING_ACTIVATION_FACT_RECEIVER_SHARED_SECRET=SECRET)
def test_activation_fact_receiver_rejects_bad_signature(
    api_client,
    organization,
    workspace,
    user,
):
    payload = _payload(organization, workspace, user)
    body = _body(payload)
    headers = _headers(payload, body, signature="sha256=bad")

    response = _post(api_client, payload, headers=headers)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert OnboardingActivationFactReceipt.no_workspace_objects.count() == 0
    rejection = OnboardingActivationFactReceiptRejection.no_workspace_objects.get()
    assert rejection.reason == "invalid_signature"
    assert rejection.idempotency_key == payload["idempotency_key"]


@pytest.mark.django_db
@override_settings(ONBOARDING_ACTIVATION_FACT_RECEIVER_SHARED_SECRET=SECRET)
def test_activation_fact_receiver_rejects_missing_signature(
    api_client,
    organization,
    workspace,
    user,
):
    payload = _payload(organization, workspace, user)
    body = _body(payload)
    headers = _headers(payload, body)
    headers.pop("HTTP_X_FUTUREAGI_ACTIVATION_EXPORT_SIGNATURE")

    response = _post(api_client, payload, headers=headers)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert OnboardingActivationFactReceipt.no_workspace_objects.count() == 0
    rejection = OnboardingActivationFactReceiptRejection.no_workspace_objects.get()
    assert rejection.reason == "invalid_signature"


@pytest.mark.django_db
@override_settings(ONBOARDING_ACTIVATION_FACT_RECEIVER_SHARED_SECRET=SECRET)
def test_activation_fact_receiver_rejects_header_body_mismatch(
    api_client,
    organization,
    workspace,
    user,
):
    payload = _payload(organization, workspace, user)
    body = _body(payload)
    headers = _headers(payload, body)
    headers["HTTP_X_FUTUREAGI_ACTIVATION_EXPORT_KEY"] = "different-key"

    response = _post(api_client, payload, headers=headers)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert OnboardingActivationFactReceipt.no_workspace_objects.count() == 0
    rejection = OnboardingActivationFactReceiptRejection.no_workspace_objects.get()
    assert rejection.reason == "header_mismatch"


@pytest.mark.django_db
@override_settings(ONBOARDING_ACTIVATION_FACT_RECEIVER_SHARED_SECRET=SECRET)
def test_activation_fact_receiver_rejects_malformed_fact_identifiers(
    api_client,
    organization,
    workspace,
    user,
):
    payload = _payload(organization, workspace, user)
    payload["fact"]["organization"]["id"] = "not-a-uuid"

    response = _post(api_client, payload)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert OnboardingActivationFactReceipt.no_workspace_objects.count() == 0
    rejection = OnboardingActivationFactReceiptRejection.no_workspace_objects.get()
    assert rejection.reason == "malformed_payload"
    assert rejection.payload_hash


@pytest.mark.django_db
@override_settings(ONBOARDING_ACTIVATION_FACT_RECEIVER_SHARED_SECRET=SECRET)
def test_activation_fact_receiver_rejects_unsafe_payload_keys(
    api_client,
    organization,
    workspace,
    user,
):
    payload = _payload(organization, workspace, user)
    payload["fact"]["signals"]["secret"] = "hidden"

    response = _post(api_client, payload)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert OnboardingActivationFactReceipt.no_workspace_objects.count() == 0
    rejection = OnboardingActivationFactReceiptRejection.no_workspace_objects.get()
    assert rejection.reason == "unsafe_payload"


@pytest.mark.django_db
@override_settings(ONBOARDING_ACTIVATION_FACT_RECEIVER_SHARED_SECRET=SECRET)
def test_activation_fact_receiver_fails_closed_for_unknown_schema(
    api_client,
    organization,
    workspace,
    user,
):
    payload = _payload(organization, workspace, user)
    payload["schema_version"] = "unknown.v1"
    payload["fact"]["schema_version"] = "unknown.v1"

    response = _post(api_client, payload)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert OnboardingActivationFactReceipt.no_workspace_objects.count() == 0
    rejection = OnboardingActivationFactReceiptRejection.no_workspace_objects.get()
    assert rejection.reason == "unknown_schema_version"
    assert rejection.schema_version == "unknown.v1"


@pytest.mark.django_db
@override_settings(ONBOARDING_ACTIVATION_FACT_RECEIVER_SHARED_SECRET=SECRET)
def test_activation_fact_receiver_rejects_idempotency_conflicts(
    api_client,
    organization,
    workspace,
    user,
):
    payload = _payload(organization, workspace, user)
    changed = copy.deepcopy(payload)
    changed["fact"]["activation"]["stage"] = "review_first_trace"

    accepted = _post(api_client, payload)
    rejected = _post(api_client, changed)

    assert accepted.status_code == status.HTTP_200_OK
    assert rejected.status_code == status.HTTP_400_BAD_REQUEST
    assert OnboardingActivationFactReceipt.no_workspace_objects.count() == 1
    rejection = OnboardingActivationFactReceiptRejection.no_workspace_objects.get()
    assert rejection.reason == "idempotency_conflict"


@pytest.mark.django_db
@override_settings(ONBOARDING_ACTIVATION_FACT_RECEIVER_SHARED_SECRET=SECRET)
def test_activation_fact_receipts_are_queryable_for_lifecycle_scoring(
    api_client,
    organization,
    workspace,
    user,
):
    payload = _payload(organization, workspace, user)

    response = _post(api_client, payload)

    assert response.status_code == status.HTTP_200_OK
    query = OnboardingActivationFactReceipt.no_workspace_objects.filter(
        workspace_id_value=workspace.id,
        user_id_value=user.id,
        activation_stage="waiting_for_first_trace",
        primary_path="observe",
        lifecycle_campaign_key="first_trace_recovery",
        primary_cohort_key="observe_waiting_first_trace",
        email_eligible=True,
    )
    assert query.count() == 1
    receipt = query.get()
    assert "observe_waiting_first_trace" in receipt.cohort_keys
