"""Negative, boundary, and regression tests for provider-credential PUT/PATCH.

Covers the #2282 fix (PUT now syncs the gateway) plus the tenant-isolation
correction (cross-tenant PUT stays 404). Focus areas:
- Negative: malformed/invalid payloads, unauthenticated, unknown field,
  bad types, SQL/blank edge inputs.
- Boundary: max-length strings, empty lists/dicts, None/blank, integer
  extrema, is_active toggles, model list with one vs many entries.
- Regression: PUT and PATCH keep identical happy-path + failure semantics,
  cross-tenant PUT returns 404 without a gateway push, encrypted credentials
  are never rewritten on a metadata-only update.
"""

from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.provider_credential import AgentccProviderCredential
from conftest import WorkspaceAwareAPIClient
from integrations.services.credentials import CredentialManager
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def nb_secondary_org_context(user):
    org = Organization.objects.create(name="NB Provider Org")
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    workspace = Workspace.objects.create(
        name="NB Provider Workspace",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.objects.create(
        workspace=workspace,
        user=user,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        organization_membership=membership,
        is_active=True,
    )
    return org, workspace


@pytest.fixture
def nb_client(user, nb_secondary_org_context):
    _, workspace = nb_secondary_org_context
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    yield client
    client.stop_workspace_injection()


def _make_cred(org, **kwargs):
    defaults = {
        "provider_name": "openai",
        "display_name": "Original",
        "encrypted_credentials": CredentialManager.encrypt({"api_key": "sk-orig"}),
        "api_format": "openai",
    }
    defaults.update(kwargs)
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=org, **defaults
    )


# ---------------------------------------------------------------------------
# NEGATIVE TESTS
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestAgentccProviderCredentialNegative:
    def test_put_unauthenticated_returns_401_or_403(
        self, api_client, db, nb_secondary_org_context
    ):
        org, _ = nb_secondary_org_context
        cred = _make_cred(org)
        response = api_client.put(
            f"/agentcc/provider-credentials/{cred.id}/",
            {"display_name": "x"},
            format="json",
        )
        assert response.status_code in (401, 403)

    def test_put_unknown_field_is_accepted_and_ignored(
        self, nb_secondary_org_context, nb_client
    ):
        """A field the serializer does not recognize must not error or persist."""
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, display_name="Before")
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            response = nb_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"display_name": "After", "bogus_field": "drop-me"},
                format="json",
            )
        assert response.status_code == 200, response.json()
        cred.refresh_from_db()
        assert cred.display_name == "After"
        assert not hasattr(cred, "bogus_field")

    def test_put_invalid_url_returns_400(self, nb_secondary_org_context, nb_client):
        org, _ = nb_secondary_org_context
        cred = _make_cred(org)
        response = nb_client.put(
            f"/agentcc/provider-credentials/{cred.id}/",
            {"base_url": "not-a-url"},
            format="json",
        )
        assert response.status_code == 400, response.json()
        cred.refresh_from_db()
        # Gateway push must not have run on a rejected payload.
        assert cred.base_url == ""

    def test_put_wrong_type_timeout_returns_400(
        self, nb_secondary_org_context, nb_client
    ):
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, default_timeout_seconds=60)
        response = nb_client.put(
            f"/agentcc/provider-credentials/{cred.id}/",
            {"default_timeout_seconds": "sixty"},
            format="json",
        )
        assert response.status_code == 400, response.json()
        cred.refresh_from_db()
        assert cred.default_timeout_seconds == 60

    def test_put_is_active_wrong_type_is_coerced_or_rejected(
        self, nb_secondary_org_context, nb_client
    ):
        """BooleanField coerces many non-bool values (e.g. "yes" -> True).

        The serializer must not 400 on a coercible value, and the coerced
        boolean must persist. If it cannot coerce, it 400s. Either is valid
        behavior; we assert the request does not silently 200 with the old
        value while claiming success.
        """
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, is_active=True)
        response = nb_client.put(
            f"/agentcc/provider-credentials/{cred.id}/",
            {"is_active": "yes"},
            format="json",
        )
        cred.refresh_from_db()
        if response.status_code == 200:
            # Coerced successfully — value must reflect the change.
            assert cred.is_active is True  # "yes" coerces to True
            assert response.json()["result"]["is_active"] is True
        else:
            assert response.status_code == 400
            assert cred.is_active is True  # unchanged on rejection

    def test_put_empty_body_is_accepted_noop(self, nb_secondary_org_context, nb_client):
        """Empty body is valid (all fields optional) and leaves the row intact."""
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, display_name="Kept")
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            response = nb_client.put(
                f"/agentcc/provider-credentials/{cred.id}/", {}, format="json"
            )
        assert response.status_code == 200, response.json()
        cred.refresh_from_db()
        assert cred.display_name == "Kept"
        # Empty update still re-syncs (config unchanged but pushed once).
        mock_push.assert_called_once()

    def test_put_nonexistent_id_returns_404_no_push(
        self, nb_secondary_org_context, nb_client
    ):
        import uuid

        org, _ = nb_secondary_org_context
        fake = uuid.uuid4()
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            response = nb_client.put(
                f"/agentcc/provider-credentials/{fake}/",
                {"display_name": "x"},
                format="json",
            )
        assert response.status_code == 404
        mock_push.assert_not_called()


# ---------------------------------------------------------------------------
# BOUNDARY TESTS
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestAgentccProviderCredentialBoundary:
    def test_put_display_name_max_length(self, nb_secondary_org_context, nb_client):
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, display_name="short")
        boundary = "x" * 255
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            response = nb_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"display_name": boundary},
                format="json",
            )
        assert response.status_code == 200, response.json()
        cred.refresh_from_db()
        assert cred.display_name == boundary

    def test_put_display_name_over_max_length_returns_400(
        self, nb_secondary_org_context, nb_client
    ):
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, display_name="short")
        too_long = "x" * 256
        response = nb_client.put(
            f"/agentcc/provider-credentials/{cred.id}/",
            {"display_name": too_long},
            format="json",
        )
        assert response.status_code == 400, response.json()
        cred.refresh_from_db()
        assert cred.display_name == "short"

    def test_put_empty_models_list(self, nb_secondary_org_context, nb_client):
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, models_list=["gpt-4o"])
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            response = nb_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"models_list": []},
                format="json",
            )
        assert response.status_code == 200, response.json()
        cred.refresh_from_db()
        assert cred.models_list == []

    def test_put_is_active_false_then_true(self, nb_secondary_org_context, nb_client):
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, is_active=True)
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            r1 = nb_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"is_active": False},
                format="json",
            )
            assert r1.status_code == 200, r1.json()
            cred.refresh_from_db()
            assert cred.is_active is False
            r2 = nb_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"is_active": True},
                format="json",
            )
            assert r2.status_code == 200, r2.json()
        cred.refresh_from_db()
        assert cred.is_active is True

    def test_put_non_positive_timeout_rejected(
        self, nb_secondary_org_context, nb_client
    ):
        """#2294 follow-up: 0 and negative timeouts must 400, not persist.

        The gateway silently substitutes defaults for any value <= 0, so the
        control plane must not report a successful save with a value the
        runtime will never use. The update serializer rejects them.
        """
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, default_timeout_seconds=60)
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            for value in (0, -1):
                response = nb_client.put(
                    f"/agentcc/provider-credentials/{cred.id}/",
                    {"default_timeout_seconds": value},
                    format="json",
                )
                assert response.status_code == 400, response.json()
        # Neither rejected payload nor any gateway push must have happened.
        mock_push.assert_not_called()
        cred.refresh_from_db()
        assert cred.default_timeout_seconds == 60

    def test_put_extra_config_empty_dict(self, nb_secondary_org_context, nb_client):
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, extra_config={"a": 1})
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            response = nb_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"extra_config": {}},
                format="json",
            )
        assert response.status_code == 200, response.json()
        cred.refresh_from_db()
        assert cred.extra_config == {}


# ---------------------------------------------------------------------------
# REGRESSION TESTS (PUT vs PATCH parity + #2282 + tenant isolation)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestAgentccProviderCredentialRegression:
    def test_put_and_patch_return_identical_shape(
        self, nb_secondary_org_context, nb_client
    ):
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, display_name="P")
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ):
            put_r = nb_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"display_name": "ViaPut"},
                format="json",
            )
            patch_r = nb_client.patch(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"display_name": "ViaPatch"},
                format="json",
            )
        assert put_r.status_code == 200 and patch_r.status_code == 200
        # Both wrap under `result` and both carry gateway_synced.
        assert "result" in put_r.json() and "result" in patch_r.json()
        assert put_r.json()["result"]["gateway_synced"] is True
        assert patch_r.json()["result"]["gateway_synced"] is True

    def test_cross_tenant_put_returns_404_without_gateway_push(
        self, user, nb_secondary_org_context, nb_client
    ):
        """#2282 tenant-isolation: put on another org's credential is 404, no push."""
        cred = _make_cred(user.organization, display_name="OrgA")
        encrypted_before = bytes(cred.encrypted_credentials)
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            response = nb_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"display_name": "Must Not Apply"},
                format="json",
            )
        assert response.status_code == 404
        mock_push.assert_not_called()
        cred.refresh_from_db()
        assert cred.display_name == "OrgA"
        assert bytes(cred.encrypted_credentials) == encrypted_before

    def test_cross_tenant_patch_returns_404_without_gateway_push(
        self, user, nb_secondary_org_context, nb_client
    ):
        """PUT/PATCH parity: a cross-tenant PATCH must also be 404, no push.

        get_object() is taken outside the broad handler in both update() and
        partial_update(), so a tenant-scoped miss keeps DRF's 404 semantics
        instead of being rewritten to 400.
        """
        cred = _make_cred(user.organization, display_name="OrgA")
        encrypted_before = bytes(cred.encrypted_credentials)
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            response = nb_client.patch(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"display_name": "Must Not Apply"},
                format="json",
            )
        assert response.status_code == 404
        mock_push.assert_not_called()
        cred.refresh_from_db()
        assert cred.display_name == "OrgA"
        assert bytes(cred.encrypted_credentials) == encrypted_before

    def test_put_preserves_encrypted_credentials_and_pushes_once(
        self, nb_secondary_org_context, nb_client
    ):
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, display_name="Old", models_list=["gpt-4o-mini"])
        encrypted_before = bytes(cred.encrypted_credentials)
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=True,
        ) as mock_push:
            response = nb_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"display_name": "New", "models_list": ["gpt-4o"]},
                format="json",
            )
        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        mock_push.assert_called_once_with(org)
        cred.refresh_from_db()
        assert cred.display_name == "New"
        assert cred.models_list == ["gpt-4o"]
        # api_key must be untouched byte-for-byte.
        assert bytes(cred.encrypted_credentials) == encrypted_before
        assert CredentialManager.decrypt(cred.encrypted_credentials) == {
            "api_key": "sk-orig"
        }

    def test_put_reports_gateway_sync_failure(
        self, nb_secondary_org_context, nb_client
    ):
        org, _ = nb_secondary_org_context
        cred = _make_cred(org, display_name="Old")
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._push_config_to_gateway",
            return_value=False,
        ) as mock_push:
            response = nb_client.put(
                f"/agentcc/provider-credentials/{cred.id}/",
                {"display_name": "SavedButUnsynced"},
                format="json",
            )
        assert response.status_code == 200, response.json()
        result = response.json()["result"]
        assert result["gateway_synced"] is False
        assert result["gateway_warning"]
        mock_push.assert_called_once_with(org)
        cred.refresh_from_db()
        # Saved change is NOT hidden by the sync warning.
        assert cred.display_name == "SavedButUnsynced"
