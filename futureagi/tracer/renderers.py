"""JSON rendering for trace-detail responses.

A trace-detail body nests one ``{"observation_span": ..., "children": [...]}``
entry per tree level. The stock C JSON encoder spends two recursion units per
level, so a long parent chain within the span cap cannot be rendered by the
stock ``JSONRenderer``: it raises ``RecursionError`` after the view returned,
and the client gets a generic server error.
"""

from typing import Any

from rest_framework import renderers as drf_renderers
from rest_framework.compat import LONG_SEPARATORS, SHORT_SEPARATORS

from tfc.utils.contants import RESULT_KEY_NAME

# The keys whose values hold the span tree: the envelope result, the root list
# and each entry's children. Only these containers nest deeper as the trace
# grows. Descending into other values would give the same bytes, at more cost.
_TREE_KEYS = frozenset({RESULT_KEY_NAME, "observation_spans", "children"})

_TEXT, _VALUE, _TREE, _CLOSE = range(4)


class TraceDetailJSONRenderer(drf_renderers.JSONRenderer):
    """``JSONRenderer`` with the same media type, format and bytes.

    The stock renderer always runs first, so every body it can render is
    unchanged. Only when it raises ``RecursionError`` is the payload encoded
    again: the span-tree containers (exact ``dict`` and ``list``) are walked
    with an explicit stack, and every other value is encoded by the encoder
    ``JSONRenderer`` builds. The result is the text the stock renderer would
    produce with no recursion limit. Indented output is never re-rendered.
    """

    def render(self, data, accepted_media_type=None, renderer_context=None):
        try:
            return super().render(data, accepted_media_type, renderer_context)
        except RecursionError:
            if self.get_indent(accepted_media_type, renderer_context or {}) is not None:
                raise
            text = self._encode_without_recursion(data)
            if text is None:
                raise
        # The same post-processing as JSONRenderer.render.
        text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        return text.encode()

    def _encode_without_recursion(self, data):
        """Return the unindented JSON text for ``data``, or ``None`` to decline.

        With ``indent=None`` the C encoder writes an exact dict as ``{`` +
        ``item_separator``-joined ``key + key_separator + value`` + ``}`` and an
        exact list as ``[`` + ``item_separator``-joined values + ``]``. A value
        encodes to the same text wherever it sits, so the pieces are spliced.
        """
        # The separators JSONRenderer.render takes from rest_framework.compat.
        separators = SHORT_SEPARATORS if self.compact else LONG_SEPARATORS
        item_separator, key_separator = separators
        # Exactly the encoder json.dumps builds for JSONRenderer.render.
        encode = self.encoder_class(
            skipkeys=False,
            ensure_ascii=self.ensure_ascii,
            check_circular=True,
            allow_nan=not self.strict,
            indent=None,
            separators=separators,
            default=None,
            sort_keys=False,
        ).encode
        encoded_keys: dict[str, str] = {}
        parts: list[str] = []
        open_containers: set[int] = set()
        pending: list[tuple[int, Any]] = [(_TREE, data)]
        items: list[tuple[int, Any]]
        while pending:
            kind, value = pending.pop()
            if kind == _TEXT:
                parts.append(value)
                continue
            if kind == _CLOSE:
                open_containers.discard(value)
                continue
            value_type = type(value)
            if kind == _VALUE or (value_type is not dict and value_type is not list):
                parts.append(encode(value))
                continue
            if id(value) in open_containers:
                raise ValueError("Circular reference detected")
            open_containers.add(id(value))
            if value_type is dict:
                items = [(_TEXT, "{")]
                for index, (key, item) in enumerate(value.items()):
                    if type(key) is not str:
                        # The stock encoder coerces such keys; leave it to the
                        # caller, which re-raises the original error.
                        return None
                    encoded_key = encoded_keys.get(key)
                    if encoded_key is None:
                        encoded_key = encoded_keys[key] = encode(key) + key_separator
                    items.append(
                        (_TEXT, item_separator + encoded_key if index else encoded_key)
                    )
                    items.append((_TREE if key in _TREE_KEYS else _VALUE, item))
                items.append((_TEXT, "}"))
            else:
                items = [(_TEXT, "[")]
                for index, item in enumerate(value):
                    if index:
                        items.append((_TEXT, item_separator))
                    items.append((_TREE, item))
                items.append((_TEXT, "]"))
            items.append((_CLOSE, id(value)))
            pending.extend(reversed(items))
        return "".join(parts)
