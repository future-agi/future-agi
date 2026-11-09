"""The session exchange's `products` claim (READINESS 3.13): A gets each admitted product with its
state, A2 loses the product its workspace is denied, B gets no key at all (404), a paused product is
`paused` in the claim, and an unreadable rollout store mints nothing (RC-07)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import DatabaseError

from accounts.omega_session import HostAssertionSigner
from accounts.tests.test_omega_session_signing import assertion_parts, decode_segment
from omega_rollout import ops
from omega_rollout import products as p
from omega_rollout.tests.conftest import admit, client_for

pytestmark = pytest.mark.django_db
EXCHANGE = "/accounts/omega/session-exchange/"


@pytest.fixture
def signer_env(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_file = Path(tmp_path) / "issuer.pem"
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    environ = {
        "OMEGA_SQUAD_DELEGATION_ISSUER": "https://host.example/omega",
        "OMEGA_SQUAD_DELEGATION_KEY_ID": "test-key",
        "OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE": str(key_file),
    }
    with patch.dict(os.environ, environ):
        yield environ


def exchange(person, workspace=None):
    return client_for(person, workspace).post(
        EXCHANGE, {"audience": "omega-squad"}, format="json"
    )


def claims_of(response):
    assert response.status_code == 200, response.content
    return json.loads(decode_segment(assertion_parts(response.data)[1]))


def test_a_gets_every_admitted_product_and_the_epoch(admit_all, signer_env):
    claims = claims_of(exchange(admit_all.a))
    assert claims["products"] == dict.fromkeys(sorted(p.HOSTED_PRODUCTS), "active")
    assert isinstance(claims["rollout_epoch"], int) and claims["rollout_epoch"] > 0


def test_a2_loses_only_the_product_its_workspace_is_denied(tenants, signer_env):
    admit(tenants, p.CONSOLE, deny_a2=True)
    admit(tenants, p.SANDBOXES, deny_a2=False)
    assert claims_of(exchange(tenants.a))["products"] == {
        "console": "active",
        "sandboxes": "active",
    }
    assert claims_of(exchange(tenants.a, tenants.a2))["products"] == {
        "sandboxes": "active"
    }


def test_a2_denied_everything_and_b_get_no_key(admit_all, signer_env):
    for response in (exchange(admit_all.a, admit_all.a2), exchange(admit_all.b)):
        assert response.status_code == 404
        assert response.json()["code"] == "product_not_admitted"
        assert "accessToken" not in response.json()


def test_a_paused_product_is_named_paused_not_dropped(admit_all, signer_env):
    ops.kill(p.CONSOLE, actor=admit_all.operator.user, reason="drill")
    products = claims_of(exchange(admit_all.a))["products"]
    assert products["console"] == "paused" and products["sandboxes"] == "active"


def test_an_unreadable_rollout_store_mints_nothing(admit_all, signer_env):
    with patch(
        "omega_rollout.models.ProductRollout.objects.all",
        side_effect=DatabaseError("down"),
    ):
        response = exchange(admit_all.a)
    assert response.status_code == 503
    assert response.json()["code"] == "verification_unavailable"
    assert "accessToken" not in response.json()


def test_the_signer_refuses_a_product_it_does_not_know(signer_env):
    signer = HostAssertionSigner.from_environment()
    # Every assertion names how the caller signed in and the credential it is bound to (SB
    # W2-KEYS, `.omega/program/SB-W2-KEYS/CLAIMS.md` section 1); a browser session binds `sid`.
    base = {
        "subject": "u1",
        "organization_id": "o1",
        "workspace_id": "w1",
        "role": "member",
        "audience": "omega-squad",
        "auth_method": "session",
        "credential_id": "s1",
    }
    for products, epoch in (
        ({"studio": "active"}, 1),
        ({"console": "on"}, 1),
        ({"console": "active"}, -1),
        ({"console": "active"}, None),
        ("console", 1),
    ):
        with pytest.raises(ValueError):
            signer.issue(**base, products=products, rollout_epoch=epoch)
    token = signer.issue(
        **base, products={"hub": "paused", "console": "active"}, rollout_epoch=7
    )
    claims = json.loads(decode_segment(assertion_parts(token)[1]))
    assert (
        list(claims["products"]) == ["console", "hub"] and claims["rollout_epoch"] == 7
    )
    # The rollout claims ride beside the authentication claim; neither replaces the other.
    assert claims["auth_method"] == "session" and claims["sid"] == "s1"
    assert "key_id" not in claims
    assert "products" not in json.loads(
        decode_segment(assertion_parts(signer.issue(**base))[1])
    )
