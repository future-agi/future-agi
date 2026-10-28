"""HTTP surface of "Fix my agent": the user's runs, and the engine's calls from its sandbox."""

from django.http import HttpResponse
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.authentication import AgentFixEngineAuthentication
from simulate.models import AgentFixCandidate, AgentFixRun, HostedHarnessJob
from simulate.pydantic_schemas.agent_fix import (
    CandidateCreated,
    CheckRunRequest,
    EngineReport,
)
from simulate.selectors.agent_fix import (
    check_run_status,
    engine_brief,
    fix_runs_for_environment,
)
from simulate.serializers.agent_fix import (
    AgentFixLaunchSerializer,
    AgentFixListQuerySerializer,
    AgentFixMakeDefaultSerializer,
    AgentFixRunSerializer,
)
from simulate.services import agent_fix_engine
from simulate.services.agent_fix_runs import (
    launch_fix_run,
    make_candidate_default,
    request_stop,
)
from simulate.services.harness_provider import request_organization, scope_jobs
from simulate.services.hosted_harness import HostedHarnessError
from tfc.ee_gating import EEFeature, check_ee_feature
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import EmptyRequestSerializer


def _error_response(exc):
    if isinstance(exc, HostedHarnessError):
        return Response(exc.as_dict(), status=exc.status_code)
    if isinstance(exc, PydanticValidationError):
        return Response(
            {"error": "validation_error", "message": str(exc), "retryable": False},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _not_found():
    return Response(
        {"error": "not_found", "message": "Not found", "retryable": False},
        status=status.HTTP_404_NOT_FOUND,
    )


class AgentFixRunViewSet(viewsets.ViewSet):
    """A user's "Fix my agent" runs: start one from a finished run, follow it, stop it,
    and make a proven candidate the environment's default."""

    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        check_ee_feature(
            EEFeature.FIX_MY_AGENT, org_id=str(request_organization(request).id)
        )

    def handle_exception(self, exc):
        return _error_response(exc) or super().handle_exception(exc)

    def _jobs(self, request):
        return scope_jobs(
            HostedHarnessJob.no_workspace_objects.filter(
                organization=request_organization(request), deleted=False
            ),
            request,
        )

    def _fix_run(self, request, pk):
        return (
            AgentFixRun.no_workspace_objects.filter(
                id=pk, environment__in=self._jobs(request)
            )
            .select_related("environment")
            .prefetch_related("candidates__agent_version")
            .first()
        )

    @validated_request(
        query_serializer=AgentFixListQuerySerializer,
        responses={200: AgentFixRunSerializer(many=True)},
    )
    def list(self, request):
        environment = (
            self._jobs(request)
            .filter(
                id=request.validated_query_data["environment_id"],
                environment__isnull=True,
            )
            .first()
        )
        if environment is None:
            return _not_found()
        runs = fix_runs_for_environment(environment)[:50]
        return Response(AgentFixRunSerializer(runs, many=True).data)

    @validated_request(
        request_serializer=AgentFixLaunchSerializer,
        responses={201: AgentFixRunSerializer},
        reject_unknown_fields=True,
    )
    def create(self, request):
        data = request.validated_data
        source_run = (
            self._jobs(request)
            .filter(id=data["source_run_id"], environment__isnull=False)
            .select_related("environment")
            .first()
        )
        if source_run is None:
            return _not_found()
        fix_run = launch_fix_run(
            source_run,
            target_scenario_keys=data["target_scenario_keys"],
            surfaces=data["surfaces"],
            locked_paths=data["locked_paths"],
            budget_usd=data["budget_usd"],
            max_minutes=data["max_minutes"],
            user=request.user,
        )
        return Response(
            AgentFixRunSerializer(fix_run).data, status=status.HTTP_201_CREATED
        )

    @validated_request(responses={200: AgentFixRunSerializer})
    def retrieve(self, request, pk=None):
        fix_run = self._fix_run(request, pk)
        if fix_run is None:
            return _not_found()
        return Response(AgentFixRunSerializer(fix_run).data)

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={200: AgentFixRunSerializer},
    )
    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        fix_run = self._fix_run(request, pk)
        if fix_run is None:
            return _not_found()
        request_stop(fix_run)
        return Response(AgentFixRunSerializer(self._fix_run(request, pk)).data)

    def _candidate(self, request, pk, candidate_id):
        fix_run = self._fix_run(request, pk)
        if fix_run is None:
            return None
        return (
            AgentFixCandidate.no_workspace_objects.filter(
                fix_run=fix_run, id=candidate_id
            )
            .select_related("fix_run__environment", "agent_version")
            .first()
        )

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={200: AgentFixMakeDefaultSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"candidates/(?P<candidate_id>[0-9a-f-]{36})/make-default",
    )
    def make_default(self, request, pk=None, candidate_id=None):
        candidate = self._candidate(request, pk, candidate_id)
        if candidate is None:
            return _not_found()
        outcome = make_candidate_default(candidate, user=request.user)
        return Response({"outcome": outcome})

    @action(
        detail=True,
        methods=["get"],
        url_path=r"candidates/(?P<candidate_id>[0-9a-f-]{36})/patch",
    )
    def patch(self, request, pk=None, candidate_id=None):
        """The candidate's change as a git patch on the base version, to apply by hand."""
        candidate = self._candidate(request, pk, candidate_id)
        if candidate is None:
            return _not_found()
        body = agent_fix_engine.load_object(candidate.patch_object_key) or b""
        response = HttpResponse(body, content_type="text/x-diff")
        response["Content-Disposition"] = (
            f'attachment; filename="fix-{candidate.fix_run_id}-attempt-{candidate.attempt}.patch"'
        )
        return response


class AgentFixEngineViewSet(viewsets.ViewSet):
    """The calls the improvement engine makes from its sandbox, for its one fix run."""

    authentication_classes = [AgentFixEngineAuthentication]
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        return _error_response(exc) or super().handle_exception(exc)

    @property
    def _fix_run(self) -> AgentFixRun:
        return self.request.auth

    @action(detail=True, methods=["get"])
    def brief(self, request, pk=None):
        return Response(engine_brief(self._fix_run).model_dump(mode="json"))

    @action(detail=True, methods=["get"])
    def source(self, request, pk=None):
        body = agent_fix_engine.source_archive(self._fix_run)
        return HttpResponse(body, content_type="application/gzip")

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def candidates(self, request, pk=None):
        tree, patch = request.FILES.get("tree"), request.FILES.get("patch")
        if tree is None or patch is None:
            raise HostedHarnessError(
                "fix_candidate_incomplete", "send the candidate's tree and patch"
            )
        try:
            attempt = int(request.data.get("attempt", ""))
        except (TypeError, ValueError):
            attempt = 0
        if not 1 <= attempt <= 32767:
            raise HostedHarnessError(
                "fix_candidate_invalid", "attempt must be a whole number from 1"
            )
        candidate, status_ = agent_fix_engine.add_candidate(
            self._fix_run,
            attempt=attempt,
            summary=str(request.data.get("summary", "")),
            tree=tree.read(),
            patch=patch.read(),
        )
        created = CandidateCreated(
            candidate_id=str(candidate.id),
            agent_version_id=str(candidate.agent_version_id),
            preflight_status=status_,
        )
        return Response(created.model_dump(mode="json"), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="check-runs")
    def check_runs(self, request, pk=None):
        run = agent_fix_engine.start_check_run(
            self._fix_run, CheckRunRequest.model_validate(request.data)
        )
        return Response({"run_id": str(run.id)}, status=status.HTTP_202_ACCEPTED)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"check-runs/(?P<run_id>[0-9a-f-]{36})",
    )
    def check_run(self, request, pk=None, run_id=None):
        run = HostedHarnessJob.no_workspace_objects.filter(
            id=run_id, fix_run=self._fix_run
        ).first()
        if run is None:
            return _not_found()
        return Response(check_run_status(run).model_dump(mode="json"))

    @action(detail=True, methods=["post"])
    def report(self, request, pk=None):
        fix_run = agent_fix_engine.apply_report(
            self._fix_run, EngineReport.model_validate(request.data)
        )
        return Response({"state": fix_run.state})

    @action(detail=True, methods=["get", "put"])
    def checkpoint(self, request, pk=None):
        if request.method == "PUT":
            agent_fix_engine.store_checkpoint(self._fix_run, request.body)
            return Response(status=status.HTTP_204_NO_CONTENT)
        body = agent_fix_engine.load_object(self._fix_run.checkpoint_key)
        if body is None:
            return _not_found()
        return HttpResponse(body, content_type="application/json")
