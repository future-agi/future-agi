import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.metric import Metric
from model_hub.models.metric_prompt_checker import PromptChecker
from tfc.ee_gating import FeatureUnavailable


def _assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


def _make_model(*, organization, workspace, name):
    return AIModel.all_objects.create(
        user_model_id=name,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )


def _make_metric(model, *, name="Quality metric", tags=None):
    return Metric.objects.create(
        name=name,
        text_prompt="Score the response quality.",
        criteria_breakdown=["Score the response quality."],
        model=model,
        metric_type=Metric.MetricTypes.WHOLE_USER_OUTPUT,
        evaluation_type=Metric.EvalMetricTypes.EVAL_OUTPUT,
        datasets=[{"environment": "Production", "model_version": "v1"}],
        tags=tags or ["quality:good", "quality:bad"],
    )


def _metric_payload(**overrides):
    payload = {
        "name": "Updated quality metric",
        "prompt": "Score the response quality.",
        "metric_type": 1,
        "evaluation_type": "EVALUATE_CHAT",
        "datasets": [{"environment": "Production", "model_version": "v1"}],
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestMetricContracts:
    def test_create_metric_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/model-hub/custom-metric/create/",
            {
                "model_id": str(uuid.uuid4()),
                "name": "Quality metric",
                "prompt": "Check answer quality",
                "metric_type": "boolean",
                "evaluation_type": "llm",
                "datasets": [],
                "modelId": "legacy camel alias",
            },
            format="json",
        )

        _assert_unknown_field(response, "modelId")

    def test_edit_metric_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/model-hub/custom-metric/update/",
            {
                "id": str(uuid.uuid4()),
                "name": "Quality metric",
                "prompt": "Check answer quality",
                "metric_type": "boolean",
                "evaluation_type": "llm",
                "datasets": [],
                "metricType": "legacy camel alias",
            },
            format="json",
        )

        _assert_unknown_field(response, "metricType")

    def test_test_metric_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/model-hub/custom-metric/test/",
            {
                "prompt": "Check answer quality",
                "promptText": "legacy camel alias",
            },
            format="json",
        )

        _assert_unknown_field(response, "promptText")

    def test_test_metric_returns_cached_prompt_without_prompt_validator(
        self, auth_client
    ):
        prompt = "Check groundedness"
        cached_prompt = "Suggested groundedness prompt"
        PromptChecker.objects.create(
            deleted=False,
            user_prompt=prompt,
            ai_prompt=cached_prompt,
            ambiguity=False,
            explanation="cached",
        )

        with patch("model_hub.views.metric.PromptValidator") as mock_prompt_validator:
            response = auth_client.post(
                "/model-hub/custom-metric/test/",
                {"prompt": prompt},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "success", "prompts": cached_prompt}
        mock_prompt_validator.assert_not_called()

    def test_test_metric_persists_prompt_validator_success(self, auth_client):
        prompt = "Check whether the answer is grounded in the supplied context."
        suggested_prompt = "Evaluate whether the answer is grounded in context."
        explanation = "The prompt is specific enough."

        with patch("model_hub.views.metric.PromptValidator") as mock_prompt_validator:
            validator = mock_prompt_validator.return_value
            validator.is_valid_prompt.return_value = {
                "is_ambiguity": False,
                "explanation": explanation,
                "prompts": suggested_prompt,
            }

            response = auth_client.post(
                "/model-hub/custom-metric/test/",
                {"prompt": prompt},
                format="json",
            )
            cached_response = auth_client.post(
                "/model-hub/custom-metric/test/",
                {"prompt": prompt},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "success", "prompts": suggested_prompt}
        assert cached_response.status_code == status.HTTP_200_OK
        assert cached_response.json() == {
            "status": "success",
            "prompts": suggested_prompt,
        }
        mock_prompt_validator.assert_called_once_with()
        validator.is_valid_prompt.assert_called_once_with(prompt)

        checker = PromptChecker.objects.get(user_prompt=prompt)
        assert checker.ai_prompt == suggested_prompt
        assert checker.explanation == explanation
        assert checker.ambiguity is False
        assert checker.deleted is False

    def test_test_metric_preserves_prompt_validator_api_exception_status(
        self, auth_client
    ):
        prompt = "Check answer usefulness."

        with patch(
            "model_hub.views.metric.PromptValidator",
            side_effect=FeatureUnavailable("PromptValidator"),
        ):
            response = auth_client.post(
                "/model-hub/custom-metric/test/",
                {"prompt": prompt},
                format="json",
            )

        assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
        body = response.json()
        assert body["status"] is False
        assert body["error"]["code"] == "ENTITLEMENT_DENIED"
        assert body["upgrade_required"] is True
        assert not PromptChecker.objects.filter(user_prompt=prompt).exists()

    @patch("model_hub.views.metric.check_valid_metrics", return_value=(True, True))
    def test_create_metric_uses_scoped_model(
        self, mock_check_valid_metrics, auth_client, user, workspace
    ):
        model = _make_model(
            organization=user.organization,
            workspace=workspace,
            name="active metric model",
        )
        other_workspace = Workspace.no_workspace_objects.create(
            name="Hidden metric workspace",
            organization=user.organization,
            is_active=True,
            created_by=user,
        )
        hidden_model = _make_model(
            organization=user.organization,
            workspace=other_workspace,
            name="hidden metric model",
        )

        response = auth_client.post(
            "/model-hub/custom-metric/create/",
            _metric_payload(model_id=str(model.id), name="Created metric"),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert Metric.objects.filter(model=model, name="Created metric").exists()
        mock_check_valid_metrics.assert_called_once()

        hidden_response = auth_client.post(
            "/model-hub/custom-metric/create/",
            _metric_payload(model_id=str(hidden_model.id), name="Hidden metric"),
            format="json",
        )

        assert hidden_response.status_code == status.HTTP_404_NOT_FOUND
        assert not Metric.objects.filter(
            model=hidden_model, name="Hidden metric"
        ).exists()
        mock_check_valid_metrics.assert_called_once()

    def test_metric_read_update_and_tag_options_are_workspace_scoped(
        self, auth_client, user, workspace
    ):
        model = _make_model(
            organization=user.organization,
            workspace=workspace,
            name="active custom metric model",
        )
        metric = _make_metric(model, name="Active metric")
        other_workspace = Workspace.no_workspace_objects.create(
            name="Hidden metric workspace",
            organization=user.organization,
            is_active=True,
            created_by=user,
        )
        hidden_model = _make_model(
            organization=user.organization,
            workspace=other_workspace,
            name="hidden custom metric model",
        )
        hidden_metric = _make_metric(hidden_model, name="Hidden metric")

        list_response = auth_client.get(f"/model-hub/custom-metric/{model.id}/")
        assert list_response.status_code == status.HTTP_200_OK
        assert list_response.json()["count"] == 1
        assert list_response.json()["results"][0]["id"] == str(metric.id)

        all_response = auth_client.get(f"/model-hub/custom-metric/all/{model.id}/")
        assert all_response.status_code == status.HTTP_200_OK
        assert [row["id"] for row in all_response.json()["metrics"]] == [str(metric.id)]

        tag_response = auth_client.get(
            f"/model-hub/custom-metric/tag-options/{metric.id}/"
        )
        assert tag_response.status_code == status.HTTP_200_OK
        assert tag_response.json() == [
            {"label": "quality:bad", "value": "quality:bad"},
            {"label": "quality:good", "value": "quality:good"},
        ]

        update_response = auth_client.post(
            "/model-hub/custom-metric/update/",
            _metric_payload(id=str(metric.id), name="Renamed active metric"),
            format="json",
        )
        assert update_response.status_code == status.HTTP_200_OK
        metric.refresh_from_db()
        assert metric.name == "Renamed active metric"

        for url in [
            f"/model-hub/custom-metric/{hidden_model.id}/",
            f"/model-hub/custom-metric/all/{hidden_model.id}/",
            f"/model-hub/custom-metric/tag-options/{hidden_metric.id}/",
        ]:
            response = auth_client.get(url)
            assert response.status_code == status.HTTP_404_NOT_FOUND

        hidden_update = auth_client.post(
            "/model-hub/custom-metric/update/",
            _metric_payload(id=str(hidden_metric.id), name="Leaked metric update"),
            format="json",
        )
        assert hidden_update.status_code == status.HTTP_404_NOT_FOUND
        hidden_metric.refresh_from_db()
        assert hidden_metric.name == "Hidden metric"
