"""Shared bounded value-suggestion contract for legacy and catalog pickers.

These limits do not constrain exact span-attribute filtering. They only bound
values offered by picker APIs; keys remain discoverable when a value is larger.
The tiny dependency-free module is also shipped with the standalone DEV
backfill bundle.
"""

TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES = 16 * 1024
JSON_ARRAY_STRING_SUGGESTION_MAX_UTF8_BYTES = 4 * 1024
