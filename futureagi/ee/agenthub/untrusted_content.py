"""Framing for recorded content that is put in front of a model.

Traces, tool results and eval explanations are written by the customer's
application, its tools and its end users. A model that reads them is reading
data, and the prompt has to make that boundary visible: recorded text goes
inside a delimited block, and text inside the block cannot produce the
block's own markers.

The markers use the mathematical white square brackets ⟦ ⟧. Recorded text that
contains either character has it replaced with an ASCII pair, so the only
⟦ or ⟧ a model ever sees is one this module wrote. Nothing else in the
recorded text is altered: quotes copied from a block still match the recorded
value once the same substitution is applied to it.
"""

import json
from typing import Any

DATA_OPEN = "⟦"
DATA_CLOSE = "⟧"
_SUBSTITUTES = {DATA_OPEN: "[[", DATA_CLOSE: "]]"}


def neutralize(text: Any) -> str:
    """Recorded text as a string that cannot open or close a data block."""
    if text is None:
        return ""
    # A str subclass keeps its content: ``str()`` of a ``(str, Enum)`` member is
    # "Cls.MEMBER", while ``json.dumps`` writes its value, which is what the
    # model saw before this framing existed.
    s = str.__str__(text) if isinstance(text, str) else str(text)
    if DATA_OPEN not in s and DATA_CLOSE not in s:
        return s
    for marker, replacement in _SUBSTITUTES.items():
        s = s.replace(marker, replacement)
    return s


def neutralize_tree(value: Any) -> Any:
    """``neutralize`` applied to every string, dict key and leaf of a JSON-like value.

    Needed before ``json.dumps``: with the default ``ensure_ascii`` a marker
    inside a value is written as ``\\u27e6``, which a model reads back as the
    marker itself, so substituting after serialisation would miss it.
    """
    if isinstance(value, str):
        return neutralize(value)
    if isinstance(value, dict):
        return {
            neutralize(k) if isinstance(k, str) else k: neutralize_tree(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [neutralize_tree(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # Anything else is what ``json.dumps(default=str)`` would have written as
    # its ``str`` form, so write that form, neutralised.
    return neutralize(value)


def block_open(label: str) -> str:
    return f"{DATA_OPEN}{label}{DATA_CLOSE}"


def block_close(label: str) -> str:
    return f"{DATA_OPEN}/{label}{DATA_CLOSE}"


def data_block(label: str, body: str) -> str:
    """Wrap already-neutralised ``body`` between this label's markers."""
    return f"{block_open(label)}\n{body}\n{block_close(label)}"


def field(text: Any) -> str:
    """One recorded field, neutralised and enclosed in ⟦ ⟧."""
    return f"{DATA_OPEN}{neutralize(text)}{DATA_CLOSE}"


def close_open_field(text: str) -> str:
    """Close a field a length cut left open.

    Recorded text cannot contain either marker, so any surplus of openers is
    ours and is closed here; the block's closing marker then still ends it.
    """
    surplus = text.count(DATA_OPEN) - text.count(DATA_CLOSE)
    return text + DATA_CLOSE * surplus if surplus > 0 else text


def json_data_block(label: str, value: Any) -> str:
    """A JSON value serialised inside a data block, every string neutralised.

    ``default`` covers values ``json`` cannot encode (UUIDs, datetimes), whose
    ``str`` form is recorded data like any other.
    """
    body = json.dumps(neutralize_tree(value), default=lambda o: neutralize(str(o)))
    return data_block(label, body)
