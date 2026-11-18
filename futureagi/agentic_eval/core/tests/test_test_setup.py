"""Regression coverage for the evaluator suite's standalone pytest setup."""

from pathlib import Path

from django.apps import apps
from django.conf import settings


def test_standalone_suite_loads_real_django_test_settings():
    assert settings.SETTINGS_MODULE == "tfc.settings.test"
    assert apps.ready
    assert apps.get_model("accounts", "AuditLog")._meta.app_label == "accounts"


def test_collection_preserves_the_agentic_eval_package_chain():
    assert __name__ == "agentic_eval.core.tests.test_test_setup"


def test_prompt_fixtures_have_a_distinct_package():
    from agentic_eval.core_evals import run_prompt
    from agentic_eval.core_evals.run_prompt.tests import conftest

    assert Path(run_prompt.__file__).name == "__init__.py"
    assert conftest.__name__ == "agentic_eval.core_evals.run_prompt.tests.conftest"


def test_live_provider_checks_are_opt_in(pytestconfig):
    options = pytestconfig.getini("addopts")
    expression = options[options.index("-m") + 1]
    for marker in ("live_llm", "live_image", "external", "network"):
        assert f"not {marker}" in expression
