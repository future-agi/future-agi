"""Shared runtime projections; do not duplicate routes for skills and contexts."""

from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.serializers.skills_hub import SkillsError
from model_hub.serializers.skills_hub_runtime import LatestLoadQuery, LatestLoadResult
from model_hub.services.skills_hub.runtime_loads import latest_load
from model_hub.views.skills_hub import SkillsView, contract


class SkillLoadsView(SkillsView):
    @contract(LatestLoadResult, query=LatestLoadQuery, responses={503: SkillsError})
    def get(self, request):
        """Read the newest load for Check tracing, with fetch diagnosis separately.

        A recorded load is client-reported delivery, never proof of skill use.
        Both resource kinds share this read and the caller's workspace scope.
        """
        query = request.validated_query_data
        resource = get_resource(
            principal=request.skill_principal,
            resource_id=query["resource_id"], kind=query["kind"],
        )
        return self.output(request, LatestLoadResult, latest_load(
            resource=resource, workspace=request.workspace, since=query["since"],
        ))
