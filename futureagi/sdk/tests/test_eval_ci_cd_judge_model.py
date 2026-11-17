"""The model an SDK CI/CD evaluation (/sdk/api/v1/evaluate-pipeline/) runs on
when an LLM-judge item does not name one.

Without model_name, the engine runs a judge on its template's configured
model, or on a decision model the item's config names; it reads neither the
template's saved model nor any other model in the item's config, so with
neither the item fails with "This evaluation could not run.". Such an item
takes the model its config names (as the engine reads the config, with the
keys in params lifted), else its template's saved model. The model is
written to the item, so the evaluation stores it, the usage check before the
run uses its type, the engine runs it and the charge is the one of an item
that named it. With no model at all, the whole request is refused with a 400
naming the item's model_name, before any run or evaluation is created.

Every other item is left as it is: a judge item the engine has a model for,
an item that names a model, agents, system templates and code evaluations.
A judge item whose run_config is set but is not an object is refused for its
run_config, whatever its model. The run's results list the completed
evaluations per template and the model each was stored with.

The real serializers, view, run creation, evaluation dispatch and evaluation
worker run. Replaced: the start of the evaluation workflow, and in the tests
that run an evaluation, the usage check, the cost log, the engine and the
usage emit. Where the engine's own choice of model is the point, a test
builds the evaluator as the engine does and reads the model it is given.
"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from sdk.utils import evaluations as sdk_evaluations
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.temporal.evaluations import activities
from tracer.models.eval_ci_cd import EvaluationResult, EvaluationRun

pytestmark = pytest.mark.django_db

URL = "/sdk/api/v1/evaluate-pipeline/"
NO_SAVED_MODEL = (
    "This evaluation template has no saved model, so pass model_name to "
    "choose the model it runs on."
)
JUDGE = {
    "eval_type_id": "CustomPromptEvaluator",
    "required_keys": ["query"],
    "output": "score",
    "rule_prompt": "Is {{query}} polite?",
    "custom_eval": True,
}
BUILTIN_JUDGE = {
    "eval_type_id": "CustomPromptEvaluator",
    "required_keys": ["query"],
    "output": "score",
    "rule_prompt": "Is {{query}} polite?",
}
AGENT = {
    "eval_type_id": "AgentEvaluator",
    "required_keys": ["query"],
    "output": "score",
    "rule_prompt": "Is {{query}} polite?",
    "custom_eval": True,
}
CODE = {
    "eval_type_id": "CustomCodeEval",
    "required_keys": ["query"],
    "output": "score",
    "custom_eval": True,
    "code": "def evaluate(query):\n    return 1.0\n",
}
SMALL = APICallTypeChoices.TURING_SMALL_EVALUATOR.value
FLASH = APICallTypeChoices.TURING_FLASH_EVALUATOR.value
LARGE = APICallTypeChoices.TURING_LARGE_EVALUATOR.value


def template(organization, workspace, config, *, model=None, owner="user"):
    """A template saved with exactly this organization, workspace and model."""
    name = f"cicd_model_{uuid4().hex[:8]}"
    row = EvalTemplate.no_workspace_objects.create(
        name=name,
        owner=owner,
        organization=organization,
        workspace=workspace,
        config=dict(config),
        model=model,
    )
    EvalTemplate.no_workspace_objects.filter(pk=row.pk).update(
        organization=organization, workspace=workspace, name=name
    )
    row.refresh_from_db()
    return row


def item(row, **fields):
    return {"eval_template": row.name, "inputs": {"query": ["q"]}, **fields}


def refusal(path, sentence):
    """The whole 400 body of a request refused for one field of one item."""
    message = f"{path}: {sentence}"
    return {
        "status": False,
        "type": "validation_error",
        "code": "invalid",
        "detail": message,
        "message": message,
        "error": message,
        "result": message,
        "details": {path: [sentence]},
        "errors": {path: [sentence]},
        "attr": path,
    }


@pytest.fixture
def pipeline(
    auth_client, organization, monkeypatch, django_capture_on_commit_callbacks
):
    """POST and GET of the CI/CD endpoint; the workflow start is recorded."""
    from tfc.temporal import evaluations as temporal_evaluations
    from tracer.models.project import Project

    project, _ = Project.objects.get_or_create(
        name="cicd-judge-model-project",
        organization=organization,
        trace_type="observe",
        defaults={"model_type": "GenerativeLLM"},
    )
    started = Mock()
    monkeypatch.setattr(temporal_evaluations, "start_evaluation_workflow", started)

    def post(*items):
        version = f"v-{uuid4().hex[:8]}"
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(
                URL,
                {
                    "project_name": project.name,
                    "version": version,
                    "eval_data": list(items),
                },
                format="json",
            )
        return SimpleNamespace(response=response, version=version)

    def accepted(*items):
        """The run of an accepted POST and its evaluations."""
        posted = post(*items)
        response = posted.response
        assert response.status_code == 200, response.content.decode()[:500]
        run = EvaluationRun.no_workspace_objects.get(
            id=response.json()["result"]["evaluation_run_id"]
        )
        linked = EvaluationResult.no_workspace_objects.filter(evaluation_run=run)
        rows = list(
            Evaluation.no_workspace_objects.filter(
                id__in=linked.values("evaluation_id")
            ).order_by("created_at", "id")
        )
        assert len(rows) == len(items)
        return SimpleNamespace(version=posted.version, run=run, evaluations=rows)

    def results(version):
        return auth_client.get(f"{URL}?project_name={project.name}&versions={version}")

    return SimpleNamespace(
        post=post, accepted=accepted, results=results, started=started
    )


@pytest.fixture
def worker(monkeypatch):
    """Runs a queued evaluation as the evaluation worker does. Records the
    usage check, the cost log and the engine call in the order they happen,
    and keeps what each received and every usage event emitted."""
    from ee.usage.schemas.events import CheckResult
    from ee.usage.services import emitter, metering
    from ee.usage.services.config import BillingConfig
    from evaluations import engine

    order = []
    checks = []
    cost_logs = []
    requests = []
    events = []

    def check_usage(org_id, api_call_type):
        order.append(("check", api_call_type))
        checks.append((org_id, api_call_type))
        return CheckResult(allowed=True)

    def cost_log(**kwargs):
        order.append(("log", kwargs["api_call_type"]))
        cost_logs.append(kwargs)
        return Mock(
            status=APICallStatusChoices.PROCESSING.value,
            config="{}",
            log_id=uuid4(),
        )

    def run_eval(request):
        order.append(("run", request.model))
        requests.append(request)
        return SimpleNamespace(
            data={},
            failure=None,
            reason="ok",
            runtime=0.01,
            model_used=request.model,
            metrics=None,
            metadata={},
            output_type="score",
            value=1.0,
            cost={"total_cost": 0.002},
            token_usage={},
            evaluator=None,
        )

    billing = Mock()
    billing.get_eval_per_run_fee.return_value = 0.0005
    billing.calculate_ai_credits.side_effect = lambda usd: usd * 100
    monkeypatch.setattr(BillingConfig, "get", Mock(return_value=billing))
    monkeypatch.setattr(metering, "check_usage", check_usage)
    monkeypatch.setattr(
        sdk_evaluations, "log_and_deduct_cost_for_api_request", cost_log
    )
    monkeypatch.setattr(engine, "run_eval", run_eval)
    monkeypatch.setattr(emitter, "emit", events.append)
    # Connection cleanup is worker plumbing; keep the test's transaction open.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)

    def run(evaluation):
        clear_workspace_context()
        activities._run_single_evaluation_sync(str(evaluation.id))
        evaluation.refresh_from_db()
        assert evaluation.status == StatusChoices.COMPLETED, evaluation.error_message

    return SimpleNamespace(
        run=run,
        order=order,
        checks=checks,
        cost_logs=cost_logs,
        requests=requests,
        events=events,
    )


def engine_model(request):
    """The model the engine builds the evaluator with for this engine request,
    with the arguments the engine's runner passes."""
    from evaluations.engine.instance import create_eval_instance

    built = {}
    create_eval_instance(
        eval_class=lambda **config: built.update(config),
        eval_template=request.eval_template,
        config=dict(request.config_overrides),
        model=request.model,
        kb_id=request.kb_id,
        runtime_config=request.runtime_config,
        organization_id=request.organization_id,
        workspace_id=request.workspace_id,
        version_number=request.version_number,
    )
    return built["model"]


def charge(event):
    """What a usage event charges, without the ids that are its own."""
    fields = event.model_dump(exclude={"event_id", "timestamp"})
    fields["properties"] = {
        key: value for key, value in fields["properties"].items() if key != "log_id"
    }
    return fields


@pytest.mark.requires_ee
def test_a_judge_without_model_name_runs_on_its_saved_model(
    pipeline, worker, organization, workspace
):
    judge = template(organization, workspace, JUDGE, model="turing_small")

    [evaluation] = pipeline.accepted(item(judge)).evaluations

    assert evaluation.model_name == "turing_small"
    worker.run(evaluation)
    assert worker.order == [
        ("check", SMALL),
        ("log", SMALL),
        ("run", "turing_small"),
    ]
    assert [event.event_type for event in worker.events] == [SMALL]


@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "config",
    [
        {"run_config": {"model": "turing_flash"}},
        {"model": "turing_flash"},
        {"params": {"run_config": {"model": "turing_flash"}}},
    ],
    ids=["run-config", "top-level", "params"],
)
def test_a_judge_without_model_name_runs_on_the_model_its_config_names(
    pipeline, worker, organization, workspace, config
):
    judge = template(organization, workspace, JUDGE)

    [evaluation] = pipeline.accepted(item(judge, config=config)).evaluations

    assert evaluation.model_name == "turing_flash"
    worker.run(evaluation)
    assert worker.order == [
        ("check", FLASH),
        ("log", FLASH),
        ("run", "turing_flash"),
    ]
    assert worker.cost_logs[0]["config"]["model"] == "turing_flash"
    assert [event.event_type for event in worker.events] == [FLASH]


@pytest.mark.parametrize(
    "missing",
    [{}, {"model_name": None}, {"model_name": ""}],
    ids=["absent", "null", "empty"],
)
def test_a_judge_without_model_name_is_refused_when_its_template_has_no_model(
    pipeline, organization, workspace, missing
):
    row = template(organization, workspace, JUDGE)
    runs_before = EvaluationRun.no_workspace_objects.count()

    response = pipeline.post(item(row, **missing)).response

    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal("eval_data.0.model_name", NO_SAVED_MODEL)
    assert EvaluationRun.no_workspace_objects.count() == runs_before
    assert not Evaluation.no_workspace_objects.filter(eval_template=row).exists()
    pipeline.started.assert_not_called()


@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "config",
    [{}, {"run_config": {"model": "turing_flash"}}],
    ids=["no-model-in-item-config", "another-model-in-item-config"],
)
def test_a_judge_on_its_configured_model_is_left_as_it_is(
    pipeline, worker, organization, workspace, config
):
    """A judge whose template configures its model runs on it without
    model_name, so nothing is written to the item: the engine picks the
    configured model, a model in the item's run_config does not reach a
    judge, and the usage check and the charge keep the type of an item that
    names no model."""
    judge = template(organization, workspace, {**JUDGE, "model": "turing_small"})

    [evaluation] = pipeline.accepted(item(judge, config=config)).evaluations

    assert evaluation.model_name is None
    worker.run(evaluation)
    assert worker.order == [("check", LARGE), ("log", LARGE), ("run", None)]
    assert "model" not in worker.cost_logs[0]["config"]
    assert [event.event_type for event in worker.events] == [LARGE]
    assert engine_model(worker.requests[0]) == "turing_small"


def test_a_judge_whose_config_names_a_decision_model_is_left_as_it_is(
    pipeline, organization, workspace
):
    """The engine runs a decision model named in a judge item's config (or
    refuses it as unknown) without model_name, so nothing is written to the
    item, not even the template's saved model."""
    judge = template(organization, workspace, JUDGE, model="turing_small")
    config = {"run_config": {"model": "jev-latest"}}

    [evaluation] = pipeline.accepted(item(judge, config=config)).evaluations

    assert evaluation.model_name is None
    assert evaluation.eval_config == config
    pipeline.started.assert_called_once_with(evaluation_id=str(evaluation.id))


@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "configured, saved",
    [("turing_small", None), (None, "turing_small"), (None, None)],
    ids=["configured-model", "saved-model", "no-model"],
)
def test_an_agent_item_without_model_name_is_left_as_it_is(
    pipeline, worker, organization, workspace, configured, saved
):
    """An agent item sent without model_name is not given one, whatever its
    template sets: the engine gives the agent its configured model or none,
    and the usage check and the charge keep the type of an item that names
    no model."""
    config = {**AGENT, "model": configured} if configured else AGENT
    agent = template(organization, workspace, config, model=saved)

    [evaluation] = pipeline.accepted(item(agent)).evaluations

    assert evaluation.model_name is None
    worker.run(evaluation)
    assert worker.order == [("check", LARGE), ("log", LARGE), ("run", None)]
    assert "model" not in worker.cost_logs[0]["config"]
    assert [event.event_type for event in worker.events] == [LARGE]
    assert engine_model(worker.requests[0]) == configured


@pytest.mark.parametrize("saved", ["turing_small", None], ids=["saved", "unsaved"])
@pytest.mark.parametrize(
    "config, field",
    [
        ({"run_config": "x"}, "run_config"),
        ({"params": {"run_config": [1]}}, "params.run_config"),
    ],
    ids=["run-config-a-string", "params-run-config-a-list"],
)
def test_a_judge_whose_run_config_is_not_an_object_is_refused(
    pipeline, organization, workspace, config, field, saved
):
    """A run_config that is set but is not an object is refused for that
    field whether or not the template has a saved model: the item is neither
    given a model nor refused for lacking one, and no run is created."""
    judge = template(organization, workspace, JUDGE, model=saved)
    runs_before = EvaluationRun.no_workspace_objects.count()

    response = pipeline.post(item(judge, config=config)).response

    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal(
        f"eval_data.0.config.{field}", "must be an object"
    )
    assert EvaluationRun.no_workspace_objects.count() == runs_before
    assert not Evaluation.no_workspace_objects.filter(eval_template=judge).exists()
    pipeline.started.assert_not_called()


@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "saved_model, config",
    [
        (None, {}),
        ("turing_small", {"run_config": {"model": "turing_large"}}),
    ],
    ids=["template-without-a-model", "saved-and-config-models-differ"],
)
def test_an_item_that_names_a_model_runs_and_is_checked_on_exactly_that_model(
    pipeline, worker, organization, workspace, saved_model, config
):
    judge = template(organization, workspace, JUDGE, model=saved_model)

    [evaluation] = pipeline.accepted(
        item(judge, model_name="turing_flash", config=config)
    ).evaluations

    assert evaluation.model_name == "turing_flash"
    worker.run(evaluation)
    assert worker.order == [
        ("check", FLASH),
        ("log", FLASH),
        ("run", "turing_flash"),
    ]
    assert [event.event_type for event in worker.events] == [FLASH]


@pytest.mark.parametrize(
    "fields, sentence",
    [
        ({}, "Model is required for system evals"),
        (
            {"model_name": "gpt-unknown"},
            "Model name gpt-unknown is invalid for this template. Please use "
            "one of: turing_large, turing_small",
        ),
    ],
    ids=["missing", "invalid"],
)
def test_a_system_template_keeps_its_model_refusals(pipeline, fields, sentence):
    builtin = template(
        None,
        None,
        {**BUILTIN_JUDGE, "models": ["turing_large", "turing_small"]},
        model="turing_large",
        owner="system",
    )

    response = pipeline.post(item(builtin, **fields)).response

    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal("eval_data.0.model_name", sentence)


def test_a_system_template_without_a_model_list_is_left_as_it_is(pipeline):
    builtin = template(
        None, None, BUILTIN_JUDGE, model="turing_large", owner="system"
    )

    [evaluation] = pipeline.accepted(item(builtin)).evaluations

    assert evaluation.model_name is None


def test_a_code_eval_without_model_name_is_accepted_without_a_model(
    pipeline, organization, workspace
):
    code = template(organization, workspace, CODE)

    [evaluation] = pipeline.accepted(item(code)).evaluations

    assert evaluation.status == StatusChoices.PENDING
    assert evaluation.model_name is None


@pytest.mark.requires_ee
def test_a_judge_on_its_saved_model_is_charged_as_if_the_model_were_named(
    pipeline, worker, organization, workspace
):
    judge = template(organization, workspace, JUDGE, model="turing_small")
    [fallback] = pipeline.accepted(item(judge)).evaluations
    [named] = pipeline.accepted(item(judge, model_name="turing_small")).evaluations

    worker.run(fallback)
    worker.run(named)

    assert fallback.model_name == named.model_name == "turing_small"
    assert worker.checks[0] == worker.checks[1]
    assert worker.cost_logs[0] == worker.cost_logs[1]
    assert worker.requests[0] == worker.requests[1]
    assert worker.requests[0].model == "turing_small"
    first, second = worker.events
    assert charge(first) == charge(second)
    assert first.event_type == SMALL
    assert first.amount == pytest.approx(0.25)
    # Each evaluation is its own event.
    assert first.event_id != second.event_id


@pytest.mark.requires_ee
def test_the_usage_check_before_the_run_uses_the_saved_models_type(
    pipeline, worker, organization, workspace
):
    judge = template(organization, workspace, JUDGE, model="turing_small")
    [evaluation] = pipeline.accepted(item(judge)).evaluations

    worker.run(evaluation)

    assert worker.order == [
        ("check", SMALL),
        ("log", SMALL),
        ("run", "turing_small"),
    ]
    assert [event.event_type for event in worker.events] == [SMALL]


@pytest.mark.requires_ee
def test_the_runs_results_name_the_model_each_evaluation_ran_on(
    pipeline, worker, organization, workspace
):
    saved = template(organization, workspace, JUDGE, model="turing_small")
    named = template(organization, workspace, JUDGE)
    posted = pipeline.accepted(item(saved), item(named, model_name="turing_flash"))
    for evaluation in posted.evaluations:
        worker.run(evaluation)

    response = pipeline.results(posted.version)

    assert response.status_code == 200, response.content.decode()[:500]
    result = response.json()["result"]
    assert result["status"] == "completed"
    [entry] = result["evaluation_runs"]
    assert entry["failed_evaluations"] == []
    expected = [
        {"eval_template": saved.name, "model": "turing_small", "count": 1},
        {"eval_template": named.name, "model": "turing_flash", "count": 1},
    ]
    assert entry["completed_evaluations"] == sorted(
        expected, key=lambda group: group["eval_template"]
    )
