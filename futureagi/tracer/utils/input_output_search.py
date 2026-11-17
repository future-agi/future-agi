"""Shared validation for the reserved Observe input/output search leaf."""

INPUT_OUTPUT_SEARCH_MAX_UTF8_BYTES = 1024


def validate_input_output_search(filter_type, filter_op, value):
    """Keep both public serializers and direct compiler callers bounded."""
    if filter_type != "text" or filter_op != "contains":
        raise ValueError("Input/output search requires a text contains filter.")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Input/output search requires nonblank text.")
    if len(value.encode("utf-8")) > INPUT_OUTPUT_SEARCH_MAX_UTF8_BYTES:
        raise ValueError("Input/output search supports at most 1,024 UTF-8 bytes.")
    return value
