"""
Utility module for JSON path resolution and schema extraction.

Enables dot notation access to JSON column values (e.g., {{input.prompt}})
and extracts schema for frontend autocomplete.
"""

import ast
import json
import re
from typing import Any, List, Optional, Tuple

import json_repair


def resolve_json_path(json_data: Any, path: str) -> str:
    """
    Resolve a dot-notation path in JSON data with array support.

    Args:
        json_data: Parsed JSON object (dict/list) or JSON string
        path: Dot notation path like "a.b.c" or "items[0].name"

    Returns:
        Resolved value as string, or empty string if path not found

    Examples:
        >>> resolve_json_path({"a": {"b": "value"}}, "a.b")
        'value'
        >>> resolve_json_path({"items": [{"name": "first"}]}, "items[0].name")
        'first'
        >>> resolve_json_path({"a": 1}, "a.missing")
        ''
    """
    if json_data is None:
        return ""

    # Parse JSON string if needed
    if isinstance(json_data, str):
        try:
            json_data = json.loads(json_data)
        except (json.JSONDecodeError, ValueError):
            return ""

    # Split path into components, handling array notation
    # "items[0].name" -> ["items", "0", "name"]
    # "a.b.c" -> ["a", "b", "c"]
    parts = re.split(r"\.|\[|\]", path)
    parts = [p for p in parts if p]  # Remove empty strings

    current = json_data
    for part in parts:
        if current is None:
            return ""

        # Handle array index
        if part.isdigit():
            try:
                index = int(part)
                if isinstance(current, list) and 0 <= index < len(current):
                    current = current[index]
                else:
                    return ""
            except (ValueError, IndexError):
                return ""
        # Handle dict key
        elif isinstance(current, dict):
            current = current.get(part)
            if current is None:
                return ""
        else:
            return ""

    # Convert result to string for template substitution
    if current is None:
        return ""
    if isinstance(current, (dict, list)):
        return json.dumps(current)
    if isinstance(current, bool):
        return str(current).lower()
    return str(current)


def resolve_json_path_raw(json_data: Any, path: str) -> Any:
    """
    Resolve a dot-notation path in JSON data, returning raw values.

    Unlike resolve_json_path, this preserves native types (dict, list, int, float, bool)
    instead of converting everything to strings. Use this for schema extraction
    and type inference where preserving native types is important.

    Args:
        json_data: Parsed JSON object (dict/list) or JSON string
        path: Dot notation path like "a.b.c" or "items[0].name"

    Returns:
        Resolved value in its native type, or None if path not found

    Examples:
        >>> resolve_json_path_raw({"a": {"b": 123}}, "a.b")
        123
        >>> resolve_json_path_raw({"items": [{"name": "first"}]}, "items[0].name")
        'first'
        >>> resolve_json_path_raw({"a": 1}, "a.missing")
        None
    """
    if json_data is None:
        return None

    # Parse JSON string if needed
    if isinstance(json_data, str):
        try:
            json_data = json.loads(json_data)
        except (json.JSONDecodeError, ValueError):
            return None

    # Split path into components, handling array notation
    parts = re.split(r"\.|\[|\]", path)
    parts = [p for p in parts if p]

    current = json_data
    for part in parts:
        if current is None:
            return None

        # Handle array index
        if part.isdigit():
            try:
                index = int(part)
                if isinstance(current, list) and 0 <= index < len(current):
                    current = current[index]
                else:
                    return None
            except (ValueError, IndexError):
                return None
        # Handle dict key
        elif isinstance(current, dict):
            current = current.get(part)
            if current is None:
                return None
        else:
            return None

    return current


def extract_json_keys(
    json_data: Any,
    prefix: str = "",
    max_depth: int = 5,
    max_paths: Optional[int] = None,
) -> List[str]:
    """
    Extract all possible dot-notation paths from a JSON object.

    Stops extraction early if max_paths is reached to avoid excessive memory usage.

    Args:
        json_data: Parsed JSON object
        prefix: Current path prefix (used for recursion)
        max_depth: Maximum recursion depth to prevent infinite loops
        max_paths: Maximum number of paths to extract (stops early if reached)

    Returns:
        List of dot-notation paths (e.g., ["date", "config.timeout", "items[0].name"])

    Examples:
        >>> extract_json_keys({"a": 1, "b": {"c": 2}})
        ['a', 'b', 'b.c']
        >>> extract_json_keys({"items": [{"name": "x"}]})
        ['items', 'items[0]', 'items[0].name']
    """
    if max_depth <= 0:
        return []

    paths = []

    if isinstance(json_data, list) and json_data and prefix:
        # Handle arrays with a known prefix (nested arrays inside objects).
        # Top-level arrays (no prefix) are handled via max_array_count in the
        # schema response; the frontend generates indexed options from that
        # count so we don't emit bare "[0]" keys which would produce
        # malformed "col.[0]" paths in the dot-join expansion loops.
        count = min(len(json_data), 2)
        for i in range(count):
            if max_paths is not None and len(paths) >= max_paths:
                break
            array_path = f"{prefix}[{i}]"
            paths.append(array_path)

            if isinstance(json_data[i], dict):
                remaining = max_paths - len(paths) if max_paths else None
                sub_paths = extract_json_keys(
                    json_data[i], array_path, max_depth - 1, remaining
                )
                paths.extend(sub_paths)

    elif isinstance(json_data, dict):
        for key, value in json_data.items():
            # Check if we've reached the limit
            if max_paths is not None and len(paths) >= max_paths:
                break

            current_path = f"{prefix}.{key}" if prefix else key
            paths.append(current_path)

            if isinstance(value, dict):
                # Calculate remaining paths for recursion
                remaining = max_paths - len(paths) if max_paths else None
                sub_paths = extract_json_keys(
                    value, current_path, max_depth - 1, remaining
                )
                paths.extend(sub_paths)
            elif isinstance(value, list) and value:
                # Check limit before adding array path
                if max_paths is not None and len(paths) >= max_paths:
                    break

                # Add array notation for first element as sample
                array_path = f"{current_path}[0]"
                paths.append(array_path)

                if isinstance(value[0], dict):
                    remaining = max_paths - len(paths) if max_paths else None
                    sub_paths = extract_json_keys(
                        value[0], array_path, max_depth - 1, remaining
                    )
                    paths.extend(sub_paths)

            # Check again after recursion
            if max_paths is not None and len(paths) >= max_paths:
                break

    return paths[:max_paths] if max_paths else paths


def parse_json_safely(value: Any) -> Tuple[Optional[Any], bool]:
    """
    Safely parse a string that might be JSON.

    Uses a multi-layer fallback strategy:
      1. json.loads — strict, standard JSON
      2. json_repair.loads — tolerant of minor syntax issues (trailing commas,
         single quotes, unquoted keys, etc.)
      3. ast.literal_eval — catches Python dict/list literals that aren't valid
         JSON (e.g. True/False/None, single-quoted strings)

    Args:
        value: String value that might contain JSON

    Returns:
        Tuple of (parsed_data, is_valid_json)
        - parsed_data: The parsed JSON object, or None if invalid
        - is_valid_json: True if value was valid JSON dict/list

    Examples:
        >>> parse_json_safely('{"a": 1}')
        ({'a': 1}, True)
        >>> parse_json_safely('not json')
        (None, False)
        >>> parse_json_safely('"just a string"')
        (None, False)  # Valid JSON but not dict/list
        >>> parse_json_safely("{'a': 1, 'b': None}")
        ({'a': 1, 'b': None}, True)  # Python dict literal
        >>> parse_json_safely('{"a": 1, "b": 2,}')
        ({'a': 1, 'b': 2}, True)  # Trailing comma repaired
    """
    if not value:
        return None, False

    if isinstance(value, (dict, list)):
        return value, True

    if not isinstance(value, str):
        return None, False

    stripped = value.strip()
    if not stripped:
        return None, False

    # 1. Strict JSON parse
    try:
        data = json.loads(stripped)
        if isinstance(data, (dict, list)):
            return data, True
        return None, False
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Tolerant JSON repair (handles trailing commas, single quotes, etc.)
    try:
        data = json_repair.loads(stripped)
        if isinstance(data, (dict, list)):
            return data, True
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # 3. Python literal eval (handles True/False/None, single-quoted keys)
    try:
        data = ast.literal_eval(stripped)
        if isinstance(data, (dict, list)):
            return data, True
    except (ValueError, SyntaxError):
        pass

    return None, False


def extract_json_schema_for_column(sample_values: List[str]) -> dict:
    """
    Extract a unified JSON schema from all sample values.

    Iterates through all provided samples and merges their keys to capture
    the complete schema across rows with varying JSON structures.

    Args:
        sample_values: List of JSON string values from cells

    Returns:
        Schema dict with keys and sample data:
        {
            "keys": ["path1", "path2", ...],
            "sample": {...}  # First valid sample (truncated if large)
        }
    """
    all_keys = set()
    first_sample = None
    max_array_count = 0

    for value in sample_values:
        parsed, is_valid = parse_json_safely(value)
        if is_valid:
            if first_sample is None:
                first_sample = parsed
            # Track top-level array lengths
            if isinstance(parsed, list):
                max_array_count = max(max_array_count, len(parsed))
            keys = extract_json_keys(parsed, max_depth=5)
            all_keys.update(keys)

    # Sort keys for consistent ordering
    sorted_keys = sorted(all_keys)

    # Truncate sample if too large
    sample_for_storage = None
    if first_sample is not None:
        sample_str = json.dumps(first_sample)
        if len(sample_str) < 1000:
            sample_for_storage = first_sample

    result = {
        "keys": sorted_keys,
        "sample": sample_for_storage,
    }

    # Include max_array_count when column contains top-level arrays
    if max_array_count > 0:
        result["max_array_count"] = max_array_count

    return result
