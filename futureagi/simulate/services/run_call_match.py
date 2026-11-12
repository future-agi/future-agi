"""``apply_run_call_query``'s search and filters in Python, over cached call rows.

A compare page filters several runs' cached rows without a query per run. Each
rule here, the Scenarios-tab filters included, twins one in
``run_results_v3_queries``; change both. The parity tests
in ``test_run_call_match_parity`` hold them together.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from functools import lru_cache
from typing import Any, TypedDict

from django.db.models import F, TextField
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Cast

from simulate.models import (
    CallExecution,
    HarnessSuiteMember,
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.services.run_results_v3_expressions import _json_text

# Scenarios-tab filters a call row answers: an enum matches a value, a text contains one.
ENUM_FILTERS: dict[str, str] = {
    "persona": "result_persona_name",
    "age": "result_age",
    "attack": "result_attack",
    "task": "result_task",
}
TEXT_FILTERS: dict[str, str] = {
    "scenario": "result_scenario_name",
    "situation": "result_situation",
}
# The filters a compare page applies; the rest need the SQL read model.
MATCH_FILTERS = (
    "goal",
    "goal_not",
    "sub_goal",
    "sub_goal_not",
    "status",
    "status_not",
    *TEXT_FILTERS,
    *(f"{key}{suffix}" for key in ENUM_FILTERS for suffix in ("", "_not")),
)
STATUS_FILTERS = ("status", "status_not")
# A JSON path that does not exist: SQL NULL, unlike JSON ``null``.
_MISSING: Any = object()


class CallFacts(TypedDict):
    """What the filters read about a call beyond its cached row."""

    scenario_key: str | None
    # The searched texts, already upper-cased as Postgres ``UPPER`` does.
    scenario_name: str | None
    ended_reason: str | None
    error_message: str | None
    # The sub-goal filter values this call matches.
    sub_goals: list[str]
    # The call's sub-goal names as the sub-goal facet counts them.
    facet_sub_goals: list[str]
    # ``harness_trial_index``, as the call row shows it.
    trial_index: Any


# glibc maps ß to ẞ; Python expands it to "SS".
_PG_UPPER = {"ß": "ẞ"}


def _upper_char(char: str) -> str:
    if (mapped := _PG_UPPER.get(char)) is not None:
        return mapped
    for cased in (char.upper(), char.title()):
        if len(cased) == 1:
            return cased
    return char


def pg_upper(text: str | None) -> str | None:
    """``text`` upper-cased one letter to one letter, as Postgres ``UPPER`` does.

    Python's ``str.upper`` expands some letters (ß to SS, ﬁ to FI), which would
    let a search match here that ``icontains`` does not.
    """
    if text is None:
        return None
    upper = text.upper()
    # No letter expanded, so each was upper-cased one to one (ß always expands).
    if len(upper) == len(text):
        return upper
    return "".join(_upper_char(char) for char in text)


# Goals repeat across calls and requests; fold each once.
_folded_goal = lru_cache(maxsize=4096)(pg_upper)


def _json(text: str | None) -> Any:
    """A JSON value read as text: ``_MISSING`` for SQL NULL."""
    return _MISSING if text is None else json.loads(text)


def _contained_names(value: Any, *, strings: bool) -> set[str]:
    """The ``v`` for which ``value @> [{"name": v}]`` (and ``[v]`` with ``strings``)."""
    if not isinstance(value, list):
        return set()
    names = set()
    for item in value:
        if strings and isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.add(item["name"])
    return names


def _no_result(value: Any) -> bool:
    """``key__isnull=True | key=[]``: the path is absent, or an empty list."""
    return value is _MISSING or value == []


def _facet_names(value: Any) -> list[str]:
    """``_sub_goal_facet``'s names for one call's sub-goal list."""
    if not isinstance(value, list):
        return []
    names = []
    for item in value:
        name = item.get("name") if isinstance(item, dict) else item
        if name:
            names.append(str(name))
    return names


def _first_present(*values: Any) -> Any:
    """``COALESCE(NULLIF(value, '[]'), ...)`` over JSON values."""
    for value in values:
        if value is not _MISSING and value != []:
            return value
    return _MISSING


def read_call_facts(execution_ids: list[Any]) -> dict[Any, dict[str, CallFacts]]:
    """Each run's call facts by call id, for every call of the runs, in a few reads."""
    # Twin: ``result_scenario_key`` in ``run_calls_queryset``.
    receipt = KeyTransform("hosted_harness_receipt", "call_metadata")
    calls = (
        CallExecution.objects.filter(test_execution_id__in=execution_ids)
        .order_by()
        .annotate(
            fact_key=_json_text("call_metadata", "harness_scenario_key"),
            fact_receipt_key=_json_text(receipt, "scenario_key"),
            fact_member_key=KeyTextTransform("harness_scenario_key", "call_metadata"),
            fact_receipt_sub_goals=Cast(
                KeyTransform("sub_goals", receipt), TextField()
            ),
            fact_metadata_sub_goals=Cast(
                KeyTransform("sub_goals", "call_metadata"), TextField()
            ),
            fact_scenario_name=F("scenario__name"),
            fact_trial=Cast(
                KeyTransform("harness_trial_index", "call_metadata"), TextField()
            ),
        )
        .values_list(
            "id",
            "test_execution_id",
            "row_id",
            "scenario_id",
            "ended_reason",
            "error_message",
            "fact_key",
            "fact_receipt_key",
            "fact_member_key",
            "fact_receipt_sub_goals",
            "fact_metadata_sub_goals",
            "fact_scenario_name",
            "fact_trial",
        )
    )
    in_runs = {"call_execution__test_execution_id__in": execution_ids}
    # Joined in SQL, so soft-deleted rows count, as the filter's joins see them.
    sources = dict(
        HostedHarnessExecution._base_manager.filter(**in_runs).values_list(
            "call_execution_id",
            Cast("source_scenario__sub_goals", TextField()),
        )
    )
    registrations = {
        call_id: (key, sub_goals)
        for call_id, key, sub_goals in HostedHarnessScenario._base_manager.filter(
            **in_runs
        ).values_list(
            "call_execution_id", "scenario_key", Cast("sub_goals", TextField())
        )
    }
    # The newest live pinned job's suite; a run without one reads no members.
    suites: dict[Any, Any] = dict(
        HostedHarnessJob.no_workspace_objects.filter(
            test_execution_id__in=execution_ids, suite_version__isnull=False
        )
        .order_by("created_at")
        .values_list("test_execution_id", "suite_version_id")
    )
    rows = list(calls)
    members: dict[tuple[Any, str], Any] = {}
    if suites:
        keys = {
            key
            for _, run_id, *_, member_key, _, _, _, _ in rows
            if run_id in suites and (key := member_key)
        } | {key for key, _ in registrations.values()}
        members = {
            (suite_id, key): _json(sub_goals) if sub_goals is not None else None
            for suite_id, key, sub_goals in HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id__in=set(suites.values()), scenario_key__in=keys
            ).values_list(
                "suite_version_id",
                "scenario_key",
                Cast(KeyTransform("sub_goals", "content"), TextField()),
            )
        }
    facts: dict[Any, dict[str, CallFacts]] = {run_id: {} for run_id in execution_ids}
    for (
        call_id,
        run_id,
        row_id,
        scenario_id,
        ended_reason,
        error_message,
        key,
        receipt_key,
        member_key,
        receipt_text,
        metadata_text,
        scenario_name,
        trial_text,
    ) in rows:
        receipt_goals = _json(receipt_text)
        metadata_goals = _json(metadata_text)
        registration = registrations.get(call_id)
        source = _json(sources[call_id]) if call_id in sources else _MISSING
        own = _contained_names(receipt_goals, strings=False) | _contained_names(
            metadata_goals, strings=True
        )
        # ``_authored_sub_goal_q``: the trial's source scenario, else (only with no
        # trial) its registration.
        if call_id in sources:
            authored = source
        else:
            authored = _json(registration[1]) if registration else _MISSING
        # A pinned run's member wins over both: by the call's key, else its
        # registration's (``_member_scenario_key``).
        member = _MISSING
        if run_id in suites:
            key_for_member = member_key or (registration[0] if registration else None)
            if (suites[run_id], key_for_member) in members:
                member = members[(suites[run_id], key_for_member)]
                member = [] if member is None else member
        if member is not _MISSING:
            authored = member
        sub_goals = own
        if _no_result(receipt_goals) and _no_result(metadata_goals):
            sub_goals = own | _contained_names(authored, strings=True)
        # ``_result_sub_goals_expression``: the call's own list, else the member
        # (even an empty one), else the source scenario's, else the registration's.
        facet_value = _first_present(receipt_goals, metadata_goals)
        if facet_value is _MISSING:
            facet_value = (
                member
                if member is not _MISSING
                else _first_present(
                    source, _json(registration[1]) if registration else _MISSING
                )
            )
        facts[run_id][str(call_id)] = {
            "scenario_key": key
            or receipt_key
            or (str(row_id) if row_id else None)
            or (str(scenario_id) if scenario_id else None),
            "scenario_name": pg_upper(scenario_name),
            "ended_reason": pg_upper(ended_reason),
            "error_message": pg_upper(error_message),
            "sub_goals": sorted(sub_goals),
            "facet_sub_goals": _facet_names(facet_value),
            "trial_index": None if trial_text is None else json.loads(trial_text),
        }
    return facts


def matches(
    row: dict[str, Any],
    facts: CallFacts,
    search: str,
    filters: dict[str, list[str]],
) -> bool:
    """Whether ``apply_run_call_query`` keeps this call; ``search`` is folded."""
    if search and not any(
        value is not None and search in value
        for value in (
            _folded_goal(row["result_goal"]),
            facts["scenario_name"],
            facts["ended_reason"],
            facts["error_message"],
        )
    ):
        return False
    goal = row["result_goal"]
    if (values := filters.get("goal")) and goal not in values:
        return False
    if (values := filters.get("goal_not")) and goal is not None and goal in values:
        return False
    outcome = row["result_outcome"]
    if (values := filters.get("status")) and outcome not in _lowered(values):
        return False
    if (values := filters.get("status_not")) and outcome in _lowered(values):
        return False
    sub_goals = facts["sub_goals"]
    if (values := filters.get("sub_goal")) and not any(v in sub_goals for v in values):
        return False
    if (values := filters.get("sub_goal_not")) and any(v in sub_goals for v in values):
        return False
    for key, field in ENUM_FILTERS.items():
        if (values := filters.get(key)) and row[field] not in values:
            return False
        if (values := filters.get(f"{key}_not")) and row[field] in values:
            return False
    for key, field in TEXT_FILTERS.items():
        # Folded in ``filter_rows``, as ``icontains`` folds both sides.
        if (values := filters.get(key)) and not any(
            v in _folded_goal(row[field]) for v in values
        ):
            return False
    return True


def _lowered(values: Iterable[str]) -> set[str]:
    return {str(value).lower() for value in values}


def filter_rows(
    rows: list[dict[str, Any]],
    facts: dict[str, CallFacts],
    query: dict[str, Any],
    *,
    drop_status: bool = False,
) -> list[dict[str, Any]]:
    """The rows ``apply_run_call_query`` keeps, in their order.

    ``drop_status`` leaves out the status filters, as the status chips count.
    """
    search = pg_upper(str(query.get("search") or "").strip()) or ""
    filters = {
        key: [pg_upper(str(v)) for v in values] if key in TEXT_FILTERS else values
        for key, values in (query.get("filters") or {}).items()
        if values and not (drop_status and key in STATUS_FILTERS)
    }
    if not search and not filters:
        return rows
    return [row for row in rows if matches(row, facts[row["id"]], search, filters)]


def sub_goal_facet(facts: Iterable[CallFacts]) -> list[dict[str, Any]]:
    """``_sub_goal_facet`` over these calls."""
    counts: Counter[str] = Counter()
    for one in facts:
        counts.update(one["facet_sub_goals"])
    return [
        {"value": value, "count": count}
        for value, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0].lower())
        )
    ]
