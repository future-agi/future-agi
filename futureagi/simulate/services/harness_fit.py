"""How a connected agent fits a ready environment, before anything is built (catalogue.md,
"Use with my agent").

Each world tool is paired with one of the agent's own tools: the same name is a match, a
tool that does the same job is suggested by a model reading both tools' descriptions and
arguments (or, without a model, by how alike their names read) for the person to review, the
rest are Not available. A scenario can be measured only when every tool its reference solution
calls is paired, as the adaptation's proof later decides (`binding_trajectories.
derive_trajectories` in agent-learning-kit), so a pairing the person changes is counted again at
once. The same model reads the agent's instructions against the world's rules and flags each
conflict with the instruction quoted; a quote the instructions don't hold is dropped.
"""

import json
import os
import re
from functools import lru_cache
from typing import Any

import httpx
import structlog
from django.core.cache import cache

from simulate.models import (
    HarnessSuiteRelease,
    HostedHarnessConversation,
    HostedHarnessJob,
)
from simulate.services.harness_environment import _TERMINAL_FAILED
from simulate.services.harness_releases import (
    ADAPTATION_APPROVAL_PREFIX,
    _chain,
    _download,
    _error,
    _visible,
    adaptation_of,
    archive_files,
    organization_agent_version,
    scenario_documents,
)
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import platform_model_gateway
from simulate.services.provider_profile import (
    CONNECTOR_PROVIDERS,
    profile_digest,
    profile_document,
    profile_for_version,
)

logger = structlog.get_logger(__name__)

# A suggestion or a policy check is kept a day per agent profile and world: neither changes
# unless one of them does, and both digests are in the key.
_CACHE_SECONDS = 24 * 60 * 60
# This call is made inside a request, so it is kept short and made by one request at a time
# (`_one_at_a_time`): a second cold request is answered from names instead of waiting too.
_MODEL_SECONDS = 20
_FLIGHT_SECONDS = 2 * _MODEL_SECONDS + 5

# Verbs that say what a tool does; two tools that read alike share one of these groups.
_VERBS = (
    {"get", "read", "fetch", "show", "detail", "details", "info", "lookup"},
    {"find", "search", "list", "query"},
    {"check", "verify", "validate", "confirm"},
    {"create", "add", "new", "open", "make"},
    {"update", "change", "edit", "set"},
    {"cancel", "void"},
    {"delete", "remove"},
)
_VERB_WORDS = set().union(*_VERBS)
# The least a suggestion must score: one shared noun, or a shared verb and argument.
_SUGGEST_AT = 1.5


def _words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    words = [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]


def _parameters(tool: dict[str, Any]) -> list[str]:
    return list(((tool.get("parameters") or {}).get("properties") or {}).keys())


def _score(agent_tool: dict[str, Any], world_tool: dict[str, Any]) -> float:
    """How alike two tools read: shared nouns, a shared kind of action and shared arguments,
    less the world tool's nouns the agent's name never mentions."""
    agent_words, world_words = (
        set(_words(agent_tool["name"])),
        set(_words(world_tool["name"])),
    )
    agent_nouns, world_nouns = agent_words - _VERB_WORDS, world_words - _VERB_WORDS
    verbs = any(agent_words & group and world_words & group for group in _VERBS)
    arguments = sum(
        any(
            a == w or w.startswith(f"{a}_") or a in _words(w)
            for w in _parameters(world_tool)
        )
        for a in _parameters(agent_tool)
    )
    return (
        2 * len(agent_nouns & world_nouns)
        + (0.5 if verbs else 0)
        + arguments
        - 0.5 * len(world_nouns - agent_nouns)
    )


def suggested_pairing(
    agent_tools: list[dict[str, Any]], world_tools: list[dict[str, Any]]
) -> dict[str, str | None]:
    """World tool → the agent tool it is paired with: the same name first, then the closest
    reading pairs, each tool used once."""
    pairing: dict[str, str | None] = {tool["name"]: None for tool in world_tools}
    free = {tool["name"]: tool for tool in agent_tools}
    for name in pairing:
        if name in free:
            pairing[name] = name
            del free[name]
    candidates = sorted(
        (
            (_score(agent, world), world["name"], agent["name"])
            for world in world_tools
            if pairing[world["name"]] is None
            for agent in free.values()
        ),
        key=lambda item: -item[0],
    )
    for score, world, agent in candidates:
        if score < _SUGGEST_AT:
            break
        if pairing[world] is None and agent in free:
            pairing[world] = agent
            del free[agent]
    return pairing


@lru_cache(maxsize=32)
def _solution_tools(
    object_key: str, suite_json: str
) -> tuple[tuple[str, str, tuple[frozenset, ...]], ...]:
    """(key, name, the tools each of its routes calls) per scenario: the reference solution
    first, then every alternative route the scenario allows. The binding proof scores a
    scenario on any of them, so the fit counts it the same way. A sealed suite never changes
    under its object key."""
    files = archive_files(_download(object_key))
    return tuple(
        (
            document["scenario_key"],
            str(document.get("name") or document["scenario_key"]),
            tuple(
                frozenset(str(step.get("tool")) for step in route or [])
                for route in (
                    document.get("solution") or [],
                    *(document.get("alternatives") or []),
                )
            ),
        )
        for document in scenario_documents(json.loads(suite_json), files.__getitem__)
    )


def agent_of(agent_version) -> dict[str, Any]:
    """The agent as its provider defines it: its own tools (bindable), its platform actions,
    its instructions and the digest of the profile they were read from."""
    definition = agent_version.agent_definition
    connector = str(definition.provider or "").strip().lower()
    provider = CONNECTOR_PROVIDERS.get(connector)
    profile = profile_for_version(agent_version) if provider else None
    if profile is None:
        raise _error(
            "agent_tools_unavailable",
            "this agent's tools can't be read from its provider",
            409,
        )
    modality = "voice" if definition.agent_type == "voice" else "chat"
    document = profile_document(
        provider, str(definition.assistant_id or ""), profile, modality=modality
    )
    tools = document.get("tools") or []
    return {
        "tools": [tool for tool in tools if tool.get("kind") != "builtin"],
        "platform_actions": [tool for tool in tools if tool.get("kind") == "builtin"],
        "instructions": "\n\n".join(
            str(unit.get("instructions") or "")
            for unit in document.get("behaviour") or []
        ).strip(),
        "digest": profile_digest(profile),
    }


def _world_digest(suite) -> str:
    return ":".join(release.digest for release in _chain(suite))


def _ask_model(task: str, material: dict[str, Any]) -> dict[str, Any]:
    """One JSON answer from the model the environment builder uses, through AgentCC."""
    url, key = platform_model_gateway()
    model = (
        os.environ.get("ALK_HARNESS_MODEL")
        or os.environ.get("ALK_HOSTED_AGENTCC_MODEL")
        or "vertex_ai/gemini-3.7-flash"
    )
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": task},
            {"role": "user", "content": json.dumps(material)},
        ],
    }
    # One retry: a dropped connection or a gateway hiccup is common and costs nothing to repeat.
    for attempt in (1, 2):
        try:
            response = httpx.post(
                f"{url}/v1/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key}"},
                timeout=_MODEL_SECONDS,
            )
        except httpx.TransportError:
            if attempt == 2:
                raise
            continue
        if attempt == 2 or not (
            response.status_code == 429 or response.status_code >= 500
        ):
            break
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"]
    return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))


class _Busy(Exception):
    """Another request is already asking the model this question."""


def _one_at_a_time(key: str, task: str, material: dict[str, Any]) -> dict[str, Any]:
    """`_ask_model`, by one request at a time per cache key: concurrent cold requests do not
    each hold a worker on the same call. The others raise `_Busy` and answer without the model;
    the next request after this one finds its answer cached."""
    if not cache.add(f"{key}:asking", 1, _FLIGHT_SECONDS):
        raise _Busy
    try:
        return _ask_model(task, material)
    finally:
        cache.delete(f"{key}:asking")


_PAIRING_TASK = (
    "You pair a customer's agent tools with a test world's tools. For each agent tool, name "
    "the world tool that does the same job (the same action on the same thing), judged by "
    "descriptions and arguments, not only names. Use each world tool at most once and leave an "
    'agent tool out when no world tool does its job. Answer JSON: {"pairs": [{"agent": '
    'agent tool name, "world": world tool name, "reason": one short sentence}]}.'
)
_CONFLICTS_TASK = (
    "You compare a customer's agent instructions with the rules a test world grades it on. List "
    "every rule the instructions contradict, or ignore where the rule needs the agent to act "
    "(for example a different notice period, no identity check, a different hand-off). Quote "
    "the instruction exactly as written; an ignored rule quotes the instruction that should "
    'have covered it. Answer JSON: {"conflicts": [{"rule": rule id, "agent": exact '
    "quote}]}, an empty list when there are none."
)


def _describe(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool["name"],
        "description": tool.get("description") or "",
        "arguments": tool.get("parameters") or {},
    }


def model_pairing(
    agent: dict[str, Any], world_tools: list[dict[str, Any]], world_digest: str
) -> dict[str, str | None]:
    """World tool → agent tool, as the model pairs them, or by names when it can't answer. The
    same names always pair."""
    key = f"harness-fit:pairing:{agent['digest']}:{world_digest}"
    found = cache.get(key)
    if found is None:
        try:
            answer = _one_at_a_time(
                key,
                _PAIRING_TASK,
                {
                    "world_tools": [_describe(tool) for tool in world_tools],
                    "agent_tools": [_describe(tool) for tool in agent["tools"]],
                },
            )
            found = [one for one in answer.get("pairs") or [] if isinstance(one, dict)]
            cache.set(key, found, _CACHE_SECONDS)
        except (
            _Busy,
            HostedHarnessError,
            httpx.HTTPError,
            KeyError,
            ValueError,
            TypeError,
        ):
            logger.warning("harness_fit_pairing_by_names", world=world_digest)
            return suggested_pairing(agent["tools"], world_tools)
    names = {tool["name"] for tool in agent["tools"]}
    pairing: dict[str, str | None] = {
        tool["name"]: tool["name"] if tool["name"] in names else None
        for tool in world_tools
    }
    for one in found:
        world, agent_tool = str(one.get("world") or ""), str(one.get("agent") or "")
        if (
            pairing.get(world, "") is None
            and agent_tool in names
            and agent_tool not in pairing.values()
        ):
            pairing[world] = agent_tool
    return pairing


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def release_conflicts(suite, agent_version) -> dict[str, Any]:
    """Where the agent's instructions conflict with the rules the world grades it on, each with
    the instruction quoted. `checked` is false when the model couldn't answer."""
    agent = agent_of(agent_version)
    rules = {
        rule["id"]: rule
        for release in _chain(suite)
        for rule in (release.policy or {}).get("rules") or []
    }
    # Only the rules on the agent's behaviour can conflict with its instructions; the world
    # enforces its invariants and settings itself. A world that doesn't class its rules
    # offers them all.
    behaviour = {i: r for i, r in rules.items() if r.get("class") == "agent_policy"}
    rules = behaviour or rules
    key = f"harness-fit:conflicts:{agent['digest']}:{_world_digest(suite)}"
    found = cache.get(key)
    if found is None:
        if not agent["instructions"]:
            return {"checked": True, "conflicts": []}
        try:
            answer = _one_at_a_time(
                key,
                _CONFLICTS_TASK,
                {
                    "rules": [
                        {"id": i, "text": r.get("text")} for i, r in rules.items()
                    ],
                    "instructions": agent["instructions"],
                },
            )
            found = [
                one for one in answer.get("conflicts") or [] if isinstance(one, dict)
            ]
            cache.set(key, found, _CACHE_SECONDS)
        except (
            _Busy,
            HostedHarnessError,
            httpx.HTTPError,
            KeyError,
            ValueError,
            TypeError,
        ):
            logger.warning(
                "harness_fit_conflicts_unchecked", world=_world_digest(suite)
            )
            return {"checked": False, "conflicts": []}
    instructions = _normalized(agent["instructions"])
    conflicts, seen = [], set()
    for one in found:
        rule, quote = str(one.get("rule") or ""), str(one.get("agent") or "").strip()
        # A quote the instructions don't hold is the model's, not the agent's: dropped.
        if (
            rule in rules
            and quote
            and _normalized(quote) in instructions
            and rule not in seen
        ):
            seen.add(rule)
            conflicts.append(
                {"rule": rule, "policy": rules[rule].get("text") or "", "agent": quote}
            )
    return {"checked": True, "conflicts": conflicts}


def _world_tools(suite) -> list[dict[str, Any]]:
    return list({t["name"]: t for r in _chain(suite) for t in r.tools}.values())


def resolved_pairing(
    suite,
    agent_version,
    edits: dict[str, str | None] | None = None,
    *,
    agent: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    """The complete pairing, every world tool onto one of the agent's or onto none: the
    suggestion with the person's `edits` applied. This one map is what the fit shows, what the
    adaptation stores and what the binding is held to.

    Every edit is checked: its world tool is the world's, its agent tool is the agent's, and no
    agent tool is given to two world tools (a binding admits each agent tool once). An agent
    tool the person gives to one world tool is taken from the world tool the suggestion had put
    it on."""
    agent = agent or agent_of(agent_version)
    names = {tool["name"] for tool in agent["tools"]}
    paired = model_pairing(agent, _world_tools(suite), _world_digest(suite))
    edits = dict(edits or {})
    problems = [
        f"{world!r} is not one of this world's tools"
        for world in edits
        if world not in paired
    ]
    problems += [
        f"{world}: {agent_tool!r} is not one of the agent's tools"
        for world, agent_tool in edits.items()
        if agent_tool is not None and agent_tool not in names
    ]
    given = [agent_tool for agent_tool in edits.values() if agent_tool]
    problems += [
        f"{agent_tool} is paired with more than one world tool"
        for agent_tool in sorted(set(given))
        if given.count(agent_tool) > 1
    ]
    if problems:
        raise _error("pairing_invalid", "; ".join(problems), 400)
    for world, agent_tool in paired.items():
        if world not in edits and agent_tool in given:
            paired[world] = None
    paired.update({world: edits[world] for world in edits})
    return paired


def _frozen_pairing(
    stored: Any, world_tools: list[dict[str, Any]], names: set[str]
) -> dict[str, str | None] | None:
    """The complete pairing an adaptation stored, when it still names exactly this world's
    tools and the agent's; None for an adaptation stored before pairings were complete."""
    if not isinstance(stored, dict) or set(stored) != {t["name"] for t in world_tools}:
        return None
    if any(value is not None and value not in names for value in stored.values()):
        return None
    return dict(stored)


def release_fit(
    suite,
    agent_version,
    pairing: dict[str, str | None] | None = None,
    *,
    frozen: bool = False,
) -> dict[str, Any]:
    """The pairing table, the cases it can measure and the agent's platform actions. `pairing`
    is the person's edits over the suggestion (`resolved_pairing`), or, with `frozen`, the
    complete map an adaptation stored, shown as it was reviewed and never suggested again."""
    agent = agent_of(agent_version)
    agent_tools, platform_actions = agent["tools"], agent["platform_actions"]
    world_tools = _world_tools(suite)
    names = {tool["name"] for tool in agent_tools}
    paired = (_frozen_pairing(pairing, world_tools, names) if frozen else None) or (
        resolved_pairing(suite, agent_version, pairing, agent=agent)
    )
    reached = {world for world, agent_tool in paired.items() if agent_tool}
    scenarios = _solution_tools(
        suite.object_key, json.dumps(suite.manifest, sort_keys=True)
    )
    return {
        "agent": {
            "name": agent_version.agent_definition.agent_name,
            "version": agent_version.version_name or f"v{agent_version.version_number}",
        },
        "agent_tools": [{"name": t["name"]} for t in agent_tools],
        "rows": [
            {
                "world": tool["name"],
                "agent": paired[tool["name"]],
                "kind": (
                    "missing"
                    if not paired[tool["name"]]
                    else "matched"
                    if paired[tool["name"]] == tool["name"]
                    else "mapped"
                ),
            }
            for tool in world_tools
        ],
        "platform_actions": [
            {"name": t["name"], "description": t.get("description")}
            for t in platform_actions
        ],
        "total": len(scenarios),
        "unmeasured": [
            {"key": key, "title": name}
            for key, name, routes in scenarios
            if not any(route <= reached for route in routes)
        ],
    }


def adaptation_design(job) -> dict[str, Any]:
    """Review the design: what an adaptation takes from the ready world, the pairing the person
    chose and the cases it measures, and where it stands. Nothing is built until it's approved."""
    adaptation = adaptation_of(job) or {}
    organization = job.organization
    suite = _visible(HarnessSuiteRelease, organization, adaptation["suite"]["digest"])
    agent_version = organization_agent_version(
        organization, adaptation["agent_version_id"]
    )
    environment = _chain(suite)[-1]
    catalogue = (environment.manifest.get("metadata") or {}).get("catalogue") or {}
    rules = (environment.policy or {}).get("rules") or []
    result = (job.payload.get("metadata") or {}).get("adaptation_result") or {}
    question = (
        HostedHarnessConversation.no_workspace_objects.filter(job=job)
        .values_list("blocking_input", flat=True)
        .first()
    ) or {}
    asked = str(question.get("prompt") or "").startswith(ADAPTATION_APPROVAL_PREFIX)
    state = (
        "ready"
        if job.state == HostedHarnessJob.State.COMPLETED
        else "failed"
        if job.state in _TERMINAL_FAILED
        else "building"
        if adaptation.get("approved_at")
        else "awaiting_approval"
        if asked
        else "checking"
    )
    return {
        "state": state,
        "failure": job.failure if state == "failed" else None,
        "composition_id": result.get("composition_id"),
        "agent": {
            "name": agent_version.agent_definition.agent_name,
            "version": agent_version.version_name or f"v{agent_version.version_number}",
        },
        "world": {
            "name": environment.name,
            "title": catalogue.get("title") or environment.name,
            "summary": catalogue.get("summary") or "",
            "channel": catalogue.get("channel") or "chat",
            "release": environment.digest,
            "records": catalogue.get("records") or {},
            "tools": [
                {"name": t["name"], "description": t.get("description")}
                for t in environment.tools
            ],
            "rules": [
                {"id": r["id"], "text": r.get("text") or ""}
                for r in rules
                if r.get("class") == "agent_policy"
            ],
            "people": sorted(
                {str(p.get("name")) for p in suite.personas if p.get("name")}
            ),
        },
        "suite": suite.name,
        "scenarios": [
            {"key": key, "title": title}
            for key, title, _routes in _solution_tools(
                suite.object_key, json.dumps(suite.manifest, sort_keys=True)
            )
        ],
        "fit": release_fit(
            suite, agent_version, adaptation.get("pairing"), frozen=True
        ),
        "conflicts": release_conflicts(suite, agent_version),
        "check": question.get("data") if asked else None,
    }
