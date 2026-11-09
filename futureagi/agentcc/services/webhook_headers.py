"""A webhook's custom headers, read the same way everywhere.

The API stored ``headers`` as it was sent: a JSON object, or (taken until
ALERT-ACCESS-1) a list of ``[name, value]`` pairs, which delivery's
``dict.update`` reads like the object. ``header_map`` turns either shape into
the mapping a delivery sends, so the read mask, the masked-value retention, the
URL-change check, validation and delivery all reason about the same headers.
Any other shape raises ``UnsupportedHeaders``: a read shows no value of it, a
URL change treats it as saved headers, and delivery sends nothing.
"""

HEADERS_SHAPE_ERROR = (
    "headers must be a JSON object of header names to values, or a list of "
    "[name, value] pairs"
)


class UnsupportedHeaders(ValueError):
    """Saved or sent ``headers`` in neither accepted shape."""


def _is_pair(item) -> bool:
    return isinstance(item, list | tuple) and len(item) == 2


def header_map(value) -> dict:
    """``value`` as the ``{name: value}`` a delivery sends.

    Empty (``None``, ``{}``, ``[]``) is no headers. A list of pairs keeps the
    last value for a name, as ``dict.update`` does. Names must be strings.
    Raises ``UnsupportedHeaders`` otherwise.
    """
    if not value:
        return {}
    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, list | tuple) and all(_is_pair(item) for item in value):
        pairs = list(value)
    else:
        raise UnsupportedHeaders(HEADERS_SHAPE_ERROR)
    headers = {}
    for name, item in pairs:
        if not isinstance(name, str) or not name:
            raise UnsupportedHeaders(HEADERS_SHAPE_ERROR)
        headers[name] = item
    return headers
