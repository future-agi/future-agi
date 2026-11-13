"""Credential-free, read-only inventory of a whole local trial collection.

This uses the production portable checker and compiler, not a second set of
compatibility rules. Preview UUIDs are length-equivalent placeholders, never
published identities. Only first-publication version-1 pins are projected.
Nothing is uploaded, executed, staged or implicitly excluded.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from model_hub.services.skills_hub.checks import (
    parse_frontmatter,
    path_is_valid,
    run_checks,
    text_is_valid,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.limits import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_UNZIPPED_BYTES,
    MAX_VERSION_BYTES,
    MAX_ZIP_ENTRIES,
)
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.types import FileInput
from model_hub.services.skills_hub.v12.compiler import compile_context
from model_hub.services.skills_hub.v12.types import LinkInput, LockedLink

# Input inspection is bounded independently from the much smaller compiled limit.
MAX_INPUT_BYTES = MAX_UNZIPPED_BYTES
CONTROL_NAMES = {
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
CONTROL_DIRS = {
    ".git",
    ".github",
    ".claude",
    ".agents",
    ".cursor",
    ".gemini",
    ".vscode",
}


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_at(name: str, directory: int, limit: int) -> bytes:
    """No symlink following, including a final-component swap after enumeration."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("Input must be a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        if len(raw) > limit or (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise ValueError("Input changed during inventory")
        return raw
    finally:
        os.close(fd)


def read_local_file(path: Path, limit: int = MAX_INPUT_BYTES) -> bytes:
    """Walk every component using dirfds, refusing symlink ancestors too."""
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        return _read_at(path.name, fd, limit)
    finally:
        os.close(fd)


def _row(path: str, raw: bytes) -> tuple[dict, str | None]:
    blockers = []
    if not path_is_valid(path):
        blockers.append("path_invalid")
    if len(raw) > MAX_FILE_BYTES:
        blockers.append("file_too_large")
    try:
        text = raw.decode("utf-8")
        if not text_is_valid(text):
            raise ValueError("nonportable text")
    except (UnicodeError, ValueError):
        text = None
        blockers.append("binary_or_nonportable_text")
    parts = path.lower().split("/")
    return (
        {
            "path": path,
            "size_bytes": len(raw),
            "sha256": _hash(raw),
            "blockers": blockers,
            "host_control": parts[-1] in CONTROL_NAMES
            or any(p in CONTROL_DIRS for p in parts),
            "nested_instruction": parts[-1] in {"skill.md", "agents.md", "claude.md"}
            and len(parts) > 1,
        },
        text,
    )


def _member(
    key: str, root: str, entries: list[tuple[str, bytes]], source: dict | None = None
) -> dict:
    rows: list[dict] = []
    files: list[FileInput] = []
    for path, raw in entries:
        row, text = _row(path, raw)
        rows.append(row)
        if text is not None:
            files.append({"path": path, "content": text})
    checks = run_checks(files)
    # Messages/metadata may contain source text. The local report retains codes,
    # hashes and key names, never content or metadata scalar values.
    blockers = [
        {"code": code, "path": row["path"]} for row in rows for code in row["blockers"]
    ]
    blockers.extend(
        {"code": item["code"], "path": item.get("path")}
        for item in checks["items"]
        if item["severity"] == "error"
    )
    metadata, _, _ = parse_frontmatter(
        next((f["content"] for f in files if f["path"] == "SKILL.md"), "")
    )
    name = (metadata or {}).get("name")
    # Check context embedding with the real compiler, including host-control and
    # nested instruction behavior that a valid standalone leaf may not support.
    if not blockers:
        try:
            _compile(
                [{"key": key, "name": name, "files": files}],
                "inventory",
                "Local inventory",
                [{"path": "AGENTS.md", "content": "Local inventory\n"}],
            )
        except SkillsHubError as exc:
            # Aggregate capacity/path failures belong to the full compilation,
            # so the report retains its exact counts and descriptor contribution.
            if exc.code != "context_closure_too_large":
                blockers.append({"code": exc.code, "path": None})
    return {
        "key": key,
        "root": root,
        "name": name if isinstance(name, str) else None,
        "source": source,
        "files": files,
        "inventory": sorted(rows, key=lambda row: row["path"]),
        "metadata_keys": sorted(str(key) for key in (metadata or {})),
        "unsupported_behavior": [
            {"code": item["code"], "path": item.get("path")}
            for item in checks["items"]
            if item["code"]
            in {
                "host_extension",
                "frontmatter_unknown_key",
                "allowed_tools_note",
                "script_not_executed",
            }
        ],
        "dependencies": [
            {"path": item.get("path"), "code": item["code"]}
            for item in checks["items"]
            if "reference" in item["code"] or "link" in item["code"]
        ],
        "blockers": blockers,
        "manifest_digest": manifest_digest(files),
    }


def _folder(
    path: Path, prefix: str, sink: list | None = None
) -> tuple[list[dict], list[dict]]:
    # Opening the folder via its parent chain prevents ancestor symlink escapes.
    absolute = Path(os.path.abspath(path))
    fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    entries, rejected = [], []
    total, count = 0, 0
    try:
        for part in absolute.parts[1:]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child

        def onerror(error: OSError) -> None:
            raise ValueError("Cannot enumerate the complete folder") from error

        for directory, dirs, names, dirfd in os.fwalk(
            ".", dir_fd=fd, follow_symlinks=False, onerror=onerror
        ):
            relative = directory.removeprefix("./")
            relative = "" if relative == "." else relative
            for name in sorted(dirs + names):
                count += 1
                if count > MAX_ZIP_ENTRIES:
                    raise ValueError(
                        "Inventory exceeds 1000 filesystem entries; no partial result"
                    )
                item = f"{relative}/{name}" if relative else name
                mode = os.stat(name, dir_fd=dirfd, follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    rejected.append({"path": item, "code": "symlink_or_special_file"})
                    if name in dirs:
                        dirs.remove(name)
                elif stat.S_ISREG(mode):
                    raw = _read_at(name, dirfd, MAX_INPUT_BYTES - total)
                    total += len(raw)
                    entries.append((item, raw))
    finally:
        os.close(fd)
    if sink is not None:
        # The exact bytes that were inventoried, for a migration packet; never
        # re-read later, so the packet cannot differ from this report.
        sink.extend(entries)
    roots = sorted(
        path[: -len("SKILL.md")].rstrip("/")
        for path, _ in entries
        if path == "SKILL.md" or path.endswith("/SKILL.md")
    )
    if len(roots) > 50:
        raise ValueError("Collection exceeds 50 roots; no partial result")
    members = []
    expanded_bytes = 0
    for root in roots:
        start = root + "/" if root else ""
        # Do not silently trim nested roots out of the parent's closure.
        package = [
            (path[len(start) :], raw) for path, raw in entries if path.startswith(start)
        ]
        expanded_bytes += sum(len(raw) for _, raw in package)
        if expanded_bytes > MAX_INPUT_BYTES:
            raise ValueError("Nested collection expansion exceeds the inventory budget")
        member = _member(f"{prefix}/{root or '.'}", root or ".", package)
        member["blockers"].extend(
            {"path": row["path"], "code": row["code"]}
            for row in rejected
            if row["path"].startswith(start)
        )
        members.append(member)
    unowned = [
        {
            "path": path,
            "code": "outside_skill_roots",
            "size_bytes": len(raw),
            "sha256": _hash(raw),
        }
        for path, raw in entries
        if not any(path.startswith(root + "/") if root else True for root in roots)
    ]
    return members, [{"source": prefix, **row} for row in rejected + unowned]


def _exchange(path: Path, prefix: str, sink: list | None = None) -> list[dict]:
    export = json.loads(read_local_file(path))
    if (
        not isinstance(export, dict)
        or set(export) != {"format", "origin", "project", "skills"}
        or export["format"] != "langfuse-skills-v1"
    ):
        raise ValueError(
            "Expected the bounded langfuse-skills-v1 exchange schema, not a native-export conformance claim"
        )
    if not isinstance(export["skills"], list) or not 1 <= len(export["skills"]) <= 50:
        raise ValueError("Exchange requires 1–50 members")
    members, seen = [], set()
    for index, item in enumerate(export["skills"]):
        if not isinstance(item, dict) or set(item) != {
            "resource",
            "revision",
            "digest",
            "files",
        }:
            raise ValueError("Invalid exchange member")
        source = {key: export[key] for key in ("origin", "project")}
        source.update({key: item[key] for key in ("resource", "revision", "digest")})
        for key in ("origin", "project", "resource", "revision"):
            value = source[key]
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > (255 if key == "origin" else 200)
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError("Invalid immutable source identity")
        if source["resource"] in seen:
            raise ValueError("Duplicate source identity")
        seen.add(source["resource"])
        files = item["files"]
        if (
            not isinstance(files, list)
            or not files
            or len(files) > MAX_FILES
            or any(
                not isinstance(f, dict)
                or set(f) != {"path", "content"}
                or not isinstance(f["path"], str)
                or not isinstance(f["content"], str)
                for f in files
            )
        ):
            raise ValueError("Invalid exchange files")
        if source["digest"] != manifest_digest(files):
            raise ValueError("Immutable source manifest digest differs")
        members.append(
            _member(
                f"{prefix}/{index}",
                str(index),
                [(f["path"], f["content"].encode("utf-8")) for f in files],
                source,
            )
        )
    if sink is not None:
        sink.append(export)
    return members


def _compile(members: list[dict], name: str, description: str, owned: list[FileInput]):
    links: list[LinkInput] = []
    locked: list[LockedLink] = []
    snapshots: dict[UUID, list[FileInput]] = {}
    for member in members:
        identity = str(uuid5(NAMESPACE_URL, "local-preview/skill/" + member["key"]))
        version = uuid5(NAMESPACE_URL, "local-preview/version/" + member["key"])
        links.append({"skill_id": identity, "selector": {"version": 1}})
        locked.append(
            {
                "skill_id": identity,
                "version_id": str(version),
                "version": 1,
                "requested": {"version": 1},
                "mount": f"skills/{member['name']}/",
                "digest": manifest_digest(member["files"]),
            }
        )
        snapshots[version] = member["files"]
    return compile_context(
        name=name,
        files=owned,
        spec={"schema": 1, "description": description, "links": links},
        lock={"schema": 1, "links": locked},
        child_snapshots=snapshots,
    )


def _closure(included: list[dict], name: str, description: str, owned: list[FileInput]):
    closure = {
        "fits": False,
        "identity_kind": "unpublished-version-1-projection",
        "limits": {"links": 20, "files": MAX_FILES, "bytes": MAX_VERSION_BYTES},
        "links": len(included),
        "error": None,
    }
    try:
        if any(member["blockers"] for member in included):
            raise SkillsHubError(
                "unsupported_members", "Selected members are not portable", status=422
            )
        compiled = _compile(included, name, description, owned)
        closure.update(
            fits=True,
            file_count=len(compiled.files),
            total_bytes=sum(len(f["content"].encode("utf-8")) for f in compiled.files),
            descriptor_bytes=next(
                len(f["content"].encode("utf-8"))
                for f in compiled.files
                if f["path"] == ".futureagi/context.json"
            ),
            paths=[f["path"] for f in compiled.files],
        )
    except SkillsHubError as exc:
        closure["error"] = exc.code
        closure["diagnostics"] = [
            {"code": item["code"], "path": item.get("path")}
            for item in exc.detail.get("checks", exc.detail).get("items", [])
        ]
        for key in ("file_count", "total_bytes", "descriptor_bytes"):
            if key in exc.detail:
                closure[key] = exc.detail[key]
    return closure


def inventory(
    *,
    folders: list[Path],
    exchanges: list[Path],
    agents: Path,
    name: str,
    description: str,
    selected: list[str] | None = None,
    local_source: dict | None = None,
    capture: dict | None = None,
) -> dict:
    if len(folders) + len(exchanges) > 50:
        raise ValueError("At most 50 input sources")
    if local_source is not None:
        if set(local_source) != {"origin", "project", "revision"}:
            raise ValueError(
                "Supply the local source origin, project and immutable revision"
            )
        for key, value in local_source.items():
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > (255 if key == "origin" else 200)
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError("Invalid local source identity")
    members: list[dict] = []
    unowned: list[dict] = []
    folder_identities: dict[str, dict] = {}

    def check_budget():
        if (
            len(members) > 50
            or sum(
                row["size_bytes"] for member in members for row in member["inventory"]
            )
            > MAX_INPUT_BYTES
        ):
            raise ValueError(
                "Complete collection exceeds bounded intake; no partial result"
            )

    sources: list[dict] | None = [] if capture is not None else None
    for index, folder in enumerate(folders):
        entries: list | None = [] if capture is not None else None
        found, others = _folder(folder, f"folder-{index}", entries)
        identities: dict[str, dict] = {}
        members.extend(found)
        unowned.extend(others)
        if local_source is not None:
            for member in found:
                # webkitRelativePath includes the selected directory's basename.
                # The source resource is its retained path, NOT skill frontmatter
                # or a Hub name. Keep it stable across revisions and name edits.
                resource = folder.name + (
                    "/" + member["root"] if member["root"] != "." else ""
                )
                if len(resource) > 200:
                    raise ValueError("Local source resource exceeds identity limit")
                member["source"] = {
                    **local_source,
                    "resource": resource,
                    "digest": member["manifest_digest"],
                }
                key = _hash(
                    json.dumps(["loose", resource], separators=(",", ":")).encode()
                )
                if key in folder_identities:
                    raise ValueError("Ambiguous folder upload identity")
                if not member["blockers"]:
                    folder_identities[key] = member["source"]
                    identities[key] = member["source"]
        check_budget()
        if sources is not None:
            sources.append(
                {
                    "kind": "folder",
                    "base": folder.name,
                    "entries": entries,
                    "source_identities": identities,
                }
            )
    for index, exchange in enumerate(exchanges):
        exports: list | None = [] if capture is not None else None
        members.extend(_exchange(exchange, f"exchange-{index}", exports))
        check_budget()
        if sources is not None and exports is not None:
            sources.append({"kind": "langfuse", "export": exports[0]})
    if not members:
        raise ValueError("No SKILL.md roots found")
    # Migration mappings are unique across the entire source collection, not
    # only within one exchange file. Revision, digest and frontmatter name are
    # not identity components: two such inputs would target the same Hub leaf.
    # Check before selection so excluding one cannot hide an ambiguous library.
    source_identities = set()
    for member in members:
        source = member["source"]
        if source is None:
            continue
        identity = tuple(source[key] for key in ("origin", "project", "resource"))
        if identity in source_identities:
            raise ValueError(
                "Duplicate retained source identity in complete collection"
            )
        source_identities.add(identity)
    keys = {member["key"] for member in members}
    if selected is not None and (
        len(selected) != len(set(selected)) or not set(selected) <= keys
    ):
        raise ValueError("Selected members must be unique keys in the full inventory")
    included = (
        members
        if selected is None
        else [member for member in members if member["key"] in selected]
    )
    root = read_local_file(agents, MAX_FILE_BYTES).decode("utf-8")
    owned: list[FileInput] = [{"path": "AGENTS.md", "content": root}]
    closure = _closure(included, name, description, owned)
    # Without an explicit selection, also project the closure of every member
    # that has no blocker. This is a proposal shown for review, never an
    # automatic exclusion: intake still requires explicit acknowledgment.
    proposed = None
    if selected is None and any(member["blockers"] for member in members):
        supported = [member for member in members if not member["blockers"]]
        proposed = {
            "selected": [member["key"] for member in supported],
            "excluded": [member["key"] for member in members if member["blockers"]],
            "compiled_closure": _closure(supported, name, description, owned),
        }
    if capture is not None:
        capture["sources"] = sources
        capture["context"] = {
            "name": name,
            "description": description,
            "agents_md": root,
        }
    # Unassigned files and excluded roots are visible before any intake. They are
    # not silently counted as migrated merely because the selected closure fits.
    return {
        "schema": 1,
        "kind": "local-library-inventory",
        "network_requests": 0,
        "complete_inventory": True,
        "source_member_count": len(members),
        "members": [
            {k: v for k, v in member.items() if k != "files"} for member in members
        ],
        "folder_upload_source_identities": folder_identities,
        "local_identity_policy": "Retain the selected folder basename and resource paths across revisions. Moving a source requires explicit retained-identity reconciliation; frontmatter names are never matched.",
        "unowned_or_rejected": unowned,
        "selected": [member["key"] for member in included],
        "excluded": [member["key"] for member in members if member not in included],
        "whole_collection_supported": closure["fits"]
        and len(included) == len(members)
        and not unowned,
        "compiled_closure": closure,
        **({"proposed_supported_subset": proposed} if proposed else {}),
        "limitations": [
            "Local projection only; publish and resolve exact accepted links before delivery.",
            "No source rights or native Langfuse export conformance is established.",
            "No dependency inference, host execution or automatic exclusion.",
        ],
    }
