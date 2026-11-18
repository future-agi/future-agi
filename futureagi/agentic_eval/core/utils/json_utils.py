import json
import re
from typing import Any

from agentic_eval.core_evals.fi_utils.json import JsonHelper

# Skip prose placeholders: a supported object begins with a quoted key or is empty.
_OBJECT_START = re.compile(r"""\{(?=(?:\s|\\[tr])*["'}])""")


def _recover_object(text: str, start: int) -> tuple[str, int]:
    """Isolate one brace-delimited candidate and normalize only unquoted indentation."""
    depth = 0
    quote = None
    escaped = False
    parts = []
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
    end = 0
    while match := _OBJECT_START.search(json_str, end):
        start = match.start()
        try:
            # Trailing commentary (even another object) cannot invalidate a good reply.
            value, _ = decoder.raw_decode(json_str, start)
        except json.JSONDecodeError:
            recovered, end = _recover_object(json_str, start)
            for candidate in (recovered, recovered.replace("'", '"')):
                try:
                    value = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue
            else:
                try:
                    value = JsonHelper.extract_json_from_text(recovered)
                except ValueError:
                    continue  # Try the next object, not the intervening prose.
        if not isinstance(value, dict):
            raise ValueError("Extracted string is not valid Response.")
        return value
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
