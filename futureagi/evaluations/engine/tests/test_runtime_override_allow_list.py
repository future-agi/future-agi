"""
Tests for the per-evaluator runtime override allow-list in
``evaluations.engine.instance`` (TH-4787).

Pins three behaviours per evaluator type:
  - run_config override on an allow-listed key wins over the template default
  - run_config override on a non-allow-listed key is silently dropped
  - no run_config falls back to the template default
"""

from __future__ import annotations

import pytest


def _merge(eval_type_id, base_config, runtime_config):
    """Mirror of the merge step in ``create_eval_instance``."""
    from evaluations.engine.instance import _RUNTIME_ALLOWED_KEYS

    cfg = dict(base_config)
    allowed = _RUNTIME_ALLOWED_KEYS.get(eval_type_id) or set()
    overrides = (runtime_config or {}).get("run_config") or {}
    for key, value in overrides.items():
        if key in allowed and value is not None:
            cfg[key] = value
    return cfg


def test_runtime_allowed_keys_dict_exposed():
    from evaluations.engine.instance import _RUNTIME_ALLOWED_KEYS

    assert "AgentEvaluator" in _RUNTIME_ALLOWED_KEYS
    assert "CustomPromptEvaluator" in _RUNTIME_ALLOWED_KEYS
    assert "data_injection" in _RUNTIME_ALLOWED_KEYS["AgentEvaluator"]
    assert "data_injection" not in _RUNTIME_ALLOWED_KEYS["CustomPromptEvaluator"]


class TestAgentEvaluatorMerge:
    def test_run_config_override_wins(self):
        cfg = _merge(
            "AgentEvaluator",
            base_config={"data_injection": {"variables_only": True}},
            runtime_config={
                "run_config": {"data_injection": {"trace_context": True}}
            },
        )
        assert cfg["data_injection"] == {"trace_context": True}

    def test_unknown_key_dropped(self):
        cfg = _merge(
            "AgentEvaluator",
            base_config={"check_internet": False},
            runtime_config={
                "run_config": {
                    "check_internet": True,
                    "some_unknown_kwarg": "should_be_ignored",
                }
            },
        )
        assert cfg["check_internet"] is True
        assert "some_unknown_kwarg" not in cfg

    def test_no_run_config_preserves_template_default(self):
        cfg = _merge(
            "AgentEvaluator",
            base_config={"pass_threshold": 0.5},
            runtime_config={},
        )
        assert cfg["pass_threshold"] == 0.5

    def test_explicit_false_overrides_template_true(self):
        cfg = _merge(
            "AgentEvaluator",
            base_config={"check_internet": True},
            runtime_config={"run_config": {"check_internet": False}},
        )
        assert cfg["check_internet"] is False

    def test_explicit_none_does_not_override(self):
        cfg = _merge(
            "AgentEvaluator",
            base_config={"pass_threshold": 0.7},
            runtime_config={"run_config": {"pass_threshold": None}},
        )
        assert cfg["pass_threshold"] == 0.7


class TestCustomPromptEvaluatorMerge:
    def test_check_internet_override_applies(self):
        cfg = _merge(
            "CustomPromptEvaluator",
            base_config={"check_internet": False},
            runtime_config={"run_config": {"check_internet": True}},
        )
        assert cfg["check_internet"] is True

    def test_pass_threshold_override_applies(self):
        cfg = _merge(
            "CustomPromptEvaluator",
            base_config={"pass_threshold": 0.5},
            runtime_config={"run_config": {"pass_threshold": 0.8}},
        )
        assert cfg["pass_threshold"] == 0.8

    def test_choice_scores_override_applies(self):
        cfg = _merge(
            "CustomPromptEvaluator",
            base_config={"choice_scores": {"Yes": 1.0}},
            runtime_config={
                "run_config": {"choice_scores": {"Good": 1.0, "Bad": 0.0}}
            },
        )
        assert cfg["choice_scores"] == {"Good": 1.0, "Bad": 0.0}

    def test_agent_only_keys_dropped_on_llm_judge(self):
        cfg = _merge(
            "CustomPromptEvaluator",
            base_config={},
            runtime_config={
                "run_config": {
                    "data_injection": {"trace_context": True},
                    "agent_mode": "agent",
                    "tools": {},
                    "summary": {"type": "concise"},
                }
            },
        )
        for k in ("data_injection", "agent_mode", "tools", "summary"):
            assert k not in cfg

    def test_model_not_in_allow_list(self):
        from evaluations.engine.instance import _RUNTIME_ALLOWED_KEYS

        assert "model" not in _RUNTIME_ALLOWED_KEYS["CustomPromptEvaluator"]


def test_unknown_evaluator_type_drops_all_overrides():
    cfg = _merge(
        "FutureUnknownEvaluator",
        base_config={"check_internet": False},
        runtime_config={"run_config": {"check_internet": True}},
    )
    assert cfg["check_internet"] is False
