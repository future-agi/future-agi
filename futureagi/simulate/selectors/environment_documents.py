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


def version_contract(
    job: HostedHarnessJob,
    version: Any = None,
    *,
    agent_version_id: Any = None,
    suite_version_id: Any = None,
) -> dict[str, Any]:
    """The contract a run of an environment version uses: ``version``, else the environment's default version, run
    by the agent and suite named, else the environment's default ones.

    The one that pair's ready bundle was sealed with, else the one the version's documents record, else the
    default version's; the authoring snapshot only before the environment has a version. Reads the pair's bundle
    row, and the bundle once per bundle."""
    from simulate.services.harness_versioning.assembly import sealed_contract

    version = version if version is not None else job.environment_version
    if version is None:
        return contract_data(job)
    sealed = sealed_contract(
        version,
        agent_version_id=agent_version_id or job.agent_version_id,
        suite_version_id=suite_version_id or job.suite_version_id,
    )
    recorded = sealed or recorded_contract(version.documents)
    if recorded:
        from simulate.services.harness_versioning.versions import PROVED_ORIGINS

        # A full build's version reaches each tool as that build sealed it; anything else (a save's reseal, a
        # version no build sealed) sealed or recorded the author's entries again.
        if getattr(version, "origin", "") in PROVED_ORIGINS:
            return recorded
        return _reached_as_built(
            job, version, recorded, agent_version_id, suite_version_id
        )
    # A draft or older version that recorded no contract runs as the default version would, never as the first
    # authoring snapshot.
    default = job.environment_version
    if default is not None and default.id != version.id:
        return version_contract(
            job,
            default,
            agent_version_id=agent_version_id,
            suite_version_id=suite_version_id,
        )
    return contract_data(job)


def _reached_as_built(
    job: HostedHarnessJob,
    version: Any,
    recorded: dict[str, Any],
    agent_version_id: Any,
    suite_version_id: Any,
) -> dict[str, Any]:
    """A version's recorded contract, each tool reached as the version it was made from runs it unless this version
    changed how (``reach_as_built``)."""
    parent_id = getattr(version, "parent_id", None)
    if parent_id is None:
        return recorded
    return reach_as_built(
        job,
        recorded,
        (version.documents or {}).get("contract"),
        type(version)._base_manager.filter(id=parent_id).first(),
        agent_version_id=agent_version_id,
        suite_version_id=suite_version_id,
    )


def reach_as_built(
    job: HostedHarnessJob,
    contract: dict[str, Any],
    authored: Any,
    base: Any,
    *,
    agent_version_id: Any = None,
    suite_version_id: Any = None,
) -> dict[str, Any]:
    """``contract`` (a version's sealed or recorded one), each tool reached as ``base`` runs it where neither this
    version's build nor its edit changed how.

    How a tool is reached is a build's fact: a build can serve a tool the author's contract imports, and a later
    save seals or records the author's entry again. So, tool by tool and matched by name, a tool whose entry here is
    its ``authored`` one (no build amended it) and is ``base``'s authored one (no edit changed it) is reached as
    ``base`` runs it; a tool a build amended, or an edit added or changed, keeps its own entry, and one removed is
    gone. Only ``tool_entrypoints`` are carried."""
    entries = contract.get("tool_entrypoints")
    before = _by_tool((getattr(base, "documents", None) or {}).get("contract"))
    if not isinstance(entries, list) or not before:
        return contract
    own = _by_tool(authored)
    built = _by_tool(
        version_contract(
            job,
            base,
            agent_version_id=agent_version_id,
            suite_version_id=suite_version_id,
        )
    )
    reached = [
        built[tool]
        if isinstance(one, dict)
        and (tool := one.get("tool")) in built
        and _reach(own.get(tool)) == _reach(one) == _reach(before.get(tool))
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


def _reach(entry: Any) -> tuple[Any, ...] | None:
    """How a tool entry reaches its tool, with an empty or absent field read alike: a seal can drop the keys an
    authored entry leaves empty. Whether the door is said not to reach it counts only as yes or no."""
    if not isinstance(entry, dict):
        return None
    return (
        *(entry.get(field) or "" for field in _REACH_FIELDS),
        bool(entry.get("door_unreached")),
    )


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
