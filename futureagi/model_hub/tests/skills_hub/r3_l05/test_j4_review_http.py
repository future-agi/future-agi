"""Contract and authorization regressions for the production author composer."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import OrgApiKey
from model_hub.models.skills_hub_v12 import HubSkillContextBinding
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.tests.skills_hub import test_i2_integration as http_fixtures
from model_hub.tests.skills_hub.r3_l04 import conftest as native_fixtures
from model_hub.tests.skills_hub.r3_l04 import test_native_privacy as privacy
from model_hub.tests.skills_hub.r3_l05 import test_release_requests as fixtures


def stored_native(configuration):
    """Native evidence identity as production stores it."""
    return {
        "schema": 1,
        "digest": canonical_digest(configuration),
        "native": configuration,
    }


admin = http_fixtures.admin
actors = fixtures.actors
candidate = fixtures.candidate
native_configuration = native_fixtures.native_configuration
evidence = privacy.evidence
pytestmark = pytest.mark.django_db(transaction=True)
BASE = "/model-hub/skills/release-requests/"


def test_options_contract_and_authored_list(admin, actors, candidate, hub_skill):
    author, reviewer = actors
    run = fixtures.text_run(author, hub_skill, candidate[1])
    response = admin.get(
        BASE + "options/",
        {
            "resource_id": str(hub_skill.pk),
            "kind": "skill",
            "candidate_version_id": str(candidate[1].pk),
        },
    )
    assert response.status_code == 200, response.data
    body = response.data["result"]
    assert str(reviewer.user_id) in [row["id"] for row in body["recipients"]]
    assert body["evidence"][0]["source_id"] == str(run.pk)
    assert (
        body["evidence"][0]["configuration"]["text"]["model"]["model_id"] == "fixture"
    )
    request = fixtures.submit(
        author, reviewer, fixtures.preview(author, hub_skill, candidate[1])
    )
    response = admin.get(BASE)
    assert response.status_code == 200, response.data
    assert response.data["result"]["results"][0]["id"] == request["id"]
    assert "packet" not in response.data["result"]["results"][0]


def test_native_share_is_explicit_owner_only_and_expiry_guarded(
    admin, actors, evidence, native_configuration, organization, workspace
):
    # Synthetic terminal evidence fixture, stored as production stores it:
    # {schema, digest, native} (native_evidence.create_native_evidence).
    evidence.configuration = stored_native(native_configuration)
    evidence.save(update_fields=["configuration"])
    path = BASE + f"native-evidence/{evidence.pk}/share/"
    denied = admin.post(path, {"acknowledge_snapshot_sharing": False}, format="json")
    assert denied.status_code == 400, denied.data
    evidence.refresh_from_db()
    assert evidence.visibility == "private"
    reviewer = actors[1]
    key = OrgApiKey.objects.create(
        name="Synthetic nonowner",
        type="user",
        user_id=reviewer.user_id,
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        organization=organization,
        workspace=workspace,
    )
    other = APIClient()
    other.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    assert (
        other.post(
            path, {"acknowledge_snapshot_sharing": True}, format="json"
        ).status_code
        == 404
    )
    shared = admin.post(path, {"acknowledge_snapshot_sharing": True}, format="json")
    assert shared.status_code == 200, shared.data
    assert shared.data["result"]["visibility"] == "workspace"
    assert shared.data["result"]["source"] == "simulation_native"
    assert (
        other.post(
            path, {"acknowledge_snapshot_sharing": True}, format="json"
        ).status_code
        == 404
    )
    evidence.payload_expires_at = timezone.now() - timedelta(seconds=1)
    evidence.save(update_fields=["payload_expires_at"])
    expired = admin.post(path, {"acknowledge_snapshot_sharing": True}, format="json")
    assert expired.status_code == 410, expired.data


def test_options_returns_typed_native_evidence_and_exact_target(
    admin, candidate, hub_skill, evidence, native_configuration, organization, workspace
):
    evidence.configuration = stored_native(native_configuration)
    evidence.candidate_digest = candidate[1].digest
    evidence.save(update_fields=["configuration", "candidate_digest"])
    binding = HubSkillContextBinding.objects.create(
        organization=organization,
        workspace=workspace,
        context=hub_skill,
        name="Synthetic application",
        kind="runtime",
        selector={"label": "production"},
        update_mode="next_session",
        profile="generic",
        profile_version="1",
        configuration=native_configuration,
        configuration_digest=canonical_digest(native_configuration),
        expected_report_interval_seconds=60,
    )
    response = admin.get(
        BASE + "options/",
        {
            "resource_id": str(hub_skill.pk),
            "kind": "skill",
            "candidate_version_id": str(candidate[1].pk),
        },
    )
    assert response.status_code == 200, response.data
    body = response.data["result"]
    assert body["targets"] == [
        {
            "name": "Synthetic application",
            "target": {
                "binding_id": str(binding.pk),
                "environment_id": None,
                "revision": 1,
                "configuration_digest": binding.configuration_digest,
            },
        }
    ]
    assert body["evidence"][0]["source"] == "simulation_native"
    assert body["evidence"][0]["visibility"] == "private"
    assert (
        body["evidence"][0]["configuration"]["native"]["entrypoint"] == "synthetic:run"
    )
