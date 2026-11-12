"""A pinned evaluation version is what the evaluator is built with.

These tests call the real engine (resolve_version and create_eval_instance)
and replace only the evaluator class, so nothing calls a model. A run with no
version number keeps the live template, even when the template has a default
version that differs.
"""

import uuid
from unittest.mock import patch

import pytest

from evaluations.engine import instance
from evaluations.engine.instance import create_eval_instance, resolve_version
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

LIVE_RUBRIC = "Live rubric: is {{output}} polite?"
LIVE_SYSTEM = "Live system message"
PINNED_RUBRIC = "Pinned rubric: is {{output}} correct?"
PINNED_SYSTEM = "Pinned system message"
DEFAULT_RUBRIC = "Default rubric: is {{output}} short?"
DEFAULT_SYSTEM = "Default system message"
REFUSAL = (
    "This evaluation's saved version uses a different model than its key "
    "allows. Pick a key for that model or unpin the version."
)


class Built:
    """Records what the engine built the evaluator with. Never runs a model."""

    made = 0

    def __init__(self, **kwargs):
        type(self).made += 1
        self.kwargs = kwargs

    def run(self, *args, **kwargs):
        raise AssertionError("the evaluator is never run in these tests")


@pytest.fixture(autouse=True)
def _reset_built():
    Built.made = 0
    yield


def make_template(
    organization,
    workspace,
    *,
    model="turing_small",
    provider=None,
    eval_type_id="CustomPromptEvaluator",
):
    config = {
        "eval_type_id": eval_type_id,
        "output": "Pass/Fail",
        "required_keys": ["output"],
        "rule_prompt": LIVE_RUBRIC,
        "system_prompt": LIVE_SYSTEM,
        "model": model,
    }
    if provider:
        config["provider"] = provider
    return EvalTemplate.no_workspace_objects.create(
        name=f"pinned-engine-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="llm",
        criteria=LIVE_RUBRIC,
        config=config,
        model=model,
    )


def make_version(
    template, number, rubric, system, *, model="turing_small", default=False
):
    return EvalTemplateVersion.no_workspace_objects.create(
        eval_template=template,
        version_number=number,
        is_default=default,
        prompt_messages=[
            {"role": "system", "content": system},
            {"role": "user", "name": "eval_prompt", "content": rubric},
        ],
        config_snapshot=dict(template.config),
        criteria=rubric,
        model=model,
        organization=template.organization,
        workspace=template.workspace,
    )


def build(template, organization, **kwargs):
    kwargs.setdefault("model", None)
    built, _criteria = create_eval_instance(
        eval_class=Built,
        eval_template=template,
        config={},
        organization_id=str(organization.id),
        **kwargs,
    )
    return built.kwargs


@pytest.mark.django_db
def test_resolve_version_returns_the_version_a_pin_names(organization, workspace):
    template = make_template(organization, workspace)
    pinned = make_version(template, 1, PINNED_RUBRIC, PINNED_SYSTEM)
    make_version(template, 2, DEFAULT_RUBRIC, DEFAULT_SYSTEM, default=True)

    assert resolve_version(template, 1, organization) == pinned


@pytest.mark.django_db
def test_no_version_number_resolves_nothing_even_with_a_default(
    organization, workspace
):
    template = make_template(organization, workspace)
    make_version(template, 1, DEFAULT_RUBRIC, DEFAULT_SYSTEM, default=True)

    assert resolve_version(template, None, organization) is None


@pytest.mark.django_db
def test_the_evaluator_is_built_with_the_pinned_version(organization, workspace):
    template = make_template(organization, workspace)
    make_version(template, 1, PINNED_RUBRIC, PINNED_SYSTEM)
    make_version(template, 2, DEFAULT_RUBRIC, DEFAULT_SYSTEM, default=True)

    built = build(template, organization, version_number=1)

    assert built["rule_prompt"] == PINNED_RUBRIC
    assert built["system_prompt"] == PINNED_SYSTEM


@pytest.mark.django_db
def test_no_pin_builds_the_live_template_even_when_the_default_differs(
    organization, workspace
):
    template = make_template(organization, workspace)
    make_version(template, 1, DEFAULT_RUBRIC, DEFAULT_SYSTEM, default=True)

    built = build(template, organization)

    assert built["rule_prompt"] == LIVE_RUBRIC
    assert built["system_prompt"] == LIVE_SYSTEM


@pytest.mark.django_db
def test_a_pinned_model_without_a_key_is_refused_before_the_evaluator_exists(
    organization, workspace
):
    template = make_template(organization, workspace)
    make_version(template, 1, PINNED_RUBRIC, PINNED_SYSTEM, model="gpt-4o")
    asked = []

    def no_key(model, *args, **kwargs):
        asked.append(model)
        raise ValueError(f"No API key found for organization {organization.id}")

    with patch.object(instance, "_get_api_key", side_effect=no_key):
        with pytest.raises(ValueError) as refused:
            build(template, organization, version_number=1, model="turing_small")

    assert str(refused.value) == getattr(
        instance, "PINNED_VERSION_MODEL_KEY_MESSAGE", None
    )
    assert str(refused.value) == (
        "This evaluation's saved version uses a different model than its key "
        "allows. Pick a key for that model or unpin the version."
    )
    assert asked == ["gpt-4o"]
    assert Built.made == 0


@pytest.mark.django_db
def test_a_pinned_model_gets_the_provider_and_key_for_that_model(
    organization, workspace
):
    template = make_template(
        organization, workspace, model="claude-3-5-sonnet", provider="anthropic"
    )
    make_version(template, 1, PINNED_RUBRIC, PINNED_SYSTEM, model="gpt-4o")
    providers = {"gpt-4o": "openai", "claude-3-5-sonnet": "anthropic"}

    with (
        patch.object(
            instance,
            "_get_api_key",
            side_effect=lambda model, *args, **kwargs: f"key-for-{model}",
        ),
        patch(
            "model_hub.utils.llm_providers.get_provider_for_model",
            side_effect=lambda model, *args, **kwargs: providers.get(model),
        ),
    ):
        built = build(
            template, organization, version_number=1, model="claude-3-5-sonnet"
        )

    assert built["model"] == "gpt-4o"
    assert built["api_key"] == "key-for-gpt-4o"
    assert built["provider"] == "openai"
    assert built["rule_prompt"] == PINNED_RUBRIC


@pytest.mark.django_db
def test_a_caller_that_keeps_its_model_runs_the_pinned_prompt_on_that_model(
    organization, workspace
):
    template = make_template(organization, workspace)
    make_version(template, 1, PINNED_RUBRIC, PINNED_SYSTEM, model="gpt-4o")

    def no_lookup(model, *args, **kwargs):
        raise AssertionError(f"no key is looked up for {model}")

    with patch.object(instance, "_get_api_key", side_effect=no_lookup):
        built = build(
            template,
            organization,
            version_number=1,
            model="turing_flash",
            keep_model=True,
        )

    assert built["model"] == "turing_flash"
    assert built["provider"] == "turing"
    assert built["rule_prompt"] == PINNED_RUBRIC


@pytest.mark.django_db
def test_a_pinned_agent_evaluator_runs_the_pinned_model_over_the_card_model(
    organization, workspace
):
    template = make_template(organization, workspace, eval_type_id="AgentEvaluator")
    make_version(template, 1, PINNED_RUBRIC, PINNED_SYSTEM, model="turing_flash")

    built = build(
        template,
        organization,
        version_number=1,
        model="turing_small",
        runtime_config={"run_config": {"model": "turing_small"}},
    )

    assert built["model"] == "turing_flash"
    assert built["rule_prompt"] == PINNED_RUBRIC


@pytest.mark.django_db
@pytest.mark.parametrize("model", ["gpt-4o", "acme-custom-judge"])
def test_a_pinned_agent_model_finds_its_key_like_an_unpinned_agent(
    organization, workspace, model
):
    """One routing rule. The core engine looks up no key for an agent
    evaluator, pinned or not; the agent finds its own when it runs. So a
    pinned agent model with no stored key, including a custom-provider model
    the judge key lookup skips, is built exactly like an unpinned agent
    naming that model: same model, no refusal, no lookup."""
    template = make_template(organization, workspace, eval_type_id="AgentEvaluator")
    make_version(template, 1, PINNED_RUBRIC, PINNED_SYSTEM, model=model)

    def no_lookup(looked_up, *args, **kwargs):
        raise AssertionError(f"no key is looked up for agent model {looked_up}")

    with patch.object(instance, "_get_api_key", side_effect=no_lookup):
        pinned = build(template, organization, version_number=1, model="turing_small")
        unpinned = build(template, organization, model=model)
        # The runners' pre-debit check makes no exception for agents either.
        preflight = getattr(instance, "refuse_pinned_model_without_key", None)
        if preflight is not None:
            preflight(
                template,
                version_number=1,
                model="turing_small",
                organization_id=str(organization.id),
            )

    assert pinned["model"] == unpinned["model"] == model
    assert Built.made == 2


@pytest.mark.django_db
def test_the_runner_preflight_refuses_a_pinned_model_without_a_key(
    organization, workspace
):
    """The check runners make before their up-front debit (LLM-as-judge)."""
    template = make_template(organization, workspace)
    make_version(template, 1, PINNED_RUBRIC, PINNED_SYSTEM, model="gpt-4o")
    preflight = getattr(instance, "refuse_pinned_model_without_key", None)
    assert preflight is not None, "no preflight before the debit"
    asked = []

    def no_key(model, *args, **kwargs):
        asked.append(model)
        raise ValueError(f"No API key found for organization {organization.id}")

    with patch.object(instance, "_get_api_key", side_effect=no_key):
        with pytest.raises(ValueError) as refused:
            preflight(
                template,
                version_number=1,
                model="turing_small",
                organization_id=str(organization.id),
            )
        # No pin, no check: an unpinned run is never refused for a version.
        preflight(
            template,
            version_number=None,
            model="turing_small",
            organization_id=str(organization.id),
        )

    assert str(refused.value) == REFUSAL
    assert asked == ["gpt-4o"]
