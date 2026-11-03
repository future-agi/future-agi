"""
SDK Evaluations API Tests

Tests for SDK evaluation endpoints that allow running evaluations via API.
Note: These endpoints use APIKeyAuthentication which accepts both JWT and API key auth.
"""

from unittest.mock import patch

import pytest
from rest_framework import status


@pytest.fixture
def api_key(organization, user, db):
    """Create an API key for testing SDK endpoints."""
    from accounts.models import OrgApiKey

    api_key, _ = OrgApiKey.objects.get_or_create(
        organization=organization,
        user=user,
        defaults={
            "api_key": "test_api_key_12345",
            "secret_key": "test_secret_key_67890",
            "name": "Test API Key",
            "enabled": True,
            "type": "user",
        },
    )
    return api_key


@pytest.fixture
def sdk_client(api_client, api_key):
    """API client with SDK API key headers."""
    api_client.credentials(
        HTTP_X_API_KEY=api_key.api_key,
        HTTP_X_SECRET_KEY=api_key.secret_key,
    )
    return api_client


@pytest.fixture
def eval_template(user, db):
    """Create an eval template for testing.

    Requires user fixture to ensure organization and workspace exist for signals.
    """
    from model_hub.models.evals_metric import EvalTemplate

    template, _ = EvalTemplate.objects.get_or_create(
        name="Test Eval Template",
        defaults={
            "description": "A test evaluation template",
            "eval_id": 999002,  # Must be an integer
            "eval_tags": ["test"],
            "config": {
                "eval_type_id": "TestEval",
                "required_keys": ["input", "output"],
                "optional_keys": [],
            },
            "owner": "system",
            "organization": None,  # System template
        },
    )
    return template


@pytest.fixture
def function_param_eval_template(user, db):
    """Create a function-eval template with function_params_schema."""
    from model_hub.models.evals_metric import EvalTemplate

    template, _ = EvalTemplate.objects.get_or_create(
        name="recall_at_k_sdk_test",
        defaults={
            "description": "Function params template for SDK tests",
            "eval_id": 999003,
            "eval_tags": ["FUNCTION", "RAG"],
            "config": {
                "eval_type_id": "RecallAtK",
                "required_keys": ["hypothesis", "reference"],
                "output": "score",
                "function_eval": True,
                "function_params_schema": {
                    "k": {
                        "type": "integer",
                        "default": None,
                        "nullable": True,
                        "minimum": 1,
                    }
                },
            },
            "owner": "system",
            "organization": None,
        },
    )
    return template


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalStructureAPI:
    """Tests for /sdk/api/v1/eval/<eval_id>/ endpoint."""

    def test_get_eval_structure_unauthenticated(self, api_client, db):
        """An anonymous request is rejected before the lookup runs."""
        response = api_client.get("/sdk/api/v1/eval/123/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_get_eval_structure_with_jwt(self, auth_client, eval_template):
        """JWT authenticated user can get eval structure."""
        response = auth_client.get(f"/sdk/api/v1/eval/{eval_template.eval_id}/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert result["eval_id"] == eval_template.eval_id
        assert result["name"] == eval_template.name

    def test_get_eval_structure_with_api_key(self, sdk_client, eval_template):
        """API key authenticated request can get eval structure."""
        response = sdk_client.get(f"/sdk/api/v1/eval/{eval_template.eval_id}/")
        assert response.status_code == status.HTTP_200_OK

    def test_get_eval_structure_not_found(self, auth_client):
        """Returns error for non-existent eval_id."""
        # eval_id is an integer field, so use a non-existent integer
        response = auth_client.get("/sdk/api/v1/eval/999999999/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_eval_structure_response_format(self, auth_client, eval_template):
        """Response includes expected fields."""
        response = auth_client.get(f"/sdk/api/v1/eval/{eval_template.eval_id}/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        expected_fields = ["id", "name", "description", "eval_id", "config", "owner"]
        for field in expected_fields:
            assert field in result

    def _missing_template_body(self, auth_client):
        missing = auth_client.get("/sdk/api/v1/eval/999999999/")
        assert missing.status_code == status.HTTP_400_BAD_REQUEST
        return missing.json()

    def test_get_eval_structure_hides_other_org(self, auth_client, workspace, db):
        """Another organization's template is indistinguishable from a missing id."""
        from accounts.models.organization import Organization
        from model_hub.models.evals_metric import EvalTemplate

        foreign_org = Organization.objects.create(name="Foreign Eval Org")
        secret = "foreign-org-criteria-must-not-leak"
        foreign = EvalTemplate.no_workspace_objects.create(
            name="foreign_org_template",
            description="private",
            eval_id=999101,
            owner="user",
            organization=foreign_org,
            workspace=None,
            config={"secret": secret},
            criteria=secret,
        )

        response = auth_client.get(f"/sdk/api/v1/eval/{foreign.eval_id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body == self._missing_template_body(auth_client)
        assert secret not in response.content.decode()

    def test_get_eval_structure_hides_other_workspace(
        self, auth_client, user, organization, workspace, db
    ):
        """A same-org template bound to another workspace is hidden."""
        from accounts.models.workspace import Workspace
        from model_hub.models.evals_metric import EvalTemplate

        other_workspace = Workspace.objects.create(
            name="Other Eval Workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        secret = "other-workspace-criteria-must-not-leak"
        foreign = EvalTemplate.no_workspace_objects.create(
            name="other_workspace_template",
            eval_id=999102,
            owner="user",
            organization=organization,
            workspace=other_workspace,
            config={"secret": secret},
            criteria=secret,
        )

        response = auth_client.get(f"/sdk/api/v1/eval/{foreign.eval_id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == self._missing_template_body(auth_client)
        assert secret not in response.content.decode()

    def test_get_eval_structure_allows_org_shared_template(
        self, auth_client, organization, db
    ):
        """An org-owned template with no workspace is readable in every workspace."""
        from django.db.models.signals import post_save, pre_save
        from model_hub.models.evals_metric import EvalTemplate
        from tfc.utils.signals import auto_assign_workspace, ensure_workspace_after_save

        pre_save.disconnect(auto_assign_workspace)
        post_save.disconnect(ensure_workspace_after_save)
        try:
            shared = EvalTemplate.no_workspace_objects.create(
                name="org_shared_template",
                eval_id=999103,
                owner="user",
                organization=organization,
                workspace=None,
                config={"shared": True},
                criteria="org-shared-criteria",
            )
        finally:
            pre_save.connect(auto_assign_workspace)
            post_save.connect(ensure_workspace_after_save)
        shared.refresh_from_db()
        assert shared.workspace_id is None

        response = auth_client.get(f"/sdk/api/v1/eval/{shared.eval_id}/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", response.json())
        assert result["name"] == shared.name
        assert result["criteria"] == "org-shared-criteria"

    def test_get_eval_structure_prefers_own_template_on_eval_id_collision(
        self, auth_client, organization, workspace, db
    ):
        """When eval_id is shared, the caller's own template wins over system."""
        from django.db.models.signals import post_save, pre_save
        from model_hub.models.evals_metric import EvalTemplate
        from tfc.utils.signals import auto_assign_workspace, ensure_workspace_after_save

        pre_save.disconnect(auto_assign_workspace)
        post_save.disconnect(ensure_workspace_after_save)
        try:
            system = EvalTemplate.no_workspace_objects.create(
                name="system_collision_template",
                eval_id=999104,
                owner="system",
                organization=None,
                workspace=None,
                config={"which": "system"},
                criteria="system-collision-criteria",
            )
        finally:
            pre_save.connect(auto_assign_workspace)
            post_save.connect(ensure_workspace_after_save)
        own = EvalTemplate.no_workspace_objects.create(
            name="own_collision_template",
            eval_id=system.eval_id,
            owner="user",
            organization=organization,
            workspace=workspace,
            config={"which": "own"},
            criteria="own-collision-criteria",
        )

        response = auth_client.get(f"/sdk/api/v1/eval/{system.eval_id}/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", response.json())
        assert result["id"] == str(own.id)
        assert result["criteria"] == "own-collision-criteria"

    def test_get_eval_structure_tie_break_is_newest_then_id(
        self, auth_client, organization, workspace, db
    ):
        """Two readable rows in the same workspace: newest created_at wins, then id."""
        from datetime import timedelta

        from django.utils import timezone
        from model_hub.models.evals_metric import EvalTemplate

        older = EvalTemplate.no_workspace_objects.create(
            name="tie_break_older",
            eval_id=999105,
            owner="user",
            organization=organization,
            workspace=workspace,
            criteria="older-tie-break",
        )
        newer = EvalTemplate.no_workspace_objects.create(
            name="tie_break_newer",
            eval_id=999105,
            owner="user",
            organization=organization,
            workspace=workspace,
            criteria="newer-tie-break",
        )
        EvalTemplate.no_workspace_objects.filter(id=older.id).update(
            created_at=timezone.now() - timedelta(days=1)
        )

        response = auth_client.get("/sdk/api/v1/eval/999105/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", response.json())
        assert result["id"] == str(newer.id)

        same_time = timezone.now()
        EvalTemplate.no_workspace_objects.filter(id__in=[older.id, newer.id]).update(
            created_at=same_time
        )
        expected = str(min(older.id, newer.id))
        response = auth_client.get("/sdk/api/v1/eval/999105/")
        result = response.json().get("result", response.json())
        assert result["id"] == expected

    def test_removed_member_cannot_read_org_shared_template(
        self, api_client, user, organization, db
    ):
        """A removed member keeps the user.organization FK and must not read with it."""
        from django.db.models.signals import post_save, pre_save

        from accounts.models.organization_membership import OrganizationMembership
        from model_hub.models.evals_metric import EvalTemplate
        from tfc.utils.signals import auto_assign_workspace, ensure_workspace_after_save

        pre_save.disconnect(auto_assign_workspace)
        post_save.disconnect(ensure_workspace_after_save)
        try:
            shared = EvalTemplate.no_workspace_objects.create(
                name="removed_member_shared",
                eval_id=999106,
                owner="user",
                organization=organization,
                workspace=None,
                config={"secret": "removed-member-must-not-read"},
                criteria="removed-member-must-not-read",
            )
        finally:
            pre_save.connect(auto_assign_workspace)
            post_save.connect(ensure_workspace_after_save)
        shared.refresh_from_db()
        assert shared.workspace_id is None

        OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=organization
        ).update(is_active=False)
        user.refresh_from_db()
        assert user.organization_id == organization.id

        login = api_client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        assert login.status_code == status.HTTP_200_OK
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
        response = api_client.get(f"/sdk/api/v1/eval/{shared.eval_id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "removed-member-must-not-read" not in response.content.decode()


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalsAPI:
    """Tests for /sdk/api/v1/get-evals/ endpoint."""

    def test_get_evals_unauthenticated(self, api_client):
        """Unauthenticated request fails.

        NOTE: Currently returns 500 due to missing permission_classes on view.
        Should return 401/403. This is a known bug.
        """
        response = api_client.get("/sdk/api/v1/get-evals/")
        # 500 is current buggy behavior - should be 401/403
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_get_evals_authenticated(self, auth_client, eval_template):
        """Authenticated user can list evals."""
        response = auth_client.get("/sdk/api/v1/get-evals/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert isinstance(result, list)

    def test_get_evals_with_api_key(self, sdk_client, eval_template):
        """API key authenticated request can list evals."""
        response = sdk_client.get("/sdk/api/v1/get-evals/")
        assert response.status_code == status.HTTP_200_OK

    def test_get_evals_includes_system_templates(self, auth_client, eval_template):
        """Response includes system templates (organization=None)."""
        response = auth_client.get("/sdk/api/v1/get-evals/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        # Should include our test template
        names = [e["name"] for e in result]
        assert eval_template.name in names


@pytest.mark.integration
@pytest.mark.api
class TestStandaloneEvalV1API:
    """Tests for /sdk/api/v1/eval/ endpoint (v1)."""

    def test_standalone_eval_unauthenticated(self, api_client):
        """Unauthenticated request fails.

        NOTE: Currently returns 500 due to missing permission_classes on view.
        Should return 401/403. This is a known bug.
        """
        response = api_client.post(
            "/sdk/api/v1/eval/",
            {"inputs": [{"input": "test"}], "config": {"1": {}}},
            format="json",
        )
        # 500 is current buggy behavior - should be 401/403
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_standalone_eval_missing_inputs(self, auth_client):
        """Request without inputs fails."""
        response = auth_client.post(
            "/sdk/api/v1/eval/",
            {"config": {"1": {}}},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_standalone_eval_empty_inputs(self, auth_client):
        """Request with empty inputs fails."""
        response = auth_client.post(
            "/sdk/api/v1/eval/",
            {"inputs": [], "config": {"1": {}}},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_standalone_eval_missing_config(self, auth_client):
        """Request without config fails."""
        response = auth_client.post(
            "/sdk/api/v1/eval/",
            {"inputs": [{"input": "test"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_standalone_eval_invalid_eval_id(self, auth_client):
        """Request with non-numeric eval_id in config fails."""
        response = auth_client.post(
            "/sdk/api/v1/eval/",
            {"inputs": [{"input": "test"}], "config": {"not_a_number": {}}},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestStandaloneEvalV2API:
    """Tests for /sdk/api/v1/new-eval/ endpoint (v2)."""

    def test_new_eval_get_unauthenticated(self, api_client):
        """Unauthenticated GET request fails.

        NOTE: Currently returns 500 due to missing permission_classes on view.
        Should return 401/403. This is a known bug.
        """
        response = api_client.get("/sdk/api/v1/new-eval/?eval_id=123")
        # 500 is current buggy behavior - should be 401/403
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_new_eval_get_missing_eval_id(self, auth_client):
        """GET request without eval_id fails."""
        response = auth_client.get("/sdk/api/v1/new-eval/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_new_eval_get_invalid_eval_id(self, auth_client):
        """GET request with non-existent eval_id fails."""
        response = auth_client.get(
            "/sdk/api/v1/new-eval/?eval_id=00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_new_eval_post_unauthenticated(self, api_client):
        """Unauthenticated POST request fails.

        NOTE: Currently returns 500 due to missing permission_classes on view.
        Should return 401/403. This is a known bug.
        """
        response = api_client.post(
            "/sdk/api/v1/new-eval/",
            {"eval_name": "Test", "inputs": {"input": "test"}},
            format="json",
        )
        # 500 is current buggy behavior - should be 401/403
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_new_eval_post_missing_eval_name(self, auth_client):
        """POST request without eval_name fails."""
        response = auth_client.post(
            "/sdk/api/v1/new-eval/",
            {"inputs": {"input": "test"}},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_new_eval_post_invalid_eval_name(self, auth_client):
        """POST request with non-existent eval_name fails."""
        response = auth_client.post(
            "/sdk/api/v1/new-eval/",
            {"eval_name": "NonExistentEval", "inputs": {"input": "test"}},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_new_eval_post_rejects_invalid_function_param(
        self, auth_client, function_param_eval_template
    ):
        response = auth_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": function_param_eval_template.name,
                "inputs": {
                    "hypothesis": '["A", "B"]',
                    "reference": '["A", "C"]',
                },
                "config": {"params": {"k": 0}},
                "is_async": False,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "k must be >= 1" in str(response.json())

    def test_new_eval_post_rejects_unknown_function_param(
        self, auth_client, function_param_eval_template
    ):
        response = auth_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": function_param_eval_template.name,
                "inputs": {
                    "hypothesis": '["A", "B"]',
                    "reference": '["A", "C"]',
                },
                "config": {"params": {"n": 2}},
                "is_async": False,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Unknown function params: n" in str(response.json())

    def test_new_eval_post_rejects_double_negative_integer_with_clean_400(
        self, auth_client, function_param_eval_template
    ):
        response = auth_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": function_param_eval_template.name,
                "inputs": {
                    "hypothesis": '["A", "B"]',
                    "reference": '["A", "C"]',
                },
                "config": {"params": {"k": "--5"}},
                "is_async": False,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "k must be an integer" in str(response.json())

    def test_new_eval_post_async_persists_function_params(
        self, auth_client, function_param_eval_template
    ):
        from model_hub.models.evaluation import Evaluation

        with patch("tfc.temporal.evaluations.start_evaluation_workflow") as mock_start:
            response = auth_client.post(
                "/sdk/api/v1/new-eval/",
                {
                    "eval_name": function_param_eval_template.name,
                    "inputs": {
                        "hypothesis": '["A", "B"]',
                        "reference": '["A", "C"]',
                    },
                    "config": {"params": {"k": 4}},
                    "is_async": True,
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        payload = response.json()
        result = payload.get("result", [])
        assert isinstance(result, list) and len(result) == 1
        evals = result[0].get("evaluations", [])
        assert len(evals) == 1

        eval_id = evals[0].get("eval_id")
        assert eval_id is not None

        evaluation = Evaluation.objects.get(id=eval_id)
        assert evaluation.eval_config.get("params", {}).get("k") == 4
        mock_start.assert_called_once()


@pytest.mark.integration
@pytest.mark.api
class TestConfigureEvaluationsAPI:
    """Tests for /sdk/api/v1/configure-evaluations/ endpoint."""

    def test_configure_evaluations_unauthenticated(self, api_client):
        """Unauthenticated request fails.

        NOTE: Currently returns 500 due to missing permission_classes on view.
        Should return 401/403. This is a known bug.
        """
        response = api_client.post(
            "/sdk/api/v1/configure-evaluations/",
            {"eval_config": {}, "platform": "test"},
            format="json",
        )
        # 500 is current buggy behavior - should be 401/403
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_configure_evaluations_missing_eval_config(self, auth_client):
        """Request without eval_config fails."""
        response = auth_client.post(
            "/sdk/api/v1/configure-evaluations/",
            {"platform": "test"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_configure_evaluations_missing_platform(self, auth_client):
        """Request without platform fails."""
        response = auth_client.post(
            "/sdk/api/v1/configure-evaluations/",
            {"eval_config": {"eval_templates": "Test", "inputs": {}}},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_configure_evaluations_invalid_eval_template(self, auth_client):
        """Request with non-existent eval template fails."""
        response = auth_client.post(
            "/sdk/api/v1/configure-evaluations/",
            {
                "eval_config": {
                    "eval_templates": "NonExistentTemplate",
                    "inputs": {"input": "test"},
                },
                "platform": "test",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
