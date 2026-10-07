"""Settings that settings.py derives from the environment at load time.

Each case loads the settings module in a fresh interpreter with the variable
set, since the value is fixed once the module has been imported.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2]


def _load_settings(names, **env):
    """Load the settings with ``env``; the last stdout line has ``names``."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib, os; "
            "s = importlib.import_module(os.environ['DJANGO_SETTINGS_MODULE']); "
            f"print(*(getattr(s, n) for n in {list(names)!r}))",
        ],
        env={**os.environ, **env},
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )


# USAGE_EVENTS_MAX_LEN, the cap on the usage:events billing stream, is read
# as fi-collector reads it (cmd/fi-collector/main.go): blank is the default,
# and zero, a negative number or text refuses to start. 0 would trim events
# the consumer has not read yet, and a negative cap makes every XADD fail,
# which the emitter only logs.


@pytest.mark.parametrize("value", ["", " "])
def test_unset_usage_stream_cap_is_one_million(value):
    loaded = _load_settings(["USAGE_EVENTS_MAX_LEN"], USAGE_EVENTS_MAX_LEN=value)
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "1000000"


def test_a_positive_usage_stream_cap_is_used():
    loaded = _load_settings(["USAGE_EVENTS_MAX_LEN"], USAGE_EVENTS_MAX_LEN="250000")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "250000"


@pytest.mark.parametrize("value", ["0", "-1", "lots"])
def test_any_other_usage_stream_cap_refuses_to_load(value):
    loaded = _load_settings(["USAGE_EVENTS_MAX_LEN"], USAGE_EVENTS_MAX_LEN=value)
    assert loaded.returncode != 0
    assert "ImproperlyConfigured: USAGE_EVENTS_MAX_LEN must be" in loaded.stderr


# FRONTEND_BASE_URL: the UI that links leaving the app point at (annotation
# digests and discussions, rule-run emails, the MCP OAuth consent page).


@pytest.mark.parametrize(
    "frontend_url, expected",
    [
        ("https://ui.example.com/", "https://ui.example.com"),
        # Compose passes an unset variable through as empty.
        ("", "http://app.example.com"),
        (" ", "http://app.example.com"),
    ],
)
def test_frontend_url_wins_over_app_url(frontend_url, expected):
    loaded = _load_settings(
        ["FRONTEND_BASE_URL"],
        FRONTEND_URL=frontend_url,
        APP_URL="http://app.example.com",
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == expected


# Only a Future AGI Cloud region (US, EU, DEV) is Cloud. Any other non-empty
# CLOUD_DEPLOYMENT is a self-hosted install: it must not force reCAPTCHA on
# (every login would be rejected without a key) nor select the Redis channel
# layer for a single web process.


@pytest.mark.parametrize(
    "deployment, recaptcha, channel_layer",
    [
        ("", "False", "memory"),
        ("false", "False", "memory"),
        ("self-hosted", "False", "memory"),
        ("US", "True", "redis"),
    ],
)
def test_only_a_cloud_region_turns_on_cloud_defaults(
    deployment, recaptcha, channel_layer
):
    loaded = _load_settings(
        ["RECAPTCHA_ENABLED", "CHANNEL_LAYER_BACKEND"],
        CLOUD_DEPLOYMENT=deployment,
        ENV_TYPE="production",
        SECRET_KEY="settings-env-test-" + "k" * 40,
        RECAPTCHA_ENABLED="",
        RECAPTCHA_SECRET_KEY="",
        CHANNEL_LAYER_BACKEND="",
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == f"{recaptcha} {channel_layer}"
