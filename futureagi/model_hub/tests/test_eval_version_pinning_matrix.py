"""Conformance matrix for eval version pinning.

Asserts that a pinned version — not the live template — is what reaches the
evaluator, across both LLM-judge and agent eval types, for versions that carry
prompt_messages and for versions that carry only a config_snapshot.
"""

import pytest

from evaluations.engine.instance import create_eval_instance
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

LIVE_PROMPT = "LIVE prompt {{input}}"
V1_PROMPT = "V1 prompt {{input}}"


class Capture:
    """Stands in for the evaluator; records the config it is constructed with."""

    last = None

    def __init__(self, **kwargs):
        Capture.last = kwargs


def _live_config(eval_type_id):
    config = {
        "eval_type_id": eval_type_id,
        "rule_prompt": LIVE_PROMPT,
        "system_prompt": "LIVE system",
        "model": "turing_large",
        "output": "Pass/Fail",
        "check_internet": True,
        "required_keys": ["input", "output"],
        "template_format": "mustache",
        "few_shot_examples": [{"input": "LIVE-fs", "output": "x"}],
        "messages": [{"role": "user", "content": "LIVE turn"}],
        "choices": ["Passed", "Failed"],
    }
    if eval_type_id == "AgentEvaluator":
        config.update(
            {
                "agent_mode": "agent",
                "summary": {"type": "short"},
                "data_injection": {"full_row": True},
                "tools": {"live_tool": {}},
                "knowledge_bases": ["live-kb"],
            }
        )
    return config


def _v1_config(eval_type_id):
    config = {
        "eval_type_id": eval_type_id,
        "rule_prompt": V1_PROMPT,
        "system_prompt": "V1 system",
        "model": "turing_small",
        "output": "Pass/Fail",
        "check_internet": False,
        "required_keys": ["input"],
        "template_format": "mustache",
        "few_shot_examples": [{"input": "V1-fs", "output": "x"}],
        "messages": [{"role": "user", "content": "V1 turn"}],
        "choices": ["V1-Yes", "V1-No"],
    }
    if eval_type_id == "AgentEvaluator":
        config.update(
            {
                "agent_mode": "prompt",
                "summary": {"type": "concise"},
                "data_injection": {"variables_only": True},
                "tools": {"v1_tool": {}},
                "knowledge_bases": ["v1-kb"],
            }
        )
    return config


def _make_template(organization, workspace, eval_type_id, owner=OwnerChoices.USER.value):
    return EvalTemplate.no_workspace_objects.create(
        name=f"pinning-matrix-{eval_type_id.lower()}-{owner}",
        organization=organization,
        workspace=workspace,
        owner=owner,
        config=_live_config(eval_type_id),
        criteria=LIVE_PROMPT,
        model="turing_large",
        pass_threshold=0.5,
        choices=["Passed", "Failed"],
        visible_ui=True,
    )


def _make_v1(template, organization, *, with_prompt_messages):
    eval_type_id = template.config["eval_type_id"]
    prompt_messages = (
        [
            {"role": "system", "name": "system_prompt", "content": "V1 system"},
            {"role": "user", "name": "eval_prompt", "content": V1_PROMPT},
        ]
        if with_prompt_messages
        else []
    )
    return EvalTemplateVersion.objects.create_version(
        eval_template=template,
        prompt_messages=prompt_messages,
        config_snapshot=_v1_config(eval_type_id),
        criteria=V1_PROMPT,
        model="turing_small",
        organization=organization,
        pass_threshold=0.9,
        choice_scores={"V1-Yes": 1.0, "V1-No": 0.0},
    )


def _build(template, organization, version, runtime_config=None):
    """Build the evaluator config the way every runner does."""
    Capture.last = None
    create_eval_instance(
        eval_class=Capture,
        eval_template=template,
        config={},
        model="turing_large",
        runtime_config=runtime_config or {},
        organization_id=str(organization.id),
        version_number=version.version_number if version else None,
        is_futureagi=False,
    )
    return Capture.last or {}


@pytest.fixture
def eval_type_id(request):
    return request.param


@pytest.mark.unit
@pytest.mark.django_db
@pytest.mark.parametrize(
    "eval_type_id", ["CustomPromptEvaluator", "AgentEvaluator"], indirect=True
)
class TestPinnedVersionReachesEvaluator:
    """The pinned version drives the evaluator config, not the live template."""

    @pytest.mark.parametrize("with_prompt_messages", [True, False])
    def test_prompt_comes_from_the_pin(
        self, organization, workspace, eval_type_id, with_prompt_messages
    ):
        template = _make_template(organization, workspace, eval_type_id)
        v1 = _make_v1(template, organization, with_prompt_messages=with_prompt_messages)

        config = _build(template, organization, v1)

        assert config["rule_prompt"] == V1_PROMPT

    def test_scoring_fields_come_from_the_pin(
        self, organization, workspace, eval_type_id
    ):
        template = _make_template(organization, workspace, eval_type_id)
        v1 = _make_v1(template, organization, with_prompt_messages=True)

        config = _build(template, organization, v1)

        assert config["choices"] == ["V1-Yes", "V1-No"]
        assert config["choice_scores"] == {"V1-Yes": 1.0, "V1-No": 0.0}
        assert config["pass_threshold"] == 0.9

    def test_model_comes_from_the_pin(self, organization, workspace, eval_type_id):
        template = _make_template(organization, workspace, eval_type_id)
        v1 = _make_v1(template, organization, with_prompt_messages=True)

        config = _build(template, organization, v1)

        assert config["model"] == "turing_small"

    def test_editing_the_template_does_not_change_a_pinned_run(
        self, organization, workspace, eval_type_id
    ):
        template = _make_template(organization, workspace, eval_type_id)
        v1 = _make_v1(template, organization, with_prompt_messages=True)

        template.config = {**template.config, "rule_prompt": "EDITED AFTER PINNING"}
        template.save(update_fields=["config"])
        template.refresh_from_db()

        config = _build(template, organization, v1)

        assert config["rule_prompt"] == V1_PROMPT

    def test_unpinned_run_uses_the_live_template(
        self, organization, workspace, eval_type_id
    ):
        template = _make_template(organization, workspace, eval_type_id)

        config = _build(template, organization, None)

        assert config["rule_prompt"] == LIVE_PROMPT


@pytest.mark.unit
@pytest.mark.django_db
class TestAgentToggles:
    """Agent-only runtime toggles are restored from the pinned snapshot."""

    def test_toggles_come_from_the_pin(self, organization, workspace):
        template = _make_template(organization, workspace, "AgentEvaluator")
        v1 = _make_v1(template, organization, with_prompt_messages=True)

        config = _build(template, organization, v1)

        assert config["check_internet"] is False
        assert config["summary"] == {"type": "concise"}
        assert config["agent_mode"] == "prompt"
        assert config["data_injection"] == {"variables_only": True}
        assert config["tools"] == {"v1_tool": {}}
        assert config["knowledge_bases"] == ["v1-kb"]

    def test_run_config_still_overrides_the_pin(self, organization, workspace):
        """Per-binding toggles win over the version — the documented ordering."""
        template = _make_template(organization, workspace, "AgentEvaluator")
        v1 = _make_v1(template, organization, with_prompt_messages=True)

        config = _build(
            template,
            organization,
            v1,
            runtime_config={"run_config": {"check_internet": True}},
        )

        assert config["check_internet"] is True


@pytest.mark.unit
@pytest.mark.django_db
class TestDegradation:
    """Unresolvable pins fall back without raising."""

    def test_unknown_version_number_falls_back_to_the_template(
        self, organization, workspace
    ):
        template = _make_template(organization, workspace, "CustomPromptEvaluator")
        _make_v1(template, organization, with_prompt_messages=True)

        Capture.last = None
        create_eval_instance(
            eval_class=Capture,
            eval_template=template,
            config={},
            model="turing_large",
            runtime_config={},
            organization_id=str(organization.id),
            version_number=9999,
            is_futureagi=False,
        )

        assert Capture.last["rule_prompt"] == LIVE_PROMPT

    def test_template_without_versions_uses_its_own_config(
        self, organization, workspace
    ):
        template = _make_template(organization, workspace, "CustomPromptEvaluator")

        config = _build(template, organization, None)

        assert config["rule_prompt"] == LIVE_PROMPT
        assert config["check_internet"] is True


@pytest.mark.unit
@pytest.mark.django_db
class TestSystemTemplatesAreNeverPinned:
    def test_resolve_pin_for_new_binding_returns_none(self, organization, workspace):
        from model_hub.services.eval_version_pinning import (
            is_versioned_template,
            resolve_pin_for_new_binding,
        )

        template = _make_template(
            organization,
            workspace,
            "CustomPromptEvaluator",
            owner=OwnerChoices.SYSTEM.value,
        )
        version = _make_v1(template, organization, with_prompt_messages=True)

        assert is_versioned_template(template) is False
        assert resolve_pin_for_new_binding(template, str(version.id)) is None

    def test_resolve_version_for_binding_returns_none(self, organization, workspace):
        from model_hub.services.eval_version_pinning import resolve_version_for_binding

        template = _make_template(
            organization,
            workspace,
            "CustomPromptEvaluator",
            owner=OwnerChoices.SYSTEM.value,
        )
        version = _make_v1(template, organization, with_prompt_messages=True)

        assert resolve_version_for_binding(template, version) is None


@pytest.mark.unit
@pytest.mark.django_db
class TestSetAsDefault:
    def test_opt_out_leaves_the_template_default_alone(self, organization, workspace):
        template = _make_template(organization, workspace, "CustomPromptEvaluator")
        v1 = _make_v1(template, organization, with_prompt_messages=True)
        assert v1.is_default is True

        v2 = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            config_snapshot={"rule_prompt": "binding-scoped edit"},
            criteria="binding-scoped edit",
            model="turing_large",
            organization=organization,
            set_as_default=False,
        )

        v1.refresh_from_db()
        assert v2.is_default is False
        assert v1.is_default is True
        assert EvalTemplateVersion.objects.get_default(template).id == v1.id
