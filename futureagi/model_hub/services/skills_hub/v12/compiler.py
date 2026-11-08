"""Pure compilation of one standing-instruction root and exact leaf snapshots."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import NoReturn, cast
from uuid import UUID

from model_hub.services.skills_hub.checks import parse_frontmatter, run_checks
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.limits import LABEL_RE, NAME_RE
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.types import FileInput
from model_hub.services.skills_hub.v12.limits import DESCRIPTOR_PATH, MAX_LINKS
from model_hub.services.skills_hub.v12.types import (
    CompiledContext,
    ContextSpec,
    LinkInput,
    LockedLink,
    ResolvedLock,
    Selector,
)

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTROL_NAMES = {
    "agents.md",
    "agents.override.md",
    "claude.md",
    "claude.local.md",
    "gemini.md",
    "copilot-instructions.md",
    ".cursorrules",
    ".mcp.json",
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "makefile",
    "dockerfile",
    "docker-compose.yml",
    "compose.yml",
}
_CONTROL_DIRS = {
    ".git",
    ".github",
    ".claude",
    ".agents",
    ".cursor",
    ".gemini",
    ".vscode",
}


def _invalid(message: str, code: str = "invalid_context_definition") -> NoReturn:
    raise SkillsHubError(code, message, status=400)


def validate_links(links: list[LinkInput]) -> list[LinkInput]:
    """Normalize identity spelling, never an unsafe path or authored byte."""
    if not isinstance(links, list) or len(links) > MAX_LINKS:
        _invalid("Choose at most 20 unique leaf skills.", "invalid_dependency_selector")
    result: list[LinkInput] = []
    seen: set[str] = set()
    for link in links:
        if not isinstance(link, dict) or set(link) != {"skill_id", "selector"}:
            _invalid(
                "Each link needs a skill ID and one selector.",
                "invalid_dependency_selector",
            )
        try:
            identity = str(UUID(str(link["skill_id"])))
        except (ValueError, TypeError, AttributeError) as exc:
            raise SkillsHubError(
                "invalid_dependency_selector", "Use a skill UUID.", status=400
            ) from exc
        raw_selector = link["selector"]
        if not isinstance(raw_selector, dict):
            _invalid("Choose a version or label.", "invalid_dependency_selector")
        selector = cast(dict, raw_selector)
        valid = (
            set(selector) == {"version"}
            and type(selector["version"]) is int
            and selector["version"] > 0
        ) or (
            set(selector) == {"label"}
            and isinstance(selector["label"], str)
            and re.fullmatch(LABEL_RE, selector["label"]) is not None
        )
        if not valid or identity in seen:
            _invalid(
                "Use unique skills and explicit positive versions or labels.",
                "invalid_dependency_selector",
            )
        seen.add(identity)
        result.append(
            {"skill_id": identity, "selector": cast(Selector, dict(selector))}
        )
    return sorted(result, key=lambda item: item["skill_id"])


def _item(code: str, message: str, path: str) -> dict:
    return {"code": code, "severity": "error", "message": message, "path": path}


def _summary(items: list[dict], tokens: dict) -> dict:
    return {
        "items": items,
        "summary": {
            level + "s": sum(row["severity"] == level for row in items)
            for level in ("error", "warning", "info")
        },
        "tokens": tokens,
        "frontmatter": None,
    }


def _reserved(path: str, *, owned: bool) -> bool:
    parts = path.casefold().split("/")
    if owned and parts[0] in {"skills", ".futureagi"}:
        return True
    if any(part in _CONTROL_DIRS for part in parts):
        return True
    if parts[-1] in _CONTROL_NAMES:
        return path != "AGENTS.md" or not owned
    return parts[-1] == "skill.md"


def check_context_files(*, files: list[FileInput], complete: bool) -> dict:
    """Reuse portable byte/path/secret/reference checks, without a fake SKILL root.

    ``complete`` allows server-generated mounts/descriptor; compilation separately
    checks every owned and leaf path before constructing that complete tree.
    """
    if not isinstance(files, list) or any(
        not isinstance(file, dict)
        or set(file) != {"path", "content"}
        or not isinstance(file["path"], str)
        or not isinstance(file["content"], str)
        for file in files
    ):
        _invalid("Files must be path/content text pairs.")
    # No root SKILL.md is permitted, so its frontmatter is never canonical here.
    portable = run_checks([file for file in files if file["path"] != "SKILL.md"])
    items = [item for item in portable["items"] if item["code"] != "root_missing"]
    roots = [file for file in files if file["path"] == "AGENTS.md"]
    if len(roots) != 1 or not roots[0]["content"].strip():
        items.append(
            _item("root_missing", "Add a nonempty AGENTS.md at the root.", "AGENTS.md")
        )
    for file in files:
        path = file["path"]
        if path == "SKILL.md" or (not complete and _reserved(path, owned=True)):
            items.append(
                _item(
                    "unsupported_instruction_scope",
                    "This path is reserved for managed instructions or host control.",
                    path,
                )
            )
    tokens = {**portable["tokens"], "skill_md": 0}
    return _summary(items, tokens)


def _require_checks(checks: dict) -> None:
    if checks["summary"]["errors"]:
        raise SkillsHubError(
            "checks_failed",
            "Fix the context errors before compiling.",
            status=422,
            detail=checks,
        )


def compile_context(
    *,
    name: str,
    files: list[FileInput],
    spec: ContextSpec,
    lock: ResolvedLock,
    child_snapshots: dict[UUID, list[FileInput]],
) -> CompiledContext:
    """Compile exact retained children; never resolve a selector or query the DB.

    Snapshot keys are immutable child version UUIDs, not moving skill identities.
    The caller authorizes and checks each locked identity before providing bytes.
    """
    if not isinstance(name, str) or len(name) > 64 or not re.fullmatch(NAME_RE, name):
        _invalid("Use a valid immutable context name.")
    if (
        not isinstance(spec, dict)
        or set(spec) != {"schema", "description", "links"}
        or type(spec["schema"]) is not int
        or spec["schema"] != 1
        or not isinstance(spec["description"], str)
        or not spec["description"].strip()
        or len(spec["description"]) > 1024
    ):
        _invalid("Use schema 1 with a nonempty purpose of at most 1024 characters.")
    links = validate_links(spec["links"])
    if (
        not isinstance(lock, dict)
        or set(lock) != {"schema", "links"}
        or type(lock["schema"]) is not int
        or lock["schema"] != 1
        or not isinstance(lock["links"], list)
        or len(lock["links"]) != len(links)
    ):
        _invalid("The accepted lock must match the complete authored link set.")
    _require_checks(check_context_files(files=files, complete=False))
    desired = {link["skill_id"]: link["selector"] for link in links}
    retained: list[LockedLink] = []
    mounted: list[FileInput] = []
    child_items: list[dict] = []
    contributions: list[dict] = []
    seen = set()
    for entry in lock["links"]:
        if not isinstance(entry, dict) or set(entry) != {
            "mount",
            "skill_id",
            "requested",
            "version",
            "version_id",
            "digest",
        }:
            _invalid("The accepted lock is incomplete.")
        try:
            identity, version_id = str(UUID(str(entry["skill_id"]))), UUID(
                str(entry["version_id"])
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise SkillsHubError(
                "invalid_context_definition",
                "The lock has an invalid identity.",
                status=400,
            ) from exc
        if (
            identity in seen
            or identity not in desired
            or entry["requested"] != desired[identity]
            or type(entry["version"]) is not int
            or entry["version"] < 1
            or not isinstance(entry["digest"], str)
            or not _DIGEST.fullmatch(entry["digest"])
            or (
                "version" in desired[identity]
                and desired[identity].get("version") != entry["version"]
            )
        ):
            _invalid(
                "The accepted identity or selector does not match the authored links."
            )
        seen.add(identity)
        child = child_snapshots.get(version_id)
        if child is None:
            raise SkillsHubError(
                "dependency_unavailable",
                "A complete accepted snapshot is required.",
                status=404,
            )
        checks = run_checks(child)
        _require_checks(checks)
        root = next(file["content"] for file in child if file["path"] == "SKILL.md")
        metadata, _, _ = parse_frontmatter(root)
        assert metadata is not None  # The complete leaf checks succeeded.
        mount = f"skills/{metadata['name']}/"
        if entry["mount"] != mount or manifest_digest(child) != entry["digest"]:
            _invalid(
                "The mounted name or retained bytes do not match the accepted lock."
            )
        for file in child:
            if file["path"] != "SKILL.md" and _reserved(file["path"], owned=False):
                raise SkillsHubError(
                    "unsupported_instruction_scope",
                    "A child contains another instruction root or host-control path.",
                    status=422,
                )
            mounted.append({"path": mount + file["path"], "content": file["content"]})
        child_items.extend(
            {**item, "path": mount + item.get("path", "SKILL.md")}
            for item in checks["items"]
        )
        contributions.append(
            {
                "skill_id": identity,
                "version_id": str(version_id),
                "file_count": len(child),
                "total_bytes": sum(
                    len(file["content"].encode("utf-8")) for file in child
                ),
            }
        )
        retained.append(
            {**deepcopy(entry), "skill_id": identity, "version_id": str(version_id)}
        )
    retained.sort(key=lambda entry: entry["mount"])
    descriptor = {
        "schema": 1,
        "kind": "agent_context",
        "name": name,
        "description": spec["description"],
        "entrypoint": "AGENTS.md",
        "owned_paths": sorted(file["path"] for file in files),
        "links": retained,
    }
    encoded = json.dumps(
        descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    compiled: list[FileInput] = [
        *deepcopy(files),
        *mounted,
        {"path": DESCRIPTOR_PATH, "content": encoded},
    ]
    compiled.sort(key=lambda file: file["path"])
    checks = check_context_files(files=compiled, complete=True)
    overflow = {"too_many_files", "file_too_large", "bundle_too_large", "path_invalid"}
    if any(item["code"] in overflow for item in checks["items"]):
        raise SkillsHubError(
            "context_closure_too_large",
            "The complete tree including mounts and descriptor does not fit.",
            status=422,
            detail={
                "checks": checks,
                "contributions": contributions,
                "owned_file_count": len(files),
                "descriptor_bytes": len(encoded.encode("utf-8")),
                "file_count": len(compiled),
                "total_bytes": sum(
                    len(file["content"].encode("utf-8")) for file in compiled
                ),
            },
        )
    _require_checks(checks)
    # Add child metadata diagnostics not already emitted by complete-tree checks.
    unique = {
        json.dumps(item, sort_keys=True): item
        for item in [*checks["items"], *child_items]
    }
    checks = _summary(list(unique.values()), checks["tokens"])
    return CompiledContext(
        compiled,
        {**deepcopy(spec), "links": links},
        {"schema": 1, "links": retained},
        descriptor,
        manifest_digest(compiled),
        checks,
    )


def context_descriptor(*, compiled: CompiledContext) -> dict:
    return deepcopy(compiled.descriptor)
