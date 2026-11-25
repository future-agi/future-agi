"""Jev evals go through this gateway unless someone chooses otherwise.

A Jev eval reaches TypeSafe through the AgentCC gateway's /v1/systemone, on the
backend's internal key, with the org's own TypeSafe key for that one call.
JEV_TRANSPORT=direct is the only way around the gateway. The Jev client's tests
pick the transport themselves, so these pin the value settings.py ships with
and what a Jev eval sends on it.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[2]
GATEWAY_URL = "http://agentcc-gateway.test:8080"
ORG_KEY = "org-typesafe-key-CANARY"
INTERNAL_KEY = "internal-gateway-key-CANARY"
GATEWAY_ENV = (
    "AGENTCC_GATEWAY_INTERNAL_URL",
    "AGENTCC_INTERNAL_URL",
    "AGENTCC_GATEWAY_URL",
    "AGENTCC_INTERNAL_API_KEY",
)


def _shipped_transport(**env):
    """JEV_TRANSPORT as the production settings derive it, loaded in a fresh
    interpreter with JEV_TRANSPORT unset unless ``env`` sets it."""
    environ = {k: v for k, v in os.environ.items() if k != "JEV_TRANSPORT"}
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tfc.settings import settings; print(settings.JEV_TRANSPORT)",
        ],
        env={**environ, **env},
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert loaded.returncode == 0, loaded.stderr
    return loaded.stdout.splitlines()[-1]


@pytest.mark.unit
def test_settings_ship_the_gateway_transport():
    assert _shipped_transport() == "gateway"


@pytest.mark.unit
def test_only_an_explicit_direct_leaves_the_gateway():
    assert _shipped_transport(JEV_TRANSPORT=" Direct ") == "direct"


@pytest.mark.unit
@pytest.mark.requires_ee
def test_a_jev_eval_on_the_shipped_settings_is_a_gateway_call(monkeypatch, settings):
    from ee.jev.client import PROVIDER_KEY_HEADER, JevSystemOneClient

    for name in GATEWAY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTCC_GATEWAY_INTERNAL_URL", GATEWAY_URL)
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", INTERNAL_KEY)
    if hasattr(settings, "AGENTCC_INTERNAL_API_KEY"):
        del settings.AGENTCC_INTERNAL_API_KEY
    settings.JEV_TRANSPORT = _shipped_transport()
    answer = {
        "model": "jev-1.13.0",
        "answers": {"q1": {"type": "noul", "noul": 0.9}},
        "usage": {"input_tokens": 12, "output_tokens": 0},
    }

    with patch("httpx.post", return_value=httpx.Response(200, json=answer)) as post:
        result = JevSystemOneClient(api_key=ORG_KEY).evaluate(
            "jev-1.13.0", {"input": "value"}, {"q1": {"type": "noul"}}
        )

    assert result == answer
    assert post.call_count == 1
    assert post.call_args.args == (f"{GATEWAY_URL}/v1/systemone",)
    headers = post.call_args.kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {INTERNAL_KEY}"
    assert headers[PROVIDER_KEY_HEADER] == ORG_KEY
    assert post.call_args.kwargs["json"]["model"] == "typesafe/jev-1.13.0"
