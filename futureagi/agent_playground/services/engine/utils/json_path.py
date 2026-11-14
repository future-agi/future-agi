"""
JSON dot notation utilities for variable parsing and extraction.

Used by the LLM prompt runner and other components that need to:
- Parse variable placeholders like {{Node1.response.data.name}}
- Extract nested values from JSON data using dot notation paths
- Resolve variables against input data with fallback behavior

These utilities are reusable across the codebase (runner, validation, UI port generation).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParsedVariable:
    """Result of parsing a variable placeholder string.

    Attributes:
        parent_node_name: Name of the parent node (first segment before first dot).
            None for simple variables like "question".
        output_port_name: Display name of the output port on the parent node
            (after first dot, before next dot or bracket).
            None for simple variables.
        extraction_path: Remaining JSON extraction path after parent_node.output_port.
            e.g., ".data.name" or "[0].key". None if no extraction needed.
        is_dot_notation: True if the variable references a parent node port.
    """

    parent_node_name: str | None
    output_port_name: str | None
    extraction_path: str | None
    is_dot_notation: bool


def parse_variable(variable_str: str) -> ParsedVariable:
    """Parse a variable placeholder string into its components.

    The variable string comes from a {{...}} placeholder in a prompt message.
    It can be:
    - A simple variable: "question" → no dot notation
    - A port reference: "Node1.response" → parent node + output port
    - A port reference with extraction: "Node1.response.data.name" → parent + port + path

    Args:
        variable_str: The variable string (without {{ }}).

    Returns:
        ParsedVariable with the parsed components.

    Examples:
        >>> parse_variable("question")
        ParsedVariable(parent_node_name=None, output_port_name=None,
                       extraction_path=None, is_dot_notation=False)

        >>> parse_variable("Node1.response")
        ParsedVariable(parent_node_name="Node1", output_port_name="response",
                       extraction_path=None, is_dot_notation=True)

        >>> parse_variable("Node1.response.data.name")
        ParsedVariable(parent_node_name="Node1", output_port_name="response",
                       extraction_path=".data.name", is_dot_notation=True)

        >>> parse_variable("Node1.response[0].key")
        ParsedVariable(parent_node_name="Node1", output_port_name="response",
                       extraction_path="[0].key", is_dot_notation=True)
    """
    first_dot = variable_str.find(".")
    if first_dot == -1:
        return ParsedVariable(
            parent_node_name=None,
            output_port_name=None,
            extraction_path=None,
            is_dot_notation=False,
        )

    parent_node_name = variable_str[:first_dot]
    remainder = variable_str[first_dot + 1 :]

    # Find the next delimiter (. or [) in the remainder
    next_dot = remainder.find(".")
    next_bracket = remainder.find("[")

    if next_dot == -1 and next_bracket == -1:
        # Only two segments, no extraction path
        return ParsedVariable(
            parent_node_name=parent_node_name,
            output_port_name=remainder,
            extraction_path=None,
            is_dot_notation=True,
        )

    # Find the earlier delimiter
    if next_dot == -1:
        delimiter_pos = next_bracket
    elif next_bracket == -1:
        delimiter_pos = next_dot
    else:
        delimiter_pos = min(next_dot, next_bracket)

    output_port_name = remainder[:delimiter_pos]
    extraction_path = remainder[delimiter_pos:]

    return ParsedVariable(
        parent_node_name=parent_node_name,
        output_port_name=output_port_name,
        extraction_path=extraction_path,
        is_dot_notation=True,
    )


def _tokenize_path(path: str) -> list[str | int]:
    """Tokenize a JSON path string into a list of keys (str) and indices (int).

    Args:
        path: A path string like ".data.name", "[0].key", ".items[1]".

    Returns:
        List of tokens. String tokens for dict key access, int tokens for list indexing.

    Examples:
        >>> _tokenize_path(".data.name")
        ["data", "name"]
        >>> _tokenize_path("[0].key")
        [0, "key"]
        >>> _tokenize_path(".items[1]")
        ["items", 1]
    """
    tokens: list[str | int] = []
    i = 0
    while i < len(path):
        if path[i] == ".":
            # Key access
            i += 1
            key_start = i
            while i < len(path) and path[i] not in (".", "["):
                i += 1
            if i > key_start:
                tokens.append(path[key_start:i])
        elif path[i] == "[":
            # Index access
            i += 1
            idx_start = i
            while i < len(path) and path[i] != "]":
                i += 1
            try:
                tokens.append(int(path[idx_start:i]))
            except ValueError:
                raise ValueError(
                    f"Invalid array index '{path[idx_start:i]}' in path '{path}'"
                )
            i += 1  # skip ']'
        else:
            i += 1
    return tokens


def extract_json_path(data: Any, path: str) -> Any:
    """Extract a value from nested data using a JSON dot notation path.

    Args:
        data: The data to extract from (dict, list, or nested combination).
        path: The extraction path (e.g., ".data.name", "[0].key", ".items[1]").

    Returns:
        The extracted value.

    Raises:
        ValueError: If extraction fails (missing key, index out of range, type mismatch).

    Examples:
        >>> extract_json_path({"data": {"name": "Alice"}}, ".data.name")
        "Alice"
        >>> extract_json_path([{"key": "v"}], "[0].key")
        "v"
        >>> extract_json_path({"items": [1, 2, 3]}, ".items[1]")
        2
    """
    tokens = _tokenize_path(path)
    current = data

    for token in tokens:
        if isinstance(token, int):
            if not isinstance(current, (list, tuple)):
                raise ValueError(
                    f"Cannot index with [{token}]: expected list/tuple, "
                    f"got {type(current).__name__}"
                )
            if token >= len(current) or token < -len(current):
                raise ValueError(
                    f"Index [{token}] out of range for list of length {len(current)}"
                )
            current = current[token]
        else:
            if not isinstance(current, dict):
                raise ValueError(
                    f"Cannot access key '{token}': expected dict, "
                    f"got {type(current).__name__}"
                )
            if token not in current:
                raise ValueError(
                    f"Key '{token}' not found. Available keys: {list(current.keys())}"
                )
            current = current[token]

    return current


def resolve_variable(variable_str: str, inputs: dict[str, Any]) -> Any:
    """Resolve a variable placeholder against the inputs dict.

    The input port display_name IS the full variable name from the prompt.
    So inputs[variable_str] gives the raw data from the connected parent port.
    If the variable has an extraction path, it is applied to that raw data.

    If extraction fails, the variable is treated as a global variable.
    If the global variable also isn't found, ValueError is raised.

    Args:
        variable_str: The variable string (without {{ }}).
        inputs: Dict mapping port routing keys to their values.

    Returns:
        The resolved value.

    Raises:
        ValueError: If the variable cannot be resolved.

    Examples:
        >>> resolve_variable("question", {"question": "What is AI?"})
        "What is AI?"

        >>> resolve_variable("Node1.response", {"Node1.response": "Hello"})
        "Hello"

        >>> resolve_variable("Node1.response.name",
        ...     {"Node1.response.name": {"name": "Alice", "age": 30}})
        "Alice"
    """
    if variable_str in inputs:
        value = inputs[variable_str]
        parsed = parse_variable(variable_str)

        if parsed.extraction_path is not None:
            try:
                return extract_json_path(value, parsed.extraction_path)
            except (ValueError, KeyError, IndexError, TypeError):
                # Extraction failed — fall through to global variable fallback
                pass
        else:
            return value

    raise ValueError(f"Input '{variable_str}' not found")


def _fetch_user_response_schema(schema_id: str) -> dict | None:
    """Fetch a UserResponseSchema's schema by its UUID.

    Returns the schema dict, or None if not found or if the id is not a valid UUID.
    """
    import uuid as _uuid

    try:
        _uuid.UUID(str(schema_id))
    except (ValueError, AttributeError):
        return None

    try:
        from model_hub.models.run_prompt import UserResponseSchema

        urs = UserResponseSchema.no_workspace_objects.get(id=schema_id, deleted=False)
        return urs.schema if urs.schema else None
    except Exception:
        return None


def get_output_schema_for_response_format(
    response_format=None,
) -> dict:
    """Determine the output port data_schema based on response_format.

    Args:
        response_format: The response_format from prompt_config_snapshot.configuration.
            Can be:
            - "text" or None → string schema
            - "json" → object schema
            - UUID string → lookup UserResponseSchema by id
            - dict with "schema" key → inline JSON schema (UserResponseSchema)

    Returns:
        A JSON Schema dict suitable for setting on an output port's data_schema.
    """
    if response_format is None or response_format == "text":
        return {"type": "string", "description": "LLM response text"}
    elif response_format == "json":
        return {"type": "object", "description": "LLM JSON response"}
    elif isinstance(response_format, dict) and response_format.get("schema"):
        return response_format["schema"]
    else:
        # UUID string pointing to UserResponseSchema
        schema = _fetch_user_response_schema(response_format)
        if schema:
            return schema
        return {"type": "string", "description": "LLM response"}
