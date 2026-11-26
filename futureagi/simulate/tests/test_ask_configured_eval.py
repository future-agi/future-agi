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
# The launch also queues a later settle; no test reaches a real queue.
_settle_not_queued = typed._settle_not_queued

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
        # Raised once the request is recorded: the worker dying mid-call.
        self.error: BaseException | None = None

    # Set on the class as a plain callable, so it is not bound: no client argument.
    async def __call__(self, messages, tools=None):
        self.requests.append(json.loads(json.dumps(messages, default=str)))
        if self.error is not None:
            raise self.error
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
def test_a_versions_own_criteria_edited_in_place_after_launch_is_not_graded(
    organization,
    workspace,
    user,
    selection,
    gate_passes,
    queued,
    agent_model,
):
    """The version holds the criteria, so the template's own fields don't change
    and its version number stays: only the criteria the evaluator reads do."""
    held = "Did the agent keep to {{agent_prompt}}? Answer Yes or No."
    template = _agent_template(
        organization,
        workspace,
        user,
        criteria=held,
        prompt_messages=[{"role": "user", "name": "eval_prompt", "content": held}],
    )
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        configured_eval("c1", template, modality="voice"),
    )
    version = EvalTemplateVersion.objects.get_default(template)
    version.prompt_messages = [
        {
            "role": "user",
            "name": "eval_prompt",
            "content": "Was {{agent_prompt}} polite?",
        }
    ]
    version.save(update_fields=["prompt_messages"])

    grade_row(str(_rows(ask)[0].id))

    row = _rows(ask)[0]
    assert (row.state, row.cause) == (STATE.GRADING_ERROR, "question_changed")
    assert agent_model.requests == []


@pytest.mark.django_db
def test_a_dead_workers_configured_row_names_the_evaluators_log_row(
    organization,
    workspace,
    user,
    selection,
    gate_passes,
    queued,
    agent_model,
):
    """The worker died inside the agent evaluator's call: the row is closed as a
    dead worker's and names the log row that call was charged on."""
    from datetime import timedelta

    from django.utils import timezone

    from ee.usage.models.usage import APICallLog
    from simulate.services import ask_engine as engine

    template = _agent_template(organization, workspace, user)
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        configured_eval("c1", template, modality="voice"),
    )
    row = _rows(ask)[0]
    agent_model.error = typed._WorkerKilled()
    with pytest.raises(typed._WorkerKilled):
        grade_row(str(row.id))
    SimulateEvalResult.objects.filter(id=row.id).update(
        claimed_at=timezone.now() - engine.GRADING_GIVE_UP - timedelta(seconds=1)
    )

    engine.settle_ask(ask.id)

    row.refresh_from_db()
    assert (row.state, row.cause) == (STATE.INFRASTRUCTURE_ERROR, "worker_died")
    (log,) = APICallLog.objects.filter(organization=organization)
    assert row.evaluator_log_id == log.log_id


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
    # Every ask is Instant Eval's; a configured eval needs agent evals as well.
    instant_eval.assert_called_once_with(organization)
    credits.assert_not_called()

    with (
        patch("simulate.services.ask_selection._check_capability"),
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
        patch("simulate.services.ask_selection._check_capability"),
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


def test_without_ee_usage_the_usage_check_gives_nothing_to_refuse_on():
    from tfc.ee_gates import eval_usage_check

    # A build with ee/ stripped: the import fails, and nothing is checked.
    with patch.dict("sys.modules", {"ee.usage.services.metering": None}):
        assert eval_usage_check("org", "agent_eval") is None


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


# --- round 1 review -----------------------------------------------------------------


def _long_instructions(agent_version, size):
    text = ("Refund rules, paragraph after paragraph. " * (size // 41 + 1))[:size]
    agent_version.configuration_snapshot = {
        **agent_version.configuration_snapshot,
        "description": text + LONG_TAIL_MARKER,
    }
    agent_version.save(update_fields=["configuration_snapshot"])


GROUND_TRUTH_TEXT = "A ground truth example. "
FEEDBACK_TEXT = "An earlier graded example. "


def _with_examples(ground_truth_size, feedback_size):
    """An eval with a ground-truth set and feedback: both would go into the same
    message as the criteria, in front of and after them."""
    from model_hub.services.ground_truth_service import GroundTruthService

    def inject(mapped, eval_template, **_options):
        mapped["ground_truth_blocks"] = [
            {"type": "text", "text": GROUND_TRUTH_TEXT * (ground_truth_size // 24)}
        ]
        return mapped

    return (
        patch.object(GroundTruthService, "inject_context", side_effect=inject),
        patch(
            "model_hub.views.eval_runner.EvaluationRunner.get_few_shot_examples",
            return_value=[
                {"type": "text", "text": FEEDBACK_TEXT * (feedback_size // 27)}
            ],
        ),
    )


@pytest.mark.django_db
def test_an_asks_grading_carries_no_ground_truth_or_feedback_examples(
    organization,
    workspace,
    user,
    selection,
    agent_version,
    gate_passes,
    queued,
    agent_model,
):
    """The size check measures the criteria and the inputs. Examples would join
    them in the same message and push it over the cut, so an ask sends none: what
    was measured is what is sent."""
    _long_instructions(agent_version, 60_000)
    template = _agent_template(organization, workspace, user)
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        configured_eval("c1", template, modality="voice"),
    )
    ground_truth, feedback = _with_examples(30_800, 31_200)

    with ground_truth, feedback:
        grade_row(str(_rows(ask)[0].id))

    row = _rows(ask)[0]
    assert row.state == STATE.ANSWERED, row.reason
    assert len(agent_model.requests) == 1
    sent = _sent_text(agent_model)
    assert LONG_TAIL_MARKER in sent
    assert GROUND_TRUTH_TEXT not in sent
    assert FEEDBACK_TEXT not in sent
    assert "[truncated" not in sent


@pytest.mark.django_db
def test_another_caller_of_the_shared_evaluator_still_gets_its_examples(
    organization, workspace, user, agent_model
):
    from model_hub.views.utils.evals import run_eval_func

    template = _agent_template(
        organization, workspace, user, criteria=OWN_CRITERIA + " {{ agent_prompt }}"
    )
    ground_truth, feedback = _with_examples(240, 270)

    with ground_truth, feedback:
        run_eval_func(
            {"run_config": {"model": template.model}},
            {"agent_prompt": "Refund at most 100."},
            template,
            organization,
            model=template.model,
            workspace=workspace,
            input_data_types={"agent_prompt": "text"},
        )

    sent = _sent_text(agent_model)
    assert GROUND_TRUTH_TEXT in sent
    assert FEEDBACK_TEXT in sent


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    [
        {"multi_choice": True},
        {"config": {"required_keys": ["agent_prompt", "conversation"]}},
        {"config": {"eval_type_id": "CustomPromptEvaluator"}},
        {"config": {"data_injection": {"call_context": True}}},
    ],
    ids=["multi_choice", "recording_read", "another_evaluator", "call_context"],
)
def test_an_eval_no_longer_offered_when_launched_is_refused(
    organization, workspace, user, selection, gate_passes, queued, change
):
    """Chosen, then changed in the library before the launch: the launch offers the
    eval again, and refuses one that would no longer be offered as chosen."""
    template = _agent_template(organization, workspace, user)
    definition = configured_eval("c1", template, modality="voice")
    fields = dict(change)
    if "config" in fields:
        fields["config"] = {**template.config, **fields["config"]}
    EvalTemplate.no_workspace_objects.filter(id=template.id).update(**fields)

    with pytest.raises(LaunchRefused) as refused:
        _launch(selection, organization, workspace, user, definition)

    assert refused.value.code == "question_changed"
    assert not SimulateEvalResult.objects.filter(organization=organization).exists()
    queued.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("criteria", "injection"),
    [
        (OWN_CRITERIA + " The call ended as {{call.ended_reason}}.", {}),
        (OWN_CRITERIA + " The whole row: {{ row }}.", {}),
        (OWN_CRITERIA + " Its trace: {{trace.id}}.", {}),
        (OWN_CRITERIA, {"call_context": True}),
        (OWN_CRITERIA, {"full_row": True}),
        (OWN_CRITERIA, {"spanContext": True}),
    ],
    ids=["call", "row", "trace", "call_context", "full_row", "span_context"],
)
def test_an_eval_that_reads_the_calls_own_context_is_not_offered(
    organization, workspace, user, criteria, injection
):
    """The evaluator fills these from context its caller passes. An ask passes none,
    so the criteria would be graded with "(call data not provided)" in its place."""
    template = _agent_template(organization, workspace, user, criteria=criteria)
    if injection:
        template.config = {**template.config, "data_injection": injection}
        template.save(update_fields=["config"])

    with pytest.raises(LaunchRefused) as refused:
        configured_eval("c1", template, modality="voice")

    assert refused.value.code == "eval_not_offered"


def test_criteria_that_only_look_like_context_are_still_offered():
    from tfc.ee_gates import agent_criteria_read_context

    assert not agent_criteria_read_context("Did {{agent_prompt}} say call me back?", {})
    assert not agent_criteria_read_context("Rows of {{ rows }} and {{caller}}.", {})
    assert agent_criteria_read_context("Ended: {{ call.ended_reason }}", {})
    assert agent_criteria_read_context("Plain.", {"trace_context": True})


@pytest.mark.django_db
@pytest.mark.parametrize("hidden", ["another_workspace", "draft"])
def test_an_eval_the_run_offer_hides_is_refused_at_launch(
    organization, workspace, user, selection, gate_passes, queued, hidden
):
    template = _agent_template(organization, workspace, user)
    definition = configured_eval("c1", template, modality="voice")
    _hide(template, hidden, organization, user)

    with pytest.raises(LaunchRefused) as refused:
        _launch(selection, organization, workspace, user, definition)

    assert refused.value.code == "not_found"
    assert not SimulateEvalResult.objects.filter(organization=organization).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("hidden", ["another_workspace", "draft"])
def test_an_eval_hidden_from_the_run_offer_after_launch_is_not_graded(
    organization,
    workspace,
    user,
    selection,
    gate_passes,
    queued,
    agent_model,
    hidden,
):
    template = _agent_template(organization, workspace, user)
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        configured_eval("c1", template, modality="voice"),
    )
    _hide(template, hidden, organization, user)

    grade_row(str(_rows(ask)[0].id))

    row = _rows(ask)[0]
    assert (row.state, row.cause) == (STATE.GRADING_ERROR, "question_changed")
    assert agent_model.requests == []


def _hide(template, hidden, organization, user):
    if hidden == "another_workspace":
        from accounts.models.workspace import Workspace

        other = Workspace.objects.create(
            name="Another team", organization=organization, created_by=user
        )
        EvalTemplate.no_workspace_objects.filter(id=template.id).update(workspace=other)
    else:
        EvalTemplate.no_workspace_objects.filter(id=template.id).update(
            visible_ui=False
        )


@pytest.mark.django_db
def test_a_configured_only_ask_needs_instant_eval_too(organization, workspace, user):
    from tfc.ee_gating import FeatureUnavailable

    template = _agent_template(organization, workspace, user)
    configured = configured_eval("c1", template, modality="voice").as_question()
    allowed = type("Check", (), {"allowed": True})()

    def denied(*_args, **_options):
        raise FeatureUnavailable("instant_eval")

    with (
        patch("tfc.ee_gating.check_ee_feature", side_effect=denied),
        patch(
            "simulate.services.ask_selection._agent_evals_allowed", return_value=True
        ),
        patch("simulate.services.ask_selection._usage_check", return_value=allowed),
        pytest.raises(LaunchRefused) as refused,
    ):
        launch_gate(
            organization=organization,
            workspace_id=workspace.id,
            questions=[configured],
            answers=2,
        )

    assert refused.value.code == "ENTITLEMENT_DENIED"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    [
        {"choice_scores": {"Yes": 0.0, "No": 1.0}},
        {"config": {"agent_mode": "auto"}},
    ],
    ids=["choice_scores", "agent_mode"],
)
def test_a_configured_eval_changed_only_where_the_digest_sees_it_is_not_graded(
    organization,
    workspace,
    user,
    selection,
    gate_passes,
    queued,
    agent_model,
    change,
):
    """A change after launch that the criteria, model and labels don't show."""
    template = _agent_template(organization, workspace, user)
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        configured_eval("c1", template, modality="voice"),
    )
    fields = dict(change)
    if "config" in fields:
        fields["config"] = {**template.config, **fields["config"]}
    EvalTemplate.no_workspace_objects.filter(id=template.id).update(**fields)

    grade_row(str(_rows(ask)[0].id))

    row = _rows(ask)[0]
    assert (row.state, row.cause) == (STATE.GRADING_ERROR, "question_changed")
    assert agent_model.requests == []
