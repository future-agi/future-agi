"""The push forwards an output check's "judges" as the admin set it, and logs
the kind each check runs with: the org, the check's name and its kind, never
a config value."""

import copy
from unittest.mock import call, patch

import pytest

from agentcc.services.config_push import _transform_guardrails

ORG = "11111111-1111-4111-8111-111111111111"
SECRET = "sk-live-not-for-logs-5d1c"

RULES = [
    {"name": "lakera-guard", "config": {"api_key": SECRET, "judges": "shape"}},
    {
        "name": "futureagi-eval",
        "config": {
            "eval_ids": ["23"],
            "api_key": SECRET,
            "secret_key": SECRET,
            "judges": "says",
        },
    },
    {"name": "presidio-pii", "config": {"api_key": SECRET}},
    {"name": "aporia-guard", "config": {"api_key": SECRET, "judges": "content"}},
    {"name": "pangea-guard", "config": {"api_key": SECRET, "judges": None}},
]
WANT = {
    "lakera-guard": "shape",
    "futureagi-eval": "says",
    "presidio-pii": "unset",
    "aporia-guard": "invalid",
    "pangea-guard": "unset",
}


def _section(shape):
    rules = [dict(r, enabled=True) for r in copy.deepcopy(RULES)]
    if shape == "checks-map":
        return {"checks": {r["name"]: r for r in rules}}
    return {"checks" if shape == "checks-list" else "rules": rules}


@pytest.mark.parametrize("shape", ["checks-map", "checks-list", "rules"])
def test_push_forwards_judges_and_logs_each_checks_kind(shape):
    target = "agentcc.services.config_push"
    with patch(f"{target}._inject_guardrail_credentials"):
        with patch(f"{target}._inject_fi_credentials"):
            with patch(f"{target}.logger") as log:
                result = _transform_guardrails(_section(shape), ORG)

    checks = result["checks"]
    for rule in RULES:
        cfg = checks[rule["name"]]["config"]
        if "judges" in rule["config"]:
            assert cfg["judges"] == rule["config"]["judges"]
        else:
            assert "judges" not in cfg

    got = {
        c.kwargs["check"]: c
        for c in log.info.call_args_list
        if c.args == ("guardrail_check_kind",)
    }
    assert got == {
        name: call("guardrail_check_kind", org_id=ORG, check=name, judges=kind)
        for name, kind in WANT.items()
    }
    assert SECRET not in repr(log.mock_calls)
