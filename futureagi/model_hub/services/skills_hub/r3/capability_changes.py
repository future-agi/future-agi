"""Deterministic execution-related deltas; static inspection is not a sandbox."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import yaml

from model_hub.services.skills_hub.checks import parse_frontmatter
from model_hub.services.skills_hub.packages import file_sha256, manifest_digest
from model_hub.services.skills_hub.types import FileInput
from model_hub.services.skills_hub.v12.credentials import canonical_digest

CONTROL_KEYS = {
    "allowed-tools",
    "hooks",
    "shell",
    "context",
    "agent",
    "model",
    "paths",
    "disable-model-invocation",
    "user-invocable",
    "when_to_use",
}
EXECUTABLE_SUFFIXES = (".sh", ".py", ".js", ".ts", ".rb", ".ps1")
URL = re.compile(r"https?://[^\s<>\]\[\"'`]+")
DYNAMIC_SHELL = re.compile(r"!`")


def _facts(file: FileInput) -> dict:
    path, text = file["path"], file["content"]
    facts = {}
    if path == "SKILL.md" or path.endswith("/SKILL.md"):
        frontmatter, _, errors = parse_frontmatter(text)
        if frontmatter is not None:
            for key in sorted(CONTROL_KEYS & frontmatter.keys()):
                # Hash values: tool metadata and hooks can themselves contain secrets.
                facts[f"metadata:{key}"] = file_sha256(
                    yaml.safe_dump(frontmatter[key], sort_keys=True)
                )
        elif errors:
            facts["unparsed_metadata"] = file_sha256(text)
        if DYNAMIC_SHELL.search(text):
            facts["dynamic_shell"] = file_sha256(text)
    if "scripts" in path.split("/") or path.lower().endswith(EXECUTABLE_SUFFIXES):
        facts["executable_file"] = file_sha256(text)
    origins: dict[str, set[str]] = {}
    for match in URL.finditer(text):
        try:
            parsed = urlsplit(match.group(0))
            if parsed.hostname:
                # Show origins only; hashes still detect path/query/port changes.
                origin = f"{parsed.scheme}://{parsed.hostname.lower()}"
                origins.setdefault(origin, set()).add(file_sha256(match.group(0)))
        except ValueError:
            continue
    for origin in sorted(origins):
        facts[f"external_destination:{origin}"] = canonical_digest(
            sorted(origins[origin])
        )
    return facts


def capability_changes(*, before: list[FileInput], after: list[FileInput]) -> dict:
    old = {item["path"]: _facts(item) for item in before}
    new = {item["path"]: _facts(item) for item in after}
    changes = []
    for path in sorted(old.keys() | new.keys()):
        left, right = old.get(path, {}), new.get(path, {})
        for key in sorted(left.keys() | right.keys()):
            if left.get(key) == right.get(key):
                continue
            category, _, name = key.partition(":")
            changes.append(
                {
                    "path": path,
                    "category": category,
                    "name": name or None,
                    "change": (
                        "added"
                        if key not in left
                        else "removed" if key not in right else "modified"
                    ),
                    "before": left.get(key),
                    "after": right.get(key),
                }
            )
    result = {
        "before_digest": manifest_digest(before),
        "after_digest": manifest_digest(after),
        "changes": changes,
        "scope": "recognized_static_declarations",
        "notice": "Declared permissions are not a sandbox. Unrecognized behavior may still exist.",
    }
    return {**result, "fingerprint": canonical_digest(result)}
