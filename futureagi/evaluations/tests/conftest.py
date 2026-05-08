"""
Shared fixtures for evaluations engine tests.

All fixtures produce plain Python objects (SimpleNamespace / dict) —
no Django ORM, no database, no Docker required.
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _stub_django_deps():
    """
    Pre-stub the Django-dependent modules that the engine imports lazily
    (inside function bodies). This allows unit tests to run without Django.

    model_hub.utils.scoring is a pure-Python module but model_hub/utils/__init__.py
    imports Django. We bypass __init__ by injecting the module directly.
    """
    # Build a fake model_hub.utils.scoring that delegates to the real pure file
    import importlib.util
    import os

    scoring_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "model_hub", "utils", "scoring.py"
    )
    scoring_path = os.path.normpath(scoring_path)

    spec = importlib.util.spec_from_file_location("model_hub.utils.scoring", scoring_path)
    scoring_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scoring_mod)

    # Stub the package chain so the real __init__.py is never executed
    fake_model_hub = types.ModuleType("model_hub")
    fake_utils = types.ModuleType("model_hub.utils")
    fake_utils.scoring = scoring_mod

    sys.modules.setdefault("model_hub", fake_model_hub)
    sys.modules.setdefault("model_hub.utils", fake_utils)
    sys.modules.setdefault("model_hub.utils.scoring", scoring_mod)


_stub_django_deps()


def make_template(
    output_type="score",
    eval_type_id="CustomPromptEvaluator",
    choice_scores=None,
    multi_choice=False,
    criteria=None,
    name="test-eval",
):
    return SimpleNamespace(
        name=name,
        config={"eval_type_id": eval_type_id, "output": output_type},
        choice_scores=choice_scores or {},
        multi_choice=multi_choice,
        choices=[],
        criteria=criteria,
        organization=None,
    )


def make_result(
    output_type="score",
    failure=None,
    reason=None,
    data=None,
    metrics=None,
):
    return {
        "output": output_type,
        "failure": failure,
        "reason": reason,
        "data": data,
        "metrics": metrics or [],
        "model": "test-model",
        "runtime": 0.1,
        "metadata": {},
    }


@pytest.fixture
def template():
    return make_template


@pytest.fixture
def result():
    return make_result
