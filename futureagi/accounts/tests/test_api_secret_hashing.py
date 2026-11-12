"""API secrets are stored as a keyed hash and every key keeps working (READINESS L03).

Real Postgres (tfc.settings.test), two tenants, every authentication path:
DRF header auth, Langfuse Basic auth, Omega observe-target and MCP. The
migration, rollout, system-key, revocation and timing tests are in
test_api_secret_migration.py, test_api_secret_rollout.py,
test_api_secret_system_keys.py, test_api_key_revocation.py and
test_api_secret_timing.py.
"""

import base64
import uuid

import pytest
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient

from accounts import api_secrets
from accounts.api_key_lookup import backfill_secret_hashes, find_api_key
from accounts.api_secrets import ApiSecretConfigError, ApiSecretNotRecoverable
from accounts.models import Organization, OrgApiKey, User
from accounts.models.workspace import Workspace
from accounts.tests.personal_keys import personal_key_for, session_client_for
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

HEALTH_URL = "/api/public/health"

# Pinned in fi-collector/pkg/auth/secrethash_test.go too (and checked with openssl).
GOLDEN_PEPPER = "golden-vector-pepper-0123456789abcdef"
GOLDEN_SECRET = "golden-secret-4f1c2b"
GOLDEN_HASH = "hmac-sha256$5c1fdc64$0fe42b43c7243f5c62a9d9eec31c02b5813c7f702d0ff8b90d2ab549b59d32d9"
GOLDEN_LOCAL_HASH = "hmac-sha256$b9df05ee$d577a3daef0ced43290fb3a11c8d9ab5f76bb70d7729894d3950c937d8152f17"
GOLDEN_REVOKE = (
    "apikey:71a099d844d0c4e0540283ac182aeb5d01991f97fa705e686bbf2d232b59b9f6"
)


def _stored(key_id):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT secret_key, secret_key_hash, secret_key_hint, secret_key_ciphertext"
            " FROM accounts_orgapikey WHERE id = %s",
            [str(key_id)],
        )
        return cursor.fetchone()


def _headers(api_key, secret):
    return {"HTTP_X_API_KEY": api_key, "HTTP_X_SECRET_KEY": secret}


def _basic(api_key, secret):
    token = base64.b64encode(f"{api_key}:{secret}".encode()).decode()
    return {"HTTP_AUTHORIZATION": f"Basic {token}"}


@pytest.fixture
def tenant_b(db):
    org = Organization.objects.create(name="Tenant B")
    owner = User.objects.create_user(
        email=f"tenant-b-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Tenant B Owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    Workspace.objects.create(
        name="B", organization=org, is_default=True, is_active=True, created_by=owner
    )
    clear_workspace_context()
    return org, owner


def _issue_user_key(owner, name="ci"):
    key = personal_key_for(owner, owner.organization, name=name)
    return key, key.secret_key  # the issuing instance holds the plaintext


@pytest.mark.django_db
class TestStorage:
    def test_golden_vector_matches_fi_collector(self):
        with override_settings(API_SECRET_PEPPER=GOLDEN_PEPPER, ENV_TYPE="production"):
            assert api_secrets.hash_secret(GOLDEN_SECRET) == GOLDEN_HASH
        with override_settings(
            API_SECRET_PEPPER="", ENV_TYPE="local", SECRET_KEY="golden-secret-key"
        ):
            assert api_secrets.hash_secret(GOLDEN_SECRET) == GOLDEN_LOCAL_HASH
        assert api_secrets.revocation_payloads("golden-api-key") == [GOLDEN_REVOKE]

    def test_issued_user_key_keeps_no_plaintext(self, user):
        key, secret = _issue_user_key(user)
        plain, stored_hash, hint, ciphertext = _stored(key.id)
        assert plain == "" and ciphertext == ""
        assert stored_hash == api_secrets.hash_secret(secret)
        assert secret not in stored_hash and secret not in hint
        from model_hub.models.api_key import mask_key

        assert hint == mask_key(secret)
        with pytest.raises(ApiSecretNotRecoverable):
            OrgApiKey.no_workspace_objects.get(id=key.id).reveal_secret()

    def test_system_key_is_encrypted_not_plaintext(self, user):
        key = OrgApiKey.no_workspace_objects.create(
            organization=user.organization, type="system"
        )
        secret = key.secret_key
        plain, stored_hash, _hint, ciphertext = _stored(key.id)
        assert plain == "" and secret not in ciphertext
        assert stored_hash == api_secrets.hash_secret(secret)
        assert OrgApiKey.no_workspace_objects.get(id=key.id).reveal_secret() == secret

    def test_update_fields_save_never_writes_plaintext(self, user):
        key, secret = _issue_user_key(user)
        key.enabled = False
        key.save(update_fields=["enabled"])
        assert _stored(key.id)[0] == ""
        assert key.secret_key == secret

    def test_backfill_seals_plaintext_rows_and_is_idempotent(self, user):
        key, secret = _issue_user_key(user)
        OrgApiKey._base_manager.filter(id=key.id).update(
            secret_key=secret, secret_key_hash="", secret_key_hint=""
        )
        assert backfill_secret_hashes(OrgApiKey)["sealed"] == 1
        assert backfill_secret_hashes(OrgApiKey)["sealed"] == 0
        assert _stored(key.id)[0] == ""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM accounts_orgapikey WHERE secret_key <> ''"
            )
            assert cursor.fetchone()[0] == 0


@pytest.mark.django_db
class TestEveryAuthenticationPath:
    def test_header_auth_accepts_right_secret_refuses_wrong(self, user):
        key, secret = _issue_user_key(user)
        client = APIClient()
        assert (
            client.get(HEALTH_URL, **_headers(key.api_key, secret)).status_code == 200
        )
        assert client.get(
            HEALTH_URL, **_headers(key.api_key, secret + "x")
        ).status_code in (401, 403)
        assert client.get(HEALTH_URL, **_headers(key.api_key, "")).status_code in (
            401,
            403,
        )
        assert client.get(
            HEALTH_URL, **_headers("no-such-key", secret)
        ).status_code in (401, 403)

    def test_langfuse_basic_auth(self, user):
        key, secret = _issue_user_key(user)
        client = APIClient()
        assert client.get(HEALTH_URL, **_basic(key.api_key, secret)).status_code == 200
        assert client.get(HEALTH_URL, **_basic(key.api_key, "wrong")).status_code == 401

    def test_issue_endpoint_returns_the_secret_once_and_it_authenticates(
        self, user, workspace
    ):
        auth_client = session_client_for(user, user.organization, workspace)
        response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "shown-once"},
            format="json",
        )
        result = response.json()["result"]
        plain, _hash, hint, _ct = _stored(result["key_id"])
        assert plain == "" and hint == result["masked_secret_key"]
        listed = auth_client.get("/accounts/key/get_secret_keys/").json()["result"][
            "table"
        ]
        row = next(r for r in listed if r["key_name"] == "shown-once")
        assert row["secret_key"] == result["masked_secret_key"]
        assert result["secret_key"] not in str(listed)
        assert (
            APIClient()
            .get(HEALTH_URL, **_headers(result["api_key"], result["secret_key"]))
            .status_code
            == 200
        )

    def test_keys_bootstrap_retires_without_revealing_or_issuing_secrets(
        self, auth_client
    ):
        before = OrgApiKey._base_manager.count()
        response = auth_client.get("/accounts/keys/")
        assert response.status_code == 410
        assert response.json() == {
            "code": "org_keys_retired",
            "create_url": "/dashboard/keys",
        }
        assert OrgApiKey._base_manager.count() == before
        assert "api_key" not in response.json()
        assert "secret_key" not in response.json()

    def test_mcp_transport(self, user):
        from mcp_server.mcp_app import _authenticate_and_set_context

        key, secret = _issue_user_key(user)
        assert _authenticate_and_set_context(key.api_key, secret) is not None
        assert _authenticate_and_set_context(key.api_key, secret[:-1]) is None

    def test_two_tenants_never_cross(self, user, tenant_b):
        _org_b, owner_b = tenant_b
        key_a, secret_a = _issue_user_key(user, "a")
        key_b, secret_b = _issue_user_key(owner_b, "b")
        client = APIClient()
        assert client.get(
            HEALTH_URL, **_headers(key_a.api_key, secret_b)
        ).status_code in (401, 403)
        assert client.get(
            HEALTH_URL, **_headers(key_b.api_key, secret_a)
        ).status_code in (401, 403)
        assert (
            client.get(HEALTH_URL, **_headers(key_b.api_key, secret_b)).status_code
            == 200
        )
        assert _stored(key_a.id)[1] != _stored(key_b.id)[1]


@pytest.mark.django_db
class TestMigrationOfLiveKeys:
    def test_legacy_plaintext_row_works_once_then_is_resealed(self, user):
        key, secret = _issue_user_key(user)
        OrgApiKey._base_manager.filter(id=key.id).update(
            secret_key=secret, secret_key_hash="", secret_key_hint=""
        )
        client = APIClient()
        assert client.get(
            HEALTH_URL, **_headers(key.api_key, secret + "x")
        ).status_code in (401, 403)
        assert _stored(key.id)[0] == secret, "a wrong secret must not re-seal the row"
        assert (
            client.get(HEALTH_URL, **_headers(key.api_key, secret)).status_code == 200
        )
        plain, stored_hash, hint, _ct = _stored(key.id)
        assert plain == "" and stored_hash == api_secrets.hash_secret(secret) and hint
        assert (
            client.get(HEALTH_URL, **_headers(key.api_key, secret)).status_code == 200
        )

    def test_retired_pepper_verifies_then_rehashes_under_the_current(self, user):
        old = "old-pepper-from-the-secret-store-000000000001"
        new = "new-pepper-from-the-secret-store-000000000002"
        with override_settings(API_SECRET_PEPPER=old):
            key, secret = _issue_user_key(user)
            old_hash = _stored(key.id)[1]
        qs = OrgApiKey.no_workspace_objects.filter(enabled=True)
        with override_settings(API_SECRET_PEPPER=new, API_SECRET_PEPPERS_RETIRED=[]):
            assert find_api_key(qs, key.api_key, secret) is None, (
                "an unlisted pepper must not verify"
            )
        with override_settings(API_SECRET_PEPPER=new, API_SECRET_PEPPERS_RETIRED=[old]):
            assert find_api_key(qs, key.api_key, secret) is not None
            new_hash = _stored(key.id)[1]
            assert new_hash != old_hash and new_hash == api_secrets.hash_secret(secret)
        with override_settings(API_SECRET_PEPPER=new, API_SECRET_PEPPERS_RETIRED=[]):
            assert find_api_key(qs, key.api_key, secret) is not None

    def test_overlong_inputs_are_refused_without_a_lookup(self, user):
        key, _secret = _issue_user_key(user)
        qs = OrgApiKey.no_workspace_objects.all()
        assert find_api_key(qs, key.api_key, "x" * 256) is None
        assert find_api_key(qs, "k" * 256, "x") is None


class TestConfiguration:
    def test_non_local_install_without_a_pepper_fails_closed(self):
        with override_settings(API_SECRET_PEPPER="", ENV_TYPE="production"):
            with pytest.raises(ApiSecretConfigError, match="FI_API_SECRET_PEPPER"):
                api_secrets.hash_secret("anything")

    def test_short_pepper_is_refused(self):
        with override_settings(API_SECRET_PEPPER="short", ENV_TYPE="production"):
            with pytest.raises(ApiSecretConfigError):
                api_secrets.hash_secret("anything")

    def test_non_local_install_without_an_encryption_key_fails_closed(self):
        with override_settings(API_SECRET_ENCRYPTION_KEY="", ENV_TYPE="production"):
            with pytest.raises(
                ApiSecretConfigError, match="INTEGRATION_ENCRYPTION_KEY"
            ):
                api_secrets.encrypt_secret("anything")


@pytest.mark.django_db
def test_api_secrets_command_reports_and_finishes_the_backfill(user):
    import json
    from io import StringIO

    from django.core.management import CommandError, call_command

    key, secret = _issue_user_key(user)
    OrgApiKey._base_manager.filter(id=key.id).update(
        secret_key=secret, secret_key_hash=""
    )
    with pytest.raises(CommandError, match="plaintext"):
        call_command("api_secrets", "status", stdout=StringIO())
    out = StringIO()
    call_command("api_secrets", "backfill", stdout=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert lines[0]["sealed"] == 1 and lines[-1]["plaintext"] == 0
    assert lines[-1]["current_pepper_id"] == api_secrets.peppers()[0].id
