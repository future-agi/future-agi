"""Keep credentials that ride inside connection URLs out of log lines."""

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
