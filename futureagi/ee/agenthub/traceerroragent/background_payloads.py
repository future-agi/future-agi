"""One original-JSON validator and immutable, generation-bound payload capability.

No cleanup, Pydantic coercion, substring salvage or missing report defaults occur
here. The sealed bytes are owned by a witness; consumers obtain copies, so later
mutation cannot change the value that was checked. This is a program boundary,
not a cryptographic sandbox against arbitrary Python code in the same process.
"""

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from tracer.services.analysis_result_completion import IncompleteAnalysis

_SEAL = object()


def refuse(owner):
    witness = getattr(owner, "completion_witness", owner)
    if witness is not None:
        witness.failed = True
        witness.fallback_used = True
    raise IncompleteAnalysis()


def witness_for(owner):
    witness = getattr(owner, "completion_witness", owner)
    if witness is None or not hasattr(witness, "failed") or witness.failed:
        refuse(witness)
    return witness


@dataclass(frozen=True, init=False)
class ValidatedPayload(Mapping):
    _json: str
    _witness: object
    kind: str

    def __init__(self, text, witness, kind, seal=None):
        if seal is not _SEAL:
            refuse(witness)
        object.__setattr__(self, "_json", text)
        object.__setattr__(self, "_witness", witness)
        object.__setattr__(self, "kind", kind)

    def __getitem__(self, key):
        return json.loads(self._json)[key]

    def __iter__(self):
        return iter(json.loads(self._json))

    def __len__(self):
        return len(json.loads(self._json))


def require_payload(value, owner, kind):
    witness = witness_for(owner)
    if (
        not isinstance(value, ValidatedPayload)
        or value._witness is not witness
        or value.kind != kind
    ):
        refuse(witness)
    return json.loads(value._json)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("nonfinite JSON constant")


def _finite_float(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("nonfinite JSON number")
    return value


def original_json(raw):
    """Complete JSON/fence only. This decoder never repairs provider output."""
    if not isinstance(raw, str):
        raise TypeError("original JSON wire string required")
    text = raw.strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if match is None:
            raise ValueError("incomplete fence")
        text = match.group(1)
    value = json.loads(
        text,
        object_pairs_hook=_pairs,
        parse_constant=_invalid_constant,
        parse_float=_finite_float,
    )
    if type(value) is not dict:
        raise ValueError("original JSON object required")
    return value


def _check(value, schema, root):
    if "$ref" in schema:
        prefix = "#/$defs/"
        if not schema["$ref"].startswith(prefix):
            raise ValueError("unsupported schema reference")
        return _check(value, root["$defs"][schema["$ref"][len(prefix) :]], root)
    if "anyOf" in schema:
        for choice in schema["anyOf"]:
            try:
                _check(value, choice, root)
                return
            except (ValueError, TypeError, KeyError):
                pass
        raise ValueError("no matching JSON type")
    kind = schema["type"]  # Unknown/untyped schema nodes refuse, never allow-all.
    types = {
        "object": (dict,),
        "array": (list,),
        "string": (str,),
        "number": (int, float),
        "integer": (int,),
        "boolean": (bool,),
        "null": (type(None),),
    }
    if type(value) not in types[kind]:
        raise ValueError("original JSON type mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("invalid enum")
    if kind == "object":
        props = schema["properties"]
        if set(value) - set(props) or set(schema.get("required", ())) - set(value):
            raise ValueError("unknown or missing fields")
        for key, item in value.items():
            _check(item, props[key], root)
    elif kind == "array":
        if len(value) < schema.get("minItems", 1):
            raise ValueError("empty array")
        for item in value:
            _check(item, schema["items"], root)
    elif kind == "string":
        if not value.strip() and schema.get("minLength", 1):
            raise ValueError("empty string")
        if schema.get("format") == "uuid":
            UUID(value)
        elif "format" in schema:
            raise ValueError("unclassified string format")
    elif kind in ("number", "integer"):
        if (
            not math.isfinite(value)
            or value < schema.get("minimum", -math.inf)
            or value > schema.get("maximum", math.inf)
        ):
            raise ValueError("invalid numeric value")


def validate_payload(raw, schema, owner, kind, *, decode=False):
    """The sole validation entry: original data in, typed capability out."""
    witness = witness_for(owner)
    try:
        value = original_json(raw) if decode else raw
        _check(value, schema, schema)
        text = json.dumps(value, allow_nan=False)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        refuse(witness)
    return ValidatedPayload(text, witness, kind, _SEAL)
