"""`run_eval_func` must hand the evaluator set-up a copy of the caller's nested
settings; the fake records what it was built with."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import model_hub.views.utils.evals as evals_module
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate

pytestmark = [pytest.mark.unit, pytest.mark.django_db, pytest.mark.requires_ee]


@pytest.fixture
def template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="config-isolation-judge",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="llm",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "rule_prompt": "v1",
            "output": "Pass/Fail",
            "config": {},
        },
    )


@pytest.fixture
def fake_evaluator(monkeypatch):
    """Install a fresh evaluator fake per test so `seen` starts empty."""

    class FakeEvaluator:
        seen: list[dict] = []
        run_error: Exception | None = None

        def __init__(self, **kwargs):
            type(self).seen.append(dict(kwargs))

        def run(self, **_kwargs):
            if type(self).run_error is not None:
                raise type(self).run_error
            return SimpleNamespace(
                eval_results=[
                    {
                        "data": {"input": "hello"},
                        "failure": None,
                        "reason": "ok",
                        "runtime": 0.01,
                        "model": "turing_large",
                        "metrics": None,
                        "metadata": None,
                    }
                ]
            )

    # run_eval_func looks the class up in its own module globals before the
    # registry, so the fake has to live there.
    monkeypatch.setattr(evals_module, "CustomPromptEvaluator", FakeEvaluator)
    _install_run_eval_func_patches(monkeypatch)
    return FakeEvaluator


def _install_run_eval_func_patches(monkeypatch):
    # Everything around the evaluator set-up would bill or call out; the set-up itself stays real.
    from tfc.constants.api_calls import APICallStatusChoices

    def _fake_log_and_deduct(organization, api_call_type, config=None, **_kw):
        return SimpleNamespace(
            log_id="log-001",
            config=json.dumps(config or {}),
            status=APICallStatusChoices.PROCESSING.value,
            input_token_count=0,
            save=MagicMock(),
        )

    monkeypatch.setattr(
        evals_module,
        "log_and_deduct_cost_for_api_request",
        _fake_log_and_deduct,
    )
    # Imported inside run_eval_func on every call, so patch the source module.
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.map_fields",
        lambda *_args, **_kwargs: {"input": "hello"},
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.format_output",
        lambda *_args, **_kwargs: 0.9,
    )
    monkeypatch.setattr(
        "model_hub.utils.eval_input_validation.validate_eval_inputs",
        lambda template, run_kwargs, **_kw: (None, run_kwargs),
    )


@pytest.mark.parametrize(
    "nested",
    [
        pytest.param({}, id="empty-nested-config"),
        # run_eval_func pops `mapping` from the nested dict, so a copy taken
        # after that pop would still change the caller's dict.
        pytest.param({"mapping": {"k": "v"}}, id="nested-mapping"),
    ],
)
def test_run_eval_func_leaves_caller_config_untouched(
    nested, template, organization, workspace, fake_evaluator
):
    caller = {"config": nested, "run_config": {}}
    before = copy.deepcopy(caller)

    evals_module.run_eval_func(
        caller, {"input": "x"}, template, organization, workspace=workspace
    )

    assert caller == before
    assert len(fake_evaluator.seen) == 1
    assert fake_evaluator.seen[-1]["rule_prompt"] == "v1"


def test_run_eval_func_leaves_caller_config_untouched_on_error(
    template, organization, workspace, fake_evaluator
):
    fake_evaluator.run_error = RuntimeError("judge unavailable")
    caller = {"config": {}, "run_config": {}}
    before = copy.deepcopy(caller)

    with pytest.raises(RuntimeError, match="judge unavailable"):
        evals_module.run_eval_func(
            caller, {"input": "x"}, template, organization, workspace=workspace
        )

    assert caller == before
    # The set-up ran before the failure, so its writes had a chance to leak.
    assert len(fake_evaluator.seen) == 1


def test_run_eval_func_still_honours_text_stored_by_the_caller(
    template, organization, workspace, fake_evaluator
):
    caller = {"config": {"rule_prompt": "draft"}, "run_config": {}}
    before = copy.deepcopy(caller)

    evals_module.run_eval_func(
        caller, {"input": "x"}, template, organization, workspace=workspace
    )

    assert fake_evaluator.seen[-1]["rule_prompt"] == "draft"
    assert caller == before
