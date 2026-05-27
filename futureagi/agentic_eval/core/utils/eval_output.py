"""Helpers for shaping the LLM ``response_format`` used by eval prompts.

Pure, dependency-free utilities that turn an eval template's declared
output type into the structured-output schema sent to the model. Use
these whenever an LLM judge needs to return a parseable verdict — they
guarantee the same schema across every code path so the gateway can
translate it to provider-native structured output (OpenAI
``response_format``, Anthropic tool-call, Gemini, Bedrock, etc.).
"""

from typing import Any

#: Output types this module recognises. Other strings are accepted but
#: collapse to a free-form-string schema, matching legacy behaviour.
SUPPORTED_OUTPUT_TYPES: frozenset[str] = frozenset(
    {"score", "numeric", "Pass/Fail", "choices"}
)


def response_format_schema(
    output_type: str,
    choices: list[str] | None = None,
    multi_choice: bool = False,
) -> dict[str, Any]:
    """Build a ``json_schema`` ``response_format`` dict for an LLM judge call.

    The returned dict is the exact value an LLM client should set as its
    ``response_format`` parameter. The schema constrains the model to a
    JSON object with two fields, ``result`` and ``explanation``:

    * ``result`` is typed according to the eval's output. For
      ``"choices"`` evals the schema reflects whether the template was
      configured for a single selection or multi-pick (``multi_choice``
      — an array of labels, at least one; deduplicated downstream).
    * ``explanation`` is always a free-form string the model uses to
      justify the verdict.

    ``choices`` is required when ``output_type == "choices"``. An empty
    list or ``None`` falls back to the free-form-string schema rather
    than producing an unusable empty enum.

    Examples:

    >>> response_format_schema("score")["json_schema"]["schema"]["properties"]["result"]
    {'type': 'number'}

    >>> response_format_schema("Pass/Fail")["json_schema"]["schema"]["properties"]["result"]
    {'type': 'string', 'enum': ['Pass', 'Fail']}

    >>> response_format_schema("choices", ["High", "Medium", "Low"]) \\
    ...     ["json_schema"]["schema"]["properties"]["result"]["enum"]
    ['High', 'Medium', 'Low']

    >>> response_format_schema("choices", ["A", "B", "C"], multi_choice=True) \\
    ...     ["json_schema"]["schema"]["properties"]["result"]["type"]
    'array'

    Mutating the ``choices`` list after the call does not affect the
    returned schema — it stores a fresh copy.
    """
    if output_type in ("score", "numeric"):
        result_schema: dict[str, Any] = {"type": "number"}
    elif output_type == "Pass/Fail":
        result_schema = {"type": "string", "enum": ["Pass", "Fail"]}
    elif output_type == "choices" and choices:
        if multi_choice:
            # NB: no ``uniqueItems`` — some provider structured-output
            # validators (Bedrock) reject that keyword on arrays.
            # Duplicates are filtered downstream when the result is
            # persisted.
            result_schema = {
                "type": "array",
                "items": {"type": "string", "enum": list(choices)},
                "minItems": 1,
            }
        else:
            result_schema = {"type": "string", "enum": list(choices)}
    else:
        result_schema = {"type": "string"}

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "eval_result",
            "schema": {
                "type": "object",
                "properties": {
                    "result": result_schema,
                    "explanation": {"type": "string"},
                },
                "required": ["result", "explanation"],
            },
        },
    }


__all__ = ["SUPPORTED_OUTPUT_TYPES", "response_format_schema"]
