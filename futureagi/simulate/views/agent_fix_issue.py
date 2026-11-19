"""HTTP surface of Run Epoch from an Error Feed issue: the plan, the job from the issue's
traces to a running epoch, and each issue's newest job. The issue page and the `fi` CLI
read the same routes."""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.models import AgentFixIssueJob, HostedHarnessJob
from simulate.pydantic_schemas.agent_fix_issue import IssueLaunch
from simulate.selectors.agent_fix_issue import (
    default_environment,
    environment_blocker,
    is_open,
    issue_job_view,
    issue_sample,
    issue_scenarios,
    issue_scenarios_max,
    issue_view,
    jobs_of_issues,
    launch_defaults,
    requested_issue,
)
from simulate.selectors.evidence_import import evidence_project
from simulate.serializers.agent_fix_issue import (
    AgentFixIssueCreateSerializer,
    AgentFixIssueJobDetailSerializer,
    AgentFixIssuePlanQuerySerializer,
    AgentFixIssuePlanSerializer,
    AgentFixIssueRunSerializer,
    AgentFixIssueStatusListSerializer,
    AgentFixIssueStatusQuerySerializer,
)
from simulate.services.agent_fix_issue import (
    create_issue_job,
    run_issue_job,
    stop_issue_job,
)
from simulate.services.harness_environment import environment_name
from simulate.services.harness_provider import request_organization, scope_jobs
from simulate.services.hosted_harness_ingress import _public_base_url
from simulate.views.agent_fix import _error_response, _not_found
from tfc.ee_gating import EEFeature, check_ee_feature
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import EmptyRequestSerializer
from tracer.models.project import Project
from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.utils.workspace_scope import project_queryset_for_request


class AgentFixIssueJobViewSet(viewsets.ViewSet):
    """Run Epoch from an Error Feed issue: plan it, start it from the picked traces,
    follow it, run it after the review, and stop it before its epoch starts."""

    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        check_ee_feature(
            EEFeature.FIX_MY_AGENT, org_id=str(request_organization(request).id)
        )

    def handle_exception(self, exc):
        return _error_response(exc) or super().handle_exception(exc)

    def _environments(self, request):
        return scope_jobs(
            HostedHarnessJob.no_workspace_objects.filter(
                organization=request_organization(request),
                deleted=False,
                environment__isnull=True,
            ),
            request,
        )

    def _project(self, request, project_id) -> Project | None:
        project: Project | None = (
            project_queryset_for_request(request).filter(id=project_id).first()
        )
        return project

    def _issue(self, request, data):
        project = self._project(request, data["project_id"])
        return requested_issue(data["cluster_id"], project) if project else None

    def _environment(self, request, environment_id, issue) -> HostedHarnessJob | None:
        """The caller's environment that may take evidence from the issue's project."""
        environment: HostedHarnessJob | None = (
            self._environments(request).filter(id=environment_id).first()
        )
        if environment is None or not evidence_project(
            project_queryset_for_request(request), environment, issue.project_id
        ):
            return None
        return environment

    def _job(self, request, pk) -> AgentFixIssueJob | None:
        return (
            AgentFixIssueJob.no_workspace_objects.filter(
                id=pk, environment__in=self._environments(request)
            )
            .select_related(
                "environment",
                "evidence_import__source_issue__project",
                "baseline_run",
                "fix_run",
            )
            .first()
        )

    def _detail(self, request, pk, status_code: int = status.HTTP_200_OK) -> Response:
        """The job as its detail reads it now, or not found."""
        job = self._job(request, pk)
        if job is None:
            missing: Response = _not_found()
            return missing
        return Response(
            AgentFixIssueJobDetailSerializer(issue_job_view(job, detail=True)).data,
            status=status_code,
        )

    @validated_request(
        query_serializer=AgentFixIssuePlanQuerySerializer,
        responses={200: AgentFixIssuePlanSerializer},
    )
    @action(detail=False, methods=["get"])
    def plan(self, request):
        """What Run Epoch on an issue shows before anything is drafted: the environment,
        a sample of the issue's traces with why each was picked, and the launch form's
        defaults. It reads the picked traces' roots once, and calls no model."""
        data = request.validated_query_data
        issue = self._issue(request, data)
        if issue is None:
            return _not_found()
        if "environment_id" in data:
            environment = self._environment(request, data["environment_id"], issue)
            if environment is None:
                return _not_found()
        else:
            environment = default_environment(
                issue.project, self._environments(request)
            )
        blocked = environment_blocker(environment) if environment else None
        open_job = next(
            (job for job in jobs_of_issues([issue]).get(issue.id, []) if is_open(job)),
            None,
        )
        plan = {
            "issue": issue_view(issue),
            "environment": {"id": environment.id, "name": environment_name(environment)}
            if environment
            else None,
            "blocked": {"code": blocked.code, "message": blocked.message}
            if blocked
            else None,
            "sample": issue_sample(issue, environment).model_dump(mode="json"),
            "open_job_id": open_job.id if open_job else None,
            "issue_scenarios": {
                "held": len(issue_scenarios(environment)),
                "limit": issue_scenarios_max(),
            }
            if environment
            else None,
            "defaults": launch_defaults(environment),
        }
        return Response(AgentFixIssuePlanSerializer(plan).data)

    @validated_request(
        query_serializer=AgentFixIssueStatusQuerySerializer,
        responses={200: AgentFixIssueStatusListSerializer},
    )
    def list(self, request):
        """Each named issue's newest job, for the issue list's epoch icon: one call per
        page of issues. Without ``project_id`` the ids are read in every project the
        caller may read, so a page across projects is one call too; each result names
        its project."""
        data = request.validated_query_data
        projects = project_queryset_for_request(request)
        if "project_id" in data:
            projects = projects.filter(id=data["project_id"])
        issues = list(
            TraceErrorGroup.objects.filter(
                cluster_id__in=data["cluster_ids"],
                project__in=projects,
                target_type="error_feed",
            )
        )
        jobs = jobs_of_issues(issues, environments=self._environments(request))
        results = [
            {
                "cluster_id": issue.cluster_id,
                "project_id": issue.project_id,
                "job": issue_job_view(jobs[issue.id][0]),
            }
            for issue in issues
            if jobs.get(issue.id)
        ]
        return Response(AgentFixIssueStatusListSerializer({"results": results}).data)

    @validated_request(
        request_serializer=AgentFixIssueCreateSerializer,
        responses={201: AgentFixIssueJobDetailSerializer},
        reject_unknown_fields=True,
    )
    def create(self, request):
        """Start Run Epoch from an issue: import the picked traces as evidence for the
        environment and ask its builder to draft them. One builder turn, billed as
        authoring credits."""
        data = request.validated_data
        issue = self._issue(request, data)
        if issue is None:
            return _not_found()
        environment = self._environment(request, data["environment_id"], issue)
        if environment is None:
            return _not_found()
        job = create_issue_job(
            issue,
            environment=environment,
            member_ids=list(data["member_ids"]),
            user=request.user,
            endpoint_base_url=_public_base_url(request),
        )
        return self._detail(request, job.id, status.HTTP_201_CREATED)

    @validated_request(responses={200: AgentFixIssueJobDetailSerializer})
    def retrieve(self, request, pk=None):
        return self._detail(request, pk)

    @validated_request(
        request_serializer=AgentFixIssueRunSerializer,
        responses={202: AgentFixIssueJobDetailSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        """Run Epoch after the review: the kept drafts join the environment's suite, and
        the current version runs on the kept scenarios. The epoch starts from that run
        once it finishes."""
        job = self._job(request, pk)
        if job is None:
            return _not_found()
        data = request.validated_data
        run_issue_job(
            job,
            case_ids=[str(one) for one in data["case_ids"]],
            launch=IssueLaunch(
                surfaces=data["surfaces"],
                locked_paths=data["locked_paths"],
                budget_credits=data["budget_credits"],
                max_minutes=data.get("max_minutes"),
                allowed_models=data.get("allowed_models"),
                reply_latency_margin_pct=data.get("reply_latency_margin_pct"),
                cost_margin_pct=data.get("cost_margin_pct"),
                cost_limit_usd=data.get("cost_limit_usd"),
            ),
            user=request.user,
        )
        return self._detail(request, pk, status.HTTP_202_ACCEPTED)

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={200: AgentFixIssueJobDetailSerializer},
    )
    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        """Stop before the epoch starts: drafting is dropped, or the baseline canceled.
        A started epoch stops on its own route."""
        job = self._job(request, pk)
        if job is None:
            return _not_found()
        stop_issue_job(job)
        return self._detail(request, pk)
