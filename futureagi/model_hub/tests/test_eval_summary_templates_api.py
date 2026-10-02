import pytest
from rest_framework import status

from model_hub.views.eval_summary_templates import EvalSummaryTemplate


def _assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


@pytest.mark.django_db
class TestEvalSummaryTemplateResponseContracts:
    def test_list_returns_typed_result(self, auth_client, user):
        template = EvalSummaryTemplate.objects.create(
            name="Safety summary",
            description="Summarize safety issues",
            criteria="Group failures by cause",
            organization=user.organization,
        )

        response = auth_client.get("/model-hub/eval-summary-templates/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == {
            "templates": [
                {
                    "id": str(template.id),
                    "name": "Safety summary",
                    "description": "Summarize safety issues",
                    "criteria": "Group failures by cause",
                }
            ]
        }

    def test_create_returns_typed_template_result(self, auth_client):
        response = auth_client.post(
            "/model-hub/eval-summary-templates/",
            {
                "name": "Quality summary",
                "description": "Summarize quality issues",
                "criteria": "Group low scores by reason",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["name"] == "Quality summary"
        assert result["description"] == "Summarize quality issues"
        assert result["criteria"] == "Group low scores by reason"
        assert EvalSummaryTemplate.objects.filter(id=result["id"]).exists()

    def test_create_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/model-hub/eval-summary-templates/",
            {
                "name": "Quality summary",
                "description": "Summarize quality issues",
                "criteria": "Group low scores by reason",
                "templateName": "legacy camel alias",
            },
            format="json",
        )

        _assert_unknown_field(response, "templateName")

    def test_update_returns_typed_template_result(self, auth_client, user):
        template = EvalSummaryTemplate.objects.create(
            name="Old summary",
            description="Old description",
            criteria="Old criteria",
            organization=user.organization,
        )

        response = auth_client.put(
            f"/model-hub/eval-summary-templates/{template.id}/",
            {
                "name": "Updated summary",
                "description": "Updated description",
                "criteria": "Updated criteria",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == {
            "id": str(template.id),
            "name": "Updated summary",
            "description": "Updated description",
            "criteria": "Updated criteria",
        }

    def test_update_rejects_unknown_request_fields(self, auth_client, user):
        template = EvalSummaryTemplate.objects.create(
            name="Old summary",
            description="Old description",
            criteria="Old criteria",
            organization=user.organization,
        )

        response = auth_client.put(
            f"/model-hub/eval-summary-templates/{template.id}/",
            {
                "name": "Updated summary",
                "description": "Updated description",
                "criteria": "Updated criteria",
                "templateName": "legacy camel alias",
            },
            format="json",
        )

        _assert_unknown_field(response, "templateName")

    def test_delete_returns_typed_result(self, auth_client, user):
        template = EvalSummaryTemplate.objects.create(
            name="Delete summary",
            description="Delete description",
            criteria="Delete criteria",
            organization=user.organization,
        )

        response = auth_client.delete(
            f"/model-hub/eval-summary-templates/{template.id}/",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == {"deleted": True}
        assert not EvalSummaryTemplate.objects.filter(id=template.id).exists()
