"""Checking and launching an ask about one run's calls.

``check_run_ask`` freezes the selection, counts what each question would read, and
runs Instant Eval's gate; it writes nothing and calls no model. ``launch_run_ask``
launches only what the person accepted: a selection whose number of calls changed
since the check is refused, and the ask engine refuses a changed cost.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from django.db import IntegrityError
from django.db.models import QuerySet

from simulate.models.ask import SimulateAsk
from simulate.models.test_execution import TestExecution
from simulate.services import ask_engine
from simulate.services.ask_selection import (
    FrozenSelection,
    LaunchRefused,
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
    # The same questions and gate as the launch (ask_engine.launch_ask), so the
    # check never says yes to what the launch would refuse.
    questions = [definition.as_question() for definition in definitions]
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
            priced_answers=sum(
                one["ready"]
                for question, one in zip(questions, shown["questions"], strict=True)
                if not question.priced_by_use
            ),
        )
        refusal = None
    except LaunchRefused as refused:
        refusal = {"code": refused.code, "message": refused.message}
    return {**shown, "refusal": refusal}


def launch_key_reused() -> LaunchRefused:
    return LaunchRefused(
        "launch_key_reused",
        "This launch was already used for a different question. Check it again.",
    )


class LaunchKeyTaken(Exception):
    """Another launch under the same key saved first."""


def earlier_launch(
    asks: QuerySet[SimulateAsk],
    launch_key: uuid.UUID,
    definitions: Sequence[ask_engine.Definition],
    selection: dict,
) -> SimulateAsk | None:
    """The ask this run already launched under ``launch_key``, or None.

    ``asks`` is this run's asks as the person may read them. A key that launched
    other calls or other questions is refused: it was not this launch."""
    ask = asks.filter(launch_key=launch_key).first()
    if ask is None:
        return None
    launched = ask.selection or {}
    same_calls = launched.get("kind") == selection["kind"] and (
        selection["kind"] != "calls"
        or sorted(launched.get("call_ids") or ())
        == sorted(str(one) for one in selection.get("call_ids") or ())
    )
    # Each stored question is its definition plus what the launch filled in: a
    # typed question's saved template, its version. The rest must be the same.
    stored = ask.questions or []
    wanted = [_as_sent(definition) for definition in definitions]
    same_questions = len(stored) == len(wanted) and all(
        {key: question.get(key) for key in one} == one
        for question, one in zip(stored, wanted, strict=True)
    )
    if not (same_calls and same_questions):
        raise launch_key_reused()
    return ask


def _as_sent(definition: ask_engine.Definition) -> dict[str, Any]:
    """The definition's fields as the person sent them; a typed question has no
    template until the launch saves one, so its template id is not compared."""
    fields = definition.as_json()
    if fields.get("eval_template_id") is None:
        fields.pop("eval_template_id", None)
    return fields


def launch_run_ask(
    selection: FrozenSelection,
    definitions: Sequence[ask_engine.Definition],
    *,
    accepted: dict,
    organization: Any,
    asked_by: Any,
    launch_key: uuid.UUID | None = None,
) -> SimulateAsk:
    """Launches what the person accepted (``requested``, ``credits_estimate``).

    Raises ``LaunchKeyTaken`` when a launch under the same key saved first;
    nothing of this one was saved or queued."""
    if len(selection.call_ids) != accepted["requested"]:
        raise LaunchRefused(
            "selection_changed",
            "The calls in this selection changed since it was checked. Check it again.",
            requested=len(selection.call_ids),
        )
    try:
        return ask_engine.launch_ask(
            selection,
            definitions,
            organization=organization,
            workspace_id=selection.workspace_id,
            asked_by=asked_by,
            accepted_credits=accepted["credits_estimate"],
            launch_key=launch_key,
        )
    except IntegrityError as error:
        if "uniq_simulate_ask_org_launch_key" not in str(error):
            raise
        raise LaunchKeyTaken from error
