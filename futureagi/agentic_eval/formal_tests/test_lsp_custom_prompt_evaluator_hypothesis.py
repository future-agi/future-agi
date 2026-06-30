"""
Hypothesis property tests for the LSP contract of CustomPromptEvaluator (issue #315).

Tests that:
  1. CustomPromptEvaluator is a subclass of BaseEvaluator (post-fix)
  2. All abstract properties return values of the expected types
  3. metric_ids is a non-empty list of strings
  4. required_args is a list (may be empty — template-specific)
  5. examples is None or a list
  6. name and display_name are non-empty strings
  7. is_failure(result) returns bool or None

These tests load the module directly via importlib to avoid Django setup.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

_THIS_DIR = Path(__file__).resolve().parent
EVALUATOR_PATH = _THIS_DIR.parent / "core_evals/fi_evals/llm/custom_prompt_evaluator/evaluator.py"
if not EVALUATOR_PATH.exists():
    # Fallback for when test file is copied to /tmp
    EVALUATOR_PATH = Path("/Users/jonathanhill/src/future-agi/futureagi/agentic_eval/core_evals/fi_evals/llm/custom_prompt_evaluator/evaluator.py")


def _load_module_with_stubs() -> ModuleType:
    """Load evaluator.py with heavy Django/external dependencies stubbed out."""

    stubs = {
        "django": MagicMock(),
        "django.conf": MagicMock(),
        "django.conf.settings": MagicMock(DEBUG=False),
        "jinja2": MagicMock(),
        "structlog": MagicMock(),
        "agentic_eval.core.utils.json_utils": MagicMock(),
        "agentic_eval.core.utils.llm_payloads": MagicMock(),
        "agentic_eval.core.utils.model_config": MagicMock(),
        "agentic_eval.core_evals.fi_utils.evals_result": MagicMock(),
        "agentic_eval.core_evals.fi_utils.utils": MagicMock(),
        "tfc.ee_stub": MagicMock(_ee_stub=lambda name: MagicMock()),
        "tfc.telemetry": MagicMock(),
        "model_hub.utils": MagicMock(),
        "tfc.utils.storage": MagicMock(),
    }

    # Build LLM stub that doesn't need network
    llm_stub = MagicMock()
    llm_cls = type("LLM", (), {
        "__init__": lambda self, *a, **kw: None,
        "token_usage": {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0},
        "cost": {"total_cost": 0, "prompt_cost": 0, "completion_cost": 0},
        "temperature": 0.7,
        "max_tokens": 1024,
    })
    llm_stub.LLM = llm_cls
    stubs["agentic_eval.core.llm.llm"] = llm_stub

    # Build ModelConfigs stub
    mc_stub = MagicMock()
    mc_stub.ModelConfigs.is_turing = lambda m: False
    stubs["agentic_eval.core.utils.model_config"] = mc_stub

    # Build BaseEvaluator stub using the real ABC
    from abc import ABC, abstractmethod

    class _BaseEvaluator(ABC):
        @property
        @abstractmethod
        def name(self) -> str: ...
        @property
        @abstractmethod
        def display_name(self) -> str: ...
        @property
        @abstractmethod
        def metric_ids(self) -> list: ...
        @property
        @abstractmethod
        def required_args(self) -> list: ...
        @property
        @abstractmethod
        def examples(self): ...
        @abstractmethod
        def is_failure(self, *args): ...
        @abstractmethod
        def _evaluate(self, **kwargs): ...

    base_stub = MagicMock()
    base_stub.BaseEvaluator = _BaseEvaluator
    stubs["agentic_eval.core_evals.fi_evals.base_evaluator"] = base_stub

    # Build eval_type stub
    from enum import Enum
    class _LlmEvalTypeId(Enum):
        CUSTOM_PROMPT_EVAL = "CustomPromptEvaluator"
    eval_type_stub = MagicMock()
    eval_type_stub.LlmEvalTypeId = _LlmEvalTypeId
    stubs["agentic_eval.core_evals.fi_evals.eval_type"] = eval_type_stub

    # Patch all stubs into sys.modules
    for name, mod in stubs.items():
        sys.modules[name] = mod

    spec = importlib.util.spec_from_file_location("evaluator", EVALUATOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_module = _load_module_with_stubs()
CustomPromptEvaluator = _module.CustomPromptEvaluator


def _make_evaluator(**overrides):
    """Construct a minimal CustomPromptEvaluator with required args."""
    defaults = {
        "rule_prompt": "Evaluate: {{input}}",
        "model": "gpt-4o",
        "output_type": "Pass/Fail",
    }
    defaults.update(overrides)
    return CustomPromptEvaluator(**defaults)


# ── Structural contracts ──────────────────────────────────────────────────────

def test_is_subclass_of_base_evaluator():
    """CustomPromptEvaluator must inherit from BaseEvaluator (the ABC)."""
    from abc import ABC
    # Get the BaseEvaluator from the stub
    base = sys.modules["agentic_eval.core_evals.fi_evals.base_evaluator"].BaseEvaluator
    assert issubclass(CustomPromptEvaluator, base)


def test_is_subclass_of_llm():
    """CustomPromptEvaluator must still inherit from LLM for API call capabilities."""
    llm_cls = sys.modules["agentic_eval.core.llm.llm"].LLM
    assert issubclass(CustomPromptEvaluator, llm_cls)


# ── Abstract property contracts ───────────────────────────────────────────────

def test_name_is_non_empty_string():
    ev = _make_evaluator()
    assert isinstance(ev.name, str)
    assert len(ev.name) > 0


def test_display_name_is_non_empty_string():
    ev = _make_evaluator()
    assert isinstance(ev.display_name, str)
    assert len(ev.display_name) > 0


def test_metric_ids_is_non_empty_list_of_strings():
    ev = _make_evaluator()
    assert isinstance(ev.metric_ids, list)
    assert len(ev.metric_ids) > 0
    for mid in ev.metric_ids:
        assert isinstance(mid, str)


def test_required_args_is_list():
    """required_args can be empty (template-specific validation in _evaluate)."""
    ev = _make_evaluator()
    assert isinstance(ev.required_args, list)


def test_examples_is_none_or_list():
    ev = _make_evaluator()
    assert ev.examples is None or isinstance(ev.examples, list)


# ── is_failure contract ───────────────────────────────────────────────────────

def test_is_failure_returns_bool_or_none_for_fail():
    ev = _make_evaluator()
    result = ev.is_failure("Fail")
    assert isinstance(result, (bool, type(None)))


def test_is_failure_returns_bool_or_none_for_pass():
    ev = _make_evaluator()
    result = ev.is_failure("Pass")
    assert isinstance(result, (bool, type(None)))


def test_is_failure_false_for_pass():
    ev = _make_evaluator()
    assert ev.is_failure("Pass") is False or ev.is_failure("pass") is False


def test_is_failure_true_for_fail():
    ev = _make_evaluator()
    assert ev.is_failure("Fail") is True or ev.is_failure("fail") is True


# ── Cannot be instantiated without all abstract members ───────────────────────

def test_cannot_instantiate_partial_subclass():
    """A subclass missing metric_ids cannot be instantiated."""
    from abc import ABC
    base = sys.modules["agentic_eval.core_evals.fi_evals.base_evaluator"].BaseEvaluator
    llm_cls = sys.modules["agentic_eval.core.llm.llm"].LLM

    class Partial(base, llm_cls):
        @property
        def name(self): return "test"
        @property
        def display_name(self): return "Test"
        # metric_ids deliberately omitted
        @property
        def required_args(self): return []
        @property
        def examples(self): return None
        def is_failure(self, *a): return False
        def _evaluate(self, **kw): return {}

    with pytest.raises(TypeError):
        Partial()
