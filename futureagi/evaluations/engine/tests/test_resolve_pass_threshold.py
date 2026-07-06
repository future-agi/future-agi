"""Tests for `evaluations.engine.instance.resolve_pass_threshold`.

Pins the layered resolution used by both the engine (for evaluator instantiation)
and the error-localizer trigger sites (for EL Pass/Fail gating). Single source of
truth for pass_threshold semantics.

Priority (highest to lowest):
  1. runtime_config["run_config"]["pass_threshold"]  - picker override
  2. runtime_config["pass_threshold"]                 - flat top-level
  3. resolved_version.pass_threshold                  - version snapshot
  4. eval_template.pass_threshold                     - template default
  5. 0.5                                              - hard fallback
"""

from __future__ import annotations

from types import SimpleNamespace

from evaluations.engine.instance import resolve_pass_threshold


def _template(**overrides):
    defaults = {"pass_threshold": 0.5}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _version(**overrides):
    defaults = {"pass_threshold": 0.7}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# Layer 1 — run_config picker


def test_run_config_pass_threshold_wins_over_everything():
    template = _template(pass_threshold=0.5)
    version = _version(pass_threshold=0.7)
    runtime_config = {"run_config": {"pass_threshold": 0.9}, "pass_threshold": 0.8}
    assert (
        resolve_pass_threshold(template, runtime_config, version) == 0.9
    )


def test_run_config_zero_is_honoured():
    template = _template(pass_threshold=0.5)
    runtime_config = {"run_config": {"pass_threshold": 0}}
    assert resolve_pass_threshold(template, runtime_config) == 0.0


# Layer 2 — flat top-level (tracing / composite / SDK)


def test_flat_top_level_wins_over_version_and_template():
    template = _template(pass_threshold=0.5)
    version = _version(pass_threshold=0.7)
    runtime_config = {"pass_threshold": 0.8}
    assert resolve_pass_threshold(template, runtime_config, version) == 0.8


def test_flat_top_level_ignored_when_run_config_present():
    template = _template(pass_threshold=0.5)
    runtime_config = {"run_config": {"pass_threshold": 0.9}, "pass_threshold": 0.8}
    assert resolve_pass_threshold(template, runtime_config) == 0.9


# Layer 3 — version


def test_version_wins_over_template_when_no_runtime_override():
    template = _template(pass_threshold=0.5)
    version = _version(pass_threshold=0.7)
    assert resolve_pass_threshold(template, None, version) == 0.7


def test_version_none_falls_through_to_template():
    template = _template(pass_threshold=0.5)
    assert resolve_pass_threshold(template, None, None) == 0.5


def test_version_without_pass_threshold_attribute_falls_through():
    template = _template(pass_threshold=0.5)
    version = SimpleNamespace()  # no pass_threshold attr
    assert resolve_pass_threshold(template, None, version) == 0.5


# Layer 4 — template default


def test_template_default_when_no_runtime_no_version():
    template = _template(pass_threshold=0.6)
    assert resolve_pass_threshold(template, None) == 0.6


# Layer 5 — hard fallback


def test_hard_fallback_when_everything_missing():
    template = _template(pass_threshold=None)
    assert resolve_pass_threshold(template, None) == 0.5


# Defensive input shapes


def test_non_dict_runtime_config_ignored():
    template = _template(pass_threshold=0.5)
    assert resolve_pass_threshold(template, "not a dict") == 0.5


def test_empty_run_config_dict_falls_through():
    template = _template(pass_threshold=0.5)
    runtime_config = {"run_config": {}}
    assert resolve_pass_threshold(template, runtime_config) == 0.5


def test_run_config_none_value_falls_through():
    template = _template(pass_threshold=0.5)
    runtime_config = {"run_config": {"pass_threshold": None}}
    assert resolve_pass_threshold(template, runtime_config) == 0.5
