"""Personal issuance after signup, and retained ciphertext for legacy rollback.

Signup leaves zero keys. Explicit personal fixtures keep only hashes and stay
isolated across tenants. Legacy ciphertext remains decryptable for rollback
and rotation tests, but never authenticates. Internal span emission is OFF.
The gateway config injection assertions are retained for its replacement lane.
"""

import base64
import json
import uuid
from io import StringIO
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient

from accounts import api_secrets
from accounts.api_secrets import ApiSecretNotRecoverable
from accounts.models import Organization, OrgApiKey, User
from accounts.models.workspace import Workspace
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

HEALTH_URL = "/api/public/health"
# Shaped as deploy/setup.sh writes them: openssl rand -hex 32, and 32 random
# bytes in URL-safe base64.
PRODUCTION_PEPPER = "5f0c" * 16
CONFIGURED_KEY = base64.urlsafe_b64encode(bytes(range(200, 232))).decode()
OTHER_KEY = Fernet.generate_key().decode()
PASSWORD = "Provisioning-test-pass-9341!"


def _row(key_id):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT secret_key, secret_key_hash, secret_key_ciphertext"
            " FROM accounts_orgapikey WHERE id = %s",
            [str(key_id)],
        )
        return cursor.fetchone()


def _headers(api_key, secret):
    return {"HTTP_X_API_KEY": api_key, "HTTP_X_SECRET_KEY": secret}


def _tenant(label):
    org = Organization.objects.create(name=f"System-key tenant {label}")
    owner = User.objects.create_user(
        email=f"system-key-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password=PASSWORD,
        name=f"Owner {label}",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    Workspace.objects.create(
        name=f"WS {label}",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    clear_workspace_context()
    return org


def _command(action):
    out = StringIO()
    call_command("api_secrets", action, stdout=out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _status():
    return _command("status")[-1]


def _authenticates(api_key, secret):
    try:
        response = APIClient().get(HEALTH_URL, **_headers(api_key, secret))
        return response.status_code == 200
    finally:
        # Authentication sets the key's workspace context; WorkspaceContextMiddleware
        # clears it after every request, but tfc.settings.test does not run it, so
        # the next request would be filtered to this tenant's workspace.
        clear_workspace_context()


def _assert_retired(api_key, secret):
    try:
        response = APIClient().get(HEALTH_URL, **_headers(api_key, secret))
        assert response.status_code == 401
        assert response.json()["code"] == "api_key_retired"
        assert response.json()["create_url"] == "/dashboard/keys"
    finally:
        clear_workspace_context()


@pytest.fixture
def quiet_signup():
    """first_signup also mails the owner and reports to Mixpanel."""
    with (
        patch("accounts.utils.process_post_registration"),
        patch("accounts.utils.track_mixpanel_event"),
        patch("accounts.utils.mixpanel_tracker"),
    ):
        yield


def test_the_configured_key_is_a_fernet_key():
    Fernet(CONFIGURED_KEY.encode())  # the fixture must be what setup.sh writes


@pytest.mark.django_db
def test_each_tenant_signup_is_keyless_then_personal_issuance_is_hashed(
    quiet_signup, settings
):
    from accounts.utils import first_signup

    settings.CLOUD_DEPLOYMENT = ""  # a self-hosted production compose install
    production = override_settings(
        ENV_TYPE="production",
        API_SECRET_PEPPER=PRODUCTION_PEPPER,
        API_SECRET_ENCRYPTION_KEY=CONFIGURED_KEY,
    )
    with production:
        owners = [
            first_signup(
                {
                    "email": f"owner@tenant-{label}-{uuid.uuid4().hex[:8]}.example.com",
                    "full_name": f"Owner {label}",
                    "company_name": "",
                    "password": PASSWORD,
                }
            )
            for label in "ab"
        ]
        from accounts.tests.personal_keys import personal_key_for

        for owner in owners:
            assert not OrgApiKey._base_manager.filter(
                organization=owner.organization
            ).exists()
        keys = [personal_key_for(owner, owner.organization) for owner in owners]
        assert keys[0].organization_id != keys[1].organization_id
        secrets = []
        for key in keys:
            plaintext, stored_hash, ciphertext = _row(key.id)
            assert plaintext == ""
            assert ciphertext == ""
            assert key.type == "user" and key.user_id is not None
            secret = key.secret_key
            assert stored_hash == api_secrets.hash_secret(secret)
            assert secret not in ciphertext and secret not in stored_hash
            assert _authenticates(key.api_key, secret)
            secrets.append(secret)
        assert not _authenticates(keys[0].api_key, secrets[1])
        assert not _authenticates(keys[1].api_key, secrets[0])
        status = _status()
        assert status["plaintext"] == 0 and status["system_without_encrypted_copy"] == 0
        assert status["system_copies"] == {
            "configured": 0,
            "derived": 0,
            "undecryptable": 0,
        }


@pytest.mark.django_db
def test_legacy_ciphertext_is_preserved_but_keys_stay_retired():
    orgs = [_tenant("a"), _tenant("b")]
    with override_settings(ENV_TYPE="local", API_SECRET_ENCRYPTION_KEY=""):
        keys = [
            OrgApiKey.no_workspace_objects.create(organization=org, type="system")
            for org in orgs
        ]
        secrets = [key.secret_key for key in keys]  # the issuing instances
    assert all(api_secrets.ciphertext_key(_row(k.id)[2]) == "derived" for k in keys)

    # ./bin/install fills INTEGRATION_ENCRYPTION_KEY on an existing install.
    with override_settings(ENV_TYPE="local", API_SECRET_ENCRYPTION_KEY=CONFIGURED_KEY):
        for key, secret in zip(keys, secrets, strict=True):
            assert (
                OrgApiKey.no_workspace_objects.get(id=key.id).reveal_secret() == secret
            )
            _assert_retired(key.api_key, secret)
        assert _status()["system_copies"]["derived"] == 2
        # Under the derived key the copy still depends on SECRET_KEY.
        with override_settings(SECRET_KEY="another-secret-key"):
            with pytest.raises(ApiSecretNotRecoverable):
                OrgApiKey.no_workspace_objects.get(id=keys[0].id).reveal_secret()

        backfill = _command("backfill")[0]
        assert backfill["system_copies_moved"] == {"reencrypted": 2, "skipped": 0}
        assert _command("backfill")[0]["system_copies_moved"]["reencrypted"] == 0
        assert _status()["system_copies"] == {
            "configured": 2,
            "derived": 0,
            "undecryptable": 0,
        }
        for key, secret in zip(keys, secrets, strict=True):
            plaintext, stored_hash, ciphertext = _row(key.id)
            assert plaintext == "" and stored_hash == api_secrets.hash_secret(secret)
            opened = Fernet(CONFIGURED_KEY.encode()).decrypt(ciphertext.encode())
            assert opened.decode() == secret
            with override_settings(SECRET_KEY="another-secret-key"):
                loaded = OrgApiKey.no_workspace_objects.get(id=key.id)
                assert loaded.reveal_secret() == secret
            _assert_retired(key.api_key, secret)


@pytest.mark.django_db
def test_a_copy_no_key_opens_is_reported_and_never_presented_half():
    from agentcc.services.config_push import _inject_fi_credentials
    from tracer.services.collector_ingest import resolve_system_emit_credentials

    stranded_org, healthy_org = _tenant("stranded"), _tenant("healthy")
    with override_settings(API_SECRET_ENCRYPTION_KEY=OTHER_KEY):
        # Sealed under a key that is no longer configured.
        OrgApiKey.no_workspace_objects.create(organization=stranded_org, type="system")
    with override_settings(API_SECRET_ENCRYPTION_KEY=CONFIGURED_KEY):
        healthy = OrgApiKey.no_workspace_objects.create(
            organization=healthy_org, type="system"
        )
        healthy_secret = healthy.secret_key
        assert _status()["system_copies"] == {
            "configured": 1,
            "derived": 0,
            "undecryptable": 1,
        }

        typed = {"api_key": "typed-key", "secret_key": "typed-secret"}
        stranded_checks = {"futureagi-eval": {"enabled": True, "config": dict(typed)}}
        _inject_fi_credentials(stranded_checks, stranded_org.id)
        stranded_cfg = stranded_checks["futureagi-eval"]["config"]
        assert {k: stranded_cfg[k] for k in typed} == typed

        healthy_checks = {"futureagi-eval": {"enabled": True, "config": {}}}
        _inject_fi_credentials(healthy_checks, healthy_org.id)
        healthy_cfg = healthy_checks["futureagi-eval"]["config"]
        assert (healthy_cfg["api_key"], healthy_cfg["secret_key"]) == (
            healthy.api_key,
            healthy_secret,
        )

        assert resolve_system_emit_credentials(str(stranded_org.id), None) is None
        before = OrgApiKey._base_manager.count()
        assert resolve_system_emit_credentials(str(healthy_org.id), None) is None
        assert OrgApiKey._base_manager.count() == before
        from tracer.services.collector_ingest import PLATFORM_EMISSION_STATE

        assert PLATFORM_EMISSION_STATE == "OFF"
