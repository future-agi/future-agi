"""Certification campaign API contract tests.

Covers the plan's API guarantees: Idempotency-Key required, org allowlist
gate, tenant scoping, idempotent re-admission, cancel, and secret rejection
in deployment_identity. Temporal calls are mocked — no workflow is started.
"""

from unittest.mock import patch

import pytest
from django.test import override_settings

from simulate.models import CertificationCampaign, CertificationCase

BASE = "/simulate/api/certification-campaigns/"


def _enabled_suite_doc():
    return {
        "version": 1,
        "suite_key": "test_agents_v1",
        "defaults": {"artifacts": {"level": "full"}, "max_duration_seconds": 3600},
        "cases": [
            {
                "case_key": "agent_one__primary",
                "enabled": True,
                "agent": {
                    "name": "Agent One",
                    "model": "test-model",
                    "connector": "vapi",
                    "source": {
                        "repository": "future-agi/agent-one",
                        "commit_sha": "a" * 40,
                    },
                },
                "target": {"kind": "hosted_service", "ref": "vapi://agent-one"},
                "run_test_id": "l2_audio_room_speak_l2",
                "interactions": [
                    {
                        "interaction": 1,
                        "scenario_ref": "scenarios:agent_one/turn1.yaml",
                        "config_ref": "cert/agent_one.yaml",
                        "seed": 42,
                        "secret_ref_set": [],
                    },
                    {
                        "interaction": 2,
                        "scenario_ref": "scenarios:agent_one/turn2.yaml",
                        "config_ref": "cert/agent_one.yaml",
                        "seed": 43,
                        "secret_ref_set": [],
                    },
                ],
                "authorization": {
                    "owner": "cert-team",
                    "automated_production_calls": "approved",
                    "approved_by": "rishav",
                    "approved_at": "2026-10-01T00:00:00Z",
                    "target_endpoint_reviewed": True,
                },
            }
        ],
    }


def _post(client, org, payload=None, idem="key-1"):
    body = {
        "deployment_id": "dep-1",
        "deployment_identity": {"region": "us", "image": "runner@sha256:abc"},
        "suite_key": "test_agents_v1",
    }
    body.update(payload or {})
    return client.post(BASE, body, format="json", HTTP_IDEMPOTENCY_KEY=idem)


@pytest.mark.django_db(transaction=True)
def test_create_requires_idempotency_key(auth_client, organization):
    with override_settings(PRODUCTION_CERTIFICATION_ORG_ALLOWLIST=str(organization.id)):
        resp = auth_client.post(
            BASE,
            {"deployment_id": "dep-1", "suite_key": "x"},
            format="json",
        )
    assert resp.status_code == 400
    assert "Idempotency-Key" in resp.json()["detail"]


@pytest.mark.django_db(transaction=True)
def test_create_rejected_for_non_allowlisted_org(auth_client):
    resp = _post(auth_client, None)
    assert resp.status_code == 403


@pytest.mark.django_db(transaction=True)
def test_create_rejects_secret_material_in_identity(auth_client, organization):
    with override_settings(PRODUCTION_CERTIFICATION_ORG_ALLOWLIST=str(organization.id)):
        resp = _post(
            auth_client,
            organization,
            {"deployment_identity": {"api_key": "sk-live-abc123"}},
        )
    assert resp.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_create_admits_campaign_once_and_materializes_cases(auth_client, organization):
    with (
        override_settings(PRODUCTION_CERTIFICATION_ORG_ALLOWLIST=str(organization.id)),
        patch(
            "simulate.services.certification.load_suite",
            return_value=_enabled_suite_doc(),
        ),
        patch(
            "simulate.temporal.client.start_certification_workflow",
            return_value="cert-wf-1",
        ) as start_wf,
    ):
        resp = _post(auth_client, organization)
        assert resp.status_code == 201, resp.json()
        campaign_id = resp.json()["id"]
        assert resp.json()["cases_total"] == 1
        assert len(resp.json()["cases"]) == 1

        # Exact-once: same deployment+suite returns the existing campaign.
        resp2 = _post(auth_client, organization, idem="key-2")
        assert resp2.status_code == 200
        assert resp2.json()["id"] == campaign_id

        assert CertificationCampaign.objects.count() == 1
        assert CertificationCase.objects.count() == 1
        assert start_wf.call_count == 1  # started once, not per request


@pytest.mark.django_db(transaction=True)
def test_retrieve_scoped_to_requesting_org(auth_client, organization):
    from rest_framework.test import APIClient

    from accounts.models import Organization, User

    with (
        override_settings(PRODUCTION_CERTIFICATION_ORG_ALLOWLIST=str(organization.id)),
        patch(
            "simulate.services.certification.load_suite",
            return_value=_enabled_suite_doc(),
        ),
        patch("simulate.temporal.client.start_certification_workflow"),
    ):
        resp = _post(auth_client, organization)
        campaign_id = resp.json()["id"]

        # Same-org retrieve works.
        detail = auth_client.get(f"{BASE}{campaign_id}/")
        assert detail.status_code == 200
        assert detail.json()["id"] == campaign_id

        # Another org cannot see it.
        other_org = Organization.objects.create(name="Other Org")
        other_user = User.objects.create_user(
            email="other@futureagi.com",
            password="x",
            name="Other",
            organization=other_org,
        )
        other_client = APIClient()
        other_client.force_authenticate(user=other_user)
        assert other_client.get(f"{BASE}{campaign_id}/").status_code == 404


@pytest.mark.django_db(transaction=True)
def test_cancel_signals_workflow_and_sets_state(auth_client, organization):
    with (
        override_settings(PRODUCTION_CERTIFICATION_ORG_ALLOWLIST=str(organization.id)),
        patch(
            "simulate.services.certification.load_suite",
            return_value=_enabled_suite_doc(),
        ),
        patch("simulate.temporal.client.start_certification_workflow"),
        patch("simulate.temporal.client.cancel_certification_workflow") as cancel_wf,
    ):
        resp = _post(auth_client, organization)
        campaign_id = resp.json()["id"]

        cancel = auth_client.post(f"{BASE}{campaign_id}/cancel/", {}, format="json")
        assert cancel.status_code == 200
        assert cancel.json()["state"] in ("canceling", "canceled")
        cancel_wf.assert_called_once()

        # Second cancel on a finished campaign conflicts.
        campaign = CertificationCampaign.no_workspace_objects.get(id=campaign_id)
        campaign.state = CertificationCampaign.State.CANCELED
        campaign.save(update_fields=["state"])
        assert (
            auth_client.post(
                f"{BASE}{campaign_id}/cancel/", {}, format="json"
            ).status_code
            == 409
        )
