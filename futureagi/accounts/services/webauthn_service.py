import json

import structlog
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from accounts.models.user import User
from accounts.models.webauthn_credential import WebAuthnCredential

logger = structlog.get_logger(__name__)

WEBAUTHN_CHALLENGE_TTL = getattr(settings, "WEBAUTHN_CHALLENGE_TTL", 120)


def _get_rp_id():
    return getattr(settings, "WEBAUTHN_RP_ID", "localhost")


def _get_rp_name():
    return getattr(settings, "WEBAUTHN_RP_NAME", "FutureAGI")


def _get_origin():
    return getattr(settings, "WEBAUTHN_ORIGIN", "http://localhost:3031")


def get_registration_options(user):
    """Generate WebAuthn registration options.
    Returns: (options_json_dict, challenge_bytes)
    """
    # Get existing credential IDs to exclude
    existing_creds = WebAuthnCredential.objects.filter(user=user)
    exclude_credentials = []
    for cred in existing_creds:
        try:
            exclude_credentials.append(
                PublicKeyCredentialDescriptor(
                    id=base64url_to_bytes(cred.credential_id),
                )
            )
        except Exception:
            pass

    options = generate_registration_options(
        rp_id=_get_rp_id(),
        rp_name=_get_rp_name(),
        user_id=str(user.id).encode("utf-8"),
        user_name=user.email,
        user_display_name=user.name or user.email,
        exclude_credentials=exclude_credentials,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )

    # Store challenge in Redis
    challenge_key = f"webauthn_reg_challenge:{user.id}"
    cache.set(
        challenge_key,
        bytes_to_base64url(options.challenge),
        timeout=WEBAUTHN_CHALLENGE_TTL,
    )

    # Convert to JSON-serializable dict
    options_json = json.loads(options_to_json(options))

    return options_json, options.challenge


def verify_registration(user, credential_response, expected_challenge, name=""):
    """Verify the WebAuthn registration response from the browser.
    Creates and returns a WebAuthnCredential record.
    """
    verification = verify_registration_response(
        credential=credential_response,
        expected_challenge=expected_challenge,
        expected_rp_id=_get_rp_id(),
        expected_origin=_get_origin(),
    )

    # Store the credential
    credential = WebAuthnCredential.objects.create(
        user=user,
        name=name or "Passkey",
        credential_id=bytes_to_base64url(verification.credential_id),
        public_key=bytes_to_base64url(verification.credential_public_key),
        sign_count=verification.sign_count,
        aaguid=str(verification.aaguid) if verification.aaguid else "",
        backup_eligible=getattr(verification, "credential_backed_up", False),
        backup_state=getattr(verification, "credential_backed_up", False),
    )

    # Clean up the challenge
    cache.delete(f"webauthn_reg_challenge:{user.id}")

    return credential


def get_authentication_options(user=None):
    """Generate WebAuthn authentication options.
    If user provided: include their credential IDs as allowCredentials.
    If None (passwordless): empty allowCredentials for discoverable credentials.
    Returns: (options_json_dict, challenge_bytes)
    """
    allow_credentials = []
    if user:
        existing_creds = WebAuthnCredential.objects.filter(user=user)
        for cred in existing_creds:
            try:
                allow_credentials.append(
                    PublicKeyCredentialDescriptor(
                        id=base64url_to_bytes(cred.credential_id),
                    )
                )
            except Exception:
                pass

    options = generate_authentication_options(
        rp_id=_get_rp_id(),
        allow_credentials=allow_credentials if allow_credentials else None,
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    # Store challenge in Redis
    import uuid

    session_id = str(uuid.uuid4())
    challenge_data = {
        "challenge": bytes_to_base64url(options.challenge),
        "user_id": str(user.id) if user else None,
    }
    cache.set(
        f"webauthn_auth_challenge:{session_id}",
        json.dumps(challenge_data),
        timeout=WEBAUTHN_CHALLENGE_TTL,
    )

    options_json = json.loads(options_to_json(options))
    # Include session_id in the response so client can pass it back
    # Include session_id so the client can pass it back during verification
    options_json["session_id"] = session_id

    return options_json, options.challenge


def verify_authentication(credential_response, expected_challenge, user=None):
    """Verify the WebAuthn authentication response.
    Returns: (user, credential)
    """
    # Look up credential by credential_id from response
    raw_id = credential_response.get("rawId") or credential_response.get("id", "")

    lookup = {"credential_id": raw_id}
    if user is not None:
        lookup["user"] = user
    credential = WebAuthnCredential.objects.get(**lookup)

    verification = verify_authentication_response(
        credential=credential_response,
        expected_challenge=expected_challenge,
        expected_rp_id=_get_rp_id(),
        expected_origin=_get_origin(),
        credential_public_key=base64url_to_bytes(credential.public_key),
        credential_current_sign_count=credential.sign_count,
    )

    # Update sign count and last used
    credential.sign_count = verification.new_sign_count
    credential.last_used_at = timezone.now()
    credential.save(update_fields=["sign_count", "last_used_at", "updated_at"])

    return credential.user, credential
