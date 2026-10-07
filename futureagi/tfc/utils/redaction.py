"""Keep credentials out of log lines: a known secret, or one inside a connection URL."""

import re

# scheme://user:password@ -- the password runs to the last "@" before the
# path, query or fragment. RFC 3986 forbids a raw "@" there, but redis-py,
# urllib, kombu and Go accept one and split on the last "@", so do the same.
_URL_PASSWORD = re.compile(
    r"(?P<head>[A-Za-z][A-Za-z0-9+.\-]*://[^:/?#@\s]*):[^/?#\s]*@"
)
# ?password=... / &password=... -- redis-py and ClickHouse read it there too.
_QUERY_PASSWORD = re.compile(r"(?P<head>[?&]password=)[^&#\s]*")


def redact_url_credentials(text: str) -> str:
    """Mask every URL password in ``text``.

    Covers the ``scheme://user:password@`` userinfo and a ``password=``
    query parameter. The user, host, port and path stay readable for
    debugging: ``redis://:s3cret@redis:6379/2`` becomes
    ``redis://:***@redis:6379/2``.
    """
    text = _URL_PASSWORD.sub(r"\g<head>:***@", text)
    return _QUERY_PASSWORD.sub(r"\g<head>***", text)


def redact_secret(text: str, secret: str) -> str:
    """Replace a known ``secret`` in ``text`` with ``[HIDDEN]``.

    Also covers the secret as it appears inside a quoted SQL string literal
    (backslashes, then single quotes, backslash-escaped, as ClickHouse
    writes it), since a server error can quote the statement it rejected.
    """
    if not secret:
        return text
    escaped = secret.replace("\\", "\\\\").replace("'", "\\'")
    for form in (escaped, secret):
        text = text.replace(form, "[HIDDEN]")
    return text
