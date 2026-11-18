"""A scenario's test conditions (faults): how the harness loads one, which modes the world can apply to a tool, and
the refusal of one it cannot apply. Every rule about a fault lives here, so the editor, the amend receipts and the
draft writes refuse the same faults with the same words."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from simulate.constants.scenario_faults import FAULT_MODES, FaultMode
from simulate.selectors.environment_documents import registration_key
from simulate.services.hosted_harness import HostedHarnessError

if TYPE_CHECKING:
    from simulate.services.harness_actor_rules import ActorRecord

# The one mode that needs the tool to change something: the world commits the change, then the answer is lost.
AFTER_COMMIT: FaultMode = "timeout_after_commit"
# The world refuses the agent's write until an actor records an approval of it.
APPROVAL_REQUIRED: FaultMode = "approval_required"
FAULT_MODE_LABELS: dict[str, str] = {
    "timeout_before_commit": "Times out before it runs",
    "timeout_after_commit": "Runs, then the answer is lost",
    "unavailable": "Unavailable",
    "error": "Answers with an error",
    "approval_required": "Needs approval first",
}
# What each mode means for the agent, shown under the choice.
FAULT_MODE_HINTS: dict[str, str] = {
    "timeout_before_commit": "The call waits, then times out. Nothing changes in the world.",
    "timeout_after_commit": "The world makes the change, then the agent hears a timeout. A retry makes it twice.",
    "unavailable": "Unavailable: the service answers that it is down.",
    "error": "The service answers with the status and message you set.",
    "approval_required": "The world refuses the change until an actor records an approval of it.",
}
FaultField = Literal[
    "on_call", "delay_seconds", "status", "message", "approval", "approved_when"
]
# The fault values each mode reads, as the harness applies it: a delay for the timeouts, a status and message for an
# error; every mode reads which call it holds.
FAULT_MODE_FIELDS: dict[str, list[FaultField]] = {
    "timeout_before_commit": ["on_call", "delay_seconds"],
    "timeout_after_commit": ["on_call", "delay_seconds"],
    "unavailable": ["on_call"],
    "error": ["on_call", "status", "message"],
    # The action that records the decision, and what its arguments must say for it to approve.
    "approval_required": ["approval", "approved_when"],
}


class FaultDefaults(TypedDict):
    on_call: int
    delay_seconds: float
    status: int


class FaultRange(TypedDict):
    min: float
    max: float


class FaultFloor(TypedDict):
    min: int


class FaultLength(TypedDict):
    max_length: int


class FaultBounds(TypedDict):
    on_call: FaultFloor
    delay_seconds: FaultRange
    status: FaultRange
    message: FaultLength


# What the harness gives a fault that leaves a value out, and the values it accepts.
FAULT_DEFAULTS: FaultDefaults = {"on_call": 1, "delay_seconds": 30.0, "status": 500}
FAULT_MESSAGE_MAX_CHARS = 500
FAULT_BOUNDS: FaultBounds = {
    "on_call": {"min": 1},
    "delay_seconds": {"min": 0, "max": 30},
    "status": {"min": 400, "max": 599},
    "message": {"max_length": FAULT_MESSAGE_MAX_CHARS},
}


class ScenarioFault(BaseModel):
    """One scenario fault as the harness loads it (its `Fault` model): the same fields, bounds and lax reading, so
    `on_call: "2"` loads here as it does there."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    mode: FaultMode
    on_call: int = Field(
        default=FAULT_DEFAULTS["on_call"], ge=FAULT_BOUNDS["on_call"]["min"]
    )
    delay_seconds: float = Field(
        default=FAULT_DEFAULTS["delay_seconds"],
        ge=FAULT_BOUNDS["delay_seconds"]["min"],
        le=FAULT_BOUNDS["delay_seconds"]["max"],
    )
    status: int = Field(
        default=FAULT_DEFAULTS["status"],
        ge=FAULT_BOUNDS["status"]["min"],
        le=FAULT_BOUNDS["status"]["max"],
    )
    message: str = Field(default="", max_length=FAULT_MESSAGE_MAX_CHARS)
    # For approval_required: the world action that records the decision, and the arguments that make it an approval.
    approval: str | None = None
    approved_when: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _approval_named(self) -> ScenarioFault:
        if self.mode == APPROVAL_REQUIRED and not (self.approval or "").strip():
            raise PydanticCustomError(
                "approval_unnamed", "approval_required needs an approval action"
            )
        return self


# Why a fault's own value would not load, in the editor's words, by the field it is in.
_FIELD_PROBLEMS = {
    "action": "choose the tool it applies to",
    "mode": "choose what happens to the call",
    "on_call": "the call it applies to must be a whole number from 1",
    "delay_seconds": (
        f"the delay must be between {FAULT_BOUNDS['delay_seconds']['min']:g} and "
        f"{FAULT_BOUNDS['delay_seconds']['max']:g} seconds"
    ),
    "status": (
        f"the status must be a whole number from {FAULT_BOUNDS['status']['min']:g} to "
        f"{FAULT_BOUNDS['status']['max']:g}"
    ),
    "message": f"the message must be text of at most {FAULT_MESSAGE_MAX_CHARS} characters",
    "approval": "choose the action that records the approval",
    "approved_when": "what the approval must say has to be a set of values",
}


class FaultProblem(TypedDict):
    """Why the test world cannot apply one fault: which fault of the list (None when the list itself is unreadable),
    the fault field the editor shows it under (None when it is no one field's), and why, in the editor's words."""

    index: int | None
    field: str | None
    message: str


# A problem with one fault, before it knows its place in the list: (field, message).
_Issue = tuple[str | None, str]


def _shape_issue(error: ValidationError) -> _Issue:
    """The first reason a fault would not load, by the field it is in, in the editor's words."""
    first = error.errors()[0]
    where = str(first["loc"][0]) if first["loc"] else ""
    if first["type"] == "extra_forbidden":
        return None, f"{where} is not part of a test condition"
    if first["type"] == "model_type":
        return None, "a test condition could not be read"
    if first["type"] == "approval_unnamed":
        return "approval", _FIELD_PROBLEMS["approval"]
    if where in _FIELD_PROBLEMS:
        return where, _FIELD_PROBLEMS[where]
    return None, "a test condition could not be read"


def faults_as_stored(faults: object) -> object:
    """Faults as an edit sends them, ready to store: an optional value sent empty (null) is left out, so the harness
    gives it its default; it would refuse a null. Anything that is not a list of faults is kept for the check to
    refuse."""
    if not isinstance(faults, list):
        return faults
    return [
        {key: value for key, value in fault.items() if value is not None}
        if isinstance(fault, dict)
        else fault
        for fault in faults
    ]


def _read(fault: object) -> ScenarioFault | _Issue:
    try:
        return ScenarioFault.model_validate(fault)
    except ValidationError as error:
        return _shape_issue(error)


def read_fault(fault: object) -> ScenarioFault | str:
    """A scenario fault as the harness would load it, or why it would not load."""
    read = _read(fault)
    return read if isinstance(read, ScenarioFault) else read[1]


def scenario_key(scenario: dict[str, object]) -> str:
    """A scenario's key as its registration reads it: its own key, else its name."""
    return registration_key(scenario.get("scenario_key") or scenario.get("name"))


class ApprovalOption(TypedDict):
    """A world action an approval could be recorded by, and who holds it."""

    value: str
    label: str


class FaultModeOption(TypedDict):
    mode: FaultMode
    label: str
    # The line shown under the choice.
    hint: str
    # The fault values this mode reads; the editor asks for these and no others.
    fields: list[FaultField]
    # On approval_required only: the choices for the fault's `approval`.
    approval_options: NotRequired[list[ApprovalOption]]


class FaultTarget(TypedDict):
    """A contract tool a scenario's fault can name, by the world action the fault holds, with the modes the world can
    apply to it and what a fault leaving a value out gets."""

    tool: str
    action: str
    reachable: bool
    writes: bool
    modes: list[FaultModeOption]
    defaults: FaultDefaults
    bounds: FaultBounds


class ResolvedFault(TypedDict):
    """A scenario fault as the harness applies it: a value it leaves out at its default, a value its mode never reads
    as None, its tool and its mode in words."""

    action: str
    tool: str | None
    mode: str
    mode_label: str
    on_call: int | None
    delay_seconds: float | None
    status: int | None
    message: str
    # For approval_required: the action that records the decision, and what it must say.
    approval: str | None
    approved_when: dict[str, object]
    # The fault as one line: its tool (else its action), what happens, on which call, and the value its mode reads.
    line: str


def _applies(
    mode: FaultMode, *, writes: bool, approvals: Sequence[ApprovalOption]
) -> bool:
    """Whether the world can apply a mode to a reachable tool: losing the answer after the change, or holding the
    change for an approval, needs a tool that changes something; an approval also needs an actor who can record it."""
    if mode == AFTER_COMMIT:
        return writes
    if mode == APPROVAL_REQUIRED:
        return writes and bool(approvals)
    return True


def _option(mode: FaultMode, approvals: Sequence[ApprovalOption]) -> FaultModeOption:
    option: FaultModeOption = {
        "mode": mode,
        "label": FAULT_MODE_LABELS[mode],
        "hint": FAULT_MODE_HINTS[mode],
        "fields": FAULT_MODE_FIELDS[mode],
    }
    if mode == APPROVAL_REQUIRED:
        option["approval_options"] = list(approvals)
    return option


def fault_modes(
    *, reachable: bool, writes: bool, approvals: Sequence[ApprovalOption]
) -> list[FaultModeOption]:
    """The modes the world can apply to a tool's calls: none where its door does not stand in front of the tool, an
    answer lost after the change only for a tool that changes something, and an approval first only for such a tool
    when some actor holds an action (``approvals``) that could record one."""
    if not reachable:
        return []
    return [
        _option(mode, approvals)
        for mode in FAULT_MODES
        if _applies(mode, writes=writes, approvals=approvals)
    ]


def approval_options(action: str, cast: Sequence[ActorRecord]) -> list[ApprovalOption]:
    """The world actions the cast's actors hold, other than ``action`` itself, each with who holds it: what an
    approval of it could be recorded by."""
    holders: dict[str, list[str]] = {}
    for actor in cast:
        for one in actor.get("permitted_actions") or []:
            if isinstance(one, str) and one and one != action:
                holders.setdefault(one, []).append(
                    str(actor.get("name") or actor.get("id") or "")
                )
    return [
        {
            "value": one,
            "label": f"{one}, by {', '.join(name for name in holders[one] if name)}",
        }
        for one in sorted(holders)
    ]


def scenario_approvers(scenario: object, cast: Sequence[ActorRecord]) -> set[str]:
    """The actions the actors a scenario plays (its `actors`, a `pre_run` session's, the one it `starts_with`) may
    take: an approval_required fault there can only be met by one of them."""
    doc = scenario if isinstance(scenario, dict) else {}
    played = {
        str(one.get("id"))
        for one in doc.get("actors") or []
        if isinstance(one, dict) and one.get("id")
    }
    for session in doc.get("pre_run") or []:
        if isinstance(session, dict):
            played.update(
                str(one) for one in session.get("actors") or [] if isinstance(one, str)
            )
    starts = doc.get("starts_with")
    if isinstance(starts, dict) and starts.get("actor"):
        played.add(str(starts["actor"]))
    return {
        str(one)
        for actor in cast
        if actor.get("id") in played
        for one in actor.get("permitted_actions") or []
        if isinstance(one, str)
    }


def target_of(action: str, targets: list[FaultTarget]) -> FaultTarget | None:
    """The target a fault's action names: by its world action, else by the tool's own name."""
    return next((one for one in targets if one["action"] == action), None) or next(
        (one for one in targets if one["tool"] == action), None
    )


def resolved_faults(faults: object, targets: list[FaultTarget]) -> list[ResolvedFault]:
    """A scenario's faults as the harness applies them, against ``fault_targets``. A fault that would not load is
    shown as it is stored, with no value it would default."""
    resolved: list[ResolvedFault] = []
    for fault in faults if isinstance(faults, list) else []:
        read = read_fault(fault)
        if isinstance(read, str):
            raw = fault if isinstance(fault, dict) else {}
            action, mode = str(raw.get("action") or ""), str(raw.get("mode") or "")
            target = target_of(action, targets)
            resolved.append(
                _with_line(
                    {
                        "action": action,
                        "tool": target["tool"] if target else None,
                        "mode": mode,
                        "mode_label": FAULT_MODE_LABELS.get(mode, mode),
                        "on_call": None,
                        "delay_seconds": None,
                        "status": None,
                        "message": "",
                        "approval": str(raw["approval"])
                        if isinstance(raw.get("approval"), str)
                        else None,
                        "approved_when": {},
                        "line": "",
                    }
                )
            )
            continue
        target = target_of(read.action, targets)
        reads = FAULT_MODE_FIELDS[read.mode]
        resolved.append(
            _with_line(
                {
                    "action": read.action,
                    "tool": target["tool"] if target else None,
                    "mode": read.mode,
                    "mode_label": FAULT_MODE_LABELS[read.mode],
                    "on_call": read.on_call if "on_call" in reads else None,
                    "delay_seconds": read.delay_seconds
                    if "delay_seconds" in reads
                    else None,
                    "status": read.status if "status" in reads else None,
                    "message": read.message if "message" in reads else "",
                    "approval": read.approval if "approval" in reads else None,
                    "approved_when": read.approved_when
                    if "approved_when" in reads
                    else {},
                    "line": "",
                }
            )
        )
    return resolved


def _with_line(fault: ResolvedFault) -> ResolvedFault:
    """The fault with its one line: "{tool}: {what happens} on call {n}", the delay for a timeout and the status and
    message for an error; a fault naming no tool reads by its action, one naming nothing as a test condition."""
    parts = [
        f"{fault['tool'] or fault['action'] or 'A test condition'}: {fault['mode_label'] or 'unknown'}"
    ]
    if fault["on_call"] is not None:
        parts.append(f" on call {fault['on_call']}")
    if fault["delay_seconds"] is not None:
        parts.append(f" after {fault['delay_seconds']:g} s")
    if fault["status"] is not None:
        said = f": {fault['message']}" if fault["message"] else ""
        parts.append(f" ({fault['status']}{said})")
    if fault["approval"]:
        parts.append(f", recorded by {fault['approval']}")
    fault["line"] = "".join(parts)
    return fault


def _issue(
    fault: object, targets: list[FaultTarget] | None, approvers: set[str] | None
) -> _Issue | None:
    read = _read(fault)
    if not isinstance(read, ScenarioFault):
        return read
    if (
        read.mode == APPROVAL_REQUIRED
        and approvers is not None
        and read.approval not in approvers
    ):
        return "approval", (
            f"no actor in this scenario may take {read.approval}, so the approval could never be recorded"
        )
    if targets is None:
        return None
    target = target_of(read.action, targets)
    if target is None:
        return "action", f"{read.action} is not one of this agent's tools"
    if not target["reachable"]:
        return (
            "action",
            f"{target['tool']} runs inside the agent, so the test world cannot hold its calls",
        )
    if read.mode == APPROVAL_REQUIRED and not target["writes"]:
        return (
            "mode",
            f"{target['tool']} changes no records, so there is no change to approve",
        )
    if read.mode != APPROVAL_REQUIRED and read.mode not in {
        one["mode"] for one in target["modes"]
    }:
        return (
            "mode",
            f"{target['tool']} changes no records, so its answer cannot be lost after a change",
        )
    return None


def fault_problem(
    fault: object,
    targets: list[FaultTarget] | None,
    *,
    approvers: set[str] | None = None,
) -> str | None:
    """Why the test world cannot apply one scenario fault, in the editor's words, or None.

    ``targets`` None (the contract says nothing about the door) checks the fault alone. ``approvers`` is
    ``scenario_approvers`` for the scenario the fault is in; None skips who could approve."""
    issue = _issue(fault, targets, approvers)
    return issue[1] if issue else None


def faults_problems(
    faults: object,
    targets: list[FaultTarget] | None,
    *,
    approvers: set[str] | None = None,
) -> list[FaultProblem]:
    """Every reason the test world cannot apply a scenario's faults, one per fault that has one, each by its place
    in the list and its field; none when it can apply them all."""
    if not isinstance(faults, list):
        return [
            {
                "index": None,
                "field": None,
                "message": "a scenario's test conditions could not be read",
            }
        ]
    problems: list[FaultProblem] = []
    for index, fault in enumerate(faults):
        issue = _issue(fault, targets, approvers)
        if issue:
            problems.append({"index": index, "field": issue[0], "message": issue[1]})
    return problems


class ScenarioFaultProblem(FaultProblem):
    scenario: str


def _faults_by_scenario(scenarios: object) -> dict[str, object]:
    return {
        scenario_key(one): one.get("faults")
        for one in (scenarios if isinstance(scenarios, list) else [])
        if isinstance(one, dict)
    }


def require_valid_faults(
    scenarios: object,
    *,
    targets: list[FaultTarget] | None,
    cast: Sequence[ActorRecord],
    before: object = None,
) -> None:
    """Refuse a fault the test world cannot apply, checking only scenarios whose faults changed from ``before``.
    ``cast`` is the environment's actors, who alone can record an approval.

    Raises ``HostedHarnessError`` ``fault_invalid`` (422)."""
    kept = _faults_by_scenario(before) if isinstance(before, list) else {}
    found: list[ScenarioFaultProblem] = []
    for scenario in scenarios if isinstance(scenarios, list) else []:
        if not isinstance(scenario, dict) or not scenario.get("faults"):
            continue
        key = scenario_key(scenario)
        if key in kept and kept[key] == scenario.get("faults"):
            continue
        name = str(scenario.get("name") or scenario.get("scenario_key") or "a scenario")
        found.extend(
            {
                "scenario": name,
                "index": problem["index"],
                "field": problem["field"],
                "message": problem["message"],
            }
            for problem in faults_problems(
                scenario.get("faults"),
                targets,
                approvers=scenario_approvers(scenario, cast),
            )
        )
    if found:
        first = found[0]
        raise HostedHarnessError(
            "fault_invalid",
            f"Scenario {first['scenario']} has a test condition the test world cannot apply: {first['message']}"
            + (f" (and {len(found) - 1} more)" if len(found) > 1 else "")
            + ". Ask the builder chat to fix it",
            status_code=422,
            details={"problems": found},
        )
