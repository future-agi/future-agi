"""``manage.py api_secrets`` as an operator runs it (READINESS L03).

The runbook's post-deploy check (``status``) and its backfill run here in fresh
processes, so the mutation-free startup guard in ``model_hub.apps`` sees the real
argv; ``call_command`` in the other tests never meets it, and before this the
guard refused both. ``status`` writes nothing and is admitted anywhere;
``backfill`` writes, so it runs only in an operator job (SERVICE_TYPE=bootstrap
with STARTUP_DB_MUTATION_MODE=operator) or in the explicit local mode
(NO_STARTUP_DB_MUTATIONS=false outside a hosted deployment). Real Postgres, two
tenants.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.db import connection

from accounts.models import Organization, OrgApiKey
from model_hub.apps import (
    explicit_management_mutation_authorized,
    guarded_management_command,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
GUARD_ENV = (
    "SERVICE_TYPE",
    "STARTUP_DB_MUTATION_MODE",
    "NO_STARTUP_DB_MUTATIONS",
    "ENV_TYPE",
    "CLOUD_DEPLOYMENT",
)


@pytest.mark.parametrize(
    "prefix", [["manage.py"], ["python", "-m", "django"], ["django-admin"]]
)
def test_the_guard_admits_status_anywhere(prefix):
    assert guarded_management_command([*prefix, "api_secrets", "status"]) is None


@pytest.mark.parametrize(
    "args", [["backfill"], ["status", "--batch-size", "5"], ["backfill", "--x"], []]
)
def test_the_guard_holds_every_other_form(args):
    command = ["manage.py", "api_secrets", *args]
    assert guarded_management_command(command) == "api_secrets"


@pytest.mark.parametrize(
    "env, authorized",
    [
        (
            {
                "SERVICE_TYPE": "bootstrap",
                "STARTUP_DB_MUTATION_MODE": "operator",
                "ENV_TYPE": "production",
            },
            True,
        ),
        ({"NO_STARTUP_DB_MUTATIONS": "false", "ENV_TYPE": "local"}, True),
        ({"NO_STARTUP_DB_MUTATIONS": "false", "ENV_TYPE": "production"}, False),
        ({"SERVICE_TYPE": "backend", "ENV_TYPE": "production"}, False),
        ({"ENV_TYPE": "local"}, False),
    ],
)
def test_backfill_runs_only_where_mutations_are_authorized(
    monkeypatch, env, authorized
):
    for name in GUARD_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    argv = ["manage.py", "api_secrets", "backfill"]
    assert explicit_management_mutation_authorized(argv) is authorized


def _manage_py(*args, **env):
    """``python manage.py ...`` against this test database, as a new process."""
    environment = {k: v for k, v in os.environ.items() if k not in GUARD_ENV}
    environment.update(
        DJANGO_SETTINGS_MODULE="tfc.settings.test",
        PG_DB=connection.settings_dict["NAME"],
        NO_STARTUP_DB_MUTATIONS="true",
        **env,
    )
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        start_new_session=True,
    )


def _reports(result):
    return [json.loads(line) for line in result.stdout.splitlines() if line[:1] == "{"]


def _plaintext_rows():
    return OrgApiKey._base_manager.exclude(secret_key="").count()


@pytest.fixture
def plaintext_keys(transactional_db):
    """One system key per tenant, as a pod of the previous release stores it."""
    keys = []
    for label in ("a", "b"):
        org = Organization.objects.create(name=f"Command tenant {label}")
        key = OrgApiKey.no_workspace_objects.create(organization=org, type="system")
        secret = key.secret_key
        OrgApiKey._base_manager.filter(id=key.id).update(
            secret_key=secret,
            secret_key_hash="",
            secret_key_hint="",
            secret_key_ciphertext="",
        )
        keys.append((key.id, secret))
    return keys


def test_status_then_backfill_as_the_runbook_says(plaintext_keys):
    status = _manage_py("api_secrets", "status")
    assert status.returncode == 1, status.stderr[-2000:]
    assert "2 API keys still hold a plaintext secret" in status.stderr
    assert _reports(status)[-1]["plaintext"] == 2

    refused = _manage_py("api_secrets", "backfill")
    assert refused.returncode != 0
    assert "api_secrets is disabled during mutation-free startup" in refused.stderr
    assert _plaintext_rows() == 2

    operator = {"SERVICE_TYPE": "bootstrap", "STARTUP_DB_MUTATION_MODE": "operator"}
    done = _manage_py("api_secrets", "backfill", **operator)
    assert done.returncode == 0, done.stderr[-2000:]
    backfill, after = _reports(done)
    assert (backfill["sealed"], backfill["system"]) == (2, 2)
    assert after["plaintext"] == 0
    assert after["system_copies"]["configured"] == 2

    clean = _manage_py("api_secrets", "status")
    assert clean.returncode == 0, clean.stderr[-2000:]
    assert _plaintext_rows() == 0
    for key_id, secret in plaintext_keys:
        assert OrgApiKey._base_manager.get(id=key_id).reveal_secret() == secret


OLD_PEPPER = "old-pepper-from-the-secret-store-000000000001"
NEW_PEPPER = "new-pepper-from-the-secret-store-000000000002"


def _status_report(*args):
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("api_secrets", *args, stdout=out)
    return json.loads(out.getvalue().splitlines()[-1])


@pytest.mark.django_db
def test_a_pepper_rotation_ends_at_zero_with_disabled_keys_counted_apart():
    """runbook.md, Rotate the pepper, step 3: wait for 0, then drop the old value.

    The verifier's case: under pepper A one live, one disabled and one deleted key
    per tenant; then A is retired and every live key is used. Before, the count
    included the disabled and deleted keys, which nothing can re-hash, so it never
    reached 0 and the step could not complete.
    """
    from django.test import override_settings

    from accounts.api_key_lookup import find_api_key

    issued = {"live": [], "disabled": []}
    with override_settings(API_SECRET_PEPPER=OLD_PEPPER, API_SECRET_PEPPERS_RETIRED=[]):
        for label in ("a", "b"):
            org = Organization.objects.create(name=f"Rotation tenant {label}")
            for state in ("live", "disabled", "deleted"):
                key = OrgApiKey.no_workspace_objects.create(
                    name=state, organization=org, type="user"
                )
                issued.setdefault(state, []).append((key.api_key, key.secret_key))
    OrgApiKey._base_manager.filter(name="disabled").update(enabled=False)
    OrgApiKey._base_manager.filter(name="deleted").update(deleted=True)
    usable = OrgApiKey.no_workspace_objects.filter(enabled=True, deleted=False)
    with override_settings(
        API_SECRET_PEPPER=NEW_PEPPER, API_SECRET_PEPPERS_RETIRED=[OLD_PEPPER]
    ):
        rotating = _status_report("status")
        for api_key, secret in issued["live"]:
            assert find_api_key(usable, api_key, secret) is not None
        done = _status_report("status")
        after_backfill = _status_report("backfill")
    assert rotating["hashed_under_retired_pepper"] == 2
    assert rotating["hashed_under_retired_pepper_disabled"] == 2
    for report in (done, after_backfill):
        assert report["hashed_under_retired_pepper"] == 0, report
        assert report["hashed_under_retired_pepper_disabled"] == 2, report
    # Nothing re-hashed the disabled keys: re-enabled once the old pepper is
    # dropped, they fail and must be reissued (runbook.md says so).
    OrgApiKey._base_manager.filter(name="disabled").update(enabled=True)
    with override_settings(API_SECRET_PEPPER=NEW_PEPPER, API_SECRET_PEPPERS_RETIRED=[]):
        for api_key, secret in issued["disabled"]:
            assert find_api_key(usable, api_key, secret) is None
        for api_key, secret in issued["live"]:
            assert find_api_key(usable, api_key, secret) is not None


def test_backfill_refuses_a_batch_size_below_one():
    from django.core.management import CommandError, call_command

    with pytest.raises(CommandError, match="at least 1"):
        call_command("api_secrets", "backfill", "--batch-size", "0")
