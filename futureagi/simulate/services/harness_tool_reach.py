"""Where an environment's contract tools run, which reach production, and the test conditions its scenarios set."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict

import structlog

from simulate.constants.scenario_faults import FAULT_MODES
from simulate.models import HostedHarnessJob
from simulate.selectors.environment_documents import contract_data, registration_key
from simulate.selectors.stage_outputs import stage_output
from simulate.services.harness_actor_rules import ActorRules, world_actions
from simulate.services.hosted_harness import HostedHarnessError

if TYPE_CHECKING:
    from simulate.models import HarnessEnvironmentVersion

logger = structlog.get_logger(__name__)


FAULT_KEYS = {"action", "mode", "on_call", "delay_seconds", "status", "message"}


def _fault_problem(fault: Any) -> str | None:
    """Why one scenario fault would not load in the harness, or None; mirrors its `Fault` model."""
    if not isinstance(fault, dict):
        return "a fault must be an object"
    unknown = sorted(set(fault) - FAULT_KEYS)
    if unknown:
        return f"unknown field {unknown[0]}"
    action = fault.get("action")
    if not isinstance(action, str) or not action.strip():
        return "action must name a world action"
    if fault.get("mode") not in FAULT_MODES:
        return "mode must be timeout_before_commit, timeout_after_commit, unavailable or error"
    on_call = fault.get("on_call", 1)
    if isinstance(on_call, bool) or not isinstance(on_call, int) or on_call < 1:
        return "on_call must be a whole number from 1"
    delay = fault.get("delay_seconds", 30)
    if (
        isinstance(delay, bool)
        or not isinstance(delay, (int, float))
        or not 0 <= delay <= 30
    ):
        return "delay_seconds must be between 0 and 30"
    status = fault.get("status", 500)
    if (
        isinstance(status, bool)
        or not isinstance(status, int)
        or not 400 <= status <= 599
    ):
        return "status must be a whole number from 400 to 599"
    message = fault.get("message", "")
    if not isinstance(message, str) or len(message) > 500:
        return "message must be text of at most 500 characters"
    return None


def door_reach(contract: Any) -> dict[str, bool]:
    """Whether the world's door can stand in front of each contract tool, by tool name: a tool served by a tools
    service is behind the tool proxy; a Python agent's module-level callable or method (`import`, `construct`) is
    behind the door in its own process. Anything else (a closure, a tool of an agent in another language, an
    unreachable entrypoint) runs where no door stands, so actors cannot take it and faults cannot be set on it. A
    build that found the door bound to a tool but out of reach (the agent started as a script) says so on the entry
    (`door_unreached`); an older build's contract has no such word and reads by mode alone."""
    contract = contract if isinstance(contract, dict) else {}
    runtime = (
        contract.get("runtime") if isinstance(contract.get("runtime"), dict) else {}
    )
    python = str(runtime.get("language") or "python").strip().lower() in (
        "",
        "python",
        "py",
    )
    reach: dict[str, bool] = {}
    for entry in contract.get("tool_entrypoints") or []:
        if not isinstance(entry, dict) or not entry.get("tool"):
            continue
        mode = str(entry.get("mode") or "")
        reach[str(entry["tool"])] = bool(
            (mode == "service" and str(entry.get("endpoint") or "").strip("/"))
            or (
                python
                and mode in ("import", "construct")
                and entry.get("module")
                and entry.get("callable")
                and not entry.get("door_unreached")
            )
        )
    return reach


# Engines that are an API, not a store.
_API_ENGINES = frozenset(
    {"http", "https", "rest", "grpc", "graphql", "websocket", "soap"}
)
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


def _reaches_someone_elses_api(
    dependency: dict[str, Any], served_tools: set[str]
) -> bool:
    """Whether a dependency is an API someone else runs: a public host or URL, or an API whose address comes only
    from configuration. A store, an internal host, a port of its own or the tools the environment serves never are,
    and neither is a dependency we cannot tell (the sandbox's firewall still stops it)."""
    engine = str(dependency.get("engine") or "").strip().lower()
    reached = dependency.get("reached") or {}
    host = str(reached.get("host") or "").strip().lower()
    used_by = {str(tool) for tool in dependency.get("used_by") or []}
    if used_by and used_by <= served_tools:
        return False
    if reached.get("url") or (host and "." in host and host not in _LOCAL_HOSTS):
        return True
    return engine in _API_ENGINES and not host and not reached.get("port")


def production_only(contract: Any) -> list[str]:
    """The contract tools that reach an API someone else runs, other than the agent's own tools service. A test never
    calls those; they read Unsupported for this controlled test."""
    contract = contract if isinstance(contract, dict) else {}
    served = {
        str(entry.get("service"))
        for entry in contract.get("tool_entrypoints") or []
        if isinstance(entry, dict) and entry.get("service")
    }
    served_tools = {
        str(entry.get("tool"))
        for entry in contract.get("tool_entrypoints") or []
        if isinstance(entry, dict)
        and entry.get("mode") == "service"
        and entry.get("tool")
    }
    return sorted(
        {
            str(tool)
            for dependency in contract.get("dependencies") or []
            if isinstance(dependency, dict)
            and _reaches_someone_elses_api(dependency, served_tools)
            and str(dependency.get("name") or "") not in served
            for tool in dependency.get("used_by") or []
        }
    )


PRODUCTION_ONLY_FIX = "serve it from the agent's tools service or remove it"


def needed_production_tools(scenario: Any, blocked: list[str]) -> list[str]:
    """The production-only tools a scenario cannot pass without: those its solution calls, unless one of its
    alternative paths calls none of them."""

    def calls(steps: Any) -> set[str]:
        return (
            {
                str(step.get("tool"))
                for step in steps
                if isinstance(step, dict) and step.get("tool") in blocked
            }
            if isinstance(steps, list)
            else set()
        )

    if any(
        isinstance(path, list) and path and not calls(path)
        for path in scenario.get("alternatives") or []
    ):
        return []
    return sorted(calls(scenario.get("solution")))


class BlockedScenario(TypedDict):
    key: str
    name: str


class BlockedTool(TypedDict):
    tool: str
    scenarios: list[BlockedScenario]


class PinnedEnvironment(Protocol):
    """A run's pins as far as the production-only rule reads them: the environment version, if any."""

    environment_version: HarnessEnvironmentVersion | None


def _version_documents(
    environment: HostedHarnessJob, pins: PinnedEnvironment | None
) -> dict[str, Any]:
    return (
        getattr(pins, "environment_version", None)
        and pins.environment_version.documents
    ) or {}


def _version_scenarios(
    environment: HostedHarnessJob, documents: dict[str, Any]
) -> list[dict[str, Any]]:
    """The scenarios of the run's version, else the environment's own."""
    scenarios = documents.get("scenarios")
    if not isinstance(scenarios, list):
        scenarios = stage_output(environment, "scenarios")
    return [one for one in scenarios or [] if isinstance(one, dict)]


def _scenario_key(scenario: dict[str, Any]) -> str:
    return registration_key(scenario.get("scenario_key") or scenario.get("name"))


def blocked_tools(
    environment: HostedHarnessJob, pins: PinnedEnvironment | None = None
) -> list[BlockedTool]:
    """Each production-only tool of the run's environment version, with the scenarios that need it."""
    documents = _version_documents(environment, pins)
    contract = documents.get("contract") or contract_data(environment)
    blocked = production_only(contract)
    if not blocked:
        return []
    needs: dict[str, list[BlockedScenario]] = {tool: [] for tool in blocked}
    for scenario in _version_scenarios(environment, documents):
        key = _scenario_key(scenario)
        for tool in needed_production_tools(scenario, blocked):
            needs[tool].append({"key": key, "name": str(scenario.get("name") or key)})
    return [{"tool": tool, "scenarios": needs[tool]} for tool in blocked]


class RunVerdict(TypedDict):
    """Whether a run of a selection can start, by the production-only rule the run itself enforces."""

    blocked_tools: list[BlockedTool]
    runnable: bool
    blocked_code: Literal["production_only"] | None
    held_back: int


def run_verdict(
    environment: HostedHarnessJob,
    pins: PinnedEnvironment | None,
    *,
    scenario_keys: list[str] | None,
    hidden: tuple[str, ...] = (),
) -> RunVerdict:
    """The production-only tools a run of ``scenario_keys`` (None: every scenario) meets, each with the selected
    scenarios that need it; how many selected scenarios need one; and whether the run would start. A run every
    selected scenario of which needs one is refused, as ``refuse_production_only`` refuses it. ``hidden`` scenarios
    are those the caller may not read: never selected and never named."""
    found = blocked_tools(environment, pins)
    if found and scenario_keys is None:
        scenario_keys = [
            _scenario_key(one)
            for one in _version_scenarios(
                environment, _version_documents(environment, pins)
            )
        ]
    selected = [key for key in scenario_keys or [] if key not in hidden]
    tools: list[BlockedTool] = [
        {
            "tool": entry["tool"],
            "scenarios": [
                {"key": one["key"], "name": one["name"]}
                for one in entry["scenarios"]
                if one["key"] in selected
            ],
        }
        for entry in found
    ]
    held = {one["key"] for entry in tools for one in entry["scenarios"]}
    runnable = not _all_blocked(found, selected)
    return {
        "blocked_tools": tools,
        "runnable": runnable,
        "blocked_code": None if runnable else "production_only",
        "held_back": len(held),
    }


def _all_blocked(found: list[BlockedTool], scenario_keys: list[str]) -> bool:
    """Whether a selection is refused: it names scenarios, and every one of them needs a production-only tool."""
    needing = {one["key"] for entry in found for one in entry["scenarios"]}
    return bool(scenario_keys) and set(scenario_keys) <= needing


def refuse_production_only(
    environment: HostedHarnessJob,
    pins: PinnedEnvironment | None,
    scenario_keys: list[str],
) -> None:
    """Refuse a run every selected scenario of which needs a production-only tool: none of it could pass."""
    found = blocked_tools(environment, pins)
    if not _all_blocked(found, scenario_keys):
        return
    tools = sorted(
        entry["tool"]
        for entry in found
        if any(one["key"] in scenario_keys for one in entry["scenarios"])
    )
    logger.info(
        "harness_run_refused_production_only",
        environment_id=str(environment.id),
        tools=tools,
    )
    raise HostedHarnessError(
        "production_only",
        f"Every selected scenario needs {', '.join(tools)}, which "
        f"{'reaches production and is' if len(tools) == 1 else 'reach production and are'} blocked in this "
        f"controlled test: {PRODUCTION_ONLY_FIX}, then run again.",
        status_code=409,
    )


NO_WORLD_DOOR = (
    "Actors cannot act and faults cannot be set here: this agent's tools run inside it. Serve the tools from a "
    "tools service to test this."
)


def faults_problem(faults: Any) -> str | None:
    """Why a scenario's list of faults would not load in the harness, or None."""
    if not isinstance(faults, list):
        return "faults must be a list"
    for fault in faults:
        problem = _fault_problem(fault)
        if problem:
            return problem
    return None


def require_valid_faults(scenarios: Any) -> None:
    """Refuse a write whose scenario faults the harness could not load, naming the scenario and the problem, so a
    broken fault never reaches a saved version and fails its checks later."""
    for scenario in scenarios if isinstance(scenarios, list) else []:
        if not isinstance(scenario, dict) or "faults" not in scenario:
            continue
        name = scenario.get("scenario_key") or scenario.get("name") or "a scenario"
        problem = faults_problem(scenario.get("faults"))
        if problem:
            raise HostedHarnessError(
                "scenario_faults_invalid",
                f"Scenario {name} has a fault the test world cannot apply: {problem}. Ask the builder chat to fix it",
                status_code=422,
            )


ToolState = Literal["simulated", "in_process", "unsupported", "unknown"]
DoorProblemCode = Literal["fault_unreachable", "actor_no_door"]


class ToolStatus(TypedDict):
    """Where one contract tool runs in a test, from the contract's own reading of it."""

    tool: str
    # simulated: the world's door stands in front of it; in_process: it runs inside the agent; unsupported: it
    # reaches a system the test world has no copy of; unknown: the build read nothing about where it runs.
    status: ToolState
    # Whether the world restores what it changes before each trial; None when where it runs is not known.
    reset_between_trials: bool | None
    # Where it runs: the agent's callable, or the tools service route; None when the contract names neither.
    runs_at: str | None
    # It declares a change to records.
    writes: bool


class FaultTarget(TypedDict):
    """A contract tool a scenario's fault can name, by the world action the fault holds."""

    tool: str
    action: str
    reachable: bool
    writes: bool


class DoorProblem(TypedDict):
    code: DoorProblemCode
    message: str


def _entrypoints(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in contract.get("tool_entrypoints") or []
        if isinstance(entry, dict) and entry.get("tool")
    ]


def _writes(entry: dict[str, Any] | None) -> bool:
    """Whether a tool's entrypoint declares that it changes records."""
    return bool(entry) and entry.get("effect") == "write"


def _runs_at(entry: dict[str, Any] | None) -> str | None:
    """Where a tool runs, as the contract names it: the agent's module and callable, or the tools service's method
    and route (the method a tools service is sent when the contract states none is POST)."""
    if not entry:
        return None
    if entry.get("callable"):
        module = str(entry.get("module") or "")
        return f"{module}.{entry['callable']}" if module else str(entry["callable"])
    endpoint = str(entry.get("endpoint") or "").lstrip("/")
    if not endpoint:
        return None
    service = str(entry.get("service") or "")
    method = str(entry.get("method") or "POST").upper()
    return f"{service} {method} /{endpoint}" if service else f"{method} /{endpoint}"


def _tool_names(contract: dict[str, Any]) -> list[str]:
    names = []
    for tool in contract.get("tools") or []:
        name = tool if isinstance(tool, str) else (tool or {}).get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def tool_status(contract: object) -> list[ToolStatus]:
    """Each contract tool with where it runs in a test, whether the world resets it between trials, where the contract
    says it runs and whether it changes records."""
    contract = contract if isinstance(contract, dict) else {}
    reach = door_reach(contract)
    unsupported = set(production_only(contract))
    entries = {str(entry["tool"]): entry for entry in _entrypoints(contract)}
    statuses: list[ToolStatus] = []
    for name in _tool_names(contract):
        known = name not in unsupported and name in reach
        status: ToolState
        if name in unsupported:
            status = "unsupported"
        elif known:
            status = "simulated" if reach[name] else "in_process"
        else:
            status = "unknown"
        statuses.append(
            {
                "tool": name,
                "status": status,
                "reset_between_trials": reach[name] if known else None,
                "runs_at": _runs_at(entries.get(name)),
                "writes": _writes(entries.get(name)),
            }
        )
    return statuses


def _action_names(contract: dict[str, Any], rules: ActorRules) -> dict[str, str]:
    """Each tool's world action, named by the sealed world-actions rule (the same names actors are given)."""
    files = {"contract.json": json.dumps(contract).encode("utf-8")}
    names: dict[str, str] = {}
    for one in world_actions(files, rules):
        if one["tool"]:
            names.setdefault(one["tool"], one["action"])
    return names


def fault_targets(contract: object, rules: ActorRules) -> list[FaultTarget]:
    """Each contract tool a fault can name, with whether the world's door stands in front of it and whether it
    changes records. A tool's action is its world action as the sealed rules name it; a tool with none (it runs
    inside the agent, or its route is not one an action can be sent to) is named by the tool itself. A contract with
    no entrypoints offers its tools by name."""
    contract = contract if isinstance(contract, dict) else {}
    reach = door_reach(contract)
    entries = _entrypoints(contract)
    if entries:
        actions = _action_names(contract, rules)
        return [
            {
                "tool": str(entry["tool"]),
                "action": actions.get(str(entry["tool"]), str(entry["tool"])),
                "reachable": bool(reach.get(str(entry["tool"]))),
                "writes": _writes(entry),
            }
            for entry in entries
        ]
    return [
        {
            "tool": name,
            "action": name,
            "reachable": bool(reach.get(name)),
            "writes": False,
        }
        for name in _tool_names(contract)
    ]


def door_targets(contract: object, rules: ActorRules) -> list[FaultTarget] | None:
    """The fault targets a scenario's door problem is judged against, once per page; None when the contract says
    nothing about the world's door."""
    contract = contract if isinstance(contract, dict) else {}
    if not door_reach(contract):
        return None
    return fault_targets(contract, rules)


def _acts(actor: object) -> bool:
    """Whether a scenario's actor acts in the world: it has actions, or one of the agent's tool calls brings it in."""
    if not isinstance(actor, dict):
        return False
    trigger = actor.get("trigger")
    return bool(actor.get("actions")) or (
        isinstance(trigger, dict) and trigger.get("kind") == "on_tool"
    )


def door_problem(
    *, faults: object, actors: object, targets: list[FaultTarget] | None
) -> DoorProblem | None:
    """Why a scenario cannot work as set on this agent, or None: a fault on a tool the world's door does not stand in
    front of, or an acting actor where the door stands in front of no tool. ``targets`` is ``door_targets``; None
    (the contract says nothing about the door) raises neither."""
    if targets is None:
        return None
    by_action = {one["action"]: one for one in targets}
    by_tool = {one["tool"]: one for one in targets}
    for fault in faults if isinstance(faults, list) else []:
        action = str(fault.get("action") or "") if isinstance(fault, dict) else ""
        target = by_action.get(action) or by_tool.get(action)
        if target is None:
            logger.warning("scenario_fault_names_no_target", action=action)
            continue
        if not target["reachable"]:
            return {
                "code": "fault_unreachable",
                "message": f"{target['tool']} runs inside the agent, so the test world cannot apply a test "
                "condition to it",
            }
    acting = any(_acts(actor) for actor in (actors if isinstance(actors, list) else []))
    if acting and targets and not any(one["reachable"] for one in targets):
        return {"code": "actor_no_door", "message": NO_WORLD_DOOR}
    return None
