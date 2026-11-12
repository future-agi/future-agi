"""The value-free own-estimate variant is not a window/reset response."""
import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest


@pytest.mark.parametrize("window", ["minute", "hour"])
def test_own_estimate_velocity_schema(window):
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "api_contracts/gateway/examples/error-402-v1.schema.json").read_text())
    validator = Draft202012Validator(schema)
    body = {"error": {"type": "rate_limit_error", "code": "managed_spend_velocity", "param": None,
                      "message": f"This request's worst-case cost exceeds the per-{window} spending limit. Lower max_tokens to fit within the limit.",
                      "metadata": {"reason": f"request_exceeds_{window}_cap"}}}
    validator.validate(body)
    for field, value in {"required_usd": 5, "available_usd": 1, "scope": "org", "resets_at": None}.items():
        bad = copy.deepcopy(body)
        bad["error"]["metadata"][field] = value
        assert list(validator.iter_errors(bad)), field
    bad = copy.deepcopy(body)
    bad["error"]["code"] = "managed_billing_unavailable"
    assert list(validator.iter_errors(bad))
    normal = copy.deepcopy(body)
    normal["error"]["metadata"] = {"limit_source": "agentcc_spend_guard", "remedy_hint": "retry", "scope": "org", "resets_at": "2026-11-03T00:01:00Z"}
    validator.validate(normal)
