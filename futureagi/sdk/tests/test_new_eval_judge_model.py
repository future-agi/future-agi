"""The model an SDK evaluation (/sdk/api/v1/new-eval/), sync or async, runs
on when an LLM-judge request does not name one.

The route runs a judge on the same engine as an SDK CI/CD item, and gets the
same rule. Without a model, the engine runs a judge on its template's
configured model, or on a decision model the request's config names; it
reads neither the template's saved model nor any other model in the config,
so with neither the evaluation fails. Such a request takes the model its
config names (as the engine reads the config, with the keys in params
lifted), else its template's saved model: it runs on it, the usage check
before the run uses its type, the charge is the one of a request that named
it, and an async evaluation stores it. With no model at all, the request is
refused with a 400 naming model, its field for the model, before anything is
created, checked, run or charged. A CI/CD item, whose field is model_name,
is still refused under model_name.

Every other request is left as it is: a judge the engine has a model for, a
request that names a model, agents, system templates and code evaluations.
A judge whose run_config is set but is not an object is refused for its
run_config, whatever its model.

The real serializers, view, evaluation runner and evaluation worker run.
Replaced: the start of the evaluation workflow and analytics, and in the
tests that run an evaluation, the usage check, the cost log, the engine and
the usage emit (the CI/CD tests' worker). Where nothing may run, the view's
runners are replaced and must not be called.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model_hub.models.evaluation import Evaluation, StatusChoices
from sdk.tests import test_eval_ci_cd_judge_model as cicd
from sdk.tests.test_eval_ci_cd_judge_model import (
    AGENT,
    BUILTIN_JUDGE,
    CODE,
    FLASH,
    JUDGE,
    LARGE,
    SMALL,
    charge,
    engine_model,
    item,
    refusal,
    template,
)
from sdk.views import evaluations as views
from tracer.models.external_eval_config import ExternalEvalConfig, PlatformChoices

pytestmark = pytest.mark.django_db

URL = "/sdk/api/v1/new-eval/"
MODES = pytest.mark.parametrize("mode", ["sync", "async"])
NO_SAVED_MODEL = (
    "This evaluation template has no saved model, so pass model to choose the "
    "model it runs on."
)

# The CI/CD tests' evaluation worker and endpoint, bound here so pytest finds
# the fixtures.
worker = cicd.worker
pipeline = cicd.pipeline


@pytest.fixture
def new_eval(auth_client, monkeypatch):
    """POST and GET of the route; an async evaluation's workflow start is
    recorded, and no analytics are sent."""
    from tfc.temporal import evaluations as temporal_evaluations

    started = Mock()
    monkeypatch.setattr(temporal_evaluations, "start_evaluation_workflow", started)
    monkeypatch.setattr(views, "track_mixpanel_event", Mock())

    def post(row, mode, **fields):
        return auth_client.post(
            URL,
            {
                "eval_name": row.name,
                "inputs": {"query": "q"},
                "is_async": mode == "async",
                **fields,
            },
            format="json",
        )

    def accepted(row, mode, **fields):
        """The evaluation of an accepted POST."""
        response = post(row, mode, **fields)
        assert response.status_code == 200, response.content.decode()[:500]
        [entry] = response.json()["result"]
        [result] = entry["evaluations"]
        return Evaluation.no_workspace_objects.get(id=result["eval_id"])

    def result(evaluation):
        return auth_client.get(f"{URL}?eval_id={evaluation.id}")

    return SimpleNamespace(post=post, accepted=accepted, result=result, started=started)


@pytest.fixture
def nothing_runs(monkeypatch):
    """The view's runners, replaced: every evaluation the route creates,
    checks, runs or charges goes through one of them."""
    runners = SimpleNamespace(
        single=Mock(return_value={"name": "not run", "eval_id": "not run"}),
        batched=Mock(return_value=[]),
        queued=Mock(return_value=[{"evaluations": []}]),
    )
    monkeypatch.setattr(views, "_run_standalone_eval", runners.single)
    monkeypatch.setattr(views, "_run_batched_standalone_eval", runners.batched)
    monkeypatch.setattr(views, "_handle_async_eval", runners.queued)
    return runners


def ran(new_eval, worker, row, mode, **fields):
    """The evaluation of an accepted request, after it ran: in the request
    when sync, by the evaluation worker when async."""
    evaluation = new_eval.accepted(row, mode, **fields)
    if mode == "async":
        worker.run(evaluation)
    return evaluation


def assert_nothing_ran(runners):
    runners.single.assert_not_called()
    runners.batched.assert_not_called()
    runners.queued.assert_not_called()


@pytest.mark.requires_ee
@MODES
def test_a_judge_without_a_model_runs_on_its_saved_model(
    new_eval, worker, organization, workspace, mode
):
    judge = template(organization, workspace, JUDGE, model="turing_small")

    evaluation = ran(new_eval, worker, judge, mode)

    assert worker.order == [
        ("check", SMALL),
        ("log", SMALL),
        ("run", "turing_small"),
    ]
    assert worker.cost_logs[0]["config"]["model"] == "turing_small"
    assert [event.event_type for event in worker.events] == [SMALL]
    assert evaluation.model == "turing_small"


@pytest.mark.requires_ee
@MODES
@pytest.mark.parametrize(
    "config",
    [
        {"run_config": {"model": "turing_flash"}},
        {"model": "turing_flash"},
        {"params": {"run_config": {"model": "turing_flash"}}},
    ],
    ids=["run-config", "top-level", "params"],
)
def test_a_judge_without_a_model_runs_on_the_model_its_config_names(
    new_eval, worker, organization, workspace, config, mode
):
    judge = template(organization, workspace, JUDGE, model="turing_small")

    evaluation = ran(new_eval, worker, judge, mode, config=config)

    assert worker.order == [
        ("check", FLASH),
        ("log", FLASH),
        ("run", "turing_flash"),
    ]
    assert worker.cost_logs[0]["config"]["model"] == "turing_flash"
    assert [event.event_type for event in worker.events] == [FLASH]
    assert evaluation.eval_config == config


def test_a_batch_from_a_judge_without_a_model_runs_on_its_saved_model(
    new_eval, monkeypatch, organization, workspace
):
    """Sync: the batch's runner is given the saved model for every input."""
    batched = Mock(return_value=[])
    monkeypatch.setattr(views, "_run_batched_standalone_eval", batched)
    judge = template(organization, workspace, JUDGE, model="turing_small")

    response = new_eval.post(judge, "sync", inputs={"query": ["a", "b"]})

    assert response.status_code == 200, response.content.decode()[:500]
    batched.assert_called_once()
    assert batched.call_args.kwargs["inputs"] == {"query": ["a", "b"]}
    assert batched.call_args.kwargs["model"] == "turing_small"


def test_every_evaluation_of_a_queued_batch_is_stored_with_the_saved_model(
    new_eval, organization, workspace
):
    judge = template(organization, workspace, JUDGE, model="turing_small")

    response = new_eval.post(judge, "async", inputs={"query": ["a", "b"]})

    assert response.status_code == 200, response.content.decode()[:500]
    [entry] = response.json()["result"]
    stored = [
        Evaluation.no_workspace_objects.get(id=result["eval_id"])
        for result in entry["evaluations"]
    ]
    assert [(row.input_data, row.model_name) for row in stored] == [
        ({"query": "a"}, "turing_small"),
        ({"query": "b"}, "turing_small"),
    ]


@MODES
@pytest.mark.parametrize(
    "missing",
    [{}, {"model": None}, {"model": ""}],
    ids=["absent", "null", "empty"],
)
def test_a_judge_without_a_model_is_refused_when_its_template_has_no_model(
    new_eval, nothing_runs, organization, workspace, missing, mode
):
    judge = template(organization, workspace, JUDGE)

    response = new_eval.post(judge, mode, **missing)

    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal("model", NO_SAVED_MODEL)
    assert_nothing_ran(nothing_runs)
    new_eval.started.assert_not_called()


def test_a_ci_cd_item_without_a_model_is_still_refused_under_model_name(
    pipeline, organization, workspace
):
    """The CI/CD route shares the refusal; its items pass the model as
    model_name, so its message is unchanged."""
    judge = template(organization, workspace, JUDGE)

    response = pipeline.post(item(judge)).response

    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal(
        "eval_data.0.model_name",
        "This evaluation template has no saved model, so pass model_name to "
        "choose the model it runs on.",
    )


@pytest.mark.parametrize("saved", ["turing_small", None], ids=["saved", "unsaved"])
def test_a_judge_configured_without_a_model_is_accepted_and_stored_without_one(
    auth_client, organization, workspace, saved
):
    """configure-evaluations stores the configuration it is sent: a judge
    without a model is neither refused nor given its template's model."""
    judge = template(organization, workspace, JUDGE, model=saved)
    credentials = dict.fromkeys(
        ExternalEvalConfig.get_required_credentials(PlatformChoices.LANGFUSE), "unused"
    )

    response = auth_client.post(
        "/sdk/api/v1/configure-evaluations/",
        {
            "eval_config": {"eval_templates": judge.name, "inputs": {"query": "q"}},
            "platform": "langfuse",
            "custom_eval_name": "judge on langfuse",
            **credentials,
        },
        format="json",
    )

    assert response.status_code == 200, response.content.decode()[:500]
    assert response.json()["result"] == {"message": "Configuration is valid."}
    [stored] = ExternalEvalConfig.no_workspace_objects.filter(eval_template=judge)
    assert stored.model is None


@pytest.mark.requires_ee
@MODES
def test_a_judge_on_its_saved_model_is_charged_as_if_the_model_were_named(
    new_eval, worker, organization, workspace, mode
):
    judge = template(organization, workspace, JUDGE, model="turing_small")
    fallback = ran(new_eval, worker, judge, mode)
    named = ran(new_eval, worker, judge, mode, model="turing_small")

    assert fallback.model_name == named.model_name
    assert fallback.model == named.model == "turing_small"
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
@MODES
def test_a_judge_that_runs_today_costs_and_answers_the_same(
    new_eval, worker, organization, workspace, mode
):
    """A judge whose template configures its model runs on it without a
    model in the request, whatever model the template saves: nothing is
    resolved for it, so its usage check, cost log, charge and answer are
    those of a request that names no model."""
    configured = {**JUDGE, "model": "turing_small"}
    judge = template(organization, workspace, configured, model="turing_flash")

    evaluation = ran(new_eval, worker, judge, mode)

    assert worker.order == [("check", LARGE), ("log", LARGE), ("run", None)]
    assert "model" not in worker.cost_logs[0]["config"]
    [event] = worker.events
    assert event.event_type == LARGE
    assert event.amount == pytest.approx(0.25)
    assert engine_model(worker.requests[0]) == "turing_small"
    assert (
        evaluation.status,
        evaluation.value,
        evaluation.reason,
        evaluation.model,
        evaluation.model_name,
    ) == (StatusChoices.COMPLETED, "1.0", "ok", None, None)


@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "config",
    [{}, {"run_config": {"model": "turing_flash"}}],
    ids=["no-model-in-config", "another-model-in-config"],
)
def test_a_judge_on_its_configured_model_is_left_as_it_is(
    new_eval, worker, organization, workspace, config
):
    """A model in the request's run_config does not reach a judge, so the
    judge runs on its configured model and keeps the usage type of a
    request that names no model."""
    judge = template(organization, workspace, {**JUDGE, "model": "turing_small"})

    ran(new_eval, worker, judge, "sync", config=config)

    assert worker.order == [("check", LARGE), ("log", LARGE), ("run", None)]
    assert [event.event_type for event in worker.events] == [LARGE]
    assert engine_model(worker.requests[0]) == "turing_small"


def test_a_judge_whose_config_names_a_decision_model_is_left_as_it_is(
    new_eval, organization, workspace
):
    """The engine runs a decision model named in a judge's config (or refuses
    it as unknown) without a model in the request, so nothing is written to
    the evaluation, not even the template's saved model."""
    judge = template(organization, workspace, JUDGE, model="turing_small")
    config = {"run_config": {"model": "jev-latest"}}

    evaluation = new_eval.accepted(judge, "async", config=config)

    assert evaluation.model_name is None
    assert evaluation.eval_config == config
    new_eval.started.assert_called_once_with(evaluation_id=str(evaluation.id))


@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "configured, saved",
    [("turing_small", None), (None, "turing_small"), (None, None)],
    ids=["configured-model", "saved-model", "no-model"],
)
def test_an_agent_without_a_model_is_left_as_it_is(
    new_eval, worker, organization, workspace, configured, saved
):
    """An agent sent without a model is not given one, whatever its template
    sets: the engine gives the agent its configured model or none, and the
    usage check and the charge keep the type of a request that names no
    model."""
    config = {**AGENT, "model": configured} if configured else AGENT
    agent = template(organization, workspace, config, model=saved)

    ran(new_eval, worker, agent, "sync")

    assert worker.order == [("check", LARGE), ("log", LARGE), ("run", None)]
    assert "model" not in worker.cost_logs[0]["config"]
    assert [event.event_type for event in worker.events] == [LARGE]
    assert engine_model(worker.requests[0]) == configured


@pytest.mark.requires_ee
@pytest.mark.parametrize(
    "saved_model, config",
    [
        (None, {}),
        ("turing_small", {"run_config": {"model": "turing_large"}}),
    ],
    ids=["template-without-a-model", "saved-and-config-models-differ"],
)
def test_a_request_that_names_a_model_runs_and_is_checked_on_exactly_that_model(
    new_eval, worker, organization, workspace, saved_model, config
):
    judge = template(organization, workspace, JUDGE, model=saved_model)

    ran(new_eval, worker, judge, "sync", model="turing_flash", config=config)

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
            {"model": "gpt-unknown"},
            "Model name gpt-unknown is invalid please use one of the following "
            "models: turing_large, turing_small",
        ),
    ],
    ids=["missing", "invalid"],
)
def test_a_system_template_keeps_its_model_refusals(
    new_eval, nothing_runs, fields, sentence
):
    builtin = template(
        None,
        None,
        {**BUILTIN_JUDGE, "models": ["turing_large", "turing_small"]},
        model="turing_large",
        owner="system",
    )

    response = new_eval.post(builtin, "sync", **fields)

    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal("model_name", sentence)
    assert_nothing_ran(nothing_runs)


def test_a_system_template_without_a_model_list_is_left_as_it_is(new_eval):
    builtin = template(
        None, None, BUILTIN_JUDGE, model="turing_large", owner="system"
    )

    evaluation = new_eval.accepted(builtin, "async")

    assert evaluation.model_name is None


def test_a_code_eval_without_a_model_is_accepted_without_a_model(
    new_eval, organization, workspace
):
    code = template(organization, workspace, CODE)

    evaluation = new_eval.accepted(code, "async")

    assert evaluation.status == StatusChoices.PENDING
    assert evaluation.model_name is None


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
    new_eval, organization, workspace, config, field, saved
):
    """A run_config that is set but is not an object is refused for that
    field whether or not the template has a saved model: the evaluation is
    neither given a model nor refused for lacking one, and none is created
    or started."""
    judge = template(organization, workspace, JUDGE, model=saved)

    response = new_eval.post(judge, "async", config=config)

    assert response.status_code == 400, response.content.decode()[:500]
    assert response.json() == refusal(f"config.{field}", "must be an object")
    assert not Evaluation.no_workspace_objects.filter(eval_template=judge).exists()
    new_eval.started.assert_not_called()


@pytest.mark.requires_ee
def test_the_async_result_names_the_model_the_evaluation_ran_on(
    new_eval, worker, organization, workspace
):
    judge = template(organization, workspace, JUDGE, model="turing_small")
    evaluation = new_eval.accepted(judge, "async")
    assert evaluation.model_name == "turing_small"

    worker.run(evaluation)
    response = new_eval.result(evaluation)

    assert response.status_code == 200, response.content.decode()[:500]
    result = response.json()["result"]
    assert result["eval_status"] == StatusChoices.COMPLETED
    assert result["result"]["model"] == "turing_small"
    assert worker.requests[0].model == "turing_small"
