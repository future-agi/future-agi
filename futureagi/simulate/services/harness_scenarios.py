"""Reading a run's authored suite: index it, filter it, and describe its own filters.

The suite reached the front end only as a JSON stage artefact, so every filter, every dropdown's
options and every keyword count was computed in the browser over whatever had been downloaded.
That works at twenty scenarios and cannot work at five hundred: a filter can only see the page it
was given. This module puts the suite in SQL so the server answers those questions instead.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet, Value
from django.db.models.functions import Replace

from simulate.models.hosted_harness import HostedHarnessJob, HostedHarnessScenario

# The filter panel is the platform's own: Property, operator, value, with Ask AI above it. It asks
# for a `fields` list and returns `{field, operator, value}` rows, so this is the whole contract.
# A dotted path reads into the JSON document, which is what gives persona its depth.
# One flat list with a `category` on every property, which is how the shared filter panel groups
# them: the category is shown against each row, so the label stays the plain name of the thing.
# The dotted `value` is what the filter is sent as, so where a property lives is carried by the
# query shape rather than spelled into its label.
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
    # The suite's own vocabulary for finding a scenario. It describes the situation, not the
    # caller, which is why it is a scenario property and not a persona one.
    {"value": "keywords", "label": "Keywords", "type": "enum", "category": "scenario"},
    {"value": "status", "label": "Status", "type": "enum", "category": "run"},
)

_BY_VALUE = {field["value"]: field for field in FIELDS}

# Columns holding many values for one scenario, where a filter means membership.
_LIST_FIELDS = frozenset({"sub_goals", "persona.languages", "keywords"})

# Which model column a dotted property reads from, and the key path inside it.
_COLUMNS = {"persona": "persona", "coverage": "coverage"}

# What a suite is worth cutting into sections by. Persona NAME is deliberately absent: first names
# are unique across a suite by one of the authoring gates, so grouping on it can only ever produce
# groups of one. The persona axes that do repeat are here instead.
GROUP_BY = {
    "goal": "use_case",
    "sub_goal": "sub_goals",
    "accent": "persona.accent",
    "age": "persona.age_group",
    "attack": "coverage.overlay",
    "task": "coverage.task",
}

# What the client offers in its "group by" control. Served rather than written into the client, so
# adding a grouping is one change here and not two across two repositories.
GROUPINGS: tuple[dict[str, str], ...] = (
    {"value": "goal", "label": "Use case"},
    {"value": "sub_goal", "label": "Sub-goal"},
    {"value": "accent", "label": "Accent"},
    {"value": "age", "label": "Age"},
    {"value": "attack", "label": "Attack"},
    {"value": "task", "label": "Task"},
    {"value": "", "label": "No grouping"},
)

# Which pair of axes the grid opens on. The client asks for the grid without naming axes and is
# told which it got, so the two dropdowns have a value before anyone picks one.
# The grouping a suite opens on when the caller names none. Asking for no grouping is different
# from not asking, so an explicitly empty `group_by` still means one flat list.
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

# What a reader outside this team should see instead of the axis key. The keys are the scenario
# framework's own words and mean nothing to a customer: "counterparty" and "disposition" in
# particular were carried over verbatim from the framework write-up. Served rather than written
# into the client so renaming an axis is one change here and none anywhere else, and so the grid
# and the filter panel cannot drift apart - `overlay` is "Attack" in both.
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


def axis_label(axis: str) -> str:
    """The reader-facing name for one axis, falling back to the key made readable."""
    return AXIS_LABELS.get(axis) or str(axis or "").replace("_", " ").strip().capitalize()


# The same for a level. Only the ones whose key reads badly are listed; everything else falls
# through to the key with its underscores removed, which is right far more often than not. Served
# for the same reason the axis names are: renaming what a reader sees is a change here and nowhere
# else, and never a change in a client.
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


def level_label(level: str) -> str:
    """The reader-facing name for one level of an axis."""
    said = str(level or "").strip()
    if not said:
        return ""
    return LEVEL_LABELS.get(said) or said.replace("_", " ").strip().capitalize()


# Properties that are not columns on the row. Status lives on the call, and is absent until one
# has been made, which is what "authored" means when it is read back.
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
    """Persist the authored suite so it can be queried, and return how many rows it holds.

    Registration happens when a call starts, which is far too late for a tab that exists to show
    the suite before anything is called. So the rows are written when the suite itself arrives.
    """
    if not docs:
        return 0
    existing = {
        row.scenario_key: row
        for row in HostedHarnessScenario.no_workspace_objects.filter(job=job)
    }
    # A number belongs to the scenario, not to its position in the list. Re-indexing after an
    # edit must not hand scenario 12 the number 11 because 3 was deleted: the number is what a
    # person types to name it, and the amend route resolves ranges like "12-30" with it. So a row
    # that already has one keeps it, and only a new row is given the next free number.
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
            # First index of an authored suite: the written order is the numbering.
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
            # Suites written before keywords moved off the persona still carry them there, and
            # those documents are durable: sealed archives a rerun replays are never rewritten.
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
            # Tenancy is the job's. The row has no organization or workspace column of its own,
            # so every read reaches it through a job the caller is already scoped to.
            HostedHarnessScenario.no_workspace_objects.create(
                job=job, scenario_key=key, **fields
            )
        else:
            for name, value in fields.items():
                setattr(row, name, value)
            row.save(update_fields=[*fields, "updated_at"])
        written += 1
    # A scenario dropped from the suite has to leave the table too, or a delete only ever looks
    # like one. Only an amend may do this: indexing also runs on every poll, where a suite that
    # arrives short means the artefact is still being written, not that anything was removed.
    # A row that already carries a call is left either way, because that call really happened.
    if prune:
        HostedHarnessScenario.no_workspace_objects.filter(job=job).exclude(
            scenario_key__in=seen
        ).filter(call_execution__isnull=True).delete()
    return written


def apply_filters(queryset: QuerySet, params) -> QuerySet:
    """Narrow the suite by the filter panel's own query params, in SQL.

    The panel sends object-style filters, not triples: repeated keys are an OR-set on one field,
    and a `_not` suffix negates. So `?persona.accent=Canadian&persona.accent=American` is "either
    accent" and `?coverage.overlay_not=none` is "carries an attack".
    """
    for key in params.keys():
        negated = key.endswith("_not")
        field = key[:-4] if negated else key
        if field not in _BY_VALUE:
            continue
        values = [one for one in params.getlist(key) if one not in (None, "")]
        if not values:
            continue
        path = _orm_path(field)
        # A list column holds many values per row, so membership is "contains", not equality.
        lookup = f"{path}__contains" if _BY_VALUE[field]["value"] in _LIST_FIELDS else f"{path}__in"
        matches = Q()
        for value in values:
            matches |= Q(**{lookup: [value] if lookup.endswith("contains") else [value]})
        queryset = queryset.exclude(matches) if negated else queryset.filter(matches)
    return queryset


def apply_search(queryset: QuerySet, term: str) -> QuerySet:
    """Free text over what a person would remember: the name, the situation, the use case.

    A name is stored snake_case and shown with the underscores swapped for spaces, so somebody
    searching for what is on their screen types "book ride" against a stored "book_ride_...".
    Matching the readable form as well as the stored one means the search answers the table
    rather than the column.
    """
    words = (term or "").split()
    if not words:
        return queryset
    queryset = queryset.annotate(
        readable_name=Replace("name", Value("_"), Value(" ")),
        readable_folder=Replace("scenario_key", Value("_"), Value(" ")),
    )
    # Every word has to appear somewhere, rather than the whole phrase appearing in one place.
    # Matching the phrase as one substring meant "payment sms" found nothing against a scenario
    # called "payment link sms", which is not how anyone expects a search box to behave.
    matches = Q()
    for word in words:
        anywhere = Q(readable_name__icontains=word) | Q(readable_folder__icontains=word)
        for field in SEARCHABLE:
            anywhere |= Q(**{f"{field}__icontains": word})
        matches &= anywhere
    return queryset.filter(matches)


def field_catalogue(queryset: QuerySet) -> list[dict[str, Any]]:
    """The panel's `fields`, with each enum's real values and counts.

    Counted over the whole filtered suite rather than the page, because a choice list built from
    one page offers filters that hide rows the user can see and omits values they cannot.
    """
    enums = [field for field in FIELDS if field["type"] == "enum"]
    paths = {field["value"]: _orm_path(field["value"]) for field in enums}
    # One pass over the suite, not one per property. A scan per field meant a dozen full reads on
    # every poll, which is the difference between a tab that scales to thousands and one that does
    # not. The counts themselves are cheap; the repeated round trips were not.
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


def coverage_grid(queryset: QuerySet, row_axis: str, col_axis: str) -> dict[str, Any]:
    """One cross-tab of the suite. Empty cells are the point, so they are returned as zero."""
    rows: dict[str, int] = {}
    cols: dict[str, int] = {}
    cells: dict[str, int] = {}
    # How many distinct levels each axis actually got, counted in the same pass. A grid answers
    # "which pairing is thin"; this answers "which axis was barely varied at all", which is asked
    # first and which a single cross-tab cannot show.
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
                "label": axis_label(axis),
                "levels": len(held),
                "scenarios": sum(held.values()),
                "counts": dict(sorted(held.items(), key=lambda pair: (-pair[1], pair[0]))),
            }
            for axis, held in levels.items()
            if held
        ],
        "row_axis": row_axis,
        "row_axis_label": axis_label(row_axis),
        "col_axis": col_axis,
        "col_axis_label": axis_label(col_axis),
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
        # Every axis the client may offer, with the name to show for it. The client picks its two
        # dropdowns from this and never spells an axis name itself.
        "axis_labels": {axis: axis_label(axis) for axis in AXES},
        # Every level the grid can show, with the name to show for it. A client renders these and
        # never turns a key into words itself, so renaming one is a change here alone.
        "level_labels": {
            level: level_label(level)
            for axis in levels
            for level in levels[axis]
        },
    }


def level_labels_for(rows: list[dict[str, Any]]) -> dict[str, str]:
    """The reader-facing name for every coverage level and noise bed on one page of rows.

    One map for the page rather than a label beside every value: the same dozen levels repeat
    down a page of fifty, and the client looks each up once.
    """
    seen: set[str] = set()
    for row in rows or []:
        for value in (row.get("coverage") or {}).values():
            said = str(value or "").strip()
            if said:
                seen.add(said)
        bed = row.get("background_noise")
        if isinstance(bed, str) and bed.strip():
            seen.add(bed.strip())
    return {level: level_label(level) for level in sorted(seen)}


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
    """Where this scenario got to. Authored is the honest answer before any call ran."""
    execution = getattr(row, "call_execution", None)
    if execution is None:
        return "authored"
    return str(getattr(execution, "status", "") or "authored").lower()


def apply_ordering(queryset: QuerySet, ordering: str) -> QuerySet:
    """Sort by a column the client named, defaulting to the suite's own order."""
    allowed = {"number", "-number", "name", "-name", "use_case", "-use_case"}
    return queryset.order_by(ordering if ordering in allowed else "number")


def grouped(rows: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    """Tag each row with its group and order the page by it.

    Ordering is part of grouping. Tagging alone leaves a page whose rows interleave, which forces
    whoever draws it to gather them again; sorted here, a section is a run of consecutive rows and
    the client only has to notice where the tag changes.
    """
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
    """The sections on this page, each carrying how big the group really is.

    `count` is how many of the group landed on this page; `total` is how many the whole filtered
    suite holds. A group larger than a page is the normal case, and a header that reported only
    the slice in front of the reader made grouping look like ordering.
    """
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
    # One scan for every group total, rather than a count query per section.
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
