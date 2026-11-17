"""The Hugging Face Hub token for an organization's dataset import.

Every Hub call made for an organization's dataset import (preview, load,
revision, size and detail lookups) carries the organization's saved Hugging
Face key. An organization with no saved key calls the Hub with no token.
"""

from huggingface_hub.errors import GatedRepoError

from model_hub.models.api_key import ApiKey

ACCESS_TOKEN_NEEDED_MESSAGE = (
    "This dataset needs a Hugging Face access token. "
    "Save your own token in Settings, then try again."
)

# The Hub's answers for a dataset that needs a token the call did not carry.
_NEEDS_ACCESS_STATUSES = frozenset({401, 403})


class HubAccessTokenNeeded(Exception):
    """The Hub answered that the dataset needs an access token, and the
    organization has no saved Hugging Face key."""

    def __init__(self):
        super().__init__(ACCESS_TOKEN_NEEDED_MESSAGE)


def organization_hub_token(organization_id):
    """The organization's saved Hugging Face key, or None when it has none.

    A saved key this deployment cannot decrypt raises ``KeyUnreadable``.
    """
    key = ApiKey.objects.filter(
        organization_id=organization_id, provider="huggingface"
    ).first()
    return (key.actual_key if key and key.key else None) or None


def hub_headers(token):
    """Headers for an HTTP call to the Hub: a bearer header only with a key."""
    return {"Authorization": f"Bearer {token}"} if token else {}


def hub_client_token(token):
    """The ``token`` argument for huggingface_hub and datasets calls.

    Those libraries read a locally saved login when ``token`` is None;
    ``False`` makes the call with no token.
    """
    return token if token else False


def _status(error):
    return getattr(getattr(error, "response", None), "status_code", None)


def _error_chain(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or error.__context__


def needs_access_status(status_code, token):
    """True when an HTTP answer to a call without a key says the dataset
    needs one."""
    return not token and status_code in _NEEDS_ACCESS_STATUSES


def needs_access_token(error, token):
    """True when a call without a key failed because the dataset needs one.

    The datasets library answers a gated or private dataset with its own
    not-found error raised from the Hub's answer, so the whole chain is read.
    """
    if token:
        return False
    return any(
        isinstance(link, GatedRepoError) or _status(link) in _NEEDS_ACCESS_STATUSES
        for link in _error_chain(error)
    )
