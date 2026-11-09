"""The network boundary of SIEM export: which URLs a destination may name, and
the HTTP session a delivery goes out on.

Production admits https URLs whose host resolves to public addresses only (no
loopback, link-local, metadata or private ranges, no credentials in the URL)
and sends through the SSRF-safe session the outbound webhooks use, which
checks the address again at connect time and follows no redirect. Tests
replace exactly these two functions to reach a loopback receiver; nothing
else in SIEM export opens a connection.
"""

from urllib.parse import urlsplit

from agentcc.services.url_safety import build_ssrf_safe_session, ensure_public_http_url

SIEM_URL_ERROR = (
    "A SIEM destination must be an https URL on a public address, with no "
    "credentials, query or fragment in it."
)
MAX_URL_LENGTH = 2048


def check_destination_url(url):
    """Raise ``ValueError`` unless ``url`` is a URL a destination may name."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise ValueError(SIEM_URL_ERROR)
    if any(ch.isspace() or ord(ch) < 0x21 or ord(ch) > 0x7E for ch in url):
        raise ValueError(SIEM_URL_ERROR)
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises on a malformed port
    except ValueError:
        raise ValueError(SIEM_URL_ERROR) from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError(SIEM_URL_ERROR)
    ensure_public_http_url(url, SIEM_URL_ERROR)


def open_session():
    """The session one delivery attempt posts on."""
    return build_ssrf_safe_session(SIEM_URL_ERROR)
