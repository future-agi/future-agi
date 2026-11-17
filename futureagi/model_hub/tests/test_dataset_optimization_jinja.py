"""Dataset optimization must execute the source Jinja template."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from jinja2.exceptions import SecurityError

from ee.agenthub.fix_your_agent.direct_evaluator import DirectEvaluator


LOOP = "Items: {% for item in items %}{{ item }} {% endfor %}"


@pytest.mark.parametrize("template_format", ["jinja", "jinja2"])
@pytest.mark.parametrize(
    "template, row, expected",
    [
        (LOOP, {"items": '["red", "blue"]'}, "Items: red blue "),
        (LOOP, {"items": ["red", "blue"]}, "Items: red blue "),
        ("Name: {{account.name}}", {"account": '{"name":"Ada"}'}, "Name: Ada"),
        ("Name: {{account.name}}", {"account.name": "Ada"}, "Name: Ada"),
        ("Raw: {{account}}", {"account": '{"name":"Ada"}'}, 'Raw: {"name":"Ada"}'),
        ("Reply to {{ question }}", {"question": "ping"}, "Reply to ping"),
        ("Name: {{full name}}", {"full name": "Ada"}, "Name: Ada"),
        ("{{count}} / {{enabled}}", {"count": 0, "enabled": False}, "0 / False"),
        (
            "{% if account.enabled %}{{account.name | upper}}{% else %}disabled{% endif %}",
            {"account": '{"enabled": true, "name": "Ada"}'},
            "ADA",
        ),
    ],
)
def test_baseline_and_candidate_render_jinja_before_provider(
    template_format, template, row, expected
):
    eval_config = SimpleNamespace(name="check")
    evaluator = DirectEvaluator([eval_config], initial_agent_prompt=template)
    # Setting the attribute also lets the RED test reach the old literal renderer.
    evaluator.template_format = template_format
    evaluator._call_llm_async = AsyncMock(return_value="answer")
    evaluator._run_eval_with_config = Mock(return_value=(1.0, "matched"))

    for prefix in ("", "Improved instructions: "):
        result = asyncio.run(
            evaluator._evaluate_single_input_impl(
                0, {"agent_prompt": prefix + template, "input": row}
            )
        )
        assert result.metadata["filled_prompt"] == prefix + expected
        evaluator._call_llm_async.assert_awaited_with(prefix + expected)
        evaluator._run_eval_with_config.assert_called_with(
            eval_config, row, "answer", prefix + expected
        )
        assert result.score == 1.0


def test_legacy_direct_evaluation_keeps_literal_placeholder_behavior():
    evaluator = DirectEvaluator([])
    assert evaluator._fill_template(
        "Reply to {{question}} and {question}", {"question": "ping"}
    ) == "Reply to ping and ping"


@pytest.mark.parametrize(
    "candidate",
    [
        "Items: red blue",
        "Items: {{items}}",
        "Items: {% for item in items %}{{ item.name }} {% endfor %}",
        "Items: {% for item in items %}{{ item }}",
    ],
)
def test_jinja_candidate_cannot_drop_or_change_template_logic(candidate):
    evaluator = DirectEvaluator([], initial_agent_prompt=LOOP)
    evaluator.template_format = "jinja"
    evaluator._call_llm_async = AsyncMock(return_value="answer")

    result = asyncio.run(
        evaluator._evaluate_single_input_impl(
            0, {"agent_prompt": candidate, "input": {"items": '["red", "blue"]'}}
        )
    )

    evaluator._call_llm_async.assert_not_awaited()
    assert result.score == 0.0
    assert "Jinja" in result.reason


def test_jinja_rendering_uses_sandbox():
    evaluator = DirectEvaluator([])
    evaluator.template_format = "jinja"
    with pytest.raises(SecurityError, match="unsafe"):
        evaluator._fill_template("{{account.__class__.__mro__}}", {"account": "{}"})


def test_source_format_survives_execution_data_and_optimizer_handoff(monkeypatch):
    from ee.agenthub.fix_your_agent.fix_your_agent import FixYourAgent
    from model_hub.models.develop_dataset import Cell, Column, Row
    from model_hub.models.run_prompt import RunPrompter
    from tfc.temporal.dataset_optimization.activities import (
        _prepare_dataset_execution_data,
    )

    column = SimpleNamespace(id="output", name="output", source="run_prompt", source_id="prompt")
    dataset = SimpleNamespace(
        id="dataset", organization_id="organization", workspace_id="workspace"
    )
    input_column = SimpleNamespace(id="items", name="items")
    columns = Mock()
    columns.__iter__ = Mock(return_value=iter([input_column, column]))
    columns.values_list.return_value = ["items", "output"]
    monkeypatch.setattr(Column.objects, "filter", Mock(return_value=columns))
    rows = Mock()
    rows.order_by.return_value = [SimpleNamespace(id="row")]
    monkeypatch.setattr(Row.objects, "filter", Mock(return_value=rows))
    cells = Mock()
    cells.select_related.return_value = [
        SimpleNamespace(column=input_column, value='["red", "blue"]')
    ]
    monkeypatch.setattr(Cell.objects, "filter", Mock(return_value=cells))
    monkeypatch.setattr(
        RunPrompter.objects,
        "get",
        Mock(return_value=SimpleNamespace(run_prompt_config={"template_format": "jinja"})),
    )

    execution_data = _prepare_dataset_execution_data(column, dataset, [], LOOP)
    assert execution_data["agent_definition_prompt"].get("template_format") == "jinja"
    RunPrompter.objects.get.assert_called_once_with(id=column.source_id, dataset=dataset)
    Column.objects.filter.assert_called_once_with(
        dataset_id=dataset.id,
        dataset__organization_id=dataset.organization_id,
        dataset__workspace_id=dataset.workspace_id,
        dataset__deleted=False,
        deleted=False,
    )
    agent = FixYourAgent()
    agent.optimize = Mock(return_value="done")
    assert agent.optimize_from_execution(execution_data) == "done"
    assert agent.optimize.call_args.kwargs["template_format"] == "jinja"
    assert agent.optimize.call_args.kwargs["scenarios"][0]["input"]["items"] == '["red", "blue"]'


def test_optimizer_passes_format_and_preservation_instructions(monkeypatch):
    from ee.agenthub.fix_your_agent import direct_evaluator, fix_your_agent

    monkeypatch.setattr(fix_your_agent, "update_agent_optimiser_run_step", Mock())
    captured = {}

    class EvaluatorReached(Exception):
        pass

    def capture_evaluator(**kwargs):
        captured.update(kwargs)
        raise EvaluatorReached

    monkeypatch.setattr(direct_evaluator, "DirectEvaluator", capture_evaluator)
    agent = fix_your_agent.FixYourAgent()
    with pytest.raises(EvaluatorReached):
        agent.optimize(
            initial_agent_prompt=LOOP,
            scenarios=[],
            use_direct_evaluation=True,
            template_format="jinja",
            api_key="test-only",
        )
    assert captured["template_format"] == "jinja"
    # Loop locals must not go through the legacy {{variable}} repair mechanism.
    assert captured["template_variables"] is None


def test_jinja_optimizer_instruction_preserves_full_expressions():
    from ee.agent_opt.utils.template_variables import build_jinja_template_instruction

    prompt = LOOP + " Name: {{account.name}}"
    instruction = build_jinja_template_instruction(prompt)
    assert "{% for item in items %}" in instruction
    assert "{{account.name}}" in instruction
    assert "{% endfor %}" in instruction
