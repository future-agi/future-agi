"""The worker -> backend live-update relay must never hand a self-hosted org's
system API key and secret to Future AGI Cloud.

call_websocket POSTs to WEBSOCKET_ENDPOINT with those credentials. That
endpoint defaults to BASE_URL, which used to default to
https://api.futureagi.com whenever ENV_TYPE was not local/test, so every
self-hosted production install sent its keys to the vendor.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import override_settings

from model_hub.utils import call_websocket, is_forbidden_vendor_endpoint

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "url",
    [
        "https://api.futureagi.com/call-websocket/",
        "https://dev.api.futureagi.com/call-websocket/",
        "https://FutureAGI.com./call-websocket/",
        "http://user@api.futureagi.com:8443/call-websocket/",
    ],
)
@override_settings(CLOUD_DEPLOYMENT="")
def test_self_hosted_refuses_futureagi_hosts(url):
    assert is_forbidden_vendor_endpoint(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/call-websocket/",
        "http://backend/call-websocket/",
        "https://futureagi.com.example.org/call-websocket/",
        "https://notfutureagi.com/call-websocket/",
    ],
)
@override_settings(CLOUD_DEPLOYMENT="")
def test_self_hosted_allows_its_own_hosts(url):
    assert not is_forbidden_vendor_endpoint(url)


@override_settings(CLOUD_DEPLOYMENT="US")
def test_cloud_may_relay_to_its_own_api():
    assert not is_forbidden_vendor_endpoint("https://api.futureagi.com/call-websocket/")


@override_settings(
    CLOUD_DEPLOYMENT="", WEBSOCKET_ENDPOINT="https://api.futureagi.com/call-websocket/"
)
def test_call_websocket_sends_nothing_to_the_vendor():
    with (
        patch("model_hub.utils.requests.post") as post,
        patch("model_hub.utils.OrgApiKey.no_workspace_objects") as keys,
    ):
        result = call_websocket("00000000-0000-0000-0000-000000000001", {"x": 1})

    assert result["status"] == "error"
    post.assert_not_called()
    # Refused before the org's credentials are even looked up (or minted).
    keys.get.assert_not_called()
    keys.create.assert_not_called()


def _settings_urls(**env):
    """BASE_URL and WEBSOCKET_ENDPOINT as a fresh process computes them."""
    script = (
        "import tfc.settings.settings as s; "
        "print(s.BASE_URL); print(s.WEBSOCKET_ENDPOINT)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "SECRET_KEY": "s" * 64,
            **env,
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout.strip().splitlines()[-2:]


@pytest.mark.parametrize("env_type", ["local", "development", "production"])
def test_self_hosted_defaults_to_its_own_api_whatever_the_env_type(env_type):
    assert _settings_urls(ENV_TYPE=env_type) == [
        "http://localhost:8000",
        "http://localhost:8000/call-websocket/",
    ]


def test_cloud_keeps_its_public_api_default():
    assert _settings_urls(ENV_TYPE="prod", CLOUD_DEPLOYMENT="US") == [
        "https://api.futureagi.com",
        "https://api.futureagi.com/call-websocket/",
    ]
