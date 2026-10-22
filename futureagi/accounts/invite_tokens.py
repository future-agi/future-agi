"""Signed tokens that name one OrganizationInvite.

The token goes in the ``<token>`` part of the accept-invite link, so each
email's link accepts the invite it was sent for. Validity (pending, not
expired) is checked on the invite itself, not in the token.
"""

import uuid

from django.core.signing import BadSignature, Signer

_SIGNER = Signer(salt="accounts.invite-accept", sep=".")


def make_invite_token(invite):
    return _SIGNER.sign(str(invite.id))


def read_invite_token(token):
    """Return the invite id in *token*, or None if it is not a valid invite token.

    Django's legacy password-reset tokens never contain ``.``, so they read
    as None and callers fall back to the legacy path.
    """
    if not token or "." not in token:
        return None
    try:
        return uuid.UUID(_SIGNER.unsign(token))
    except (BadSignature, ValueError):
        return None
