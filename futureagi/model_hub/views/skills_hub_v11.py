"""Thin, principal-aware HTTP adapters for the Skills v1.1 services."""

from __future__ import annotations

from inspect import signature
from uuid import UUID

from django.conf import settings
from drf_yasg import openapi
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.skills_hub_v11 import HubSkillJob
from model_hub.selectors import skills_hub_v11 as selectors
from model_hub.serializers import skills_hub as v1
from model_hub.serializers import skills_hub_v11 as s
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11 import (
    adoption,
    assist,
    evidence,
    github,
    governance,
    jobs,
    limits,
    model_calls,
    principals,
    suites,
    test_runs,
)
from model_hub.services.skills_hub.v11.types import (
    TERMINAL_JOB_STATUSES,
    SkillPrincipal,
)
from model_hub.views.skills_hub import SkillsView
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods


def invalid_request(errors: dict) -> Response:
    response: Response = GeneralMethods().custom_error_response(
        400, errors, code="invalid_test_config"
    )
    return response


def contract(
    output,
    *,
    body=None,
    query=None,
    status: int = 200,
    job_start: bool = False,
    **kwargs,
):
    """Use the existing error envelope and declare every v1.1 error status."""
    responses = dict.fromkeys(
        (400, 401, 403, 404, 409, 410, 413, 422, 428, 429, 503), s.SkillsError
    )
    responses[status] = v1.envelope(output)
    if status in {201, 202}:
        responses[200] = responses[status]

    def decorate(handler):
        parameters = [
            openapi.Parameter(
                name,
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                required=True,
            )
            for name in signature(handler).parameters
            if name in {"skill_id", "run_id", "attempt_id", "proposal_id", "preview_id"}
        ]
        if job_start:
            parameters.append(
                openapi.Parameter(
                    "Idempotency-Key",
                    openapi.IN_HEADER,
                    type=openapi.TYPE_STRING,
                    format="uuid",
                    required=True,
                )
            )
        return validated_request(
            body,
            query_serializer=query
            or (s.EmptyRequest if handler.__name__ == "get" else None),
            responses=responses,
            manual_parameters=parameters,
            reject_unknown_fields=True,
            validation_error_response=invalid_request,
            tags=["Skills"],
            **kwargs,
        )(handler)

    return decorate


class SkillsV11Permission(BasePermission):
    def has_permission(self, request: Request, view: APIView) -> bool:
        if not isinstance(view, SkillsV11View):
            return False
        principal = principals.principal_from_request(request)
        # Classify keys before any child/owner lookup. Synthetic system users
        # never inherit private access, even to deliberately shared test jobs.
        principals.require_private_principal(principal=principal)
        principals.require_scope_access(
            principal=principal, write=request.method not in {"GET", "HEAD", "OPTIONS"}
        )
        view.principal = principal
        return True


class SkillsV11View(SkillsView):
    permission_classes = [IsAuthenticated, SkillsV11Permission]
    principal: SkillPrincipal

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if "skill_id" in kwargs:
            # Keep the private-resource error contract while isolating leaf routes.
            from model_hub.selectors.skills_hub_v12 import get_resource

            get_resource(
                principal=self.principal, resource_id=kwargs["skill_id"], kind="skill"
            )

    def idempotency_key(self, request: Request) -> UUID:
        value = request.headers.get("Idempotency-Key", "")
        try:
            key = UUID(value)
        except (ValueError, TypeError, AttributeError) as exc:
            raise SkillsHubError(
                "invalid_test_config",
                "Supply an Idempotency-Key UUID for this job and reuse it on retry.",
                status=400,
            ) from exc
        if str(key) != value.lower():
            raise SkillsHubError(
                "invalid_test_config",
                "Use a canonical Idempotency-Key UUID.",
                status=400,
            )
        return key

    def cancel(
        self, request: Request, *, skill_id: UUID, job_id: UUID, kind: str
    ) -> Response:
        # Parent routing must be checked without a payload-owner selector: a
        # current admin may cancel another user's job but may not inspect it.
        if not HubSkillJob.objects.filter(
            pk=job_id,
            skill_id=skill_id,
            kind=kind,
            skill__deleted=False,
            organization_id=self.principal.scope.organization_id,
            workspace_id=self.principal.scope.workspace_id,
            skill__organization_id=self.principal.scope.organization_id,
            skill__workspace_id=self.principal.scope.workspace_id,
        ).exists():
            raise SkillsHubError("resource_not_found", "Job not found.", status=404)
        job = jobs.cancel_job(principal=self.principal, job_id=job_id)
        return self.output(
            request,
            s.CancelAck,
            job,
            status=200 if job.status in TERMINAL_JOB_STATUSES else 202,
        )


def job_ack(job: HubSkillJob) -> dict:
    return {
        "id": job.pk,
        "status": job.status,
        "poll_after_ms": 2000,
        "deadline_at": job.deadline_at,
    }


class SkillTestingOptionsView(SkillsV11View):
    @contract(s.TestingOptions)
    def get(self, request: Request) -> Response:
        return self.output(
            request,
            s.TestingOptions,
            {
                "enabled": getattr(settings, "SKILLS_TESTING_ENABLED", False),
                "github_enabled": getattr(settings, "SKILLS_GITHUB_ENABLED", False),
                "assist_enabled": bool(
                    getattr(settings, "SKILLS_TESTING_ENABLED", False)
                    and getattr(settings, "SKILLS_ASSIST_ENABLED", False)
                ),
                "models": model_calls.list_test_models(scope=self.principal.scope),
                "limits": limits.effective_limits(),
                "harness_version": limits.HARNESS_VERSION,
                "evaluator_version": limits.EVALUATOR_VERSION,
            },
        )


class SkillTestSuiteView(SkillsV11View):
    @contract(s.SuiteOut)
    def get(self, request: Request, skill_id: UUID) -> Response:
        return self.output(
            request,
            s.SuiteOut,
            suites.get_suite(scope=self.principal.scope, skill_id=skill_id),
        )

    @contract(s.SuiteOut, body=s.SaveSuiteRequest)
    def put(self, request: Request, skill_id: UUID) -> Response:
        suite = suites.save_suite(
            principal=self.principal, skill_id=skill_id, **request.validated_data
        )
        return self.output(
            request,
            s.SuiteOut,
            {
                "revision": suite.revision,
                "digest": suite.digest,
                "cases": suite.cases,
                "updated_at": suite.updated_at,
                "updated_by": suite.updated_by,
                "visibility": "workspace",
            },
        )


class SkillTestRunPreviewView(SkillsV11View):
    @contract(s.TestRunPreview, body=s.RunConfig)
    def post(self, request: Request, skill_id: UUID) -> Response:
        value = test_runs.preview_test_run(
            principal=self.principal,
            skill_id=skill_id,
            configuration=request.validated_data,
        )
        return self.output(request, s.TestRunPreview, value)


class SkillTestRunsView(SkillsV11View):
    @contract(s.RunPage, query=s.PageQuery)
    def get(self, request: Request, skill_id: UUID) -> Response:
        value = selectors.list_visible_runs(
            principal=self.principal, skill_id=skill_id, **request.validated_query_data
        )
        return self.output(request, s.RunPage, value)

    @contract(s.TestRunAck, body=s.CreateTestRunRequest, status=202, job_start=True)
    def post(self, request: Request, skill_id: UUID) -> Response:
        run = test_runs.create_test_run(
            principal=self.principal,
            skill_id=skill_id,
            idempotency_key=self.idempotency_key(request),
            request=request.validated_data,
        )
        return self.output(
            request,
            s.TestRunAck,
            {
                **job_ack(run.job),
                "visibility": run.visibility,
                "attempt_count": run.hub_skill_attempts.count(),
                "execution_fingerprint": run.execution_fingerprint,
            },
            status=202,
        )


class SkillTestRunView(SkillsV11View):
    @contract(s.RunSummary)
    def get(self, request: Request, skill_id: UUID, run_id: UUID) -> Response:
        run = selectors.get_visible_run(
            principal=self.principal, skill_id=skill_id, run_id=run_id
        )
        return self.output(
            request,
            s.RunSummary,
            selectors.run_summary(principal=self.principal, run=run),
        )


class SkillTestResultsView(SkillsV11View):
    @contract(s.CaseResultPage, query=s.ResultsQuery)
    def get(self, request: Request, skill_id: UUID, run_id: UUID) -> Response:
        value = selectors.list_case_results(
            principal=self.principal,
            skill_id=skill_id,
            run_id=run_id,
            **request.validated_query_data,
        )
        return self.output(request, s.CaseResultPage, value)


class SkillTestAttemptView(SkillsV11View):
    @contract(s.AttemptDetail)
    def get(
        self, request: Request, skill_id: UUID, run_id: UUID, attempt_id: UUID
    ) -> Response:
        value = selectors.get_attempt_detail(
            principal=self.principal,
            skill_id=skill_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        return self.output(request, s.AttemptDetail, value)


class SkillTestFileView(SkillsV11View):
    @contract(s.SnapshotFileOut, query=s.SnapshotFileQuery)
    def get(self, request: Request, skill_id: UUID, run_id: UUID) -> Response:
        value = selectors.get_snapshot_file(
            principal=self.principal,
            skill_id=skill_id,
            run_id=run_id,
            **request.validated_query_data,
        )
        return self.output(request, s.SnapshotFileOut, value)


class SkillCancelTestRunView(SkillsV11View):
    @contract(s.CancelAck, body=s.EmptyRequest, status=202)
    def post(self, request: Request, skill_id: UUID, run_id: UUID) -> Response:
        return self.cancel(request, skill_id=skill_id, job_id=run_id, kind="test")


class SkillShareTestRunView(SkillsV11View):
    @contract(s.ShareRunOut, body=s.ShareRunRequest)
    def post(self, request: Request, skill_id: UUID, run_id: UUID) -> Response:
        selectors.get_visible_run(
            principal=self.principal, skill_id=skill_id, run_id=run_id
        )
        run = test_runs.share_test_run(
            principal=self.principal, run_id=run_id, **request.validated_data
        )
        return self.output(
            request,
            s.ShareRunOut,
            {"id": run.pk, "visibility": run.visibility, "shared_at": run.shared_at},
        )


class SkillTestEvidenceView(SkillsV11View):
    @contract(s.TestEvidenceOut, query=s.EvidenceQuery)
    def get(self, request: Request, skill_id: UUID) -> Response:
        query = request.validated_query_data
        candidate = {
            "kind": query["candidate"],
            **{key: value for key, value in query.items() if key != "candidate"},
        }
        value = evidence.list_test_evidence(
            principal=self.principal, skill_id=skill_id, candidate=candidate
        )
        return self.output(request, s.TestEvidenceOut, value)


class SkillAssistPreviewView(SkillsV11View):
    @contract(s.AssistPreview, body=s.AssistInput)
    def post(self, request: Request, skill_id: UUID) -> Response:
        value = assist.preview_assist_proposal(
            principal=self.principal, skill_id=skill_id, request=request.validated_data
        )
        return self.output(request, s.AssistPreview, value)


class SkillAssistCreateView(SkillsV11View):
    @contract(s.AssistAck, body=s.CreateAssistRequest, status=202, job_start=True)
    def post(self, request: Request, skill_id: UUID) -> Response:
        proposal = assist.create_assist_proposal(
            principal=self.principal,
            skill_id=skill_id,
            idempotency_key=self.idempotency_key(request),
            request=request.validated_data,
        )
        return self.output(
            request,
            s.AssistAck,
            {
                **job_ack(proposal.job),
                "kind": proposal.kind,
                "base_revision": proposal.base_revision,
                "base_digest": proposal.base_digest,
            },
            status=202,
        )


class SkillAssistView(SkillsV11View):
    @contract(s.AssistProposalOut)
    def get(self, request: Request, skill_id: UUID, proposal_id: UUID) -> Response:
        proposal = selectors.get_owned_proposal(
            principal=self.principal, skill_id=skill_id, proposal_id=proposal_id
        )
        return self.output(
            request, s.AssistProposalOut, selectors.proposal_summary(proposal=proposal)
        )


class SkillApplyAssistView(SkillsV11View):
    @contract(s.SourceDraftOut, body=s.ApplyAssistRequest)
    def post(self, request: Request, skill_id: UUID, proposal_id: UUID) -> Response:
        selectors.get_owned_proposal(
            principal=self.principal, skill_id=skill_id, proposal_id=proposal_id
        )
        draft = assist.apply_assist_proposal(
            principal=self.principal, proposal_id=proposal_id, **request.validated_data
        )
        return self.output(request, s.SourceDraftOut, draft)


class SkillCancelAssistView(SkillsV11View):
    @contract(s.CancelAck, body=s.EmptyRequest, status=202)
    def post(self, request: Request, skill_id: UUID, proposal_id: UUID) -> Response:
        return self.cancel(
            request, skill_id=skill_id, job_id=proposal_id, kind="assist"
        )


class SkillGitHubPreviewsView(SkillsV11View):
    @contract(s.JobAck, body=s.GitHubPreviewRequest, status=202, job_start=True)
    def post(self, request: Request) -> Response:
        preview = github.create_github_preview(
            principal=self.principal,
            idempotency_key=self.idempotency_key(request),
            request=request.validated_data,
        )
        return self.output(request, s.JobAck, job_ack(preview.job), status=202)


class SkillGitHubPreviewView(SkillsV11View):
    @contract(s.GitHubPreviewOut)
    def get(self, request: Request, preview_id: UUID) -> Response:
        preview = selectors.get_owned_preview(
            principal=self.principal, preview_id=preview_id
        )
        return self.output(
            request, s.GitHubPreviewOut, selectors.preview_summary(preview=preview)
        )


class SkillGitHubImportView(SkillsV11View):
    @contract(s.StageGitHubImportOut, body=s.StageGitHubImportRequest, status=201)
    def post(self, request: Request) -> Response:
        preview = selectors.get_owned_preview(
            principal=self.principal, preview_id=request.validated_data["preview_id"]
        )
        if preview.operation != "import" or preview.job.skill_id is not None:
            raise SkillsHubError(
                "resource_not_found", "Initial import preview not found.", status=404
            )
        value = github.stage_github_preview(
            principal=self.principal,
            preview_id=request.validated_data["preview_id"],
            request=request.validated_data,
        )
        return self.output(
            request,
            s.StageGitHubImportOut,
            value,
            status=200 if value.get("replayed", False) else 201,
        )


class SkillSourceView(SkillsV11View):
    @contract(s.GitHubSourceOut)
    def get(self, request: Request, skill_id: UUID) -> Response:
        return self.output(
            request,
            s.GitHubSourceOut,
            selectors.get_source(principal=self.principal, skill_id=skill_id),
        )


class SkillCheckSourceView(SkillsV11View):
    @contract(s.JobAck, body=s.SourceCheckRequest, status=202, job_start=True)
    def post(self, request: Request, skill_id: UUID) -> Response:
        preview = github.create_github_preview(
            principal=self.principal,
            skill_id=skill_id,
            idempotency_key=self.idempotency_key(request),
            request=request.validated_data,
        )
        return self.output(request, s.JobAck, job_ack(preview.job), status=202)


class SkillStageSourceView(SkillsV11View):
    @contract(s.StageSourceOut, body=s.StageSourceUpdateRequest)
    def post(self, request: Request, skill_id: UUID) -> Response:
        selectors.get_owned_preview(
            principal=self.principal,
            preview_id=request.validated_data["preview_id"],
            skill_id=skill_id,
        )
        value = github.stage_github_preview(
            principal=self.principal,
            preview_id=request.validated_data["preview_id"],
            request=request.validated_data,
        )
        return self.output(request, s.StageSourceOut, value)


class SkillLabelProtectionView(SkillsV11View):
    @contract(s.ProtectionOut)
    def get(self, request: Request, skill_id: UUID) -> Response:
        return self.output(
            request,
            s.ProtectionOut,
            selectors.get_label_protection(principal=self.principal, skill_id=skill_id),
        )

    @contract(s.ProtectionOut, body=s.ProtectionRequest)
    def put(self, request: Request, skill_id: UUID) -> Response:
        value = governance.set_label_protection(
            principal=self.principal, skill_id=skill_id, **request.validated_data
        )
        return self.output(request, s.ProtectionOut, value)


class SkillLabelProtectionHistoryView(SkillsV11View):
    @contract(s.PolicyEventPage, query=s.PageQuery)
    def get(self, request: Request, skill_id: UUID) -> Response:
        value = selectors.list_label_protection_history(
            principal=self.principal, skill_id=skill_id, **request.validated_query_data
        )
        return self.output(request, s.PolicyEventPage, value)


class SkillAdoptionView(SkillsV11View):
    @contract(s.AdoptionOut, query=s.AdoptionQuery)
    def get(self, request: Request, skill_id: UUID) -> Response:
        value = adoption.adoption_summary(
            scope=self.principal.scope,
            skill_id=skill_id,
            **request.validated_query_data,
        )
        return self.output(request, s.AdoptionOut, value)
