"""An SDK evaluation of an agent or LLM-judge template whose config sets a
run_config the engine cannot read is refused before anything is stored; every
other config runs as before.

The engine reads an evaluation's config with the keys of its params lifted
over it. An agent or LLM-judge evaluator takes settings from the run_config it
gets that way, so for one of them a run_config that is set (truthy) but is not
an object carries no settings it can read. POST /sdk/api/v1/new-eval/ and POST
/sdk/api/v1/evaluate-pipeline/ refuse it with 400 "<field path>: must be an
object", naming the run_config the engine reads: params.run_config when params
sets one, else run_config. No evaluation is created and none starts. Any other
evaluator reads no run_config, so one that is not an object is ignored and the
evaluation runs with the answer it gives without one.

Every other config is accepted as before: a falsy run_config (null, "", [],
0, false or {}), which the engine reads as none, a run_config object, and a
run_config or model that params replaces. A model that is not a string is not
checked. Where the run does not use it (the request names a model, or the
template calls none), the evaluation completes as it does without it. Where
it becomes the model of a CI/CD judge item that names none, the request is
accepted and the run fails, naming the model the engine could not find.

The real serializers, views, evaluation creation, evaluation worker and
engine run. Replaced: the start of the evaluation workflow, and in the tests
that run an evaluation, the usage check, the cost log, the usage emit, the
judge's feedback few-shot lookup and the LLM judge itself, which keeps the
settings the engine builds it with and gives a fixed score.
"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from model_hub.models.evaluation import Evaluation, StatusChoices
from sdk.tests.test_eval_ci_cd_judge_model import AGENT, JUDGE, refusal, template
from sdk.utils import evaluations as sdk_evaluations
from sdk.utils.api_errors import EVALUATION_COULD_NOT_RUN
from sdk.views import evaluations as views
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.temporal import evaluations as temporal_evaluations
from tfc.temporal.evaluations import activities
from tracer.models.eval_ci_cd import EvaluationRun

pytestmark = pytest.mark.django_db

NOT_AN_OBJECT = "must be an object"
MODEL = "turing_flash"
FLASH = APICallTypeChoices.TURING_FLASH_EVALUATOR.value
LARGE = APICallTypeChoices.TURING_LARGE_EVALUATOR.value
JUDGE_SCORE = 0.75
JUDGE_REASON = "judged"
CONTAINS = {
    "eval_type_id": "Contains",
    "function_eval": True,
    "required_keys": ["text"],
    "output": "Pass/Fail",
    "config": {"keyword": "query"},
}
RUN_CONFIG_FIELDS = pytest.mark.parametrize(
    "field", ["run_config", "params.run_config"], ids=["top-level", "params"]
)
NOT_A_STRING = pytest.mark.parametrize(
    "model", [5, ["a"], {"a": 1}, True], ids=["number", "list", "object", "true"]
)


@pytest.fixture
def judge(organization, workspace):
    """A customer's LLM judge saved with a model; its config names none."""
    return template(organization, workspace, JUDGE, model="turing_small")


@pytest.fixture
def contains(organization, workspace):
    """A customer's function evaluation, which calls no model."""
    return template(organization, workspace, CONTAINS)


@pytest.fixture
def agent(organization, workspace):
    """A customer's agent evaluation."""
    return template(organization, workspace, AGENT)


@pytest.fixture
def builtin():
    """A built-in function evaluation, which reads no run_config and calls no
    model."""
    return template(None, None, CONTAINS, owner="system")


@pytest.fixture
def project(organization):
    from tracer.models.project import Project

    project, _ = Project.objects.get_or_create(
        name="run-config-shape-project",
        organization=organization,
        trace_type="observe",
        defaults={"model_type": "GenerativeLLM"},
    )
    return project


@pytest.fixture
def started(monkeypatch):
    """The start of the evaluation workflow, recorded."""
    start = Mock()
    monkeypatch.setattr(temporal_evaluations, "start_evaluation_workflow", start)
    return start


def poster(kind, auth_client, project, started, capture):
    """POST of one evaluation to new-eval (asynchronous) or to
    evaluate-pipeline, and the path of the evaluation's config in a refusal.
    Evaluate-pipeline starts its evaluations when the request's transaction
    commits, so its POST runs inside ``capture``
    (django_capture_on_commit_callbacks), which runs those callbacks."""
    new_eval = kind == "new-eval"

    def post(row, config, *, model=None):
        """One evaluation of ``row`` with this config; the request names
        ``model`` when one is given."""
        inputs = {key: "a query" for key in row.config["required_keys"]}
        if new_eval:
            body = {
                "eval_name": row.name,
                "inputs": inputs,
                "config": config,
                "is_async": True,
            }
            if model:
                body["model"] = model
            return auth_client.post("/sdk/api/v1/new-eval/", body, format="json")
        item = {
            "eval_template": row.name,
            "inputs": {key: [value] for key, value in inputs.items()},
            "config": config,
        }
        if model:
            item["model_name"] = model
        body = {
            "project_name": project.name,
            "version": f"v-{uuid4().hex[:8]}",
            "eval_data": [item],
        }
        with capture(execute=True):
            response = auth_client.post(
                "/sdk/api/v1/evaluate-pipeline/", body, format="json"
            )
        return response

    return SimpleNamespace(
        post=post,
        prefix="config" if new_eval else "eval_data.0.config",
        project=project,
        started=started,
    )


@pytest.fixture(params=["new-eval", "evaluate-pipeline"])
def route(request, auth_client, project, started, django_capture_on_commit_callbacks):
    return poster(
        request.param,
        auth_client,
        project,
        started,
        django_capture_on_commit_callbacks,
    )


@pytest.fixture
def worker(monkeypatch):
    """Runs a queued evaluation as the evaluation worker does, on the real
    engine. Records the usage check and the cost log in the order they
    happen, every usage event emitted, and the settings each LLM judge is
    built with; the judge answers JUDGE_SCORE without calling a model. Every
    other evaluator is the real one."""
    from ee.usage.schemas.events import CheckResult
    from ee.usage.services import emitter, metering
    from ee.usage.services.config import BillingConfig
    from evaluations.engine import params as engine_params
    from evaluations.engine import runner as engine_runner

    charged = []
    built = []
    events = []
    real_eval_class = engine_runner.get_eval_class

    class Judge:
        def __init__(self, **settings):
            self.model = settings.get("model")
            built.append(settings)

        def run(self, **params):
            result = {
                "data": None,
                "failure": False,
                "reason": JUDGE_REASON,
                "runtime": 1,
                "model": self.model,
                "metrics": [{"id": "score", "value": JUDGE_SCORE}],
                "metadata": {},
            }
            return SimpleNamespace(eval_results=[result])

    def eval_class(eval_type_id):
        if eval_type_id == "CustomPromptEvaluator":
            return Judge
        return real_eval_class(eval_type_id)

    def check_usage(org_id, api_call_type):
        charged.append(("check", api_call_type))
        return CheckResult(allowed=True)

    def cost_log(**kwargs):
        charged.append(("log", kwargs["api_call_type"]))
        return Mock(
            status=APICallStatusChoices.PROCESSING.value,
            config="{}",
            log_id=uuid4(),
        )

    billing = Mock()
    billing.get_eval_per_run_fee.return_value = 0.0005
    billing.calculate_ai_credits.side_effect = lambda usd: usd * 100
    monkeypatch.setattr(BillingConfig, "get", Mock(return_value=billing))
    monkeypatch.setattr(metering, "check_usage", check_usage)
    monkeypatch.setattr(
        sdk_evaluations, "log_and_deduct_cost_for_api_request", cost_log
    )
    monkeypatch.setattr(emitter, "emit", events.append)
    monkeypatch.setattr(engine_runner, "get_eval_class", eval_class)
    # The judge's feedback examples come from ClickHouse.
    monkeypatch.setattr(engine_params, "_get_few_shot_examples", lambda **_: [])
    # Connection cleanup is worker plumbing; keep the test's transaction open.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)

    def run(evaluation):
        clear_workspace_context()
        activities._run_single_evaluation_sync(str(evaluation.id))
        evaluation.refresh_from_db()

    return SimpleNamespace(run=run, charged=charged, built=built, events=events)


def config_with(field, value):
    """A config that sets only ``field``, a dotted path, to ``value``."""
    config = value
    for key in reversed(field.split(".")):
        config = {key: config}
    return config


def assert_refused(route, response, row, field):
    """A 400 naming the run_config at ``field`` of the evaluation's config,
    and nothing stored or started."""
    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal(f"{route.prefix}.{field}", NOT_AN_OBJECT)
    assert not Evaluation.no_workspace_objects.filter(eval_template=row).exists()
    assert not EvaluationRun.no_workspace_objects.filter(
        project=route.project
    ).exists()
    route.started.assert_not_called()


def assert_accepted(route, response, row, config):
    """A 200, and one evaluation stored with the config as sent and started."""
    assert response.status_code == 200, response.content.decode()[:500]
    evaluation = Evaluation.no_workspace_objects.get(eval_template=row)
    assert evaluation.eval_config == config
    route.started.assert_called_once_with(evaluation_id=str(evaluation.id))
    return evaluation


def assert_judged_on_the_named_model(worker, evaluation):
    """Completed with the judge's answer, by a judge built on the model the
    request names, and checked, logged and charged as that model."""
    assert (
        evaluation.status,
        evaluation.value,
        evaluation.reason,
        evaluation.model,
    ) == (
        StatusChoices.COMPLETED,
        str(JUDGE_SCORE),
        JUDGE_REASON,
        MODEL,
    ), evaluation.error_message
    assert [settings["model"] for settings in worker.built] == [MODEL]
    assert worker.charged == [("check", FLASH), ("log", FLASH)]
    assert [event.event_type for event in worker.events] == [FLASH]


@RUN_CONFIG_FIELDS
@pytest.mark.parametrize(
    "value", ["x", [1], 5, True], ids=["string", "list", "number", "true"]
)
def test_a_run_config_that_is_not_an_object_is_refused(route, judge, field, value):
    response = route.post(judge, config_with(field, value), model=MODEL)

    assert_refused(route, response, judge, field)


@pytest.mark.parametrize(
    "config, field",
    [
        ({"run_config": "x", "params": {"k": 1}}, "run_config"),
        ({"run_config": {}, "params": {"run_config": "x"}}, "params.run_config"),
    ],
    ids=["params-without-one", "params-over-an-object"],
)
def test_the_refusal_names_the_run_config_the_engine_reads(
    route, judge, config, field
):
    """Params' keys replace the config's: the run_config the engine reads is
    the one params sets, else the config's own."""
    response = route.post(judge, config, model=MODEL)

    assert_refused(route, response, judge, field)


@RUN_CONFIG_FIELDS
@pytest.mark.parametrize("value", ["x", [1]], ids=["string", "list"])
def test_an_agent_whose_run_config_is_not_an_object_is_refused(
    route, agent, field, value
):
    """An agent evaluator takes settings from its run_config, as a judge does."""
    response = route.post(agent, config_with(field, value), model=MODEL)

    assert_refused(route, response, agent, field)


@pytest.mark.requires_ee
@RUN_CONFIG_FIELDS
@pytest.mark.parametrize("value", ["x", [1]], ids=["string", "list"])
def test_a_synchronous_new_eval_is_refused_before_it_is_checked_or_charged(
    auth_client, monkeypatch, started, worker, judge, field, value
):
    """Synchronous new-eval runs the evaluation in the request, after its
    usage check and cost log. The refusal comes before both, and nothing is
    stored or started."""
    monkeypatch.setattr(views, "track_mixpanel_event", Mock())
    body = {
        "eval_name": judge.name,
        "inputs": {key: "a query" for key in judge.config["required_keys"]},
        "config": config_with(field, value),
        "model": MODEL,
        "is_async": False,
    }

    response = auth_client.post("/sdk/api/v1/new-eval/", body, format="json")

    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal(f"config.{field}", NOT_AN_OBJECT)
    assert not Evaluation.no_workspace_objects.filter(eval_template=judge).exists()
    started.assert_not_called()
    assert worker.charged == []


@RUN_CONFIG_FIELDS
@pytest.mark.parametrize(
    "value",
    [None, "", [], 0, False, {}],
    ids=["null", "empty-string", "empty-list", "zero", "false", "empty-object"],
)
def test_a_falsy_run_config_is_accepted_as_not_set(route, judge, field, value):
    config = config_with(field, value)

    assert_accepted(route, route.post(judge, config, model=MODEL), judge, config)


@RUN_CONFIG_FIELDS
def test_a_run_config_object_is_accepted(route, judge, field):
    config = config_with(field, {"pass_threshold": 0.25})

    assert_accepted(route, route.post(judge, config, model=MODEL), judge, config)


@RUN_CONFIG_FIELDS
def test_an_agent_run_config_object_is_accepted(route, agent, field):
    config = config_with(field, {"pass_threshold": 0.25})

    assert_accepted(route, route.post(agent, config, model=MODEL), agent, config)


@RUN_CONFIG_FIELDS
@pytest.mark.parametrize("value", ["x", [1]], ids=["string", "list"])
def test_a_run_config_that_is_not_an_object_is_accepted_where_none_is_read(
    route, builtin, field, value
):
    """An evaluator that is neither an agent nor an LLM judge reads no
    run_config, so its shape is not checked."""
    config = config_with(field, value)

    assert_accepted(route, route.post(builtin, config), builtin, config)


@pytest.mark.requires_ee
@RUN_CONFIG_FIELDS
@pytest.mark.parametrize(
    "value", ["x", [1], {"pass_threshold": 0.25}], ids=["string", "list", "object"]
)
def test_a_template_that_reads_no_run_config_runs_whatever_its_shape(
    route, worker, builtin, field, value
):
    """The engine ignores a built-in function evaluation's run_config: the
    evaluation completes with the answer it gives without one, checked and
    charged as a run that names no model."""
    config = config_with(field, value)

    evaluation = assert_accepted(route, route.post(builtin, config), builtin, config)
    worker.run(evaluation)

    assert (evaluation.status, evaluation.value) == (
        StatusChoices.COMPLETED,
        "Passed",
    ), evaluation.error_message
    assert worker.built == []
    assert worker.charged == [("check", LARGE), ("log", LARGE)]
    assert [event.event_type for event in worker.events] == [LARGE]


@pytest.mark.requires_ee
@RUN_CONFIG_FIELDS
@pytest.mark.parametrize(
    "value", ["x", [1], {"pass_threshold": 0.25}], ids=["string", "list", "object"]
)
def test_a_synchronous_new_eval_runs_a_template_that_reads_no_run_config(
    auth_client, monkeypatch, started, worker, builtin, field, value
):
    """Synchronous new-eval runs the evaluation in the request and answers
    with its result, whatever the run_config's shape."""
    monkeypatch.setattr(views, "track_mixpanel_event", Mock())
    config = config_with(field, value)
    body = {
        "eval_name": builtin.name,
        "inputs": {key: "a query" for key in builtin.config["required_keys"]},
        "config": config,
        "is_async": False,
    }

    response = auth_client.post("/sdk/api/v1/new-eval/", body, format="json")

    assert response.status_code == 200, response.content.decode()[:500]
    [result] = response.json()["result"][0]["evaluations"]
    assert result["output"] == "Passed"
    evaluation = Evaluation.no_workspace_objects.get(eval_template=builtin)
    assert (evaluation.status, evaluation.value, evaluation.eval_config) == (
        StatusChoices.COMPLETED,
        "Passed",
        config,
    )
    started.assert_not_called()
    assert worker.built == []
    assert worker.charged == [("check", LARGE), ("log", LARGE)]
    assert [event.event_type for event in worker.events] == [LARGE]


@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "replacement", [{"pass_threshold": 0.25}, None], ids=["object", "null"]
)
def test_a_run_config_that_params_replaces_is_accepted_and_runs(
    route, worker, judge, replacement
):
    """The engine never reads a run_config that params replaces, whatever its
    shape."""
    config = {"run_config": "x", "params": {"run_config": replacement}}
    response = route.post(judge, config, model=MODEL)

    evaluation = assert_accepted(route, response, judge, config)
    worker.run(evaluation)

    assert_judged_on_the_named_model(worker, evaluation)


@pytest.mark.requires_ee
@NOT_A_STRING
def test_a_model_that_is_not_a_string_runs_on_the_model_the_request_names(
    route, worker, judge, model
):
    """A judge runs on the model the request names; the config's model does
    not reach it."""
    config = {"model": model}
    response = route.post(judge, config, model=MODEL)

    evaluation = assert_accepted(route, response, judge, config)
    worker.run(evaluation)

    assert_judged_on_the_named_model(worker, evaluation)


@pytest.mark.requires_ee
def test_a_model_that_params_replaces_runs_on_the_model_the_request_names(
    route, worker, judge
):
    config = {"model": 5, "params": {"model": "turing_small"}}
    response = route.post(judge, config, model=MODEL)

    evaluation = assert_accepted(route, response, judge, config)
    worker.run(evaluation)

    assert_judged_on_the_named_model(worker, evaluation)


@pytest.mark.requires_ee
@NOT_A_STRING
def test_a_model_that_is_not_a_string_is_not_read_by_a_template_without_one(
    route, worker, contains, model
):
    """A function evaluation calls no model, so it completes with its own
    answer, checked and charged as a run that names no model."""
    config = {"model": model}

    evaluation = assert_accepted(route, route.post(contains, config), contains, config)
    worker.run(evaluation)

    assert (evaluation.status, evaluation.value) == (
        StatusChoices.COMPLETED,
        "Passed",
    ), evaluation.error_message
    assert worker.built == []
    assert worker.charged == [("check", LARGE), ("log", LARGE)]
    assert [event.event_type for event in worker.events] == [LARGE]


@pytest.mark.requires_ee
def test_a_number_that_becomes_a_judge_items_model_is_accepted_and_its_run_fails(
    auth_client, project, started, worker, judge, django_capture_on_commit_callbacks
):
    """A CI/CD judge item that names no model takes the model its config
    names, here a number. The request is accepted, not a 500, and the
    evaluation stores the number's text as its model. When it runs, the
    engine finds no model of that name, so the evaluation fails with that
    sentence before any judge is built, and no usage is emitted."""
    pipeline = poster(
        "evaluate-pipeline",
        auth_client,
        project,
        started,
        django_capture_on_commit_callbacks,
    )
    config = {"model": 5}

    response = pipeline.post(judge, config)

    assert response.status_code != 500, response.content.decode()[:500]
    evaluation = assert_accepted(pipeline, response, judge, config)
    assert evaluation.model_name == "5"
    worker.run(evaluation)
    assert evaluation.status == StatusChoices.FAILED
    assert evaluation.reason == EVALUATION_COULD_NOT_RUN
    assert evaluation.error_message.startswith(
        "Model '5' is not available in the current model catalog."
    ), evaluation.error_message
    assert worker.built == []
    assert worker.charged == [("check", LARGE), ("log", LARGE)]
    assert worker.events == []
