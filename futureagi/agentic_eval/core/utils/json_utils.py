import json
import re
from typing import Any

from agentic_eval.core_evals.fi_utils.json import JsonHelper


def extract_dict_from_string(input_str: str) -> dict[str, Any]:
    """Extract the first JSON object from text and return it as a dictionary."""
    match = re.search(r"\{.*\}", input_str, re.DOTALL)
    if not match:
        raise ValueError(
            "Unable to generate a response at this time. Please check your input for accuracy."
        )

    json_str = (
        match.group(0)
        .strip()
        .replace("\n", "")
        .replace("\r", "")
        .replace("\t", "")
        .replace("\\r", "")
        .replace("\\t", "")
    )

    try:
        value = json.loads(json_str)
    except json.JSONDecodeError:
        try:
            value = json.loads(json_str.replace("'", '"'))
        except json.JSONDecodeError:
            value = JsonHelper.extract_json_from_text(json_str)

    if not isinstance(value, dict):
        raise ValueError("Extracted string is not valid Response.")
    return value


def extract_eval_json(content: str) -> dict[str, Any] | None:
    """Extract an evaluation JSON object containing a ``"result"`` key.

    Tries, in order: direct extraction, markdown-fenced JSON, inline
    ``{...\"result\"...}`` matches, and the last JSON object anywhere in
    the content. Returns ``None`` if no candidate has a ``"result"`` key.
    """
    if not isinstance(content, str) or not content:
        return None

    try:
        parsed = extract_dict_from_string(content)
        if isinstance(parsed, dict) and "result" in parsed:
            return parsed
    except (ValueError, KeyError):
        pass

    # Try to find JSON block in markdown
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if json_match:
        try:
            candidate = json.loads(json_match.group(1))
            if isinstance(candidate, dict) and "result" in candidate:
                return candidate
        except json.JSONDecodeError:
            pass

    # Try to find raw JSON with "result" key (allows nested braces)
    for m in re.finditer(r"\{[^{}]*\"result\"[^{}]*\}", content):
        try:
            candidate = json.loads(m.group(0))
            if isinstance(candidate, dict) and "result" in candidate:
                return candidate
        except json.JSONDecodeError:
            continue

    # Last resort: scan backwards for the last JSON object (most likely the eval result)
    last_json = None
    for m in re.finditer(r"\{[^{}]+\}", content):
        try:
            candidate = json.loads(m.group(0))
            if isinstance(candidate, dict):
                last_json = candidate
        except json.JSONDecodeError:
            continue
    if last_json and "result" in last_json:
        return last_json

    return None
