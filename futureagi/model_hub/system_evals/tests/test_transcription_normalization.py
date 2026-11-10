"""Opt-in transcription preprocessing contracts, without Django or services.

Also runnable with ``python test_transcription_normalization.py``. The small
stdlib-only subset runs without the application's third-party dependencies;
PyYAML and Levenshtein enable schema and word-information coverage when present.
"""

from __future__ import annotations

import ast
import builtins
import importlib.util
import textwrap
import unittest
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
SDK_PATH = ROOT / "agentic_eval/core_evals/fi_evals/function/functions.py"
NAMES = (
    "word_error_rate",
    "character_error_rate",
    "match_error_rate",
    "word_info_lost",
    "word_info_preserved",
)
HAS_LEVENSHTEIN = importlib.util.find_spec("Levenshtein") is not None
HAS_YAML = importlib.util.find_spec("yaml") is not None


@cache
def _yaml_eval(name):
    path = FUNCTION_DIR / f"{name}.yaml"
    # These templates end with a literal code block. Full YAML/schema parsing
    # is separately covered below when the application's PyYAML is available.
    code = textwrap.dedent(path.read_text().split("  code: |\n", 1)[1])
    namespace = {}
    builtins.exec(compile(code, str(path), "exec"), namespace)
    return namespace["evaluate"]


@cache
def _sdk_functions():
    # Execute the real pure functions, without importing the SDK's service clients.
    source = ast.parse(SDK_PATH.read_text())
    names = {f"calculate_{name}" for name in NAMES} | {
        "_transcription_tokens", "_levenshtein_distance_list", "_align_counts"
    }
    module = ast.Module(
        body=[node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names],
        type_ignores=[],
    )
    namespace = {}
    if HAS_LEVENSHTEIN:
        import Levenshtein
        namespace["Levenshtein"] = Levenshtein
    builtins.exec(compile(module, str(SDK_PATH), "exec"), namespace)
    return namespace


class TranscriptionContract:
    """Shared table-driven tests for both public implementations of each metric."""

    @classmethod
    def setUpClass(cls):
        if not HAS_LEVENSHTEIN and (
            cls.name.startswith("word_info_")
            or (cls.implementation == "sdk" and cls.name == "character_error_rate")
        ):
            raise unittest.SkipTest("Levenshtein is not installed")

    def score(self, reference, hypothesis, **options):
        if self.implementation == "yaml":
            return _yaml_eval(self.name)(None, hypothesis, reference, None, **options)["score"]
        return _sdk_functions()[f"calculate_{self.name}"](reference, hypothesis, **options)["result"]

    def test_formatting_only_changes_are_not_recognition_errors(self):
        pairs = [
            ("hello how are you today", "Hello, how are you today?"),
            ("今日は良い天気です", "今日は良い天気です。"),
            ("今日は良い天気です", "今日 は 良い 天気 です"),
            ("予約番号は1234です", "予約番号は１２３４です"),
            ("我们明天见", "我们，明天见。"),
            ("안녕하세요", "안녕 하세요！"),
            ("book two 予約", "ＢＯＯＫ　two，予 約。"),
            ("Straße", "STRASSE!"),
            ("スーパー", "「スーパー」。"),
        ]
        mode = "char" if self.name == "character_error_rate" else "auto"
        for reference, hypothesis in pairs:
            with self.subTest(reference=reference, hypothesis=hypothesis):
                self.assertEqual(self.score(reference, hypothesis, normalize=True, tokenize=mode), 1.0)

    def test_real_cjk_error_is_still_counted(self):
        expected = (8 / 9) ** 2 if self.name.startswith("word_info_") else 8 / 9
        self.assertAlmostEqual(self.score(
            "今日は良い天気です", "今日は悪い天気です", normalize=True, tokenize="auto"
        ), expected)

    def test_real_latin_error_keeps_word_and_character_denominators(self):
        expected = {"word_error_rate": 0.8, "character_error_rate": 15 / 16,
                    "match_error_rate": 0.8, "word_info_lost": 0.64, "word_info_preserved": 0.64}[self.name]
        mode = "char" if self.name == "character_error_rate" else "auto"
        self.assertAlmostEqual(self.score(
            "book a table for two", "book a cable for two", normalize=True, tokenize=mode
        ), expected)

    def test_omitted_options_preserve_legacy_scores(self):
        expected = {"word_error_rate": 0.6, "character_error_rate": 1 - 2 / 23,
                    "match_error_rate": 0.6, "word_info_lost": 0.36, "word_info_preserved": 0.36}[self.name]
        for options in ({}, {"normalize": False, "tokenize": None}):
            with self.subTest(options=options):
                self.assertAlmostEqual(self.score(
                    "hello how are you today", "Hello, how are you today?", **options
                ), expected)
        spacing = 1 - 4 / 9 if self.name == "character_error_rate" else 0.0
        self.assertAlmostEqual(self.score("今日は良い天気です", "今日 は 良い 天気 です"), spacing)

    def test_tokenization_modes_are_independent_of_normalization(self):
        self.assertEqual(self.score("你好 world", "你 好 world", tokenize="auto"), 1.0)
        self.assertEqual(self.score("ab cd", "a bcd", tokenize="char"), 1.0)
        self.assertEqual(self.score("你好", "你 好", tokenize="word"), 0.0)
        self.assertEqual(self.score("你好", "你 好", normalize=True, tokenize="word"), 0.0)
        self.assertEqual(self.score("hello!", "hello", tokenize="word"), 0.0)
        self.assertEqual(self.score("hello!", "hello", normalize=True, tokenize="word"), 1.0)

    def test_auto_keeps_latin_words_and_splits_cjk_extensions(self):
        expected = 0.25 if self.name.startswith("word_info_") else 0.5
        self.assertAlmostEqual(self.score("hello界", "hallo界", tokenize="auto"), expected)
        for text in ("㐀界", "𠀀界", "ひらがな", "カタカナ", "한글", "한", "ﾊﾛｰ"):
            with self.subTest(text=text):
                self.assertEqual(self.score(text, " ".join(text), tokenize="auto"), 1.0)
        self.assertEqual(self.score("café déjà", "café déjà", normalize=True, tokenize="auto"), 1.0)
        self.assertEqual(self.score("café", "cafe", normalize=True, tokenize="auto"), 0.0)

    def test_normalized_empty_and_whitespace_inputs(self):
        for mode in ("auto", "word", "char"):
            with self.subTest(mode=mode):
                self.assertEqual(self.score("！。", " \t\n", normalize=True, tokenize=mode), 1.0)
                self.assertEqual(self.score("", "word", normalize=True, tokenize=mode), 0.0)
                self.assertEqual(self.score("word", "", normalize=True, tokenize=mode), 0.0)
                self.assertEqual(self.score(" a\t b\n", "a b", normalize=True, tokenize=mode), 1.0)

    def test_invalid_tokenization_is_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, "tokenize"):
            self.score("same", "same", tokenize="sentence")

    def test_word_information_alignment_fix_and_none_contract_survive(self):
        if not self.name.startswith("word_info_"):
            self.skipTest("word-information alignment contract")
        self.assertEqual(self.score(None, None), 1.0)
        self.assertEqual(self.score(None, "word"), 0.0)
        self.assertEqual(self.score("the cat sat", "the cat sat on the mat"), 0.5)
        self.assertAlmostEqual(self.score("a b a", "a a b"), 4 / 9)
        self.assertAlmostEqual(self.score("你 好", "你 好 啊", normalize=True, tokenize="auto"), 2 / 3)


for _implementation in ("yaml", "sdk"):
    for _name in NAMES:
        _class_name = f"Test_{_implementation}_{_name}"
        globals()[_class_name] = type(_class_name, (TranscriptionContract, unittest.TestCase), {
            "implementation": _implementation, "name": _name,
        })


class TestTemplateRegistration(unittest.TestCase):
    @unittest.skipUnless(HAS_YAML, "PyYAML is not installed")
    def test_templates_advertise_opt_in_parameters(self):
        import yaml
        for name in NAMES:
            with self.subTest(name=name):
                config = yaml.safe_load((FUNCTION_DIR / f"{name}.yaml").read_text())["config"]
                schema = config["function_params_schema"]
                self.assertEqual(schema["normalize"]["type"], "boolean")
                self.assertIs(schema["normalize"]["default"], False)
                self.assertIsNone(schema["tokenize"]["default"])
                self.assertIs(schema["tokenize"]["nullable"], True)
                self.assertEqual(schema["tokenize"]["enum"], ["auto", "word", "char"])
                self.assertEqual(config["required_keys"], ["reference", "hypothesis"])
                self.assertIn("normalize", config["config_params_desc"])
                self.assertIn("tokenize", config["config_params_desc"])

    def test_seeder_version_advances_for_new_parameters(self):
        path = ROOT / "model_hub/management/commands/seed_system_evals.py"
        tree = ast.parse(path.read_text())
        version = next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "SYSTEM_EVALS_VERSION" for target in node.targets))
        self.assertGreater(version, 28)


if __name__ == "__main__":
    unittest.main()
