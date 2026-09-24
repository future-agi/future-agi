from uuid import UUID

from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.models import AgentDefinition, HostedHarnessJob
from simulate.serializers.harness_environment import (
    HarnessEnvironmentAddEvaluationSerializer,
    HarnessEnvironmentAvailableEvalsSerializer,
    HarnessEnvironmentDetailSerializer,
    HarnessEnvironmentListQuerySerializer,
    HarnessEnvironmentListResponseSerializer,
    HarnessEnvironmentRenameSerializer,
    HarnessEnvironmentRunEvaluationQueuedSerializer,
    HarnessEnvironmentRunResponseSerializer,
    HarnessEnvironmentRunSerializer,
    HarnessEnvironmentToolCallEvaluationSerializer,
)
from simulate.services.harness_environment import (
    annotate_for_list,
    environment_detail,
    environment_row,
    eval_modality,
)
from simulate.services.harness_provider import (
    get_harness_provider,
    request_organization,
    request_workspace,
    scope_jobs,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.pagination import ExtendedPageNumberPagination


def _touch_content(job):
    """Record that the environment's content changed, for the list's clock.

    Which evals grade an environment is part of what the environment is, so
    binding or removing one moves the row the same way a pipeline stage does,
    and so is whether its tool calls are graded.
    """
    job.content_updated_at = timezone.now()
    job.save(update_fields=["content_updated_at", "updated_at"])


def _uuid_or_none(value):
    """The id as a UUID, or ``None`` when it is not one.

    The router matches any segment without a slash or dot, so a hand-edited URL
    reaches the view as a string the column cannot hold. Filtering on it raises
    a Django ``ValidationError``, which DRF's handler does not translate and
    which therefore surfaces as a 500. A miss is the honest answer: the caller
    named something that cannot exist.
    """
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


class HarnessEnvironmentViewSet(viewsets.ViewSet):
    """The environments surface: list, delete, and start a simulation.

    An environment is the job that built it (the world itself lives in object
    storage, addressed from the job's metadata), so these endpoints project the
    same rows the harness-jobs API serves. They exist separately because the
    list needs a row, not a run: the jobs list returns every event, receipt and
    stage-output payload for up to a hundred jobs, which is a detail document
    repeated a hundred times.

    Running and grading a simulation are deliberately not here. ``run`` starts
    one and returns 202; progress is read from the job.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination

    def _queryset(self, request):
        return annotate_for_list(
            scope_jobs(
                HostedHarnessJob.no_workspace_objects.filter(
                    organization=request_organization(request), deleted=False
                ),
                request,
            )
        )

    def _job(self, request, pk):
        identifier = _uuid_or_none(pk)
        if identifier is None:
            return None
        return self._queryset(request).filter(id=identifier).first()

    @validated_request(
        query_serializer=HarnessEnvironmentListQuerySerializer,
        responses={200: HarnessEnvironmentListResponseSerializer},
    )
    def list(self, request):
        organization = request_organization(request)
        if organization is None:
            return Response(
                {"detail": "an organization is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(self._queryset(request), request, view=self)
        return paginator.get_paginated_response([environment_row(job) for job in page])

    @swagger_auto_schema(responses={200: HarnessEnvironmentDetailSerializer})
    def retrieve(self, request, pk=None):
        """One environment: overview, contract, world, scenarios, evaluations, settings.

        Same shape whichever door built it. A section the pipeline has not reached
        yet is null, so the client renders "building" rather than an empty pane.
        """
        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(environment_detail(job))

    @validated_request(
        request_serializer=HarnessEnvironmentRenameSerializer,
        responses={200: HarnessEnvironmentDetailSerializer},
        reject_unknown_fields=True,
    )
    def partial_update(self, request, pk=None):
        """Rename an environment.

        The name is the only editable field: everything else on an environment
        records how it was built, and editing that would make the provenance the
        contract tab shows a claim rather than a record.
        """
        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        job.name = request.validated_data["name"]
        # The list sorts and reports "last updated" from this column, so a
        # rename that left it alone would show a stale time on the row it just
        # changed.
        job.content_updated_at = timezone.now()
        job.save(update_fields=["name", "content_updated_at", "updated_at"])
        return Response(environment_detail(job))

    @swagger_auto_schema(responses={204: "Deleted"})
    def destroy(self, request, pk=None):
        """Soft-delete an environment, cancelling its run first if one is live.

        Deleting while a sandbox is running would leave that sandbox billing
        against a row nobody can see, so cancellation is requested before the
        row disappears. The authoring archive and the organization's secrets are
        left in place: neither is owned by this row, and other environments may
        reference the same credentials.
        """
        from simulate.services.hosted_harness import delete_environment

        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        delete_environment(job)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @validated_request(
        request_serializer=HarnessEnvironmentRunSerializer,
        responses={202: HarnessEnvironmentRunResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        """Start a simulation on an existing environment.

        This reuses the saved contract and scenario suite rather than authoring
        a new one, which is what makes a second run comparable to the first.
        """
        from simulate.services.hosted_harness import HostedHarnessError

        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            result = get_harness_provider().rerun_saved(
                str(job.id),
                organization=request_organization(request),
                workspace=request_workspace(request),
                environment_values={},
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)

        started = result.get("job") or {}
        started_status = result.get("status") or {}
        return Response(
            {
                "environment_id": str(job.id),
                "job_id": started.get("job_id") or str(job.id),
                "run_id": started.get("run_id"),
                "state": started_status.get("state"),
                "stage": started_status.get("stage"),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def _run_test_job(self, request, pk):
        """The environment and its run test, or the response that refuses the call.

        Evaluations hang off the run test, which authoring creates when it
        registers scenarios, so every evaluation endpoint has the same two ways
        of having nothing to act on.
        """
        job = self._job(request, pk)
        if job is None:
            return None, Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not job.run_test_id:
            return None, Response(
                {"detail": "Environment has no evaluations until it finishes building"},
                status=status.HTTP_409_CONFLICT,
            )
        return job, None

    @swagger_auto_schema(responses={200: HarnessEnvironmentAvailableEvalsSerializer})
    @action(detail=True, methods=["get"], url_path="evaluations/available")
    def available_evaluations(self, request, pk=None):
        """The evals this environment could still be graded by.

        The same catalogue authoring chose from, filtered to this environment's
        modality and to the templates the organization can see, minus what is
        already selected. Every entry is addable as it stands: an eval whose
        inputs this modality does not produce is left out rather than offered
        and then refused.
        """
        from simulate.services.harness_evals import addable_evals

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        return Response(
            {"evaluations": addable_evals(job.run_test, eval_modality(job))}
        )

    @validated_request(
        request_serializer=HarnessEnvironmentAddEvaluationSerializer,
        responses={201: HarnessEnvironmentDetailSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="evaluations")
    def add_evaluation(self, request, pk=None):
        """Grade this environment by one more eval from the catalogue.

        Applies to scenarios graded from here on. Calls that already ran keep
        the verdicts they were given, so adding an eval does not backfill a
        column onto past results.
        """
        from simulate.services.harness_evals import (
            EvalSelectionFull,
            EvalSelectionRefused,
            add_selected_eval,
        )

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        try:
            add_selected_eval(
                job.run_test, request.validated_data["name"], eval_modality(job)
            )
        except EvalSelectionFull as full:
            return Response({"detail": str(full)}, status=status.HTTP_409_CONFLICT)
        except EvalSelectionRefused as refused:
            return Response(
                {"detail": str(refused)}, status=status.HTTP_400_BAD_REQUEST
            )
        _touch_content(job)
        return Response(environment_detail(job), status=status.HTTP_201_CREATED)

    @validated_request(
        request_serializer=HarnessEnvironmentAddEvaluationSerializer,
        responses={202: HarnessEnvironmentRunEvaluationQueuedSerializer},
        reject_unknown_fields=True,
        operation_description=(
            "Add an eval to the environment and grade this run's "
            "already-finished calls with it."
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"runs/(?P<execution_id>[0-9a-fA-F-]{36})/evaluations",
    )
    def add_run_evaluation(self, request, pk=None, execution_id=None):
        """Add an eval from inside a finished run, and grade that run's calls with it.

        Two things happen inside one ``transaction.atomic()`` block: the eval
        is bound to the environment exactly as ``add_evaluation`` binds it --
        same gates, refusals, lock, and idempotency, so adding an
        already-bound name is not an error and creates no second row -- and
        this run's finished calls are selected and stamped for grading.
        Wrapping both together means a failure anywhere in this request rolls
        the bind and the stamps back together: no grading job is ever
        dispatched against a bind that did not survive, because
        ``queue_eval_for_finished_calls`` schedules every dispatch with
        ``transaction.on_commit``, which this outer block is what actually
        commits. A refusal from the bind is returned unchanged and nothing is
        queued or stamped.

        A call is queued for grading unless it already holds a verdict for
        this eval, its own evaluations have not finished, or it was queued
        for this eval within the last ten minutes; the rest are stamped and
        scheduled for dispatch, one grading job each, after this transaction
        commits. The answer is the five counts, not the environment detail --
        the client refetches that itself.

        The bind can return an eval config whose ``mapping`` is empty -- one
        of the harness's own result columns, bound by ingestion under the
        same name. Such a config is not something this endpoint can grade
        with: it is refused 400 with its own reason, distinct from the "does
        not produce" refusal, because the run demonstrably CAN fill this
        eval's inputs -- the harness already reports it natively. Checked
        right after the bind and before anything is stamped.

        The run is resolved *before* the eval is bound: a request naming a
        run that is not this environment's must leave nothing behind.
        """
        from django.db import transaction

        from simulate.models import TestExecution
        from simulate.services.harness_evals import (
            EvalSelectionFull,
            EvalSelectionRefused,
            add_selected_eval,
        )
        from simulate.services.harness_run_evals import queue_eval_for_finished_calls

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        identifier = _uuid_or_none(execution_id)
        # A run of this environment is one of its run test's executions, not
        # only the latest: `job.test_execution` holds the most recent one,
        # while a rerun deliberately keeps the same run test
        # (harness_provider.py:1130-1136), so an older run must still be
        # addressable.
        execution = (
            TestExecution.objects.filter(
                id=identifier, run_test_id=job.run_test_id
            ).first()
            if identifier is not None
            else None
        )
        if execution is None:
            return Response(
                {"detail": "Run not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        modality = eval_modality(job)
        wanted = request.validated_data["name"]
        # Captured before the bind, so a freshly bound config's `updated_at`
        # (set by `auto_now` on insert and on a revived binding's save) reads
        # as after this timestamp, while an idempotent bind's -- an
        # already-bound row returned unchanged -- reads as before it. Under
        # contention, two concurrent clicks can both read
        # `updated_at >= before_bind`, since the second blocks on the same
        # row's `select_for_update` and observes the first's write --
        # harmless, an extra clock bump, never a missed one.
        before_bind = timezone.now()
        try:
            with transaction.atomic():
                eval_config = add_selected_eval(job.run_test, wanted, modality)
                if not eval_config.mapping:
                    # The idempotent name match above can return one of the
                    # harness's own result-column rows (empty ``mapping``)
                    # instead of a selected eval -- not the same condition as
                    # the "does not produce" refusal, since this run
                    # demonstrably CAN fill the eval's inputs. It gets its
                    # own reason before anything is stamped.
                    transaction.set_rollback(True)
                    return Response(
                        {
                            "detail": (
                                f"{wanted} is bound as a result column and "
                                "has nothing to grade"
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                counts = queue_eval_for_finished_calls(execution, eval_config)
        except EvalSelectionFull as full:
            return Response({"detail": str(full)}, status=status.HTTP_409_CONFLICT)
        except EvalSelectionRefused as refused:
            return Response(
                {"detail": str(refused)}, status=status.HTTP_400_BAD_REQUEST
            )
        # Skipped only when the bind wrote nothing (the idempotent name match
        # returned an already-bound row unchanged) AND nothing was queued --
        # a genuinely no-op repeat. Any other click, including a repeat that
        # queues a call newly eligible since the last one, still bumps it: a
        # click that stamps or dispatches anything is real content movement,
        # the same as `add_evaluation`'s own touch.
        if eval_config.updated_at >= before_bind or counts["queued"]:
            _touch_content(job)
        return Response(counts, status=status.HTTP_202_ACCEPTED)

    @swagger_auto_schema(responses={204: "Removed"})
    @action(
        detail=True,
        methods=["delete"],
        url_path=r"evaluations/(?P<eval_config_id>[0-9a-fA-F-]{36})",
    )
    def remove_evaluation(self, request, pk=None, eval_config_id=None):
        """Stop running one eval against this environment.

        Soft-delete only. The verdicts an eval already produced live on the call
        executions and in their receipts, not on this row, so a hard delete would
        leave past runs showing scores for something the environment no longer
        lists. Removing it stops future scenarios being graded by it and leaves
        the history it already wrote intact.
        """
        from simulate.models.eval_config import SimulateEvalConfig

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        config_id = _uuid_or_none(eval_config_id)
        config = (
            SimulateEvalConfig.objects.filter(
                id=config_id, run_test_id=job.run_test_id, deleted=False
            ).first()
            if config_id is not None
            else None
        )
        if config is None:
            return Response(
                {"detail": "Evaluation not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        config.deleted = True
        config.deleted_at = timezone.now()
        config.save(update_fields=["deleted", "deleted_at", "updated_at"])
        _touch_content(job)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @validated_request(
        request_serializer=HarnessEnvironmentToolCallEvaluationSerializer,
        responses={200: HarnessEnvironmentDetailSerializer},
        reject_unknown_fields=True,
        operation_description=(
            "Turn the tool-call judge on or off for this environment. "
            "Returns the full environment detail. Turning it on is refused "
            "for a hosted voice environment with no agent version yet."
        ),
    )
    @action(detail=True, methods=["put"], url_path="evaluations/tool-call")
    def set_tool_call_evaluation(self, request, pk=None):
        """Turn the tool-call judge on or off for this environment.

        Deliberately not an eval: it sets a single column on the run test
        (``RunTest.enable_tool_evaluation``), never offered by ``available``,
        never listed in ``evaluations.selected``, and never counted against
        the cap of eight.

        What "on" costs is worth saying out loud: every call the environment
        produces is read once more by a judge that grades the tools the agent
        reached for. Its output goes to ``tool_outputs``, not where verdicts
        live, so this can never touch a stored verdict.

        ``PUT`` rather than ``PATCH``: the body carries the whole state of the
        switch, so the same request twice leaves the same result, and the
        response is the whole environment detail so the client doesn't have
        to refetch.

        Past calls are untouched either way. Turning it on is refused, 409,
        for a hosted voice environment whose agent definition has no version
        yet; turning it off is always allowed.
        """
        from django.db import transaction

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        enable_tool_evaluation = request.validated_data["enable_tool_evaluation"]
        if enable_tool_evaluation:
            agent_definition = job.run_test.agent_definition
            if (
                agent_definition is not None
                and agent_definition.agent_type
                == AgentDefinition.AgentTypeChoices.VOICE
                and agent_definition.latest_version is None
            ):
                return Response(
                    {
                        "detail": "Tool-call evaluation is not available for a voice environment yet"
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        # `job.run_test` caches the instance, so `environment_detail` below
        # reports the value this request just wrote without a second query.
        run_test = job.run_test
        run_test.enable_tool_evaluation = enable_tool_evaluation
        with transaction.atomic():
            run_test.save(update_fields=["enable_tool_evaluation", "updated_at"])
            # Unconditional, like the add and the remove: one rule for the
            # list's clock beats a special case for a no-op request.
            _touch_content(job)
        return Response(environment_detail(job))
