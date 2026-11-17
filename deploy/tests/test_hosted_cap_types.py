"""HOSTED-HARDEN-4: reject coerced cap types before a pod can crashloop."""

import subprocess

import pytest
import yaml

from test_hosted_caps import FIELDS, activated
from test_hosted_deployment import CHART, render


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", ["NaN", "Infinity", "5", True, "abc", "0.5", ".inf", False, [], {}],
                         ids=["nan", "infinity", "quoted_int", "true", "abc", "quoted_fraction", "dot_inf", "false", "list", "map"])
def test_hosted_cap_wrong_kind(tmp_path, field, value):
    guard = {"max_usd_per_minute": 5, "max_usd_per_hour": 100}
    guard[field] = value
    result = render(tmp_path, activated(guard))
    assert result.returncode != 0
    assert "managed.spend_guard." + field in result.stderr
    assert "must be a number" in result.stderr


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("flag,value,accepted", [
    ("--set", "0.5", False), ("--set", "NaN", False),
    ("--set-string", "5", False), ("--set", "true", False),
    ("--set", "5", True), ("--set-json", "0.5", True),
])
def test_hosted_cap_cli_kind(tmp_path, field, flag, value, accepted):
    fixture = tmp_path / "numeric-cap-values.yaml"
    fixture.write_text(yaml.safe_dump(activated({"max_usd_per_minute": 5, "max_usd_per_hour": 100})))
    result = subprocess.run([
        "helm", "template", "hosted-test", str(CHART),
        "-f", str(CHART / "examples/bundled.yaml"),
        "-f", str(CHART / "examples/hosted-gateway.yaml"), "-f", str(fixture),
        "--set", "secrets.allowGeneratedKeysWithoutCluster=true",
        flag, "agentccGateway.config.managed.spend_guard." + field + "=" + value,
    ], capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) == accepted, result.stderr
    if not accepted:
        assert "managed.spend_guard." + field in result.stderr
        assert "must be a number" in result.stderr


@pytest.mark.parametrize("field", FIELDS)
def test_hosted_cap_fraction_values_control(tmp_path, field):
    guard = {"max_usd_per_minute": 5, "max_usd_per_hour": 100, field: 0.5}
    result = render(tmp_path, activated(guard))
    assert result.returncode == 0, result.stderr
