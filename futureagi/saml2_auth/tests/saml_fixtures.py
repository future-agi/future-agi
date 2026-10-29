"""Signed SAML fixtures used by the tenant-isolation acceptance tests.

The test IdPs are intentionally ephemeral: their RSA keypairs are created in
pytest's session temporary directory and never come from an environment value
or a checked-in fixture.  Responses are produced and signed by pysaml2, which
uses the ``xmlsec1`` binary exactly as production verification does.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from saml2 import (
    BINDING_HTTP_POST,
    BINDING_HTTP_REDIRECT,
    NAMEID_FORMAT_EMAILADDRESS,
    class_name,
    saml,
)
from saml2.config import IdPConfig
from saml2.metadata import entity_descriptor
from saml2.saml import AuthnContext, AuthnContextClassRef, AuthnStatement
from saml2.server import Server
from saml2.sigver import pre_signature_part, signed_instance_factory
from saml2.time_util import instant


@dataclass(frozen=True)
class IdPKeyPair:
    """A throwaway IdP signing identity for a single pytest session."""

    name: str
    issuer: str
    key_path: Path
    cert_path: Path
    certificate_pem: bytes


Mutation = Callable[[Any], None]


def create_idp_keypair(directory: Path, name: str) -> IdPKeyPair:
    """Create a test-only RSA key and a short-lived self-signed certificate."""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    issuer = f"https://{name}.saml.test/metadata"
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )

    key_path = directory / f"{name}.key"
    cert_path = directory / f"{name}.crt"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
    cert_path.write_bytes(certificate_pem)
    return IdPKeyPair(
        name=name,
        issuer=issuer,
        key_path=key_path,
        cert_path=cert_path,
        certificate_pem=certificate_pem,
    )


def build_idp_metadata(keypair: IdPKeyPair, shape: str) -> str:
    """Return synthetic Okta, Google, or AWS-shaped IdP metadata XML.

    The provider labels intentionally vary the entity ID, endpoint binding and
    descriptor extensions while retaining an ordinary signed-assertion trust
    path.  These are compatibility fixtures, not provider-account fixtures.
    """

    endpoint_by_shape = {
        "okta": ("/app/okta/sso/saml", BINDING_HTTP_POST),
        "google": ("/a/example/sso/saml", BINDING_HTTP_REDIRECT),
        "aws": ("/saml/acs/example", BINDING_HTTP_POST),
    }
    try:
        suffix, binding = endpoint_by_shape[shape]
    except KeyError as exc:
        raise ValueError(f"Unsupported synthetic metadata shape: {shape}") from exc

    config = IdPConfig().load(
        {
            "entityid": keypair.issuer,
            "key_file": str(keypair.key_path),
            "cert_file": str(keypair.cert_path),
            "service": {
                "idp": {
                    "endpoints": {
                        "single_sign_on_service": [
                            (f"https://{keypair.name}.saml.test{suffix}", binding),
                            (
                                f"https://{keypair.name}.saml.test{suffix}/redirect",
                                BINDING_HTTP_REDIRECT,
                            ),
                        ]
                    }
                }
            },
        }
    )
    return str(entity_descriptor(config))


def _sp_metadata(*, audience: str, recipient: str) -> str:
    return (
        '<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" '
        f'entityID="{audience}">'
        "<SPSSODescriptor "
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        "<AssertionConsumerService "
        'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{recipient}" index="1"/>'
        "</SPSSODescriptor></EntityDescriptor>"
    )


def _idp_server(keypair: IdPKeyPair, *, audience: str, recipient: str) -> Server:
    config = IdPConfig().load(
        {
            "entityid": keypair.issuer,
            "key_file": str(keypair.key_path),
            "cert_file": str(keypair.cert_path),
            "metadata": {
                "inline": [_sp_metadata(audience=audience, recipient=recipient)]
            },
            "service": {
                "idp": {
                    "endpoints": {
                        "single_sign_on_service": [
                            (
                                f"https://{keypair.name}.saml.test/sso",
                                BINDING_HTTP_REDIRECT,
                            )
                        ]
                    },
                    "policy": {"default": {"attribute_restrictions": None}},
                }
            },
        }
    )
    return Server(config=config)


def _first_assertion(response: Any) -> Any:
    assertion = response.assertion
    return assertion[0] if isinstance(assertion, list) else assertion


def _sign_response(
    response: Any,
    server: Server,
    *,
    sign_assertion: bool,
    sign_response: bool,
) -> str:
    to_sign: list[tuple[str, str]] = []
    assertion = _first_assertion(response)
    if sign_assertion:
        assertion.signature = pre_signature_part(assertion.id, server.sec.my_cert, 2)
        to_sign.append((class_name(assertion), assertion.id))
    if sign_response:
        response.signature = pre_signature_part(response.id, server.sec.my_cert, 1)
        to_sign.append((class_name(response), response.id))
    signed = signed_instance_factory(response, server.sec, to_sign)
    xml = signed if isinstance(signed, str) else signed.to_string()
    if isinstance(xml, str):
        xml = xml.encode()
    return base64.b64encode(xml).decode()


def signed_response(
    idp_key: IdPKeyPair,
    idp_cert: bytes | None = None,
    request_id: str = "request-id",
    recipient: str = "http://localhost:8000/saml2_auth/acs/",
    destination: str | None = None,
    audience: str = "http://None",
    subject_email: str = "member@example.test",
    not_on_or_after: datetime | None = None,
    *,
    email: str | None = None,
    email_attribute: str = "email",
    sign_assertion: bool = True,
    sign_response: bool = False,
    mutate: Mutation | None = None,
) -> str:
    """Build a real signed SAML ``Response`` with controlled bindings.

    ``mutate`` runs after pysaml2 has constructed the response and before
    xmlsec signs it, so negative cases can alter a binding while retaining a
    genuine signature.  Supplying a different ``idp_cert`` is rejected: the
    certificate and private key must remain a pair in these hermetic fixtures.
    """

    if idp_cert is not None and idp_cert != idp_key.certificate_pem:
        raise ValueError("idp_cert must match the supplied throwaway idp_key")

    expiry = not_on_or_after or (datetime.now(UTC) + timedelta(minutes=5))
    server = _idp_server(idp_key, audience=audience, recipient=recipient)
    response = server.create_authn_response(
        {
            email_attribute: [email if email is not None else subject_email],
            "first_name": ["Saml"],
            "last_name": ["Fixture"],
        },
        in_response_to=request_id,
        destination=recipient,
        sp_entity_id=audience,
        name_id=saml.NameID(text=subject_email, format=NAMEID_FORMAT_EMAILADDRESS),
        sign_assertion=False,
        sign_response=False,
        session_not_on_or_after=expiry,
    )
    response.destination = destination or recipient
    assertion = _first_assertion(response)
    assertion.authn_statement = [
        AuthnStatement(
            authn_instant=instant(),
            authn_context=AuthnContext(
                authn_context_class_ref=AuthnContextClassRef(
                    text="urn:oasis:names:tc:SAML:2.0:ac:classes:Password"
                )
            ),
            session_not_on_or_after=instant(time_stamp=expiry.timestamp()),
        )
    ]
    if assertion.conditions is not None:
        assertion.conditions.not_on_or_after = instant(time_stamp=expiry.timestamp())
    for confirmation in assertion.subject.subject_confirmation:
        confirmation.subject_confirmation_data.not_on_or_after = instant(
            time_stamp=expiry.timestamp()
        )
    if mutate is not None:
        mutate(response)
    return _sign_response(
        response,
        server,
        sign_assertion=sign_assertion,
        sign_response=sign_response,
    )
