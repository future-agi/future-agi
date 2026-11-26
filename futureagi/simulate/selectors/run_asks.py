"""A run's asks, read for its page: the asks whose selection names the run, each
with requested and the six counts per question, and one row per call and question
naming the call the way the run's table does."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db.models import Q, QuerySet

from simulate.models.ask import SimulateAsk
from simulate.models.eval_result_store import SimulateEvalResult
from simulate.models.test_execution import TestExecution
from simulate.services import ask_engine

UNTITLED_SCENARIO = "Untitled scenario"


def asks_of_run(
    execution: TestExecution, *, organization: Any, workspace_id: Any
) -> QuerySet[SimulateAsk]:
    """The asks kept in this organisation and workspace whose selection names the run."""
    return SimulateAsk._base_manager.filter(
        organization=organization,
        workspace_id=workspace_id,
        selection__description__run_ids__contains=[str(execution.id)],
    ).select_related("asked_by")


def ask_summary(ask: SimulateAsk) -> dict[str, Any]:
    counts = ask_engine.result_counts(ask)
    selection = ask.selection or {}
    description = selection.get("description") or {}
    asked_by = ask.asked_by
    return {
        "id": ask.id,
        "status": ask.status,
        "stop_cause": ask.stop_cause,
        "created_at": ask.created_at,
        "asked_by": (getattr(asked_by, "name", "") or asked_by.email)
        if asked_by is not None
        else None,
        "selection_kind": selection.get("kind") or "",
        "note": description.get("note") or "",
        "requested": len(selection.get("call_ids") or []),
        "credits_estimate": ask.credits_estimate,
        "questions": [
            {
                "key": question["key"],
                "name": question["name"],
                "answer_type": question["answer_type"],
                "labels": question.get("labels") or [],
                "kinds": [kind for _, kind in question.get("inputs") or []],
                "counts": counts[question["key"]],
            }
            for question in ask.questions
        ],
    }


def ask_rows(ask: SimulateAsk) -> list[dict[str, Any]]:
    """The ask's own rows, in the order the calls were picked and the questions
    asked. A row whose call or run is deleted is left out."""
    rows = SimulateEvalResult._base_manager.filter(
        # An ask kept with no workspace matches rows kept with none.
        Q(workspace_id=ask.workspace_id),
        ask=ask,
        origin=SimulateEvalResult.Origin.ASK,
        organization_id=ask.organization_id,
        call_execution__deleted=False,
        call_execution__test_execution__deleted=False,
        call_execution__test_execution__run_test__deleted=False,
    ).values(
        "call_execution_id",
        "call_execution__test_execution_id",
        "call_execution__scenario__name",
        "call_execution__call_metadata__harness_scenario_key",
        "call_execution__call_metadata__harness_trial_index",
        "question_key",
        "state",
        "cause",
        "label",
        "score",
        "confidence",
        "reason",
        "finished_at",
    )
    call_order = {
        call_id: index
        for index, call_id in enumerate((ask.selection or {}).get("call_ids") or [])
    }
    question_order = {
        question["key"]: index for index, question in enumerate(ask.questions)
    }
    ordered = sorted(
        rows,
        key=lambda row: (
            call_order.get(str(row["call_execution_id"]), len(call_order)),
            question_order.get(row["question_key"], len(question_order)),
        ),
    )
    return [
        {
            **{
                key: value
                for key, value in row.items()
                if not key.startswith("call_execution__")
            },
            "test_execution_id": row["call_execution__test_execution_id"],
            "call_name": _call_name(row),
        }
        for row in ordered
    ]


def _call_name(row: Mapping[str, Any]) -> str:
    """The call as the run's table names it: its scenario, and its trial."""
    name = (
        row["call_execution__call_metadata__harness_scenario_key"]
        or row["call_execution__scenario__name"]
        or UNTITLED_SCENARIO
    )
    trial = row["call_execution__call_metadata__harness_trial_index"]
    return f"{name} · Trial {trial}" if trial else str(name)
