"""
Regression tests for the per-attachment ``rule_prompt`` override in
``evaluations.engine.instance.create_eval_instance`` (TH-4908).

The override (``runtime_config['rule_prompt']`` from CustomEvalConfig.config /
UserEvalMetric.config) must win over the template default. Critically it must be
applied AFTER the function-eval ``config`` reassignments, otherwise a
``function_eval`` template silently discards the saved per-attachment override.

These exercise the real ``create_eval_instance`` (DB-touching helpers stubbed),
so they fail if the override is ever moved back above the reassignment.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from evaluations.engine import instance as engine_instance


class _CapturingEval:
    """Stand-in evaluator that records the kwargs it was constructed with."""

    def __init__(self, **kwargs):
        self.captured = kwargs


@pytest.fixture
def stub_db_helpers(monkeypatch):
    """Make create_eval_instance run without touching the DB / version model."""
    monkeypatch.setattr(engine_instance, "resolve_version", lambda *a, **k: None)
    monkeypatch.setattr(
        engine_instance,
        "prepare_eval_config",
        lambda eval_template, config, **k: (config, None),
    )
    monkeypatch.setattr(
        engine_instance,
        "apply_version_overrides",
        lambda config, version, criteria: (config, criteria),
    )


def _template(config):
    return SimpleNamespace(config=config, organization=None)


def _run(template_config, runtime_config):
    inst, _ = engine_instance.create_eval_instance(
        eval_class=_CapturingEval,
        eval_template=_template(template_config),
        config={},
        runtime_config=runtime_config,
    )
    return inst.captured


def test_override_survives_function_eval_reassignment(stub_db_helpers):
    # function_eval templates reassign `config` to template.config["config"];
    # the override must land AFTER that, not before (the original TH-4908 bug).
    captured = _run(
        template_config={
            "eval_type_id": "DeterministicEvaluator",
            "function_eval": True,
            "config": {"rule_prompt": "TEMPLATE DEFAULT"},
        },
        runtime_config={"rule_prompt": "CUSTOM FOR IFORM"},
    )
    assert captured["rule_prompt"] == "CUSTOM FOR IFORM"


def test_override_applies_for_non_function_eval(stub_db_helpers):
    captured = _run(
        template_config={"eval_type_id": "CustomPromptEvaluator"},
        runtime_config={"rule_prompt": "CUSTOM FOR IFORM"},
    )
    assert captured["rule_prompt"] == "CUSTOM FOR IFORM"


def test_empty_override_falls_back_to_template(stub_db_helpers):
    captured = _run(
        template_config={
            "eval_type_id": "DeterministicEvaluator",
            "function_eval": True,
            "config": {"rule_prompt": "TEMPLATE DEFAULT"},
        },
        runtime_config={"rule_prompt": "   "},
    )
    assert captured["rule_prompt"] == "TEMPLATE DEFAULT"


def test_no_runtime_config_keeps_template_default(stub_db_helpers):
    captured = _run(
        template_config={
            "eval_type_id": "DeterministicEvaluator",
            "function_eval": True,
            "config": {"rule_prompt": "TEMPLATE DEFAULT"},
        },
        runtime_config=None,
    )
    assert captured["rule_prompt"] == "TEMPLATE DEFAULT"
