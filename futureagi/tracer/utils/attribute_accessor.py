"""
Span Attribute Accessor Utility

Provides a unified interface to access span attributes with backward compatibility.
During the transition from eval_attributes to span_attributes, this accessor
ensures consistent attribute access regardless of which field contains the data.

Usage:
    from tracer.utils.attribute_accessor import SpanAttributeAccessor

    # Get attributes from a span
    accessor = SpanAttributeAccessor(span)
    model_name = accessor.get("llm.model_name")

    # Get all attributes
    all_attrs = accessor.get_all()
"""

import json
from typing import Any, Optional

from tracer.models.observation_span import ObservationSpan


class SpanAttributeAccessor:
    """
    Unified accessor for span attributes with backward compatibility.

    Prefers span_attributes, falls back to eval_attributes for older data.
    This ensures a smooth transition during the attribute consolidation migration.
    """

    def __init__(self, span: ObservationSpan):
        """
        Initialize the accessor with an ObservationSpan instance.

        Args:
            span: The ObservationSpan to access attributes from
        """
        self._span = span

    def get_all(self) -> dict[str, Any]:
        """
        Get all attributes from the span.

        Returns span_attributes if populated, otherwise falls back to eval_attributes.
        This provides backward compatibility during the migration.

        Returns:
            dict: All span attributes
        """
        # Prefer span_attributes (new canonical field)
        # Note: empty dict {} is truthy, so explicitly check for it
        if self._span.span_attributes and self._span.span_attributes != {}:
            return self._span.span_attributes

        # Fall back to eval_attributes (deprecated, for backward compat)
        if self._span.eval_attributes and self._span.eval_attributes != {}:
            return self._span.eval_attributes

        return {}

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a specific attribute by key.

        First checks span_attributes, then eval_attributes for backward compatibility.

        Args:
            key: The attribute key to look up
            default: Default value if key not found

        Returns:
            The attribute value or default
        """
        attributes = self.get_all()
        return attributes.get(key, default)

    def has(self, key: str) -> bool:
        """
        Check if an attribute exists.

        Args:
            key: The attribute key to check

        Returns:
            bool: True if the attribute exists
        """
        return key in self.get_all()

    def keys(self) -> list[str]:
        """
        Get all attribute keys.

        Returns:
            list: All attribute keys
        """
        return list(self.get_all().keys())

    def items(self) -> list[tuple[str, Any]]:
        """
        Get all attribute key-value pairs.

        Returns:
            list: List of (key, value) tuples
        """
        return list(self.get_all().items())


def get_span_attributes(span: ObservationSpan) -> dict[str, Any]:
    """
    Convenience function to get all attributes from a span.

    This is a simpler alternative to using SpanAttributeAccessor when
    you just need to get all attributes at once.

    Args:
        span: The ObservationSpan to get attributes from

    Returns:
        dict: All span attributes with backward compatibility
    """
    return SpanAttributeAccessor(span).get_all()


def get_span_attribute(span: ObservationSpan, key: str, default: Any = None) -> Any:
    """
    Convenience function to get a specific attribute from a span.

    Args:
        span: The ObservationSpan to get the attribute from
        key: The attribute key to look up
        default: Default value if key not found

    Returns:
        The attribute value or default
    """
    return SpanAttributeAccessor(span).get(key, default)


def span_raw_log(attrs) -> dict:
    """The provider call payload (``raw_log``) in a voice span's attributes.

    ClickHouse keeps it as a JSON string in ``attrs_string`` (collector
    ingest) or as an object in ``attributes_extra`` (PG-era rows); return a
    dict either way, ``{}`` when absent or unparseable.
    """
    raw_log = attrs.get("raw_log")
    if isinstance(raw_log, str):
        try:
            raw_log = json.loads(raw_log)
        except json.JSONDecodeError:
            return {}
    return raw_log if isinstance(raw_log, dict) else {}
