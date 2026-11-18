"""An environment's contract and scenario keys, read from what the job row already holds."""

from __future__ import annotations

import re
from typing import Any

from simulate.models import HostedHarnessJob


def contract_data(job: HostedHarnessJob) -> dict[str, Any]:
    """The contract snapshot, preferring the verified copy over the live one."""
    data = row_stage_output(job, "contract")
    return data if isinstance(data, dict) else {}


def recorded_contract(documents: Any) -> dict[str, Any]:
    """The contract a version's documents record it runs with: the one its build sealed (or the default's it
    keeps), else the authored one."""
    documents = documents if isinstance(documents, dict) else {}
    for field in ("run_contract", "contract"):
        contract = documents.get(field)
        if isinstance(contract, dict) and contract:
            return contract
    return {}


# How far back a version's ancestry is read for how its tools are reached; every version saved since records it,
# so only a long run of versions saved before then walks this far.
_MOST_ANCESTORS = 1000


def version_contract(
    job: HostedHarnessJob, version: Any = None, *, key: str | None = None
) -> dict[str, Any]:
    """The contract a run of an environment version uses: ``version`` replaying the bundle under assembly ``key``
    (as the run's launch picks it), else the environment's default version and the bundle its default run replays.

    The contract that bundle was sealed with, else the one the version's documents record, each tool reached as
    the version it was made from runs it unless this version changed how (``reach_as_built``); for a draft or an
    older version that recorded no contract, the default version's; the authoring snapshot only before the
    environment has a version."""
    if version is None:
        version = job.environment_version
        if version is None:
            return contract_data(job)
        if key is None:
            key = default_bundle_key(job)
    resolved = _resolve(job, version, key)
    if resolved:
        return resolved
    default = job.environment_version
    if default is not None and default.id != version.id:
        return version_contract(job)
    return contract_data(job)


def default_bundle_key(job: HostedHarnessJob) -> str | None:
    """The assembly key of the bundle the environment's default run replays; None when it pins no runs."""
    from simulate.services.harness_versioning.assembly import pins_bundle_key
    from simulate.services.hosted_harness import HostedHarnessError, resolve_run_pins

    try:
        pins = resolve_run_pins(job)
    except HostedHarnessError:
        return None
    return pins_bundle_key(pins) if pins is not None else None


def reach_as_built(
    job: HostedHarnessJob, contract: dict[str, Any], authored: Any, base: Any
) -> dict[str, Any]:
    """``contract`` (a version's sealed or recorded one, written from ``authored``), each tool reached as ``base``
    runs it where neither this version's build nor its edit changed how.

    How a tool is reached is a build's fact: a build can serve a tool the author's contract imports, and a later
    save seals or records the author's entry again. So, tool by tool and matched by name, a tool whose entry here
    reaches it as its ``authored`` one does (no build amended it) and as ``base``'s authored one does (no edit
    changed it) is reached as ``base`` runs it; a tool a build amended, or an edit added or changed, keeps its own
    entry, and one removed is gone. Only how a tool is reached is carried."""
    if base is None:
        return contract
    return _overlay(
        contract,
        authored,
        (base.documents or {}).get("contract"),
        # The default version runs the bundle its default run replays; another runs its latest.
        version_contract(job)
        if base.id == job.environment_version_id
        else version_contract(job, base),
    )


def _resolve(job: HostedHarnessJob, version: Any, key: str | None) -> dict[str, Any]:
    """``version``'s run contract, its ancestry walked iteratively until a contract that says how each tool is
    reached on its own: a build's seal, a recorded run contract, a full build's own documents."""
    from simulate.services.harness_versioning.assembly import latest_seal, run_seal
    from simulate.services.harness_versioning.versions import PROVED_ORIGINS

    # Each version whose contract still holds the author's entries, with that contract, nearest first.
    chain: list[tuple[Any, dict[str, Any]]] = []
    base: dict[str, Any] | None = None
    stop: Any = None
    node, node_key = version, key
    for _ in range(_MOST_ANCESTORS):
        documents = node.documents or {}
        # An older version on the walk, which no run names, reads any pair's bundle: reach is the environment's.
        seal = run_seal(job.id, node_key) if node_key else latest_seal(node)
        # A bundle sealed before seals were stamped says nothing: on a version no full build made, it is a reseal.
        resealed = seal is not None and (
            seal.resealed
            or (not seal.stamped and getattr(node, "origin", "") not in PROVED_ORIGINS)
        )
        if seal is not None and not resealed:
            # A build's seal: how it reaches each tool.
            base, stop = seal.contract, node
            break
        if seal is not None:
            own = seal.contract
        elif (
            isinstance(documents.get("run_contract"), dict)
            and documents["run_contract"]
        ):
            base, stop = documents["run_contract"], node
            break
        else:
            authored = documents.get("contract")
            own = authored if isinstance(authored, dict) else {}
            if own and getattr(node, "origin", "") in PROVED_ORIGINS:
                # A full build that sealed the author's contract as it was recorded no separate run contract.
                base, stop = own, node
                break
            if not own:
                if node is version:
                    return {}
                break
        chain.append((node, own))
        parent_id = getattr(node, "parent_id", None)
        if parent_id is None:
            break
        parent = type(node)._base_manager.filter(id=parent_id).first()
        if parent is None:
            break
        node, node_key = parent, None
    resolved = base
    for index in range(len(chain) - 1, -1, -1):
        node, own = chain[index]
        parent = chain[index + 1][0] if index + 1 < len(chain) else stop
        if resolved is None or parent is None:
            resolved = own
            continue
        resolved = _overlay(
            own,
            (node.documents or {}).get("contract"),
            (parent.documents or {}).get("contract"),
            resolved,
        )
    return resolved or {}


def _overlay(
    contract: dict[str, Any], authored: Any, before: Any, base: dict[str, Any]
) -> dict[str, Any]:
    """``contract``'s tools, each reached as in ``base`` where its entry reaches the tool as ``authored`` does and
    as ``before`` (the parent's authored contract) does; the rest of each entry stays its own."""
    entries = contract.get("tool_entrypoints")
    before_entries = _by_tool(before)
    if not isinstance(entries, list) or not before_entries:
        return contract
    own = _by_tool(authored)
    built = _by_tool(base)
    reached = [
        # Only how the tool is reached comes from the build, absent fields included; the rest is this entry's.
        {
            **{field: value for field, value in one.items() if field not in _CARRIED},
            **{field: built[tool][field] for field in _CARRIED if field in built[tool]},
        }
        if isinstance(one, dict)
        and (tool := one.get("tool")) in built
        and _reach(own.get(tool)) == _reach(one) == _reach(before_entries.get(tool))
        else one
        for one in entries
    ]
    return {**contract, "tool_entrypoints": reached}


# The fields of a tool entry that say how the tool is reached.
_REACH_FIELDS = (
    "mode",
    "service",
    "endpoint",
    "method",
    "module",
    "callable",
    "factory",
    "first_arg",
)


_CARRIED = (*_REACH_FIELDS, "door_unreached")
# What an entry that leaves a field empty means by it, as the harness reads the contract.
_REACH_DEFAULTS = {"mode": "unreachable", "method": "POST"}


def _reach(entry: Any) -> tuple[Any, ...] | None:
    """How a tool entry reaches its tool, with an empty or absent field read as its default: a seal can drop the
    keys an authored entry leaves empty, or write out a default it left out. Whether the door is said not to reach
    it counts only as yes or no."""
    if not isinstance(entry, dict):
        return None
    read = {
        field: entry.get(field) or _REACH_DEFAULTS.get(field, "")
        for field in _REACH_FIELDS
    }
    read["method"] = str(read["method"]).upper()
    return (*read.values(), bool(entry.get("door_unreached")))


def _by_tool(contract: Any) -> dict[Any, dict[str, Any]]:
    """A contract's tool entries by the tool each is for."""
    entries = contract.get("tool_entrypoints") if isinstance(contract, dict) else None
    listed = entries if isinstance(entries, list) else []
    return {
        one["tool"]: one for one in listed if isinstance(one, dict) and one.get("tool")
    }


def row_stage_output(job: HostedHarnessJob, kind: str) -> Any:
    """One authoring snapshot from the prefetch, else the row's live copy; never issues a query."""
    for output in getattr(job, "row_outputs", None) or []:
        if output.kind == kind:
            return output.data
    for output in job.stage_outputs or []:
        if isinstance(output, dict) and output.get("kind") == kind:
            return output.get("data")
    return None


def registration_key(value: Any) -> str:
    """The key the runner registers a scenario under: its name slugged to ASCII."""
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
