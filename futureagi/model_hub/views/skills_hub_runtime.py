"""Shared runtime projections; do not duplicate routes for skills and contexts."""

from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.serializers.skills_hub import SkillsError
from model_hub.serializers.skills_hub_runtime import (
    LatestLoadQuery,
    LatestLoadResult,
    RuntimeComparisonQuery,
    RuntimeComparisonResult,
    RuntimeFilterValuesQuery,
    RuntimeFilterValuesResult,
    RuntimeUsageQuery,
    RuntimeUsageResult,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.runtime_loads import latest_load
from model_hub.services.skills_hub.runtime_reads import filter_values
from model_hub.views.skills_hub import SkillsView, contract, domain_error


def invalid_runtime_request(errors):
    return domain_error(SkillsHubError(
        "invalid_runtime_query", "Check the runtime query.", status=400,
    ))


class SkillRuntimeUsageView(SkillsView):
    @contract(RuntimeUsageResult, query=RuntimeUsageQuery,
              operation_id="model-hub_skills_runtime_usage_list",
              responses={503: SkillsError}, validation_error_response=invalid_runtime_request)
    def get(self, request):
        """Shared, cursor-paged runtime cohorts; loads never imply use."""
        from model_hub.services.skills_hub.runtime_api import usage

        return self.output(request, RuntimeUsageResult, usage(
            principal=request.skill_principal, workspace=request.workspace,
            query=request.validated_query_data,
        ))


class SkillRuntimeComparisonView(SkillsView):
    @contract(RuntimeComparisonResult, query=RuntimeComparisonQuery,
              operation_id="model-hub_skills_runtime_comparison_list",
              responses={503: SkillsError}, validation_error_response=invalid_runtime_request)
    def get(self, request):
        """Compare a label move or two explicit windows, same agents first."""
        from model_hub.services.skills_hub.runtime_api import comparison

        return self.output(request, RuntimeComparisonResult, comparison(
            principal=request.skill_principal, workspace=request.workspace,
            query=request.validated_query_data,
        ))


class SkillFilterValuesView(SkillsView):
    @contract(RuntimeFilterValuesResult, query=RuntimeFilterValuesQuery,
              responses={503: SkillsError}, validation_error_response=invalid_runtime_request)
    def get(self, request):
        """Page through live runtime filter values in one authorized project."""
        return self.output(request, RuntimeFilterValuesResult, filter_values(
            principal=request.skill_principal, workspace=request.workspace,
            query=request.validated_query_data,
        ))


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
