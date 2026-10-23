"""Per-cohort sampling audit: design, realized sample, weights and balance.

Closed cohorts: the realized sample is the persisted membership. The
population of every stratum is re-read once from ClickHouse at the cohort's
frozen snapshot (one bounded preview statement), so strata that selected no
unit are reported too and any drift since the freeze is visible per stratum.
Live cohorts: every first decision (selected and rejected) is persisted, so
the audit reads PostgreSQL only and tests realized counts against the
configured rates (sample-ratio mismatch).

All arithmetic is delegated to the pure ``stats`` module; ``audit_report`` is
pure as well and is unit-tested with hand-computed values.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from django.db.models import Count, Max, Min, Q

from tracer.models.eval_sampling import EvalSamplingCohort, EvalSamplingMember
from tracer.models.eval_task import EvalTask
from tracer.services.eval_tasks.sampling import stats
from tracer.services.eval_tasks.sampling.policy import (
    InvalidSamplingPolicy,
    quota_target,
    target_size,
)

MAX_AUDIT_STRATA = 1000


class SamplingAuditNotFound(LookupError):
    """No cohort exists (yet) for the requested task policy."""


class SamplingAuditIncomplete(RuntimeError):
    """A closed cohort is still freezing; its membership is not certified."""


class SamplingAuditTooLarge(ValueError):
    """More strata than one audit reports."""


def parse_stratum(label: str) -> tuple[str, list[tuple[bool, str | None]]]:
    """Split a stratum identity into its slice name and group-by values."""

    try:
        name, groups = json.loads(label)
        parts = []
        for encoded in groups:
            present, value = json.loads(encoded)
            parts.append((bool(present), str(value) if present else None))
        if isinstance(name, str):
            return name, parts
    except (TypeError, ValueError):
        pass
    return label, []


def _rules(policy: dict) -> dict[str, dict]:
    rules = {rule["name"]: rule for rule in policy.get("strata", [])}
    rules["default"] = policy["default"]
    return rules


def _stratum_row(policy: dict, rules: dict, cell: dict) -> dict:
    name, _groups = parse_stratum(cell["stratum"])
    rule = rules.get(name)
    population, achieved = cell["population"], cell["achieved"]
    row = {
        "stratum": cell["stratum"],
        "rule": name,
        "population_size": population,
        "achieved": achieved,
        "population_check": cell["population_check"],
        "target": None,
        "expected_size": None,
        "shortage": None,
        "exact": None,
        "srm_z": None,
        "srm_p_value": None,
        "srm_flagged": None,
    }
    if policy["mode"] == "closed":
        expected = target_size(rule, population) if rule else None
        probability = cell.get("probability")
        if probability is None:
            probability = expected / population if expected and population else 0.0
        row.update(
            target=quota_target(rule, population) if rule else None,
            expected_size=expected,
            exact=expected is not None and achieved == expected,
        )
        if row["target"] is not None:
            row["shortage"] = max(0, row["target"] - achieved)
    else:
        probability = cell.get("probability")
        if probability is None:
            probability = (rule or {}).get("rate", 100) / 100
        test = stats.srm_test(achieved, population, probability)
        row.update(
            expected_size=population * probability,
            srm_z=test["z"],
            srm_p_value=test["p_value"],
            srm_flagged=test["flagged"],
        )
    row["inclusion_probability"] = probability
    row["design_weight"] = 1 / probability if probability > 0 else None
    return row


def _balance(policy: dict, strata: list[dict]) -> list[dict]:
    attributes = [{"attribute": "slice", "source": None}] + [
        {"attribute": item["key"], "source": item.get("source", "span")}
        for item in policy.get("group_by", [])
    ]
    results = []
    for index, attribute in enumerate(attributes):
        categories: dict[tuple[bool, str | None], dict] = {}
        for row in strata:
            name, groups = parse_stratum(row["stratum"])
            if index == 0:
                key = (True, name)
            else:
                key = groups[index - 1] if index - 1 < len(groups) else (False, None)
            entry = categories.setdefault(
                key,
                {
                    "value": key[1],
                    "present": key[0],
                    "population_size": 0,
                    "achieved": 0,
                    "weighted": 0.0,
                },
            )
            entry["population_size"] += row["population_size"]
            entry["achieved"] += row["achieved"]
            if row["achieved"] and row["design_weight"]:
                entry["weighted"] += row["achieved"] * row["design_weight"]
        population = sum(entry["population_size"] for entry in categories.values())
        sampled = sum(entry["achieved"] for entry in categories.values())
        weighted = sum(entry["weighted"] for entry in categories.values())
        ordered = sorted(
            categories.values(),
            key=lambda entry: (-entry["population_size"], str(entry["value"])),
        )
        rows = []
        for entry in ordered:
            share = entry["population_size"] / population if population else 0.0
            sample_share = entry["achieved"] / sampled if sampled else None
            weighted_share = entry["weighted"] / weighted if weighted else None
            rows.append(
                {
                    "value": entry["value"],
                    "present": entry["present"],
                    "population_size": entry["population_size"],
                    "achieved": entry["achieved"],
                    "population_share": share,
                    "sample_share": sample_share,
                    "weighted_share": weighted_share,
                    "standardized_difference": None
                    if sample_share is None
                    else stats.standardized_difference(sample_share, share),
                    "weighted_standardized_difference": None
                    if weighted_share is None
                    else stats.standardized_difference(weighted_share, share),
                }
            )
        fit = stats.chi_square_goodness_of_fit(
            [row["achieved"] for row in rows], [row["population_share"] for row in rows]
        )
        weighted_gaps = [
            abs(row["weighted_standardized_difference"])
            for row in rows
            if row["weighted_standardized_difference"] is not None
        ]
        unbounded = any(
            row["weighted_share"] is not None
            and row["weighted_standardized_difference"] is None
            for row in rows
        )
        results.append(
            {
                **attribute,
                "categories": rows,
                "chi_square": fit["statistic"],
                "degrees_of_freedom": fit["degrees_of_freedom"],
                "p_value": fit["p_value"],
                "max_abs_standardized_difference": max(
                    (
                        abs(row["standardized_difference"])
                        for row in rows
                        if row["standardized_difference"] is not None
                    ),
                    default=None,
                ),
                "max_abs_weighted_standardized_difference": max(
                    weighted_gaps, default=None
                ),
                "weighted_imbalance": unbounded
                or any(gap > stats.BALANCE_THRESHOLD for gap in weighted_gaps),
            }
        )
    return results


def audit_report(policy: dict, cells: list[dict]) -> dict:
    """Assemble the audit from per-stratum population and realized counts.

    ``cells`` holds ``stratum``, ``population`` (N), ``achieved`` (selected
    units), ``probability`` (persisted inclusion probability or ``None``) and
    ``population_check``.
    """

    rules = _rules(policy)
    strata = [
        _stratum_row(policy, rules, cell)
        for cell in sorted(cells, key=lambda cell: cell["stratum"])
    ]
    weighted = [
        (row["design_weight"], row["achieved"])
        for row in strata
        if row["achieved"] and row["design_weight"]
    ]
    represented = [row for row in strata if row["achieved"]]
    unrepresented = [
        row for row in strata if row["population_size"] and not row["achieved"]
    ]
    population = sum(row["population_size"] for row in strata)
    if policy["mode"] == "closed":
        attention = [
            row
            for row in strata
            if not row["exact"] or row["population_check"] != "match"
        ]
    else:
        attention = [row for row in strata if row["srm_flagged"]]
    reconstructed = sum(weight * count for weight, count in weighted)
    represented_population = sum(row["population_size"] for row in represented)
    return {
        "strata": strata,
        "summary": {
            "strata": len(strata),
            "population_size": population,
            "achieved": sum(row["achieved"] for row in strata),
            "kish_effective_n": stats.kish_effective_n(weighted),
            "design_effect": stats.design_effect(weighted),
            "weight_cv": stats.weight_cv(weighted),
            "weighted_population": reconstructed,
            "represented_population": represented_population,
            "unrepresented_strata": len(unrepresented),
            "unrepresented_population": sum(
                row["population_size"] for row in unrepresented
            ),
            "flagged_strata": len(attention),
            "integrity": "attention" if attention else "ok",
            "srm_alpha": stats.SRM_ALPHA,
        },
        "balance": _balance(policy, strata),
    }


def _current_cohort(task: EvalTask) -> EvalSamplingCohort | None:
    from tracer.services.eval_tasks.sampling.service import task_policy

    _policy, version = task_policy(task, now=datetime.now(UTC))
    return EvalSamplingCohort.objects.filter(task=task, policy_version=version).first()


def _frozen_population(task: EvalTask, cohort: EvalSamplingCohort) -> dict:
    """Per-stratum (N, k) of the frozen population, read at the freeze time."""

    from tracer.services.eval_tasks.sampling.query import SamplingQuery, stream_query

    context = cohort.selection_context or {}
    population_task = EvalTask(
        project=task.project,
        row_type=context.get("row_type", task.row_type),
        run_type=task.run_type,
        filters=context.get("filters", task.filters or {}),
        sampling_policy=cohort.policy,
    )
    statement = SamplingQuery(
        population_task, cohort.policy, cohort.policy_version, cohort.frozen_at
    ).preview()
    cells: dict[str, tuple[int, int]] = {}
    for block in stream_query(*statement):
        for name, population, _rows, _units, _expected, selected, _selected in block:
            cells[str(name)] = (int(population), int(selected))
            if len(cells) > MAX_AUDIT_STRATA:
                raise SamplingAuditTooLarge(
                    f"This cohort has more than {MAX_AUDIT_STRATA} strata to audit."
                )
    return cells


def sampling_audit(task: EvalTask, *, cohort_id=None) -> dict:
    if task.sampling_policy is None:
        raise InvalidSamplingPolicy("This task has no sampling policy to audit.")
    if cohort_id is not None:
        cohort = EvalSamplingCohort.objects.filter(task=task, pk=cohort_id).first()
    else:
        cohort = _current_cohort(task)
    if cohort is None:
        raise SamplingAuditNotFound(
            "No sampling cohort exists for this task's current policy yet. It is "
            "created when the task first runs."
        )
    policy = cohort.policy
    if policy["mode"] == "closed" and not cohort.complete:
        raise SamplingAuditIncomplete(
            "This closed cohort is still freezing. Retry after the task's first "
            "run completes."
        )
    persisted = list(
        EvalSamplingMember.objects.filter(cohort=cohort)
        .values("stratum")
        .annotate(
            decided=Count("id"),
            achieved=Count("id", filter=Q(selected=True)),
            probability_max=Max("inclusion_probability"),
            probability_min=Min("inclusion_probability"),
            population_max=Max("population_size"),
            population_min=Min("population_size"),
        )
        .order_by("stratum")[: MAX_AUDIT_STRATA + 1]
    )
    if len(persisted) > MAX_AUDIT_STRATA:
        raise SamplingAuditTooLarge(
            f"This cohort has more than {MAX_AUDIT_STRATA} strata to audit."
        )
    cells = []
    if policy["mode"] == "closed":
        stored = {row["stratum"]: row for row in persisted}
        frozen = _frozen_population(task, cohort)
        for stratum in sorted(set(stored) | set(frozen)):
            row, current = stored.get(stratum), frozen.get(stratum)
            if row is None:
                population, selected = current
                cells.append(
                    {
                        "stratum": stratum,
                        "population": population,
                        "achieved": 0,
                        "probability": None,
                        "population_check": "match" if selected == 0 else "changed",
                    }
                )
                continue
            consistent = (
                row["population_min"] == row["population_max"]
                and row["probability_min"] == row["probability_max"]
            )
            cells.append(
                {
                    "stratum": stratum,
                    "population": row["population_max"],
                    "achieved": row["achieved"],
                    "probability": row["probability_max"],
                    "population_check": "match"
                    if consistent
                    and current == (row["population_max"], row["achieved"])
                    else "changed",
                }
            )
    else:
        for row in persisted:
            consistent = row["probability_min"] == row["probability_max"]
            cells.append(
                {
                    "stratum": row["stratum"],
                    "population": row["decided"],
                    "achieved": row["achieved"],
                    "probability": row["probability_max"],
                    "population_check": "persisted" if consistent else "changed",
                }
            )
    report = audit_report(policy, cells)
    report.update(
        {
            "cohort_id": str(cohort.id),
            "policy_version": cohort.policy_version,
            "mode": policy["mode"],
            "sampling_unit": policy["sampling_unit"],
            "coordination": policy.get("coordination", "independent"),
            "frozen_at": cohort.frozen_at if policy["mode"] == "closed" else None,
            "complete": cohort.complete,
            "population_source": "frozen_snapshot"
            if policy["mode"] == "closed"
            else "persisted_decisions",
            "notes": _notes(policy),
        }
    )
    return report


def _notes(policy: dict) -> list[str]:
    if policy["mode"] == "closed":
        return [
            "Population per stratum is re-read at the cohort's freeze time; "
            "'changed' means it no longer reproduces the persisted membership "
            "(deleted rows or identity merges since the freeze).",
            "Closed quotas are exact: achieved equals the expected size unless a "
            "stratum is flagged. Shortage is the quota a stratum could not fill "
            "because it had too few units.",
            "Unweighted sample shares differ from population shares by design "
            "when quotas oversample small strata; design weights restore them. "
            "A weighted imbalance means part of the population is unrepresented.",
        ]
    return [
        "Population per stratum is every unit decided since the policy start "
        "(selected and rejected first decisions).",
        f"A stratum is flagged when an exact binomial test of its realized "
        f"selections against its rate gives p < {stats.SRM_ALPHA}.",
    ]
