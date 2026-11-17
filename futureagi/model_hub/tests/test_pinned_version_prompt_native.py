"""On the native experiment path a pinned version runs the prompt it saved.

Versions are saved by the evaluation pages' own requests with the bodies
they send (the create page's draft, autosave and publish; the editor's
update and version save; see test_pinned_version_prompt). Admission, the
worker row and the runner are real, and the row builds the real
LLM-as-judge evaluator. Only that judge's provider call (what it sends is
recorded), the row's prompt step, the usage preflight and the billing
boundary are replaced.
"""

import json
import uuid
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentic_eval.core.llm.llm import LLM
from evaluations.engine import instance, registry
from model_hub.models.evals_metric import EvalTemplateVersion, UserEvalMetric
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    native_fixture_evaluator,
    native_preflight,
    row,
    setup,
)
from model_hub.tests.test_native_run_binding import native_world as native_world
from model_hub.tests.test_pinned_version_native import admit, upstream_output
from model_hub.tests.test_pinned_version_prompt import (
    COPIED_BINDING,
    create_draft_judge,
    create_in_editor,
    generated_system_message,
    rendered,
    save_in_editor,
    sent_messages,
    turns_for,
)
from model_hub.views import eval_runner

pytestmark = pytest.mark.django_db(transaction=True)

ONE = "Version one: does {{output}} answer {{input}}?"
TWO = "Version two: is {{output}} polite about {{input}}?"
THREE = "Version three: is {{output}} short for {{input}}?"
TWO_SYSTEM = "Version two system: judge {{output}} for {{input}}"
TWO_TURN = "Version two turn: the question was {{input}}"
INPUTS = {"input": "probe question", "output": "probe answer"}


@contextmanager
def native_judges():
    """Each row builds the real judge; what it would send is recorded.

    Each entry is (messages, the judge's generated system message). The row
    then finishes with a recorded result, so nothing calls a model or
    charges anyone.
    """
    registry._build_registry()
    real = instance.create_eval_instance
    engine = MagicMock(format_result=None)
    engine.cost = {}
    engine.token_usage = {}
    engine.model_route = None
    engine.run.return_value = SimpleNamespace(
        eval_results=[
            {"metrics": [{"id": "score", "value": 1}], "reason": "ok", "failure": False}
        ]
    )
    sent = []

    def build(eval_class, *args, **kwargs):
        judge, _criteria = real(eval_class, *args, **kwargs)
        messages = sent_messages(judge, **INPUTS)
        sent.append((messages, generated_system_message(judge)))
        return engine, None

    def debit(*args, **kwargs):
        return SimpleNamespace(
            status=eval_runner.APICallStatusChoices.PROCESSING.value,
            config=json.dumps(kwargs["config"], default=str),
            log_id=uuid.uuid4(),
            save=MagicMock(),
        )

    with ExitStack() as stack:
        stack.enter_context(patch.object(LLM, "__init__", lambda self, **kwargs: None))
        stack.enter_context(
            patch.object(instance, "create_eval_instance", side_effect=build)
        )
        stack.enter_context(
            patch.object(
                eval_runner,
                "log_and_deduct_cost_for_api_request",
                MagicMock(side_effect=debit),
            )
        )
        stack.enter_context(
            patch.object(eval_runner, "refund_cost_for_api_call", MagicMock())
        )
        stack.enter_context(
            patch.object(eval_runner.EvaluationRunner, "_emit_cell_usage")
        )
        stack.enter_context(
            native_preflight(MagicMock(return_value=SimpleNamespace(allowed=True)))
        )
        yield sent


def attach(w, template, pinned_version, inner, *, model="turing_small"):
    """The evaluation on the experiment, as the experiment page binds it."""
    metric = UserEvalMetric.no_workspace_objects.create(
        name=template.name,
        template=template,
        pinned_version=pinned_version,
        dataset=w["snapshot"],
        source_id=str(w["experiment"].id),
        user=w["created_by"],
        organization=w["dataset"].organization,
        workspace=w["dataset"].workspace,
        model=model,
        config={
            "mapping": {"input": str(w["source_column"].id), "output": "output"},
            "config": dict(inner),
        },
    )
    w["experiment"].user_eval_template_ids.add(metric)
    return metric


def run_one_row(w):
    w["experiment"].refresh_from_db()
    admit(w)
    context, prepared = setup(w["experiment"])
    with native_judges() as sent, upstream_output() as upstream:
        row(w["experiment"], context, prepared, prepared["row_ids"][0])
    upstream.call.assert_called_once()
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    refs = [item.score_refs for item in run.items.order_by("id")]
    assert sent, f"the native judge was never built: {refs}"
    return sent[0]


def version_of(template, number):
    return EvalTemplateVersion.no_workspace_objects.get(
        eval_template=template, version_number=number
    )


def three_versions(client):
    template = create_in_editor(client, ONE)
    save_in_editor(client, template, TWO)
    save_in_editor(client, template, THREE)
    return template


def assert_rule_turn(messages, text):
    last = messages[-1]
    assert last["role"] == "user"
    assert last["content"].startswith(
        rendered(text, **INPUTS) + "\n\n--- Input Data ---\n"
    ), last["content"]


@pytest.mark.parametrize(
    "pinned,text", [(1, ONE), (2, TWO)], ids=["created-version", "editor-saved"]
)
def test_a_pinned_native_judge_sends_the_saved_prompt_not_the_binding_copy(
    native_world, auth_client, pinned, text
):
    w = native_world
    template = three_versions(auth_client)
    attach(w, template, version_of(template, pinned), COPIED_BINDING)

    messages, system_message = run_one_row(w)

    assert messages[:-1] == [{"role": "system", "content": system_message}]
    assert_rule_turn(messages, text)
    sent = json.dumps(messages)
    assert "Copied" not in sent
    assert "Version three" not in sent


@pytest.mark.parametrize("origin", ["created-in-editor", "drafted-judge"])
def test_a_pinned_native_single_message_judge_sends_no_turn_added_later(
    native_world, auth_client, origin
):
    """V1 from the create page, or from the Draft a judge request (whose
    version saves no turns); V2, with an extra turn, from the editor."""
    w = native_world
    model = "turing_small" if origin == "created-in-editor" else "turing_large"
    template = (
        create_in_editor(auth_client, ONE)
        if origin == "created-in-editor"
        else create_draft_judge(auth_client, ONE)
    )
    save_in_editor(
        auth_client,
        template,
        TWO_SYSTEM,
        model=model,
        messages=turns_for(TWO_SYSTEM, [{"role": "user", "content": TWO_TURN}]),
    )
    attach(w, template, version_of(template, 1), {}, model=model)

    messages, system_message = run_one_row(w)

    assert messages[:-1] == [{"role": "system", "content": system_message}]
    assert_rule_turn(messages, ONE)
    assert "Version two" not in json.dumps(messages)


@pytest.mark.parametrize("case", ["unpinned", "pin-deleted-before-admission"])
def test_unpinned_and_deleted_native_pins_send_the_live_prompt(
    native_world, auth_client, case
):
    w = native_world
    template = three_versions(auth_client)
    first = version_of(template, 1)
    attach(w, template, None if case == "unpinned" else first, {})
    if case != "unpinned":
        EvalTemplateVersion.all_objects.filter(pk=first.pk).update(deleted=True)

    messages, system_message = run_one_row(w)

    assert messages[:-1] == [{"role": "system", "content": system_message}]
    assert_rule_turn(messages, THREE)
    sent = json.dumps(messages)
    assert "Version one" not in sent
    assert "Version two" not in sent
