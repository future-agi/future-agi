import json
import re
from typing import Any

from agentic_eval.core_evals.fi_utils.json import JsonHelper


def _recover_escaped_indentation(text: str) -> str:
    """Treat literal \\t and \\r between tokens as spaces, never as part of a token."""
    chars = list(text)
    quote = None
    escaped = False
    for i, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == "\\" and text[i + 1 : i + 2] in ("t", "r"):
            chars[i : i + 2] = "  "
    return "".join(chars)


def extract_dict_from_string(input_str: str) -> dict[str, Any]:
    """Extract the first JSON object from text and return it as a dictionary."""
    match = re.search(r"\{.*\}", input_str, re.DOTALL)
    if not match:
        raise ValueError(
            "Unable to generate a response at this time. Please check your input for accuracy."
        )

    json_str = (
        match.group(0).strip().replace("\n", "").replace("\r", "").replace("\t", "")
    )

    try:
        value = json.loads(json_str)
    except json.JSONDecodeError:
        recovered = _recover_escaped_indentation(json_str)
        for candidate in (recovered, recovered.replace("'", '"')):
            if candidate == json_str:
                continue  # Already tried above.
            try:
                value = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        else:
            value = JsonHelper.extract_json_from_text(recovered)

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
