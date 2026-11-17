"""A pinned evaluation version runs the prompt it saved.

Every version here is saved by the requests the evaluation pages send, with
the bodies they send:

- Create (EvalCreatePage.jsx): a draft is created (:457-466), autosaved
  (buildUpdatePayload :513-560, sent at :598-600) and published with a name
  (:656-662). The publish saves V1 (separate_evals.py lazy V1 on publish).
- Save in the editor (EvalDetailPage.jsx handleSaveVersion): the update
  (:1052-1079), then a version of it (:1083-1116), after the page has read
  the evaluation's detail (useEvalDetail.js:5-13).

One test also covers a judge made by the experiment wizard's Draft a judge
dialog (DraftJudgeDialog.jsx:121-130), whose first version comes from the
create request itself, then edited in the editor.

The real runner and engine build the real LLM-as-judge evaluator; only its
provider call is replaced, so each test reads the message list the judge
sends to its model. Unpinned runs, and runs whose pinned version was
deleted, keep the live evaluation's prompt.
"""

import json
import uuid
from unittest.mock import Mock

import pytest

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
    CustomPromptEvaluator,
)
from evaluations.engine import instance
from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.tests.test_pinned_version_runs import Built, built_evaluators

pytestmark = pytest.mark.django_db

CREATE_URL = "/model-hub/eval-templates/create-v2/"
MODEL = "turing_small"
ANSWER = "an answer"

ONE = "Version one: is {{output}} correct?"
TWO = "Version two: is {{output}} polite?"
THREE = "Version three: is {{output}} short?"
COPIED = "Copied on the binding: is {{output}} kind?"

ONE_SYSTEM = "Version one system: judge {{output}}"
ONE_TURN = "Version one turn: the answer was {{output}}"
TWO_SYSTEM = "Version two system: judge {{output}}"
TWO_TURN = "Version two turn: the answer was {{output}}"

AGENT_ONE = "Agent version one: check {{output}}"
AGENT_TWO = "Agent version two: check {{output}}"

# A prompt the binding copied, in the shape the evaluation picker saves it
# (evalPickerConfigUtils.js:55-86).
COPIED_BINDING = {
    "rule_prompt": COPIED,
    "messages": [{"role": "system", "content": COPIED}],
    "few_shot_examples": [],
    "template_format": "mustache",
}

# Agent fields as both pages send them for an agent with nothing selected.
AGENT_FIELDS = {
    "mode": "agent",
    "tools": {},
    "knowledge_bases": [],
    "data_injection": {"variables_only": True},
    "summary": {"type": "concise"},
}


def rendered(text, **values):
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def turns_for(instructions, extra=()):
    """The editor's message list: the System turn holds the instructions."""
    return [{"role": "system", "content": instructions}, *extra]


def definition(eval_type, instructions, model, messages, few_shot_examples):
    """The fields both pages send for the definition on screen."""
    body = {
        "instructions": instructions,
        "model": model,
        "output_type": "pass_fail",
        "pass_threshold": 0.5,
        "choice_scores": None,
        "multi_choice": False,
        "check_internet": False,
        "error_localizer_enabled": False,
        "template_format": "mustache",
    }
    if eval_type == "llm":
        body["messages"] = messages or turns_for(instructions)
        body["few_shot_examples"] = list(few_shot_examples or [])
    else:
        body.update(AGENT_FIELDS)
    return body


def update(client, template_id, body):
    response = client.put(
        f"/model-hub/eval-templates/{template_id}/update/", body, format="json"
    )
    assert response.status_code == 200, response.data


def create_in_editor(
    client,
    instructions,
    *,
    eval_type="llm",
    model=MODEL,
    messages=None,
    few_shot_examples=None,
):
    """The create page: draft, autosave, then publish (which saves V1)."""
    draft = client.post(
        CREATE_URL,
        {
            "is_draft": True,
            "eval_type": "agent",
            "output_type": "pass_fail",
            "model": "turing_large",
            "pass_threshold": 0.5,
        },
        format="json",
    )
    assert draft.status_code == 200, draft.data
    template_id = draft.data["result"]["id"]
    autosave = {
        "eval_type": eval_type,
        **definition(eval_type, instructions, model, messages, few_shot_examples),
    }
    update(client, template_id, autosave)
    update(
        client,
        template_id,
        {
            "name": f"pinned-prompt-{uuid.uuid4().hex[:8]}",
            **autosave,
            "description": None,
            "tags": [],
            "publish": True,
        },
    )
    return EvalTemplate.no_workspace_objects.get(pk=template_id)


# Keys the editor's version body sets to undefined, so JSON drops them.
_UNSENT_SNAPSHOT_KEYS = ("choice_scores", "code", "language", "allow_na")
_LLM_UNSENT_SNAPSHOT_KEYS = (
    "agent_mode",
    "tools",
    "knowledge_bases",
    "data_injection",
    "summary",
)


def save_in_editor(
    client,
    template,
    instructions,
    *,
    eval_type="llm",
    model=MODEL,
    messages=None,
    few_shot_examples=None,
):
    """The editor's Save: update the evaluation, then save a version of it."""
    detail = client.get(f"/model-hub/eval-templates/{template.id}/detail/")
    assert detail.status_code == 200, detail.data
    loaded_config = detail.data["result"].get("config") or {}
    body = definition(eval_type, instructions, model, messages, few_shot_examples)
    update(client, template.id, {**body, "description": None, "tags": []})
    snapshot = {
        **loaded_config,
        "rule_prompt": instructions,
        "model": model,
        "output": "Pass/Fail",
        "pass_threshold": body["pass_threshold"],
        "multi_choice": body["multi_choice"],
        "check_internet": body["check_internet"],
        "error_localizer_enabled": body["error_localizer_enabled"],
        "template_format": body["template_format"],
    }
    unsent = _UNSENT_SNAPSHOT_KEYS
    if eval_type == "llm":
        snapshot["messages"] = body["messages"]
        snapshot["few_shot_examples"] = body["few_shot_examples"]
        unsent += _LLM_UNSENT_SNAPSHOT_KEYS
    else:
        snapshot["agent_mode"] = body["mode"]
        for key in ("tools", "knowledge_bases", "data_injection", "summary"):
            snapshot[key] = body[key]
        unsent += ("messages", "few_shot_examples")
    for key in unsent:
        snapshot.pop(key, None)
    created = client.post(
        f"/model-hub/eval-templates/{template.id}/versions/create/",
        {"config_snapshot": snapshot, "criteria": instructions, "model": model},
        format="json",
    )
    assert created.status_code == 200, created.data
    return EvalTemplateVersion.no_workspace_objects.get(pk=created.data["result"]["id"])


def create_draft_judge(client, instructions):
    """The experiment wizard's Draft a judge dialog: the create request
    itself saves V1. Not the evaluation editor."""
    response = client.post(
        CREATE_URL,
        {
            "name": f"pinned-prompt-draft-{uuid.uuid4().hex[:8]}",
            "eval_type": "llm",
            "instructions": instructions,
            "description": "Drafted judge",
            "output_type": "pass_fail",
            "tags": ["draft"],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    return EvalTemplate.no_workspace_objects.get(pk=response.data["result"]["id"])


def version_of(template, number):
    return EvalTemplateVersion.no_workspace_objects.get(
        eval_template=template, version_number=number
    )


def three_versions(client):
    """V1 is saved by the create page, V2 and V3 by the editor; V3 is live."""
    template = create_in_editor(client, ONE)
    save_in_editor(client, template, TWO)
    save_in_editor(client, template, THREE)
    return template


@pytest.fixture
def dataset(organization, workspace, user):
    return Dataset.no_workspace_objects.create(
        name=f"pinned-prompt-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        user=user,
    )


@pytest.fixture
def judge_without_transport(monkeypatch):
    """The real judge is built; it opens no provider client."""
    monkeypatch.setattr(LLM, "__init__", lambda self, **kwargs: None)


def binding(template, dataset, user, pinned_version, inner, *, model=MODEL):
    return UserEvalMetric.no_workspace_objects.create(
        name=f"pinned-prompt-{uuid.uuid4().hex[:8]}",
        organization=dataset.organization,
        workspace=dataset.workspace,
        dataset=dataset,
        template=template,
        pinned_version=pinned_version,
        model=model,
        config={"mapping": {"output": "output"}, "config": dict(inner)},
        user=user,
    )


def classic_judge(metric, source):
    """The classic dataset and experiment runner's judge for one row."""
    from model_hub.views.eval_runner import EvaluationRunner

    runner = EvaluationRunner(
        user_eval_metric_id=metric.id,
        is_only_eval=True,
        source=source,
        source_id=str(metric.template_id),
        source_configs={"dataset_id": str(metric.dataset_id)},
    )
    runner.eval_template = EvalTemplate.no_workspace_objects.get(pk=metric.template_id)
    # The binding's config as each row passes it to the engine.
    config = runner.update_config_list_values(metric.config.get("config"), None)
    judge = runner._create_eval_instance(config=config, model=metric.model)
    assert isinstance(judge, CustomPromptEvaluator)
    return judge


def sent_messages(judge, **inputs):
    """The messages the judge sends to its model for ``inputs``.

    Only the provider call is replaced; the judge builds its messages for
    real. A Turing judge hands the same list to its own client, so the probe
    takes the provider route.
    """
    judge._is_turing = False
    judge.temperature = 0
    judge.max_tokens = 256
    judge.token_usage = {"completion_tokens": 0, "prompt_tokens": 0, "total_tokens": 0}
    judge.cost = {"total_cost": 0, "prompt_cost": 0, "completion_cost": 0}
    judge.call_llm = Mock(return_value='{"result": "Pass", "explanation": "ok"}')
    judge._evaluate(required_keys=list(inputs), **inputs)
    judge.call_llm.assert_called_once()
    return judge.call_llm.call_args.kwargs["prompt"]


def generated_system_message(judge):
    """The judge's own system message, sent when no system prompt is set."""
    return CustomPromptEvaluator._system_message(judge)


def assert_sent(
    messages, judge, *, rule, turns=(), examples=(), absent=(), values=None
):
    """Generated system message, few-shot pairs, extra turns, rule prompt."""
    values = values or {"output": ANSWER}
    expected = [{"role": "system", "content": generated_system_message(judge)}]
    for question, verdict in examples:
        expected.append({"role": "user", "content": question})
        expected.append({"role": "assistant", "content": verdict})
    for role, text in turns:
        expected.append({"role": role, "content": rendered(text, **values)})
    *head, last = messages
    assert head == expected
    assert last["role"] == "user"
    assert isinstance(last["content"], str)
    assert last["content"].startswith(
        rendered(rule, **values) + "\n\n--- Input Data ---\n"
    ), last["content"]
    sent = json.dumps(messages)
    for text in absent:
        assert text not in sent


def test_versions_hold_unnamed_turns_and_the_config_the_evaluation_ran(
    auth_client,
):
    """What the pages save, read back (passes before and after the fix)."""
    template = create_in_editor(auth_client, ONE)
    first = version_of(template, 1)
    second = save_in_editor(auth_client, template, TWO)
    drafted = version_of(create_draft_judge(auth_client, ONE), 1)

    for version, text in ((first, ONE), (second, TWO)):
        assert version.prompt_messages == [{"role": "system", "content": text}]
        assert version.config_snapshot["rule_prompt"] == text
        assert version.config_snapshot["messages"] == version.prompt_messages
        assert "system_prompt" not in version.config_snapshot
    assert drafted.prompt_messages == []
    assert drafted.config_snapshot["rule_prompt"] == ONE
    assert drafted.config_snapshot["system_prompt"] is None
    assert "messages" not in drafted.config_snapshot


@pytest.mark.parametrize("source", ["dataset_evaluation", "experiment"])
@pytest.mark.parametrize("copy", ["no-copy", "copied"])
@pytest.mark.parametrize(
    "pinned,text",
    [(1, ONE), (2, TWO)],
    ids=["created-version", "editor-saved"],
)
def test_a_pinned_judge_sends_the_saved_prompt_on_the_classic_path(
    auth_client, dataset, user, judge_without_transport, source, copy, pinned, text
):
    template = three_versions(auth_client)
    inner = COPIED_BINDING if copy == "copied" else {}
    metric = binding(template, dataset, user, version_of(template, pinned), inner)

    judge = classic_judge(metric, source)
    messages = sent_messages(judge, output=ANSWER)

    other = "Version two" if text == ONE else "Version one"
    assert_sent(messages, judge, rule=text, absent=["Version three", "Copied", other])


def test_a_pinned_newest_version_sends_what_the_unpinned_run_sends(
    auth_client, dataset, user, judge_without_transport
):
    """The newest version is the live evaluation: pinning it changes nothing."""
    template = three_versions(auth_client)
    pinned = binding(template, dataset, user, version_of(template, 3), {})
    unpinned = binding(template, dataset, user, None, {})

    sent_pinned = sent_messages(classic_judge(pinned, "experiment"), output=ANSWER)
    sent_unpinned = sent_messages(classic_judge(unpinned, "experiment"), output=ANSWER)

    assert sent_pinned == sent_unpinned


@pytest.mark.parametrize("copy", ["no-copy", "copied"])
def test_a_pinned_multi_message_judge_sends_its_saved_turns(
    auth_client, dataset, user, judge_without_transport, copy
):
    template = create_in_editor(
        auth_client,
        ONE_SYSTEM,
        messages=turns_for(ONE_SYSTEM, [{"role": "user", "content": ONE_TURN}]),
    )
    save_in_editor(
        auth_client,
        template,
        TWO_SYSTEM,
        messages=turns_for(TWO_SYSTEM, [{"role": "user", "content": TWO_TURN}]),
    )
    inner = COPIED_BINDING if copy == "copied" else {}
    metric = binding(template, dataset, user, version_of(template, 1), inner)

    judge = classic_judge(metric, "experiment")
    messages = sent_messages(judge, output=ANSWER)

    assert_sent(
        messages,
        judge,
        turns=[("user", ONE_TURN)],
        rule=ONE_SYSTEM,
        absent=["Version two", "Copied"],
    )


def test_a_pinned_single_message_judge_sends_no_turn_added_later(
    auth_client, dataset, user, judge_without_transport
):
    template = create_in_editor(auth_client, ONE)
    save_in_editor(
        auth_client,
        template,
        TWO_SYSTEM,
        messages=turns_for(TWO_SYSTEM, [{"role": "user", "content": TWO_TURN}]),
    )
    metric = binding(template, dataset, user, version_of(template, 1), {})

    judge = classic_judge(metric, "dataset_evaluation")
    messages = sent_messages(judge, output=ANSWER)

    assert_sent(messages, judge, rule=ONE, absent=["Version two"])


def few_shot_dataset(organization, workspace, user):
    """A dataset the editor can pick for few-shot examples."""
    examples = Dataset.objects.create(
        name=f"few-shot-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    row = Row.objects.create(dataset=examples, order=0)
    for name, value in (("input", "Example question"), ("output", "Example verdict")):
        column = Column.objects.create(
            dataset=examples,
            name=name,
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        Cell.objects.create(dataset=examples, column=column, row=row, value=value)
    return examples


def test_a_pinned_judge_sends_its_saved_few_shot_examples(
    auth_client, organization, workspace, dataset, user, judge_without_transport
):
    examples = few_shot_dataset(organization, workspace, user)
    template = create_in_editor(
        auth_client,
        ONE,
        few_shot_examples=[{"id": str(examples.id), "name": examples.name}],
    )
    # The editor clears the examples in the next version.
    save_in_editor(auth_client, template, TWO, few_shot_examples=[])
    metric = binding(template, dataset, user, version_of(template, 1), {})

    judge = classic_judge(metric, "dataset_evaluation")
    messages = sent_messages(judge, output=ANSWER)

    assert_sent(
        messages,
        judge,
        examples=[("Example question", "Example verdict")],
        rule=ONE,
        absent=["Version two"],
    )


def test_a_pinned_drafted_judge_sends_its_first_version_after_editor_saves(
    auth_client, dataset, user, judge_without_transport
):
    """First version from the Draft a judge request (not the editor), whose
    version saves no turns; later versions from the editor."""
    template = create_draft_judge(auth_client, ONE)
    save_in_editor(auth_client, template, TWO, model="turing_large")
    metric = binding(
        template, dataset, user, version_of(template, 1), {}, model="turing_large"
    )

    judge = classic_judge(metric, "experiment")
    messages = sent_messages(judge, output=ANSWER)

    assert_sent(messages, judge, rule=ONE, absent=["Version two"])


@pytest.mark.parametrize("copy", ["no-copy", "copied"])
@pytest.mark.parametrize("case", ["unpinned", "pin-deleted"])
def test_unpinned_and_deleted_pins_send_what_they_sent_before(
    auth_client, dataset, user, judge_without_transport, case, copy
):
    template = three_versions(auth_client)
    first = version_of(template, 1)
    inner = COPIED_BINDING if copy == "copied" else {}
    metric = binding(
        template, dataset, user, None if case == "unpinned" else first, inner
    )
    if case == "pin-deleted":
        EvalTemplateVersion.all_objects.filter(pk=first.pk).update(deleted=True)

    judge = classic_judge(metric, "experiment")
    messages = sent_messages(judge, output=ANSWER)

    # The binding's copy runs when it has one, else the live evaluation's.
    rule = COPIED if copy == "copied" else THREE
    assert_sent(messages, judge, rule=rule, absent=["Version one", "Version two"])


@pytest.mark.parametrize(
    "pinned,expected",
    [(1, AGENT_ONE), (None, AGENT_TWO)],
    ids=["pinned-created-version", "unpinned-keeps-live"],
)
def test_an_agent_evaluator_builds_with_the_pinned_version_rule_prompt(
    auth_client, organization, pinned, expected
):
    template = create_in_editor(auth_client, AGENT_ONE, eval_type="agent")
    save_in_editor(auth_client, template, AGENT_TWO, eval_type="agent")
    live = EvalTemplate.no_workspace_objects.get(pk=template.pk)

    built, _criteria = instance.create_eval_instance(
        eval_class=Built,
        eval_template=live,
        config={},
        model=MODEL,
        organization_id=str(organization.id),
        version_number=pinned,
    )

    assert built.kwargs["rule_prompt"] == expected


@pytest.mark.parametrize(
    "deleted,expected", [(False, ONE), (True, THREE)], ids=["pinned", "pin-deleted"]
)
def test_a_pinned_composite_child_builds_with_its_saved_prompt(
    auth_client, organization, workspace, deleted, expected
):
    from model_hub.utils.composite_execution import _execute_child

    template = three_versions(auth_client)
    first = version_of(template, 1)
    parent = EvalTemplate.no_workspace_objects.create(
        name=f"pinned-prompt-parent-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="llm",
        template_type="composite",
        config={"eval_type_id": "CustomPromptEvaluator"},
    )
    link = CompositeEvalChild.no_workspace_objects.create(
        parent=parent, child=template, order=0, weight=1.0, pinned_version=first
    )
    if deleted:
        EvalTemplateVersion.all_objects.filter(pk=first.pk).update(deleted=True)
        link.refresh_from_db()
    child = EvalTemplate.no_workspace_objects.get(pk=template.pk)

    with built_evaluators(stop=True) as seen:
        _execute_child(
            child_template=child,
            link=link,
            weight=1.0,
            mapping={"output": ANSWER},
            config={},
            model=None,
            org=organization,
            workspace=workspace,
            input_data_types={},
            row_context=None,
            span_context=None,
            trace_context=None,
            session_context=None,
            call_context=None,
            error_localizer=False,
            source="composite_eval",
        )

    (built,) = seen
    assert built["rule_prompt"] == expected
    assert built.get("system_prompt") in (None, "")
