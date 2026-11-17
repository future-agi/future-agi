"""The Console model list's attestation (fi-security ruling c4e532bc).

An RS256 compact JWS over the RFC 8785 (JCS) digest of the rows, signed with
the FA host key that signs the omega_delegate_ host assertions, told apart from
them by its exact typ and claim set, and minted only for a person's own browser
session. Every key here is generated for the test; nothing is configured.
"""

import base64
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.omega_session import AUDIENCE, DelegationUnavailable, HostAssertionSigner
from agentcc.services import console_models_attestation as att

ORG = SimpleNamespace(
    pk="00000000-0000-4000-8000-0000000000aa",
    id="00000000-0000-4000-8000-0000000000aa",
)
WORKSPACE = SimpleNamespace(pk="00000000-0000-4000-8000-0000000000bb")
USER_ID = "00000000-0000-4000-8000-0000000000cc"
NOW = 1_800_000_000


def row(**fields):
    base = dict.fromkeys(
        (
            "model",
            "name",
            "provider",
            "vendor",
            "billing_mode",
            "payers",
            "priced",
            "input_per_mtok_usd",
            "output_per_mtok_usd",
            "cached_input_per_mtok_usd",
            "context_length",
            "top",
            "top_rank",
        )
    )
    base.update(fields)
    return base


# The three vectors shared with EE's verifier (console-models/JCS-VECTORS-r1.txt).
VECTORS = {
    "v1-priced-byok": (
        [
            row(
                model="gpt-4o",
                name="GPT-4o",
                provider="openai",
                vendor="openai",
                billing_mode="byok",
                payers=["byok"],
                priced=True,
                input_per_mtok_usd=2.5,
                output_per_mtok_usd=10.0,
                context_length=128000,
                top=True,
                top_rank=1,
            )
        ],
        "93bc4cbc790c2619e83a46bd1264e5a8bdd43aabb6ca4f60ec4a5bb60188d91d",
    ),
    "v2-managed-large-nonascii": (
        [
            row(
                model="vendor/modele-omega",
                name="Mod\u00e8le\u2013\u03a9",
                provider="fagi",
                vendor="vendor",
                billing_mode="managed",
                payers=["managed"],
                priced=True,
                input_per_mtok_usd=0.000001,
                output_per_mtok_usd=1234567.5,
                context_length=200000,
                top=False,
            )
        ],
        "f8dff7f94034f6f5ad4cb9423e47eef0a4eb931a1d238d399b20c784a51ebc40",
    ),
    "v3-unpriced": (
        [
            row(
                model="house-alias",
                name="house-alias",
                provider="anthropic",
                billing_mode="byok",
                payers=["byok"],
                priced=False,
                top=False,
            )
        ],
        "f5bacd14e8c3d5f00c662ed503c6e7d3800e31a9f85c757418d4dc972f11fa3f",
    ),
}


@pytest.fixture(scope="module")
def signer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return HostAssertionSigner(
        issuer="https://fa.test.invalid", key_id="test-k1", private_key=key
    )


def _unb64(segment):
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def decode(token):
    header, claims, signature = token.split(".")
    return (
        json.loads(_unb64(header)),
        json.loads(_unb64(claims)),
        _unb64(signature),
        f"{header}.{claims}".encode("ascii"),
    )


@pytest.mark.parametrize(
    ("number", "text"),
    [
        (10.0, "10"),
        (2.5, "2.5"),
        (1e21, "1e+21"),
        (0.000001, "0.000001"),
        (1e-7, "1e-7"),
        (1234567.5, "1234567.5"),
        (123456789012345680000.0, "123456789012345680000"),
        (-0.5, "-0.5"),
        (0, "0"),
        (128000, "128000"),
    ],
)
def test_jcs_numbers_follow_ecmascript(number, text):
    assert att.jcs(number) == text


def test_jcs_sorts_keys_by_utf16_code_units():
    # U+1F600 is D83D DE00 in UTF-16, which sorts before U+E000.
    assert att.jcs({"\ue000": 1, "\U0001f600": 2}) == '{"\U0001f600":2,"\ue000":1}'


def test_jcs_refuses_values_json_cannot_carry():
    for value in (float("nan"), float("inf"), object()):
        with pytest.raises((TypeError, ValueError)):
            att.jcs(value)


@pytest.mark.parametrize("name", sorted(VECTORS))
def test_the_three_shared_vectors_hash_as_ee_does(name):
    models, digest = VECTORS[name]
    assert att.models_sha256(models) == digest


def test_the_attestation_is_an_rs256_statement_with_the_exact_claims(signer):
    models, digest = VECTORS["v1-priced-byok"]
    token = att.issue_attestation(
        models,
        subject=USER_ID,
        organization_id=ORG.pk,
        workspace_id=WORKSPACE.pk,
        signer=signer,
        now=NOW,
    )
    header, claims, signature, signing_input = decode(token)
    assert header == {"alg": "RS256", "typ": "omega-models+jwt", "kid": "test-k1"}
    # Dated back by the host assertions' skew margin; exp - iat is 900 exactly.
    assert claims == {
        "iss": "https://fa.test.invalid",
        "aud": "omega-squad",
        "sub": USER_ID,
        "org_id": ORG.pk,
        "workspace_id": WORKSPACE.pk,
        "models_sha256": digest,
        "iat": NOW - 1,
        "exp": NOW - 1 + 900,
    }
    # A statement, not a credential: never a jti, nonce or role.
    assert not {"jti", "nonce", "role"} & set(claims)
    # The same audience the host assertion uses, from the same constant.
    assert claims["aud"] == AUDIENCE
    signer.private_key.public_key().verify(
        signature, signing_input, padding.PKCS1v15(), hashes.SHA256()
    )


def test_the_typ_differs_from_the_host_assertions():
    assert att.TYP == "omega-models+jwt"
    assert att.TYP != "JWT"


def test_an_unconfigured_host_key_gives_no_attestation():
    with (
        patch.object(
            att.HostAssertionSigner,
            "from_environment",
            side_effect=DelegationUnavailable("Omega delegation is unavailable"),
        ),
        pytest.raises(att.AttestationUnavailable),
    ):
        att.issue_attestation(
            [], subject=USER_ID, organization_id=ORG.pk, workspace_id=WORKSPACE.pk
        )


LIST_RESULT = {
    "sources": {},
    "models": VECTORS["v1-priced-byok"][0],
    "total": 1,
    "truncated": False,
}


def call_view(signer, *, session=True, workspace=WORKSPACE, headers=None):
    factory = APIRequestFactory()
    request = factory.get("/agentcc/console-models/", **(headers or {}))
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True, pk=USER_ID))
    request.organization = ORG
    request.workspace = workspace
    if session:
        request.credential_provenance = "interactive_session"
    with (
        patch(
            "agentcc.views.console_models.list_console_models",
            return_value=dict(LIST_RESULT),
        ),
        patch.object(att.HostAssertionSigner, "from_environment", return_value=signer),
    ):
        from agentcc.views.console_models import ConsoleModelsView

        return ConsoleModelsView.as_view()(request)


def test_a_browser_session_gets_the_attestation_of_its_own_rows(signer):
    response = call_view(signer)
    assert response.status_code == 200
    result = response.data["result"]
    _, claims, _, _ = decode(result["attestation"])
    assert claims["sub"] == USER_ID
    assert claims["org_id"] == ORG.pk
    assert claims["workspace_id"] == WORKSPACE.pk
    assert claims["models_sha256"] == att.models_sha256(result["models"])


def test_an_api_key_gets_the_list_without_an_attestation(signer):
    response = call_view(signer, session=False)
    assert response.status_code == 200
    assert response.data["result"]["attestation"] is None
    assert response.data["result"]["models"] == LIST_RESULT["models"]


def test_no_workspace_means_no_attestation(signer):
    assert call_view(signer, workspace=None).data["result"]["attestation"] is None


def test_a_selection_header_naming_another_workspace_means_no_attestation(signer):
    other = {"HTTP_X_WORKSPACE_ID": "00000000-0000-4000-8000-0000000000dd"}
    assert call_view(signer, headers=other).data["result"]["attestation"] is None
    same = {"HTTP_X_WORKSPACE_ID": WORKSPACE.pk.upper()}
    assert call_view(signer, headers=same).data["result"]["attestation"] is not None


def test_an_unconfigured_host_key_still_answers_the_list():
    factory = APIRequestFactory()
    request = factory.get("/agentcc/console-models/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True, pk=USER_ID))
    request.organization = ORG
    request.workspace = WORKSPACE
    request.credential_provenance = "interactive_session"
    with (
        patch(
            "agentcc.views.console_models.list_console_models",
            return_value=dict(LIST_RESULT),
        ),
        patch.object(
            att.HostAssertionSigner,
            "from_environment",
            side_effect=DelegationUnavailable("Omega delegation is unavailable"),
        ),
    ):
        from agentcc.views.console_models import ConsoleModelsView

        response = ConsoleModelsView.as_view()(request)
    assert response.status_code == 200
    assert response.data["result"]["attestation"] is None
    assert response["Cache-Control"] == "no-store"


def test_jcs_zero_and_string_escaping_follow_rfc_8785():
    assert att.jcs(-0.0) == "0"
    assert att.jcs(0.0) == "0"
    # Quote, backslash and control characters are escaped; other characters,
    # including non-ASCII, are written as they are.
    assert att.jcs('a"b\\c') == '"a\\"b\\\\c"'
    assert att.jcs("\b\t\n\f\r") == '"\\b\\t\\n\\f\\r"'
    assert att.jcs("\x00\x1f") == '"\\u0000\\u001f"'
    assert att.jcs("\u00e9\u2013") == '"\u00e9\u2013"'
    assert att.jcs([3, [2, 1]]) == "[3,[2,1]]"
    with pytest.raises(TypeError):
        att.jcs({1: "not a string key"})
    with pytest.raises(ValueError):
        att.jcs(float("-inf"))


def test_the_token_is_compact_unpadded_base64url_of_utf8_json(signer):
    unicode_signer = HostAssertionSigner(
        issuer="https://fa.test.invalid/\u00e9",
        key_id="test-k1",
        private_key=signer.private_key,
    )
    token = att.issue_attestation(
        [],
        subject=USER_ID,
        organization_id=ORG.pk,
        workspace_id=WORKSPACE.pk,
        signer=unicode_signer,
        now=NOW,
    )
    segments = token.split(".")
    assert len(segments) == 3
    for segment in segments:
        assert segment and set(segment) <= set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        )
    assert _unb64(segments[0]) == (
        b'{"alg":"RS256","typ":"omega-models+jwt","kid":"test-k1"}'
    )
    payload = _unb64(segments[1])
    # Compact UTF-8 JSON: no spaces, and a non-ASCII issuer as UTF-8 bytes.
    assert b" " not in payload
    assert "https://fa.test.invalid/\u00e9".encode() in payload
