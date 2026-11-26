"""The engine step, with a configured eval as the definition.

A configured eval keeps its own keys; one table turns each key into the kind the
reader reads. Its own criteria are not rewritten: a key they don't name is named in
one fixed block after them, sent with the grading and never saved on the template.
Only the agent evaluator's model call is faked here, so the shared evaluator entry,
the agent evaluator's rendering, its cuts and its usage log are the real ones.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ai_tools.base import ToolContext
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.utils.prompt_migration import config_to_prompt_messages
from simulate.models import SimulateEvalResult
from simulate.services import harness_evals
from simulate.services.ask_engine import (
    CONFIGURED_BLOCK_HEAD,
    configured_eval,
    grade_row,
)
from simulate.services.ask_selection import (
    LaunchRefused,
    check_before_launch,
    launch_gate,
)
from simulate.services.call_evidence import (
    ASK_ONLY_KEYS,
    KIND_BY_SOURCE,
    Kind,
    kind_for_key,
)
from simulate.tests import test_ask_engine as typed

# Fixtures and helpers of the typed question's tests, shared rather than copied.
# Fixtures are bound here by name so pytest finds them in this module.
LONG_TAIL_MARKER = typed.LONG_TAIL_MARKER
_launch = typed._launch
_question = typed._question
_rows = typed._rows
_grade_on_one_thread = typed._grade_on_one_thread
agent_definition = typed.agent_definition
agent_version = typed.agent_version
simulator_agent = typed.simulator_agent
scenario = typed.scenario
run = typed.run
calls = typed.calls
selection = typed.selection
decision_model = typed.decision_model
gate_passes = typed.gate_passes
queued = typed.queued

STATE = SimulateEvalResult.State
OWN_CRITERIA = "Did the agent keep to its refund limit? Answer Yes or No."


def _agent_template(
    organization,
    workspace,
    user,
    *,
    criteria=OWN_CRITERIA,
    keys=("agent_prompt",),
    eval_type_id="AgentEvaluator",
    output="choices",
    choice_scores=None,
    prompt_messages=None,
    model="turing_large",
    versioned=True,
):
    """A configured eval, saved with its first version as the product saves one."""
    choice_scores = {"Yes": 1.0, "No": 0.0} if choice_scores is None else choice_scores
    config = {
        "eval_type_id": eval_type_id,
        "output": output,
        "required_keys": list(keys),
        "rule_prompt": criteria,
        "agent_mode": "quick",
    }
    template = EvalTemplate.no_workspace_objects.create(
        name=f"refund-limit-{eval_type_id}-{output}".lower().replace("/", "-"),
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="agent" if eval_type_id == "AgentEvaluator" else "llm",
        config=config,
        criteria=criteria,
        choices=list(choice_scores),
        choice_scores=choice_scores,
        model=model,
        visible_ui=True,
    )
    if not versioned:
        return template
    EvalTemplateVersion.objects.create_version(
        eval_template=template,
        prompt_messages=(
            config_to_prompt_messages(config, criteria, eval_type_id)
            if prompt_messages is None
            else prompt_messages
        ),
        config_snapshot=config,
        criteria=criteria,
        model=model,
        user=user,
        organization=organization,
        workspace=workspace,
    )
    return template


class FakeAgentModel:
    """The agent evaluator's model call, and only that. Records each request."""

    def __init__(self, answer="No"):
        self.requests = []
        self.answer = answer

    # Set on the class as a plain callable, so it is not bound: no client argument.
    async def __call__(self, messages, tools=None):
        self.requests.append(json.loads(json.dumps(messages, default=str)))
        content = json.dumps(
            {"result": self.answer, "explanation": "It refunded 250, over its limit."}
        )
        yield {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]}
        yield {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 9}}


@pytest.fixture
def agent_model():
    from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator
    from ee.falcon_ai.llm_client import FalconLLMClient

    fake = FakeAgentModel()
    with (
        patch.object(FalconLLMClient, "stream_completion", fake),
        patch.object(
            AgentEvaluator,
            "_build_tool_context",
            return_value=ToolContext(user=None, organization=None, workspace=None),
        ),
        patch.object(
            AgentEvaluator,
            "_build_llm_override",
            return_value={"provider": "openai", "model": "test-model"},
        ),
        patch("ee.falcon_ai.llm_client.asyncio.sleep"),
        # The shared evaluator's feedback lookup closes every database
        # connection when it is done, which ends the test's transaction.
        patch(
            "model_hub.views.eval_runner.EvaluationRunner.get_few_shot_examples",
            return_value=[],
        ),
    ):
        yield fake


def _sent_text(fake: FakeAgentModel) -> str:
    return json.dumps(fake.requests, ensure_ascii=False)


# --- the one table ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "modality", "kind"),
    [
        ("transcript", "voice", Kind.TRANSCRIPT),
        ("output", "voice", Kind.TRANSCRIPT),
        ("conversation", "chat", Kind.TRANSCRIPT),
        ("conversation", "voice", Kind.AUDIO),
        ("agent_prompt", "chat", Kind.AGENT_INSTRUCTIONS),
        ("context", "voice", Kind.AGENT_INSTRUCTIONS),
        ("input", "chat", Kind.SCENARIO),
        ("tool_calls", "voice", Kind.TOOL_CALLS),
        ("world_changes", "chat", Kind.WORLD_CHANGES),
        ("world_state_end", "chat", Kind.WORLD_STATE_END),
        ("audio", "chat", None),
        ("no_such_key", "voice", None),
    ],
)
def test_each_key_is_read_as_one_kind_through_the_one_table(key, modality, kind):
    assert kind_for_key(key, modality) == kind


def test_the_ask_only_keys_leave_todays_offer_at_run_creation_unchanged():
    for modality in ("voice", "chat"):
        sources = harness_evals._sources_for(modality)
        assert not set(ASK_ONLY_KEYS) & set(sources)
        # Every source the table names is read as a kind, so the two can't drift.
        assert set(sources.values()) <= set(KIND_BY_SOURCE)
    assert set(harness_evals._SOURCE_BY_KEY_VOICE) == {
        "conversation",
        "input_audio",
        "audio",
        "output",
        "text",
        "transcript",
        "agent_prompt",
        "system_prompt",
        "context",
        "input",
    }


# --- the definition -----------------------------------------------------------------


@pytest.mark.django_db
def test_an_input_the_criteria_do_not_name_is_named_in_one_fixed_block_after_them(
    organization, workspace, user
):
    template = _agent_template(
        organization,
        workspace,
        user,
        criteria=OWN_CRITERIA + " Read {{transcript}} too.",
        keys=("transcript", "agent_prompt", "tool_calls"),
    )

    definition = configured_eval("c1", template, modality="voice")

    assert definition.criteria.startswith(OWN_CRITERIA + " Read {{transcript}} too.")
    block = definition.criteria[len(OWN_CRITERIA + " Read {{transcript}} too.") :]
    assert block.startswith("\n\n" + CONFIGURED_BLOCK_HEAD)
    assert "{{agent_prompt}}" in block and "{{tool_calls}}" in block
    assert "{{transcript}}" not in block
    assert definition.inputs == (
        ("transcript", Kind.TRANSCRIPT),
        ("agent_prompt", Kind.AGENT_INSTRUCTIONS),
        ("tool_calls", Kind.TOOL_CALLS),
    )
    assert definition.labels == ("Yes", "No")
    assert definition.model == "turing_large"
    assert definition.eval_template_id == str(template.id)
    assert definition.own_criteria == OWN_CRITERIA + " Read {{transcript}} too."
    # The template itself is not rewritten.
    template.refresh_from_db()
    assert template.config["rule_prompt"] == OWN_CRITERIA + " Read {{transcript}} too."


@pytest.mark.django_db
def test_criteria_that_name_every_input_are_sent_as_they_are(
    organization, workspace, user
):
    template = _agent_template(
        organization, workspace, user, criteria=OWN_CRITERIA + " {{ agent_prompt }}"
    )

    definition = configured_eval("c1", template, modality="chat")

    assert definition.criteria == OWN_CRITERIA + " {{ agent_prompt }}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"eval_type_id": "CustomPromptEvaluator"}, "eval_not_offered"),
        ({"output": "Pass/Fail"}, "eval_not_offered"),
        ({"choice_scores": {}}, "eval_not_offered"),
        ({"keys": ("conversation",)}, "eval_not_offered"),
        ({"keys": ("no_such_key",)}, "eval_not_offered"),
        ({"keys": ()}, "eval_not_offered"),
        ({"criteria": ""}, "eval_not_offered"),
        # A decision model is billed per answer and keyed per organisation; a
        # configured eval is gated as a platform eval, so one on it isn't offered.
        ({"model": "jev-latest"}, "eval_not_offered"),
    ],
)
def test_an_eval_the_step_cannot_send_whole_and_read_back_is_not_offered(
    organization, workspace, user, change, code
):
    template = _agent_template(organization, workspace, user, **change)

    with pytest.raises(LaunchRefused) as refused:
        configured_eval("c1", template, modality="voice")

    assert refused.value.code == code


@pytest.mark.django_db
def test_an_eval_whose_version_holds_its_criteria_and_leaves_an_input_unnamed_is_not_offered(
    organization, workspace, user
):
    """Such a version's text replaces whatever the step sends, so its block would
    never reach the model."""
    template = _agent_template(
        organization,
        workspace,
        user,
        prompt_messages=[
            {"role": "user", "name": "eval_prompt", "content": OWN_CRITERIA}
        ],
    )

    with pytest.raises(LaunchRefused) as refused:
        configured_eval("c1", template, modality="voice")

    assert refused.value.code == "eval_not_offered"


# --- grading --------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_marker_at_the_end_of_long_instructions_the_criteria_do_not_name_reaches_the_agent_model_whole(
    organization,
    workspace,
    user,
    selection,
    agent_version,
    gate_passes,
    queued,
    agent_model,
):
    """Pass line 1, for the agent evaluator and a configured eval whose own criteria
    do not name its input: the 500-character echo and the per-message cut are both on
    the path."""
    long_text = ("Refund rules, paragraph after paragraph. " * 1500) + LONG_TAIL_MARKER
    assert 500 < len(long_text) < 80_000
    agent_version.configuration_snapshot = {
        **agent_version.configuration_snapshot,
        "description": long_text,
    }
    agent_version.save(update_fields=["configuration_snapshot"])
    template = _agent_template(organization, workspace, user)
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        configured_eval("c1", template, modality="voice"),
    )

    grade_row(str(_rows(ask)[0].id))

    row = _rows(ask)[0]
    assert row.state == STATE.ANSWERED, row.reason
    assert row.label == "No"
    assert row.score == 0.0
    assert row.evaluator_log_id
    sent = _sent_text(agent_model)
    assert LONG_TAIL_MARKER in sent
    assert CONFIGURED_BLOCK_HEAD in sent
    question = ask.questions[0]
    assert question["eval_template_id"] == str(template.id)
    assert question["own_criteria"] == OWN_CRITERIA
    assert question["criteria"].startswith(
        OWN_CRITERIA + "\n\n" + CONFIGURED_BLOCK_HEAD
    )
    # No template is saved for a configured eval, and its own is not changed.
    assert (
        EvalTemplate.no_workspace_objects.filter(organization=organization).count() == 1
    )
    template.refresh_from_db()
    assert template.config["rule_prompt"] == OWN_CRITERIA


@pytest.mark.django_db
@pytest.mark.parametrize("size", [200_001, 79_800])
def test_an_input_the_agent_evaluator_would_cut_is_unmeasured_and_never_sent(
    organization,
    workspace,
    user,
    selection,
    agent_version,
    gate_passes,
    queued,
    agent_model,
    size,
):
    """Over the evaluator's cut for one value (200,000), or a rendered message over
    its cut for one message (80,000) once the criteria and the echo are added."""
    agent_version.configuration_snapshot = {
        **agent_version.configuration_snapshot,
        "description": ("Refund rules in words. " * (size // 23 + 1))[:size],
    }
    agent_version.save(update_fields=["configuration_snapshot"])
    template = _agent_template(organization, workspace, user)
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        configured_eval("c1", template, modality="voice"),
    )

    grade_row(str(_rows(ask)[0].id))

    row = _rows(ask)[0]
    assert (row.state, row.cause) == (STATE.UNMEASURED, "too_large")
    assert agent_model.requests == []
    assert row.evaluator_log_id is None


@pytest.mark.django_db
def test_a_configured_eval_edited_after_launch_is_not_graded(
    organization,
    workspace,
    user,
    selection,
    gate_passes,
    queued,
    agent_model,
):
    template = _agent_template(organization, workspace, user)
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        configured_eval("c1", template, modality="voice"),
    )
    template.config = {**template.config, "rule_prompt": "Something else entirely."}
    template.save(update_fields=["config"])

    grade_row(str(_rows(ask)[0].id))

    row = _rows(ask)[0]
    assert (row.state, row.cause) == (STATE.GRADING_ERROR, "question_changed")
    assert agent_model.requests == []


@pytest.mark.django_db
def test_a_typed_question_and_a_configured_eval_grade_side_by_side(
    organization,
    workspace,
    user,
    selection,
    gate_passes,
    queued,
    decision_model,
    agent_model,
):
    template = _agent_template(organization, workspace, user)
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        _question(),
        configured_eval("c1", template, modality="voice"),
    )

    for row in _rows(ask):
        grade_row(str(row.id))

    answers = {(r.question_key, r.label) for r in _rows(ask)}
    assert answers == {("q1", "Yes"), ("c1", "No")}
    assert len(decision_model.requests) == 2
    assert len(agent_model.requests) == 2


# --- the check before launch and its gate ------------------------------------------


@pytest.mark.django_db
def test_a_configured_eval_is_shown_as_priced_by_use_not_as_a_fixed_cost(
    organization, workspace, user, selection
):
    template = _agent_template(organization, workspace, user)
    configured = configured_eval("c1", template, modality="voice").as_question()
    typed = _question().as_question()

    with patch("tracer.services.instant_eval_run.credits_per_answer", return_value=0.5):
        shown = check_before_launch(
            selection,
            [typed, configured],
            organization=organization,
            workspace_id=workspace.id,
        )

    assert shown["answers"] == 4
    # Only the typed question's two answers have a price per answer.
    assert shown["credits_estimate"] == 1.0
    assert shown["priced_by_use"] == [configured.name]


@pytest.mark.django_db
def test_the_gate_checks_usage_for_an_eval_priced_by_use_and_reads_no_decision_key(
    organization, workspace, user
):
    template = _agent_template(organization, workspace, user)
    configured = configured_eval("c1", template, modality="voice").as_question()
    allowed = type("Check", (), {"allowed": True})()
    refused = type("Check", (), {"allowed": False, "reason": "limit"})()

    with (
        patch("simulate.services.ask_selection._check_capability") as instant_eval,
        patch("simulate.services.ask_selection._check_key") as key,
        patch("simulate.services.ask_selection._check_credits") as credits,
        patch("simulate.services.ask_selection._usage_check", return_value=allowed),
    ):
        launch_gate(
            organization=organization,
            workspace_id=workspace.id,
            questions=[configured],
            answers=2,
        )
    key.assert_not_called()
    instant_eval.assert_not_called()
    credits.assert_not_called()

    with (
        patch("simulate.services.ask_selection._usage_check", return_value=refused),
        pytest.raises(LaunchRefused) as stopped,
    ):
        launch_gate(
            organization=organization,
            workspace_id=workspace.id,
            questions=[configured],
            answers=2,
        )
    assert stopped.value.code == "usage_limit"


# --- the rows of the scenario matrix the paths above don't cover --------------------


@pytest.mark.django_db
@pytest.mark.parametrize("gone", ["deleted", "another_organization"])
def test_an_eval_deleted_or_of_another_organisation_is_refused_at_launch_and_nothing_is_written(
    organization, workspace, user, selection, gate_passes, queued, gone
):
    from accounts.models.organization import Organization

    template = _agent_template(organization, workspace, user)
    definition = configured_eval("c1", template, modality="voice")
    if gone == "deleted":
        EvalTemplate.no_workspace_objects.filter(id=template.id).update(deleted=True)
    else:
        other = Organization.objects.create(name="Someone else")
        EvalTemplate.no_workspace_objects.filter(id=template.id).update(
            organization=other
        )

    with pytest.raises(LaunchRefused) as refused:
        _launch(selection, organization, workspace, user, definition)

    assert refused.value.code == "not_found"
    assert not SimulateEvalResult.objects.filter(organization=organization).exists()
    queued.assert_not_called()


@pytest.mark.django_db
def test_an_eval_edited_between_being_chosen_and_the_launch_is_refused(
    organization, workspace, user, selection, gate_passes, queued
):
    template = _agent_template(organization, workspace, user)
    definition = configured_eval("c1", template, modality="voice")
    template.config = {**template.config, "rule_prompt": "Something else entirely."}
    template.save(update_fields=["config"])

    with pytest.raises(LaunchRefused) as refused:
        _launch(selection, organization, workspace, user, definition)

    assert refused.value.code == "question_changed"
    assert not SimulateEvalResult.objects.filter(organization=organization).exists()
    queued.assert_not_called()


@pytest.mark.django_db
def test_an_eval_with_no_version_is_graded_on_its_own_criteria(
    organization, workspace, user, selection, gate_passes, queued, agent_model
):
    template = _agent_template(organization, workspace, user, versioned=False)
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        configured_eval("c1", template, modality="voice"),
    )

    grade_row(str(_rows(ask)[0].id))

    assert ask.questions[0]["template_version"] is None
    assert _rows(ask)[0].state == STATE.ANSWERED


@pytest.mark.django_db
def test_an_agent_evaluator_failure_is_a_grading_error_never_a_fail(
    organization, workspace, user, selection, gate_passes, queued, agent_model
):
    template = _agent_template(organization, workspace, user)
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        configured_eval("c1", template, modality="voice"),
    )
    agent_model.answer = "Not one of the labels"

    grade_row(str(_rows(ask)[0].id))

    row = _rows(ask)[0]
    assert row.state == STATE.GRADING_ERROR
    assert row.label is None and row.score is None


@pytest.mark.django_db
def test_a_usage_check_that_cannot_answer_refuses_the_launch(
    organization, workspace, user
):
    configured = configured_eval(
        "c1", _agent_template(organization, workspace, user), modality="voice"
    ).as_question()

    with (
        patch(
            "ee.usage.services.metering.check_usage",
            side_effect=RuntimeError("billing is down"),
        ),
        pytest.raises(LaunchRefused) as refused,
    ):
        launch_gate(
            organization=organization,
            workspace_id=workspace.id,
            questions=[configured],
            answers=2,
        )

    assert refused.value.code == "credit_check_unavailable"


@pytest.mark.django_db
def test_a_capability_check_that_fails_refuses_the_launch(
    organization, workspace, user
):
    configured = configured_eval(
        "c1", _agent_template(organization, workspace, user), modality="voice"
    ).as_question()

    with (
        patch("tfc.capabilities.service.is_configured", return_value=True),
        patch(
            "tfc.capabilities.service.check", side_effect=RuntimeError("unreachable")
        ),
        patch("simulate.services.ask_selection._usage_check") as usage,
        pytest.raises(LaunchRefused) as refused,
    ):
        launch_gate(
            organization=organization,
            workspace_id=workspace.id,
            questions=[configured],
            answers=2,
        )

    assert refused.value.code == "ENTITLEMENT_DENIED"
    usage.assert_not_called()


@pytest.mark.django_db
def test_an_eval_moved_to_another_organisation_after_launch_is_not_graded(
    organization, workspace, user, selection, gate_passes, queued, agent_model
):
    from accounts.models.organization import Organization

    template = _agent_template(organization, workspace, user)
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        configured_eval("c1", template, modality="voice"),
    )
    other = Organization.objects.create(name="Someone else")
    EvalTemplate.no_workspace_objects.filter(id=template.id).update(organization=other)

    grade_row(str(_rows(ask)[0].id))

    row = _rows(ask)[0]
    assert (row.state, row.cause) == (STATE.GRADING_ERROR, "question_changed")
    assert agent_model.requests == []
