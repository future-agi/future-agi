"""Human inspection of authorized active and historical parent locks."""

from uuid import UUID

from rest_framework.request import Request
from rest_framework.response import Response

from model_hub.serializers.skills_context_dependents import (
    ContextDependentsPage,
    DependentsQuery,
)
from model_hub.services.skills_hub.v12.releases import context_dependents
from model_hub.views.skills_context_foundation import contract
from model_hub.views.skills_hub import SkillsView


class SkillContextDependentsView(SkillsView):
    private_resource = True

    @contract(ContextDependentsPage, query=DependentsQuery)
    def get(self, request: Request, skill_id: UUID) -> Response:
        value = context_dependents(
            principal=request.skill_principal,
            skill_id=skill_id,
            **request.validated_query_data,
        )
        return self.output(request, ContextDependentsPage, value)
