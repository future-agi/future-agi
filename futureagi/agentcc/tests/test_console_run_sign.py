"""POST /agentcc/console-run/sign/: the agent service's grant and envelope for
one gateway call (fi-security 1d8b0ce0; wire record 459ac8ed + ADDENDUM-1).

The RED list from the ruling: wrong alg, unknown or revoked kid, iss/kid
mismatch, aud/htu/htm/bodyhash mismatch, expired or future iat, a replayed jti,
a revoked authorization, a removed member, the switch off, a model outside the
cap, an authorization used by another agent service deployment; plus the owner's
"no registered key: refused, not attempted". Every key here is generated for
the test; the call body is synthetic.
"""

import base64
import hashlib
import json
import os
import time
import uuid
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.db import DatabaseError, transaction
from django.utils import timezone
from rest_framework.test import APIRequestFactory
from structlog.testing import capture_logs

from accounts.models.service_grant import ServiceGrant
from accounts.models.workspace import WorkspaceMembership
from accounts.services.envelope_keys import pair_keys
from accounts.services.service_grants import issue_person_grant
from agentcc.services.console_run_signer_keys import add_key, revoke_key
from agentcc.services.delegated_availability import CONSOLE_RUN_SWITCH
from tfc.envelope_nonces import MemoryNonceStore
from tfc.service_envelope import verify_grant, verify_request

pytestmark = pytest.mark.django_db

CHAT_ROUTE = "/-/delegated/v1/chat/completions"
SIGN_ROUTE = "/agentcc/console-run/sign/"
CALL_BODY = b'{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
MODELS = ["gpt-4o-mini", "anthropic/claude-sonnet-5.5"]


@pytest.fixture(autouse=True)
def configured(settings, monkeypatch):
    settings.SECRET_KEY = "test_only_fa_signing_key_for_console_run_0000"
    settings.AGENTCC_ADMIN_TOKEN = "test-only-console-run-sign-admin-token-0123456789"
    monkeypatch.delenv(CONSOLE_RUN_SWITCH, raising=False)


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.fixture
def ee_key():
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    add_key(deployment="ee-prod-1", kid="ee-k1", public_key=b64(raw), operator="ops")
    return private


def authorization(user, workspace, *, ee="ee-prod-1", ttl=3600, now=None):
    with transaction.atomic():
        token, claims, row = issue_person_grant(
            kind="console-run-auth",
            org=user.organization_id,
            user=user.pk,
            workspace=workspace.pk,
            sub="chat:" + str(uuid.uuid4()),
            ttl=ttl,
            models=MODELS,
            ee=ee,
            now=now,
        )
    return token, row


def sign_body(token, **overrides):
    body = {
        "run_authorization": token,
        "route": CHAT_ROUTE,
        "method": "POST",
        "body_sha256": hashlib.sha256(CALL_BODY).hexdigest(),
        "model": "gpt-4o-mini",
    }
    body.update(overrides)
    return json.dumps(body, separators=(",", ":")).encode()


def assertion(private, raw, *, header=None, claims=None, now=None, life=60):
    now = int(time.time()) if now is None else now
    h = {"alg": "EdDSA", "kid": "ee-k1", "typ": "omega-ee-assertion+jwt"}
    h.update(header or {})
    c = {
        "iss": "ee-prod-1",
        "aud": "fa-console-run-signer",
        "htu": SIGN_ROUTE,
        "htm": "POST",
        "iat": now,
        "exp": now + life,
        "jti": b64(os.urandom(16)),
        "bodyhash": hashlib.sha256(raw).hexdigest(),
    }
    c.update(claims or {})
    signing_input = b64(json.dumps(h).encode()) + "." + b64(json.dumps(c).encode())
    return signing_input + "." + b64(private.sign(signing_input.encode("ascii")))


def post(raw, token):
    from agentcc.views.console_run import ConsoleRunSignView

    request = APIRequestFactory().post(
        SIGN_ROUTE,
        data=raw,
        content_type="application/json",
        HTTP_X_OMEGA_EE_ASSERTION=token,
    )
    return ConsoleRunSignView.as_view()(request)


def refused(response, status, code):
    assert response.status_code == status, response.data
    assert response.data["code"] == code
    assert response["Cache-Control"] == "no-store"


def test_a_signed_call_gets_a_bound_grant_and_envelope(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    raw = sign_body(token)
    with capture_logs() as records:
        response = post(raw, assertion(ee_key, raw))
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["grant"].startswith("fe1c.")
    assert result["envelope"].startswith("fe1.")
    grant_row = ServiceGrant.objects.get(kind="console-run")
    assert grant_row.model_ids == "anthropic/claude-sonnet-5.5,gpt-4o-mini"
    assert result["grant_exp"] == grant_row.exp
    grant = verify_grant(
        result["grant"],
        keys=pair_keys("fa", "fa-eval-grant"),
        audience="fa-eval-grant",
        row=grant_row.registry_row(),
    )
    envelope = verify_request(
        result["envelope"],
        keys=pair_keys("fa", "gw-delegated"),
        audience="gw-delegated",
        method="POST",
        route=CHAT_ROUTE,
        body=CALL_BODY,
        nonces=MemoryNonceStore(epoch=time.time() - 30),
        grant=result["grant"],
    )
    assert envelope["scope"] == "console-run"
    assert envelope["via"] == "grant:" + grant["id"]
    assert result["envelope_exp"] - int(envelope["iat"]) == 60
    signed = [r for r in records if r.get("event") == "console_run_sign"]
    assert signed and signed[-1]["code"] == "signed"
    assert signed[-1]["kid"] == "ee-k1"
    for secret in (token, result["grant"], result["envelope"]):
        assert secret not in repr(records)


def test_the_grant_is_reused_and_each_envelope_is_new(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    raw = sign_body(token)
    first = post(raw, assertion(ee_key, raw)).data["result"]
    second = post(raw, assertion(ee_key, raw)).data["result"]
    assert second["grant"] == first["grant"]
    assert second["envelope"] != first["envelope"]
    assert ServiceGrant.objects.filter(kind="console-run").count() == 1


@pytest.mark.parametrize(
    ("header", "claims"),
    [
        ({"alg": "HS256"}, None),
        ({"alg": "none"}, None),
        ({"typ": "JWT"}, None),
        ({"jwk": {"kty": "OKP"}}, None),
        ({"kid": "unknown-kid"}, None),
        (None, {"iss": "ee-other"}),
        (None, {"aud": "omega-squad"}),
        (None, {"htu": "https://fa.test.invalid/agentcc/console-run/sign/"}),
        (None, {"htm": "GET"}),
        (None, {"bodyhash": "0" * 64}),
        (None, {"jti": "short"}),
        (None, {"nonce": "x"}),
    ],
)
def test_assertions_that_break_the_rules_are_refused(
    user, workspace, ee_key, header, claims
):
    token, _ = authorization(user, workspace)
    raw = sign_body(token)
    refused(
        post(raw, assertion(ee_key, raw, header=header, claims=claims)),
        401,
        "assertion_rejected",
    )
    assert not ServiceGrant.objects.filter(kind="console-run").exists()


def test_assertion_time_rules(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    raw = sign_body(token)
    now = int(time.time())
    for kw in (
        {"now": now - 120},
        {"now": now - 60},
        {"now": now + 31},
        {"life": 61},
        {"life": 0},
    ):
        refused(post(raw, assertion(ee_key, raw, **kw)), 401, "assertion_rejected")


def test_a_replayed_assertion_is_refused(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    raw = sign_body(token)
    once = assertion(ee_key, raw)
    assert post(raw, once).status_code == 200
    # The jti store is the database every replica shares.
    refused(post(raw, once), 401, "assertion_rejected")


def test_a_revoked_or_unregistered_key_is_refused(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    raw = sign_body(token)
    revoke_key(kid="ee-k1", operator="ops")
    refused(post(raw, assertion(ee_key, raw)), 401, "assertion_rejected")


def test_no_registered_key_is_refused_and_nothing_is_minted(user, workspace):
    token, _ = authorization(user, workspace)
    raw = sign_body(token)
    stranger = Ed25519PrivateKey.generate()
    refused(post(raw, assertion(stranger, raw)), 401, "assertion_rejected")
    assert not ServiceGrant.objects.filter(kind="console-run").exists()


def test_a_revoked_authorization_is_refused(user, workspace, ee_key):
    token, row = authorization(user, workspace)
    ServiceGrant.objects.filter(pk=row.pk).update(revoked_at=timezone.now())
    raw = sign_body(token)
    refused(post(raw, assertion(ee_key, raw)), 403, "authorization_revoked")


def test_an_expired_authorization_is_refused(user, workspace, ee_key):
    # An authentic token whose own exp has passed.
    token, _ = authorization(user, workspace, ttl=60, now=int(time.time()) - 120)
    raw = sign_body(token)
    refused(post(raw, assertion(ee_key, raw)), 403, "authorization_expired")


def test_an_authorization_for_another_deployment_is_refused(user, workspace, ee_key):
    token, _ = authorization(user, workspace, ee="ee-other")
    raw = sign_body(token)
    refused(post(raw, assertion(ee_key, raw)), 401, "authorization_rejected")


def test_a_removed_member_is_refused(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    WorkspaceMembership._base_manager.filter(user=user, workspace=workspace).update(
        is_active=False
    )
    raw = sign_body(token)
    refused(post(raw, assertion(ee_key, raw)), 403, "member_removed")


def test_the_switch_off_refuses(user, workspace, ee_key, monkeypatch):
    token, _ = authorization(user, workspace)
    monkeypatch.setenv(CONSOLE_RUN_SWITCH, "false")
    raw = sign_body(token)
    refused(post(raw, assertion(ee_key, raw)), 403, "console_run_disabled")


def test_a_model_outside_the_cap_is_refused(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    raw = sign_body(token, model="o3")
    refused(post(raw, assertion(ee_key, raw)), 403, "model_not_in_cap")


@pytest.mark.parametrize("overrides", [{"route": "/-/batches"}, {"method": "GET"}])
def test_only_the_delegated_chat_route_is_signed(user, workspace, ee_key, overrides):
    token, _ = authorization(user, workspace)
    raw = sign_body(token, **overrides)
    refused(post(raw, assertion(ee_key, raw)), 403, "route_rejected")


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b'{"run_authorization": "x"}',
        b'{"run_authorization":"x","route":"r","method":"POST",'
        b'"body_sha256":"XYZ","model":"m"}',
    ],
)
def test_malformed_sign_bodies_are_invalid(ee_key, raw):
    refused(post(raw, assertion(ee_key, raw)), 400, "sign_request_invalid")


def test_an_extra_body_key_is_invalid(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    raw = json.dumps({**json.loads(sign_body(token)), "grant": "not-accepted"}).encode()
    refused(post(raw, assertion(ee_key, raw)), 400, "sign_request_invalid")


def test_an_unreadable_jti_store_fails_closed(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    raw = sign_body(token)
    with patch(
        "agentcc.services.console_run_signer.DurableNonceStore.consume",
        side_effect=DatabaseError("synthetic"),
    ):
        refused(post(raw, assertion(ee_key, raw)), 503, "signer_unavailable")


def test_an_oversized_body_is_invalid(ee_key):
    raw = b'{"x":"' + b"a" * 9000 + b'"}'
    refused(post(raw, assertion(ee_key, raw)), 400, "sign_request_invalid")
