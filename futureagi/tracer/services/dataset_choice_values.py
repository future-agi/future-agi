"""Bounded read-only decoding of evaluation *choice* cell storage.

TextField historically stored Python repr; newer/imported cells can contain
JSON. This is not a generic JSON flattener or an evaluator of Python code.
"""

import ast
import io
import json
import math
import tokenize

MAX_CHOICE_TEXT = 16_384
MAX_CHOICE_NODES = 1024
MAX_CHOICES = 256
MAX_CHOICE_DEPTH = 4

# Each source row contributes only an interpretation bit, never its reason or
# usage blob. GROUP BY value preserves the old distinct-value read bound. Both
# bits can be present when the same storage text means a literal in one cell
# and a container in another. JSONField's double-encoded history is supported.
CHOICE_INTERPRETATION_CTE = f"""
WITH
    if(lengthUTF8(ifNull(value_infos, '')) <= {MAX_CHOICE_TEXT},
       ifNull(value_infos, ''), '') AS bounded_choice_infos,
    if(toString(JSONType(bounded_choice_infos)) = 'String',
       JSONExtractString(bounded_choice_infos), bounded_choice_infos) AS choice_infos,
    JSONExtractRaw(choice_infos, 'data') AS choice_data,
    JSONExtractKeys(choice_infos) AS choice_info_keys,
    JSONExtractKeys(choice_data) AS choice_data_keys,
    if(toString(JSONType(choice_data)) = 'String', choice_data,
       if(JSONHas(choice_data, 'result') AND NOT JSONHas(choice_data, 'choice'),
          JSONExtractRaw(choice_data, 'result'),
          if(JSONHas(choice_data, 'choice') AND NOT JSONHas(choice_data, 'result'),
             JSONExtractRaw(choice_data, 'choice'), ''))) AS choice_result,
    (isValidJSON(choice_infos)
     AND length(choice_info_keys) = length(arrayDistinct(choice_info_keys))
     AND length(choice_data_keys) = length(arrayDistinct(choice_data_keys))
     AND JSONExtractString(choice_infos, 'output') = 'choices'
     AND toString(JSONType(choice_result)) = 'String'
     AND JSONExtractString(choice_result) = ifNull(value, '')) AS literal_choice
"""


class InvalidChoiceCell(ValueError):
    """The cell cannot provide an exact choice vocabulary."""


def _reject(*_args):
    raise InvalidChoiceCell("Invalid evaluation choice cell")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            _reject()
        result[key] = value
    return result


def _literal(node, depth=0, budget=None):
    """Convert only literal nodes, with no eval/exec or callable/name lookup."""
    if budget is None:
        budget = [MAX_CHOICE_NODES]
    budget[0] -= 1
    if depth > MAX_CHOICE_DEPTH or budget[0] < 0:
        _reject()
    if isinstance(node, ast.Constant) and type(node.value) in (str, int, float):
        return node.value
    if isinstance(node, ast.List):
        return [_literal(item, depth + 1, budget) for item in node.elts]
    if isinstance(node, ast.Dict):
        return _object(
            (_literal(key, depth + 1, budget), _literal(value, depth + 1, budget))
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal(node.operand, depth + 1, budget)
        if type(value) in (int, float):
            return -value
    _reject()


def _historical(text):
    # Reject expressions, comments and implicit adjacent-string concatenation
    # before AST conversion. Only the tokens emitted by repr of these cells
    # are needed; neither Python execution nor permissive string repair is used.
    previous = None
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type in (tokenize.NL, tokenize.NEWLINE, tokenize.ENDMARKER):
            continue
        if token.type == tokenize.OP and token.string in "[]{}:,-":
            previous = token.type
            continue
        if token.type not in (tokenize.STRING, tokenize.NUMBER):
            _reject()
        if token.type == previous == tokenize.STRING:
            _reject()
        previous = token.type
    return _literal(ast.parse(text, mode="eval").body)


def evaluation_choice_labels(serialized, *, literal=False):
    """Return exact string labels, or reject instead of suggesting a raw blob.

    Call only for authorized evaluation-origin array columns. Generic dataset
    JSON, numeric/boolean evaluations, reasons and tags retain their readers.
    """
    if not isinstance(serialized, str) or len(serialized) > MAX_CHOICE_TEXT:
        _reject()
    # Raw scalar/literal paths must be safe for the cursor's UTF-8 digest too.
    try:
        serialized.encode("utf-8")
    except UnicodeEncodeError:
        _reject()
    text = serialized.strip()
    if not text:
        return []
    # A literal choice can itself look like a container. Only same-cell,
    # structured producer evidence exactly matching the raw value resolves it.
    if literal:
        return [serialized]
    if text[0] not in "[{":
        # A scalar containing quote characters is still the literal choice.
        return [serialized]
    try:
        try:
            parsed = json.loads(text, parse_constant=_reject, object_pairs_hook=_object)
        except json.JSONDecodeError:
            parsed = _historical(text)
    except (
        SyntaxError,
        ValueError,
        RecursionError,
        OverflowError,
        tokenize.TokenError,
    ):
        _reject()

    if isinstance(parsed, dict):
        keys = set(parsed)
        choice_keys = keys & {"choice", "choices"}
        if len(choice_keys) != 1 or keys - {"choice", "choices", "score"}:
            _reject()
        if "score" in parsed:
            score = parsed["score"]
            if type(score) not in (int, float):
                _reject()
            try:
                if not math.isfinite(score):
                    _reject()
            except OverflowError:
                _reject()
        parsed = parsed[next(iter(choice_keys))]
        if "choice" in choice_keys and not isinstance(parsed, str):
            _reject()
        if isinstance(parsed, str):
            parsed = [parsed]
    if not isinstance(parsed, list) or len(parsed) > MAX_CHOICES:
        _reject()
    if any(not isinstance(item, str) or not item.strip() for item in parsed):
        _reject()
    # JSON combines valid surrogate pairs; historical lone surrogate escapes
    # are not Unicode scalar labels and must fail before cursor serialization.
    try:
        for item in parsed:
            item.encode("utf-8")
    except UnicodeEncodeError:
        _reject()
    return parsed
