"""Standalone runner for the CustomPromptEvaluator live matrix.

The pytest collection path for ``fi_evals/__init__.py`` has a pre-existing
relative-import + Django app-registry conflict that intermittently breaks
collection under ``DJANGO_SETTINGS_MODULE=tfc.settings.test``. This
script bootstraps Django explicitly with the production settings module
and walks the same scenario list directly, writing the same CSV/JSON
report files as the pytest equivalent.

Run from inside the backend container::

    docker exec -e RUN_LIVE_EVAL_TESTS=1 backend bash -c \\
        "cd /app/backend && python -m agentic_eval.core.utils.tests.run_live_cpe_matrix"
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any


def _bootstrap_django() -> None:
    """Mirror the same bootstrap that ``manage.py shell`` performs so the
    full app registry is populated before any model class is touched."""
    os.environ["DJANGO_SETTINGS_MODULE"] = "tfc.settings.settings"
    import django  # noqa: PLC0415

    django.setup()


def main() -> int:
    if os.environ.get("RUN_LIVE_EVAL_TESTS") != "1":
        print(
            "ERROR: RUN_LIVE_EVAL_TESTS=1 must be set. This script makes "
            "real LLM calls.",
            file=sys.stderr,
        )
        return 2

    # Bootstrap Django FIRST, before any importing of CPE happens.
    _bootstrap_django()

    # Now safe to import the scenarios from the pytest module.
    from agentic_eval.core.utils.tests.test_live_cpe_matrix import (
        CORE_SCENARIOS,
        CROSS_MODALITY_SCENARIOS,
        CROSS_MODEL_BASES,
        TURING_MODELS,
        Scenario,
    )
    from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
        CustomPromptEvaluator,
    )

    results: list[dict] = []

    def _run(model: str, scenario: Scenario) -> dict | None:
        ev = CustomPromptEvaluator(
            rule_prompt=scenario.rule_prompt,
            model=model,
            output_type=scenario.output_type,
            choices=scenario.choices or [],
            multi_choice=scenario.multi_choice,
            pass_threshold=scenario.pass_threshold,
            reverse_output=scenario.reverse_output,
            choice_scores=scenario.choice_scores,
        )
        evaluate_kwargs = {
            "required_keys": scenario.required_keys,
            **scenario.inputs,
            **scenario.extra_evaluate_kwargs,
        }
        return ev._evaluate(**evaluate_kwargs)

    def _record(scenario: Scenario, model: str, result: dict | None, error: BaseException | None) -> bool:
        actual_failure: Any = None
        actual_value: Any = None
        runtime: Any = None
        if result is not None:
            actual_failure = result.get("failure")
            actual_value = result.get("data", {}).get("result")
            runtime = result.get("runtime")

        passed = error is None
        failure_reasons: list[str] = []
        if error is not None:
            failure_reasons.append(f"raised: {type(error).__name__}")
        else:
            if scenario.expected_failure is not None and actual_failure != scenario.expected_failure:
                passed = False
                failure_reasons.append(f"failure={actual_failure} expected={scenario.expected_failure}")
            if scenario.expected_result_in is not None:
                valid = {str(c).strip().lower() for c in scenario.expected_result_in}
                if isinstance(actual_value, list):
                    bad = [a for a in actual_value if str(a).strip().lower() not in valid]
                    if bad:
                        failure_reasons.append(f"out_of_set={bad}")
                else:
                    if str(actual_value).strip().lower() not in valid:
                        failure_reasons.append(f"out_of_set={actual_value!r}")
            if scenario.score_must_be_in_unit_range:
                try:
                    f = float(actual_value)
                    if not (0.0 <= f <= 1.0):
                        passed = False
                        failure_reasons.append(f"score_out_of_range={f}")
                except (TypeError, ValueError):
                    passed = False
                    failure_reasons.append(f"score_unparseable={actual_value!r}")

        results.append({
            "scenario_id": scenario.id,
            "model": model,
            "output_type": scenario.output_type,
            "multi_choice": scenario.multi_choice,
            "reverse_output": scenario.reverse_output,
            "pass_threshold": scenario.pass_threshold,
            "expected_failure": scenario.expected_failure,
            "actual_failure": actual_failure,
            "result_value": str(actual_value)[:300] if actual_value is not None else None,
            "runtime_ms": runtime,
            "passed": passed,
            "failure_reasons": ";".join(failure_reasons) or None,
            "error": (f"{type(error).__name__}: {error}")[:500] if error else None,
            "notes": scenario.notes,
        })
        return passed

    def _execute(scenario: Scenario, model: str) -> None:
        result: dict | None = None
        error: BaseException | None = None
        try:
            result = _run(model, scenario)
        except BaseException as e:  # noqa: BLE001 — record everything
            error = e
        passed = _record(scenario, model, result, error)
        status = "PASS" if passed else "FAIL"
        print(
            f"  [{status}] {scenario.id} on {model}: "
            f"result={result.get('data', {}).get('result') if result else None}  "
            f"failure={result.get('failure') if result else None}  "
            f"runtime={result.get('runtime') if result else 'n/a'}ms",
            flush=True,
        )

    suite_start = time.time()

    print(f"=== CORE matrix on turing_small ({len(CORE_SCENARIOS)} scenarios) ===")
    for scenario in CORE_SCENARIOS:
        _execute(scenario, "turing_small")

    print(
        f"\n=== CROSS-MODEL matrix "
        f"({len(CROSS_MODEL_BASES)} scenarios x {len(TURING_MODELS)} models) ==="
    )
    for scenario in CROSS_MODEL_BASES:
        for model in TURING_MODELS:
            _execute(scenario, model)

    print(
        f"\n=== CROSS-MODALITY matrix on turing_large_xl "
        f"({len(CROSS_MODALITY_SCENARIOS)} scenarios) ==="
    )
    for scenario in CROSS_MODALITY_SCENARIOS:
        _execute(scenario, "turing_large_xl")

    total_time = time.time() - suite_start
    passed = sum(1 for r in results if r["passed"])
    total = len(results)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = Path(f"/tmp/cpe_matrix_{ts}.json")
    csv_path = Path(f"/tmp/cpe_matrix_{ts}.csv")
    json_path.write_text(json.dumps(results, indent=2, default=str))
    if results:
        keys = list(results[0].keys())
        with csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            for row in results:
                writer.writerow(row)

    print(
        f"\n=== CPE matrix summary: {passed}/{total} passed in {total_time:.1f}s ===\n"
        f"CSV : {csv_path}\nJSON: {json_path}"
    )
    return 0 if passed == total else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(3)
