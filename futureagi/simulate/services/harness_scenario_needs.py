"""What each scenario of a suite version needs from its environment version.

Read from the stored documents only: the suite version's scenarios (their solution
paths, sub-goals and turns) and the environment version's sub-goal catalogue, whose
checks are read with Python's ``ast``. Never estimated from text.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from django.core.cache import cache

from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteVersion,
)

logger = structlog.get_logger(__name__)

NEEDS_VERSION = "1"
TOOL_ROWS = ("tool_discovery", "tool_routing")
# ALK's default when a scenario does not say.
_DEFAULT_MAX_TURNS = 10

GradingGap = Literal["check_unreadable", "no_checks"]


@dataclass(frozen=True)
class CheckReading:
    """What one sub-goal's check reads: the world, and the tools it names."""

    world: bool
    tools: frozenset[str]


@dataclass(frozen=True)
class CaseNeeds:
    """One scenario of a suite version and the rows it needs.

    ``rows`` leaves out what depends on the read (audio for a voice agent, reset for
    more than one case or trial). ``tools`` are the tools on every solution path.
    ``unknown`` marks a need that cannot be read: a sub-goal missing from the
    catalogue, a check that cannot be read, no stored document, or a connect-only
    scenario whose checks name no tool.
    """

    key: str
    number: int
    name: str
    persona: str
    rows: frozenset[str]
    tools: frozenset[str]
    unknown: bool
    recorded: bool
    grading_gap: GradingGap | None


def affected_by_tool(case: CaseNeeds, tool: str) -> bool:
    """Whether a change to ``tool`` affects the case: it needs the tool on every path."""
    return tool in case.tools


def case_needs(
    *,
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
) -> list[CaseNeeds]:
    """The suite version's scenarios in number order, each with what it needs.

    A suite without members numbers its scenarios in document order. Cached per
    version pair with no timeout: both versions are immutable.
    """
    cache_key = (
        f"harness-scenario-needs:{NEEDS_VERSION}:"
        f"{environment_version.id}:{suite_version.id}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    found = _compute(environment_version, suite_version)
    cache.set(cache_key, found, timeout=None)
    return found


def _compute(
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
) -> list[CaseNeeds]:
    from simulate.services.harness_versioning.versions import _provider_agent
    from simulate.services.hosted_harness_gateway import _scenario_token

    documents = [
        one
        for one in (suite_version.documents or {}).get("scenarios") or []
        if isinstance(one, Mapping)
    ]
    catalogue = _catalogue(environment_version)
    # A connected agent's scenarios carry no solution: nothing local can replay it.
    connect_only = _provider_agent(environment_version.job.payload or {})
    members = list(
        HarnessSuiteMember.no_workspace_objects.filter(suite_version=suite_version)
        .order_by("number", "scenario_key")
        .values_list("scenario_key", "number", "content")
    )
    if not members:
        return [
            _needs(
                str(_document_key(document) or index),
                index,
                document,
                document,
                catalogue,
                connect_only,
            )
            for index, document in enumerate(documents, start=1)
        ]
    by_token: dict[str, Mapping[str, Any]] = {}
    for document in documents:
        for field in ("name", "scenario_key", "folder"):
            token = _scenario_token(document.get(field))
            if token:
                by_token.setdefault(token, document)
    found = []
    for key, number, content in members:
        content = content if isinstance(content, Mapping) else {}
        document = by_token.get(_scenario_token(key)) or by_token.get(
            _scenario_token(content.get("name"))
        )
        found.append(_needs(key, number, document, content, catalogue, connect_only))
    unmatched = sum(1 for one in found if not one.recorded)
    if unmatched:
        logger.warning(
            "harness_scenario_needs_unrecorded",
            suite_version_id=str(suite_version.id),
            unmatched=unmatched,
        )
    return found


def _document_key(document: Mapping[str, Any]) -> object:
    return (
        document.get("scenario_key") or document.get("folder") or document.get("name")
    )


def _catalogue(
    environment_version: HarnessEnvironmentVersion,
) -> dict[str, Mapping[str, Any]]:
    return {
        str(goal["name"]): goal
        for goal in (environment_version.documents or {}).get("sub_goals") or []
        if isinstance(goal, Mapping) and goal.get("name")
    }


def _needs(
    key: str,
    number: int,
    document: Mapping[str, Any] | None,
    shown: Mapping[str, Any],
    catalogue: Mapping[str, Mapping[str, Any]],
    connect_only: bool,
) -> CaseNeeds:
    rows = {"start", "message"}
    if document is None:
        return CaseNeeds(
            key=key,
            number=number,
            name=str(shown.get("name") or key),
            persona=_persona(shown.get("persona")),
            rows=frozenset(rows),
            tools=frozenset(),
            unknown=True,
            recorded=False,
            grading_gap="check_unreadable",
        )
    readings, unreadable = _checks(_sub_goal_names(document), catalogue)
    if any(one.world for one in readings):
        rows.add("state")
    if _max_turns(document) > 1:
        rows.add("continuity")
    tools = _path_tools(document)
    unknown = unreadable
    if tools is None and connect_only:
        tools = frozenset(name for one in readings for name in one.tools)
        unknown = unknown or not tools
    tools = tools or frozenset()
    if tools:
        rows.update(TOOL_ROWS)
    gap: GradingGap | None = None
    if unreadable:
        gap = "check_unreadable"
    elif not readings:
        gap = "no_checks"
    return CaseNeeds(
        key=key,
        number=number,
        name=str(document.get("name") or shown.get("name") or key),
        persona=_persona(document.get("persona") or shown.get("persona")),
        rows=frozenset(rows),
        tools=tools,
        unknown=unknown,
        recorded=True,
        grading_gap=gap,
    )


def _persona(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("name") or "")
    return str(value or "")


def _sub_goal_names(document: Mapping[str, Any]) -> list[str]:
    goals = document.get("sub_goals")
    return [
        str(goal.get("name") if isinstance(goal, Mapping) else goal)
        for goal in goals or []
        if goal
    ]


def _max_turns(document: Mapping[str, Any]) -> int:
    try:
        return int(document.get("max_turns", _DEFAULT_MAX_TURNS))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_TURNS


def _path_tools(document: Mapping[str, Any]) -> frozenset[str] | None:
    """The tools on every solution path; ``None`` when every path is blank.

    A blank path among others (a refusal the instructions allow) leaves no tool on
    every path.
    """
    paths = [document.get("solution") or []]
    paths += [one or [] for one in document.get("alternatives") or []]
    named = [frozenset(_step_tools(path)) for path in paths]
    if not any(named):
        return None
    return frozenset.intersection(*named)


def _step_tools(path: Iterable[Any]) -> Iterable[str]:
    for step in path:
        tool = step.get("tool") if isinstance(step, Mapping) else None
        if isinstance(tool, str) and tool:
            yield tool


def _checks(
    names: list[str], catalogue: Mapping[str, Mapping[str, Any]]
) -> tuple[list[CheckReading], bool]:
    """Each named sub-goal's reading, and whether any could not be read.

    A judged sub-goal reads no world and names no tool.
    """
    readings: list[CheckReading] = []
    unreadable = False
    for name in names:
        goal = catalogue.get(name)
        reading = read_check(str(goal.get("check") or "")) if goal else None
        if reading is not None:
            readings.append(reading)
        elif goal is not None and str(goal.get("judged") or "").strip():
            readings.append(CheckReading(world=False, tools=frozenset()))
        else:
            unreadable = True
    return readings, unreadable


def read_check(source: str) -> CheckReading | None:
    """What ``def check(world, calls[, reference])`` in ``source`` reads; ``None``
    when the source does not parse or defines no such function."""
    if not source.strip():
        return None
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    check = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "check"
            and len(node.args.args) >= 2
        ),
        None,
    )
    if check is None:
        return None
    world = check.args.args[0].arg
    return CheckReading(
        world=any(
            isinstance(node, ast.Name) and node.id == world for node in ast.walk(check)
        ),
        tools=frozenset(_compared_names(tree)),
    )


def _compared_names(tree: ast.AST) -> Iterable[str]:
    """The tool names compared with a call's ``name``: ``c.name == "<tool>"``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.Eq):
            continue
        sides = (node.left, node.comparators[0])
        for one, other in (sides, sides[::-1]):
            if (
                isinstance(one, ast.Attribute)
                and one.attr == "name"
                and isinstance(other, ast.Constant)
                and isinstance(other.value, str)
            ):
                yield other.value
