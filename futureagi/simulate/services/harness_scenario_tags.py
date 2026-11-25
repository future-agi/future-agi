"""The tags people put on an environment's scenarios: free-text labels, kept apart from the versioned suite."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, TypeVar

from django.db import transaction
from django.db.models import QuerySet
from django.http import QueryDict

from simulate.models import HostedHarnessJob, HostedHarnessScenario
from simulate.models.harness_scenario_tags import HarnessScenarioTags
from simulate.services.hosted_harness import HostedHarnessError

if TYPE_CHECKING:
    from simulate.services.harness_scenarios import ScenarioRows

_Rows = TypeVar("_Rows", "QuerySet[HostedHarnessScenario]", "ScenarioRows")

MAX_TAGS_PER_SCENARIO = 20
MAX_TAG_LENGTH = 64
MAX_SCENARIOS_PER_CHANGE = 1000
FILTER_FIELD = "tags"
FILTER_LABEL = "Tags"


def _invalid(message: str) -> HostedHarnessError:
    return HostedHarnessError("scenario_tags_invalid", message, status_code=400)


def clean_tags(values: Iterable[Any]) -> list[str]:
    """The tags as stored: trimmed, without blanks or repeats, in the order given."""
    cleaned: list[str] = []
    for value in values:
        tag = str(value or "").strip()
        if not tag or tag in cleaned:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise _invalid(f"A tag can be at most {MAX_TAG_LENGTH} characters")
        cleaned.append(tag)
    if len(cleaned) > MAX_TAGS_PER_SCENARIO:
        raise _invalid(f"A scenario can have at most {MAX_TAGS_PER_SCENARIO} tags")
    return cleaned


def known_keys(environment: HostedHarnessJob) -> set[str]:
    """The scenario keys the environment has, its open draft's included."""
    from simulate.services.harness_versioning.drafts import draft_rows

    keys = set(
        HostedHarnessScenario.no_workspace_objects.filter(job=environment).values_list(
            "scenario_key", flat=True
        )
    )
    drafted = draft_rows(environment)
    if drafted is not None:
        keys.update(str(one.scenario_key) for one in drafted)
    return keys


def _require_known(environment: HostedHarnessJob, keys: Iterable[str]) -> None:
    unknown = sorted(set(keys) - known_keys(environment))
    if unknown:
        raise HostedHarnessError(
            "scenario_not_found",
            f"No scenario {unknown[0]} in this environment",
            status_code=404,
        )


def _rows(environment: HostedHarnessJob) -> QuerySet[HarnessScenarioTags]:
    return HarnessScenarioTags.no_workspace_objects.filter(environment=environment)


def tags_by_key(
    environment: HostedHarnessJob, keys: Iterable[str]
) -> dict[str, list[str]]:
    return dict(
        _rows(environment)
        .filter(scenario_key__in=list(keys))
        .values_list("scenario_key", "tags")
    )


def environment_tags(environment: HostedHarnessJob) -> list[str]:
    """Every tag the environment's scenarios carry, for the picker and the filter."""
    seen: set[str] = set()
    for tags in _rows(environment).values_list("tags", flat=True):
        seen.update(one for one in tags or [] if isinstance(one, str))
    return sorted(seen, key=str.casefold)


def _hold(environment: HostedHarnessJob) -> None:
    list(
        HostedHarnessJob.no_workspace_objects.select_for_update()
        .filter(id=environment.id)
        .values_list("id", flat=True)
    )


def _store(environment: HostedHarnessJob, key: str, tags: list[str]) -> None:
    if tags:
        HarnessScenarioTags.no_workspace_objects.update_or_create(
            environment=environment,
            scenario_key=key,
            defaults={"tags": tags},
        )
    else:
        _rows(environment).filter(scenario_key=key).delete()


def set_tags(environment: HostedHarnessJob, key: str, tags: Iterable[Any]) -> list[str]:
    cleaned = clean_tags(tags)
    _require_known(environment, [key])
    with transaction.atomic():
        _hold(environment)
        _store(environment, key, cleaned)
    return cleaned


def change_tags(
    environment: HostedHarnessJob,
    keys: Iterable[str],
    *,
    add: Iterable[Any] = (),
    remove: Iterable[Any] = (),
) -> dict[str, list[str]]:
    """Add and remove tags on many scenarios at once; each scenario keeps the tags it had otherwise."""
    wanted = list(dict.fromkeys(str(one) for one in keys))
    if not wanted:
        raise _invalid("Pick at least one scenario")
    if len(wanted) > MAX_SCENARIOS_PER_CHANGE:
        raise _invalid(
            f"Tag at most {MAX_SCENARIOS_PER_CHANGE} scenarios in one change"
        )
    adding = clean_tags(add)
    dropping = set(clean_tags(remove))
    if not adding and not dropping:
        raise _invalid("Say which tags to add or remove")
    _require_known(environment, wanted)
    changed: dict[str, list[str]] = {}
    with transaction.atomic():
        _hold(environment)
        held = dict(
            _rows(environment)
            .filter(scenario_key__in=wanted)
            .values_list("scenario_key", "tags")
        )
        for key in wanted:
            before = list(held.get(key) or [])
            after = clean_tags(
                [one for one in before if one not in dropping]
                + [one for one in adding if one not in before]
            )
            if after != before:
                _store(environment, key, after)
            changed[key] = after
    return changed


def _wanted(params: QueryDict, name: str) -> list[str]:
    return [one for one in params.getlist(name) if one not in (None, "")]


def filter_by_tags(
    environment: HostedHarnessJob,
    rows: _Rows,
    params: QueryDict,
) -> _Rows:
    """Keep the scenarios with any of the picked tags, then drop those with any `tags_not` tag."""
    from simulate.services.harness_scenarios import ScenarioRows

    picked = _wanted(params, FILTER_FIELD)
    refused = _wanted(params, f"{FILTER_FIELD}_not")
    if not picked and not refused:
        return rows

    def keys_with(tags: list[str]) -> set[str]:
        found: set[str] = set()
        for tag in tags:
            found.update(
                _rows(environment)
                .filter(tags__contains=[tag])
                .values_list("scenario_key", flat=True)
            )
        return found

    keep = keys_with(picked) if picked else None
    drop = keys_with(refused) if refused else set()
    if isinstance(rows, ScenarioRows):
        return ScenarioRows(
            one
            for one in rows
            if (keep is None or one.scenario_key in keep)
            and one.scenario_key not in drop
        )
    if keep is not None:
        rows = rows.filter(scenario_key__in=keep)
    return rows.exclude(scenario_key__in=drop) if drop else rows


def tag_field(
    environment: HostedHarnessJob, rows: QuerySet[HostedHarnessScenario] | ScenarioRows
) -> dict[str, Any] | None:
    """The filter panel's Tags entry, counted over the searched suite; none when nothing is tagged."""
    keys = {str(one) for one in rows.values_list("scenario_key", flat=True)}
    counts: dict[str, int] = {}
    for key, tags in _rows(environment).values_list("scenario_key", "tags"):
        if key not in keys:
            continue
        for tag in tags or []:
            counts[tag] = counts.get(tag, 0) + 1
    if not counts:
        return None
    return {
        "value": FILTER_FIELD,
        "label": FILTER_LABEL,
        "type": "enum",
        "category": "scenario",
        "choices": [
            name
            for name, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "counts": counts,
    }
