"""An ask's selection, frozen at asking, and the check before it launches.

Four ways to select (runs, chosen calls, a scenario group, a filter) become one
thing: an ordered list of call ids with the user's own description, who asked
and when. It is never worked out again. ``FrozenSelection.as_json()`` is what the
ask stores.

Before launch, every call is read for the kinds each question lists, and the
counts by kind and cause are shown. The launch then passes Instant Eval's gate
(the capability, the organisation's key for the decision model, credits) or is
refused with the reason. Nothing here calls an evaluator or spends anything.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from django.db.models import Q

from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services.call_evidence import (
    CallDeleted,
    CallNotInWorkspace,
    Kind,
    State,
    in_workspace,
    read_call_evidence,
)

# An ask over more calls is refused with the count (a guessed limit).
MAX_CALLS = 200
# The number of questions Instant Eval takes in one run.
MAX_QUESTIONS = 5
# A call may be selected once it has finished, whatever its outcome.
FINISHED_CALL_STATUSES = frozenset(
    {
        CallExecution.CallStatus.COMPLETED,
        CallExecution.CallStatus.FAILED,
        CallExecution.CallStatus.CANCELLED,
    }
)
NOT_FOUND = "Some of the selected runs or calls can't be found in this workspace."
AI_PROVIDERS_SETTINGS_PATH = "/dashboard/settings/ai-providers"


class SelectionKind(StrEnum):
    RUNS = "runs"
    CALLS = "calls"
    SCENARIOS = "scenarios"
    FILTER = "filter"


class SelectionRefused(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class LaunchRefused(SelectionRefused):
    """The ask cannot launch; nothing has been spent."""


@dataclass(frozen=True)
class Question:
    name: str
    kinds: tuple[Kind, ...]
    decision_model: str


@dataclass(frozen=True)
class FrozenSelection:
    kind: SelectionKind
    call_ids: tuple[str, ...]
    description: dict[str, Any]
    agent_definition_id: str
    organization_id: str
    workspace_id: str | None
    asked_by_id: str | None = None
    asked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "call_ids": list(self.call_ids),
            "description": self.description,
            "agent_definition_id": self.agent_definition_id,
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "asked_by_id": self.asked_by_id,
            "asked_at": self.asked_at,
        }


# --- freezing -----------------------------------------------------------------------


def freeze_selection(
    *,
    kind: SelectionKind,
    organization_id: Any,
    workspace_id: Any,
    run_ids: Sequence[Any],
    note: str = "",
    call_ids: Sequence[Any] = (),
    scenario_ids: Sequence[Any] = (),
    filters: dict[str, Any] | None = None,
    asked_by_id: Any = None,
) -> FrozenSelection:
    """The calls a person picked, as an ordered list, or a refusal saying why."""
    kind = SelectionKind(kind)
    runs = _runs(run_ids, organization_id, workspace_id)
    description: dict[str, Any] = {
        "kind": kind.value,
        "run_ids": [str(run.id) for run in runs],
        "note": note,
    }
    if kind == SelectionKind.RUNS:
        _refuse_too_many(
            CallExecution._base_manager.filter(
                test_execution_id__in=[run.id for run in runs], deleted=False
            ).count()
        )
        calls = _calls_of(runs, Q())
    elif kind == SelectionKind.CALLS:
        description["call_ids"] = [str(one) for one in call_ids]
        calls = _chosen_calls(runs, call_ids)
    elif kind == SelectionKind.SCENARIOS:
        description["scenario_ids"] = [str(one) for one in scenario_ids]
        calls = _scenario_calls(runs, scenario_ids)
    else:
        description["filters"] = filters or {}
        calls = _filtered_calls(runs, filters or {})

    if not calls:
        raise SelectionRefused("empty", "No calls match this selection.")
    unfinished = [call for call in calls if call.status not in FINISHED_CALL_STATUSES]
    if unfinished:
        raise SelectionRefused(
            "not_finished",
            f"{len(unfinished)} of the {len(calls)} selected calls "
            f"{'hasn' if len(unfinished) == 1 else 'haven'}'t finished yet. "
            "Try again in a moment.",
            unfinished=len(unfinished),
        )
    _refuse_too_many(len(calls))
    return FrozenSelection(
        kind=kind,
        call_ids=tuple(str(call.id) for call in calls),
        description=description,
        agent_definition_id=_one_agent(runs),
        organization_id=str(organization_id),
        workspace_id=None if workspace_id is None else str(workspace_id),
        asked_by_id=None if asked_by_id is None else str(asked_by_id),
    )


def _refuse_too_many(count: int) -> None:
    if count > MAX_CALLS:
        raise SelectionRefused(
            "too_many_calls",
            f"An ask can take at most {MAX_CALLS} calls; this selection has {count}.",
            count=count,
        )


def _runs(run_ids: Sequence[Any], organization_id: Any, workspace_id: Any) -> list:
    ids = _uuids(run_ids)
    # Read by id alone and checked against the asker's tenant below, so the
    # request's workspace context neither widens nor narrows the selection.
    found = {
        str(run.id): run
        for run in TestExecution._base_manager.filter(
            id__in=ids, deleted=False, run_test__deleted=False
        ).select_related("run_test")
    }
    asked = [found.get(str(one)) for one in dict.fromkeys(ids)]
    runs = [run for run in asked if run is not None]
    if (
        not runs
        or len(runs) != len(asked)
        or not all(
            in_workspace(run.run_test, organization_id, workspace_id) for run in runs
        )
    ):
        raise SelectionRefused("not_found", NOT_FOUND)
    return runs


def _uuids(values: Iterable[Any]) -> list[uuid.UUID]:
    ids = []
    for value in values:
        try:
            ids.append(uuid.UUID(str(value)))
        except ValueError:
            raise SelectionRefused("not_found", NOT_FOUND) from None
    return ids


def _calls_of(runs: list, where: Q) -> list[CallExecution]:
    """The runs' calls matching ``where``, run by run in the order picked."""
    rows = (
        CallExecution._base_manager.filter(
            where, test_execution_id__in=[run.id for run in runs], deleted=False
        )
        .only("id", "status", "test_execution_id")
        .order_by("created_at", "id")
    )
    by_run: dict[Any, list[CallExecution]] = {run.id: [] for run in runs}
    for call in rows:
        by_run[call.test_execution_id].append(call)
    return [call for run in runs for call in by_run[run.id]]


def _chosen_calls(runs: list, call_ids: Sequence[Any]) -> list[CallExecution]:
    ids = list(dict.fromkeys(_uuids(call_ids)))
    found = {call.id: call for call in _calls_of(runs, Q(id__in=ids))}
    if len(found) != len(ids):
        raise SelectionRefused("not_found", NOT_FOUND)
    return [found[one] for one in ids]


def _scenario_calls(runs: list, scenario_ids: Sequence[Any]) -> list[CallExecution]:
    ids = _uuids(scenario_ids)
    # A hosted trial names its scenario; its call's own scenario row may be shared.
    return _calls_of(
        runs,
        Q(hosted_harness_execution__source_scenario__scenario_id__in=ids)
        | Q(hosted_harness_execution__isnull=True, scenario_id__in=ids),
    )


def _filtered_calls(runs: list, filters: dict[str, Any]) -> list[CallExecution]:
    """The calls the run page's filter matches now, in the page's order."""
    from simulate.services.run_results_v3_queries import (
        apply_run_call_query,
        run_calls_queryset,
    )
    from simulate.views.run_results_v3 import RunCallFiltersSerializer

    query = RunCallFiltersSerializer(data={"filters": filters})
    if not query.is_valid():
        raise SelectionRefused(
            "bad_filter",
            "This filter can't be used to select calls.",
            errors=query.errors,
        )
    ids: list[Any] = []
    for run in runs:
        matched = apply_run_call_query(run_calls_queryset(run), query.validated_data)
        ids.extend(matched.values_list("id", flat=True))
    _refuse_too_many(len(ids))
    found = {call.id: call for call in _calls_of(runs, Q(id__in=ids))}
    return [found[one] for one in ids if one in found]


def _one_agent(runs: list) -> str:
    agents = {
        run.agent_definition_id or run.run_test.agent_definition_id for run in runs
    }
    if None in agents:
        raise SelectionRefused("no_agent", "A selected run has no agent.")
    if len(agents) > 1:
        raise SelectionRefused(
            "more_than_one_agent", "An ask's calls must all be of one agent."
        )
    return str(agents.pop())


# --- the check before launch ---------------------------------------------------------


def check_before_launch(
    selection: FrozenSelection,
    questions: Sequence[Question],
    *,
    organization: Any,
    workspace_id: Any,
) -> dict[str, Any]:
    """For each question, how many calls are ready, unmeasured or not applicable,
    by kind and cause, and the credits the ready ones would cost."""
    if not 1 <= len(questions) <= MAX_QUESTIONS or any(
        not question.kinds for question in questions
    ):
        raise LaunchRefused(
            "questions",
            f"An ask takes 1 to {MAX_QUESTIONS} questions, each reading at least one kind.",
        )
    if (str(organization.id), None if workspace_id is None else str(workspace_id)) != (
        selection.organization_id,
        selection.workspace_id,
    ):
        # Checked only where it was made, with the same answer as a selection
        # that does not exist.
        raise LaunchRefused("not_found", NOT_FOUND)
    kinds = list(
        dict.fromkeys(kind for question in questions for kind in question.kinds)
    )
    calls = {
        str(call.id): call
        for call in CallExecution._base_manager.filter(
            id__in=list(selection.call_ids),
            deleted=False,
            test_execution__deleted=False,
            test_execution__run_test__deleted=False,
        ).select_related("test_execution__run_test")
    }
    if gone := len(selection.call_ids) - len(calls):
        raise LaunchRefused(
            "calls_gone",
            f"{gone} of the {len(selection.call_ids)} selected calls "
            f"{'has' if gone == 1 else 'have'} been deleted since the ask was made.",
            gone=gone,
        )
    shared: dict[Any, Any] = {}
    try:
        states = [
            {
                kind: (evidence.state, evidence.cause)
                for kind, evidence in read_call_evidence(
                    calls[call_id],
                    kinds,
                    organization_id=organization.id,
                    workspace_id=workspace_id,
                    shared=shared,
                ).items()
            }
            for call_id in selection.call_ids
        ]
    except CallNotInWorkspace:
        # A run moved to another workspace since the ask was made.
        raise LaunchRefused("not_found", NOT_FOUND) from None
    except CallDeleted:
        raise LaunchRefused(
            "calls_gone",
            "A selected call has been deleted since the ask was made.",
        ) from None
    counted = [_count(question, states) for question in questions]
    answers = sum(one["ready"] for one in counted)
    return {
        "requested": len(selection.call_ids),
        "questions": counted,
        "answers": answers,
        "credits_estimate": _credits(answers),
    }


def _count(question: Question, states: list[dict]) -> dict[str, Any]:
    by_kind: dict[str, dict[str, Any]] = {
        kind.value: {"present": 0, "not_applicable": 0, "not_captured": Counter()}
        for kind in question.kinds
    }
    ready = unmeasured = not_applicable = 0
    for call in states:
        seen = [call[kind] for kind in question.kinds]
        for kind, (state, cause) in zip(question.kinds, seen, strict=True):
            row = by_kind[kind.value]
            if state == State.NOT_CAPTURED:
                row["not_captured"][cause.value] += 1
            else:
                row[state.value] += 1
        if any(state == State.NOT_CAPTURED for state, _ in seen):
            unmeasured += 1
        elif any(state == State.NOT_APPLICABLE for state, _ in seen):
            not_applicable += 1
        else:
            ready += 1
    for row in by_kind.values():
        row["not_captured"] = dict(row["not_captured"])
    return {
        "name": question.name,
        "kinds": [kind.value for kind in question.kinds],
        "ready": ready,
        "unmeasured": unmeasured,
        "not_applicable": not_applicable,
        "by_kind": by_kind,
    }


def _credits(answers: int) -> float | None:
    """What the answers cost on Instant Eval's price, or None where it is not billed."""
    from tracer.services import instant_eval_run

    try:
        rate = instant_eval_run.credits_per_answer()
    except instant_eval_run.CreditCheckUnavailable:
        return None
    return None if rate is None else round(answers * rate, 2)


# --- the gate ---------------------------------------------------------------------------


def launch_gate(
    *,
    organization: Any,
    workspace_id: Any,
    questions: Sequence[Question],
    answers: int,
) -> None:
    """Instant Eval's own checks before a launch: refuses with the reason, else None.
    Nothing is spent or kept here; a key that is read is dropped at once."""
    if answers < 1:
        raise LaunchRefused(
            "nothing_ready", "No selected call has the evidence these questions read."
        )
    _check_capability(organization)
    for model in dict.fromkeys(question.decision_model for question in questions):
        _check_key(model, organization, workspace_id)
    _check_credits(organization, answers)


def _check_capability(organization: Any) -> None:
    try:
        from tfc.ee_gating import EEFeature, FeatureUnavailable, check_ee_feature
    except ImportError:
        raise LaunchRefused(
            "ENTITLEMENT_DENIED", "Instant Eval isn't available in this build."
        ) from None
    try:
        check_ee_feature(EEFeature.INSTANT_EVAL, org_id=str(organization.id))
    except FeatureUnavailable as denied:
        raise LaunchRefused(
            "ENTITLEMENT_DENIED", str(denied.detail), feature=denied.feature
        ) from None


def _no_description(error: BaseException) -> dict | None:
    return None


def _check_key(model: str, organization: Any, workspace_id: Any) -> None:
    from agentic_eval.core_evals.run_prompt.litellm_models import (
        MissingProviderKeyError,
        missing_provider_key_message,
    )
    from evaluations.decision_models import get_decision_model
    from evaluations.engine import instance
    from tfc.ee_gates import is_jev_model
    from tfc.middleware.workspace_context import (
        get_current_workspace,
        set_workspace_context,
    )

    # A build without ee has no decision models to key: it catches nothing here.
    gateway_errors: tuple[type[Exception], ...] = ()
    describe: Callable[[BaseException], dict | None] = _no_description
    try:
        from ee.decisions import errors as decision_errors
    except ImportError:
        pass
    else:
        gateway_errors = (decision_errors.DecisionGatewayError,)
        describe = decision_errors.describe

    if not is_jev_model(model) and get_decision_model(model) is None:
        raise LaunchRefused(
            "unknown_decision_model", f"'{model}' isn't a decision model."
        )
    org_id = str(organization.id)
    workspace = None if workspace_id is None else str(workspace_id)
    # Read as the eval runner reads it, outside any request's workspace scope;
    # the key itself is dropped at once.
    scope = get_current_workspace()
    set_workspace_context(workspace=None)
    try:
        if is_jev_model(model):
            instance._get_typesafe_api_key(org_id, workspace)
        else:
            instance._get_decision_api_key(model, org_id, workspace)
    except MissingProviderKeyError as missing:
        raise LaunchRefused(
            "JEV_KEY_MISSING", str(missing), settings_path=AI_PROVIDERS_SETTINGS_PATH
        ) from None
    except gateway_errors as failed:
        code = getattr(failed, "code", None)
        if code == "DECISION_KEY_MISSING":
            raise LaunchRefused(
                code,
                missing_provider_key_message(getattr(failed, "provider", None)),
                settings_path=AI_PROVIDERS_SETTINGS_PATH,
            ) from None
        # The gateway's own body-free wording for its code; nothing upstream.
        described = describe(failed) or {}
        raise LaunchRefused(
            code or "decision_check_unavailable",
            described.get("message")
            or "The decision model's key can't be checked right now. Please retry in a moment.",
        ) from None
    finally:
        set_workspace_context(workspace=scope)


def _check_credits(organization: Any, answers: int) -> None:
    from tracer.services import instant_eval_run

    try:
        rate = instant_eval_run.credits_per_answer()
        credits = None if rate is None else round(answers * rate, 2)
        refused = instant_eval_run.check_credits(organization, credits)
    except instant_eval_run.CreditCheckUnavailable:
        raise LaunchRefused(
            "credit_check_unavailable",
            "Credits can't be checked right now, so the ask wasn't started. "
            "Please retry in a moment.",
        ) from None
    if refused is not None:
        from tracer.views.instant_eval_scale import CREDIT_MESSAGES

        code = getattr(refused, "error_code", None) or "INSTANT_EVAL_CREDITS"
        raise LaunchRefused(
            code,
            CREDIT_MESSAGES.get(code)
            or f"This ask needs about {credits:g} credits, more than your "
            "organization has left this month.",
            credits_estimate=credits,
        )
