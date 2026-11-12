"""C7 selected definitions compose with the shared Choices partial overlay.

Static-only during the chain rehearsal; execute with the isolated cloud suite.
No evaluator in this module calls a provider or a database.
"""

from types import SimpleNamespace

import pytest

from evaluations.engine import instance
from evaluations.engine.formatting import format_eval_value
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401
from model_hub.views.eval_runner import EvaluationRunner

pytestmark = pytest.mark.usefixtures("no_external_lookups")

LIVE = {"Good": 0.9, "Neutral": 0.5, "Bad": 0.0}
SAVED = {"Good": 0.2, "Neutral": 0.3, "Bad": 0.1}
PARTIAL = {"Good": 0.7}


def _template(eval_type):
    return SimpleNamespace(
        id="template-1",
        config={
            "eval_type_id": eval_type,
            "output": "choices",
            "model": "turing_large",
            "rule_prompt": "Judge the answer",
        },
        choice_scores=dict(LIVE),
        choices=list(LIVE),
        multi_choice=False,
        pass_threshold=0.5,
        organization=None,
        workspace=None,
    )


def _version(scores):
    return SimpleNamespace(
        id="version-1",
        version_number=1,
        deleted=False,
        config_snapshot={
            "choice_scores": scores,
            "choices": ["Good", "Neutral", "Bad"],
            "multi_choice": True,
            "output": "choices",
        },
        choice_scores=scores,
        multi_choice=True,
        prompt_messages=[],
        model=None,
        pass_threshold=0.5,
    )


def _runner(template, version, runtime_config, pin="version-1"):
    runner = EvaluationRunner("binding-1", format_output=True)
    runner.eval_template = template
    runner.user_eval_metric = SimpleNamespace(
        config=runtime_config, pinned_version_id=pin, pinned_version=version
    )
    runner._resolved_version = version
    runner.version_number = version.version_number if version else None
    return runner


def _assert_consumers(runner, version, expected_scores, saved, monkeypatch):
    # Only version lookup and the evaluator constructor are replaced. The real
    # configuration preparation, selection, overlay and result conversion run.
    monkeypatch.setattr(instance, "resolve_version", lambda *args: version)
    built = []

    class Judge:
        def __init__(self, **config):
            built.append(config)

    runner._create_eval_instance(config={}, eval_class=Judge)
    error_config = runner._prepare_eval_config({})
    assert runner._effective_choice_scores() == expected_scores
    for config in (built[0], error_config):
        assert config["choice_scores"] == expected_scores
        assert config["choices"] == ["Good", "Neutral", "Bad"]
        assert config["multi_choice"] is saved
        assert config["output_type"] == "choices"
    expected_value = (
        {"score": expected_scores["Good"], "choice": "Good"}
        if expected_scores
        else "Good"
    )
    for row in (None, object()):
        assert runner.format_output(
            {"output": "choices", "data": "Good"}, row=row
        ) == expected_value


@pytest.mark.parametrize("eval_type", ["CustomPromptEvaluator", "AgentEvaluator"])
@pytest.mark.parametrize("enabled", [False, True, "true"])
@pytest.mark.parametrize("pin_state", ["matching", "unpinned", "missing", "deleted", "mismatch"])
def test_select_base_before_overlay_across_all_consumers(
    eval_type, enabled, pin_state, settings, monkeypatch
):
    settings.DSX_EVAL_SAVED_CHOICES_ENABLED = enabled
    version = _version(dict(SAVED))
    pin = None if pin_state == "unpinned" else "version-1"
    if pin_state == "missing":
        version = None
    elif pin_state == "deleted":
        version.deleted = True
    elif pin_state == "mismatch":
        version.id = "version-2"
    runtime_config = {
        "choice_scores": {"Good": 0.1},
        "run_config": {"choice_scores": dict(PARTIAL)},
    }
    runner = _runner(_template(eval_type), version, runtime_config, pin)
    use_saved = enabled is True and pin_state == "matching"
    expected = {**(SAVED if use_saved else LIVE), **PARTIAL}
    _assert_consumers(runner, version, expected, use_saved, monkeypatch)


@pytest.mark.parametrize("eval_type", ["CustomPromptEvaluator", "AgentEvaluator"])
@pytest.mark.parametrize("saved_scores", [SAVED, {}, None])
@pytest.mark.parametrize(
    "runtime_config,override",
    [
        ({}, None),
        ({"run_config": {"choice_scores": {}}}, None),
        ({"run_config": {"choice_scores": "invalid"}}, None),
        ({"run_config": ["invalid"]}, None),
        ({"choice_scores": PARTIAL}, PARTIAL),
        ({"run_config": {"choice_scores": PARTIAL}}, PARTIAL),
        ({"run_config": {"choice_scores": {}}, "choice_scores": PARTIAL}, PARTIAL),
    ],
)
def test_empty_saved_maps_never_reintroduce_live_scores(
    eval_type, saved_scores, runtime_config, override, settings, monkeypatch
):
    settings.DSX_EVAL_SAVED_CHOICES_ENABLED = True
    version = _version(saved_scores)
    runner = _runner(_template(eval_type), version, runtime_config)
    expected = saved_scores if override is None else {**(saved_scores or {}), **override}
    _assert_consumers(runner, version, expected, True, monkeypatch)


@pytest.mark.parametrize("base", [SAVED, {}, None])
@pytest.mark.parametrize("invalid", [None, {}, [], "invalid"])
def test_selected_base_identity_and_legacy_two_argument_api(base, invalid):
    template = _template("CustomPromptEvaluator")
    runtime_config = {"run_config": {"choice_scores": invalid}}
    assert instance.resolve_choice_scores(template, runtime_config) is template.choice_scores
    assert instance.resolve_choice_scores(
        template, runtime_config, base_scores=base
    ) is base
    assert instance.resolve_choice_scores(
        template, {"choice_scores": PARTIAL}, base_scores=base
    ) == {**(base or {}), **PARTIAL}
    assert template.choice_scores == LIVE


def test_resolve_version_before_selecting_saved_base(settings, monkeypatch):
    from model_hub.models.evals_metric import EvalTemplateVersion

    settings.DSX_EVAL_SAVED_CHOICES_ENABLED = True
    version = _version(dict(SAVED))
    runner = _runner(
        _template("CustomPromptEvaluator"), version, {"choice_scores": PARTIAL}
    )
    runner._resolved_version = None
    calls = []

    def filtered(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(first=lambda: version)

    monkeypatch.setattr(EvalTemplateVersion.objects, "filter", filtered)
    assert runner._effective_choice_scores() == {**SAVED, **PARTIAL}
    assert runner._resolved_version is version
    assert calls == [
        {"eval_template": runner.eval_template, "version_number": 1, "deleted": False}
    ]


def test_explicit_runtime_config_is_used_by_error_config_and_formatting(settings, monkeypatch):
    settings.DSX_EVAL_SAVED_CHOICES_ENABLED = True
    version = _version(dict(SAVED))
    runner = _runner(_template("CustomPromptEvaluator"), version, {"choice_scores": {"Good": 0.1}})
    runner._runtime_config = {"run_config": {"choice_scores": PARTIAL}}
    monkeypatch.setattr(instance, "resolve_version", lambda *args: version)
    built = []

    class Judge:
        def __init__(self, **config):
            built.append(config)

    runner._create_eval_instance(
        config={}, eval_class=Judge, runtime_config=runner._runtime_config
    )
    expected = {**SAVED, **PARTIAL}
    assert built[0]["choice_scores"] == expected
    assert runner._prepare_eval_config({})["choice_scores"] == expected
    assert runner._effective_choice_scores() == expected


def test_pure_formatter_keeps_explicit_none_and_selected_multi_choice():
    template = _template("DeterministicEvaluator")
    data = {"output": "choices", "data": "Good"}
    assert format_eval_value(data, template) == {"score": 0.9, "choice": "Good"}
    assert format_eval_value(data, template, choice_scores=None) == "Good"
    assert format_eval_value(data, template, choice_scores={}) == "Good"
    data = {"output": "Pass/Fail", "data": ["Good", "Neutral"]}
    assert format_eval_value(data, template) == "Good"
    assert format_eval_value(data, template, multi_choice=True) == ["Good", "Neutral"]
