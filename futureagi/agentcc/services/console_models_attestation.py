"""The Console model list's attestation: a signed statement, for one person's
browser session, of the rows FA computed for their organization and workspace.

An RS256 compact JWS signed with the FA host key that signs the omega_delegate_
host assertions (``accounts.omega_session.HostAssertionSigner``), told apart
from them by its exact ``typ`` and claim set:

- header: exactly ``{"alg": "RS256", "typ": "omega-models+jwt", "kid"}``;
- claims: exactly ``iss``, ``aud``, ``sub``, ``org_id``, ``workspace_id``,
  ``models_sha256``, ``iat`` and ``exp``, with the host assertion's issuer,
  audience and identity values and ``exp - iat`` = 900. Never ``jti``,
  ``nonce`` or ``role``: it is a statement, not a credential;
- ``models_sha256``: the lowercase hex sha256 of the RFC 8785 (JCS) form of
  the ``models`` array exactly as returned.

EE checks it beside each request's own host assertion. Contract:
lane-status/gwc2197-CONSOLE-RUN-SIGN-WIRE-1114.md section 3.
"""

import base64
import hashlib
import json
import math
import time
from decimal import Decimal

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from accounts.omega_session import (
    AUDIENCE,
    CLOCK_SKEW_MARGIN_SECONDS,
    DelegationUnavailable,
    HostAssertionSigner,
)

TYP = "omega-models+jwt"
LIFETIME_SECONDS = 900
MAX_ATTESTATION_LENGTH = 16384


class AttestationUnavailable(Exception):
    """The host key is not configured, or the statement cannot be signed."""


def _es_number(number):
    """ECMAScript Number::toString, as RFC 8785 section 3.2.2.3 requires."""
    number = float(number)
    if math.isnan(number) or math.isinf(number):
        raise ValueError("JCS has no NaN or Infinity")
    if number == 0:
        return "0"
    sign = "-" if number < 0 else ""
    _, digits, exponent = Decimal(repr(abs(number))).as_tuple()
    significant = "".join(map(str, digits)).rstrip("0")
    exponent += len(digits) - len(significant)
    k = len(significant)
    n = k + exponent
    if k <= n <= 21:
        return sign + significant + "0" * (n - k)
    if 0 < n <= 21:
        return sign + significant[:n] + "." + significant[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + significant
    mantissa = significant[0] + ("." + significant[1:] if k > 1 else "")
    e = n - 1
    return sign + mantissa + "e" + ("+" if e > 0 else "-") + str(abs(e))


def jcs(value):
    """The RFC 8785 canonical JSON text of ``value``."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _es_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(jcs(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JCS object keys are strings")
        keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=False) + ":" + jcs(value[key])
                for key in keys
            )
            + "}"
        )
    raise TypeError(f"JCS cannot carry {type(value).__name__}")


def models_sha256(models):
    return hashlib.sha256(jcs(models).encode("utf-8")).hexdigest()


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _segment(value):
    # The host assertions' encoding: compact UTF-8 JSON, base64url, no padding.
    return _b64url(
        json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )


def issue_attestation(
    models, *, subject, organization_id, workspace_id, signer=None, now=None
):
    """The compact JWS for ``models``. Raises AttestationUnavailable when the
    host key is not configured."""
    if signer is None:
        try:
            signer = HostAssertionSigner.from_environment()
        except DelegationUnavailable as exc:
            raise AttestationUnavailable() from exc
    # Dated back as the host assertions are: EE allows no clock skew on iat.
    issued_at = int(time.time() if now is None else now) - CLOCK_SKEW_MARGIN_SECONDS
    header = {"alg": "RS256", "typ": TYP, "kid": signer.key_id}
    claims = {
        "iss": signer.issuer,
        "aud": AUDIENCE,
        "sub": subject,
        "org_id": organization_id,
        "workspace_id": workspace_id,
        "models_sha256": models_sha256(models),
        "iat": issued_at,
        "exp": issued_at + LIFETIME_SECONDS,
    }
    signing_input = _segment(header) + "." + _segment(claims)
    signature = signer.private_key.sign(
        signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
    )
    token = signing_input + "." + _b64url(signature)
    if len(token) > MAX_ATTESTATION_LENGTH:
        raise AttestationUnavailable()
    return token
