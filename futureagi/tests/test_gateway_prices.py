"""The gateway price copy the backend bills from stays a true, well-read copy.

The gateway prices requests from agentcc-gateway/internal/modeldb/litellm.json; the
backend image holds only futureagi/, so the backend reads its own copy
(agentic_eval/core_evals/run_prompt/gateway_prices.json). These tests fail when the
copy drifts, when backend CI would not run on a change to the gateway's file, when a
bad copy would load quietly, when the loader reads the file differently from the
gateway, and when a model the platform itself configures is left with no price.

No database and no services: files are read and parsed.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agentic_eval.core_evals.run_prompt import gateway_prices
from agentic_eval.core_evals.run_prompt.gateway_prices import (
    GATEWAY_PRICES,
    GATEWAY_PRICES_PATH,
    GatewayPricesError,
    load_gateway_prices,
)
from agentic_eval.core_evals.run_prompt.model_pricing import lookup_pricing

ROOT = Path(__file__).resolve().parents[2]
FUTUREAGI = ROOT / "futureagi"
GATEWAY_LITELLM = ROOT / "agentcc-gateway" / "internal" / "modeldb" / "litellm.json"
BACKEND_CI = ROOT / ".github" / "workflows" / "backend-ci.yml"
MODEL_CONFIG = FUTUREAGI / "agentic_eval" / "core" / "utils" / "model_config.py"
FALCON_CLIENT = FUTUREAGI / "ee" / "falcon_ai" / "llm_client.py"
INSIGHTS_LLM = FUTUREAGI / "ee" / "agenthub" / "insights" / "llm.py"
HOSTED_GATEWAY = FUTUREAGI / "simulate" / "services" / "hosted_harness_gateway.py"
ALK_INGESTION = FUTUREAGI / "simulate" / "services" / "alk_simulate_ingestion.py"

GOOD_ENTRY = {
    "litellm_provider": "openai",
    "mode": "chat",
    "input_cost_per_token": 1e-6,
    "output_cost_per_token": 2e-6,
}


def _write(tmp_path, content):
    path = tmp_path / "gateway_prices.json"
    path.write_text(content, encoding="utf-8")
    return path


def test_the_python_copy_equals_the_gateway_file():
    assert GATEWAY_LITELLM.is_file(), (
        f"{GATEWAY_LITELLM} is missing. Run the backend tests from a full checkout "
        "of the repository: this test compares the backend's copy with it."
    )

    assert GATEWAY_PRICES_PATH.read_bytes() == GATEWAY_LITELLM.read_bytes(), (
        f"{GATEWAY_PRICES_PATH} differs from {GATEWAY_LITELLM}; run "
        "agentcc-gateway/scripts/update-litellm-pricing.sh or copy the gateway's "
        "file over it so both bill the same prices."
    )


def test_the_loader_keeps_what_the_gateway_keeps():
    raw = json.loads(GATEWAY_PRICES_PATH.read_text(encoding="utf-8"))

    assert len(GATEWAY_PRICES) == 4647
    assert sum(1 for mode, _ in GATEWAY_PRICES.values() if mode == "chat") == 3687
    with_cached = [
        key
        for key, (_, rates) in GATEWAY_PRICES.items()
        if "cached_input_per_1M_tokens" in rates
    ]
    assert len(with_cached) == 1829
    # A cached rate of 0 is no cached rate: the gateway bills such an entry at input.
    zero_cached = [
        key
        for key in GATEWAY_PRICES
        if raw[key].get("cache_read_input_token_cost") == 0
    ]
    assert len(zero_cached) == 18
    assert not set(zero_cached) & set(with_cached)


def test_the_loaded_rates_are_per_million_and_exact():
    # 1e-7 per token is 0.1 per 1M, not 0.09999999999999999.
    assert dict(GATEWAY_PRICES["gpt-6-luna"][1]) == {
        "input_per_1M_tokens": 0.1,
        "output_per_1M_tokens": 0.5,
        "cached_input_per_1M_tokens": 0.01,
    }


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(None, id="missing"),
        pytest.param("{", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param("{}", id="empty-object"),
        pytest.param('{"a": NaN}', id="nan"),
        pytest.param('{"a": Infinity}', id="infinity"),
        pytest.param(json.dumps({"sample_spec": GOOD_ENTRY}), id="only-sample-spec"),
    ],
)
def test_the_loader_refuses_a_bad_file(tmp_path, content):
    path = tmp_path / "gateway_prices.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")

    with pytest.raises(GatewayPricesError, match=r"^The gateway price copy "):
        load_gateway_prices(path)


def test_the_loader_drops_an_entry_the_gateway_cannot_decode(tmp_path):
    path = _write(
        tmp_path,
        json.dumps(
            {
                "x": "str",
                "y": {"input_cost_per_token": "1"},
                "z": {**GOOD_ENTRY, "mode": 5},
                "w": {**GOOD_ENTRY, "supports_vision": "yes"},
                "v": {**GOOD_ENTRY, "supported_regions": [1]},
                "good": GOOD_ENTRY,
            }
        ),
    )

    assert list(load_gateway_prices(path)) == ["good"]


def test_the_loader_applies_the_gateways_mode_and_zero_rules(tmp_path):
    path = _write(
        tmp_path,
        json.dumps(
            {
                "sample_spec": GOOD_ENTRY,
                # chat with input 0 is dropped; no mode is chat
                "chat-free": {"mode": "chat", "output_cost_per_token": 1e-6},
                "no-mode": {"input_cost_per_token": 1e-6},
                # embedding is dropped only when both prices are 0
                "embed-both-zero": {"mode": "embedding"},
                "embed-input-only": {"mode": "embedding", "input_cost_per_token": 2e-8},
                "completion": {"mode": "completion", "input_cost_per_token": 1e-6},
                "unknown-mode": {"mode": "mystery", "input_cost_per_token": 1e-6},
                # a field name matches ignoring case
                "case": {"Input_Cost_Per_Token": 3e-6, "MODE": "chat"},
                "null": None,
            }
        ),
    )

    loaded = load_gateway_prices(path)

    assert {key: mode for key, (mode, _) in loaded.items()} == {
        "no-mode": "chat",
        "embed-input-only": "embedding",
        "completion": "chat",
        "unknown-mode": "chat",
        "case": "chat",
    }
    assert loaded["case"][1]["input_per_1M_tokens"] == 3.0


def test_importing_without_the_copy_fails_loudly(tmp_path):
    module = tmp_path / "gateway_prices.py"
    shutil.copyfile(gateway_prices.__file__, module)

    with pytest.raises(RuntimeError, match="is missing") as missing:
        runpy.run_path(str(module))
    assert missing.type.__name__ == "GatewayPricesError"

    (tmp_path / "gateway_prices.json").write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not valid JSON") as malformed:
        runpy.run_path(str(module))
    assert malformed.type.__name__ == "GatewayPricesError"


def test_the_loader_needs_nothing_but_the_standard_library():
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            f"import runpy; runpy.run_path({gateway_prices.__file__!r})",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stderr


def test_the_lookup_needs_only_the_gateway_copy_and_the_catalogue(monkeypatch):
    stub = type(sys)("agentic_eval.core_evals.run_prompt.available_models")
    stub.AVAILABLE_MODELS = []
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
    path = Path(gateway_prices.__file__).with_name("model_pricing.py")
    spec = importlib.util.spec_from_file_location("model_pricing_isolated", path)
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)

    assert isolated.get_model_pricing("gpt-6-luna") == {
        "input_per_1M_tokens": 0.1,
        "output_per_1M_tokens": 0.5,
        "cached_input_per_1M_tokens": 0.01,
    }


def test_the_backend_ci_runs_when_the_gateway_price_table_changes():
    workflow = yaml.safe_load(BACKEND_CI.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["changes"]["steps"]
    filters = next(step for step in steps if step.get("id") == "filter")["with"]
    patterns = yaml.safe_load(filters["filters"])["backend"]

    assert "agentcc-gateway/internal/modeldb/litellm.json" in patterns


# --- Every model the platform configures itself has a price


def _calls_with_model_name(path):
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "model_name" and isinstance(
                    keyword.value, ast.Constant
                ):
                    names.append(keyword.value.value)
    return names


def _config_names():
    """Every ModelConfigs model_name written as a literal; the Bedrock ARNs are
    read from the environment and are empty in tests."""
    return [name for name in _calls_with_model_name(MODEL_CONFIG) if name]


def _falcon_defaults():
    for node in ast.walk(ast.parse(FALCON_CLIENT.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "model_defaults" for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            return list(ast.literal_eval(node.value).values())
    raise AssertionError("model_defaults is no longer a dict literal in llm_client.py")


def _insights_defaults():
    from agentic_eval.core.utils.model_config import ModelConfigs

    for node in ast.walk(ast.parse(INSIGHTS_LLM.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "_DEFAULTS" for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            names = []
            for value in node.value.values:
                # ModelConfigs.<ATTRIBUTE>.model_name
                names.append(getattr(ModelConfigs, value.value.attr).model_name)
            return names
    raise AssertionError("_DEFAULTS is no longer a dict literal in insights/llm.py")


def _environment_default(path, variable):
    """The literal in ``os.environ.get(variable) or "<default>"``."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            first, last = node.values[0], node.values[-1]
            if (
                isinstance(first, ast.Call)
                and first.args
                and isinstance(first.args[0], ast.Constant)
                and first.args[0].value == variable
                and isinstance(last, ast.Constant)
            ):
                return last.value
    raise AssertionError(f"{path.name} has no default for {variable}")


# Names no table prices, and why. Anything else must have both token prices.
UNPRICED = {
    "protect": "the bare Protect label is never billed by token",
}


def _platform_names():
    sources = {
        "ModelConfigs": _config_names(),
        "claude-sonnet-4-6 and its Bedrock form": [
            "claude-sonnet-4-6",
            "us.anthropic.claude-sonnet-4-6",
        ],
        "Falcon's defaults": _falcon_defaults(),
        "insights' defaults": _insights_defaults(),
        "the hosted harness's model": [
            _environment_default(HOSTED_GATEWAY, "SIMULATOR_LLM_MODEL"),
            _environment_default(HOSTED_GATEWAY, "ALK_HOSTED_AGENTCC_MODEL"),
        ],
        "the simulator's default": [
            _environment_default(ALK_INGESTION, "SIMULATOR_LLM_MODEL")
        ],
    }
    return sorted({(name, where) for where, names in sources.items() for name in names})


@pytest.mark.requires_ee
@pytest.mark.parametrize(("name", "where"), _platform_names())
def test_every_model_the_platform_configures_has_both_token_prices(name, where):
    if name in UNPRICED:
        pytest.skip(UNPRICED[name])

    found = lookup_pricing(name)

    assert found is not None, f"{name} ({where}) has no price in any table"
    assert "input_per_1M_tokens" in found.pricing, f"{name} ({where})"
    assert "output_per_1M_tokens" in found.pricing, f"{name} ({where})"


@pytest.mark.requires_ee
def test_the_exempt_names_are_the_ones_that_have_no_price():
    for name in UNPRICED:
        assert lookup_pricing(name) is None


@pytest.mark.requires_ee
@pytest.mark.parametrize(
    ("name", "rates"),
    [
        # The four platform defaults whose price moves to the gateway's.
        ("gpt-4o-mini", (0.15, 0.6)),
        ("us.anthropic.claude-sonnet-4-6", (3.3, 16.5)),
        ("gpt-4", (30.0, 60.0)),
        ("groq/llama-3.3-70b-versatile", (0.59, 0.79)),
        # Names whose price moves with the gateway's table.
        ("vertex_ai/gemini-2.5-flash", (0.3, 2.5)),
        ("vertex_ai/gemini-2.5-flash-lite", (0.1, 0.4)),
        # Names that were unpriced and now bill the gateway's price.
        ("perplexity/sonar-pro", (3.0, 15.0)),
        ("gpt-5.1", (1.25, 10.0)),
        ("perplexity/gpt-5.1", (1.25, 10.0)),
        ("anthropic.claude-3-5-sonnet-20240620-v1:0", (3.0, 15.0)),
        ("vertex_ai/gemini-3-pro-preview", (2.0, 12.0)),
        # Priced by the catalogue, not the gateway's table.
        ("turing_large", (5.5, 27.5)),
        ("protect_flash", (0.01, 0.01)),
        ("protect_toxicity", (0.02, 0.02)),
        ("protect_bias", (0.02, 0.02)),
        ("protect_privacy", (0.02, 0.02)),
        ("protect_prompt_injection", (0.02, 0.02)),
    ],
)
def test_the_platform_names_that_move_are_pinned(name, rates):
    pricing = lookup_pricing(name).pricing

    assert (pricing["input_per_1M_tokens"], pricing["output_per_1M_tokens"]) == rates


@pytest.mark.requires_ee
def test_a_cached_rate_is_pinned():
    pricing = lookup_pricing("claude-sonnet-4-6").pricing

    assert pricing["cached_input_per_1M_tokens"] == 0.3
