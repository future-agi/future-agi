"""Shared eval-output SQL, kept leaf-level so ``schema.py`` can import it."""

# Structured evals nest their number here: {"score": 0.5, "choice": "Partial"}.
EVAL_STRUCTURED_SCORE_KEY = "score"

EVAL_TRUTHY_OUTPUTS = ("passed", "pass", "true", "1")
EVAL_FALSY_OUTPUTS = ("failed", "fail", "false", "0")

# JSONType names for a real number. A null or a string score is not scorable.
EVAL_NUMERIC_JSON_TYPES = ("Double", "Int64", "UInt64")

# A bare number rendered as text, e.g. "0.8".
EVAL_NUMERIC_OUTPUT_PATTERN = "^-?[0-9]+\\.?[0-9]*$"


def sql_str_set(values: tuple[str, ...]) -> str:
    """Render a tuple of strings as a SQL ``IN`` list, e.g. ``('pass', '1')``."""
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def eval_has_structured_score(json_args: str) -> str:
    """SQL predicate: does this row's eval output nest a NUMERIC score?

    Typed, not ``JSONHas``: the extractor beside it scores null/string as 0.
    ``json_args`` is a column, or a comma-joined JSON argument fragment.
    """
    return (
        f"(JSONType({json_args}, '{EVAL_STRUCTURED_SCORE_KEY}') "
        f"IN {sql_str_set(EVAL_NUMERIC_JSON_TYPES)})"
    )
