"""Shared exact-key eligibility and bounded value-suggestion contracts.

Value-suggestion limits do not constrain exact span-attribute filtering; keys
remain discoverable when a value is larger. The key limit applies to both
picker lookups and exact filtering, preserving the ingested UTF-8 identity.
The tiny dependency-free module is also shipped with the standalone DEV
backfill bundle.
"""

TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES = 16 * 1024
JSON_ARRAY_STRING_SUGGESTION_MAX_UTF8_BYTES = 4 * 1024
ATTRIBUTE_KEY_MAX_UTF8_BYTES = 4096


def validate_exact_attribute_key(value: object) -> str:
    """Validate an opaque key without trimming, folding or excluding controls."""
    if not isinstance(value, str):
        raise ValueError("Attribute key must be text")
    if not value:
        raise ValueError("Attribute key is required")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("Attribute key must be valid UTF-8") from exc
    if len(encoded) > ATTRIBUTE_KEY_MAX_UTF8_BYTES:
        raise ValueError("Attribute key is too long")
    return value
