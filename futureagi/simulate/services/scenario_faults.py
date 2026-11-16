"""A scenario's test conditions (faults): how the harness loads one, which modes the world can apply to a tool, and
the refusal of one it cannot apply. Every rule about a fault lives here, so the editor, the amend receipts and the
draft writes refuse the same faults with the same words."""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from simulate.constants.scenario_faults import FAULT_MODES, FaultMode
from simulate.selectors.environment_documents import registration_key
from simulate.services.hosted_harness import HostedHarnessError

# The one mode that needs the tool to change something: the world commits the change, then the answer is lost.
AFTER_COMMIT: FaultMode = "timeout_after_commit"
FAULT_MODE_LABELS: dict[str, str] = {
    "timeout_before_commit": "Times out before it runs",
    "timeout_after_commit": "Runs, then the answer is lost",
    "unavailable": "Unavailable",
    "error": "Answers with an error",
}


class FaultDefaults(TypedDict):
    on_call: int
    delay_seconds: float
    status: int


class FaultRange(TypedDict):
    min: float
    max: float


class FaultBounds(TypedDict):
    delay_seconds: FaultRange
    status: FaultRange


# What the harness gives a fault that leaves a value out, and the values it accepts.
FAULT_DEFAULTS: FaultDefaults = {"on_call": 1, "delay_seconds": 30.0, "status": 500}
FAULT_BOUNDS: FaultBounds = {
    "delay_seconds": {"min": 0, "max": 30},
    "status": {"min": 400, "max": 599},
}
FAULT_MESSAGE_MAX_CHARS = 500


class ScenarioFault(BaseModel):
    """One scenario fault as the harness loads it (its `Fault` model): the same fields, bounds and lax reading, so
    `on_call: "2"` loads here as it does there."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    mode: FaultMode
    on_call: int = Field(default=FAULT_DEFAULTS["on_call"], ge=1)
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
}


def _shape_problem(error: ValidationError) -> str:
    """The first reason a fault would not load, in the editor's words."""
    first = error.errors()[0]
    where = str(first["loc"][0]) if first["loc"] else ""
    if first["type"] == "extra_forbidden":
        return f"{where} is not part of a test condition"
    if first["type"] == "model_type":
        return "a test condition could not be read"
    return _FIELD_PROBLEMS.get(where, "a test condition could not be read")


def read_fault(fault: object) -> ScenarioFault | str:
    """A scenario fault as the harness would load it, or why it would not load."""
    try:
        return ScenarioFault.model_validate(fault)
    except ValidationError as error:
        return _shape_problem(error)


def scenario_key(scenario: dict[str, object]) -> str:
    """A scenario's key as its registration reads it: its own key, else its name."""
    return registration_key(scenario.get("scenario_key") or scenario.get("name"))


class FaultModeOption(TypedDict):
    mode: FaultMode
    label: str


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


def fault_modes(*, reachable: bool, writes: bool) -> list[FaultModeOption]:
    """The modes the world can apply to a tool's calls: none where its door does not stand in front of the tool, and
    an answer lost after the change only for a tool that changes something."""
    if not reachable:
        return []
    return [
        {"mode": mode, "label": FAULT_MODE_LABELS[mode]}
        for mode in FAULT_MODES
        if mode != AFTER_COMMIT or writes
    ]


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
                {
                    "action": action,
                    "tool": target["tool"] if target else None,
                    "mode": mode,
                    "mode_label": FAULT_MODE_LABELS.get(mode, mode),
                    "on_call": None,
                    "delay_seconds": None,
                    "status": None,
                    "message": "",
                }
            )
            continue
        target = target_of(read.action, targets)
        timeout = read.mode in ("timeout_before_commit", AFTER_COMMIT)
        error = read.mode == "error"
        resolved.append(
            {
                "action": read.action,
                "tool": target["tool"] if target else None,
                "mode": read.mode,
                "mode_label": FAULT_MODE_LABELS[read.mode],
                "on_call": read.on_call,
                "delay_seconds": read.delay_seconds if timeout else None,
                "status": read.status if error else None,
                "message": read.message if error else "",
            }
        )
    return resolved


def fault_problem(fault: object, targets: list[FaultTarget] | None) -> str | None:
    """Why the test world cannot apply one scenario fault, in the editor's words, or None.

    ``targets`` None (the contract says nothing about the door) checks the fault alone."""
    read = read_fault(fault)
    if isinstance(read, str):
        return read
    if targets is None:
        return None
    target = target_of(read.action, targets)
    if target is None:
        return f"{read.action} is not one of this agent's tools"
    if not target["reachable"]:
        return f"{target['tool']} runs inside the agent, so the test world cannot hold its calls"
    if read.mode not in {one["mode"] for one in target["modes"]}:
        return f"{target['tool']} changes no records, so its answer cannot be lost after a change"
    return None


def faults_problem(faults: object, targets: list[FaultTarget] | None) -> str | None:
    """Why the test world cannot apply a scenario's list of faults, or None."""
    if not isinstance(faults, list):
        return "a scenario's test conditions could not be read"
    for fault in faults:
        problem = fault_problem(fault, targets)
        if problem:
            return problem
    return None


def _faults_by_scenario(scenarios: object) -> dict[str, object]:
    return {
        scenario_key(one): one.get("faults")
        for one in (scenarios if isinstance(scenarios, list) else [])
        if isinstance(one, dict)
    }


def require_valid_faults(
    scenarios: object, *, targets: list[FaultTarget] | None, before: object = None
) -> None:
    """Refuse a fault the test world cannot apply, checking only scenarios whose faults changed from ``before``.

    Raises ``HostedHarnessError`` ``fault_invalid`` (422)."""
    kept = _faults_by_scenario(before) if isinstance(before, list) else {}
    for scenario in scenarios if isinstance(scenarios, list) else []:
        if not isinstance(scenario, dict) or not scenario.get("faults"):
            continue
        key = scenario_key(scenario)
        if key in kept and kept[key] == scenario.get("faults"):
            continue
        problem = faults_problem(scenario.get("faults"), targets)
        if problem:
            name = scenario.get("name") or scenario.get("scenario_key") or "a scenario"
            raise HostedHarnessError(
                "fault_invalid",
                f"Scenario {name} has a test condition the test world cannot apply: {problem}. Ask the builder chat "
                "to fix it",
                status_code=422,
            )
