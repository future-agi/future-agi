"""Checking and launching an ask about one run's calls.

``check_run_ask`` freezes the selection, counts what each question would read, and
runs Instant Eval's gate; it writes nothing and calls no model. ``launch_run_ask``
launches only what the person accepted: a selection whose number of calls changed
since the check is refused, and the ask engine refuses a changed cost.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from simulate.models.ask import SimulateAsk
from simulate.models.test_execution import TestExecution
from simulate.services import ask_engine
from simulate.services.ask_selection import (
    FrozenSelection,
    LaunchRefused,
    Question,
    SelectionKind,
    check_before_launch,
    freeze_selection,
    launch_gate,
)


def question_definitions(questions: Sequence[dict]) -> list[ask_engine.Definition]:
    """The typed questions, keyed q1..q5 in the order they were asked."""
    return [
        ask_engine.typed_question(
            f"q{index}",
            one["question"],
            kinds=one["kinds"],
            answer_type=one["answer_type"],
            labels=one.get("labels") or (),
            descriptions=one.get("descriptions") or None,
        )
        for index, one in enumerate(questions, start=1)
    ]


def freeze_run_selection(
    execution: TestExecution,
    selection: dict,
    *,
    organization_id: Any,
    workspace_id: Any,
    asked_by_id: Any,
) -> FrozenSelection:
    """The whole run, or the ticked calls, which must be this run's."""
    return freeze_selection(
        kind=SelectionKind(selection["kind"]),
        organization_id=organization_id,
        workspace_id=workspace_id,
        run_ids=[str(execution.id)],
        note=selection.get("note") or "",
        call_ids=[str(one) for one in selection.get("call_ids") or ()],
        asked_by_id=asked_by_id,
    )


def check_run_ask(
    selection: FrozenSelection,
    definitions: Sequence[ask_engine.Definition],
    *,
    organization: Any,
) -> dict[str, Any]:
    """The check's counts and cost, with ``refusal`` saying why the launch would
    be refused now (or None). A refusal about the selection itself is raised."""
    questions = [
        Question(one.name, one.kinds, one.decision_model) for one in definitions
    ]
    shown = check_before_launch(
        selection,
        questions,
        organization=organization,
        workspace_id=selection.workspace_id,
    )
    try:
        launch_gate(
            organization=organization,
            workspace_id=selection.workspace_id,
            questions=questions,
            answers=shown["answers"],
        )
        refusal = None
    except LaunchRefused as refused:
        refusal = {"code": refused.code, "message": refused.message}
    return {**shown, "refusal": refusal}


def launch_run_ask(
    selection: FrozenSelection,
    definitions: Sequence[ask_engine.Definition],
    *,
    accepted: dict,
    organization: Any,
    asked_by: Any,
) -> SimulateAsk:
    """Launches what the person accepted (``requested``, ``credits_estimate``)."""
    if len(selection.call_ids) != accepted["requested"]:
        raise LaunchRefused(
            "selection_changed",
            "The calls in this selection changed since it was checked. Check it again.",
            requested=len(selection.call_ids),
        )
    return ask_engine.launch_ask(
        selection,
        definitions,
        organization=organization,
        workspace_id=selection.workspace_id,
        asked_by=asked_by,
        accepted_credits=accepted["credits_estimate"],
    )
