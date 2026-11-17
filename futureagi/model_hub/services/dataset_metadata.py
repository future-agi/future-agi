"""Flat dataset metadata patches, matching the editor's orgRules.js contract."""

import math
import re


METADATA_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,39}$")
METADATA_MAX_KEYS = 30
METADATA_VALUE_MAX_CHARS = 200


class DatasetMetadataError(ValueError):
    """A metadata patch cannot be saved; its message is the editor's sentence."""


def merge_metadata(current, patch):
    """Validate and return a new map; null deletes, and neither input is mutated.

    Call with the dataset row locked until persistence so both merge and the
    resulting-key limit see the latest committed metadata, not a stale copy.
    """
    if not isinstance(patch, dict):
        raise DatasetMetadataError("Metadata must be an object of key: value pairs.")

    # Like the frontend, inspect all keys before inspecting any values.
    for key in patch:
        if not isinstance(key, str) or not METADATA_KEY_RE.fullmatch(key):
            raise DatasetMetadataError(
                f"Metadata key “{key}” must start with a lowercase letter and use only "
                "lowercase letters, digits, “.”, “_” or “-” (40 characters at most)."
            )

    for key, value in patch.items():
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise DatasetMetadataError(f"“{key}” must be text, a number or true/false.")
        elif isinstance(value, str):
            # JavaScript string.length counts UTF-16 code units, including for
            # supplementary characters. Keep the server/editor boundary equal.
            length = len(value.encode("utf-16-le", errors="surrogatepass")) // 2
            if length > METADATA_VALUE_MAX_CHARS:
                raise DatasetMetadataError(
                    f"The value of “{key}” is longer than {METADATA_VALUE_MAX_CHARS} characters."
                )
        elif isinstance(value, list):
            raise DatasetMetadataError(
                f"“{key}” must be text, a number or true/false, not a list."
            )
        else:
            raise DatasetMetadataError(
                f"“{key}” must be text, a number or true/false, not an object."
            )

    merged = dict(current) if isinstance(current, dict) else {}
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    if len(merged) > METADATA_MAX_KEYS:
        raise DatasetMetadataError(
            f"A dataset can have up to {METADATA_MAX_KEYS} metadata keys."
        )
    return merged
