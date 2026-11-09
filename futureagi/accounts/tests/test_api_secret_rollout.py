"""A pod of the previous release writes beside the new code while a deploy rolls.

Real Postgres (tfc.settings.test), two tenants. Migrations 0026 (the columns)
and 0027 (the seal) run before the backend pods roll (Helm's bootstrap job), so
for the whole rollout pods of the previous release write to the new schema with
their own model (state 0025):

- they create keys without naming the new columns: the hash columns keep a
  database DEFAULT '' and retirement metadata is nullable. Personal keys seal
  on first use; legacy types are refused without re-sealing or retirement
  disclosure until an operator backfill supplies a verifying stored hash;
- they save sealed keys (the Django admin, the AI-tools revoke, the Keys page's
  get-or-create): their ``save()`` regenerates the empty ``secret_key`` and so
  writes a random value beside the real hash. The hash stays the key's record:
  the value is never sealed, presented or revealed, and is cleared on the key's
  next use or by the backfill.
"""

import json
import uuid
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from rest_framework.test import APIClient

from accounts.api_key_lookup import (
    backfill_secret_hashes,
    find_api_key,
    key_revocation_payloads,
)
from accounts.api_secrets import hash_secret, revocation_payloads
from accounts.models import Organization, OrgApiKey, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from accounts.tests.personal_keys import personal_key_for
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

PREVIOUS_RELEASE = (
    "accounts",
    "0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more",
)
HEALTH_URL = "/api/public/health"


def _tenant(label):
    org = Organization.objects.create(name=f"Rollout tenant {label}")
    owner = User.objects.create_user(
        email=f"rollout-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
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
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=owner,
        organization=org,
        defaults={"role": OrganizationRoles.OWNER, "is_active": True},
    )
    clear_workspace_context()
    return org, owner


def _row(key_id):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT secret_key, secret_key_hash, secret_key_ciphertext"
            " FROM accounts_orgapikey WHERE id = %s",
            [str(key_id)],
        )
        return cursor.fetchone()


def _status():
    out = StringIO()
    try:
        call_command("api_secrets", "status", stdout=out)
        failed = False
    except CommandError:
        failed = True
    return json.loads(out.getvalue().splitlines()[-1]), failed


def _authenticates(api_key, secret):
    clear_workspace_context()
    headers = {"HTTP_X_API_KEY": api_key, "HTTP_X_SECRET_KEY": secret}
    return APIClient().get(HEALTH_URL, **headers).status_code == 200


def _assert_admission(api_key, secret, kind, *, sealed=True):
    clear_workspace_context()
    response = APIClient().get(
        HEALTH_URL, HTTP_X_API_KEY=api_key, HTTP_X_SECRET_KEY=secret
    )
    assert response.status_code == (200 if kind == "user" else 401)
    if kind != "user":
        if sealed:
            assert response.json()["code"] == "api_key_retired"
            assert response.json()["create_url"] == "/dashboard/keys"
        else:
            assert response.json()["code"] != "api_key_retired"
            assert "create_url" not in response.json()


def _issued_keys():
    """Historical system rows plus explicitly issued personal keys in two tenants."""
    keys = []
    for label in ("a", "b"):
        org, owner = _tenant(label)
        historical = OrgApiKey.no_workspace_objects.create(
            organization=org, type="system"
        )
        personal = personal_key_for(owner, org)
        keys.extend((key, key.secret_key) for key in (historical, personal))
    return keys


def _clobber(keys):
    """What a previous-release ``save()`` of each sealed row writes: a new random value."""
    stale = {}
    for key, _secret in keys:
        stale[key.id] = uuid.uuid4().hex
        OrgApiKey._base_manager.filter(pk=key.pk).update(secret_key=stale[key.id])
    return stale


@pytest.mark.django_db
def test_previous_release_pod_keys_seal_without_admitting_legacy_types():
    old_apps = MigrationLoader(connection).project_state(PREVIOUS_RELEASE).apps
    PreviousKey = old_apps.get_model("accounts", "OrgApiKey")
    created = []
    for label in ("a", "b"):
        org, owner = _tenant(label)
        for kind, extra in (("system", {}), ("user", {"user_id": owner.id})):
            secret = uuid.uuid4().hex
            # The previous release's model knows none of the new columns.
            row = PreviousKey._base_manager.create(
                organization_id=org.id,
                type=kind,
                api_key=uuid.uuid4().hex,
                secret_key=secret,
                **extra,
            )
            created.append((row.id, row.api_key, secret, kind))
    assert all(_row(key_id)[0:2] == (secret, "") for key_id, _, secret, _ in created)
    status, failed = _status()
    assert failed and status["plaintext"] == 4 and status["plaintext_beside_hash"] == 0

    for key_id, api_key, secret, kind in created:
        assert not _authenticates(api_key, secret + "x")
        assert _row(key_id)[0] == secret, "a wrong secret must not re-seal the row"
        _assert_admission(api_key, secret, kind, sealed=False)
        plain, stored_hash, ciphertext = _row(key_id)
        if kind == "user":
            assert plain == "" and stored_hash == hash_secret(secret)
            assert ciphertext == ""
        else:
            # No verifying stored hash: refuse generically and leave sealing to
            # the operator, rather than disclosing a historical key's existence.
            assert (plain, stored_hash, ciphertext) == (secret, "", "")
        _assert_admission(api_key, secret, kind, sealed=False)
    status, failed = _status()
    assert failed and status["plaintext"] == 2

    counts = backfill_secret_hashes(OrgApiKey)
    assert counts["sealed"] == counts["system"] == 2
    assert counts["failed"] == 0
    for key_id, api_key, secret, kind in created:
        plain, stored_hash, ciphertext = _row(key_id)
        assert plain == "" and stored_hash == hash_secret(secret)
        assert bool(ciphertext) == (kind == "system")
        if kind == "system":
            assert OrgApiKey._base_manager.get(id=key_id).reveal_secret() == secret
        _assert_admission(api_key, secret, kind)
        assert not _authenticates(api_key, secret + "x")
    status, failed = _status()
    assert not failed and status["plaintext"] == 0
    assert backfill_secret_hashes(OrgApiKey)["sealed"] == 0


@pytest.mark.django_db
def test_backfill_clears_a_stale_plaintext_and_keeps_the_hash():
    keys = _issued_keys()
    hashes = {key.id: _row(key.id)[1] for key, _secret in keys}
    stale = _clobber(keys)
    before, failed_before = _status()

    counts = backfill_secret_hashes(OrgApiKey)
    for key, secret in keys:
        assert _row(key.id)[:2] == ("", hashes[key.id])
        _assert_admission(key.api_key, secret, key.type)
        assert not _authenticates(key.api_key, stale[key.id])
    assert counts["stale_cleared"] == len(keys) and counts["sealed"] == 0
    assert backfill_secret_hashes(OrgApiKey)["stale_cleared"] == 0
    assert failed_before and before["plaintext_beside_hash"] == len(keys)
    after = {**before, "plaintext": 0, "plaintext_beside_hash": 0}
    assert _status() == (after, False)


@pytest.mark.django_db
def test_a_stale_plaintext_is_never_revealed_or_sealed_by_a_save():
    keys = _issued_keys()
    hashes = {key.id: _row(key.id)[1] for key, _secret in keys}
    stale = _clobber(keys)
    for key, secret in keys:
        loaded = OrgApiKey._base_manager.get(id=key.id)
        assert loaded.secret_key == stale[key.id]
        if key.type == "system":
            # The historical recoverable copy is kept for rollback/invalidation,
            # not permission for the collector or relay to present this key.
            assert loaded.reveal_secret() == secret
            assert key_revocation_payloads(loaded) == revocation_payloads(
                key.api_key, secret
            )
        else:
            assert key_revocation_payloads(loaded) == revocation_payloads(key.api_key)
        # A full save, as the Django admin or a revoke does.
        loaded.name = "renamed"
        loaded.save()
        assert _row(key.id)[:2] == ("", hashes[key.id])
        _assert_admission(key.api_key, secret, key.type)
        assert not _authenticates(key.api_key, stale[key.id])


@pytest.mark.django_db
def test_use_of_the_real_secret_clears_a_stale_plaintext():
    keys = _issued_keys()
    hashes = {key.id: _row(key.id)[1] for key, _secret in keys}
    stale = _clobber(keys)
    queryset = OrgApiKey.no_workspace_objects.filter(enabled=True)
    for key, secret in keys:
        assert find_api_key(queryset, key.api_key, stale[key.id]) is None
        assert _row(key.id)[0] == stale[key.id]
        assert find_api_key(queryset, key.api_key, secret) is not None
        assert _row(key.id)[:2] == ("", hashes[key.id])
