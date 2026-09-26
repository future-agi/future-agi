"""What the Debug failures drawer tells a user about one simulation run.

The run's evals decide which goals broke on which calls; Omega explains how.
Every count here comes from those verdicts and cluster sizes, never from a
model, so a card's number always matches the calls its View link opens.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from simulate.models.test_execution import CallExecution
from simulate.services.harness_environment import sub_goal_catalogue
from simulate.services.run_results_v3 import call_outcome

# A finding about our own test caller is our bug, not the user's agent.
_CALLER_FAULT = re.compile(
    r"\b(caller|user) simulator\b|\bsimulat(ed|or) (caller|user)\b|\btest caller\b"
    r"|\b(caller|user) (repeatedly|kept) (stat|say|said|repeat|echo)"
    r"|\bun(tested|triggered)\b",
    re.IGNORECASE,
)
_SUBJECT = re.compile(r"^(the )?(voice )?(assistant|agent)('s)?\s+", re.IGNORECASE)
_RECOVERED = {"recovered", "self_corrected"}


def _phrase(title: str) -> str:
    """A cluster title as the verb phrase a sentence can carry ("prepends a notice…").

    Titles arrive in Title Case; acronyms such as PIN keep their capitals.
    """
    text = _SUBJECT.sub("", str(title or "").strip()).rstrip(".")
    return " ".join(
        word if len(word) > 1 and word.isupper() else word.lower()
        for word in text.split()
    )


def _is_caller_fault(finding) -> bool:
    # The finding's own claim, not its attributions: an agent mistake made while our
    # caller misbehaved (a PIN read back wrong) is still the agent's.
    kind = str(finding.kind or "").replace("_", " ")
    return bool(_CALLER_FAULT.search(f"{kind}. {finding.statement or ''}"))


def build_diagnosis(
    execution,
    findings: list,
    finding_goal: dict,
    clusters: dict,
    unanalyzed: frozenset = frozenset(),
) -> dict[str, Any]:
    """Goals the evals say broke, how they broke, and one-off agent issues."""
    calls = list(
        CallExecution.no_workspace_objects.filter(
            test_execution=execution, deleted=False
        ).order_by("created_at", "id")
    )
    order = {call.id: index for index, call in enumerate(calls)}
    # The run page's own reading of an errored call (the harness verdict, else the
    # call status), so the drawer and the page count the same calls. Eval ids only
    # matter past that point, so none are needed here.
    infra = {call.id for call in calls if call_outcome(call, set()) == "error"}
    verdicts: dict = {}
    for call in calls:
        if call.id in infra:
            continue
        receipt = (call.call_metadata or {}).get("hosted_harness_receipt") or {}
        # Same reading as the harness ingestion: `judged` is only the kind (a judge
        # or a code checkpoint), and a sub-goal nothing decided was not tested.
        verdicts[call.id] = [
            (str(goal["name"]), goal["held"])
            for goal in receipt.get("sub_goals") or []
            if isinstance(goal, dict)
            and goal.get("name")
            and goal.get("held") is not None
        ]
    broken: dict[str, list] = defaultdict(list)
    passed: dict = defaultdict(set)
    tested: dict[str, int] = defaultdict(int)
    for call_id, goals in verdicts.items():
        for name, held in goals:
            tested[name] += 1
            if held is False:
                broken[name].append(call_id)
            else:
                passed[call_id].add(name)
    broken_calls = {call_id for call_ids in broken.values() for call_id in call_ids}

    by_goal: dict[str, list] = defaultdict(list)
    one_offs: list = []
    for finding in findings:
        call_id = finding.report.job.call_execution_id
        # A finding about our own caller is never the agent's; an agent mistake on
        # a call our caller derailed still is.
        if call_id in infra or _is_caller_fault(finding):
            continue
        goal = finding_goal.get(finding.id)
        if goal and call_id in broken.get(goal, []):
            by_goal[goal].append(finding)
        elif goal in passed[call_id]:
            # The eval is the verdict; disagreeing with it is ours to fix, not the user's.
            continue
        elif finding.recovery not in _RECOVERED:
            one_offs.append(finding)

    def ways(members: list) -> list[dict[str, Any]]:
        grouped: dict = {}
        for finding in members:
            cluster = clusters.get(finding.cluster_id)
            key = cluster.id if cluster else finding.id
            title = cluster.title if cluster else ""
            way = grouped.setdefault(
                key,
                {
                    "id": str(key),
                    "title": title or finding.statement,
                    "phrase": _phrase(title)
                    or str(finding.kind or "").replace("_", " "),
                    "call_ids": [],
                },
            )
            call_id = finding.report.job.call_execution_id
            if call_id not in way["call_ids"]:
                way["call_ids"].append(call_id)
        result = sorted(grouped.values(), key=lambda way: -len(way["call_ids"]))
        for way in result:
            way["call_ids"] = [
                str(call_id)
                for call_id in sorted(
                    way["call_ids"], key=lambda call_id: order.get(call_id, len(order))
                )
            ]
        return result

    catalogue = sub_goal_catalogue(execution.run_test_id)
    goals = []
    for name, call_ids in broken.items():
        goal_ways = ways(by_goal.get(name, []))
        explained = {call_id for way in goal_ways for call_id in way["call_ids"]}
        entry = catalogue.get(name, {})
        goals.append(
            {
                "goal": name,
                "label": entry.get("what") or name.replace("_", " ").capitalize(),
                "criteria": entry.get("claim") or None,
                "broken_call_ids": [str(call_id) for call_id in call_ids],
                "tested_call_count": tested[name],
                "ways": goal_ways,
                "unexplained_call_ids": [
                    str(call_id)
                    for call_id in call_ids
                    if str(call_id) not in explained
                ],
            }
        )
    goals.sort(key=lambda goal: -len(goal["broken_call_ids"]))
    one_off_ways = ways(one_offs)

    def in_call_order(call_ids) -> list[str]:
        # A job can outlive its call's soft delete; sort it last, never 500.
        return [
            str(call_id)
            for call_id in sorted(call_ids, key=lambda c: order.get(c, len(order)))
        ]

    return {
        "summary": {
            "measured_call_count": len(verdicts),
            "broken_goal_count": len(goals),
            "broken_call_count": len(broken_calls),
            "one_off_count": len(one_off_ways),
            # Calls that errored before they could be measured; never counted.
            "excluded_call_ids": in_call_order(infra),
            # Calls whose own analysis failed: unread, not issue-free. One we
            # couldn't measure is already excluded.
            "unanalyzed_call_ids": in_call_order(set(unanalyzed) - infra),
        },
        "goals": goals,
        "one_offs": one_off_ways,
    }
