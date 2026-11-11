"""The pytest settings keep license traffic on a closed loopback port.

``tfc/settings/test.py`` sets a placeholder ``EE_LICENSE_KEY`` so licensed
code paths load, while the activation client and the enterprise heartbeat
default ``FUTURE_AGI_LICENSE_URL`` to Future AGI's hosted license service. A
test run that names no license server must resolve both endpoints to
loopback, as telemetry does; a run that names one keeps it.

Each case loads the settings in a fresh interpreter, because the settings
module reads the environment once, when it is imported.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

BACKEND = Path(__file__).resolve().parents[3]

_PROBE = """
import importlib, json, os
importlib.import_module("tfc.settings.test")
from ee.licensing.activation_client import get_activation_url
from ee.licensing.heartbeat import get_heartbeat_url
print("LICENSE_URLS=" + json.dumps({
    "environment": os.environ.get("FUTURE_AGI_LICENSE_URL"),
    "activation": get_activation_url(),
    "heartbeat": get_heartbeat_url(),
}))
"""


def _resolve_license_urls(**environment):
    """Import the test settings in a fresh interpreter and resolve both URLs.

    The run's own ``FUTURE_AGI_LICENSE_URL`` is removed first, so only the
    test settings and ``environment`` decide the result.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if name != "FUTURE_AGI_LICENSE_URL"
    }
    env.update(environment)
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    marker = next(
        line
        for line in reversed(completed.stdout.splitlines())
        if line.startswith("LICENSE_URLS=")
    )
    return json.loads(marker.removeprefix("LICENSE_URLS="))


def _is_hosted_license_service(url):
    host = (urlsplit(url).hostname or "").lower()
    return host == "futureagi.com" or host.endswith(".futureagi.com")


def test_unset_license_server_resolves_to_loopback():
    resolved = _resolve_license_urls()

    assert resolved == {
        "environment": "http://127.0.0.1:9",
        "activation": "http://127.0.0.1:9/v1/self-hosted/activations",
        "heartbeat": "http://127.0.0.1:9/v1/enterprise/heartbeats",
    }


# Compose passes an unset variable through as empty.
@pytest.mark.parametrize("value", ["", " "])
def test_blank_license_server_never_resolves_to_the_hosted_service(value):
    resolved = _resolve_license_urls(FUTURE_AGI_LICENSE_URL=value)

    assert not _is_hosted_license_service(resolved["activation"])
    assert not _is_hosted_license_service(resolved["heartbeat"])


def test_an_explicit_license_server_still_wins():
    resolved = _resolve_license_urls(
        FUTURE_AGI_LICENSE_URL="http://license.test.invalid:8443/"
    )

    assert resolved == {
        "environment": "http://license.test.invalid:8443/",
        "activation": "http://license.test.invalid:8443/v1/self-hosted/activations",
        "heartbeat": "http://license.test.invalid:8443/v1/enterprise/heartbeats",
    }
