"""An SDK CI/CD evaluation run (/sdk/api/v1/evaluate-pipeline/) with evaluations
that failed, and the reason a person reads for each.

Each item of the request is started on its own savepoint. An item whose start
raised is undone alone, with any row it created, and recorded as a failed
evaluation of the run, for that item's template, with the fixed sentence "This
evaluation could not start." The other items are created and linked to the run
as before, and the POST answers as before. An item whose failed row cannot be
written, or whose template no longer resolves, is dropped, and the other items
are still linked.

An evaluation that fails while the evaluation worker runs it gets a reason too,
on every install, with a cost log or without one: a typed refusal's own
sentence (CodeEvalSetupError and its subclasses, SDKAdmissionError, also
wrapped as the SDK's evaluation raises them) or else "This evaluation could not
run." A failure after the evaluation returned keeps the evaluator's own reason.

The run's results (GET) list the failed evaluations beside the summary of the
completed ones, counted per template and reason. An exception's own text is in
nothing these results return. A GET for several versions lists each run's own
failed evaluations.

Before, one item that raised dropped every item of the request: the run kept no
evaluation and its results said only results_summary {}. A failed evaluation
had no reason.

The real serializers, views, run creation and evaluation worker run. The
evaluation dispatch (_handle_async_eval) is replaced, and each test replaces
only the steps it controls: the evaluation itself (_run_eval), or, inside the
real one, the engine call and the cost log; the inline evaluation trigger; one
Evaluation create. One test deletes a template between the request check and
the run's creation.
"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError

from agentic_eval.core_evals.fi_utils.custom_code_pause import PAUSED_MESSAGE
from agentic_eval.core_evals.fi_utils.exceptions import (
    CodeEvalSetupError,
    CustomCodeEvalPausedError,
)
from agentic_eval.core_evals.fi_utils.sandbox import EXECUTOR_UNAVAILABLE_MESSAGE
from evaluations import engine
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from sdk.utils import evaluations as sdk_evaluations
from sdk.utils.api_errors import SDKAdmissionError
from tfc.constants.api_calls import APICallStatusChoices
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.temporal.evaluations import activities
from tracer.models.eval_ci_cd import EvaluationResult, EvaluationRun
from tracer.utils import inline_evals

pytestmark = pytest.mark.django_db

URL = "/sdk/api/v1/evaluate-pipeline/"
CONFIG = {"eval_type_id": "TestEval", "required_keys": ["query"], "output": "score"}
COULD_NOT_START = "This evaluation could not start."
COULD_NOT_RUN = "This evaluation could not run."
ACCEPTED = "Evaluation run accepted and is being processed."
# What an unknown exception carried: server text that must reach no caller.
PROVIDER_TEXT = "provider said 401 with key sk-cicd-item-test-0000"
PLANTED = "provider answered 401 for key sk-cicd-planted-0000, leaked"
CREDITS_SENTENCE = "Insufficient credits. Add credits in Settings > Billing."


def template(organization, workspace):
    """A customer template saved with exactly this organization and workspace."""
    name = f"cicd_item_{uuid4().hex[:8]}"
    row = EvalTemplate.no_workspace_objects.create(
        name=name,
        owner="user",
        organization=organization,
        workspace=workspace,
        config=dict(CONFIG),
    )
    EvalTemplate.no_workspace_objects.filter(pk=row.pk).update(
        organization=organization, workspace=workspace, name=name
    )
    row.refresh_from_db()
    return row


@pytest.fixture
def pipeline(auth_client, organization, monkeypatch):
    """POST and GET of the CI/CD endpoint. The dispatch creates the pending
    Evaluation row a real start creates; failures[template pk], when set, is
    called first with that row's fields and raises."""
    from sdk.utils import cicd_evaluations
    from tracer.models.project import Project

    project, _ = Project.objects.get_or_create(
        name="cicd-item-failures-project",
        organization=organization,
        trace_type="observe",
        defaults={"model_type": "GenerativeLLM"},
    )
    failures = {}
    dispatched = []

    def dispatch(eval_template, *, inputs, model, user, eval_config=None, **kwargs):
        dispatched.append(eval_template.pk)
        fields = {
            "user": user,
            "organization": organization,
            "eval_template": eval_template,
            "model_name": model,
            "input_data": inputs,
            "eval_config": eval_config or {},
        }
        fail = failures.get(eval_template.pk)
        if fail is not None:
            fail(fields)
        row = Evaluation.objects.create(**fields)
        return [
            {
                "evaluations": [
                    {
                        "name": eval_template.name,
                        "output_type": "score",
                        "eval_id": str(row.id),
                    }
                ]
            }
        ]

    monkeypatch.setattr(cicd_evaluations, "_handle_async_eval", dispatch)

    def post(*templates):
        version = f"v-{uuid4().hex[:8]}"
        response = auth_client.post(
            URL,
            {
                "project_name": project.name,
                "version": version,
                "eval_data": [
                    {"eval_template": row.name, "inputs": {"query": ["q"]}}
                    for row in templates
                ],
            },
            format="json",
        )
        assert response.status_code == 200, response.content.decode()[:500]
        run = EvaluationRun.no_workspace_objects.get(
            id=response.json()["result"]["evaluation_run_id"]
        )
        return SimpleNamespace(response=response, version=version, run=run)

    def results(version):
        return auth_client.get(f"{URL}?project_name={project.name}&versions={version}")

    return SimpleNamespace(
        project=project,
        failures=failures,
        dispatched=dispatched,
        post=post,
        results=results,
    )


def raising(make_error):
    """A start that raises make_error() before any row exists."""

    def fail(fields):
        raise make_error()

    return fail


def run_evaluations(run):
    """{template id: Evaluation} of the evaluations linked to the run."""
    linked = EvaluationResult.no_workspace_objects.filter(evaluation_run=run)
    rows = list(
        Evaluation.no_workspace_objects.filter(
            id__in=linked.values("evaluation_id")
        ).order_by("created_at", "id")
    )
    by_template = {row.eval_template_id: row for row in rows}
    assert len(by_template) == len(rows) == linked.count()
    return by_template


def complete_started(run, score):
    """Finish, with this score, every evaluation of the run still pending, as
    the engine would; a pending one keeps the results at "processing"."""
    Evaluation.no_workspace_objects.filter(
        ci_cd_result__evaluation_run=run, status=StatusChoices.PENDING
    ).update(status=StatusChoices.COMPLETED, output_float=score)


def the_run(response, run):
    assert response.status_code == 200, response.content.decode()[:500]
    result = response.json()["result"]
    assert result["status"] == "completed"
    [entry] = result["evaluation_runs"]
    assert entry["id"] == str(run.id)
    return entry


def assert_could_not_start(row):
    assert row.status == StatusChoices.FAILED
    assert row.reason == COULD_NOT_START
    assert row.error_message == COULD_NOT_START


@pytest.mark.parametrize(
    "failing_first", [True, False], ids=["failing-item-first", "failing-item-last"]
)
def test_an_item_that_raises_is_a_failed_evaluation_and_the_other_item_still_runs(
    pipeline, organization, workspace, failing_first
):
    failing = template(organization, workspace)
    working = template(organization, workspace)
    pipeline.failures[failing.pk] = raising(lambda: RuntimeError(PROVIDER_TEXT))
    order = (failing, working) if failing_first else (working, failing)

    posted = pipeline.post(*order)

    # The POST answers as before.
    assert posted.response.json()["result"] == {
        "message": ACCEPTED,
        "project_name": pipeline.project.name,
        "version": posted.version,
        "evaluation_run_id": str(posted.run.id),
    }
    assert PROVIDER_TEXT not in posted.response.content.decode()
    # Every item was started, in the request's order.
    assert pipeline.dispatched == [row.pk for row in order]

    rows = run_evaluations(posted.run)
    assert set(rows) == {failing.pk, working.pk}
    failed = rows[failing.pk]
    assert_could_not_start(failed)
    assert failed.input_data == {"query": ["q"]}
    assert failed.organization_id == organization.id
    # The started item is the normal one: pending, no reason, no error.
    started = rows[working.pk]
    assert started.status == StatusChoices.PENDING
    assert started.reason is None
    assert started.error_message is None
    assert started.input_data == {"query": ["q"]}

    complete_started(posted.run, 0.75)
    response = pipeline.results(posted.version)
    body = response.content.decode()
    assert PROVIDER_TEXT not in body
    assert "sk-cicd-item-test" not in body
    entry = the_run(response, posted.run)
    # The summary scores the completed evaluation only; the failed one is
    # counted with its reason instead of being dropped.
    assert entry["results_summary"] == pytest.approx({working.name: 75.0})
    assert entry["failed_evaluations"] == [
        {"eval_template": failing.name, "reason": COULD_NOT_START, "count": 1}
    ]


def test_a_row_created_by_a_start_that_raised_is_undone(
    pipeline, organization, workspace
):
    failing = template(organization, workspace)
    created = []

    def create_then_raise(fields):
        created.append(Evaluation.objects.create(**fields).pk)
        raise RuntimeError(PROVIDER_TEXT)

    pipeline.failures[failing.pk] = create_then_raise

    posted = pipeline.post(failing)

    [created_pk] = created
    assert not Evaluation.no_workspace_objects.filter(pk=created_pk).exists()
    rows = run_evaluations(posted.run)
    assert set(rows) == {failing.pk}
    assert rows[failing.pk].pk != created_pk
    assert_could_not_start(rows[failing.pk])

    # A run whose only item could not start is not left processing.
    response = pipeline.results(posted.version)
    assert PROVIDER_TEXT not in response.content.decode()
    entry = the_run(response, posted.run)
    assert entry["results_summary"] == {}
    assert entry["failed_evaluations"] == [
        {"eval_template": failing.name, "reason": COULD_NOT_START, "count": 1}
    ]


def test_a_start_that_aborts_the_database_transaction_leaves_the_other_items_linked(
    pipeline, organization, workspace, user
):
    failing = template(organization, workspace)
    working = template(organization, workspace)
    elsewhere = Evaluation.objects.create(
        user=user,
        organization=organization,
        eval_template=working,
        input_data={"query": "q"},
    )
    errors = []

    def insert_a_duplicate(fields):
        try:
            Evaluation.objects.create(id=elsewhere.id, **fields)
        except IntegrityError as error:
            errors.append(error)
            raise

    pipeline.failures[failing.pk] = insert_a_duplicate

    posted = pipeline.post(failing, working)

    assert len(errors) == 1
    assert pipeline.dispatched == [failing.pk, working.pk]
    rows = run_evaluations(posted.run)
    assert set(rows) == {failing.pk, working.pk}
    assert_could_not_start(rows[failing.pk])
    assert rows[working.pk].status == StatusChoices.PENDING
    assert rows[working.pk].pk != elsewhere.pk
    assert not EvaluationResult.no_workspace_objects.filter(
        evaluation=elsewhere
    ).exists()


def test_an_item_whose_failed_row_cannot_be_written_leaves_the_other_item_linked(
    pipeline, organization, workspace, monkeypatch
):
    working = template(organization, workspace)
    failing = template(organization, workspace)
    pipeline.failures[failing.pk] = raising(RuntimeError)
    create = Evaluation.objects.create
    created = []

    def create_all_but_the_second(**fields):
        # The working item's start is the first create; the failing item's
        # FAILED row is the second.
        created.append(fields["eval_template"].pk)
        if len(created) == 2:
            raise DatabaseError("the row could not be written")
        return create(**fields)

    monkeypatch.setattr(Evaluation.objects, "create", create_all_but_the_second)

    posted = pipeline.post(working, failing)

    assert posted.response.json()["result"]["message"] == ACCEPTED
    assert pipeline.dispatched == [working.pk, failing.pk]
    assert created == [working.pk, failing.pk]
    rows = run_evaluations(posted.run)
    assert set(rows) == {working.pk}
    assert rows[working.pk].status == StatusChoices.PENDING
    assert not Evaluation.no_workspace_objects.filter(eval_template=failing).exists()

    complete_started(posted.run, 0.5)
    entry = the_run(pipeline.results(posted.version), posted.run)
    assert entry["results_summary"] == pytest.approx({working.name: 50.0})
    assert entry["failed_evaluations"] == []


def test_an_item_whose_template_no_longer_resolves_is_dropped_and_the_rest_linked(
    pipeline, organization, workspace, monkeypatch
):
    from sdk.utils import cicd_evaluations

    first = template(organization, workspace)
    gone = template(organization, workspace)
    last = template(organization, workspace)
    create_evaluations = cicd_evaluations.create_evaluations

    def delete_then_create(**kwargs):
        EvalTemplate.no_workspace_objects.filter(pk=gone.pk).update(deleted=True)
        return create_evaluations(**kwargs)

    monkeypatch.setattr(cicd_evaluations, "create_evaluations", delete_then_create)

    posted = pipeline.post(first, gone, last)

    assert posted.response.json()["result"]["message"] == ACCEPTED
    assert pipeline.dispatched == [first.pk, last.pk]
    rows = run_evaluations(posted.run)
    assert set(rows) == {first.pk, last.pk}
    assert all(row.status == StatusChoices.PENDING for row in rows.values())
    assert not Evaluation.no_workspace_objects.filter(eval_template=gone).exists()

    complete_started(posted.run, 0.5)
    entry = the_run(pipeline.results(posted.version), posted.run)
    assert entry["results_summary"] == pytest.approx(
        {first.name: 50.0, last.name: 50.0}
    )
    assert entry["failed_evaluations"] == []


def paused_as_run_eval_raises_it():
    """The pause refusal as sdk.utils.evaluations._refuse_paused_custom_code
    raises it."""
    paused = CustomCodeEvalPausedError(PAUSED_MESSAGE)
    error = sdk_evaluations.StandaloneEvaluationError(paused)
    error.__cause__ = paused
    return error


@pytest.mark.parametrize(
    "make_error, sentence",
    [
        (lambda: CustomCodeEvalPausedError(PAUSED_MESSAGE), PAUSED_MESSAGE),
        (paused_as_run_eval_raises_it, PAUSED_MESSAGE),
        (
            lambda: sdk_evaluations.StandaloneEvaluationError(
                CodeEvalSetupError(EXECUTOR_UNAVAILABLE_MESSAGE)
            ),
            EXECUTOR_UNAVAILABLE_MESSAGE,
        ),
        (
            lambda: SDKAdmissionError("INSUFFICIENT_CREDITS", CREDITS_SENTENCE),
            CREDITS_SENTENCE,
        ),
        (lambda: RuntimeError(PLANTED), COULD_NOT_RUN),
        (
            lambda: sdk_evaluations.StandaloneEvaluationError(RuntimeError(PLANTED)),
            COULD_NOT_RUN,
        ),
    ],
    ids=[
        "custom-code-paused",
        "custom-code-paused-wrapped",
        "executor-unavailable-wrapped",
        "admission-refused",
        "unknown-error",
        "unknown-error-wrapped",
    ],
)
def test_an_evaluation_that_fails_while_it_runs_records_a_reason_a_person_reads(
    pipeline, organization, workspace, monkeypatch, make_error, sentence
):
    runs = template(organization, workspace)
    posted = pipeline.post(runs)
    [evaluation] = run_evaluations(posted.run).values()
    assert evaluation.status == StatusChoices.PENDING
    error = make_error()

    def run_eval(**kwargs):
        raise error

    monkeypatch.setattr(sdk_evaluations, "_run_eval", run_eval)
    # Connection cleanup is worker plumbing; keep the test's transaction open.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    clear_workspace_context()

    activities._run_single_evaluation_sync(str(evaluation.id))

    evaluation.refresh_from_db()
    assert evaluation.status == StatusChoices.FAILED
    assert evaluation.reason == sentence
    # error_message keeps the exception's text, as before; the run's results
    # never read it.
    assert evaluation.error_message == str(error)

    response = pipeline.results(posted.version)
    assert "sk-cicd-planted" not in response.content.decode()
    entry = the_run(response, posted.run)
    assert entry["results_summary"] == {}
    assert entry["failed_evaluations"] == [
        {"eval_template": runs.name, "reason": sentence, "count": 1}
    ]


@pytest.mark.parametrize(
    "has_cost_log", [False, True], ids=["without-a-cost-log", "with-a-cost-log"]
)
def test_an_evaluation_that_cannot_reach_the_executor_reads_the_executor_sentence_on_every_install(
    pipeline, organization, workspace, monkeypatch, has_cost_log
):
    runs = template(organization, workspace)
    posted = pipeline.post(runs)
    [evaluation] = run_evaluations(posted.run).values()
    cost_log = None
    if has_cost_log:
        cost_log = SimpleNamespace(
            status=APICallStatusChoices.PROCESSING.value, config="{}", save=Mock()
        )
    requests = []

    def run_engine(request):
        requests.append(request)
        raise CodeEvalSetupError(EXECUTOR_UNAVAILABLE_MESSAGE)

    # Admission returns the cost log, or None on an install without ee.
    monkeypatch.setattr(
        sdk_evaluations,
        "_log_and_deduct_cost_for_standalone_eval",
        lambda *args, **kwargs: cost_log,
    )
    monkeypatch.setattr(engine, "run_eval", run_engine)
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    clear_workspace_context()

    activities._run_single_evaluation_sync(str(evaluation.id))

    evaluation.refresh_from_db()
    [request] = requests
    assert request.eval_template.pk == runs.pk
    assert evaluation.status == StatusChoices.FAILED
    assert evaluation.reason == EXECUTOR_UNAVAILABLE_MESSAGE
    if has_cost_log:
        assert cost_log.status == APICallStatusChoices.ERROR.value
        cost_log.save.assert_called_once()

    entry = the_run(pipeline.results(posted.version), posted.run)
    assert entry["results_summary"] == {}
    assert entry["failed_evaluations"] == [
        {"eval_template": runs.name, "reason": EXECUTOR_UNAVAILABLE_MESSAGE, "count": 1}
    ]


def test_a_failure_after_the_run_keeps_the_evaluators_reason(
    pipeline, organization, workspace, monkeypatch
):
    runs = template(organization, workspace)
    posted = pipeline.post(runs)
    [evaluation] = run_evaluations(posted.run).values()
    reason = "the evaluator's own reason"
    error = RuntimeError("the inline evaluation did not start")
    triggered = []

    def run_eval(**kwargs):
        return {
            "data": {"query": ["q"]},
            "reason": reason,
            "runtime": 0.1,
            "model": None,
            "metrics": None,
            "metadata": None,
            "output": "score",
            "value": 0.8,
        }

    def trigger_inline_eval(row):
        triggered.append(row.pk)
        raise error

    monkeypatch.setattr(sdk_evaluations, "_run_eval", run_eval)
    monkeypatch.setattr(inline_evals, "trigger_inline_eval", trigger_inline_eval)
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    clear_workspace_context()

    activities._run_single_evaluation_sync(str(evaluation.id))

    evaluation.refresh_from_db()
    assert triggered == [evaluation.pk]
    assert evaluation.status == StatusChoices.FAILED
    assert evaluation.reason == reason
    assert evaluation.error_message == str(error)

    entry = the_run(pipeline.results(posted.version), posted.run)
    assert entry["results_summary"] == {}
    assert entry["failed_evaluations"] == [
        {"eval_template": runs.name, "reason": reason, "count": 1}
    ]


def test_failed_evaluations_are_counted_per_template_and_reason(
    pipeline, organization, workspace
):
    refused = template(organization, workspace)
    unexplained = template(organization, workspace)
    pipeline.failures[refused.pk] = raising(lambda: RuntimeError(PROVIDER_TEXT))
    posted = pipeline.post(refused, refused, unexplained)
    # As a row written before failed evaluations had a reason.
    Evaluation.no_workspace_objects.filter(
        ci_cd_result__evaluation_run=posted.run, eval_template=unexplained
    ).update(status=StatusChoices.FAILED)

    entry = the_run(pipeline.results(posted.version), posted.run)

    assert entry["results_summary"] == {}
    expected = [
        {"eval_template": refused.name, "reason": COULD_NOT_START, "count": 2},
        {"eval_template": unexplained.name, "reason": None, "count": 1},
    ]
    assert entry["failed_evaluations"] == sorted(
        expected, key=lambda group: group["eval_template"]
    )


def test_results_for_two_versions_keep_each_runs_failed_evaluations_apart(
    pipeline, organization, workspace
):
    refused = template(organization, workspace)
    scored = template(organization, workspace)
    refused_later = template(organization, workspace)
    pipeline.failures[refused.pk] = raising(RuntimeError)
    pipeline.failures[refused_later.pk] = raising(RuntimeError)
    first = pipeline.post(refused, scored)
    second = pipeline.post(refused, refused, refused_later)
    complete_started(first.run, 0.75)

    response = pipeline.results(f"{first.version},{second.version}")

    assert response.status_code == 200, response.content.decode()[:500]
    result = response.json()["result"]
    assert result["status"] == "completed"
    entries = {entry["id"]: entry for entry in result["evaluation_runs"]}
    assert set(entries) == {str(first.run.id), str(second.run.id)}
    earlier = entries[str(first.run.id)]
    assert earlier["results_summary"] == pytest.approx({scored.name: 75.0})
    assert earlier["failed_evaluations"] == [
        {"eval_template": refused.name, "reason": COULD_NOT_START, "count": 1}
    ]
    later = entries[str(second.run.id)]
    assert later["results_summary"] == {}
    expected = [
        {"eval_template": refused.name, "reason": COULD_NOT_START, "count": 2},
        {"eval_template": refused_later.name, "reason": COULD_NOT_START, "count": 1},
    ]
    assert later["failed_evaluations"] == sorted(
        expected, key=lambda group: group["eval_template"]
    )


def test_a_run_whose_items_all_start_is_unchanged(pipeline, organization, workspace):
    first = template(organization, workspace)
    second = template(organization, workspace)

    posted = pipeline.post(first, second)

    assert posted.response.json()["result"]["message"] == ACCEPTED
    assert pipeline.dispatched == [first.pk, second.pk]
    rows = run_evaluations(posted.run)
    assert set(rows) == {first.pk, second.pk}
    assert all(row.status == StatusChoices.PENDING for row in rows.values())
    assert all(row.reason is None for row in rows.values())
    assert all(row.error_message is None for row in rows.values())
    assert not Evaluation.no_workspace_objects.filter(
        eval_template__in=[first, second], status=StatusChoices.FAILED
    ).exists()
    links = EvaluationResult.no_workspace_objects.filter(evaluation_run=posted.run)
    assert all(link.organization_id == organization.id for link in links)


def test_results_of_a_run_whose_items_all_started_list_no_failed_evaluation(
    pipeline, organization, workspace
):
    first = template(organization, workspace)
    second = template(organization, workspace)
    posted = pipeline.post(first, second)
    complete_started(posted.run, 0.5)

    entry = the_run(pipeline.results(posted.version), posted.run)

    assert entry["results_summary"] == pytest.approx(
        {first.name: 50.0, second.name: 50.0}
    )
    assert entry["failed_evaluations"] == []
