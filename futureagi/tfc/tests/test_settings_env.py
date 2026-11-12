"""Settings that settings.py derives from the environment at load time.

Each case loads the settings module in a fresh interpreter with the variable
set, since the value is fixed once the module has been imported.
"""

import base64
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2]
# 32 bytes in URL-safe base64, the form INTEGRATION_ENCRYPTION_KEY must have.
FERNET_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()


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
        FI_API_SECRET_PEPPER="settings-env-test-pepper-" + "p" * 40,
        INTEGRATION_ENCRYPTION_KEY=FERNET_KEY,
        RECAPTCHA_ENABLED="",
        RECAPTCHA_SECRET_KEY="",
        CHANNEL_LAYER_BACKEND="",
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == f"{recaptcha} {channel_layer}"


# API secrets at rest (READINESS L03). Every sign-up seals the organization's
# system key under FI_API_SECRET_PEPPER and INTEGRATION_ENCRYPTION_KEY, so a
# process that would refuse the first sign-up refuses to start instead: without
# this, a production install's first sign-up failed half way and left the user
# behind. Local and test installs derive both from SECRET_KEY when unset.

NON_LOCAL = {
    "ENV_TYPE": "production",
    "SECRET_KEY": "settings-env-test-" + "k" * 40,
    "FI_API_SECRET_PEPPER": "settings-env-test-pepper-" + "p" * 40,
    "INTEGRATION_ENCRYPTION_KEY": FERNET_KEY,
}
SECRETS_AT_REST = ["API_SECRET_PEPPER", "API_SECRET_ENCRYPTION_KEY"]


def _fatal(loaded):
    assert loaded.returncode == 1, loaded.stderr
    return loaded.stderr.splitlines()[-1]


@pytest.mark.parametrize("env_type", ["production", "staging", "prod"])
def test_a_non_local_process_starts_with_both_keys(env_type):
    loaded = _load_settings(SECRETS_AT_REST, **{**NON_LOCAL, "ENV_TYPE": env_type})
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1].endswith(" " + FERNET_KEY)


@pytest.mark.parametrize("value", ["", "local-dev-only-api-secret-pepper-replace-me"])
def test_a_non_local_process_refuses_to_start_without_a_pepper(value):
    line = _fatal(
        _load_settings(SECRETS_AT_REST, **{**NON_LOCAL, "FI_API_SECRET_PEPPER": value})
    )
    assert line.startswith("[FATAL] FI_API_SECRET_PEPPER is unset or the published")


def test_a_non_local_process_refuses_to_start_without_an_encryption_key():
    line = _fatal(
        _load_settings(
            SECRETS_AT_REST, **{**NON_LOCAL, "INTEGRATION_ENCRYPTION_KEY": ""}
        )
    )
    assert line.startswith(
        "[FATAL] INTEGRATION_ENCRYPTION_KEY is unset in a non-local environment"
    )
    assert "urlsafe_b64encode(os.urandom(32))" in line  # how to make one


@pytest.mark.parametrize("env_type", ["production", "local"])
@pytest.mark.parametrize(
    # The placeholder futureagi/.env.example used to ship; standard base64 of
    # 31 bytes; a hex secret like the others.
    "value",
    ["your-fernet-key", base64.b64encode(bytes(31)).decode(), "ab" * 32],
)
def test_a_malformed_encryption_key_refuses_the_start_anywhere(env_type, value):
    env = {**NON_LOCAL, "ENV_TYPE": env_type, "INTEGRATION_ENCRYPTION_KEY": value}
    line = _fatal(_load_settings(SECRETS_AT_REST, **env))
    assert line.startswith("[FATAL] INTEGRATION_ENCRYPTION_KEY is not a Fernet key")
    assert value not in line  # never echo what may be a real secret


@pytest.mark.parametrize("env_type", ["production", "local"])
def test_a_short_pepper_refuses_the_start_anywhere(env_type):
    env = {**NON_LOCAL, "ENV_TYPE": env_type, "FI_API_SECRET_PEPPER": "p" * 31}
    line = _fatal(_load_settings(SECRETS_AT_REST, **env))
    assert line == "[FATAL] FI_API_SECRET_PEPPER is shorter than 32 characters."


def test_a_local_process_starts_without_either_key():
    env = {
        "ENV_TYPE": "local",
        "SECRET_KEY": "",
        "FI_API_SECRET_PEPPER": "",
        "INTEGRATION_ENCRYPTION_KEY": "",
    }
    loaded = _load_settings(["ENV_TYPE"], **env)
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "local"


def test_the_pepper_length_matches_the_one_the_app_enforces():
    from accounts.api_secrets import MIN_PEPPER_LENGTH
    from tfc.settings import settings as base

    assert base._MIN_API_SECRET_PEPPER_LENGTH == MIN_PEPPER_LENGTH


def test_the_app_and_the_collector_refuse_the_same_published_pepper():
    # fi-collector verifies collector grants under the pepper, so a value the
    # app refuses outside ENV_TYPE=local must stop the collector there too.
    from tfc.settings import settings as base

    go = (BACKEND.parent / "fi-collector/pkg/auth/secrethash.go").read_text()
    collector = re.findall(r'^\s*publishedPepper\s*=\s*"([^"]*)"', go, re.MULTILINE)
    assert collector == [base._PUBLISHED_API_SECRET_PEPPER]
    compose = [*COMPOSE_FILES, "fi-collector/docker-compose.standalone.yml"]
    assert _compose_defaults("FI_API_SECRET_PEPPER", compose) == {
        base._PUBLISHED_API_SECRET_PEPPER
    }


# SECRET_KEY signs logins, JWTs (SIMPLE_JWT's SIGNING_KEY), password-reset links
# and the SECRET_KEY-keyed service envelopes. A non-local process refuses the
# value the compose files publish as their default, as it refuses Django's
# built-in default and the published FI_API_SECRET_PEPPER. Local installs, the
# compose default ENV_TYPE, keep starting on it.

PUBLISHED_SECRET_KEY = "local-dev-only-not-for-production-replace-me"
SECRET_KEY_REFUSED = "[FATAL] SECRET_KEY is unset or a published default"
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.distributed.yml"]


def _compose_defaults(name, files):
    defaults = set()
    for file in files:
        text = (BACKEND.parent / file).read_text()
        defaults.update(re.findall(rf"\$\{{{name}:-([^}}]*)\}}", text))
    return defaults


@pytest.mark.parametrize("env_type", ["production", "staging", "prod", "dev"])
@pytest.mark.parametrize("value", ["", PUBLISHED_SECRET_KEY])
def test_a_non_local_process_refuses_a_published_secret_key(env_type, value):
    env = {**NON_LOCAL, "ENV_TYPE": env_type, "SECRET_KEY": value}
    line = _fatal(_load_settings(["ENV_TYPE"], **env))
    assert line.startswith(SECRET_KEY_REFUSED)
    assert PUBLISHED_SECRET_KEY not in line  # never echo the configured value


@pytest.mark.parametrize("env_type", ["local", "test"])
def test_a_local_process_still_starts_on_the_compose_secret_key(env_type):
    env = {**NON_LOCAL, "ENV_TYPE": env_type, "SECRET_KEY": PUBLISHED_SECRET_KEY}
    loaded = _load_settings(["ENV_TYPE"], **env)
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == env_type


def test_a_non_local_process_starts_on_a_random_secret_key():
    env = {**NON_LOCAL, "SECRET_KEY": secrets.token_hex(32)}
    loaded = _load_settings(["ENV_TYPE"], **env)
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "production"


def test_the_published_secret_key_is_the_compose_default():
    from tfc.settings import settings as base

    assert base._PUBLISHED_SECRET_KEY == PUBLISHED_SECRET_KEY
    assert _compose_defaults("SECRET_KEY", COMPOSE_FILES) == {PUBLISHED_SECRET_KEY}


# RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF: orgs created at or after it see only
# Environments in Simulate. Unset means no org is restricted, and a value that
# is not a date-time refuses to start rather than silently restricting nobody.


@pytest.mark.parametrize("value", ["", " "])
def test_unset_simulate_cutoff_restricts_nobody(value):
    loaded = _load_settings(
        ["RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF"],
        RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF=value,
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "None"


def test_a_simulate_cutoff_is_read_as_an_aware_date_time():
    loaded = _load_settings(
        ["RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF"],
        RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF="2026-10-14T18:00:00+05:30",
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "2026-10-14 18:00:00+05:30"


def test_a_simulate_cutoff_that_is_not_a_date_time_refuses_to_load():
    loaded = _load_settings(
        ["RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF"],
        RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF="tomorrow",
    )
    assert loaded.returncode != 0
    assert (
        "ImproperlyConfigured: RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF must be"
        in loaded.stderr
    )


# SAML_LOGIN_ENABLED is the SAML kill switch. Production settings (not the test
# settings, which force it on) must keep tenant-scoped SAML on when the
# variable is unset or blank, honour an explicit off, and refuse a value that
# is neither, so a typo cannot silently leave the switch in either state.


def _load_production_saml_switch(**env):
    environ = {k: v for k, v in os.environ.items() if k != "SAML_LOGIN_ENABLED"}
    environ.update(env, DJANGO_SETTINGS_MODULE="tfc.settings.settings")
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib; "
            "s = importlib.import_module('tfc.settings.settings'); "
            "print(s.SAML_LOGIN_ENABLED)",
        ],
        env=environ,
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_saml_login_is_on_when_the_switch_is_unset():
    loaded = _load_production_saml_switch()
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "True"


@pytest.mark.parametrize("value", ["", " ", "true", "1", "Yes", "on"])
def test_saml_login_is_on_for_blank_or_true(value):
    loaded = _load_production_saml_switch(SAML_LOGIN_ENABLED=value)
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "True"


@pytest.mark.parametrize("value", ["false", "0", "No", "off"])
def test_saml_login_is_off_when_switched_off(value):
    loaded = _load_production_saml_switch(SAML_LOGIN_ENABLED=value)
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "False"


@pytest.mark.parametrize("value", ["flase", "disabled"])
def test_an_unreadable_saml_switch_refuses_to_load(value):
    loaded = _load_production_saml_switch(SAML_LOGIN_ENABLED=value)
    assert loaded.returncode != 0
    assert "SAML_LOGIN_ENABLED" in loaded.stderr
