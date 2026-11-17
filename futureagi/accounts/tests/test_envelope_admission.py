"""Gateway eval admission and durable, transaction-independent replay state."""

import hashlib
import importlib
import time
import uuid
from unittest.mock import patch

import pytest
from agentcc.models.api_key import AgentccAPIKey
from agentcc.services.key_revocation_authority import (
    CREDENTIAL_REVOCATIONS,
    LEGACY_REVOCATIONS,
)
from django.db import connection, transaction
from django.test import RequestFactory
from rest_framework.exceptions import AuthenticationFailed
from tfc.service_envelope import sign_request

from accounts.authentication import ServiceEnvelopeEvalAuthentication
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.services.envelope_keys import pair_keys
from accounts.services.envelope_store import DurableNonceStore

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def nonce_schema(settings):
    settings.AGENTCC_ADMIN_TOKEN = "test_only_admin_pair_for_envelope_testing_000000"
    # nomigrations test environments do not create unmanaged tables. Use the
    # migration's exact DDL, not a second test-only interpretation of the schema.
    if "accounts_envelope_nonce" not in connection.introspection.table_names():
        migration = importlib.import_module("accounts.migrations.0032_service_grants")
        with connection.cursor() as cursor:
            cursor.execute(migration.Migration.operations[-1].sql)


def test_durable_nonce_survives_eval_rollback_and_new_store():
    identifier = uuid.uuid4().hex
    now = int(time.time())
    with pytest.raises(RuntimeError), transaction.atomic():
        assert DurableNonceStore().consume("fa-eval", identifier, now, now + 60, now)
        raise RuntimeError("eval failed")
    assert not DurableNonceStore().consume("fa-eval", identifier, now, now + 60, now)


def test_durable_nonce_does_not_depend_on_default_cache():
    from django.core.cache import caches

    identifier = uuid.uuid4().hex
    now = int(time.time())
    assert DurableNonceStore().consume("fa-eval", identifier, now, now + 60, now)
    caches["default"].clear()
    assert not DurableNonceStore().consume("fa-eval", identifier, now, now + 60, now)


def signed_request(user, workspace=None, *, route="/sdk/api/v1/eval/"):
    token = sign_request(
        keys=pair_keys("gateway", "fa-eval"),
        issuer="gateway",
        audience="fa-eval",
        method="POST",
        route=route,
        body=b"{}",
        org=user.organization_id,
        user=user.pk,
        workspace=workspace.pk if workspace else "-",
        via="key:test_key",
        scope="eval",
    )
    return RequestFactory().post(
        route, data=b"{}", content_type="application/json", HTTP_X_FA_ENVELOPE=token
    )


class RevocationSets:
    """The gateway's revocation sets, in memory; empty until a test revokes."""

    def __init__(self):
        self.members = {LEGACY_REVOCATIONS: set(), CREDENTIAL_REVOCATIONS: set()}
        self.lookups = []

    def sismember(self, name, member):
        self.lookups.append((name, member))
        return member in self.members[name]

    def close(self):
        pass


@pytest.fixture
def revocations(settings, monkeypatch):
    settings.REDIS_URL = "redis://127.0.0.1:6379/0"
    monkeypatch.setenv("AGENTCC_REDIS_DB", "0")
    monkeypatch.setenv("REDIS_URL", settings.REDIS_URL)
    sets = RevocationSets()
    with patch("redis.Redis.from_url", return_value=sets):
        yield sets


@pytest.fixture
def gateway_key(user):
    return AgentccAPIKey.no_workspace_objects.create(
        organization=user.organization,
        user=user,
        gateway_key_id="test_key",
        name="test-only-person",
        key_hash=hashlib.sha256(b"test-only-envelope-admission-key").hexdigest(),
        status=AgentccAPIKey.ACTIVE,
    )


def test_eval_principal_is_the_person(user, workspace, gateway_key, revocations):
    request = signed_request(user, workspace)
    actual, _ = ServiceEnvelopeEvalAuthentication().authenticate(request)
    assert actual.pk == user.pk
    assert request.organization.pk == user.organization_id
    assert request.workspace.pk == workspace.pk
    assert request.credential_provenance == "service_envelope"
    with pytest.raises(AuthenticationFailed):
        ServiceEnvelopeEvalAuthentication().authenticate(request)


@pytest.mark.parametrize(
    "state",
    [
        "revoked",
        "org_removed",
        "workspace_removed",
        "other_org",
        "inactive",
        "other_route",
    ],
)
def test_eval_requires_live_matching_person(
    user, workspace, gateway_key, revocations, state
):
    request = signed_request(
        user,
        workspace,
        route="/sdk/api/v1/evaluate-pipeline/"
        if state == "other_route"
        else "/sdk/api/v1/eval/",
    )
    if state == "revoked":
        AgentccAPIKey._base_manager.filter(pk=gateway_key.pk).update(
            status=AgentccAPIKey.REVOKED
        )
    elif state == "other_org":
        from accounts.models import Organization

        other = Organization.objects.create(name="Other test org")
        # A key's organization cannot change; retire it and reissue its id there.
        AgentccAPIKey._base_manager.filter(pk=gateway_key.pk).update(deleted=True)
        AgentccAPIKey.no_workspace_objects.create(
            organization=other,
            user=user,
            gateway_key_id=gateway_key.gateway_key_id,
            name=gateway_key.name,
            key_hash=gateway_key.key_hash,
            status=AgentccAPIKey.ACTIVE,
        )
    elif state == "inactive":
        type(user)._base_manager.filter(pk=user.pk).update(is_active=False)
    elif state == "org_removed":
        OrganizationMembership._base_manager.filter(user=user).update(is_active=False)
    elif state == "workspace_removed":
        WorkspaceMembership._base_manager.filter(user=user, workspace=workspace).update(
            is_active=False
        )
    with pytest.raises(AuthenticationFailed):
        ServiceEnvelopeEvalAuthentication().authenticate(request)
    if state in ("org_removed", "workspace_removed", "inactive"):
        # These pass the revocation check; the live-person check refuses them.
        assert (CREDENTIAL_REVOCATIONS, gateway_key.key_hash) in revocations.lookups


def test_eval_refuses_a_key_the_gateway_revoked(
    user, workspace, gateway_key, revocations
):
    revocations.members[CREDENTIAL_REVOCATIONS].add(gateway_key.key_hash)
    request = signed_request(user, workspace)
    with pytest.raises(AuthenticationFailed, match="service_envelope_rejected"):
        ServiceEnvelopeEvalAuthentication().authenticate(request)
    assert (CREDENTIAL_REVOCATIONS, gateway_key.key_hash) in revocations.lookups


def test_no_envelope_preserves_personal_authentication():
    request = RequestFactory().post("/sdk/api/v1/eval/", data={})
    assert ServiceEnvelopeEvalAuthentication().authenticate(request) is None
