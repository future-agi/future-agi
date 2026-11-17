"""Shared trace evidence projection, not one endpoint per Observe screen."""
from rest_framework.permissions import BasePermission, IsAuthenticated

from model_hub.serializers.skills_hub import SkillsError
from model_hub.services.skills_hub.v11.principals import (
    principal_from_request,
    require_scope_access,
)
from model_hub.views.skills_hub import SkillsView, contract
from model_hub.views.skills_hub_runtime import invalid_runtime_request
from tracer.serializers.trace_skills import TraceSkillsRequest, TraceSkillsResult
from tracer.services.filter_principal_context import (
    bind_request_my_annotations_principal,
)
from tracer.services.trace_skills import trace_skills_projection


class TraceProjectionPermission(BasePermission):
    def has_permission(self, request, view):
        # POST transports a bounded read; it must not require write permission.
        request.skill_principal = principal_from_request(request)
        require_scope_access(principal=request.skill_principal, write=False)
        return True


class TraceSkillsView(SkillsView):
    permission_classes = [IsAuthenticated, TraceProjectionPermission]

    @contract(TraceSkillsResult, body=TraceSkillsRequest, responses={503: SkillsError},
              validation_error_response=invalid_runtime_request)
    def post(self, request):
        query = dict(request.validated_data)
        query["filters"] = bind_request_my_annotations_principal(request, query["filters"])
        return self.output(request, TraceSkillsResult, trace_skills_projection(
            principal=request.skill_principal, workspace=request.workspace, query=query,
        ))
