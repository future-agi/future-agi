"""Readers for ``ProjectVersion.eval_tags``.

``eval_tags`` is persisted straight from client-supplied payloads. The SDK
contract declares it as a bare ``serializers.JSONField(required=False,
allow_null=True)`` with no per-entry schema, and ``tracer/utils/otel.py``
copies ``type`` and ``value`` through verbatim via ``eval_tag.get(...)`` —
which yields ``None`` when the client omits the key.

Every reader therefore has to treat the entries as untrusted: keys may be
absent, values may be ``None``, and an entry may not be a mapping at all.
Callers that need span-addressable tags should go through
``span_type_eval_tags`` rather than indexing the raw list.
"""

from __future__ import annotations

OBSERVATION_SPAN_TYPE = "OBSERVATION_SPAN_TYPE"


def span_type_eval_tags(eval_tags):
    """Return the eval tags that can be matched against an observation span.

    A tag is usable only when all three of these hold:

    * ``type`` is exactly ``OBSERVATION_SPAN_TYPE`` — other types address
      different targets and have no span semantics;
    * ``value`` is a non-empty string — it names the span type to match, and
      callers lowercase it;
    * ``custom_eval_config_id`` is present — without it the tag cannot be
      resolved to a ``CustomEvalConfig``.

    Entries failing any condition are dropped: they are not addressable by
    span type, so skipping them is the same outcome as never matching, minus
    the ``AttributeError``.
    """
    usable = []
    for eval_tag in eval_tags or []:
        if not isinstance(eval_tag, dict):
            continue
        if eval_tag.get("type") != OBSERVATION_SPAN_TYPE:
            continue
        value = eval_tag.get("value")
        if not isinstance(value, str) or not value:
            continue
        if not eval_tag.get("custom_eval_config_id"):
            continue
        usable.append(eval_tag)
    return usable
