"""HOSTED-HARDEN-3: Go/Helm cap conversion and explicit velocity parity."""

import pytest

from test_hosted_deployment import render, values

FIELDS = ("max_usd_per_minute", "max_usd_per_hour", "new_org_daily_cap_usd")


def activated(guard):
    data = values()
    data["hosted"]["onekeyActivation"] = True
    data["agentccGateway"]["config"]["managed"] = {"spend_guard": guard}
    return data


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value,kind", [(1e-7, "sub-micro"), (4e-7, "sub-micro"), (-1, "negative")], ids=["tiny", "tiny4", "negative"])
@pytest.mark.parametrize("other", [False, True])
def test_hosted_cap_invalid(tmp_path, field, value, kind, other):
    guard = dict.fromkeys(FIELDS, 0)
    if other:
        guard.update(max_usd_per_minute=0.01, max_usd_per_hour=0.01)
    guard[field] = value
    result = render(tmp_path, activated(guard))
    assert result.returncode != 0
    assert "managed.spend_guard." + field in result.stderr
    assert kind in result.stderr


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")], ids=["nan", "inf", "negative_inf"])
def test_hosted_cap_nonfinite_parse_refusal(tmp_path, field, value):
    guard = dict.fromkeys(FIELDS, 0)
    guard[field] = value
    result = render(tmp_path, activated(guard))
    assert result.returncode != 0
    # Helm's YAML-to-JSON conversion rejects these before template evaluation.
    assert "unsupported value" in result.stderr


@pytest.mark.parametrize("guard,kind", [
    ({"max_usd_per_minute": 0, "max_usd_per_hour": 0, "new_org_daily_cap_usd": 25}, "enforcing velocity cap"),
    ({"max_usd_per_minute": 1e-7, "max_usd_per_hour": 4e-7, "new_org_daily_cap_usd": 25}, "sub-micro"),
    ({"max_usd_per_minute": float("nan"), "max_usd_per_hour": float("nan"), "new_org_daily_cap_usd": 25}, "unsupported value"),
    (dict.fromkeys(FIELDS, float("nan")), "unsupported value"),
    ({}, "enforcing velocity cap"),
    ({"max_usd_per_minute": 0}, "enforcing velocity cap"),
], ids=["new_org_only", "both_tiny", "both_nan", "all_nan", "omitted", "partial_zero"])
def test_hosted_cap_velocity_required(tmp_path, guard, kind):
    result = render(tmp_path, activated(guard))
    assert result.returncode != 0
    assert kind in result.stderr


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [0.0000005, 0.000001, 0.01])
def test_hosted_cap_loading_controls(tmp_path, field, value):
    guard = dict.fromkeys(FIELDS, 0)
    if field == "new_org_daily_cap_usd":
        guard["max_usd_per_minute"] = 0.01
    guard[field] = value
    result = render(tmp_path, activated(guard))
    assert result.returncode == 0, result.stderr
