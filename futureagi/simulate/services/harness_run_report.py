"""The run report of a composition's run (`api_contracts/harness/environment-releases.md` §7.7).

Everything comes from immutable records: the run's frozen manifest, the composition's releases
and binding, and one receipt per scenario trial.
"""

from __future__ import annotations

from typing import Any

from simulate.models import (
    HarnessEnvironmentRelease,
    HarnessRunManifest,
    HarnessSuiteRelease,
    HostedHarnessJob,
    HostedHarnessReceipt,
)
from simulate.services.harness_releases import _visible, composition_of

MANIFEST_FIELDS = (
    "digest",
    "environment",
    "suite",
    "binding",
    "agent",
    "selection",
    "simulator",
    "scorer",
    "runtime",
)
# Contract §5, the same rule as `fi.alk.harness.releases.compare`.
COMPARABLE_FIELDS = ("environment", "suite", "simulator", "scorer", "runtime")
ENVIRONMENT_FAULT = "errored"


def run_manifest(run: HostedHarnessJob) -> dict[str, Any] | None:
    row = HarnessRunManifest.no_workspace_objects.filter(job=run).first()
    return row.document if row is not None else None


def differing_fields(first: dict[str, Any], second: dict[str, Any]) -> list[str]:
    differing = [
        field for field in COMPARABLE_FIELDS if first.get(field) != second.get(field)
    ]
    if (first.get("selection") or {}).get("scenario_keys") != (
        second.get("selection") or {}
    ).get("scenario_keys"):
        differing.append("selection.scenario_keys")
    return differing


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _root(suite: HarnessSuiteRelease) -> HarnessSuiteRelease:
    while suite.parent_id:
        suite = suite.parent
    return suite


def _coverage(
    organization, composition: dict[str, Any], suite: HarnessSuiteRelease
) -> dict[str, Any]:
    keys = [entry["key"] for entry in suite.manifest["scenarios"]]
    unscorable = [
        entry["scenario_key"]
        for entry in (composition.get("alignment") or {}).get("unscorable") or []
    ]
    chain = composition["environment"]
    extension = (
        _visible(HarnessEnvironmentRelease, organization, chain[-1]["digest"])
        if len(chain) > 1
        else None
    )
    return {
        "runnable": sum(1 for key in keys if key not in unscorable),
        "suite_scenarios": len(keys),
        "base_scenarios": len(_root(suite).manifest["scenarios"]),
        "unscorable": unscorable,
        "extension_tools": (
            [tool["name"] for tool in extension.tools] if extension else []
        ),
    }


def _scenario(
    key: str,
    entry: dict[str, Any],
    persona: dict[str, Any],
    statuses: dict[int, str],
    trials: int,
) -> dict[str, Any]:
    outcomes = [statuses.get(index) for index in range(1, trials + 1)]
    passed = outcomes.count("passed")
    failed = outcomes.count("failed")
    return {
        "scenario_key": key,
        "name": entry["path"].rsplit("/", 1)[-1],
        "use_case": persona.get("scenario_name") or "",
        "origin": entry["origin"],
        "trials": outcomes,
        "pass_1": _rate(passed, passed + failed),
        "passed_all": passed == trials,
        "_passed": passed,
        "_failed": failed,
        "_faulted": ENVIRONMENT_FAULT in outcomes,
    }


def _summary(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(one["_passed"] for one in scenarios)
    failed = sum(one["_failed"] for one in scenarios)
    eligible = [one for one in scenarios if not one["_faulted"]]
    return {
        "scenarios": len(scenarios),
        "pass_1": _rate(passed, passed + failed),
        "pass_k": _rate(sum(one["passed_all"] for one in eligible), len(eligible)),
    }


def run_report(
    environment: HostedHarnessJob,
    run: HostedHarnessJob,
    manifest: dict[str, Any],
    *,
    baseline_run_id: str | None = None,
    baseline_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The report of `run`, a run of the composition `environment` frozen as `manifest`.

    A trial without a receipt yet is `null`: it is neither passed nor failed, so it stays out of
    `pass_1`, and its scenario has not passed all of its trials.

    Read from what the run froze, never the composition's current state: a later chat edit gives
    the composition another suite, which must not change an earlier run's scenarios or coverage.
    """
    # The composition as it stood when this run was submitted, copied onto the run job.
    composition = composition_of(run)
    suite = _visible(
        HarnessSuiteRelease, environment.organization, manifest["suite"]["digest"]
    )
    by_key = {entry["key"]: entry for entry in suite.manifest["scenarios"]}
    personas = {persona["scenario_key"]: persona for persona in suite.personas}
    trials = manifest["selection"]["trials"]
    statuses: dict[str, dict[int, str]] = {}
    for key, trial_index, outcome in HostedHarnessReceipt.no_workspace_objects.filter(
        job=run, execution__isnull=False
    ).values_list(
        "execution__source_scenario__scenario_key", "execution__trial_index", "status"
    ):
        statuses.setdefault(key, {})[trial_index] = outcome
    scenarios = [
        _scenario(key, by_key[key], personas[key], statuses.get(key, {}), trials)
        for key in manifest["selection"]["scenario_keys"]
    ]
    use_cases: dict[str, list[dict[str, Any]]] = {}
    for one in scenarios:
        use_cases.setdefault(one["use_case"], []).append(one)
    comparability = None
    if baseline_run_id is not None:
        differing = differing_fields(manifest, baseline_manifest)
        comparability = {
            "baseline_run_id": baseline_run_id,
            "comparable": not differing,
            "differing": differing,
        }
    return {
        "run_mode": composition["binding"]["mode"],
        "manifest": {field: manifest[field] for field in MANIFEST_FIELDS},
        "comparability": comparability,
        "coverage": _coverage(environment.organization, composition, suite),
        "use_cases": [
            {"use_case": use_case, "trials": trials, **_summary(members)}
            for use_case, members in use_cases.items()
        ],
        "scenarios": [
            {key: value for key, value in one.items() if not key.startswith("_")}
            for one in scenarios
        ],
        "environment_faults": sum(
            one["trials"].count(ENVIRONMENT_FAULT) for one in scenarios
        ),
    }
