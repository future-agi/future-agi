"""Evaluation inspection respects the caller's workspace at every depth."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import ObjectDoesNotExist

from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from ai_tools.tests.fixtures import make_dataset_with_rows, make_project, make_trace
from ai_tools.tests.test_evaluate_with_agent import _SCOUT_BRIEF
from ee.agenthub.eval_orchestrator import EvalConfig, EvalOrchestrator
from model_hub.models.develop_dataset import Cell, Dataset
from tfc.middleware.workspace_context import clear_workspace_context, set_workspace_context
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession

pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]

TARGETS = ["cell", "dataset_row", "span", "trace", "session"]
ID_KEYS = dict(zip(TARGETS, ["cell_id", "row_id", "span_id", "trace_id", "session_id"]))


def _sources(context):
    clear_workspace_context()
    dataset, columns, rows = make_dataset_with_rows(context)
    cell = Cell.no_workspace_objects.get(row=rows[0], column=columns[0])
    project = make_project(context)
    session = TraceSession.no_workspace_objects.create(project=project, name="Session")
    trace = make_trace(context, project=project, session=session)
    span = ObservationSpan.no_workspace_objects.create(
        id=str(uuid4()), project=project, trace=trace, name="Span", observation_type="llm",
        input={"message": "workspace input"}, output={"message": "workspace output"},
    )
    return {
        "cell": cell, "dataset_row": rows[0], "span": span, "trace": trace,
        "session": session, "dataset": dataset, "project": project,
    }


def _evaluator(context, source):
    # Set scope after construction so these tests also run on older configs.
    config = EvalConfig(criteria="Check output", input_scope="cell", source_id=str(source.pk))
    config.organization_id = str(context.organization.pk)
    config.workspace = context.workspace
    with (
        patch("ee.agenthub.eval_orchestrator.orchestrator.TuringClient"),
        patch("ee.agenthub.eval_orchestrator.orchestrator.LLM"),
    ):
        return EvalOrchestrator(eval_config=config, scout_brief=dict(_SCOUT_BRIEF))


@pytest.fixture(params=["default_to_second", "second_to_default"])
def inspection_pair(tool_context, request):
    clear_workspace_context()
    workspace = Workspace.no_workspace_objects.create(
        organization=tool_context.organization, created_by=tool_context.user,
        name="Second workspace", is_default=False, is_active=True,
    )
    second = ToolContext(
        user=tool_context.user, organization=tool_context.organization, workspace=workspace,
    )
    caller, other = (tool_context, second)
    if request.param == "second_to_default":
        caller, other = other, caller
    own, hidden = _sources(caller), _sources(other)
    evaluator = _evaluator(caller, own["cell"])
    set_workspace_context(
        workspace=caller.workspace, organization=caller.organization, user=caller.user,
    )
    try:
        yield evaluator, caller, own, hidden
    finally:
        clear_workspace_context()


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("depth", ["overview", "detail"])
def test_inspect_own_workspace_control(inspection_pair, target, depth):
    evaluator, _, own, _ = inspection_pair
    result = evaluator._tool_inspect_data(target, str(own[target].pk), depth)
    assert result[ID_KEYS[target]] == str(own[target].pk)
    if target == "cell":
        assert result["value"] == own[target].value
    elif target == "dataset_row":
        assert result["cells"][own["cell"].column.name]["value"] == own["cell"].value
    elif target == "trace":
        if depth == "overview":
            assert result["span_count"] == 1
        else:
            assert result["spans"][0]["span_id"] == str(own["span"].pk)
    elif target == "session":
        assert result["traces"][0]["trace_id"] == str(own["trace"].pk)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("depth", ["overview", "detail"])
@pytest.mark.parametrize("ambient_scope", ["caller", "absent"])
def test_inspect_other_workspace_denied(inspection_pair, target, depth, ambient_scope):
    evaluator, _, own, hidden = inspection_pair
    if ambient_scope == "absent":
        clear_workspace_context()
    # Successful inspection precedes each denial; an unusable tool cannot pass.
    result = evaluator._tool_inspect_data(target, str(own[target].pk), depth)
    assert result[ID_KEYS[target]] == str(own[target].pk)
    with pytest.raises(ObjectDoesNotExist):
        evaluator._tool_inspect_data(target, str(hidden[target].pk), depth)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("workspace_state", ["missing", "null"])
def test_inspect_without_config_workspace_fails_closed(inspection_pair, target, workspace_state):
    evaluator, _, own, _ = inspection_pair
    assert evaluator._tool_inspect_data(target, str(own[target].pk), "detail")
    if workspace_state == "missing":
        evaluator.eval_config = SimpleNamespace(
            criteria="Check output", input_scope="cell", source_id=str(own["cell"].pk),
        )
    else:
        evaluator.eval_config.workspace = None
    # The dispatcher must return only an error even with a valid ambient scope.
    result = evaluator._execute_tool(
        "inspect_data", {"target_type": target, "target_id": str(own[target].pk), "depth": "detail"},
    )
    assert set(result) == {"error"}, result


@pytest.mark.parametrize("depth", ["overview", "detail"])
def test_inspect_deleted_dataset_row_denied(inspection_pair, depth):
    evaluator, _, own, _ = inspection_pair
    assert evaluator._tool_inspect_data("dataset_row", str(own["dataset_row"].pk), depth)
    Dataset.no_workspace_objects.filter(pk=own["dataset"].pk).update(deleted=True)
    with pytest.raises(ObjectDoesNotExist):
        evaluator._tool_inspect_data("dataset_row", str(own["dataset_row"].pk), depth)


@pytest.mark.parametrize("target", ["trace", "session"])
@pytest.mark.parametrize("depth", ["overview", "detail"])
def test_inspect_nested_other_workspace_span_excluded(inspection_pair, target, depth):
    evaluator, _, own, hidden = inspection_pair
    ObservationSpan.no_workspace_objects.filter(pk=hidden["span"].pk).update(trace=own["trace"])
    result = evaluator._tool_inspect_data(target, str(own[target].pk), depth)
    trace_result = result if target == "trace" else result["traces"][0]
    if depth == "overview":
        assert trace_result["span_count"] == 1
    else:
        assert [span["span_id"] for span in trace_result["spans"]] == [str(own["span"].pk)]


@pytest.mark.parametrize("depth", ["overview", "detail"])
def test_inspect_nested_other_workspace_trace_excluded(inspection_pair, depth):
    evaluator, _, own, hidden = inspection_pair
    Trace.no_workspace_objects.filter(pk=hidden["trace"].pk).update(session=own["session"])
    result = evaluator._tool_inspect_data("session", str(own["session"].pk), depth)
    assert [trace["trace_id"] for trace in result["traces"]] == [str(own["trace"].pk)]
