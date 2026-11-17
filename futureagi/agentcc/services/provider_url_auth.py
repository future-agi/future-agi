"""Value-free admission and read-only handling of legacy provider URL auth."""

from urllib.parse import unquote, urlsplit, urlunsplit

from agentcc.services.provider_model_safety import InvalidProviderCredentialContext

PROVIDER_URL_AUTH_ERROR = "Base URL must not contain username or password credentials."


def reject_provider_url_userinfo(base_url):
    """Reject even username-only/empty userinfo before DNS or a saved-URL bypass."""
    if not isinstance(base_url, str):
        raise ValueError("Enter a valid URL.")
    try:
        parts = urlsplit(base_url)
    except (TypeError, ValueError):
        raise ValueError("Enter a valid URL.") from None
    if "@" in parts.netloc:
        raise ValueError(PROVIDER_URL_AUTH_ERROR)


def _legacy_url_parts(base_url):
    if not isinstance(base_url, str):
        raise InvalidProviderCredentialContext
    try:
        parts = urlsplit(base_url)
        _ = parts.port
        if base_url and (
            parts.scheme.lower() not in ("http", "https")
            or not parts.hostname
            or any(ch.isspace() or not ch.isprintable() for ch in base_url)
        ):
            raise InvalidProviderCredentialContext
        return parts
    except ValueError:
        raise InvalidProviderCredentialContext from None


def provider_url_auth_values(base_url):
    """Include stored and percent-decoded auth in every exclusion context.

    Invalid legacy auth cannot be certified safe: callers must fail closed,
    rather than comparing against an incomplete set or reflecting parser text.
    """
    parts = _legacy_url_parts(base_url)
    if "@" not in parts.netloc:
        return ()
    userinfo = parts.netloc.rsplit("@", 1)[0]
    values = [userinfo, *userinfo.split(":", 1)]
    try:
        return tuple(values + [unquote(value, errors="strict") for value in values])
    except UnicodeError:
        raise InvalidProviderCredentialContext from None


def public_provider_base_url(base_url):
    """Never expose URL userinfo from retained rows, and never mutate the row."""
    try:
        parts = _legacy_url_parts(base_url)
        return urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[-1]))
    except InvalidProviderCredentialContext:
        return ""
