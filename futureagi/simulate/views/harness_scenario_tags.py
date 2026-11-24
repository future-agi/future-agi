from uuid import UUID

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.authentication import viewer_denied
from simulate.models import HostedHarnessJob
from simulate.serializers.harness_scenario_tags import (
    HarnessEnvironmentTagsSerializer,
    HarnessScenarioTagsChangeResultSerializer,
    HarnessScenarioTagsChangeSerializer,
    HarnessScenarioTagsResultSerializer,
    HarnessScenarioTagsSerializer,
)
from simulate.services import harness_scenario_tags
from simulate.services.harness_provider import request_organization, scope_jobs
from simulate.services.hosted_harness import HostedHarnessError
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope


class HarnessScenarioTagViewSet(viewsets.ViewSet):
    """Tags on an environment's scenarios: free-text labels that never change the versioned suite."""

    permission_classes = [IsAuthenticated]

    def _environment(self, request, environment_id):
        try:
            identifier = UUID(str(environment_id))
        except ValueError:
            return None
        return (
            scope_jobs(
                HostedHarnessJob.no_workspace_objects.filter(
                    organization=request_organization(request),
                    environment__isnull=True,
                    deleted=False,
                ),
                request,
            )
            .filter(id=identifier)
            .first()
        )

    def _not_found(self):
        return Response(
            build_error_envelope(
                "Environment not found", status_code=status.HTTP_404_NOT_FOUND
            ),
            status=status.HTTP_404_NOT_FOUND,
        )

    def handle_exception(self, exc):
        if isinstance(exc, HostedHarnessError):
            return Response(
                exc.as_dict(), status=exc.status_code, headers=exc.headers()
            )
        return super().handle_exception(exc)

    @validated_request(
        responses={200: HarnessEnvironmentTagsSerializer},
        operation_id="simulate_api_harness-environments_scenario_tags_list",
    )
    def list(self, request, environment_id=None):
        """Every tag the environment's scenarios carry."""
        environment = self._environment(request, environment_id)
        if environment is None:
            return self._not_found()
        return Response({"tags": harness_scenario_tags.environment_tags(environment)})

    @viewer_denied("change scenario tags")
    @validated_request(
        request_serializer=HarnessScenarioTagsChangeSerializer,
        responses={200: HarnessScenarioTagsChangeResultSerializer},
        request_methods=["POST"],
        reject_unknown_fields=True,
        operation_id="simulate_api_harness-environments_scenario_tags_change",
    )
    @action(detail=False, methods=["post"], url_path="change")
    def change(self, request, environment_id=None):
        """Add and remove tags on many scenarios at once."""
        environment = self._environment(request, environment_id)
        if environment is None:
            return self._not_found()
        body = request.validated_data
        changed = harness_scenario_tags.change_tags(
            environment,
            body["scenario_keys"],
            add=body.get("add") or [],
            remove=body.get("remove") or [],
        )
        return Response(
            {
                "scenarios": [
                    {"scenario_key": key, "tags": tags} for key, tags in changed.items()
                ],
                "tags": harness_scenario_tags.environment_tags(environment),
            }
        )

    @viewer_denied("change scenario tags")
    @validated_request(
        request_serializer=HarnessScenarioTagsSerializer,
        responses={200: HarnessScenarioTagsResultSerializer},
        request_methods=["PUT"],
        reject_unknown_fields=True,
        operation_id="simulate_api_harness-environments_scenario_tags_set",
    )
    @action(
        detail=False,
        methods=["put"],
        url_path=r"scenarios/(?P<scenario_key>[^/]{1,255})",
    )
    def set_for_scenario(self, request, environment_id=None, scenario_key=None):
        """Replace one scenario's tags; an empty list clears them."""
        environment = self._environment(request, environment_id)
        if environment is None:
            return self._not_found()
        tags = harness_scenario_tags.set_tags(
            environment, scenario_key, request.validated_data["tags"]
        )
        return Response({"scenario_key": scenario_key, "tags": tags})
