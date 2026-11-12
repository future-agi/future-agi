import importlib.util
import math
from pathlib import Path
import sys
import types
import pytest
import yaml

# Isolate SDK functions loading without enterprise cloud dependencies
for mod_name in [
    "agentic_eval", "agentic_eval.core_evals", "agentic_eval.core_evals.fi_evals",
    "agentic_eval.core_evals.fi_evals.grounded", "agentic_eval.core_evals.fi_evals.grounded.similarity",
    "agentic_eval.core_evals.fi_utils", "agentic_eval.core_evals.fi_utils.exceptions",
    "agentic_eval.core_evals.fi_utils.fi_code_execution", "agentic_eval.core_evals.fi_utils.json",
    "agentic_eval.core_evals.fi_utils.logging", "agentic_eval.core_evals.fi_utils.utils",
    "agentic_eval.core_evals.keys", "agentic_eval.core_evals.keys.openai_api",
    "agentic_eval.core_evals.llm_services", "agentic_eval.core_evals.llm_services.openai_api"
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

sys.modules["agentic_eval.core_evals.fi_evals.grounded.similarity"].CosineSimilarity = object
sys.modules["agentic_eval.core_evals.fi_utils.exceptions"].NoOpenAiApiKeyException = Exception
sys.modules["agentic_eval.core_evals.fi_utils.fi_code_execution"].CodeExecution = object
sys.modules["agentic_eval.core_evals.fi_utils.json"].extract_json_path = lambda *a, **k: None
sys.modules["agentic_eval.core_evals.fi_utils.json"].validate_json = lambda *a, **k: True
sys.modules["agentic_eval.core_evals.fi_utils.logging"].logger = object
sys.modules["agentic_eval.core_evals.fi_utils.utils"].PreserveUndefined = object
sys.modules["agentic_eval.core_evals.keys.openai_api"].OpenAiApiKey = object
sys.modules["agentic_eval.core_evals.llm_services.openai_api"].OpenAiService = object

functions_path = (
    Path(__file__).resolve().parents[2]
    / "agentic_eval"
    / "core_evals"
    / "fi_evals"
    / "function"
    / "functions.py"
)
spec = importlib.util.spec_from_file_location("functions", str(functions_path))
functions_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(functions_mod)
calculate_spearman_correlation = functions_mod.calculate_spearman_correlation


@pytest.fixture
def spearman_yaml_evaluator():
    yaml_path = (
        Path(__file__).resolve().parents[1]
        / "system_evals"
        / "function"
        / "spearman_correlation.yaml"
    )
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    code = data["config"]["code"]
    loc = {}
    exec(code, loc, loc)
    return loc["evaluate"]


def test_spearman_untied_perfect_correlation(spearman_yaml_evaluator):
    # YAML evaluator
    res_pos = spearman_yaml_evaluator(None, [1, 2, 3, 4, 5], [1, 2, 3, 4, 5], None)
    assert math.isclose(res_pos["score"], 1.0, rel_tol=1e-4)
    assert "rho=1.0000" in res_pos["reason"]

    res_neg = spearman_yaml_evaluator(None, [1, 2, 3, 4, 5], [5, 4, 3, 2, 1], None)
    assert math.isclose(res_neg["score"], 0.0, rel_tol=1e-4)
    assert "rho=-1.0000" in res_neg["reason"]

    # SDK function
    sdk_pos = calculate_spearman_correlation([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    assert math.isclose(sdk_pos["result"], 1.0, rel_tol=1e-4)
    assert "rho=1.0000" in sdk_pos["reason"]

    sdk_neg = calculate_spearman_correlation([1, 2, 3, 4, 5], [5, 4, 3, 2, 1])
    assert math.isclose(sdk_neg["result"], 0.0, rel_tol=1e-4)
    assert "rho=-1.0000" in sdk_neg["reason"]


def test_spearman_with_tied_ranks(spearman_yaml_evaluator):
    # Case 1: [1, 1, 2, 3] vs [1, 2, 2, 3] -> rho = 5/6 ≈ 0.8333, score ≈ 0.9167
    res1 = spearman_yaml_evaluator(None, [1, 1, 2, 3], [1, 2, 2, 3], None)
    sdk1 = calculate_spearman_correlation([1, 1, 2, 3], [1, 2, 2, 3])
    assert math.isclose(res1["score"], 0.9167, rel_tol=1e-3)
    assert math.isclose(sdk1["result"], 0.9167, rel_tol=1e-3)
    assert "rho=0.8333" in res1["reason"]
    assert "rho=0.8333" in sdk1["reason"]

    # Case 2: [1, 1, 1, 2] vs [1, 2, 3, 4] -> rho ≈ 0.7746, score ≈ 0.8873
    res2 = spearman_yaml_evaluator(None, [1, 1, 1, 2], [1, 2, 3, 4], None)
    sdk2 = calculate_spearman_correlation([1, 1, 1, 2], [1, 2, 3, 4])
    assert math.isclose(res2["score"], 0.8873, rel_tol=1e-3)
    assert math.isclose(sdk2["result"], 0.8873, rel_tol=1e-3)
    assert "rho=0.7746" in res2["reason"]
    assert "rho=0.7746" in sdk2["reason"]

    # Case 3: [1, 1, 1, 1, 2] vs [5, 4, 3, 2, 1] -> rho ≈ -0.7071, score ≈ 0.1464
    res3 = spearman_yaml_evaluator(None, [1, 1, 1, 1, 2], [5, 4, 3, 2, 1], None)
    sdk3 = calculate_spearman_correlation([1, 1, 1, 1, 2], [5, 4, 3, 2, 1])
    assert math.isclose(res3["score"], 0.1464, rel_tol=1e-3)
    assert math.isclose(sdk3["result"], 0.1464, rel_tol=1e-3)
    assert "rho=-0.7071" in res3["reason"]
    assert "rho=-0.7071" in sdk3["reason"]


def test_spearman_constant_input_zero_variance(spearman_yaml_evaluator):
    # Both constant: zero variance in ranks -> rho=0.0, score=0.5
    res_const = spearman_yaml_evaluator(None, [3, 3, 3, 3], [3, 3, 3, 3], None)
    sdk_const = calculate_spearman_correlation([3, 3, 3, 3], [3, 3, 3, 3])
    assert res_const["score"] == 0.5
    assert sdk_const["result"] == 0.5
    assert "rho=0.0000" in res_const["reason"]
    assert "rho=0.0000" in sdk_const["reason"]

    # Collapsed model predictions vs varied ground truth -> rho=0.0, score=0.5
    res_collapsed = spearman_yaml_evaluator(None, [0.7] * 5, [1, 2, 3, 4, 5], None)
    sdk_collapsed = calculate_spearman_correlation([0.7] * 5, [1, 2, 3, 4, 5])
    assert res_collapsed["score"] == 0.5
    assert sdk_collapsed["result"] == 0.5
    assert "rho=0.0000" in res_collapsed["reason"]
    assert "rho=0.0000" in sdk_collapsed["reason"]


def test_spearman_invalid_inputs(spearman_yaml_evaluator):
    assert spearman_yaml_evaluator(None, [1], [1], None)["score"] == 0.0
    assert spearman_yaml_evaluator(None, [1, 2], [1], None)["score"] == 0.0
    assert spearman_yaml_evaluator(None, [], [], None)["score"] == 0.0

    assert calculate_spearman_correlation([1], [1])["result"] == 0.0
    assert calculate_spearman_correlation([1, 2], [1])["result"] == 0.0
    assert calculate_spearman_correlation([], [])["result"] == 0.0


def test_spearman_json_and_string_inputs(spearman_yaml_evaluator):
    res_json = spearman_yaml_evaluator(None, "[1, 2, 3, 4]", "[1, 2, 3, 4]", None)
    sdk_json = calculate_spearman_correlation("[1, 2, 3, 4]", "[1, 2, 3, 4]")
    assert math.isclose(res_json["score"], 1.0, rel_tol=1e-4)
    assert math.isclose(sdk_json["result"], 1.0, rel_tol=1e-4)

    res_csv = spearman_yaml_evaluator(None, "1, 2, 3, 4", "1, 2, 3, 4", None)
    sdk_csv = calculate_spearman_correlation("1, 2, 3, 4", "1, 2, 3, 4")
    assert math.isclose(res_csv["score"], 1.0, rel_tol=1e-4)
    assert math.isclose(sdk_csv["result"], 1.0, rel_tol=1e-4)
