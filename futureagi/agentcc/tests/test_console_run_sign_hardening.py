"""Console-run signer hardening (GPT readers of S1-S5 r6, wf_08c76dae-ecd).

- Revoking a run authorization revokes the gateway grants minted for its run.
- A grant never outlives its authorization, and an authorization keeps its
  whole life (no early expiry from the grant reuse margin).
- A tampered or foreign token never learns whether a row is revoked.
- Database failures and deeply nested JSON give the typed refusals.
- A jti must be canonical base64url of 16 bytes; a bad signature never spends
  the jti. Every key here is generated for the test.
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
from rest_framework.test import APIRequestFactory

from accounts.models.service_grant import ServiceGrant
from accounts.services.service_grants import issue_person_grant, revoke_grants
from agentcc.services.console_run_signer_keys import add_key
from agentcc.services.delegated_availability import CONSOLE_RUN_SWITCH

pytestmark = pytest.mark.django_db

CHAT_ROUTE = "/-/delegated/v1/chat/completions"
SIGN_ROUTE = "/agentcc/console-run/sign/"
CALL_BODY = b'{"model":"gpt-4o-mini","messages":[]}'
MODELS = ["gpt-4o-mini"]


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


def authorization(user, workspace, *, ttl=3600, ee="ee-prod-1"):
    with transaction.atomic():
        token, _, row = issue_person_grant(
            kind="console-run-auth",
            org=user.organization_id,
            user=user.pk,
            workspace=workspace.pk,
            sub="chat:" + str(uuid.uuid4()),
            ttl=ttl,
            models=MODELS,
            ee=ee,
        )
    return token, row


def sign_body(token):
    return json.dumps(
        {
            "run_authorization": token,
            "route": CHAT_ROUTE,
            "method": "POST",
            "body_sha256": hashlib.sha256(CALL_BODY).hexdigest(),
            "model": "gpt-4o-mini",
        },
        separators=(",", ":"),
    ).encode()


def assertion(private, raw, *, jti=None, header_segment=None):
    now = int(time.time())
    h = {"alg": "EdDSA", "kid": "ee-k1", "typ": "omega-ee-assertion+jwt"}
    c = {
        "iss": "ee-prod-1",
        "aud": "fa-console-run-signer",
        "htu": SIGN_ROUTE,
        "htm": "POST",
        "iat": now,
        "exp": now + 60,
        "jti": jti or b64(os.urandom(16)),
        "bodyhash": hashlib.sha256(raw).hexdigest(),
    }
    head = header_segment or b64(json.dumps(h).encode())
    signing_input = head + "." + b64(json.dumps(c).encode())
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


def code(response):
    return response.status_code, response.data.get("code")


def test_revoking_the_authorization_revokes_its_grants(
    user, workspace, ee_key, django_capture_on_commit_callbacks
):
    token, row = authorization(user, workspace)
    raw = sign_body(token)
    assert post(raw, assertion(ee_key, raw)).status_code == 200
    child = ServiceGrant.objects.get(kind="console-run", sub=row.sub)
    published = []
    with (
        patch(
            "accounts.services.service_grants.publish_grant_revocation",
            side_effect=published.append,
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        revoke_grants(ServiceGrant.objects.filter(pk=row.pk), reason="run_revoked")
    child.refresh_from_db()
    assert child.revoked_at is not None
    assert set(published) == {row.pk, child.pk}
    assert code(post(raw, assertion(ee_key, raw))) == (403, "authorization_revoked")


def test_a_grant_never_outlives_its_authorization(user, workspace, ee_key):
    token, row = authorization(user, workspace, ttl=600)
    raw = sign_body(token)
    real, ticks = time.time, []

    def clock():
        # Every reading is two seconds later than the one before it.
        ticks.append(None)
        return real() + 2 * len(ticks)

    with patch("tfc.service_envelope.time.time", side_effect=clock):
        result = post(raw, assertion(ee_key, raw)).data["result"]
    assert result["grant_exp"] <= int(row.exp)


def test_an_authorization_keeps_its_last_minutes(user, workspace, ee_key):
    token, row = authorization(user, workspace, ttl=100)
    raw = sign_body(token)
    response = post(raw, assertion(ee_key, raw))
    assert response.status_code == 200, response.data
    assert response.data["result"]["grant_exp"] <= int(row.exp)


def test_a_tampered_token_never_learns_the_row_is_revoked(user, workspace, ee_key):
    token, row = authorization(user, workspace)
    version, canonical, mac = token.split(".")
    tampered = ".".join((version, canonical, ("A" if mac[0] != "A" else "B") + mac[1:]))
    ServiceGrant.objects.filter(pk=row.pk).update(revoked_at="2026-01-01T00:00:00Z")
    raw = sign_body(tampered)
    assert code(post(raw, assertion(ee_key, raw))) == (401, "authorization_rejected")


def test_a_foreign_deployments_token_never_learns_the_row_is_revoked(
    user, workspace, ee_key
):
    token, row = authorization(user, workspace, ee="ee-other")
    ServiceGrant.objects.filter(pk=row.pk).update(revoked_at="2026-01-01T00:00:00Z")
    raw = sign_body(token)
    assert code(post(raw, assertion(ee_key, raw))) == (401, "authorization_rejected")


def test_an_unreadable_key_registry_is_a_typed_503(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    raw = sign_body(token)
    with patch(
        "agentcc.services.console_run_signer.active_key",
        side_effect=DatabaseError("synthetic"),
    ):
        response = post(raw, assertion(ee_key, raw))
    assert code(response) == (503, "signer_unavailable")
    assert response["Cache-Control"] == "no-store"


def test_deeply_nested_json_gets_the_typed_refusals(ee_key):
    nested = b"[" * 3000 + b"]" * 3000
    assert code(post(nested, assertion(ee_key, nested))) == (
        400,
        "sign_request_invalid",
    )
    raw = b"{}"
    header = b64(b"[" * 1200 + b"]" * 1200)
    assert code(post(raw, assertion(ee_key, raw, header_segment=header))) == (
        401,
        "assertion_rejected",
    )


def test_a_jti_must_be_canonical_16_bytes(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    raw = sign_body(token)
    # 22 characters whose last one carries bits beyond 16 bytes.
    jti = "A" * 21 + "B"
    assert code(post(raw, assertion(ee_key, raw, jti=jti))) == (
        401,
        "assertion_rejected",
    )


def test_a_bad_signature_is_refused_and_never_spends_the_jti(user, workspace, ee_key):
    token, _ = authorization(user, workspace)
    raw = sign_body(token)
    jti = b64(os.urandom(16))
    stranger = Ed25519PrivateKey.generate()
    assert code(post(raw, assertion(stranger, raw, jti=jti))) == (
        401,
        "assertion_rejected",
    )
    good = assertion(ee_key, raw, jti=jti)
    corrupted = good[:-2] + ("AA" if good[-2:] != "AA" else "BB")
    assert code(post(raw, corrupted)) == (401, "assertion_rejected")
    # The jti was not spent by either refusal.
    assert post(raw, good).status_code == 200
