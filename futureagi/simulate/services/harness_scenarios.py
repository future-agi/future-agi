"""Index a run's authored suite in SQL, filter it, and describe its own filters."""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet, Value
from django.db.models.functions import Replace

from simulate.models.hosted_harness import HostedHarnessJob, HostedHarnessScenario

# Filter panel properties; a dotted `value` reads into that JSON column.
FIELDS: tuple[dict[str, Any], ...] = (
    {"value": "name", "label": "Scenario", "type": "string", "category": "scenario"},
    {"value": "use_case", "label": "Use case", "type": "enum", "category": "scenario"},
    {"value": "instruction", "label": "Situation", "type": "string", "category": "scenario"},
    {"value": "persona.name", "label": "Name", "type": "enum", "category": "persona"},
    {"value": "persona.accent", "label": "Accent", "type": "enum", "category": "persona"},
    {
        "value": "persona.languages",
        "label": "Language",
        "type": "enum",
        "category": "persona",
    },
    {"value": "persona.age_group", "label": "Age", "type": "enum", "category": "persona"},
    {"value": "persona.gender", "label": "Gender", "type": "enum", "category": "persona"},
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
    return AXIS_LABELS.get(axis) or str(axis or "").replace("_", " ").strip().capitalize()


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
    "quiet_line": "Quiet line",
    "non_native": "Non-native speaker",
    "code_switching": "Switches language",
}
# What each background a caller can be heard over sounds like, for the noise column only.
NOISE_LABELS: dict[str, str] = {
    "quiet line": "Quiet line",
    "present": "Background noise",
    "street": "Street",
    "vehicle": "In a car",
    "transit": "Airport / station",
    "retail": "Shop / mall",
    "office": "Office",
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


def _orm_path(field: str) -> str:
    """The ORM lookup for a filter property, dotted paths reaching into the JSON document."""
    head, _, tail = field.partition(".")
    if tail and head in _COLUMNS:
        return f"{_COLUMNS[head]}__{tail}"
    return _ALIASES.get(head, head)


def index_scenarios(
    job: HostedHarnessJob, docs: list[dict[str, Any]], prune: bool = False
) -> int:
    """Persist the authored suite so it can be queried, and return how many rows it holds."""
    if not docs:
        return 0
    existing = {
        row.scenario_key: row
        for row in HostedHarnessScenario.no_workspace_objects.filter(job=job)
    }
    # Numbers are stable across re-indexing; only a new row gets the next free number.
    taken = max(
        (row.number for row in existing.values() if row.number is not None), default=0
    )
    written = 0
    seen: set[str] = set()
    for position, doc in enumerate(docs, start=1):
        key = str(doc.get("scenario_key") or doc.get("name") or "").strip()
        if not key:
            continue
        seen.add(key)
        held = existing.get(key)
        if held is not None and held.number is not None:
            number = held.number
        elif not existing:
            number = position
        else:
            taken += 1
            number = taken
        noise = doc.get("background_noise")
        fields = {
            "number": number,
            "name": str(doc.get("name") or key)[:255],
            "instruction": str(doc.get("instruction") or ""),
            "use_case": str(doc.get("use_case") or "")[:255],
            "branch": str(doc.get("branch") or ""),
            "tests": str(doc.get("tests") or ""),
            "folder": str(doc.get("folder") or "")[:512],
            "persona": doc.get("persona") or None,
            "coverage": doc.get("coverage") or None,
            "sub_goals": doc.get("sub_goals") or None,
            # Older suites carry keywords on the persona.
            "keywords": (
                doc.get("keywords") or (doc.get("persona") or {}).get("keywords") or None
            ),
            "background_noise": (
                noise if isinstance(noise, str) else "present" if noise else "quiet line"
            )[:64],
            "max_turns": doc.get("max_turns") or None,
        }
        row = held
        if row is None:
            HostedHarnessScenario.no_workspace_objects.create(
                job=job, scenario_key=key, **fields
            )
        else:
            for name, value in fields.items():
                setattr(row, name, value)
            row.save(update_fields=[*fields, "updated_at"])
        written += 1
    # Only an amend prunes: a short suite on a poll may still be mid-write. Called rows are kept.
    if prune:
        HostedHarnessScenario.no_workspace_objects.filter(job=job).exclude(
            scenario_key__in=seen
        ).filter(call_execution__isnull=True).delete()
    return written


def apply_filters(queryset: QuerySet, params) -> QuerySet:
    """Narrow the suite by the filter panel's query params: repeated keys OR, a `_not` suffix negates."""
    for key in params.keys():
        negated = key.endswith("_not")
        field = key[:-4] if negated else key
        if field not in _BY_VALUE:
            continue
        values = [one for one in params.getlist(key) if one not in (None, "")]
        if not values:
            continue
        path = _orm_path(field)
        lookup = f"{path}__contains" if _BY_VALUE[field]["value"] in _LIST_FIELDS else f"{path}__in"
        matches = Q()
        for value in values:
            matches |= Q(**{lookup: [value] if lookup.endswith("contains") else [value]})
        queryset = queryset.exclude(matches) if negated else queryset.filter(matches)
    return queryset


def apply_search(queryset: QuerySet, term: str) -> QuerySet:
    """Every word must match somewhere; names are also matched with underscores as spaces."""
    words = (term or "").split()
    if not words:
        return queryset
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


def field_catalogue(queryset: QuerySet, spoken: bool = True) -> list[dict[str, Any]]:
    """The panel's `fields`, with each enum's values and counts over the whole filtered suite."""
    enums = [field for field in FIELDS if field["type"] == "enum"]
    paths = {field["value"]: _orm_path(field["value"]) for field in enums}
    tally: dict[str, dict[str, int]] = {field["value"]: {} for field in enums}
    for row in queryset.values(*set(paths.values())):
        for value, path in paths.items():
            held = row.get(path)
            for one in held if isinstance(held, list) else [held]:
                text = str(one or "").strip()
                if text:
                    counts = tally[value]
                    counts[text] = counts.get(text, 0) + 1

    catalogue: list[dict[str, Any]] = []
    for field in FIELDS:
        if not spoken and field["value"] in VOICE_ONLY_FIELDS:
            continue
        entry = {key: value for key, value in field.items()}
        if field["type"] != "enum":
            catalogue.append(entry)
            continue
        counts = tally[field["value"]]
        if not counts:
            continue
        entry["choices"] = [
            name for name, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ]
        entry["counts"] = counts
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
                "counts": dict(sorted(held.items(), key=lambda pair: (-pair[1], pair[0]))),
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
            level: level_label(level)
            for axis in levels
            for level in levels[axis]
        },
    }


def level_labels_for(
    rows: list[dict[str, Any]], fields: list[dict[str, Any]] | None = None
) -> dict[str, str]:
    """The reader-facing name for every coverage level and noise bed on a page and in its filters."""
    levels: set[str] = set()
    beds: set[str] = set()
    for field in fields or []:
        if field.get("value") == "background_noise":
            beds.update(str(one) for one in field.get("choices") or [])
        elif str(field.get("value") or "").startswith("coverage."):
            levels.update(str(one) for one in field.get("choices") or [])
    for row in rows or []:
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
    return queryset.order_by(ordering if ordering in allowed else "number")


def grouped(rows: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    """Tag each row with its group and order the page by it."""
    field = GROUP_BY.get(group_by)
    if not field:
        return rows
    head, _, tail = field.partition(".")
    for row in rows:
        held = (row.get(head) or {}).get(tail) if tail else row.get(head)
        if isinstance(held, list):
            held = (held or [None])[0]
        row["group"] = held or "Ungrouped"
    rows.sort(key=lambda row: (str(row.get("group") or ""), row.get("number") or 0))
    return rows


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
        if isinstance(held, list):
            key = (held or ["Ungrouped"])[0]
        else:
            key = held or "Ungrouped"
        totals[key] = totals.get(key, 0) + 1
    for section in sections:
        section["total"] = totals.get(section["name"], section["count"])
    return sections
