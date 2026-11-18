import json
import re
from typing import Any

from agentic_eval.core_evals.fi_utils.json import JsonHelper

# A quoted fragment in prose isn't a key unless a colon follows it.
_OBJECT_START = re.compile(
    r"""\{(?:\s|\\[tr])*(?:\}|(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')(?:\s|\\[tr])*:)"""
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
        elif char == "\\" and text[i + 1 : i + 2] in ("t", "r"):
            parts.extend((text[mark:i], " "))
            mark = i + 2
    parts.append(text[mark:end])
    return "".join(parts), end


def extract_dict_from_string(input_str: str) -> dict[str, Any]:
    """Extract the first JSON object from text and return it as a dictionary."""
    first = input_str.find("{")
    last = input_str.rfind("}")
    if first == -1 or last < first:
        raise ValueError(
            "Unable to generate a response at this time. Please check your input for accuracy."
        )

    json_str = (
        input_str[first : last + 1]
        .replace("\n", "")
        .replace("\r", "")
        .replace("\t", "")
    )

    decoder = json.JSONDecoder()
    fallback = None
    end = 0
    while (start := json_str.find("{", end)) != -1:
        if not _OBJECT_START.match(json_str, start):
            end = _skip_placeholder(json_str, start, decoder)
            continue
        try:
            # Trailing commentary (even another object) cannot invalidate a good reply.
            value, _ = decoder.raw_decode(json_str, start)
        except json.JSONDecodeError:
            recovered, end = _recover_object(json_str, start)
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
        return value
    if fallback is not None:
        return fallback
    raise ValueError("Failed to load JSON from text")


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
