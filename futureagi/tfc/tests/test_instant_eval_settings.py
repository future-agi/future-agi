"""Jev and Instant eval limits read from the environment are clamped, and the
Users count budgets are real settings (not only getattr defaults)."""

import json
import os
import subprocess
import sys

NAMES = [
    "JEV_MAX_CONCURRENT",
    "INSTANT_EVAL_MAX_ROWS",
    "INSTANT_EVAL_CONFIRM_ROWS",
    "INSTANT_EVAL_MAX_ACTIVE_PER_ORG",
    "INSTANT_EVAL_ASSUMED_ROWS_PER_MIN",
    "INSTANT_EVAL_USERS_ESTIMATE_BUDGET_S",
    "INSTANT_EVAL_USERS_RUN_BUDGET_S",
]


def _settings_with(env):
    """The settings module's values, imported fresh with ``env`` set."""
    code = (
        "import importlib, json, os;"
        "m = importlib.import_module(os.environ['DJANGO_SETTINGS_MODULE']);"
        f"print(json.dumps({{n: getattr(m, n) for n in {NAMES!r}}}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_zero_or_negative_limits_are_clamped_and_budgets_are_read():
    values = _settings_with(
        {
            "JEV_MAX_CONCURRENT": "0",
            "INSTANT_EVAL_MAX_ROWS": "0",
            "INSTANT_EVAL_CONFIRM_ROWS": "-5",
            "INSTANT_EVAL_MAX_ACTIVE_PER_ORG": "0",
            "INSTANT_EVAL_ASSUMED_ROWS_PER_MIN": "0",
            "INSTANT_EVAL_USERS_ESTIMATE_BUDGET_S": "7",
            "INSTANT_EVAL_USERS_RUN_BUDGET_S": "100000",
        }
    )
    assert values["JEV_MAX_CONCURRENT"] == 1
    assert values["INSTANT_EVAL_MAX_ROWS"] == 1
    assert values["INSTANT_EVAL_CONFIRM_ROWS"] == 1
    assert values["INSTANT_EVAL_MAX_ACTIVE_PER_ORG"] == 1
    assert values["INSTANT_EVAL_ASSUMED_ROWS_PER_MIN"] == 1.0
    assert values["INSTANT_EVAL_USERS_ESTIMATE_BUDGET_S"] == 7.0
    assert values["INSTANT_EVAL_USERS_RUN_BUDGET_S"] == 600.0
