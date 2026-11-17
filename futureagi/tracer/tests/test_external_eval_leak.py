"""Running an external (Langfuse) eval never writes the evaluator's settings into the
row, and each save writes only the fields its step changed.

The evaluator set-up writes its settings into the dict it is handed. For an external
eval that dict was the row's own `config`, and the row was then saved whole, so the
settings (a stored grading text among them) were kept and won over the template's on
every later run. Only the evaluator class, usage metering, field mapping, output
formatting and the Langfuse client are faked here.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate
from tfc.constants.api_calls import APICallStatusChoices
from tracer.models.external_eval_config import ExternalEvalConfig, StatusChoices
from tracer.utils import external_eval as external_eval_module

# Placeholder Langfuse settings: the config refuses to save without them, and
# every test patches the send, so they are never used.
LANGFUSE = {
    "langfuse_secret_key": "sk-lf-test",
    "langfuse_public_key": "pk-lf-test",
    "langfuse_host": "http://langfuse.test",
}

# What the evaluator set-up writes into the settings it is given.
WRITTEN_KEYS = {
    "api_key",
    "provider",
    "model",
    "rule_prompt",
    "system_prompt",
    "output_type",
    "check_internet",
    "multi_choice",
    "choices",
    "choice_scores",
    "pass_threshold",
}

USER_CONFIG = {"my_setting": "keep"}

pytestmark = pytest.mark.django_db(transaction=True)


class FakeEvaluator:
    """Installed where the engine resolves the evaluator class."""

    cost = {"total_cost": 0}
    token_usage = {}
    seen: list[dict] = []
    on_run = None

    def __init__(self, **kwargs):
        type(self).seen.append(dict(kwargs))

    def run(self, **_):
        if type(self).on_run is not None:
            type(self).on_run()
        return SimpleNamespace(
            eval_results=[
                {
                    "data": {"input": "hello"},
                    "failure": None,
                    "reason": "ok",
                    "runtime": 0.01,
                    "model": "gpt-4o",
                    "metrics": None,
                    "metadata": None,
                }
            ]
        )


@pytest.fixture(autouse=True)
def grading_patches(monkeypatch):
    FakeEvaluator.seen = []
    FakeEvaluator.on_run = None

    def _fake_log_and_deduct(organization, api_call_type, config=None, **_kw):
        return SimpleNamespace(
            log_id="log-001",
            config=json.dumps(config or {}, default=str),
            status=APICallStatusChoices.PROCESSING.value,
            input_token_count=0,
            save=MagicMock(),
        )

    monkeypatch.setattr(
        external_eval_module,
        "log_and_deduct_cost_for_api_request",
        _fake_log_and_deduct,
    )
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr(
        "evaluations.engine.registry.get_eval_class",
        lambda _eval_type_id: FakeEvaluator,
    )
    monkeypatch.setattr(
        "evaluations.engine.instance._get_api_key", lambda *_a, **_kw: "sk-org-key"
    )
    monkeypatch.setattr(
        "model_hub.views.eval_runner.EvaluationRunner.map_fields",
        lambda *_args, **_kwargs: {"input": "hello"},
    )
    monkeypatch.setattr(
        "model_hub.views.eval_runner.EvaluationRunner.format_output",
        lambda *_args, **_kwargs: 0.9,
    )
    monkeypatch.setattr(external_eval_module, "_emit_external_eval_usage", MagicMock())


@pytest.fixture
def template(user):
    return EvalTemplate.objects.create(
        name="external-leak",
        organization=user.organization,
        eval_type="llm",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "rule_prompt": "template text",
            "output": "Pass/Fail",
            "provider": "openai",
            "config": {},
        },
    )


def _make_config(user, workspace, template, config=None, **fields):
    return ExternalEvalConfig.objects.create(
        organization=user.organization,
        workspace=workspace,
        eval_template=template,
        name="external-leak",
        config=dict(USER_CONFIG) if config is None else config,
        mapping={"input": "hello"},
        model="gpt-4o",
        platform="langfuse",
        credentials=dict(LANGFUSE),
        status=StatusChoices.PENDING,
        **fields,
    )


@pytest.fixture
def config(user, workspace, template):
    return _make_config(user, workspace, template)


def _reload(config):
    return ExternalEvalConfig.objects.get(id=config.id)


def _marker_on_name(config):
    """An in-memory edit to a field none of the saves under test may write."""

    def _edit():
        config.name = "marker"

    return _edit


def test_a_run_leaves_the_rows_settings_free_of_the_evaluators_keys(config):
    with patch.object(external_eval_module, "_send_eval_result"):
        external_eval_module._execute_evaluation_on_external_platform(config)

    # The evaluator did receive the settings the set-up derived ...
    assert FakeEvaluator.seen
    assert FakeEvaluator.seen[-1]["rule_prompt"] == "template text"
    # ... and none of them reached the row, in the database or in memory.
    row = _reload(config)
    assert WRITTEN_KEYS.isdisjoint(row.config)
    assert row.config.get("my_setting") == "keep"
    assert row.eval_results["value"] == 0.9
    assert WRITTEN_KEYS.isdisjoint(config.config)


def test_a_second_run_takes_the_text_from_the_template_again(config, template):
    with patch.object(external_eval_module, "_send_eval_result"):
        external_eval_module._execute_evaluation_on_external_platform(config)
        assert FakeEvaluator.seen[-1]["rule_prompt"] == "template text"

        template.config["rule_prompt"] = "edited template text"
        template.save()
        again = ExternalEvalConfig.objects.get(id=config.id)
        external_eval_module._execute_evaluation_on_external_platform(again)

    assert FakeEvaluator.seen[-1]["rule_prompt"] == "edited template text"


def test_the_result_save_writes_only_the_result(config):
    FakeEvaluator.on_run = _marker_on_name(config)

    with patch.object(external_eval_module, "_send_eval_result"):
        external_eval_module._execute_evaluation_on_external_platform(config)

    assert config.name == "marker"
    row = _reload(config)
    assert row.name == "external-leak"
    assert row.eval_results["value"] == 0.9


def test_the_composite_result_save_writes_only_the_result(user, workspace, template):
    parent = EvalTemplate.objects.create(
        name="external-composite",
        organization=user.organization,
        eval_type="llm",
        template_type="composite",
        config={},
    )
    CompositeEvalChild.objects.create(parent=parent, child=template, order=0)
    config = _make_config(user, workspace, parent)
    outcome = SimpleNamespace(
        aggregate_score=1.0,
        aggregate_pass=True,
        summary="all passed",
        child_results=[],
    )

    def _run_children(**_kw):
        config.name = "marker"
        return outcome

    with (
        patch(
            "model_hub.utils.composite_execution.execute_composite_children_sync",
            side_effect=_run_children,
        ),
        patch.object(external_eval_module, "_send_eval_result"),
    ):
        external_eval_module._execute_composite_on_external_platform(config)

    assert config.name == "marker"
    row = _reload(config)
    assert row.name == "external-leak"
    assert row.eval_results["composite"] is True


def test_the_completed_status_save_writes_only_the_status(config):
    def _run_and_edit(running_config):
        running_config.name = "marker"

    with patch.object(
        external_eval_module,
        "_execute_evaluation_on_external_platform",
        side_effect=_run_and_edit,
    ):
        external_eval_module.run_external_eval_config(str(config.id))

    row = _reload(config)
    assert row.status == StatusChoices.COMPLETED
    assert row.name == "external-leak"


def test_the_failed_status_save_writes_only_status_and_message(config):
    def _edit_and_fail(running_config):
        running_config.name = "marker"
        raise RuntimeError("judge unavailable")

    with patch.object(
        external_eval_module,
        "_execute_evaluation_on_external_platform",
        side_effect=_edit_and_fail,
    ):
        external_eval_module.run_external_eval_config(str(config.id))

    row = _reload(config)
    assert row.status == StatusChoices.FAILED
    assert row.error_message == "judge unavailable"
    assert row.name == "external-leak"


def test_the_langfuse_log_save_writes_only_the_log(config):
    config.name = "marker"

    with patch("langfuse.Langfuse"):
        external_eval_module._send_langfuse_eval_result(config, "Passed", "ok")

    row = _reload(config)
    assert row.name == "external-leak"
    assert row.logs[-1]["type"] == "langfuse"
