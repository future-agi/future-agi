"""Invitation authentication without pre-validation token/cache side effects."""

from accounts.api_key_lookup import find_api_key
from accounts.authentication import APIKeyAuthentication, decode_token


class InvitationAuthentication(APIKeyAuthentication):
    """Keep identity, SAML and workspace enforcement, not auth bookkeeping.

    DRF authenticates before entering the handler's transaction. Even a valid
    preview or refused link must therefore authenticate without refreshing the
    bearer or auth cache. The invite token/row remains the acceptance authority;
    the handler still refuses a bearer belonging to a different user.
    """

    def _decode_bearer(self, token):
        return decode_token(token, touch=False)

    def _find_api_key(self, queryset, api_key, secret_key):
        # Compatibility auth must not rewrite a legacy/stale key row either.
        return find_api_key(queryset, api_key, secret_key, reseal=False)
