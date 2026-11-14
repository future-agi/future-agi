"""Deployment environment overrides reach the sampling runtime, not just specs."""

import ast
import os
from datetime import timedelta
from pathlib import Path

import pytest

from tfc.settings.runtime_setting_specs import (
    RUNTIME_NUMERIC_SETTING_SPECS,
    load_numeric_settings,
    validate_runtime_numeric_settings,
)

OVERRIDES = {
    "EVAL_SAMPLING_CLOSED_SETTLE_SECONDS": 60,
    "EVAL_SAMPLING_LEASE_SECONDS": 120,
    "EVAL_SAMPLING_LIVE_PROOF_MAX_DAYS": 30,
    "EVAL_SAMPLING_LIVE_PROOF_MAX_ROWS": 1000,
    "EVAL_SAMPLING_PREVIEW_CONCURRENCY": 2,
    "EVAL_SAMPLING_PREVIEW_PER_MINUTE": 20,
}


def production_numeric_settings():
    # Execute the real production loader statements without importing its
    # database/credential configuration. No environment values are logged.
    source = Path(__file__).resolve().parents[2] / "tfc/settings/settings.py"
    tree = ast.parse(source.read_text())
    statements = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_runtime_numeric_settings"
            for target in node.targets
        ):
            statements.append(node)
        elif isinstance(node, ast.Expr) and ast.unparse(node).startswith(
            (
                "_validate_runtime_numeric_settings(",
                "globals().update(_runtime_numeric_settings)",
            )
        ):
            statements.append(node)
    assert len(statements) == 3
    namespace = {
        "os": os,
        "_load_numeric_settings": load_numeric_settings,
        "_runtime_numeric_setting_specs": RUNTIME_NUMERIC_SETTING_SPECS,
        "_validate_runtime_numeric_settings": validate_runtime_numeric_settings,
    }
    exec(
        compile(ast.Module(body=statements, type_ignores=[]), str(source), "exec"),
        namespace,
    )
    return namespace


def test_sampling_environment_overrides_reach_effective_runtime(monkeypatch, settings):
    from tracer.services.eval_tasks.sampling.preview_guard import _limit
    from tracer.services.eval_tasks.sampling.query import (
        live_proof_max_lookback,
        live_proof_settings,
    )
    from tracer.services.eval_tasks.sampling.service import (
        closed_settle_margin,
        reconcile_lease_duration,
    )

    for name, value in OVERRIDES.items():
        monkeypatch.setenv(name, str(value))
    loaded = production_numeric_settings()
    for name, value in OVERRIDES.items():
        assert loaded[name] == value
        setattr(settings, name, loaded[name])
    assert closed_settle_margin() == timedelta(seconds=60)
    assert reconcile_lease_duration() == timedelta(seconds=120)
    assert live_proof_max_lookback() == timedelta(days=30)
    assert live_proof_settings() == {"max_rows_to_read": 1000}
    assert _limit("EVAL_SAMPLING_PREVIEW_CONCURRENCY", 1, low=1, high=10) == 2
    assert _limit("EVAL_SAMPLING_PREVIEW_PER_MINUTE", 12, low=1, high=600) == 20


@pytest.mark.parametrize("name", OVERRIDES)
@pytest.mark.parametrize("invalid", ["not-an-integer", "below", "above"])
def test_sampling_environment_rejects_invalid_values(monkeypatch, name, invalid):
    spec = RUNTIME_NUMERIC_SETTING_SPECS[name]
    value = {"below": spec.minimum - 1, "above": spec.maximum + 1}.get(invalid, invalid)
    monkeypatch.setenv(name, str(value))
    with pytest.raises(ValueError, match=name):
        production_numeric_settings()
