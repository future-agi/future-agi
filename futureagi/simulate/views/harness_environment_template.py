from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.serializers.harness_environment import (
    HarnessEnvironmentTemplateDetailSerializer,
    HarnessEnvironmentTemplateListResponseSerializer,
)
from simulate.services.harness_templates import (
    system_template,
    system_templates,
    template_detail,
    template_summary,
)


class HarnessEnvironmentTemplateViewSet(viewsets.ViewSet):
    """The shared template library.

    Every user reads the same system templates and opens one read-only at ``environment_id``.
    Nothing is created by looking: editing or running one first asks for the caller's own copy
    (``harness-environments/{id}/copy/``).
    """

    permission_classes = [IsAuthenticated]
    lookup_field = "slug"
    lookup_value_regex = r"[-a-zA-Z0-9_]+"

    @swagger_auto_schema(responses={200: HarnessEnvironmentTemplateListResponseSerializer})
    def list(self, request):
        return Response(
            {
                "results": [
                    template_summary(job) for job in system_templates().order_by("name")
                ]
            }
        )

    @swagger_auto_schema(responses={200: HarnessEnvironmentTemplateDetailSerializer})
    def retrieve(self, request, slug=None):
        job = system_template(slug)
        if job is None:
            return Response(
                {"detail": "Template not found"}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(template_detail(job))
