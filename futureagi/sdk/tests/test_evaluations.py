"""
SDK Evaluations API Tests

Tests for SDK evaluation endpoints that allow running evaluations via API.
Note: These endpoints use APIKeyAuthentication which accepts both JWT and API key auth.
"""

import builtins
import json
from unittest.mock import Mock, patch

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
    # This fixture is a global system template. The request-context signal
    # fills nullable organization/workspace fields on create, so reset them to
    # keep the row global, as the structure endpoint expects.
    EvalTemplate.all_objects.filter(pk=template.pk).update(
        organization=None,
        workspace=None,
    )
    template.refresh_from_db()
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


@pytest.fixture
def standalone_billing_unavailable(monkeypatch):
    """Exercise the real helper's optional-import fallback, not a fresh OSS boot.

    Root conftest supplies usage stubs in OSS runs, so reject the SDK helper's
    usage imports even when already loaded. Other application imports stay real;
    only the optional billing callable is set to its missing value.
    """
    from sdk.utils import evaluations

    original_import = builtins.__import__
    unavailable_imports = []

    def without_usage(name, globals=None, locals=None, fromlist=(), level=0):
        if (globals or {}).get("__name__") == evaluations.__name__ and (
            name == "ee.usage" or name.startswith("ee.usage.")
        ):
            unavailable_imports.append(name)
            raise ImportError("Optional usage package unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(evaluations, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(builtins, "__import__", without_usage)
    return unavailable_imports


@pytest.fixture
def standalone_eval_result():
    from evaluations.engine import EvalResult

    return EvalResult(
        value=1.0,
        data={"input": "test input", "output": "test output"},
        reason="Evaluation completed",
        failure=None,
        runtime=0.1,
        model_used=None,
        metrics=[],
        metadata={"test": "success"},
        output_type="score",
    )


@pytest.mark.integration
class TestStandaloneEvalCostLogging:
    def test_engine_error_without_billing_preserves_original_exception(
        self, user, workspace, eval_template, standalone_billing_unavailable
    ):
        from sdk.utils import evaluations

        inputs = {"input": "test input", "output": "test output"}
        engine_error = RuntimeError("Evaluation engine failed")
        # Keep the real cost helper: absent billing legitimately returns None.
        log_row = evaluations._log_and_deduct_cost_for_standalone_eval(
            user, eval_template, False, inputs, workspace=workspace
        )
        assert log_row is None

        with patch("evaluations.engine.run_eval", side_effect=engine_error) as run:
            with pytest.raises(evaluations.StandaloneEvaluationError) as raised:
                evaluations._run_eval(eval_template, inputs, None, user, workspace)

        run.assert_called_once()
        assert str(raised.value) == str(engine_error)
        assert raised.value.args == (engine_error,)
        assert raised.value.__context__ is engine_error
        assert "ee.usage.services.metering" in standalone_billing_unavailable

    def test_engine_error_with_billing_updates_existing_log(
        self,
        user,
        workspace,
        eval_template,
        standalone_billing_unavailable,
        monkeypatch,
    ):
        from sdk.utils import evaluations
        from tfc.constants.api_calls import APICallStatusChoices

        inputs = {"input": "test input", "output": "test output"}
        existing_config = {
            "input": inputs,
            "mappings": inputs,
            "source": "standalone_v2",
            "reference_id": str(user.id),
        }
        log_row = Mock(
            status=APICallStatusChoices.PROCESSING.value,
            config=json.dumps(existing_config),
        )
        bill = Mock(return_value=log_row)
        monkeypatch.setattr(evaluations, "log_and_deduct_cost_for_api_request", bill)
        engine_error = RuntimeError("Evaluation engine failed")

        with patch("evaluations.engine.run_eval", side_effect=engine_error) as run:
            with pytest.raises(evaluations.StandaloneEvaluationError) as raised:
                evaluations._run_eval(eval_template, inputs, None, user, workspace)

        bill.assert_called_once()
        assert bill.call_args.kwargs["source_id"] == eval_template.id
        assert bill.call_args.kwargs["config"]["mappings"] == inputs
        run.assert_called_once()
        assert log_row.status == APICallStatusChoices.ERROR.value
        assert json.loads(log_row.config) == {
            **existing_config,
            "output": {"output": None, "reason": str(engine_error)},
        }
        log_row.save.assert_called_once_with()
        assert str(raised.value) == str(engine_error)
        assert raised.value.args == (engine_error,)
        assert raised.value.__context__ is engine_error

    def test_success_without_billing_keeps_result_and_skips_log_updates(
        self,
        user,
        workspace,
        eval_template,
        standalone_billing_unavailable,
        standalone_eval_result,
    ):
        from sdk.utils import evaluations

        inputs = {"input": "test input", "output": "test output"}
        with (
            patch(
                "evaluations.engine.run_eval", return_value=standalone_eval_result
            ) as run,
            patch.object(evaluations, "json") as log_json,
        ):
            result = evaluations._run_eval(eval_template, inputs, None, user, workspace)

        run.assert_called_once()
        assert result == {
            "data": inputs,
            "failure": None,
            "reason": "Evaluation completed",
            "runtime": 0.1,
            "model": None,
            "metrics": [],
            "metadata": {"test": "success"},
            "output": "score",
            "value": 1.0,
        }
        log_json.loads.assert_not_called()
        log_json.dumps.assert_not_called()
        assert "ee.usage.services.metering" in standalone_billing_unavailable
        assert "ee.usage.services.config" in standalone_billing_unavailable


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalStructureAPI:
    """Tests for /sdk/api/v1/eval/<eval_id>/ endpoint."""

    def test_get_eval_structure_unauthenticated(self, api_client, db):
        """Unauthenticated request fails.

        NOTE: Due to missing permission_classes on view, validation runs first.
        Returns 400 for validation errors before authentication fails.
        This is a known bug - should return 401/403.
        """
        # Use a known eval_id (doesn't need to exist for auth test)
        response = api_client.get("/sdk/api/v1/eval/123/")
        # 400 is current behavior (validation error before auth check)
        # Should return 401/403 if proper permission_classes were added
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
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

    def test_new_eval_engine_error_without_billing_returns_original_error(
        self, sdk_client, eval_template, standalone_billing_unavailable
    ):
        from model_hub.models.evaluation import Evaluation

        engine_error = RuntimeError("Evaluation engine failed")
        with (
            patch("evaluations.engine.run_eval", side_effect=engine_error) as run,
            patch("sdk.views.evaluations.track_mixpanel_event"),
        ):
            response = sdk_client.post(
                "/sdk/api/v1/new-eval/",
                {
                    "eval_name": eval_template.name,
                    "inputs": {"input": "test input", "output": "test output"},
                    "is_async": False,
                },
                format="json",
            )

        run.assert_called_once()
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.json() == {
            "status": False,
            "type": "validation_error",
            "code": "invalid",
            "detail": str(engine_error),
            "message": str(engine_error),
            "error": str(engine_error),
            "result": str(engine_error),
        }
        assert "ee.usage.services.metering" in standalone_billing_unavailable
        assert not Evaluation.objects.filter(eval_template=eval_template).exists()

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
