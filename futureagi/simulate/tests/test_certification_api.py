"""Certification campaign API contract tests.

Covers the plan's API guarantees: Idempotency-Key required, org allowlist
gate, tenant scoping, idempotent re-admission, cancel, and secret rejection
in deployment_identity. Temporal calls are mocked — no workflow is started.
"""

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from simulate.models import CertificationCampaign, CertificationCase
from simulate.serializers.certification import CertificationCampaignCreateSerializer

BASE = "/simulate/api/certification-campaigns/"
CONFIG_SHA = "49d97932bda309f9b5db84b8d19b9fb977d11d6b9eec090d494db0d9fd24865e"


def _deployment_identity(**overrides):
    stable = {
        "namespace": "futureagi",
        "helm_release": {"name": "futureagi-application", "revision": 42},
        "components": [
            {
                "name": "futureagi-application-backend",
                "images": {"application": "repo/backend@sha256:" + "1" * 64},
            },
            {
                "name": "futureagi-application-temporal-worker-simulation-runner",
                "images": {"application": "repo/runner@sha256:" + "2" * 64},
            },
        ],
    }
    value = {
        **stable,
        "kind": "gke_release",
        "release_digest": hashlib.sha256(
            json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "alk_snapshot": {"name": "alk-production", "digest": "build-123"},
    }
    value.update(overrides)
    return value


def _deployment_id(identity):
    return (
        f"gke-release:{identity['release_digest'][:16]}:"
        f"{identity['alk_snapshot']['digest']}"
    )


def test_deployment_identity_accepts_immutable_sidecar_images():
    identity = _deployment_identity()
    identity["components"][0]["images"]["mesh"] = "repo/mesh@sha256:" + "3" * 64
    stable = {key: identity[key] for key in ("namespace", "helm_release", "components")}
    identity["release_digest"] = hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    serializer = CertificationCampaignCreateSerializer(
        data={
            "deployment_id": _deployment_id(identity),
            "deployment_identity": identity,
        }
    )
    assert serializer.is_valid(), serializer.errors


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
                        "config_ref": "simulate/certification/fixtures/test_agent.yaml",
                        "config_sha256": CONFIG_SHA,
                        "seed": 42,
                        "secret_ref_set": [],
                    },
                    {
                        "interaction": 2,
                        "scenario_ref": "scenarios:agent_one/turn2.yaml",
                        "config_ref": "simulate/certification/fixtures/test_agent.yaml",
                        "config_sha256": CONFIG_SHA,
                        "seed": 43,
                        "secret_ref_set": [],
                    },
                ],
                "authorization": {
                    "owner": "cert-team",
                    "automated_production_calls": "approved",
                    "approved_by": "rishav",
                    "approved_at": "2026-10-01T00:00:00Z",
                    "target_ownership": "futureagi",
                    "target_endpoint_reviewed": True,
                },
            }
        ],
    }


def _post(client, org, payload=None, idem="key-1"):
    identity = _deployment_identity()
    body = {
        "deployment_id": _deployment_id(identity),
        "deployment_identity": identity,
        "suite_key": "test_agents_v1",
    }
    body.update(payload or {})
    return client.post(BASE, body, format="json", HTTP_IDEMPOTENCY_KEY=idem)


@pytest.mark.django_db(transaction=True)
def test_create_requires_idempotency_key(auth_client, organization):
    with override_settings(PRODUCTION_CERTIFICATION_ORG_ALLOWLIST=str(organization.id)):
        resp = auth_client.post(
            BASE,
            {
                "deployment_id": _deployment_id(_deployment_identity()),
                "deployment_identity": _deployment_identity(),
                "suite_key": "x",
            },
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
        assert start_wf.call_count == 2


@pytest.mark.django_db(transaction=True)
def test_duplicate_admission_rejects_conflicting_identity(auth_client, organization):
    with (
        override_settings(PRODUCTION_CERTIFICATION_ORG_ALLOWLIST=str(organization.id)),
        patch(
            "simulate.services.certification.load_suite",
            return_value=_enabled_suite_doc(),
        ),
        patch("simulate.temporal.client.start_certification_workflow"),
    ):
        assert _post(auth_client, organization).status_code == 201
        conflict = _deployment_identity(kind="different_release_kind")
        response = _post(
            auth_client,
            organization,
            {"deployment_identity": conflict},
            idem="key-2",
        )
    assert response.status_code == 409
    assert response.json()["error"] == "deployment_identity_conflict"


@pytest.mark.django_db(transaction=True)
def test_create_authenticates_with_deployment_header_contract(
    api_client, organization, workspace, user
):
    from accounts.models import OrgApiKey

    key = OrgApiKey.no_workspace_objects.create(
        name="certification workflow",
        organization=organization,
        workspace=workspace,
        type="user",
        user=user,
    )
    with (
        override_settings(PRODUCTION_CERTIFICATION_ORG_ALLOWLIST=str(organization.id)),
        patch(
            "simulate.services.certification.load_suite",
            return_value=_enabled_suite_doc(),
        ),
        patch("simulate.temporal.client.start_certification_workflow"),
    ):
        response = api_client.post(
            BASE,
            {
                "deployment_id": _deployment_id(_deployment_identity()),
                "deployment_identity": _deployment_identity(),
                "suite_key": "test_agents_v1",
            },
            format="json",
            HTTP_X_API_KEY=key.api_key,
            HTTP_X_SECRET_KEY=key.secret_key,
            HTTP_X_WORKSPACE_ID=str(workspace.id),
            HTTP_IDEMPOTENCY_KEY="header-contract",
        )
    assert response.status_code == 201, response.json()


def test_harness_health_exposes_configured_snapshot(auth_client):
    provider = MagicMock()
    provider.health.return_value = {"configured": True, "provider": "daytona"}
    with (
        override_settings(
            HOSTED_SANDBOX_PROVIDER="daytona",
            ALK_DAYTONA_DOCKERFILE="",
            ALK_DAYTONA_SNAPSHOT="alk-production",
            ALK_DAYTONA_SNAPSHOT_DIGEST="build-123",
        ),
        patch("simulate.views.harness_job.get_harness_provider", return_value=provider),
    ):
        response = auth_client.get("/simulate/api/harness-jobs/health/")
    assert response.status_code == 200
    assert response.json()["snapshot"] == {
        "name": "alk-production",
        "digest": "build-123",
    }


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
