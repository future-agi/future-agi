"""
CRUD for eval summary templates.

Saved summary templates that users can reuse across evaluations.
Each template belongs to an organization.

GET    /model-hub/eval-summary-templates/           — list all
POST   /model-hub/eval-summary-templates/           — create
PUT    /model-hub/eval-summary-templates/<id>/       — update
DELETE /model-hub/eval-summary-templates/<id>/       — delete
"""

import traceback
import uuid

import structlog
from django.db import models
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models import Organization
from model_hub.serializers.contracts import (
    EvalSummaryTemplateDeleteResponseSerializer,
    EvalSummaryTemplateListResponseSerializer,
    EvalSummaryTemplateMutationRequestSerializer,
    EvalSummaryTemplateResponseSerializer,
    MODEL_HUB_ERROR_RESPONSES,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class EvalSummaryTemplate(models.Model):
    """Reusable summary template for eval output formatting."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    criteria = models.TextField(
        help_text="The summary instructions to inject into the eval prompt",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="eval_summary_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "model_hub"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


class EvalSummaryTemplateListView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: EvalSummaryTemplateListResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request):
        org = getattr(request, "organization", None) or request.user.organization
        templates = EvalSummaryTemplate.objects.filter(organization=org)
        items = [
            {
                "id": str(t.id),
                "name": t.name,
                "description": t.description,
                "criteria": t.criteria,
            }
            for t in templates
        ]
        return self._gm.success_response({"templates": items})

    @validated_request(
        request_serializer=EvalSummaryTemplateMutationRequestSerializer,
        responses={
            200: EvalSummaryTemplateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        try:
            org = getattr(request, "organization", None) or request.user.organization
            name = request.validated_data.get("name", "").strip()
            description = request.validated_data.get("description", "").strip()
            criteria = request.validated_data.get("criteria", "").strip()

            if not name:
                return self._gm.bad_request("Name is required")
            if not criteria:
                return self._gm.bad_request("Criteria is required")

            template = EvalSummaryTemplate.objects.create(
                name=name,
                description=description,
                criteria=criteria,
                organization=org,
            )
            return self._gm.success_response(
                {
                    "id": str(template.id),
                    "name": template.name,
                    "description": template.description,
                    "criteria": template.criteria,
                }
            )
        except Exception as e:
            logger.error(
                f"Error creating summary template: {e}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class EvalSummaryTemplateDetailView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=EvalSummaryTemplateMutationRequestSerializer,
        responses={
            200: EvalSummaryTemplateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def put(self, request, template_id):
        try:
            org = getattr(request, "organization", None) or request.user.organization
            try:
                template = EvalSummaryTemplate.objects.get(
                    id=template_id, organization=org
                )
            except EvalSummaryTemplate.DoesNotExist:
                return self._gm.not_found("Template not found")

            name = request.validated_data.get("name")
            description = request.validated_data.get("description")
            criteria = request.validated_data.get("criteria")

            if name is not None:
                template.name = name.strip()
            if description is not None:
                template.description = description.strip()
            if criteria is not None:
                template.criteria = criteria.strip()

            template.save()
            return self._gm.success_response(
                {
                    "id": str(template.id),
                    "name": template.name,
                    "description": template.description,
                    "criteria": template.criteria,
                }
            )
        except Exception as e:
            logger.error(
                f"Error updating summary template: {e}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))

    @swagger_auto_schema(
        responses={
            200: EvalSummaryTemplateDeleteResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def delete(self, request, template_id):
        try:
            org = getattr(request, "organization", None) or request.user.organization
            try:
                template = EvalSummaryTemplate.objects.get(
                    id=template_id, organization=org
                )
            except EvalSummaryTemplate.DoesNotExist:
                return self._gm.not_found("Template not found")

            template.delete()
            return self._gm.success_response({"deleted": True})
        except Exception as e:
            logger.error(
                f"Error deleting summary template: {e}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))
