from uuid import UUID

from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.models import AgentDefinition, HostedHarnessJob, HostedHarnessScenario
from simulate.serializers.harness_environment import (
    HarnessEnvironmentAddEvaluationSerializer,
    HarnessEnvironmentAvailableEvalsSerializer,
    HarnessEnvironmentDetailSerializer,
    HarnessEnvironmentEvalEditSerializer,
    HarnessEnvironmentListQuerySerializer,
    HarnessEnvironmentListResponseSerializer,
    HarnessEnvironmentRenameSerializer,
    HarnessEnvironmentRunEvaluationQueuedSerializer,
    HarnessEnvironmentRunEvaluationsQueuedSerializer,
    HarnessEnvironmentRunEvaluationsSerializer,
    HarnessEnvironmentToolCallEvaluationSerializer,
    HarnessScenarioChangeQueuedSerializer,
    HarnessScenarioChangeRequestSerializer,
    HarnessScenarioChangeResponseSerializer,
    HarnessScenarioCoverageQuerySerializer,
    HarnessScenarioCoverageResponseSerializer,
    HarnessScenarioDeleteSerializer,
    HarnessScenarioEditSerializer,
    HarnessScenarioErrorSerializer,
    HarnessScenarioListQuerySerializer,
    HarnessScenarioListResponseSerializer,
    HarnessScenarioRowSerializer,
)
from simulate.serializers.harness_job import (
    HarnessRunCreateResponseSerializer,
    HarnessRunCreateSerializer,
)
from simulate.serializers.response.run_test import SimulateEvalConfigResponseSerializer
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
from simulate.services.harness_scenarios import (
    ensure_suite_indexed,
    filtered_suite,
    scenario_page,
    scenario_row,
    suite_coverage,
)
from simulate.services.scenario_changes import (
    ScenarioChangeRefused,
    delete_scenarios,
    edit_scenario,
    request_scenario_change,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.pagination import ExtendedPageNumberPagination


def _touch_content(job):
    """Record that the environment's content changed, for the list's clock.

    Which evals grade an environment is part of what the environment is, so
    binding or removing one moves the row the same way a pipeline stage does,
    and so is whether its tool calls are graded.
    """
    job.content_updated_at = timezone.now()
    job.save(update_fields=["content_updated_at", "updated_at"])


def _bound_by_name(run_test, name):
    """The live config this environment already binds under ``name``, if any.

    "Grade this run" names an eval the environment already has. A person may
    have added it from outside the harness's offer or under a name of their
    own, so the offer's gates would refuse it even though it is bound and
    gradeable. A config's own name wins over any template's name, so a
    config cannot be shadowed by an older one whose template happens to be
    called the same; within each, the oldest binding wins.
    """
    from simulate.services.harness_evals import selected_eval_configs

    configs = selected_eval_configs(run_test)
    for config in configs:
        if name == str(config.name or ""):
            return config
    for config in configs:
        if name == str(getattr(config.eval_template, "name", "") or ""):
            return config
    return None


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


def _refused(refused):
    return Response(
        {"error": refused.code, "message": refused.message}, status=refused.status
    )


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
                    organization=request_organization(request),
                    environment__isnull=True,
                    deleted=False,
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
        query_serializer=HarnessScenarioListQuerySerializer,
        responses={200: HarnessScenarioListResponseSerializer},
    )
    @action(detail=True, methods=["get"], url_path="scenarios")
    def scenarios(self, request, pk=None):
        """One page of the environment's scenarios, each identified by its row id."""
        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        ensure_suite_indexed(job)
        filtered, offerable = filtered_suite(job, request.query_params)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(filtered, request, view=self)
        rows, details = scenario_page(
            job, page or [], filtered, offerable, request.query_params.get("group_by")
        )
        response = paginator.get_paginated_response(rows)
        response.data.update(details)
        return response

    @validated_request(
        query_serializer=HarnessScenarioCoverageQuerySerializer,
        responses={200: HarnessScenarioCoverageResponseSerializer},
    )
    @action(detail=True, methods=["get"], url_path="scenarios/coverage")
    def scenario_coverage(self, request, pk=None):
        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(suite_coverage(job, request.query_params))

    @swagger_auto_schema(
        operation_id="simulate_api_harness-environments_scenario_detail",
        responses={200: HarnessScenarioRowSerializer},
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=r"scenarios/(?P<scenario_id>[0-9a-fA-F-]{36})",
    )
    def scenario_detail(self, request, pk=None, scenario_id=None):
        job = self._job(request, pk)
        identifier = _uuid_or_none(scenario_id)
        row = (
            HostedHarnessScenario.no_workspace_objects.filter(job=job, id=identifier)
            .select_related("scenario", "call_execution")
            .first()
            if job is not None and identifier is not None
            else None
        )
        if row is None:
            return Response(
                {"detail": "Scenario not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(scenario_row(row))

    @validated_request(
        request_serializer=HarnessScenarioEditSerializer,
        responses={
            200: HarnessScenarioChangeResponseSerializer,
            400: HarnessScenarioErrorSerializer,
            404: HarnessScenarioErrorSerializer,
            409: HarnessScenarioErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    @scenario_detail.mapping.patch
    def patch_scenario(self, request, pk=None, scenario_id=None):
        """Replace a scenario's directly editable fields; nothing is re-proved."""
        job = self._job(request, pk)
        identifier = _uuid_or_none(scenario_id)
        if job is None or identifier is None:
            return _refused(
                ScenarioChangeRefused("scenario_not_found", "scenario not found", 404)
            )
        data = dict(request.validated_data)
        expected = data.pop("expected_revision", None)
        persona = data.pop("persona", None) or {}
        try:
            return Response(
                edit_scenario(
                    job,
                    identifier,
                    fields=data,
                    persona=persona,
                    expected_revision=expected,
                )
            )
        except ScenarioChangeRefused as refused:
            return _refused(refused)

    @swagger_auto_schema(
        responses={
            200: HarnessScenarioChangeResponseSerializer,
            404: HarnessScenarioErrorSerializer,
            409: HarnessScenarioErrorSerializer,
        }
    )
    @scenario_detail.mapping.delete
    def remove_scenario(self, request, pk=None, scenario_id=None):
        """Remove one scenario; runs that used it keep it in their history."""
        job = self._job(request, pk)
        identifier = _uuid_or_none(scenario_id)
        if job is None or identifier is None:
            return _refused(
                ScenarioChangeRefused("scenario_not_found", "scenario not found", 404)
            )
        try:
            return Response(delete_scenarios(job, [identifier]))
        except ScenarioChangeRefused as refused:
            return _refused(refused)

    @validated_request(
        request_serializer=HarnessScenarioDeleteSerializer,
        responses={
            200: HarnessScenarioChangeResponseSerializer,
            404: HarnessScenarioErrorSerializer,
            409: HarnessScenarioErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="scenarios/delete")
    def remove_scenarios(self, request, pk=None):
        """Remove several scenarios at once."""
        job = self._job(request, pk)
        if job is None:
            return _refused(
                ScenarioChangeRefused(
                    "scenario_not_found", "environment not found", 404
                )
            )
        try:
            return Response(
                delete_scenarios(
                    job,
                    request.validated_data["scenario_ids"],
                    expected_revision=request.validated_data.get("expected_revision"),
                )
            )
        except ScenarioChangeRefused as refused:
            return _refused(refused)

    @validated_request(
        request_serializer=HarnessScenarioChangeRequestSerializer,
        responses={
            202: HarnessScenarioChangeQueuedSerializer,
            400: HarnessScenarioErrorSerializer,
            404: HarnessScenarioErrorSerializer,
            409: HarnessScenarioErrorSerializer,
            503: HarnessScenarioErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="scenarios/changes")
    def change_scenarios(self, request, pk=None):
        """Revise scenarios or add new ones through the builder agent, which re-proves them."""
        import hashlib
        import uuid as uuid_module

        from simulate.services.hosted_harness import HostedHarnessError
        from simulate.services.hosted_harness_conversation import (
            serialize_conversation,
        )
        from simulate.services.hosted_harness_ingress import _public_base_url

        job = self._job(request, pk)
        if job is None:
            return _refused(
                ScenarioChangeRefused("scenario_not_found", "environment not found", 404)
            )
        key = request.headers.get("Idempotency-Key") or uuid_module.uuid4().hex
        data = request.validated_data
        try:
            conversation = request_scenario_change(
                job,
                kind=data["kind"],
                instruction=data.get("instruction") or "",
                scenario_ids=data.get("scenario_ids") or [],
                count=data.get("count"),
                client_request_id="change-" + hashlib.sha256(key.encode()).hexdigest()[:40],
                base_url=_public_base_url(request),
            )
        except HostedHarnessError as exc:
            return _refused(ScenarioChangeRefused(exc.code, exc.message, exc.status_code))
        except ScenarioChangeRefused as refused:
            return _refused(refused)
        return Response(serialize_conversation(conversation), status=status.HTTP_202_ACCEPTED)

    @validated_request(
        request_serializer=HarnessRunCreateSerializer,
        responses={202: HarnessRunCreateResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        """Create one new Run for the selected scenarios and trial count."""
        return get_harness_provider().run(request, pk)

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

        Two things happen inside one ``transaction.atomic()`` block. First, the
        eval is bound: a name this environment already binds, under the
        config's own name or its template's, is used as it stands, with no
        offer gate, no lock and no new row, so an eval a person added from
        outside the harness's offer still grades. Any other name is bound
        exactly as ``add_evaluation`` binds it, with the same gates, refusals,
        lock and idempotency. Second, this run's finished calls are selected
        and stamped for grading.
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

        The bind can return an eval config whose ``mapping`` is empty: one
        of the harness's own result columns, bound by ingestion under the
        same name, or a person's eval with no inputs mapped. Neither can be
        graded, so it is refused 400 with its own reason, distinct from the
        "does not produce" refusal, right after the bind and before anything
        is stamped.

        The run is resolved *before* the eval is bound: a request naming a
        run that is not this environment's must leave nothing behind. A run
        that is cancelled or cancelling is refused 409 at the same point: the
        worker would not grade it, so nothing is bound or stamped.
        """
        from django.db import transaction

        from simulate.models import TestExecution
        from simulate.services.harness_evals import (
            EvalSelectionFull,
            EvalSelectionRefused,
            add_selected_eval,
            regrade_mapping,
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
        if execution.status in (
            TestExecution.ExecutionStatus.CANCELLED,
            TestExecution.ExecutionStatus.CANCELLING,
        ):
            # The eval worker never grades a call whose run is cancelled, so
            # binding and stamping here would report queued work that will
            # not happen -- and the stamp would then hide a retry for ten
            # minutes. Refused before the bind, so nothing is left behind.
            return Response(
                {"detail": "Run is cancelled; nothing will be graded"},
                status=status.HTTP_409_CONFLICT,
            )
        modality = eval_modality(job)
        wanted = request.validated_data["name"]
        # Captured before the bind, so a freshly bound config's `updated_at`
        # (set by `auto_now` on insert and on a revived binding's save) reads
        # as after this timestamp, while an idempotent bind's -- an
        # already-bound row returned unchanged -- reads as before it. Under
        # contention, two concurrent clicks can both read
        # `updated_at >= before_bind`, since, when both bind, the second
        # blocks on the run test's `select_for_update` and observes the
        # first's write -- harmless, an extra clock bump, never a missed one.
        before_bind = timezone.now()
        try:
            with transaction.atomic():
                eval_config = _bound_by_name(job.run_test, wanted) or add_selected_eval(
                    job.run_test, wanted, modality
                )
                if regrade_mapping(eval_config) is None:
                    # The bind above can return a row with an empty mapping
                    # -- a harness result column, or a person's eval with no
                    # inputs. A harness suite eval can still be graded, by
                    # the same rule a re-grade uses; anything else has
                    # nothing to grade and gets its own reason before
                    # anything is stamped.
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

    @validated_request(
        request_serializer=HarnessEnvironmentRunEvaluationsSerializer,
        responses={202: HarnessEnvironmentRunEvaluationsQueuedSerializer},
        reject_unknown_fields=True,
        operation_description=(
            "Grade this finished run's calls again with chosen evals of the "
            "environment, without rerunning the calls."
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"runs/(?P<execution_id>[0-9a-fA-F-]{36})/evaluations/run",
    )
    def run_evaluations(self, request, pk=None, execution_id=None):
        """Grade a finished run's calls again with evals the environment already has.

        The calls are not rerun. Each chosen eval is graded again on every call
        of the run, its previous score is replaced, and the run reads
        ``evaluating`` until grading ends. The run-test page's
        ``run-new-evals`` goes through the same service, so both refuse the
        same evals and queue the same job.

        Every refusal leaves the run as it was, a run with no calls or no
        completed call included; a second click that raced the first is
        refused at the claim, and what it had started writing is rolled back.
        The job check comes before the status read, so that read sees
        whatever a harness job's teardown wrote onto the run. A grading job
        that could not be queued answers 503 after every call's scores and
        the run's status were put back.
        """
        from simulate.models import TestExecution
        from simulate.services.harness_evals import is_harness_run_test
        from simulate.services.run_regrade import (
            RegradeAlreadyRunning,
            RegradeDispatchFailed,
            RegradeEvalNotFound,
            RegradeNoCalls,
            RegradeNoCompletedCall,
            RegradeRefused,
            RegradeStillFinishing,
            regrade_run_evals,
            regrade_still_finishing,
        )

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        identifier = _uuid_or_none(execution_id)
        runs = TestExecution.objects.filter(id=identifier, run_test_id=job.run_test_id)
        if identifier is None or not runs.exists():
            return Response(
                build_error_envelope(
                    "Run not found", status_code=status.HTTP_404_NOT_FOUND
                ),
                status=status.HTTP_404_NOT_FOUND,
            )
        execution_ids = [str(identifier)]
        harness_run = is_harness_run_test(job.run_test_id)
        try:
            regrade_still_finishing(execution_ids, harness_run=harness_run)
        except RegradeStillFinishing as finishing:
            return Response(
                build_error_envelope(
                    str(finishing), status_code=status.HTTP_409_CONFLICT
                ),
                status=status.HTTP_409_CONFLICT,
            )
        # Read after the job check above, for the teardown reason it gives.
        run_status = runs.values_list("status", flat=True).first()
        if run_status != TestExecution.ExecutionStatus.COMPLETED:
            return Response(
                build_error_envelope(
                    "Only a finished run can be graded again",
                    status_code=status.HTTP_409_CONFLICT,
                ),
                status=status.HTTP_409_CONFLICT,
            )
        eval_config_ids = request.validated_data["eval_config_ids"]
        try:
            call_execution_count = regrade_run_evals(
                job.run_test,
                execution_ids,
                {str(identifier): run_status},
                eval_config_ids,
                harness_run=harness_run,
                enable_tool_evaluation=request.validated_data.get(
                    "enable_tool_evaluation"
                ),
            )
        except RegradeEvalNotFound:
            # An eval removed between the check above and the service's own.
            return Response(
                build_error_envelope(
                    "Evaluation not found", status_code=status.HTTP_404_NOT_FOUND
                ),
                status=status.HTTP_404_NOT_FOUND,
            )
        except RegradeRefused as refused:
            return Response(
                build_error_envelope(
                    str(refused), status_code=status.HTTP_400_BAD_REQUEST
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RegradeNoCalls:
            return Response(
                build_error_envelope(
                    "This run has no calls to grade",
                    status_code=status.HTTP_409_CONFLICT,
                ),
                status=status.HTTP_409_CONFLICT,
            )
        except RegradeNoCompletedCall:
            return Response(
                build_error_envelope(
                    "Nothing to grade again: no call in this run completed.",
                    status_code=status.HTTP_409_CONFLICT,
                ),
                status=status.HTTP_409_CONFLICT,
            )
        except RegradeAlreadyRunning:
            return Response(
                build_error_envelope(
                    "Grading is already running on this run.",
                    status_code=status.HTTP_409_CONFLICT,
                ),
                status=status.HTTP_409_CONFLICT,
            )
        except RegradeDispatchFailed:
            return Response(
                build_error_envelope(
                    "Grading couldn't be started. Try again.",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # Queuing a grade is content movement, as it is for add_run_evaluation.
        _touch_content(job)
        return Response(
            {"call_execution_count": call_execution_count},
            status=status.HTTP_202_ACCEPTED,
        )

    # Pinned: the PATCH below shares this action's path, and DRF names the
    # operations of a two-method action after the path and the method.
    @swagger_auto_schema(
        responses={204: "Removed"},
        operation_id="simulate_api_harness-environments_remove_evaluation",
    )
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
        lists. Removing an eval someone added stops future scenarios being
        graded by it and leaves the history it already wrote intact. An eval the
        harness reported itself comes back the next time the harness grades it.
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
        request_serializer=HarnessEnvironmentEvalEditSerializer,
        responses={200: SimulateEvalConfigResponseSerializer},
        request_methods=["PATCH"],
        reject_unknown_fields=True,
        operation_id="simulate_api_harness-environments_edit_evaluation",
        operation_description=(
            "Change one eval of this environment. Never grades anything; "
            "grade a run again afterwards to refresh its scores."
        ),
    )
    @remove_evaluation.mapping.patch
    def edit_evaluation(self, request, pk=None, eval_config_id=None):
        """Change one eval's name, settings, inputs or judge model.

        The run-test page's eval edit goes through the same service, so the
        checks and their sentences are the same, but nothing here grades:
        grading a run again is ``runs/{id}/evaluations/run``.

        A row the harness fills is refused whatever the body says. It keeps an
        empty mapping on purpose, and later harness runs write into it by its
        fixed id, so for the same reason an edit may not leave a row without
        inputs of its own. Every refusal comes before the row is saved.
        """
        from django.db import transaction

        from simulate.models.eval_config import SimulateEvalConfig
        from simulate.serializers.run_test import SimulateEvalConfigSimpleSerializer
        from simulate.services.eval_config_edit import (
            EvalConfigEditRefused,
            has_own_mapping,
            update_eval_config,
        )
        from simulate.services.harness_evals import is_harness_run_test

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        config_id = _uuid_or_none(eval_config_id)
        config = (
            SimulateEvalConfig.objects.select_related("eval_template")
            .filter(id=config_id, run_test_id=job.run_test_id, deleted=False)
            .first()
            if config_id is not None
            else None
        )
        if config is None:
            return Response(
                build_error_envelope(
                    "Evaluation not found", status_code=status.HTTP_404_NOT_FOUND
                ),
                status=status.HTTP_404_NOT_FOUND,
            )
        name = config.name or "This evaluation"
        if is_harness_run_test(job.run_test_id) and not has_own_mapping(config):
            return Response(
                build_error_envelope(
                    f"{name} is set by the harness and can't be edited here.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not request.validated_data:
            return Response(
                build_error_envelope(
                    "Nothing to change", status_code=status.HTTP_400_BAD_REQUEST
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        def keeps_its_inputs(edited):
            if not has_own_mapping(edited):
                raise EvalConfigEditRefused(f"{name} needs at least one input mapped")

        try:
            with transaction.atomic():
                update_eval_config(
                    config,
                    request.validated_data,
                    organization=request_organization(request),
                    workspace=request_workspace(request),
                    before_save=keeps_its_inputs,
                )
                _touch_content(job)
        except EvalConfigEditRefused as refused:
            return Response(
                build_error_envelope(
                    str(refused), status_code=status.HTTP_400_BAD_REQUEST
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(SimulateEvalConfigSimpleSerializer(config).data)

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
