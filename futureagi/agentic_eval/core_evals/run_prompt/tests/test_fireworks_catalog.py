"""Offline regressions for the Fireworks serverless catalog (issue #3221).

This file also runs directly with Python's standard library, without Django,
pytest plugins, provider credentials, or network access.
"""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


MODULE_DIR = Path(__file__).resolve().parents[1]
FIREWORKS_MODEL = "fireworks_ai/accounts/fireworks/models/gpt-oss-120b"
RETIRED_MODELS = (
    "deepseek-coder-v2-instruct",
    "firefunction-v2",
    "llama-v3p2-1b-instruct",
    "llama-v3p2-3b-instruct",
    "llama-v3p2-11b-vision-instruct",
    "mixtral-8x22b-instruct-hf",
    "qwen2-72b-instruct",
    "yi-large",
)


def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, MODULE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFireworksCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Exercise the real public catalog without optional private additions.
        with patch.dict(sys.modules, {"ee.prompts.additional_models": None}):
            cls.catalog = _load_module("fireworks_catalog", "available_models.py")
        with patch.dict(
            sys.modules,
            {
                "agentic_eval.core_evals.run_prompt.available_models": cls.catalog,
            },
        ):
            cls.pricing = _load_module("fireworks_pricing", "model_pricing.py")

    def test_retired_serverless_models_are_not_offered(self):
        for catalog in (
            self.catalog.OSS_AVAILABLE_MODELS,
            self.catalog.AVAILABLE_MODELS,
        ):
            names = {
                model["model_name"].removeprefix("fireworks_ai/")
                for model in catalog
                if model["providers"] == "fireworks_ai"
            }
            for retired in RETIRED_MODELS:
                with self.subTest(model=retired):
                    self.assertNotIn(f"accounts/fireworks/models/{retired}", names)

    def test_reported_working_model_has_fireworks_routing(self):
        for catalog in (
            self.catalog.OSS_AVAILABLE_MODELS,
            self.catalog.AVAILABLE_MODELS,
        ):
            matches = [m for m in catalog if m["model_name"] == FIREWORKS_MODEL]
            self.assertEqual(len(matches), 1)
            model = matches[0]
            self.assertEqual(model["providers"], "fireworks_ai")
            self.assertEqual(model["api_key_name"], "FIREWORKS_AI_API_KEY")
            self.assertEqual(model["mode"], "chat")

    def test_unknown_fireworks_pricing_does_not_use_another_provider(self):
        # The issue supplies no Fireworks rates. In particular, the unprefixed
        # OpenAI entry's zero-price placeholder is not Fireworks pricing.
        self.assertIsNone(self.pricing.get_model_pricing(FIREWORKS_MODEL))
        self.assertEqual(
            self.pricing.get_model_info(FIREWORKS_MODEL)["providers"], "fireworks_ai"
        )


if __name__ == "__main__":
    unittest.main()
