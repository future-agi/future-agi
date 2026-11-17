"""Leaf policy for persisted MCP state keys; no file writes and no value-bearing errors."""
from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Mapping, Optional

from . import envfile

NAME = "AGENTCC_MCP_STATE_KEYS"
_NEW = re.compile(r"[0-9a-f]{64}")


class StateKeysRefused(ValueError):
    """Only the variable name, never its value, may reach a diagnostic."""


def _check_gap(text: str) -> None:
    """Do not mistake a skipped/malformed target for absence.

    The dotenv reader skips bare names. Unrelated bare names are valid Compose
    inputs, but the target must have an explicit persisted assignment.
    """
    for line in text.split("\n"):
        line = line.strip(envfile._UNICODE_SPACES)
        if not line or line.startswith("#"):
            continue
        bare = re.fullmatch(r"(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)", line)
        if bare is None or bare.group(1) == NAME:
            raise ValueError


def read_value(path: Path, environ: Mapping[str, str]) -> Optional[str]:
    if NAME in environ:
        raise StateKeysRefused(f"unset {NAME} in the shell; it overrides the persisted env file")
    try:
        text = envfile._read_text(path, path)
        text.encode("utf-8", "strict")
        if "\x00" in text:
            raise ValueError
        definitions = list(envfile._definitions(text))
        # Follow lexical spans, not physical lines: unrelated quoted values may
        # span lines or share a line with another definition. Validate skipped
        # text and unclosed quotes so a malformed target cannot look absent.
        cursor = 1 if text.startswith(envfile._BOM) else 0
        current = None
        seen = False
        for definition in definitions:
            _check_gap(text[cursor:definition.start])
            cursor = definition.token_end
            value = definition.value
            raw = text[definition.token:definition.token_end]
            if not definition.quote and value[:1] in ("'", '"'):
                raise ValueError
            if definition.key != NAME:
                continue
            end = text.find("\n", definition.start)
            end = len(text) if end < 0 else end
            if definition.token_end > end:
                raise ValueError
            if definition.quote:
                tail = text[definition.token_end:end].lstrip(" \t\v\f\r")
                if tail and not tail.startswith("#"):
                    raise ValueError
            if seen or not text.startswith(NAME + "=", definition.start):
                raise ValueError
            seen = True
            if ((definition.quote != "'" and "$" in value)
                    or (definition.quote == '"' and "\\" in raw)
                    or (definition.quote == "'" and ("\\\\" in value
                        or value.endswith("\\") or "\\'" in raw))
                    or "\r" in value or "\n" in value):
                raise ValueError
            current = value
        _check_gap(text[cursor:])
        if current:
            entries = [part.strip(envfile._UNICODE_SPACES) for part in current.split(",")]
            entries = [part for part in entries if part]
            if not entries or any(len(part.encode("utf-8")) < 32 for part in entries):
                raise ValueError
        return current
    except (ValueError, UnicodeError):
        raise StateKeysRefused(f"cannot safely read {NAME}; use a valid single-line literal key list") from None


def new_key() -> str:
    try:
        value = secrets.token_hex(32)
        if isinstance(value, str) and _NEW.fullmatch(value):
            return value
    except Exception:
        pass
    raise StateKeysRefused(f"could not generate {NAME}")
