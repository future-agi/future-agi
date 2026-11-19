"""Where an environment's contract tools run, which reach production, and the test conditions its scenarios set."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict

import structlog

from simulate.models import HostedHarnessJob
from simulate.selectors.environment_documents import contract_data
from simulate.selectors.stage_outputs import stage_output
from simulate.services.harness_actor_rules import (
    ActorRecord,
    ActorRules,
    world_actions,
)
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.scenario_faults import (
    FAULT_BOUNDS,
    FAULT_DEFAULTS,
    FaultTarget,
    approval_options,
    fault_modes,
    scenario_key,
    target_of,
)

if TYPE_CHECKING:
    from simulate.models import HarnessEnvironmentVersion

logger = structlog.get_logger(__name__)

# HTTP methods the world reads as a read when a tool declares no effect, as the harness's door does.
READ_METHODS = ("GET", "HEAD", "OPTIONS")


def door_reach(contract: Any) -> dict[str, bool]:
    """Whether the world's door can stand in front of each contract tool, by tool name.

    Only tools-service tools and a Python agent's module-level callables are reachable, unless `door_unreached`."""
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

    @property
    def environment_version(self) -> HarnessEnvironmentVersion | None: ...


def _version_documents(
    environment: HostedHarnessJob, pins: PinnedEnvironment | None
) -> dict[str, Any]:
    version = pins.environment_version if pins is not None else None
    return (version.documents if version is not None else None) or {}


def _version_scenarios(
    environment: HostedHarnessJob, documents: dict[str, Any]
) -> list[dict[str, Any]]:
    """The scenarios of the run's version, else the environment's own."""
    scenarios = documents.get("scenarios")
    if not isinstance(scenarios, list):
        scenarios = stage_output(environment, "scenarios")
    return [one for one in scenarios or [] if isinstance(one, dict)]


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
        key = scenario_key(scenario)
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
    """The production-only tools a run of ``scenario_keys`` (None: all) meets, how many scenarios need one, and
    whether the run would start. ``hidden`` scenarios are never selected or named."""
    found = blocked_tools(environment, pins)
    if found and scenario_keys is None:
        scenario_keys = [
            scenario_key(one)
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
        # A run that starts runs every selected scenario, blocking only the calls; held back means refused.
        "held_back": 0 if runnable else len(held),
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


ToolState = Literal["simulated", "in_process", "unsupported", "unknown"]
DoorProblemCode = Literal["fault_unreachable", "actor_no_door"]
ToolEffect = Literal["read", "write", "unknown"]
TABLES_NOT_RECORDED = (
    "Which tables it reads and changes is not recorded for this environment version"
)


class ToolArgument(TypedDict):
    name: str
    type: str | None
    unit: str | None


class ToolReturn(TypedDict):
    name: str
    kind: str


class ToolTables(TypedDict):
    """The tables a tool reads and changes, where the contract records them."""

    recorded: bool
    read: list[str]
    changed: list[str]
    label: str


class ToolStatus(TypedDict):
    """One contract tool as the Tools card shows it: what it takes and returns, where it runs in a test and what it
    changes, from the contract's own reading of it."""

    tool: str
    # simulated: the world's door stands in front of it; in_process: it runs inside the agent; unsupported: it
    # reaches a system the test world has no copy of; unknown: the build read nothing about where it runs.
    status: ToolState
    status_label: str
    # Whether the world restores what it changes before each trial; None when where it runs is not known.
    reset_between_trials: bool | None
    # Where it runs: the agent's callable, or the tools service route; None when the contract names neither.
    runs_at: str | None
    # It changes records, by the rule the world's door applies (see `_writes`).
    writes: bool
    description: str
    arguments: list[ToolArgument]
    returns: list[ToolReturn]
    requires: list[str]
    refuses: str
    # What its entrypoint declares; unknown when it declares neither.
    effect: ToolEffect
    # What a call changes or clears, in the contract's words.
    effects: str
    tables: ToolTables


class DoorProblem(TypedDict):
    code: DoorProblemCode
    message: str


def _entrypoints(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in contract.get("tool_entrypoints") or []
        if isinstance(entry, dict) and entry.get("tool")
    ]


def _effect(entry: Mapping[str, Any] | None) -> ToolEffect:
    effect = str((entry or {}).get("effect") or "")
    return "read" if effect == "read" else "write" if effect == "write" else "unknown"


def _writes(entry: Mapping[str, Any] | None) -> bool:
    """Whether a tool changes records, as the world's door decides it: its declared effect, else its HTTP method (a
    read-only method reads; anything else counts as a write)."""
    if not entry:
        return False
    effect = _effect(entry)
    if effect != "unknown":
        return effect == "write"
    return str(entry.get("method") or "POST").upper() not in READ_METHODS


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


def _tool_specs(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each contract tool by name; a tool written as its bare name has nothing else recorded."""
    specs: dict[str, dict[str, Any]] = {}
    for tool in contract.get("tools") or []:
        spec = {"name": tool} if isinstance(tool, str) else tool
        name = spec.get("name") if isinstance(spec, dict) else None
        if isinstance(name, str) and name:
            specs.setdefault(name, spec)
    return specs


def _texts(value: object) -> list[str]:
    return (
        [str(one) for one in value if isinstance(one, str)]
        if isinstance(value, list)
        else []
    )


def _words(value: object) -> dict[str, str]:
    return (
        {str(key): str(one) for key, one in value.items() if isinstance(one, str)}
        if isinstance(value, dict)
        else {}
    )


def _tables(spec: Mapping[str, Any]) -> ToolTables:
    """The tables a tool reads and changes, where its contract entry lists them; said as not recorded otherwise."""
    if not isinstance(spec.get("tables_read"), list) and not isinstance(
        spec.get("tables_changed"), list
    ):
        return {
            "recorded": False,
            "read": [],
            "changed": [],
            "label": TABLES_NOT_RECORDED,
        }
    read, changed = _texts(spec.get("tables_read")), _texts(spec.get("tables_changed"))
    return {
        "recorded": True,
        "read": read,
        "changed": changed,
        "label": f"Reads {', '.join(read) or 'no table'}; changes {', '.join(changed) or 'no table'}",
    }


# What a test does with each tool, in words. The harness has no recorded-response or approved-external-connection
# kind of tool: a tool is behind the world's door, inside the agent, or reaches a system the world has no copy of.
STATUS_LABELS: dict[ToolState, str] = {
    "simulated": "Simulated",
    "in_process": "Runs inside the agent",
    "unsupported": "Unsupported",
    "unknown": "Where it runs is not known",
}


def _arguments(spec: Mapping[str, Any]) -> list[ToolArgument]:
    """A tool's arguments with their type and unit as the contract writes them: by name in `arg_types` and
    `arg_units`, or on an argument written as an object (`{name, type, unit}`)."""
    types, units = _words(spec.get("arg_types")), _words(spec.get("arg_units"))
    arguments: list[ToolArgument] = []
    args = spec.get("args")
    for arg in args if isinstance(args, list) else []:
        own = arg if isinstance(arg, dict) else {"name": arg}
        name = own.get("name")
        if not isinstance(name, str) or not name:
            continue
        kind, unit = own.get("type"), own.get("unit")
        arguments.append(
            {
                "name": name,
                "type": (kind if isinstance(kind, str) and kind else None)
                or types.get(name)
                or None,
                "unit": (unit if isinstance(unit, str) and unit else None)
                or units.get(name)
                or None,
            }
        )
    return arguments


def tool_status(contract: object) -> list[ToolStatus]:
    """Each contract tool as the Tools card shows it: its arguments, what it returns, when it refuses, where it runs
    in a test, whether the world resets it between trials and what it changes."""
    contract = contract if isinstance(contract, dict) else {}
    reach = door_reach(contract)
    unsupported = set(production_only(contract))
    entries = {str(entry["tool"]): entry for entry in _entrypoints(contract)}
    statuses: list[ToolStatus] = []
    for name, spec in _tool_specs(contract).items():
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
                "status_label": STATUS_LABELS[status],
                "reset_between_trials": reach[name] if known else None,
                "runs_at": _runs_at(entries.get(name)),
                "writes": _writes(entries.get(name)),
                "description": str(spec.get("description") or ""),
                "arguments": _arguments(spec),
                "returns": [
                    {"name": field, "kind": kind}
                    for field, kind in _words(spec.get("returns")).items()
                ],
                "requires": _texts(spec.get("requires")),
                "refuses": str(spec.get("refuses") or ""),
                "effect": _effect(entries.get(name)),
                "effects": str(spec.get("effects") or ""),
                "tables": _tables(spec),
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


def _target(
    tool: str,
    action: str,
    *,
    reachable: bool,
    writes: bool,
    cast: Sequence[ActorRecord],
) -> FaultTarget:
    approvals = approval_options(action, cast)
    return {
        "tool": tool,
        "action": action,
        "reachable": reachable,
        "writes": writes,
        "modes": fault_modes(reachable=reachable, writes=writes, approvals=approvals),
        "defaults": FAULT_DEFAULTS,
        "bounds": FAULT_BOUNDS,
    }


def fault_targets(
    contract: object, rules: ActorRules, cast: Sequence[ActorRecord]
) -> list[FaultTarget]:
    """Each contract tool a fault can name, whether the door holds it, whether it writes and its fault modes; for a
    write, the actions of ``cast`` (the environment's actors) an approval of it could be recorded by.

    A tool with no world action is named by the tool itself."""
    contract = contract if isinstance(contract, dict) else {}
    reach = door_reach(contract)
    entries = _entrypoints(contract)
    if entries:
        actions = _action_names(contract, rules)
        return [
            _target(
                str(entry["tool"]),
                actions.get(str(entry["tool"]), str(entry["tool"])),
                reachable=bool(reach.get(str(entry["tool"]))),
                writes=_writes(entry),
                cast=cast,
            )
            for entry in entries
        ]
    return [
        _target(name, name, reachable=False, writes=False, cast=cast)
        for name in _tool_specs(contract)
    ]


def door_targets(
    contract: object, rules: ActorRules, cast: Sequence[ActorRecord]
) -> list[FaultTarget] | None:
    """The fault targets a scenario's door problem and its faults are judged against, once per page; None when the
    contract says nothing about the world's door."""
    contract = contract if isinstance(contract, dict) else {}
    if not door_reach(contract):
        return None
    return fault_targets(contract, rules, cast)


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
    for fault in faults if isinstance(faults, list) else []:
        action = str(fault.get("action") or "") if isinstance(fault, dict) else ""
        target = target_of(action, targets)
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
