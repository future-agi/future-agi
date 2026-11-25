import json
import re
from typing import Any

from agentic_eval.core_evals.fi_utils.json import JsonHelper

# A quoted fragment in prose isn't a key unless a colon follows it.
_OBJECT_START = re.compile(
    r"""\{(?:\s|\\[tr])*(?:```|\}|(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')(?:\s|\\[tr])*:)"""
)
# Do not interpret these markers inside strings: match at decoded object boundaries.
_EXAMPLE_LABEL = re.compile(
    r"\b(?:example|sample)(?:\s+(?:only|response|answer))?\s*:\s*(?:`{1,3}(?:json)?\s*)?\Z",
    re.IGNORECASE,
)
_ANSWER_SEPARATOR = re.compile(
    r"\s*`{0,3}\s*[.;]?\s*(?:my\s+)?(?:actual(?:\s+answer)?|final(?:\s+answer)?)"
    r"(?:\s+is)?\s*:\s*(?:`{1,3}(?:json)?\s*)?(?=\{)",
    re.IGNORECASE,
)


def _skip_placeholder(text: str, start: int, decoder: json.JSONDecoder) -> int:
    """Skip a prose/template block, including any JSON examples nested in it."""
    depth = 1
    i = start + 1
    while i < len(text):
        if text[i] == "{":
            # Skip a whole example: braces in its string values are not delimiters.
            try:
                _, i = decoder.raw_decode(text, i)
                continue
            except json.JSONDecodeError:
                depth += 1
        elif text[i] == '"':
            # A quoted rubric value may itself describe a delimiter. Require
            # a field delimiter after it: a stray prose quote is not a string.
            try:
                _, after = decoder.raw_decode(text, i)
            except json.JSONDecodeError:
                pass
            else:
                if re.match(r"\s*[,}\]]", text[after:]):
                    i = after
                    continue
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _recover_object(text: str, start: int) -> tuple[str, int]:
    """Isolate one brace-delimited candidate and normalize only unquoted indentation."""
    depth = 0
    quote = None
    escaped = False
    parts: list[str] = []
    mark = start
    end = len(text)
    for i in range(start, end):
        char = text[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        elif char.isspace():
            parts.extend((text[mark:i], " "))
            mark = i + 1
        elif char == "\\" and text[i + 1 : i + 2] in ("t", "r"):
            parts.extend((text[mark:i], " "))
            mark = i + 2
    parts.append(text[mark:end])
    return "".join(parts), end


def _drop_escaped_indentation(text: str) -> str:
    """Treat a literal ``\\t`` or ``\\r`` between JSON tokens as whitespace.

    Some models print indentation as escape sequences. Inside a double-quoted
    string they are real escapes and must reach the decoder unchanged.
    Replace them with a space, not nothing: ``1\\t2`` is not the number 12.
    """
    parts: list[str] = []
    mark = 0
    in_string = False
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            if not in_string and text[i + 1 : i + 2] in ("t", "r"):
                parts.extend((text[mark:i], " "))
                mark = i + 2
            i += 2
            continue
        if char == '"':
            in_string = not in_string
        i += 1
    parts.append(text[mark:])
    return "".join(parts)


def _balanced_spans(text: str) -> list[str]:
    """Top-level bracket spans in the order ``JsonExtractor`` tries them."""
    spans: list[str] = []
    i = 0
    while i < len(text):
        if text[i] not in "{[":
            i += 1
            continue
        start = i
        depth = 1
        i += 1
        while i < len(text) and depth:
            if text[i] in "{[":
                depth += 1
            elif text[i] in "}]":
                depth -= 1
            i += 1
        if depth:
            break
        spans.append(text[start:i])
    return spans


def _loose_form(text: str) -> str:
    """The text as ``JsonHelper``'s last-resort extraction reads it."""
    text = " ".join(text.replace("```json", "").replace("```", "").split())
    return text.strip()


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_from_fragments(text: str) -> Any:
    """Pick the object ``JsonHelper.extract_json_from_text`` would pick, keeping escapes.

    That helper rewrites every backslash before decoding, so a valid ``\\t``
    in a string comes back as the two characters. Let it choose the object,
    from the reply read as before, then decode the same span unchanged.
    """
    exact = _loose_form(_drop_escaped_indentation(text))
    # The earlier reading deleted these sequences everywhere, strings included.
    legacy = _loose_form(text.replace("\\r", "").replace("\\t", ""))
    # Neither reading adds or removes a bracket, so the spans line up. When
    # no span loads, the earlier reading decoded the whole text.
    pairs = list(zip(_balanced_spans(exact), _balanced_spans(legacy), strict=True))
    for exact_part, legacy_part in [*pairs, (exact, legacy)]:
        if _loads(_relaxed(legacy_part)) is None:
            continue  # skipped before too
        value = _loads(exact_part)
        if value is None:
            value = _loads(_relaxed(exact_part))
        if value is not None:
            return value
        break  # e.g. "1\\t2", which used to be read as the number 12
    raise ValueError("Failed to load JSON from text")


def _relaxed(text: str) -> str:
    """``JsonHelper``'s backslash repair, for LaTeX and other invalid escapes."""
    text = re.sub(r'\\([\\/"bfnrtu])', r"\\\\\\1", text)
    return re.sub(r"\\(?![\\])", r"\\\\", text)


def _earlier_choice(input_str: str) -> Any:
    """Read the reply as the parser did before, except keep string escapes."""
    match = re.search(r"\{.*\}", input_str, re.DOTALL)
    if not match:
        raise ValueError("Failed to load JSON from text")
    raw = match.group(0).strip().replace("\n", "").replace("\r", "").replace("\t", "")
    json_str = _drop_escaped_indentation(raw)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            return json.loads(json_str.replace("'", '"'))
        except json.JSONDecodeError:
            return _extract_from_fragments(raw)


def _answer_by_structure(input_str: str) -> tuple[Any, str]:
    """Find the answer object by structure; also return the text that leads to it."""
    first = input_str.find("{")
    last = input_str.rfind("}")
    if first == -1 or last < first:
        raise ValueError(
            "Unable to generate a response at this time. Please check your input for accuracy."
        )

    json_str = (
        input_str[: last + 1].replace("\n", "").replace("\r", "").replace("\t", "")
    )

    decoder = json.JSONDecoder()
    fallback = None
    end = 0
    while (start := json_str.find("{", end)) != -1:
        lead = json_str[end:start]
        if not _OBJECT_START.match(json_str, start):
            end = _skip_placeholder(json_str, start, decoder)
            continue
        try:
            # Trailing commentary (even another object) cannot invalidate a good reply.
            value, end = decoder.raw_decode(json_str, start)
        except json.JSONDecodeError:
            recovered, end = _recover_object(json_str, start)
            fields = recovered[1:-1].strip()
            if fields.startswith("```") and fields.endswith("```"):
                recovered = "{" + strip_code_fence(fields) + "}"
            try:
                value = json.loads(recovered)
            except json.JSONDecodeError:
                # Keep a single-quoted mapping as a last resort; a later JSON
                # answer takes precedence over a Python-style rubric or example.
                try:
                    repaired = json.loads(recovered.replace("'", '"'))
                except json.JSONDecodeError:
                    # An apostrophe in prose may have hidden the closing brace
                    # from the string-aware scan. Don't repair the next answer.
                    end = _skip_placeholder(json_str, start, decoder)
                    try:
                        repaired = JsonHelper.extract_json_from_text(
                            json_str[start:end]
                        )
                    except ValueError:
                        continue
                if fallback is None and isinstance(repaired, dict):
                    fallback = repaired
                continue
        if not isinstance(value, dict):
            raise ValueError("Extracted string is not valid Response.")
        if _ANSWER_SEPARATOR.match(json_str, end) and _EXAMPLE_LABEL.search(
            json_str, 0, start
        ):
            # An explicitly labelled answer follows an explicitly labelled example.
            # Don't confuse unlabelled objects or trailing evidence with that pair.
            continue
        return value, lead
    if fallback is not None:
        return fallback, ""
    raise ValueError("Failed to load JSON from text")


# Text introducing an object as an illustration rather than the answer.
_EXAMPLE_WORD = re.compile(r"\b(?:example|sample|template|e\.g\.)", re.IGNORECASE)


def extract_dict_from_string(input_str: str) -> dict[str, Any]:
    """Extract the first JSON object from text and return it as a dictionary."""
    if input_str.find("{") == -1 or input_str.rfind("}") < input_str.find("{"):
        raise ValueError(
            "Unable to generate a response at this time. Please check your input for accuracy."
        )
    error: ValueError | None = None
    try:
        value, lead = _answer_by_structure(input_str)
    except ValueError as e:
        value, lead, error = None, "", e
    # The structural reading can see past a closing brace quoted in a value,
    # which the earlier reading cannot. Where the two disagree, keep the
    # earlier choice when the structural answer is empty, missing, or is
    # introduced as an example.
    try:
        earlier = _earlier_choice(input_str)
    except ValueError:
        earlier = None
    if earlier and earlier != value and (not value or _EXAMPLE_WORD.search(lead)):
        value = earlier
    if value is None:
        raise error or ValueError("Failed to load JSON from text")
    if not isinstance(value, dict):
        raise ValueError("Extracted string is not valid Response.")
    return value


_CODE_FENCE_RE = re.compile(r"^```[ \t]*[a-zA-Z0-9_-]*[ \t]*\n?(.*?)\n?```$", re.DOTALL)


def strip_code_fence(text: str) -> str:
    """Strip a Markdown code fence some models wrap their response in.

    Returns the inner content (stripped). Handles every fence variant the
    callers see:
      - multi-line ```` ```json\\n...\\n``` ```` and bare ```` ```\\n...\\n``` ````
      - single-line ```` ```json{...}``` ```` (a naive line-split returns "" here,
        dropping the whole payload)
      - a truncated opening fence with no close
    Input without a fence is returned unchanged. Parsing the result (json.loads,
    extract_dict_from_string, etc.) stays the caller's job — this only unwraps.
    """
    if not text:
        return ""
    stripped = text.strip()
    match = _CODE_FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip()
    if stripped.startswith("```"):
        # Opening fence with no close (truncated) — drop the fence + lang tag.
        return re.sub(r"^```[ \t]*[a-zA-Z0-9_-]*[ \t]*\n?", "", stripped).strip()
    return stripped
