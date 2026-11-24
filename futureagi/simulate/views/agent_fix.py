"""HTTP surface of "Fix my agent": the user's runs, and the engine's calls from its sandbox."""

from django.conf import settings
from django.http import HttpResponse
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import is_browser_session
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
    fix_lineage,
    fix_runs_for_environment,
    fix_runs_for_read,
    launch_options,
)
from simulate.selectors.agent_fix_issue import jobs_of_epochs
from simulate.selectors.agent_fix_outside import attempt_verdict, coding_agent_brief
from simulate.selectors.runs import user_runs
from simulate.serializers.agent_fix import (
    AgentFixAttemptVerdictSerializer,
    AgentFixCandidateCreatedSerializer,
    AgentFixCandidateUploadSerializer,
    AgentFixCheckRunCreatedSerializer,
    AgentFixCheckRunRequestSerializer,
    AgentFixCodingAgentAttemptSerializer,
    AgentFixCodingAgentBriefQuerySerializer,
    AgentFixCodingAgentBriefSerializer,
    AgentFixEngineReportSerializer,
    AgentFixEngineStateSerializer,
    AgentFixLaunchOptionsQuerySerializer,
    AgentFixLaunchOptionsSerializer,
    AgentFixLaunchSerializer,
    AgentFixListQuerySerializer,
    AgentFixMakeDefaultSerializer,
    AgentFixPullRequestSerializer,
    AgentFixRunDetailSerializer,
    AgentFixRunListSerializer,
    AgentFixRunSerializer,
    AgentFixShipSerializer,
)
from simulate.services import agent_fix_engine, agent_fix_models
from simulate.services.agent_fix_github import open_pull_request
from simulate.services.agent_fix_outside import submit_outside_attempt
from simulate.services.agent_fix_runs import (
    launch_fix_run,
    make_candidate_default,
    request_stop,
    retry_fix_run,
)
from simulate.services.harness_provider import (
    _usage_limit_response,
    request_organization,
    scope_jobs,
)
from simulate.services.harness_usage import usage_limit_error
from simulate.services.hosted_harness import HostedHarnessError
from tfc.ee_gating import EEFeature, check_ee_feature
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import EmptyRequestSerializer
from tfc.utils.pagination import ExtendedPageNumberPagination


def _error_response(exc):
    if isinstance(exc, HostedHarnessError):
        return Response(exc.as_dict(), status=exc.status_code)
    if isinstance(exc, PydanticValidationError):
        # The fields stay in the details for a machine; the message is for a person.
        return _error_response(
            HostedHarnessError(
                "validation_error",
                "Some settings are not valid. Check them and try again.",
                status_code=400,
                details={
                    "fields": [
                        ".".join(str(part) for part in error["loc"])
                        for error in exc.errors()
                    ]
                },
            )
        )
    return _usage_limit_response(exc)


def _engine_error_response(exc):
    # The engine reads one error shape; a plan limit is named in it, apart from the fix's budget.
    return _error_response(usage_limit_error(exc) or exc)


def _not_found():
    return _error_response(
        HostedHarnessError("not_found", "Not found", status_code=404)
    )


class _FixRunPages(ExtendedPageNumberPagination):
    page_size = 25


class AgentFixRunViewSet(viewsets.ViewSet):
    """A user's epochs: start one from a finished run, follow it, stop it,
    and ship its winner: the environment's default, or a GitHub pull request."""

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
            fix_runs_for_read(
                AgentFixRun.no_workspace_objects.filter(
                    id=pk, environment__in=self._jobs(request)
                )
            )
            .select_related("environment")
            .first()
        )

    def _source_run(self, request, test_execution_id):
        """A user run of one of the caller's environments, by its test execution. An
        Epoch's own check run or a verify run is never one: an Epoch from it would see the
        cases its own fix held back."""
        return (
            self._jobs(request)
            .filter(
                user_runs(),
                test_execution_id=test_execution_id,
                environment__isnull=False,
            )
            .select_related("environment__environment_version", "environment_version")
            .first()
        )

    def _epoch(self, request, fix_run, serializer=AgentFixRunSerializer, **context):
        """An epoch's read. Only a person's signed-in session sees which cases were held
        back, so the Epoch page can mark them; a coding agent, the CLI or MCP never."""
        context.setdefault("held_back_visible", is_browser_session(request))
        return serializer(fix_run, context=context).data

    @validated_request(
        query_serializer=AgentFixListQuerySerializer,
        responses={200: AgentFixRunListSerializer},
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
        paginator = _FixRunPages()
        page = paginator.paginate_queryset(
            fix_runs_for_environment(
                environment, states=request.validated_query_data.get("state")
            ),
            request,
            view=self,
        )
        return paginator.get_paginated_response(
            AgentFixRunSerializer(
                page,
                many=True,
                context={
                    "issue_links": jobs_of_epochs(page or []),
                    "held_back_visible": is_browser_session(request),
                },
            ).data
        )

    @validated_request(
        query_serializer=AgentFixLaunchOptionsQuerySerializer,
        responses={200: AgentFixLaunchOptionsSerializer},
    )
    @action(detail=False, methods=["get"], url_path="launch-options")
    def launch_options(self, request):
        """What the launch form shows and prefills for a finished run, and what would
        refuse the launch now."""
        source_run = self._source_run(
            request, request.validated_query_data["source_run_id"]
        )
        if source_run is None:
            return _not_found()
        return Response(
            AgentFixLaunchOptionsSerializer(launch_options(source_run)).data
        )

    @validated_request(
        query_serializer=AgentFixCodingAgentBriefQuerySerializer,
        responses={200: AgentFixCodingAgentBriefSerializer},
    )
    @action(detail=False, methods=["get"], url_path="coding-agent-brief")
    def brief(self, request):
        """What a person's own coding agent reads to fix a finished run: its failing cases,
        never a held-back one, the agent's files, the rules, and how to submit."""
        if not settings.AGENT_FIX_OUTSIDE_ATTEMPTS_ENABLED:
            return _not_found()
        source_run = self._source_run(
            request, request.validated_query_data["source_run_id"]
        )
        if source_run is None:
            return _not_found()
        return Response(
            AgentFixCodingAgentBriefSerializer(coding_agent_brief(source_run)).data
        )

    @validated_request(
        request_serializer=AgentFixCodingAgentAttemptSerializer,
        responses={201: AgentFixRunSerializer},
        reject_unknown_fields=True,
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="coding-agent-attempts",
        parser_classes=[MultiPartParser, FormParser],
    )
    def submit_attempt(self, request):
        """Submit the tree a person's own coding agent made for a finished run. Future AGI
        verifies it as a short Epoch that calls no model; read its verdict."""
        if not settings.AGENT_FIX_OUTSIDE_ATTEMPTS_ENABLED:
            return _not_found()
        data = request.validated_data
        source_run = self._source_run(request, data["source_run_id"])
        if source_run is None:
            return _not_found()
        fix_run = submit_outside_attempt(
            source_run,
            tree=data["tree"].read(),
            summary=data["summary"],
            sent_base_commit=data["base_commit"],
            locked_paths=data.get("locked_paths"),
            budget_credits=data.get("budget_credits"),
            user=request.user,
        )
        return Response(
            self._epoch(
                request, self._fix_run(request, fix_run.id), held_back_visible=False
            ),
            status=status.HTTP_201_CREATED,
        )

    @validated_request(responses={200: AgentFixAttemptVerdictSerializer})
    @action(detail=True, methods=["get"])
    def verdict(self, request, pk=None):
        """An outside attempt's verdict: each tier's status, the static and target tiers'
        detail, and its must-pass regressions, none on a held-back case."""
        if not settings.AGENT_FIX_OUTSIDE_ATTEMPTS_ENABLED:
            return _not_found()
        fix_run = self._fix_run(request, pk)
        if fix_run is None:
            return _not_found()
        return Response(AgentFixAttemptVerdictSerializer(attempt_verdict(fix_run)).data)

    @validated_request(
        request_serializer=AgentFixLaunchSerializer,
        responses={201: AgentFixRunSerializer},
        reject_unknown_fields=True,
    )
    def create(self, request):
        data = request.validated_data
        source_run = self._source_run(request, data["source_run_id"])
        if source_run is None:
            return _not_found()
        fix_run = launch_fix_run(
            source_run,
            target_scenario_keys=data["target_scenario_keys"],
            protected_scenario_keys=data["protected_scenario_keys"],
            surfaces=data["surfaces"],
            locked_paths=data["locked_paths"],
            budget_usd=data["budget_credits"] / 100,
            max_minutes=data.get("max_minutes"),
            user=request.user,
            allowed_models=data.get("allowed_models"),
            reply_latency_margin_pct=data.get("reply_latency_margin_pct"),
            cost_margin_pct=data.get("cost_margin_pct"),
            cost_limit_usd=data.get("cost_limit_usd"),
        )
        return Response(
            self._epoch(request, self._fix_run(request, fix_run.id)),
            status=status.HTTP_201_CREATED,
        )

    @validated_request(responses={200: AgentFixRunDetailSerializer})
    def retrieve(self, request, pk=None):
        fix_run = self._fix_run(request, pk)
        if fix_run is None:
            return _not_found()
        return Response(self._epoch(request, fix_run, AgentFixRunDetailSerializer))

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={201: AgentFixRunSerializer},
    )
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """Start a failed fix again: a new fix with the same request. The check runs the
        failed one finished are reused, so the user does not pay for them twice."""
        fix_run = self._fix_run(request, pk)
        if fix_run is None:
            return _not_found()
        retried = retry_fix_run(fix_run, user=request.user)
        return Response(
            self._epoch(request, self._fix_run(request, retried.id)),
            status=status.HTTP_201_CREATED,
        )

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
        return Response(self._epoch(request, self._fix_run(request, pk)))

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

    @staticmethod
    def _override_reason(request) -> str:
        """The reason to ship a candidate its checks refused. Only a person decides that,
        in Future AGI: an API key, the CLI or MCP is refused."""
        reason: str = request.validated_data["override_reason"]
        if reason and not is_browser_session(request):
            raise HostedHarnessError(
                "fix_override_needs_person",
                "A person decides whether to ship a candidate its checks refused. Open"
                " the Epoch in Future AGI to ship it.",
                status_code=403,
            )
        return reason

    @validated_request(
        request_serializer=AgentFixShipSerializer,
        responses={200: AgentFixMakeDefaultSerializer},
        reject_unknown_fields=True,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"candidates/(?P<candidate_id>[0-9a-f-]{36})/make-default",
    )
    def make_default(self, request, pk=None, candidate_id=None):
        reason = self._override_reason(request)
        candidate = self._candidate(request, pk, candidate_id)
        if candidate is None:
            return _not_found()
        outcome = make_candidate_default(
            candidate, user=request.user, override_reason=reason
        )
        return Response({"outcome": outcome})

    @validated_request(
        request_serializer=AgentFixShipSerializer,
        responses={200: AgentFixPullRequestSerializer},
        reject_unknown_fields=True,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"candidates/(?P<candidate_id>[0-9a-f-]{36})/pull-request",
    )
    def pull_request(self, request, pk=None, candidate_id=None):
        """A GitHub agent's winner, or a candidate only its must-pass check refused with
        the user's reason, opened once as a pull request on its repository."""
        reason = self._override_reason(request)
        candidate = self._candidate(request, pk, candidate_id)
        if candidate is None:
            return _not_found()
        url = open_pull_request(candidate, user=request.user, override_reason=reason)
        return Response({"pull_request_url": url})

    @swagger_auto_schema(
        responses={
            200: openapi.Response(
                description="The candidate's change as a git patch.",
                schema=openapi.Schema(type=openapi.TYPE_STRING),
            )
        }
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=r"candidates/(?P<candidate_id>[0-9a-f-]{36})/patch",
    )
    # Not named `patch`: a viewset method by that name answers HTTP PATCH on every route.
    def candidate_patch(self, request, pk=None, candidate_id=None):
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
    """The calls the improvement engine makes from its sandbox, for its one fix run.

    Fields these routes do not know are ignored, not refused: the engine template and the
    platform ship apart, and a newer template must still be heard by an older platform."""

    authentication_classes = [AgentFixEngineAuthentication]
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        return _engine_error_response(exc) or super().handle_exception(exc)

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

    @action(detail=True, methods=["get"])
    def submitted(self, request, pk=None):
        """The tree an outside attempt verifies, `source/`-rooted like ``source``."""
        body = agent_fix_engine.submitted_archive(self._fix_run)
        if body is None:
            return _not_found()
        return HttpResponse(body, content_type="application/gzip")

    @validated_request(
        request_serializer=AgentFixCandidateUploadSerializer,
        responses={201: AgentFixCandidateCreatedSerializer},
    )
    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def candidates(self, request, pk=None):
        data = request.validated_data
        candidate, status_ = agent_fix_engine.add_candidate(
            self._fix_run,
            attempt=data["attempt"],
            summary=data["summary"],
            tree=data["tree"].read(),
        )
        created = CandidateCreated(
            candidate_id=str(candidate.id),
            agent_version_id=str(candidate.agent_version_id),
            preflight_status=status_,
        )
        return Response(created.model_dump(mode="json"), status=status.HTTP_201_CREATED)

    @validated_request(
        request_serializer=AgentFixCheckRunRequestSerializer,
        responses={202: AgentFixCheckRunCreatedSerializer},
    )
    @action(detail=True, methods=["post"], url_path="check-runs")
    def check_runs(self, request, pk=None):
        run = agent_fix_engine.start_check_run(
            self._fix_run, CheckRunRequest.model_validate(request.validated_data)
        )
        return Response({"run_id": str(run.id)}, status=status.HTTP_202_ACCEPTED)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"check-runs/(?P<run_id>[0-9a-f-]{36})",
    )
    def check_run(self, request, pk=None, run_id=None):
        # A retry reads the check runs the fixes it retries finished, as its own.
        run = (
            HostedHarnessJob.no_workspace_objects.filter(
                id=run_id, fix_run_id__in=fix_lineage(self._fix_run)
            )
            .select_related("fix_run")
            .first()
        )
        if run is None:
            return _not_found()
        return Response(check_run_status(run, self._fix_run).model_dump(mode="json"))

    @validated_request(
        request_serializer=AgentFixEngineReportSerializer,
        responses={200: AgentFixEngineStateSerializer},
    )
    @action(detail=True, methods=["post"])
    def report(self, request, pk=None):
        fix_run = agent_fix_engine.apply_report(
            self._fix_run, EngineReport.model_validate(request.validated_data)
        )
        return Response({"state": fix_run.state})


class _GatewayReply(BaseContentNegotiation):
    """A model reply is the gateway's bytes as sent, so the client's Accept header is not
    negotiated: a client that asks for a stream gets the service's refusal, not a 406."""

    def select_parser(self, request, parsers):
        return parsers[0]

    def select_renderer(self, request, renderers, format_suffix=None):
        return renderers[0], renderers[0].media_type


class AgentFixModelView(APIView):
    """The engine's model calls, at the path an OpenAI-compatible client builds from its
    base URL: no trailing slash, so it is routed on its own, not by the router."""

    authentication_classes = [AgentFixEngineAuthentication]
    permission_classes = [IsAuthenticated]
    content_negotiation_class = _GatewayReply

    def handle_exception(self, exc):
        return _engine_error_response(exc) or super().handle_exception(exc)

    @swagger_auto_schema(
        # An OpenAI chat completion request, forwarded as sent: checked in the service.
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT, additional_properties=True
        ),
        runtime_request_validation=True,
        responses={200: "the gateway's reply, as it sent it"},
    )
    def post(self, request, pk=None):
        reply = agent_fix_models.complete(request.auth, request.body)
        return HttpResponse(
            reply.body, status=reply.status_code, content_type=reply.content_type
        )
