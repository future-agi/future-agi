"""Public whole-context tests on the existing bounded, private paid-job lifecycle."""

from model_hub.selectors import skills_hub_v11 as selectors
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.serializers import skills_context_testing as s
from model_hub.serializers import skills_hub_v11 as leaf
from model_hub.serializers import skills_hub_v12 as context
from model_hub.services.skills_hub.v11 import jobs, test_runs
from model_hub.services.skills_hub.v11.types import TERMINAL_JOB_STATUSES
from model_hub.services.skills_hub.v11.principals import require_private_principal
from model_hub.services.skills_hub.v12 import testing
from model_hub.views.skills_context_foundation import IDEMPOTENCY, contract, header_uuid
from model_hub.views.skills_hub import SkillsView
from model_hub.views.skills_hub_v11 import job_ack


class ContextTestingView(SkillsView):
    private_resource = True

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_private_principal(principal=request.skill_principal)
        get_resource(
            principal=request.skill_principal,
            resource_id=kwargs["context_id"],
            kind="agent_context",
        )


class ContextTestPreviewView(ContextTestingView):
    @contract(s.ContextTestPreview, body=context.ContextRunConfig)
    def post(self, request, context_id):
        value = testing.preview_context_test(
            principal=request.skill_principal,
            context_id=context_id,
            configuration=request.validated_data,
        )
        return self.output(request, s.ContextTestPreview, value)


class ContextTestRunsView(ContextTestingView):
    @contract(s.ContextRunPage, query=leaf.PageQuery)
    def get(self, request, context_id):
        value = selectors.list_visible_runs(
            principal=request.skill_principal,
            skill_id=context_id,
            **request.validated_query_data,
        )
        return self.output(request, s.ContextRunPage, value)

    @contract(
        leaf.TestRunAck,
        body=s.CreateContextTest,
        status=202,
        manual_parameters=[IDEMPOTENCY],
    )
    def post(self, request, context_id):
        run = testing.create_context_test(
            principal=request.skill_principal,
            context_id=context_id,
            request=request.validated_data,
            idempotency_key=header_uuid(request, "Idempotency-Key"),
        )
        return self.output(
            request,
            leaf.TestRunAck,
            {
                **job_ack(run.job),
                "visibility": run.visibility,
                "attempt_count": run.hub_skill_attempts.count(),
                "execution_fingerprint": run.execution_fingerprint,
            },
            status=202,
        )


class ContextTestRunView(ContextTestingView):
    @contract(context.ContextRunSummary)
    def get(self, request, context_id, run_id):
        run = selectors.get_visible_run(
            principal=request.skill_principal, skill_id=context_id, run_id=run_id
        )
        value = selectors.run_summary(principal=request.skill_principal, run=run)
        return self.output(request, context.ContextRunSummary, value)


class ContextTestResultsView(ContextTestingView):
    @contract(s.ContextResultsPage, query=leaf.ResultsQuery)
    def get(self, request, context_id, run_id):
        value = selectors.list_case_results(
            principal=request.skill_principal,
            skill_id=context_id,
            run_id=run_id,
            **request.validated_query_data,
        )
        return self.output(request, s.ContextResultsPage, value)


class ContextTestAttemptView(ContextTestingView):
    @contract(s.ContextAttemptDetail)
    def get(self, request, context_id, run_id, attempt_id):
        value = selectors.get_attempt_detail(
            principal=request.skill_principal,
            skill_id=context_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        return self.output(request, s.ContextAttemptDetail, value)


class ContextTestCancelView(ContextTestingView):
    @contract(leaf.CancelAck, body=leaf.EmptyRequest, status=202)
    def post(self, request, context_id, run_id):
        selectors.get_visible_run(
            principal=request.skill_principal, skill_id=context_id, run_id=run_id
        )
        job = jobs.cancel_job(principal=request.skill_principal, job_id=run_id)
        return self.output(
            request, leaf.CancelAck, job,
            status=200 if job.status in TERMINAL_JOB_STATUSES else 202,
        )


class ContextTestShareView(ContextTestingView):
    @contract(leaf.ShareRunOut, body=leaf.ShareRunRequest)
    def post(self, request, context_id, run_id):
        selectors.get_visible_run(
            principal=request.skill_principal, skill_id=context_id, run_id=run_id
        )
        run = test_runs.share_test_run(
            principal=request.skill_principal, run_id=run_id, **request.validated_data
        )
        return self.output(
            request,
            leaf.ShareRunOut,
            {"id": run.pk, "visibility": run.visibility, "shared_at": run.shared_at},
        )
