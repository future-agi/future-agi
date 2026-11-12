"""The test settings keep EE licensing off production.

ee/licensing's activation client and heartbeat send to FUTURE_AGI_LICENSE_URL,
which defaults to production (https://api.futureagi.com) when it is unset.
tfc/settings/test.py sets a closed local port instead, so a test run that
exports nothing never reaches production. The settings load in a fresh
interpreter with the variable removed: this process loaded them already, and
the test environment may export the variable itself.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.requires_ee]

BACKEND = Path(__file__).resolve().parents[2]
PRODUCTION = "https://api.futureagi.com"


def test_the_test_settings_keep_the_license_urls_off_production():
    environ = {k: v for k, v in os.environ.items() if k != "FUTURE_AGI_LICENSE_URL"}
    environ["DJANGO_SETTINGS_MODULE"] = "tfc.settings.test"
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib; "
            "importlib.import_module('tfc.settings.test'); "
            "from ee.licensing.activation_client import get_activation_url; "
            "from ee.licensing.heartbeat import get_heartbeat_url; "
            "print(get_activation_url()); "
            "print(get_heartbeat_url())",
        ],
        env=environ,
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert loaded.returncode == 0, loaded.stderr
    activation_url, heartbeat_url = loaded.stdout.splitlines()[-2:]
    assert not activation_url.startswith(PRODUCTION), activation_url
    assert not heartbeat_url.startswith(PRODUCTION), heartbeat_url
