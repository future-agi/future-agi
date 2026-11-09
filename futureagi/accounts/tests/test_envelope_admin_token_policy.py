"""Envelope keys derived from AGENTCC_ADMIN_TOKEN obey the admin-token policy.

The gateway signs fa-eval requests, and the backend signs gw-delegated ones,
with an HMAC key derived from AGENTCC_ADMIN_TOKEN. The compose files default
that token to a value published in this repository, so on an install left on
the default anyone could mint an fa-eval envelope and run evals as a person
of their choosing. The backend's admin endpoints already refuse such a token
(agentcc/admin_token.py); the envelope key ring refuses it too, with the same
dev-only opt-in.
"""

import importlib
import secrets

import pytest
from django.db import connection
from django.test import RequestFactory
from rest_framework.exceptions import AuthenticationFailed

from accounts.authentication import ServiceEnvelopeEvalAuthentication
from accounts.services.envelope_keys import current_kid, pair_keys
from agentcc.admin_token import ENV_ALLOW_INSECURE_ADMIN_TOKEN, PUBLISHED_ADMIN_TOKENS
from agentcc.models.api_key import AgentccAPIKey
from tfc.service_envelope import PAIR_SETTINGS, EnvelopeRejected, sign_request

EVAL_ROUTE = "/sdk/api/v1/eval/"
ADMIN_TOKEN_PAIRS = [("gateway", "fa-eval"), ("fa", "gw-delegated")]
PAIR_IDS = [f"{issuer}>{audience}" for issuer, audience in ADMIN_TOKEN_PAIRS]
SOURCES = ["setting", "env"]
GATEWAY_KEY_ID = "test_key_admin_token_policy"

# docker-compose.yml and docker-compose.distributed.yml default
# AGENTCC_ADMIN_TOKEN to this value.
COMPOSE_DEFAULT = "local-dev-only-admin-token-replace-me"
# Each is at least 32 bytes, so only the policy, not the length floor,
# refuses it. Test-only values.
REFUSED = {
    "compose-default": COMPOSE_DEFAULT,
    "replace-me": "prod-gateway-admin-token-replace-me-0123456789",
    "changeme": "changeme-7f3a9c1e5b2d4f6a8c0e1b3d5f7a9c1e",
}
# 31 bytes of hex: refused by the length floor before this policy existed.
SHORT = "7f3a9c1e5b2d4f6a8c0e1b3d5f7a9c1"


@pytest.fixture(autouse=True)
def secure_by_default(monkeypatch):
    monkeypatch.delenv(ENV_ALLOW_INSECURE_ADMIN_TOKEN, raising=False)
    monkeypatch.delenv("AGENTCC_ADMIN_TOKEN", raising=False)


def set_admin_token(settings, monkeypatch, source, value):
    """Through the Django setting, or the environment alone as deployments do."""
    if source == "setting":
        settings.AGENTCC_ADMIN_TOKEN = value
        return
    if hasattr(settings, "AGENTCC_ADMIN_TOKEN"):
        del settings.AGENTCC_ADMIN_TOKEN
    monkeypatch.setenv("AGENTCC_ADMIN_TOKEN", value)


def test_the_cases_are_the_ones_the_length_floor_misses():
    for pair in ADMIN_TOKEN_PAIRS:
        assert PAIR_SETTINGS[pair] == "AGENTCC_ADMIN_TOKEN"
    assert COMPOSE_DEFAULT in PUBLISHED_ADMIN_TOKENS
    assert all(len(value.encode()) >= 32 for value in REFUSED.values())
    assert len(SHORT.encode()) == 31


@pytest.mark.parametrize("token", [*REFUSED.values(), SHORT], ids=[*REFUSED, "31"])
@pytest.mark.parametrize("pair", ADMIN_TOKEN_PAIRS, ids=PAIR_IDS)
@pytest.mark.parametrize("source", SOURCES)
def test_refused_admin_token_derives_no_envelope_key(
    settings, monkeypatch, source, pair, token
):
    set_admin_token(settings, monkeypatch, source, token)
    with pytest.raises(EnvelopeRejected):
        pair_keys(*pair)


@pytest.mark.parametrize("token", REFUSED.values(), ids=list(REFUSED))
@pytest.mark.parametrize("pair", ADMIN_TOKEN_PAIRS, ids=PAIR_IDS)
@pytest.mark.parametrize("source", SOURCES)
def test_dev_opt_in_keeps_the_key_it_derived_before(
    settings, monkeypatch, source, pair, token
):
    monkeypatch.setenv(ENV_ALLOW_INSECURE_ADMIN_TOKEN, "1")
    set_admin_token(settings, monkeypatch, source, token)
    assert pair_keys(*pair) == {(*pair, current_kid()): token.encode()}


@pytest.mark.parametrize("pair", ADMIN_TOKEN_PAIRS, ids=PAIR_IDS)
def test_dev_opt_in_does_not_lift_the_length_floor(settings, monkeypatch, pair):
    monkeypatch.setenv(ENV_ALLOW_INSECURE_ADMIN_TOKEN, "1")
    set_admin_token(settings, monkeypatch, "env", SHORT)
    with pytest.raises(EnvelopeRejected):
        pair_keys(*pair)


@pytest.mark.parametrize("pair", ADMIN_TOKEN_PAIRS, ids=PAIR_IDS)
@pytest.mark.parametrize("source", SOURCES)
def test_random_admin_token_still_keys_envelopes(settings, monkeypatch, source, pair):
    token = secrets.token_hex(32)
    set_admin_token(settings, monkeypatch, source, token)
    assert pair_keys(*pair) == {(*pair, current_kid()): token.encode()}


def test_standalone_eval_admits_service_envelopes():
    from django.urls import resolve

    view = resolve(EVAL_ROUTE).func.view_class
    assert ServiceEnvelopeEvalAuthentication in view.authentication_classes


@pytest.fixture
def nonce_table(transactional_db):
    # nomigrations test environments do not create unmanaged tables; use the
    # migration's own DDL.
    if "accounts_envelope_nonce" not in connection.introspection.table_names():
        migration = importlib.import_module("accounts.migrations.0032_service_grants")
        with connection.cursor() as cursor:
            cursor.execute(migration.Migration.operations[-1].sql)


@pytest.fixture
def gateway_key(user, workspace):
    # The workspace is explicit: the user fixture leaves a workspace context
    # that a post-save signal would otherwise write onto this immutable key.
    return AgentccAPIKey.no_workspace_objects.create(
        organization=user.organization,
        user=user,
        workspace=workspace,
        gateway_key_id=GATEWAY_KEY_ID,
        name="test-only-person",
        key_hash="test_only_hash_admin_token_policy",
        status=AgentccAPIKey.ACTIVE,
    )


def eval_request(user, workspace, signing_token):
    """What a gateway, or anyone holding ``signing_token``, sends for eval."""
    kid = current_kid()
    envelope = sign_request(
        keys={("gateway", "fa-eval", kid): signing_token.encode()},
        kid=kid,
        issuer="gateway",
        audience="fa-eval",
        method="POST",
        route=EVAL_ROUTE,
        body=b"{}",
        org=user.organization_id,
        user=user.pk,
        workspace=workspace.pk,
        via="key:" + GATEWAY_KEY_ID,
        scope="eval",
    )
    return RequestFactory().post(
        EVAL_ROUTE,
        data=b"{}",
        content_type="application/json",
        HTTP_X_FA_ENVELOPE=envelope,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("token", REFUSED.values(), ids=list(REFUSED))
def test_envelope_keyed_by_refused_token_authenticates_no_one(
    settings, monkeypatch, nonce_table, user, workspace, gateway_key, token
):
    set_admin_token(settings, monkeypatch, "env", token)
    request = eval_request(user, workspace, token)
    with pytest.raises(AuthenticationFailed):
        ServiceEnvelopeEvalAuthentication().authenticate(request)
    assert getattr(request, "credential_provenance", None) is None


@pytest.mark.django_db(transaction=True)
def test_dev_opt_in_still_admits_the_compose_default(
    settings, monkeypatch, nonce_table, user, workspace, gateway_key
):
    monkeypatch.setenv(ENV_ALLOW_INSECURE_ADMIN_TOKEN, "1")
    set_admin_token(settings, monkeypatch, "env", COMPOSE_DEFAULT)
    request = eval_request(user, workspace, COMPOSE_DEFAULT)
    actual, _ = ServiceEnvelopeEvalAuthentication().authenticate(request)
    assert actual.pk == user.pk
    assert request.credential_provenance == "service_envelope"


@pytest.mark.django_db(transaction=True)
def test_random_token_admits_the_gateway_and_not_a_compose_default_forgery(
    settings, monkeypatch, nonce_table, user, workspace, gateway_key
):
    token = secrets.token_hex(32)
    set_admin_token(settings, monkeypatch, "env", token)
    forged = eval_request(user, workspace, COMPOSE_DEFAULT)
    with pytest.raises(AuthenticationFailed):
        ServiceEnvelopeEvalAuthentication().authenticate(forged)
    request = eval_request(user, workspace, token)
    actual, _ = ServiceEnvelopeEvalAuthentication().authenticate(request)
    assert actual.pk == user.pk
    assert request.workspace.pk == workspace.pk
    assert request.credential_provenance == "service_envelope"
