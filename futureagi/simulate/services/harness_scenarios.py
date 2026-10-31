"""Index a run's authored suite in SQL, filter it, and describe its own filters."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import count
from typing import Any
from uuid import UUID

from django.contrib.postgres.expressions import ArraySubquery
from django.db.models import (
    BooleanField,
    Case,
    F,
    Func,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Replace
from django.utils import timezone

from simulate.models.hosted_harness import HostedHarnessJob, HostedHarnessScenario
from simulate.models.test_execution import CallExecution

# Filter panel properties; a dotted `value` reads into that JSON column.
FIELDS: tuple[dict[str, Any], ...] = (
    {"value": "name", "label": "Scenario", "type": "string", "category": "scenario"},
    {"value": "use_case", "label": "Use case", "type": "enum", "category": "scenario"},
    {
        "value": "instruction",
        "label": "Situation",
        "type": "string",
        "category": "scenario",
    },
    {"value": "persona.name", "label": "Name", "type": "enum", "category": "persona"},
    {
        "value": "persona.accent",
        "label": "Accent",
        "type": "enum",
        "category": "persona",
    },
    {
        "value": "persona.languages",
        "label": "Language",
        "type": "enum",
        "category": "persona",
    },
    {
        "value": "persona.age_group",
        "label": "Age",
        "type": "enum",
        "category": "persona",
    },
    {
        "value": "persona.gender",
        "label": "Gender",
        "type": "enum",
        "category": "persona",
    },
    {
        "value": "persona.location",
        "label": "Location",
        "type": "enum",
        "category": "persona",
    },
    {
        "value": "persona.personality",
        "label": "Personality",
        "type": "enum",
        "category": "persona",
    },
    {
        "value": "persona.communication_style",
        "label": "Style",
        "type": "enum",
        "category": "persona",
    },
    {
        "value": "background_noise",
        "label": "Background",
        "type": "enum",
        "category": "conditions",
    },
    {
        "value": "sub_actors",
        "label": "Sub-actors",
        "type": "enum",
        "category": "conditions",
    },
    {
        "value": "coverage.overlay",
        "label": "Attack",
        "type": "enum",
        "category": "coverage",
    },
    {
        "value": "coverage.overlay_intensity",
        "label": "Attack intensity",
        "type": "enum",
        "category": "coverage",
    },
    {"value": "coverage.task", "label": "Task", "type": "enum", "category": "coverage"},
    {"value": "sub_goals", "label": "Sub-goal", "type": "enum", "category": "scenario"},
    {"value": "keywords", "label": "Keywords", "type": "enum", "category": "scenario"},
    {"value": "status", "label": "Status", "type": "enum", "category": "run"},
)

_BY_VALUE = {field["value"]: field for field in FIELDS}

# Columns holding many values for one scenario, where a filter means membership.
_LIST_FIELDS = frozenset({"sub_goals", "persona.languages", "keywords"})

# Which model column a dotted property reads from, and the key path inside it.
_COLUMNS = {"persona": "persona", "coverage": "coverage"}

# Persona name is absent: names are unique per suite, so it would only produce groups of one.
GROUP_BY = {
    "goal": "use_case",
    "sub_goal": "sub_goals",
    "accent": "persona.accent",
    "age": "persona.age_group",
    "attack": "coverage.overlay",
    "task": "coverage.task",
}

GROUPINGS: tuple[dict[str, str], ...] = (
    {"value": "goal", "label": "Use case"},
    {"value": "sub_goal", "label": "Sub-goal"},
    {"value": "accent", "label": "Accent"},
    {"value": "age", "label": "Age"},
    {"value": "attack", "label": "Attack"},
    {"value": "task", "label": "Task"},
    {"value": "", "label": "No grouping"},
)

# Used only when `group_by` is absent; an explicitly empty `group_by` means one flat list.
DEFAULT_GROUP_BY = "goal"

DEFAULT_ROW_AXIS = "task"
DEFAULT_COL_AXIS = "overlay"

SEARCHABLE = ("name", "instruction", "use_case", "branch")

AXES = (
    "task",
    "counterparty",
    "disposition",
    "interface",
    "interaction",
    "overlay",
    "overlay_vector",
    "overlay_intensity",
)

AXIS_LABELS: dict[str, str] = {
    "task": "Task",
    "counterparty": "Who is calling",
    "disposition": "Their situation",
    "interface": "Line and speech",
    "interaction": "How the call goes",
    "overlay": "Attack",
    "overlay_vector": "Attack method",
    "overlay_intensity": "Attack intensity",
}


# The same axes read in the words of a chat where a call's words would be wrong.
CHAT_AXIS_LABELS: dict[str, str] = {
    "counterparty": "Who is asking",
    "interface": "How they write",
    "interaction": "How the chat goes",
}


def axis_label(axis: str, spoken: bool = True) -> str:
    """The reader-facing name for one axis."""
    if not spoken and axis in CHAT_AXIS_LABELS:
        return CHAT_AXIS_LABELS[axis]
    return (
        AXIS_LABELS.get(axis) or str(axis or "").replace("_", " ").strip().capitalize()
    )


# Only levels whose key reads badly; the rest fall back to the key with underscores removed.
LEVEL_LABELS: dict[str, str] = {
    "none": "No attack",
    "minor_vulnerable": "Vulnerable caller",
    "emergency_crisis": "Emergency",
    "privacy_pii": "Personal data",
    "fraud_policy_abuse": "Fraud attempt",
    "prompt_injection": "Injected instruction",
    "social_engineering": "Social engineering",
    "out_of_scope": "Out of scope",
    "destructive": "Destructive request",
    "spoken_caller": "Spoken by the caller",
    "absent": "No attack",
    "quiet_line": "No background noise",
    "non_native": "Non-native speaker",
    "code_switching": "Switches language",
}
# What each background a caller can be heard over sounds like, for the noise column only.
NOISE_LABELS: dict[str, str] = {
    "quiet line": "Off",
    "present": "Background noise",
    "street": "Street",
    "vehicle": "In a car",
    "transit": "Station / transit",
    "airport": "Airport",
    "retail": "Shop / mall",
    "office": "Office",
    "home": "Home",
    "hospital": "Hospital",
    "outdoors": "Outdoors",
    "crowd": "Crowded room",
}
# Only a caller who is heard has an accent or a room behind them.
VOICE_ONLY_FIELDS = frozenset({"persona.accent", "background_noise"})


def level_label(level: str) -> str:
    """The reader-facing name for one level of an axis."""
    said = str(level or "").strip()
    if not said:
        return ""
    return LEVEL_LABELS.get(said) or said.replace("_", " ").strip().capitalize()


# Properties that are not columns on the row.
_ALIASES = {"status": "call_execution__status"}


def authored_scenarios(run_test_id, *, call_execution_id, scenario_key) -> QuerySet:
    """A call's authored scenario, newest first: linked to the call on a direct
    run, or found by the call's scenario key on the run's own job or its parent
    environment. Arguments may be ``OuterRef``s for a per-call subquery."""
    run_jobs = HostedHarnessJob.no_workspace_objects.filter(run_test_id=run_test_id)
    return HostedHarnessScenario.all_objects.filter(
        Q(call_execution_id=call_execution_id)
        | Q(
            Q(job_id__in=run_jobs.values("id"))
            | Q(job_id__in=run_jobs.values("environment_id")),
            scenario_key=scenario_key,
        )
    ).order_by("-created_at")


class _InArray(Func):
    """``expression = ANY(ARRAY(subquery))``, the array read once up front."""

    template = "%(expressions)s)"
    arg_joiner = " = ANY("
    output_field = BooleanField()


def _job_in(test_execution_id: UUID, field: str) -> _InArray:
    # The base manager, as a join would: deleted or other-workspace jobs still count.
    jobs = HostedHarnessJob._base_manager.filter(test_execution_id=test_execution_id)
    return _InArray(F("job_id"), ArraySubquery(jobs.values(field)))


def _flag(condition: Q) -> Case:
    return Case(
        When(condition, then=Value(True)),
        default=Value(False),
        output_field=BooleanField(),
    )


def authored_candidates(
    test_execution_id: UUID, *, call_ids: list[Any] | QuerySet, keys: Iterable[str]
) -> QuerySet[HostedHarnessScenario]:
    """Scenarios that may author a run's calls: linked to one of ``call_ids``, or
    keyed on the run's own job (``own_run``) or its environment (``in_scope``).

    ``call_ids`` is a list, or a ``values("id")`` queryset read once as an array.
    """
    # The run's own job and its environment as arrays: an OR across job
    # joins makes Postgres scan every authored scenario.
    own_run = Q(_job_in(test_execution_id, "id"))
    scope = own_run | Q(_job_in(test_execution_id, "environment_id"))
    # An IN (subquery) under the OR would scan every authored scenario too.
    linked = (
        Q(_InArray(F("call_execution_id"), ArraySubquery(call_ids)))
        if isinstance(call_ids, QuerySet)
        else Q(call_execution_id__in=call_ids)
    )
    return (
        HostedHarnessScenario.all_objects.filter(
            linked | (scope & Q(scenario_key__in=list(keys)))
        )
        .annotate(own_run=_flag(own_run), in_scope=_flag(scope))
        .order_by()
    )


class AuthoredPick:
    """A call's authored scenario among the candidates: linked to the call, else
    keyed on the run's own job, then on its environment; the newest wins within
    a rank. Keyed rows count only from ``jobs``.
    """

    def __init__(self, rows: Iterable[Any], own_jobs: set[Any], jobs: set[Any]):
        self.own_jobs = own_jobs
        self.by_call: dict[Any, list[Any]] = {}
        self.by_key: dict[str, list[Any]] = {}
        for row in rows:
            if row.call_execution_id is not None:
                self.by_call.setdefault(row.call_execution_id, []).append(row)
            if row.job_id in jobs:
                self.by_key.setdefault(row.scenario_key, []).append(row)

    def _keyed_rank(self, row: Any, call_id: Any) -> int:
        if row.call_execution_id == call_id:
            return 0
        return 1 if row.job_id in self.own_jobs else 2

    def pick(self, call_id: Any, scenario_key: str | None) -> Any:
        candidates = [(0, row) for row in self.by_call.get(call_id, ())]
        if scenario_key is not None:
            candidates += [
                (self._keyed_rank(row, call_id), row)
                for row in self.by_key.get(scenario_key, ())
            ]
        best = None
        best_rank: tuple[int, float] | None = None
        for rank, row in candidates:
            order = (rank, -row.created_at.timestamp())
            if best_rank is None or order < best_rank:
                best, best_rank = row, order
        return best


def call_row_metadata(call: CallExecution) -> Any:
    """A call's metadata as a page loaded it: the read keys, else the whole field."""
    try:
        # page_calls keeps call_metadata deferred and loads this subset instead.
        return call.row_metadata
    except AttributeError:
        return call.call_metadata


def authored_scenarios_for_calls(
    run_test_id: UUID,
    calls: list[CallExecution],
    *,
    test_execution_id: UUID | None = None,
) -> dict[UUID, HostedHarnessScenario | None]:
    """Resolve linked scenarios first, optionally restricted to one execution."""
    source_keys = {}
    for call in calls:
        metadata = call_row_metadata(call)
        metadata = metadata if isinstance(metadata, dict) else {}
        key = metadata.get("harness_scenario_key")
        source_keys[call.id] = key if isinstance(key, str) and key.strip() else None
    call_ids = [call.id for call in calls]
    keys = set(source_keys.values()) - {None}
    if test_execution_id is not None:
        # The run's pinned suite rides along as one uncorrelated subquery (evaluated
        # once), so an unpinned run stays one query.
        pinned_suite = pinned_suite_subquery(test_execution_id)
        candidates = list(
            authored_candidates(
                test_execution_id, call_ids=call_ids, keys=keys
            ).annotate(pinned_suite_id=Subquery(pinned_suite))
        )
        authored = AuthoredPick(
            candidates,
            {row.job_id for row in candidates if row.own_run},
            {row.job_id for row in candidates if row.in_scope},
        )
        resolved = {
            call.id: authored.pick(call.id, source_keys[call.id]) for call in calls
        }
        suite_id = next(
            (row.pinned_suite_id for row in candidates if row.pinned_suite_id), None
        )
        suites = (
            {call.test_execution_id: suite_id for call in calls} if suite_id else {}
        )
        return _as_pinned(calls, resolved, suites, source_keys)
    run_jobs = HostedHarnessJob.no_workspace_objects.filter(run_test_id=run_test_id)
    scope = Q(job_id__in=run_jobs.values("id")) | Q(
        job_id__in=run_jobs.values("environment_id")
    )
    rows = HostedHarnessScenario.all_objects.filter(
        Q(call_execution_id__in=call_ids) | (scope & Q(scenario_key__in=keys))
    ).order_by("-created_at")
    linked, by_key = {}, {}
    for row in rows:
        if row.call_execution_id:
            linked.setdefault(row.call_execution_id, row)
        by_key.setdefault(row.scenario_key, row)
    resolved = {
        call.id: linked.get(call.id) or by_key.get(source_keys[call.id])
        for call in calls
    }
    # One indexed read of the runs' pins; nothing more when none is pinned.
    return _as_pinned(calls, resolved, _pinned_suites(calls), source_keys)


def pinned_suite_subquery(test_execution_id: Any) -> Subquery:
    """The suite version a test execution's run job was pinned to, as one subquery."""
    return Subquery(
        HostedHarnessJob.no_workspace_objects.filter(
            test_execution_id=test_execution_id, suite_version__isnull=False
        )
        .order_by("-created_at")
        .values("suite_version_id")[:1]
    )


def _pinned_suites(calls: list[CallExecution]) -> dict[UUID, UUID]:
    """The suite version each call's run was pinned to, by test execution."""
    execution_ids = {call.test_execution_id for call in calls if call.test_execution_id}
    if not execution_ids:
        return {}
    return dict(
        HostedHarnessJob.no_workspace_objects.filter(
            test_execution_id__in=execution_ids, suite_version__isnull=False
        )
        .order_by("created_at")
        .values_list("test_execution_id", "suite_version_id")
    )


def member_key(call_key: str | None, row: HostedHarnessScenario | None) -> str | None:
    """The member a call's scenario is read from: its own key, else the picked row's.

    The SQL read model and the scan use the same rule.
    """
    if call_key:
        return call_key
    return row.scenario_key if row is not None else None


def as_member(row: HostedHarnessScenario, content: Any) -> HostedHarnessScenario:
    """The identity row as a pinned suite member held it.

    The member is authoritative: a field it lacks shows empty, never the value a newer
    suite wrote to the identity row. Values are normalised as ``index_scenarios`` stores
    them, so callers see the same types either way.
    """
    if not isinstance(content, dict):
        return row
    shown = copy.copy(row)
    for name, value in scenario_fields(content, row.scenario_key).items():
        setattr(shown, name, value)
    return shown


def _as_pinned(
    calls: list[CallExecution],
    resolved: dict[UUID, HostedHarnessScenario | None],
    suites: dict[UUID, UUID],
    source_keys: dict[UUID, str | None],
) -> dict[UUID, HostedHarnessScenario | None]:
    """Scenarios as the suite version each call's run was pinned to held them.

    A run on an older suite keeps showing that suite's content after the identity row
    moved on; runs without a pinned suite keep the identity row.
    """
    from simulate.models import HarnessSuiteMember

    if not suites:
        return resolved
    keys = {
        key
        for call in calls
        if (key := member_key(source_keys.get(call.id), resolved.get(call.id)))
    }
    contents = {
        (suite_id, key): content
        for suite_id, key, content in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id__in=set(suites.values()), scenario_key__in=keys
        ).values_list("suite_version_id", "scenario_key", "content")
    }
    pinned: dict[UUID, HostedHarnessScenario | None] = {}
    for call in calls:
        row = resolved.get(call.id)
        suite_id = suites.get(call.test_execution_id)
        key = member_key(source_keys.get(call.id), row)
        if row is None or suite_id is None or key is None:
            pinned[call.id] = row
            continue
        pinned[call.id] = as_member(row, contents.get((suite_id, key)))
    return pinned


def pinned_scenarios(
    calls: list[CallExecution],
    resolved: dict[UUID, HostedHarnessScenario | None],
    source_keys: dict[UUID, str | None],
) -> dict[UUID, HostedHarnessScenario | None]:
    """``resolved`` as the suite version each call's run was pinned to held it."""
    return _as_pinned(calls, resolved, _pinned_suites(calls), source_keys)


def _orm_path(field: str) -> str:
    """The ORM lookup for a filter property, dotted paths reaching into the JSON document."""
    head, _, tail = field.partition(".")
    if tail and head in _COLUMNS:
        return f"{_COLUMNS[head]}__{tail}"
    return _ALIASES.get(head, head)


# The filter's value for a scenario that includes no sub-actor.
NO_SUB_ACTOR = "none"
SIMULATED_USER_ONLY = "Simulated user only"


def sub_actor_entries(raw: Any) -> list[dict[str, Any]] | None:
    """A scenario's sub-actors as `{id, instruction, trigger}`, the fields a run reads."""
    entries = [
        {
            "id": str(one["id"]),
            "instruction": str(one.get("instruction") or ""),
            "trigger": one.get("trigger") or {},
        }
        for one in raw or []
        if isinstance(one, dict) and one.get("id")
    ]
    return entries or None


def sub_actor_choice_labels(listing: Any) -> dict[str, str]:
    """How the filter names each sub-actor, and scenarios with none of them."""
    labels = {NO_SUB_ACTOR: SIMULATED_USER_ONLY}
    for one in (listing.get("sub_actors") if isinstance(listing, dict) else None) or []:
        if isinstance(one, dict) and one.get("id"):
            labels[str(one["id"])] = str(one.get("name") or one["id"])
    return labels


def _sub_actor_match(value: str) -> Q:
    if value == NO_SUB_ACTOR:
        return Q(sub_actors__isnull=True)
    return Q(sub_actors__contains=[{"id": value}])


def scenario_fields(doc: dict[str, Any], key: str) -> dict[str, Any]:
    """A scenario document's values as an identity row stores them."""
    noise = doc.get("background_noise")
    persona = doc.get("persona")
    return {
        "name": str(doc.get("name") or key)[:255],
        "instruction": str(doc.get("instruction") or ""),
        "use_case": str(doc.get("use_case") or "")[:255],
        "branch": str(doc.get("branch") or ""),
        "tests": str(doc.get("tests") or ""),
        "folder": str(doc.get("folder") or "")[:512],
        "persona": persona or None,
        "coverage": doc.get("coverage") or None,
        "sub_goals": doc.get("sub_goals") or None,
        "sub_actors": sub_actor_entries(doc.get("sub_actors")),
        # Older suites carry keywords on the persona.
        "keywords": (
            doc.get("keywords")
            or (persona.get("keywords") if isinstance(persona, dict) else None)
            or None
        ),
        "background_noise": (
            noise if isinstance(noise, str) else "present" if noise else "quiet line"
        )[:64],
        "max_turns": doc.get("max_turns") or None,
    }


def _rows_held(
    rows: list[HostedHarnessScenario], docs: list[dict[str, Any]]
) -> list[HostedHarnessScenario | None]:
    """The existing row each scenario writes, or ``None`` for a new one.

    A key names one scenario, so a scenario takes the row holding its key. When several
    repeat a key, the one under that row's name keeps it and the others take the rows
    they were given before (``<key>-2`` and on). Only an older scenario without a key is
    matched by name, and never to a row already taken.
    """
    from simulate.services.hosted_harness_gateway import _scenario_token

    by_key: dict[str, HostedHarnessScenario] = {}
    by_key_token: dict[str, HostedHarnessScenario] = {}
    by_token: dict[str, HostedHarnessScenario] = {}
    for row in sorted(rows, key=lambda row: row.deleted):
        if row.scenario_key:
            by_key.setdefault(row.scenario_key, row)
            by_key_token.setdefault(_scenario_token(row.scenario_key), row)
        for token in (_scenario_token(row.scenario_key), _scenario_token(row.name)):
            if token:
                by_token.setdefault(token, row)

    def key_of(doc: dict[str, Any]) -> str:
        return str(doc.get("scenario_key") or "").strip()

    held_rows: list[HostedHarnessScenario | None] = [None] * len(docs)
    used: set[Any] = set()

    def take(index: int, row: HostedHarnessScenario | None) -> None:
        if row is not None and row.id not in used:
            held_rows[index] = row
            used.add(row.id)

    # The row's own name first, so the order of a repeated key never swaps two scenarios.
    for index in sorted(
        range(len(docs)),
        key=lambda index: (
            getattr(by_key.get(key_of(docs[index])), "name", None)
            != docs[index].get("name")
        ),
    ):
        take(index, by_key.get(key_of(docs[index])))
    owner = {
        key_of(doc): doc.get("name")
        for doc, row in zip(docs, held_rows)
        if row is not None
    }
    for index, doc in enumerate(docs):
        key = key_of(doc)
        # The same scenario listed twice writes its one row (``index_scenarios``).
        if (
            held_rows[index] is not None
            or key not in by_key
            or owner.get(key) == doc.get("name")
        ):
            continue
        repeats = sorted(
            (
                row
                for given, row in by_key.items()
                if given.startswith(f"{key}-") and given[len(key) + 1 :].isdigit()
            ),
            key=lambda row: (
                row.name != doc.get("name"),
                int(row.scenario_key.rsplit("-", 1)[1]),
            ),
        )
        for row in repeats:
            if row.id not in used:
                take(index, row)
                break
    for index, doc in enumerate(docs):
        if held_rows[index] is None:
            # Older outputs spelled keys and folders differently.
            take(
                index,
                by_key_token.get(_scenario_token(key_of(doc)))
                if key_of(doc)
                else by_token.get(_scenario_token(doc.get("name"))),
            )
    return held_rows


def index_scenarios(
    job: HostedHarnessJob,
    docs: list[dict[str, Any]],
    prune: bool = False,
    exact: bool | None = None,
) -> int:
    """Persist the authored suite so it can be queried, and return how many rows it holds."""
    return len(pair_scenarios(job, docs, prune=prune, exact=exact))


@dataclass(frozen=True)
class PlannedPair:
    """Which identity row a document pairs with (``None``: a new one), its key and number."""

    row: HostedHarnessScenario | None
    key: str
    number: int
    doc: dict[str, Any]


def plan_pairs(
    job: HostedHarnessJob,
    docs: list[dict[str, Any]],
    *,
    exact: bool | None = None,
) -> list[PlannedPair]:
    """The identity row, key and number each document is written to; writes nothing.

    With ``exact`` (the default for an environment head that has versions) a document
    that carries a scenario key matches the row the default suite links to the document
    with that key (a backfill may have paired a scenario with a differently keyed row),
    else the row with that key, compared as normalised tokens. A row the default suite
    links to one document is never given to a document with another key; one whose key
    such a row holds gets a row of its own under a derived key. A document without a key
    falls back to name matching, as legacy suites without keys need. Otherwise rows are
    matched as ``_rows_held`` matches them.
    """
    if not docs:
        return []
    from simulate.services.hosted_harness_gateway import _scenario_token

    if exact is None:
        exact = (
            getattr(job, "environment_id", None) is None
            and getattr(job, "environment_version_id", None) is not None
        )
    # Dropped rows are hidden, not deleted: a run's history still points at them.
    rows = list(HostedHarnessScenario.all_objects.filter(job=job))
    existing = {row.scenario_key: row for row in rows}
    by_token = {
        token: row
        for row in sorted(rows, key=lambda row: not row.deleted)
        for token in (_scenario_token(row.scenario_key), _scenario_token(row.name))
        if token
    }
    by_key_token: dict[str, HostedHarnessScenario] = {}
    for row in sorted(rows, key=lambda row: not row.deleted):
        if token := _scenario_token(row.scenario_key):
            by_key_token[token] = row
    paired = _default_member_rows(job, rows) if exact else {}
    owned = {row.id for row in paired.values()}
    key_tokens = set(by_key_token)
    used: set[Any] = set()
    # Numbers are stable across re-indexing; only a new row gets the next free number.
    taken = max(
        (row.number for row in existing.values() if row.number is not None), default=0
    )
    held_rows = [None] * len(docs) if exact else _rows_held(rows, docs)
    keys = {row.scenario_key for row in rows}
    # Every key given out, by a row or by this suite, so a repeat gets one nobody holds.
    given = keys | {str(doc.get("scenario_key") or "").strip() for doc in docs}
    # Each key planned so far, with the name it was planned under and its number.
    holder: dict[str, tuple[str, int]] = {}
    seen: set[str] = set()
    plan: list[PlannedPair] = []
    for position, (doc, held) in enumerate(zip(docs, held_rows, strict=True), start=1):
        doc_key = str(doc.get("scenario_key") or "").strip()
        if exact and doc_key:
            # The default suite's pairing outranks a dropped row that shares the key.
            held = (
                paired.get(_scenario_token(doc_key))
                or existing.get(doc_key)
                or by_key_token.get(_scenario_token(doc_key))
            )
        elif exact:
            held = by_token.get(_scenario_token(doc.get("name")))
        if (
            exact
            and held is not None
            and (
                held.id in used
                or (
                    doc_key
                    and held.id in owned
                    and paired.get(_scenario_token(doc_key)) is not held
                )
            )
        ):
            held = None
        if held is not None:
            key = held.scenario_key
        else:
            key = str(doc.get("scenario_key") or doc.get("name") or "").strip()
            if exact and key:
                key = _free_key(key, key_tokens)
        if not key:
            continue
        key_tokens.add(_scenario_token(key))
        name = str(doc.get("name") or key)[:255]
        repeat = None
        if not exact and held is None:
            if key in holder and holder[key][0] == name:
                # The same scenario listed twice: both write the one row.
                repeat = holder[key][1]
            elif key in keys or key in seen:
                # Another scenario in this suite already holds this key: this one gets
                # its own, so neither is lost and the key stays unique.
                key = next(
                    f"{key}-{n}" for n in count(2) if f"{key}-{n}" not in given | seen
                )
        seen.add(key)
        if repeat is not None:
            number = repeat
        elif held is not None and held.number is not None:
            number = held.number
        elif not existing:
            number = position
        else:
            taken += 1
            number = taken
        if held is not None:
            used.add(held.id)
        holder.setdefault(key, (name, number))
        plan.append(PlannedPair(row=held, key=key, number=number, doc=doc))
    return plan


def pair_scenarios(
    job: HostedHarnessJob,
    docs: list[dict[str, Any]],
    prune: bool = False,
    exact: bool | None = None,
) -> list[tuple[HostedHarnessScenario, dict[str, Any]]]:
    """Persist the authored suite and return the identity row each document was written to.

    Each document goes where ``plan_pairs`` places it.
    """
    pairs: list[tuple[HostedHarnessScenario, dict[str, Any]]] = []
    seen: set[str] = set()
    # A scenario listed twice is planned twice under one key: both write one row.
    written: dict[str, HostedHarnessScenario] = {}
    for planned in plan_pairs(job, docs, exact=exact):
        seen.add(planned.key)
        fields = {
            "number": planned.number,
            **scenario_fields(planned.doc, planned.key),
        }
        row = planned.row or written.get(planned.key)
        if row is None:
            row = HostedHarnessScenario.no_workspace_objects.create(
                job=job, scenario_key=planned.key, **fields
            )
        else:
            fields.update(deleted=False, deleted_at=None)
            for field, value in fields.items():
                setattr(row, field, value)
            row.save(update_fields=[*fields, "updated_at"])
        written[planned.key] = row
        pairs.append((row, planned.doc))
    if not docs:
        return pairs
    # Only an amend prunes: a short suite on a poll may still be mid-write. The row is
    # soft-deleted even when history points at it, so old Runs retain their foreign key
    # while active-suite reads and new Run selection no longer expose it.
    if prune:
        HostedHarnessScenario.no_workspace_objects.filter(job=job).exclude(
            scenario_key__in=seen
        ).update(deleted=True, deleted_at=timezone.now())
    return pairs


def _free_key(key: str, taken: set[str]) -> str:
    """``key``, or the first ``key-N`` whose token no row of the environment holds."""
    from simulate.services.hosted_harness_gateway import _scenario_token

    candidate, suffix = key, 1
    while _scenario_token(candidate) in taken:
        suffix += 1
        candidate = f"{key[:240]}-{suffix}"
    return candidate


def _default_member_rows(
    job: HostedHarnessJob, rows: list[HostedHarnessScenario]
) -> dict[str, HostedHarnessScenario]:
    """Identity rows the default suite links, by the key token of that suite's own document.

    A backfill may have paired a document with a row whose key differs; keying by the
    document's key keeps that pair across a rename and never hands a new key a dropped row.
    A member carries its row's key, not its document's, so key and name together pair
    first, then names (a member's name is its document's), then keys; each row once.
    """
    from simulate.models import HarnessSuiteMember, HarnessSuiteVersion
    from simulate.services.hosted_harness_gateway import _scenario_token

    suite_id = getattr(job, "suite_version_id", None)
    if suite_id is None:
        return {}
    documents = (
        HarnessSuiteVersion._base_manager.filter(id=suite_id)
        .values_list("documents", flat=True)
        .first()
    )
    scenarios = (
        (documents or {}).get("scenarios") if isinstance(documents, dict) else None
    )
    if not isinstance(scenarios, list):
        return {}
    by_id = {row.id: row for row in rows}
    members = [
        (
            by_id[scenario_id],
            _scenario_token(key),
            _scenario_token(content.get("name") if isinstance(content, dict) else None),
        )
        for key, scenario_id, content in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=suite_id
        )
        .order_by("number", "scenario_key")
        .values_list("scenario_key", "scenario_id", "content")
        if scenario_id in by_id
    ]
    linked = [
        (key_token, _scenario_token(document.get("name")))
        for document in scenarios
        if isinstance(document, dict)
        and (key_token := _scenario_token(document.get("scenario_key")))
    ]
    paired: dict[str, HostedHarnessScenario] = {}
    used: set[Any] = set()
    for match in (
        lambda key, name: (key, name) if key and name else None,
        lambda key, name: name or None,
        lambda key, name: key or None,
    ):
        candidates: dict[Any, list[HostedHarnessScenario]] = {}
        for row, key, name in members:
            if row.id not in used and (value := match(key, name)) is not None:
                candidates.setdefault(value, []).append(row)
        for key_token, name_token in linked:
            if key_token in paired:
                continue
            value = match(key_token, name_token)
            for row in candidates.get(value, []) if value is not None else []:
                if row.id not in used:
                    used.add(row.id)
                    paired[key_token] = row
                    break
    return paired


class ScenarioRows(list):
    """Draft scenario rows, read the way the list, coverage and catalogue read rows."""

    def values(self, *paths: str) -> list[dict[str, Any]]:
        return [{path: _row_value(row, path) for path in paths} for row in self]

    def values_list(self, path: str, flat: bool = False) -> list[Any]:
        return [
            _row_value(row, path) if flat else (_row_value(row, path),) for row in self
        ]

    def count(self) -> int:  # type: ignore[override]
        return len(self)

    def exists(self) -> bool:
        return bool(self)


def _row_value(row: HostedHarnessScenario, orm_path: str) -> Any:
    """A row's value at an ORM path: a column, a JSON column's key or a relation's field."""
    head, _, tail = orm_path.partition("__")
    value = getattr(row, head, None)
    if not tail:
        return value
    if head in _COLUMNS.values():
        return value.get(tail) if isinstance(value, dict) else None
    return getattr(value, tail, None)


def _filter_terms(params) -> Iterator[tuple[str, str, list[str], bool]]:
    """The filter panel's query params as (field, ORM path, values, negated)."""
    for key in params.keys():
        negated = key.endswith("_not")
        field = key[:-4] if negated else key
        if field not in _BY_VALUE:
            continue
        values = [one for one in params.getlist(key) if one not in (None, "")]
        if not values:
            continue
        yield field, _orm_path(field), values, negated


def _filtered_rows(rows: ScenarioRows, params) -> ScenarioRows:
    for field, path, values, negated in _filter_terms(params):

        def matches(
            row: HostedHarnessScenario, path=path, field=field, values=values
        ) -> bool:
            if field == "sub_actors":
                ids = {str(one.get("id")) for one in row.sub_actors or []}
                return any(
                    not ids if one == NO_SUB_ACTOR else one in ids for one in values
                )
            held = _row_value(row, path)
            if field in _LIST_FIELDS:
                return isinstance(held, list) and any(one in held for one in values)
            return held in values

        rows = ScenarioRows(row for row in rows if matches(row) != negated)
    return rows


def apply_filters(queryset: QuerySet, params) -> QuerySet:
    """Narrow the suite by the filter panel's query params: repeated keys OR, a `_not` suffix negates."""
    if isinstance(queryset, ScenarioRows):
        return _filtered_rows(queryset, params)
    for field, path, values, negated in _filter_terms(params):
        if field == "sub_actors":
            matches = Q()
            for value in values:
                matches |= _sub_actor_match(value)
            queryset = (
                queryset.exclude(matches) if negated else queryset.filter(matches)
            )
            continue
        lookup = (
            f"{path}__contains"
            if _BY_VALUE[field]["value"] in _LIST_FIELDS
            else f"{path}__in"
        )
        matches = Q()
        for value in values:
            matches |= Q(
                **{lookup: [value] if lookup.endswith("contains") else [value]}
            )
        queryset = queryset.exclude(matches) if negated else queryset.filter(matches)
    return queryset


def apply_search(queryset: QuerySet, term: str) -> QuerySet:
    """Every word must match somewhere; names are also matched with underscores as spaces."""
    words = (term or "").split()
    if not words:
        return queryset
    if isinstance(queryset, ScenarioRows):
        return ScenarioRows(
            row
            for row in queryset
            if all(
                any(
                    word.lower() in str(value or "").lower()
                    for value in (
                        str(row.name or "").replace("_", " "),
                        str(row.scenario_key or "").replace("_", " "),
                        *(getattr(row, field) for field in SEARCHABLE),
                    )
                )
                for word in words
            )
        )
    queryset = queryset.annotate(
        readable_name=Replace("name", Value("_"), Value(" ")),
        readable_folder=Replace("scenario_key", Value("_"), Value(" ")),
    )
    matches = Q()
    for word in words:
        anywhere = Q(readable_name__icontains=word) | Q(readable_folder__icontains=word)
        for field in SEARCHABLE:
            anywhere |= Q(**{f"{field}__icontains": word})
        matches &= anywhere
    return queryset.filter(matches)


def field_catalogue(
    queryset: QuerySet,
    spoken: bool = True,
    sub_actor_labels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The panel's `fields`, with each enum's values and counts over the whole filtered suite."""
    enums = [field for field in FIELDS if field["type"] == "enum"]
    paths = {field["value"]: _orm_path(field["value"]) for field in enums}
    tally: dict[str, dict[str, int]] = {field["value"]: {} for field in enums}
    for row in queryset.values(*set(paths.values())):
        for value, path in paths.items():
            held = row.get(path)
            if value == "sub_actors":
                held = [one.get("id") for one in held or []] or [NO_SUB_ACTOR]
            for one in held if isinstance(held, list) else [held]:
                text = str(one or "").strip()
                if text:
                    counts = tally[value]
                    counts[text] = counts.get(text, 0) + 1

    # A suite where no scenario includes a sub-actor has nothing to filter on.
    if set(tally["sub_actors"]) <= {NO_SUB_ACTOR}:
        tally["sub_actors"] = {}
    catalogue: list[dict[str, Any]] = []
    for field in FIELDS:
        if not spoken and field["value"] in VOICE_ONLY_FIELDS:
            continue
        entry = dict(field)
        if field["type"] != "enum":
            catalogue.append(entry)
            continue
        counts = tally[field["value"]]
        if not counts:
            continue
        entry["choices"] = [
            name
            for name, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ]
        entry["counts"] = counts
        if field["value"] == "sub_actors":
            entry["choice_labels"] = sub_actor_labels or sub_actor_choice_labels(None)
        catalogue.append(entry)
    return catalogue


def coverage_grid(
    queryset: QuerySet, row_axis: str, col_axis: str, spoken: bool = True
) -> dict[str, Any]:
    """One cross-tab of the suite. Empty cells are the point, so they are returned as zero."""
    rows: dict[str, int] = {}
    cols: dict[str, int] = {}
    cells: dict[str, int] = {}
    levels: dict[str, dict[str, int]] = {axis: {} for axis in AXES}
    for coverage in queryset.values_list("coverage", flat=True):
        held = coverage or {}
        for axis in AXES:
            level = str(held.get(axis) or "").strip()
            if level:
                levels[axis][level] = levels[axis].get(level, 0) + 1
        down = str(held.get(row_axis) or "").strip()
        across = str(held.get(col_axis) or "").strip()
        if down:
            rows[down] = rows.get(down, 0) + 1
        if across:
            cols[across] = cols.get(across, 0) + 1
        if down and across:
            key = f"{down}␟{across}"
            cells[key] = cells.get(key, 0) + 1
    return {
        "per_axis": [
            {
                "axis": axis,
                "label": axis_label(axis, spoken),
                "levels": len(held),
                "scenarios": sum(held.values()),
                "counts": dict(
                    sorted(held.items(), key=lambda pair: (-pair[1], pair[0]))
                ),
            }
            for axis, held in levels.items()
            if held
        ],
        "row_axis": row_axis,
        "row_axis_label": axis_label(row_axis, spoken),
        "col_axis": col_axis,
        "col_axis_label": axis_label(col_axis, spoken),
        "rows": sorted(rows),
        "columns": sorted(cols),
        "cells": [
            {
                "row": down,
                "column": across,
                "count": cells.get(f"{down}␟{across}", 0),
            }
            for down in sorted(rows)
            for across in sorted(cols)
        ],
        "axes": list(AXES),
        "axis_labels": {axis: axis_label(axis, spoken) for axis in AXES},
        "level_labels": {
            level: level_label(level) for axis in levels for level in levels[axis]
        },
    }


def level_labels_for(
    rows: list[dict[str, Any]], fields: list[dict[str, Any]] | None = None
) -> dict[str, str]:
    """The reader-facing name for every coverage level, sub-goal and noise bed a page can show."""
    levels: set[str] = set()
    beds: set[str] = set()
    for field in fields or []:
        if field.get("value") == "background_noise":
            beds.update(str(one) for one in field.get("choices") or [])
        elif (
            str(field.get("value") or "").startswith("coverage.")
            or field.get("value") == "sub_goals"
        ):
            levels.update(str(one) for one in field.get("choices") or [])
    for row in rows or []:
        levels.update(
            str(one) for one in row.get("sub_goals") or [] if str(one).strip()
        )
        for value in (row.get("coverage") or {}).values():
            said = str(value or "").strip()
            if said:
                levels.add(said)
        bed = row.get("background_noise")
        if isinstance(bed, str) and bed.strip():
            beds.add(bed.strip())
    labels = {bed: NOISE_LABELS.get(bed) or level_label(bed) for bed in sorted(beds)}
    labels.update({level: level_label(level) for level in sorted(levels)})
    return labels


def scenario_row(row: HostedHarnessScenario) -> dict[str, Any]:
    """One list row. Safe on a scenario indexed before any call was made."""
    persona = row.persona or {}
    return {
        "id": str(row.id),
        "scenario_id": str(row.scenario_id) if row.scenario_id else None,
        "scenario_key": row.scenario_key,
        "number": row.number,
        "name": row.name or row.scenario_key,
        "use_case": row.use_case,
        "instruction": row.instruction,
        "branch": row.branch,
        "tests": row.tests,
        "persona": persona,
        "coverage": row.coverage or {},
        "sub_goals": row.sub_goals or [],
        "sub_actors": row.sub_actors or [],
        "keywords": row.keywords or [],
        "background_noise": row.background_noise,
        "max_turns": row.max_turns,
        "status": scenario_status(row),
        "call_execution_id": (
            str(row.call_execution_id) if row.call_execution_id else None
        ),
    }


def scenario_status(row: HostedHarnessScenario) -> str:
    """Where this scenario got to; `authored` before any call ran."""
    execution = getattr(row, "call_execution", None)
    if execution is None:
        return "authored"
    return str(getattr(execution, "status", "") or "authored").lower()


def apply_ordering(queryset: QuerySet, ordering: str) -> QuerySet:
    """Sort by a column the client named, defaulting to the suite's own order."""
    allowed = {"number", "-number", "name", "-name", "use_case", "-use_case"}
    ordering = ordering if ordering in allowed else "number"
    if isinstance(queryset, ScenarioRows):
        field = ordering.lstrip("-")
        return ScenarioRows(
            sorted(
                queryset,
                key=lambda row: getattr(row, field) or (0 if field == "number" else ""),
                reverse=ordering.startswith("-"),
            )
        )
    return queryset.order_by(ordering)


def grouped(rows: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    """Tag each row with its group and order the page by it."""
    field = GROUP_BY.get(group_by)
    if not field:
        return rows
    head, _, tail = field.partition(".")
    tagged: list[dict[str, Any]] = []
    for row in rows:
        held = (row.get(head) or {}).get(tail) if tail else row.get(head)
        # A list field places the scenario in the section of each of its values.
        for value in (held or [None]) if isinstance(held, list) else [held]:
            tagged.append({**row, "group": value or "Ungrouped"})
    tagged.sort(key=lambda row: (str(row.get("group") or ""), row.get("number") or 0))
    return tagged


def group_counts(
    rows: list[dict[str, Any]], queryset: QuerySet | None = None, group_by: str = ""
) -> list[dict[str, Any]]:
    """The sections on this page: `count` on the page, `total` in the whole filtered suite."""
    sections: list[dict[str, Any]] = []
    for row in rows:
        name = row.get("group")
        if name is None:
            continue
        if sections and sections[-1]["name"] == name:
            sections[-1]["count"] += 1
        else:
            sections.append({"name": name, "count": 1})
    if queryset is None or not sections:
        return sections

    field = GROUP_BY.get(group_by)
    if not field:
        return sections
    totals: dict[str, int] = {}
    for held in queryset.values_list(_orm_path(field), flat=True):
        for value in (held if isinstance(held, list) else [held]) or [None]:
            name = value or "Ungrouped"
            totals[name] = totals.get(name, 0) + 1
    for section in sections:
        section["total"] = totals.get(section["name"], section["count"])
    return sections
