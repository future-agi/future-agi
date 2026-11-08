"""Portable skill checks. No database access, file execution or network requests."""

from __future__ import annotations

import math
import posixpath
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from typing import cast
from urllib.parse import unquote, urlsplit

import yaml

from model_hub.services.skills_hub.limits import (
    MAX_DESCRIPTION,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_FRONTMATTER_BYTES,
    MAX_NAME,
    MAX_PATH,
    MAX_PATH_SEGMENTS,
    MAX_VERSION_BYTES,
    NAME_RE,
)
from model_hub.services.skills_hub.types import FileInput

_PORTABLE_KEYS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}
_HOST_KEYS = {
    "disable-model-invocation",
    "user-invocable",
    "context",
    "agent",
    "hooks",
    "paths",
    "shell",
    "when_to_use",
    "model",
    "argument-hint",
}
_TRIGGER = re.compile(r"use (this|it)? ?(when|for|to)|when (the )?user|whenever", re.I)
_EXECUTABLE_EXTENSIONS = {".sh", ".py", ".js", ".ts", ".rb", ".ps1"}
_SECRET_PATTERNS = (
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "private key",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?"
            r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----"
        ),
    ),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b")),
    ("provider API key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{16,}\b")),
    ("Google API key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("Stripe key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{16,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
)
_GENERIC_SECRET = re.compile(
    r"\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?"
    r"(?P<value>[A-Za-z0-9/+_\-]{16,})",
    re.I,
)
_MARKDOWN_LINK = re.compile(r"!?\[[^\[\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))")
_REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|(\S+))", re.M)
_CODE_PATH = re.compile(r"(?<!`)`((?:scripts|references|assets)/[^`\n]+)`(?!`)")


def estimate_tokens(text: str) -> int:
    """Estimate tokens from Unicode characters, not encoded bytes."""
    return (len(text) + 3) // 4


def _secret_matches(text: str) -> list[tuple[int, int, str]]:
    matches = [
        (match.start(), match.end(), kind)
        for kind, pattern in _SECRET_PATTERNS
        for match in pattern.finditer(text)
    ]
    for match in _GENERIC_SECRET.finditer(text):
        value = match.group("value")
        counts = Counter(value)
        entropy = -sum(
            (count / len(value)) * math.log2(count / len(value))
            for count in counts.values()
        )
        if entropy >= 3.5:
            matches.append((match.start("value"), match.end("value"), "credential"))
    # A provider key assigned to api_key is one finding, not two.
    merged: list[tuple[int, int, str]] = []
    for start, end, kind in sorted(matches, key=lambda item: (item[0], -item[1])):
        if merged and start < merged[-1][1]:
            old_start, old_end, old_kind = merged[-1]
            merged[-1] = (old_start, max(old_end, end), old_kind)
        else:
            merged.append((start, end, kind))
    return merged


def _redact(text: str, secrets: set[str] | None = None) -> str:
    for start, end, _ in reversed(_secret_matches(text)):
        text = text[:start] + f"[redacted, {end - start} chars]" + text[end:]
    # YAML removes the assignment syntax from a scalar. Retain findings from
    # the source so generic credentials cannot leak through parsed metadata.
    for secret in sorted(secrets or (), key=len, reverse=True):
        text = text.replace(secret, f"[redacted, {len(secret)} chars]")
    return text


def _item(code: str, severity: str, message: str, **location: object) -> dict:
    return {"code": code, "severity": severity, "message": message, **location}


def path_is_valid(path: str) -> bool:
    """Validate without normalizing away evidence of traversal or duplicate paths."""
    if not isinstance(path, str) or not path or len(path) > MAX_PATH:
        return False
    if path.startswith("/") or "\\" in path or re.match(r"^[A-Za-z]:", path):
        return False
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in path):
        return False
    parts = path.split("/")
    return len(parts) <= MAX_PATH_SEGMENTS and all(
        part not in {"", ".", ".."} and not part.endswith((" ", ".")) for part in parts
    )


def path_directory_collisions(paths: list[str]) -> set[str]:
    """Find files with a file ancestor, including case-insensitive collisions."""
    folded = {path.casefold() for path in paths}
    return {
        path
        for path in paths
        if any(
            "/".join(path.casefold().split("/")[:index]) in folded
            for index in range(1, len(path.split("/")))
        )
    }


def text_is_valid(text: str) -> bool:
    """Only UTF-8 text with printable characters and ordinary whitespace is allowed."""
    return isinstance(text, str) and not any(
        unicodedata.category(char) == "Cs"
        or (unicodedata.category(char) == "Cc" and char not in "\t\n\r")
        for char in text
    )


def _json_frontmatter(value: object, ancestors: set[int], budget: list[int]) -> object:
    """Bound YAML alias expansion and refuse objects a JSON response cannot represent."""
    budget[0] -= 1
    if budget[0] < 0 or len(ancestors) > 64:
        raise ValueError("Frontmatter is too deeply nested.")
    if isinstance(value, str):
        if "\x00" in value or any(unicodedata.category(char) == "Cs" for char in value):
            raise ValueError("Frontmatter text must contain valid Unicode without NUL.")
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, (dict, list)) and id(value) not in ancestors:
        parents = ancestors | {id(value)}
        if isinstance(value, list):
            return [_json_frontmatter(item, parents, budget) for item in value]
        if not all(
            key is None
            or isinstance(key, (str, bool, int))
            or (isinstance(key, float) and math.isfinite(key))
            for key in value
        ):
            raise ValueError("Frontmatter keys must be JSON-compatible scalars.")
        return {
            _json_frontmatter(key, parents, budget): _json_frontmatter(
                item, parents, budget
            )
            for key, item in value.items()
        }
    raise ValueError("Frontmatter must contain finite JSON-compatible values.")


def _check_yaml_tree(node: yaml.Node, ancestors: set[int], budget: list[int]) -> None:
    """Bound aliases before safe_load can expand YAML merge keys exponentially."""
    budget[0] -= 1
    if budget[0] < 0 or len(ancestors) > 64 or id(node) in ancestors:
        raise ValueError("Frontmatter aliases or nesting exceed the limit.")
    parents = ancestors | {id(node)}
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            _check_yaml_tree(key, parents, budget)
            _check_yaml_tree(value, parents, budget)
    elif isinstance(node, yaml.SequenceNode):
        for child in node.value:
            _check_yaml_tree(child, parents, budget)


def parse_frontmatter(skill_md: str) -> tuple[dict | None, str, list[dict]]:
    """Read a size-bounded YAML mapping delimited by root-level --- lines."""
    lines = skill_md.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return (
            None,
            skill_md,
            [
                _item(
                    "frontmatter_missing",
                    "error",
                    "Start SKILL.md with YAML frontmatter.",
                    path="SKILL.md",
                    line=1,
                )
            ],
        )
    end = next(
        (
            index
            for index in range(1, len(lines))
            if lines[index].rstrip("\r\n") == "---"
        ),
        None,
    )
    if end is None:
        return (
            None,
            skill_md,
            [
                _item(
                    "frontmatter_invalid",
                    "error",
                    "Close the YAML frontmatter with a --- line.",
                    path="SKILL.md",
                    line=1,
                )
            ],
        )
    block, body = "".join(lines[1:end]), "".join(lines[end + 1 :])
    try:
        if len(block.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
            raise ValueError("Frontmatter exceeds the size limit.")
        node = yaml.compose(block, Loader=yaml.SafeLoader)
        if node is not None:
            _check_yaml_tree(node, set(), [MAX_FRONTMATTER_BYTES])
        parsed = yaml.safe_load(block)
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) for key in parsed
        ):
            raise ValueError("Frontmatter must be a mapping with text keys.")
        parsed = cast(dict, _json_frontmatter(parsed, set(), [MAX_FRONTMATTER_BYTES]))
    except (yaml.YAMLError, UnicodeError, ValueError, RecursionError):
        # Parser diagnostics can contain source text, including credentials.
        return (
            None,
            body,
            [
                _item(
                    "frontmatter_invalid",
                    "error",
                    "Use a valid YAML mapping of at most 16 KiB with text keys and simple values.",
                    path="SKILL.md",
                    line=1,
                )
            ],
        )
    return parsed, body, []


def _frontmatter_checks(frontmatter: dict, expected_name: str | None) -> list[dict]:
    items: list[dict] = []

    def add(code: str, severity: str, message: str) -> None:
        items.append(_item(code, severity, message, path="SKILL.md"))

    name = frontmatter.get("name")
    if not name:
        add("name_missing", "error", "Add a name to the frontmatter.")
    elif (
        not isinstance(name, str)
        or len(name) > MAX_NAME
        or not re.fullmatch(NAME_RE, name)
    ):
        add(
            "name_invalid",
            "error",
            "Use 1–64 lowercase letters or digits, separated by single hyphens, for the name.",
        )
    elif expected_name is not None and name != expected_name:
        add(
            "name_mismatch",
            "error",
            "The frontmatter name must match this skill's name.",
        )
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        add("description_missing", "error", "Add a description to the frontmatter.")
    else:
        if len(description) > MAX_DESCRIPTION:
            add(
                "description_too_long",
                "error",
                "Keep the description to 1,024 characters or fewer.",
            )
        if len(description) < 40:
            add(
                "description_short",
                "warning",
                "Use at least 40 characters to explain what this skill does.",
            )
        if not _TRIGGER.search(description):
            add(
                "description_trigger_hint",
                "info",
                "Explain when an agent should use this skill in the description.",
            )
    compatibility = frontmatter.get("compatibility")
    if compatibility is not None and len(str(compatibility)) > 500:
        add(
            "compatibility_too_long",
            "error",
            "Keep compatibility notes to 500 characters or fewer.",
        )
    if "metadata" in frontmatter:
        metadata = frontmatter["metadata"]
        if not isinstance(metadata, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in metadata.items()
        ):
            add(
                "metadata_invalid",
                "error",
                "Metadata must map text keys to text values.",
            )
    for key in sorted(frontmatter):
        if key in _HOST_KEYS:
            add(
                "host_extension",
                "info",
                "This frontmatter option is specific to Claude Code; other hosts may ignore it.",
            )
        elif key not in _PORTABLE_KEYS:
            add(
                "frontmatter_unknown_key",
                "warning",
                "This frontmatter option is not part of the portable skill format.",
            )
    if "allowed-tools" in frontmatter:
        add(
            "allowed_tools_note",
            "info",
            "`allowed-tools` pre-approves tools in some agents; it doesn't sandbox the skill.",
        )
    return items


def _link_checks(path: str, content: str, paths: set[str]) -> list[dict]:
    items: list[dict] = []
    matches = (
        [
            (match.start(), match.group(1) or match.group(2))
            for pattern in (_MARKDOWN_LINK, _REFERENCE_LINK)
            for match in pattern.finditer(content)
        ]
        if posixpath.splitext(path)[1].lower() in {".md", ".markdown"}
        else []
    )
    matches.extend(
        (match.start(), match.group(1)) for match in _CODE_PATH.finditer(content)
    )
    seen: set[tuple[int, str]] = set()
    for offset, target in matches:
        try:
            url = urlsplit(target)
        except ValueError:
            continue
        if url.scheme or url.netloc or not url.path or target.startswith("/"):
            continue
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(path), unquote(url.path))
        )
        line = content.count("\n", 0, offset) + 1
        if resolved not in paths and (line, resolved) not in seen:
            items.append(
                _item(
                    "link_broken",
                    "warning",
                    "This relative file reference is not in the skill.",
                    path=path,
                    line=line,
                )
            )
            seen.add((line, resolved))
    return items


def _frontmatter_secrets(
    value: object, *, key: str | None = None
) -> Iterator[tuple[str, str]]:
    """Rescan decoded scalars with their mapping key, not YAML source syntax.

    Escapes, quoted keys, block scalars and aliases can hide assignments from the
    raw-text scan. Keep the same provider patterns/entropy threshold and collect
    values globally so copies elsewhere in the diagnostic DTO are redacted too.
    """
    if isinstance(value, str):
        text = f"{key}: {value}" if key is not None else value
        for start, end, kind in _secret_matches(text):
            yield text[start:end], kind
    elif isinstance(value, list):
        for item in value:
            yield from _frontmatter_secrets(item, key=key)
    elif isinstance(value, dict):
        for name, item in value.items():
            yield from _frontmatter_secrets(name)
            yield from _frontmatter_secrets(item, key=str(name))


def _redacted_frontmatter(value: object, secrets: set[str]) -> object:
    if isinstance(value, str):
        return _redact(value, secrets)
    if isinstance(value, list):
        return [_redacted_frontmatter(item, secrets) for item in value]
    if isinstance(value, dict):
        return {
            _redact(str(key), secrets): _redacted_frontmatter(item, secrets)
            for key, item in value.items()
        }
    return value


def run_checks(files: list[FileInput], *, expected_name: str | None = None) -> dict:
    """Check portable metadata, paths, text, budgets, references and credentials."""
    items: list[dict] = []
    texts: dict[str, str] = {}
    seen: set[str] = set()
    secrets: set[str] = set()
    total_bytes = total_tokens = 0
    if len(files) > MAX_FILES:
        items.append(
            _item("too_many_files", "error", "Keep each skill to 200 files or fewer.")
        )
    collisions = path_directory_collisions([file["path"] for file in files])
    for file in files:
        path, content = file["path"], file["content"]
        if path in collisions:
            items.append(
                _item(
                    "path_invalid",
                    "error",
                    "A file path cannot also be a directory, even with different letter case.",
                    path=path,
                )
            )
        if not path_is_valid(path):
            items.append(
                _item(
                    "path_invalid",
                    "error",
                    "Use a relative POSIX path with no empty, dot, parent or trailing-space segments; allow at most 255 characters and eight segments.",
                    path=path,
                )
            )
        folded = path.casefold()
        if folded in seen:
            items.append(
                _item(
                    "path_duplicate",
                    "error",
                    "Another file has the same path when letter case is ignored.",
                    path=path,
                )
            )
        seen.add(folded)
        if not text_is_valid(content):
            items.append(
                _item(
                    "file_not_text",
                    "error",
                    "Use UTF-8 text without NUL or other control characters except tabs and line breaks.",
                    path=path,
                )
            )
            continue
        size = len(content.encode("utf-8"))
        total_bytes += size
        total_tokens += estimate_tokens(content)
        if size > MAX_FILE_BYTES:
            items.append(
                _item(
                    "file_too_large",
                    "error",
                    "Keep each file to 512 KiB or fewer of UTF-8 text.",
                    path=path,
                )
            )
        texts.setdefault(path, content)
        if not content.strip():
            items.append(
                _item("file_empty", "warning", "This file is empty.", path=path)
            )
        for start, end, kind in _secret_matches(content):
            secrets.add(content[start:end])
            items.append(
                _item(
                    "secret_suspected",
                    "warning",
                    f"A possible {kind} was found. Remove it and use an environment variable instead.",
                    path=path,
                    line=content.count("\n", 0, start) + 1,
                    detail={"redacted": f"[redacted, {end - start} chars]"},
                )
            )
        if (
            path.startswith("scripts/")
            or posixpath.splitext(path)[1].lower() in _EXECUTABLE_EXTENSIONS
        ):
            items.append(
                _item(
                    "executable_content",
                    "info",
                    "Agents may run these. Review them before promoting.",
                    path=path,
                )
            )
    if total_bytes > MAX_VERSION_BYTES:
        items.append(
            _item(
                "bundle_too_large",
                "error",
                "Keep the skill's total UTF-8 content to 2 MiB or fewer.",
            )
        )
    frontmatter = None
    if "SKILL.md" not in {file["path"] for file in files}:
        items.append(
            _item(
                "root_missing", "error", "Add a SKILL.md file at the root of the skill."
            )
        )
    elif "SKILL.md" in texts:
        frontmatter, _, parsed_items = parse_frontmatter(texts["SKILL.md"])
        items.extend(parsed_items)
        if frontmatter is not None:
            items.extend(_frontmatter_checks(frontmatter, expected_name))
            for secret, kind in _frontmatter_secrets(frontmatter):
                if secret not in secrets:
                    secrets.add(secret)
                    items.append(
                        _item(
                            "secret_suspected",
                            "warning",
                            f"A possible {kind} was found. Remove it and use an environment variable instead.",
                            path="SKILL.md",
                            detail={"redacted": f"[redacted, {len(secret)} chars]"},
                        )
                    )
        if len(texts["SKILL.md"].splitlines()) > 500:
            items.append(
                _item(
                    "skill_md_long",
                    "warning",
                    "SKILL.md is over 500 lines. Move supporting detail into other files.",
                    path="SKILL.md",
                )
            )
        if estimate_tokens(texts["SKILL.md"]) > 5000:
            items.append(
                _item(
                    "skill_md_tokens",
                    "warning",
                    "SKILL.md is over approximately 5,000 tokens. Move supporting detail into other files.",
                    path="SKILL.md",
                )
            )
    for path, content in texts.items():
        items.extend(_link_checks(path, content, set(texts)))
    # Never echo credentials via diagnostics, including metadata and filenames.
    for item in items:
        if "path" in item:
            item["path"] = _redact(item["path"], secrets)
    metadata_tokens = sum(
        estimate_tokens(frontmatter[key])
        for key in ("name", "description")
        if frontmatter and isinstance(frontmatter.get(key), str)
    )
    return {
        "items": items,
        "summary": {
            key: sum(item["severity"] == severity for item in items)
            for key, severity in (
                ("errors", "error"),
                ("warnings", "warning"),
                ("infos", "info"),
            )
        },
        "tokens": {
            "metadata": metadata_tokens,
            "skill_md": estimate_tokens(texts.get("SKILL.md", "")),
            "total": total_tokens,
        },
        "frontmatter": _redacted_frontmatter(frontmatter, secrets),
    }
