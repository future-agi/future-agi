from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, TypedDict

import structlog
import yaml
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from minio.error import S3Error

from simulate.models import (
    AgentDefinition,
    AgentSkillVersion,
    AgentVersion,
    AgentVersionContextFile,
    AgentVersionSetup,
    AgentVersionSkillScan,
    AgentVersionSkillScanStatus,
    CallExecution,
    ContextRuntime,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
    SkillKind,
    TestExecution,
)
from simulate.selectors.runs import user_runs
from simulate.services.harness_versioning.archives import archive_size, get_archive
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.run_traces_v3 import (
    CALL_MARK_KEY,
    READ_TIMEOUT_MS,
    RUN_MARK_KEY,
    run_traces_source,
)
from tracer.services.clickhouse.client import CHError, get_clickhouse_client
from tracer.services.clickhouse.v2.query_builders import columns as cols

logger = structlog.get_logger(__name__)

MAX_FILES = 100
MAX_FILE_BYTES = 256 * 1024
MAX_VERSION_BYTES = 2 * 1024 * 1024
SCAN_MAX_COMPRESSED_BYTES = 64 * 1024 * 1024
SCAN_MAX_SKILLS = 50
MAX_USAGE_RUNS = 50
USAGE_SOURCE = "otel_spans"
SKILL_TOOL_NAME = "Skill"
SKILL_SPAN_NAME = f"tool.{SKILL_TOOL_NAME}"
TOOL_NAME_ATTR = "claude_agent.tool.name"
TOOL_INPUT_ATTR = "claude_agent.tool.input"
SKILL_INPUT_KEYS = ("skill", "command", "name")

_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_SOURCE_ROOT = "source/"
_SCAN_SKIP_PARTS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "site-packages"}
)
CODEX_MAX_CONTEXT_BYTES = 32 * 1024
MAX_IMPORT_HOPS = 4
MAX_CONTEXT_FILES = 50
_CLAUDE_FILES = (
    ("CLAUDE.md", "claude_md"),
    (".claude/CLAUDE.md", "claude_dir_md"),
    ("CLAUDE.local.md", "claude_local_md"),
)
_CLAUDE_AGENTS_FILES = (
    ("AGENTS.md", "agents_md"),
    (".claude/AGENTS.md", "claude_dir_agents_md"),
)
_CODEX_FILES = (
    ("AGENTS.override.md", "agents_override_md"),
    ("AGENTS.md", "agents_md"),
)
_GENERIC_FILES = (("AGENTS.md", "agents_md"),)
_IMPORT_RULE = "import"
_IDE_PARTS = frozenset(
    {".github", ".cursor", ".windsurf", ".clinerules", ".roo", ".kiro", ".junie"}
)
_MANIFESTS = frozenset(
    {"pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "package.json"}
)
_CLAUDE_MARKERS = (
    "claude-agent-sdk",
    "claude_agent_sdk",
    "@anthropic-ai/claude-agent-sdk",
    "@anthropic-ai/claude-code",
)
_CODEX_MARKERS = ("@openai/codex",)
_IMPORT = re.compile(r"(?<![\w@`])@((?:\\ |[^\s`@])+)")
_FENCE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN = re.compile(r"`[^`]*`")
_TRAILING = ".,;:!?)]}>\"'"


class UsageState:
    INSTRUMENTED = "instrumented"
    NOT_INSTRUMENTED = "not_instrumented"
    NO_CALLS = "no_calls"
    UNAVAILABLE = "unavailable"


class CallUsage(TypedDict):
    instrumented: bool
    observed: list[str]


@dataclass(frozen=True)
class FoundItem:
    kind: str
    path: str
    name: str
    description: str
    problem: str
    manifest: list[dict[str, object]]
    files_hash: str
    size: int


@dataclass(frozen=True)
class ContextEntry:
    item: FoundItem
    rule: str
    on_demand: bool
    folder: str
    imported_from: str


@dataclass
class ContextChain:
    entries: list[ContextEntry] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    total: int = 0
    capped: bool = False


@dataclass(frozen=True)
class UploadScan:
    runtime: str
    skills: list[FoundItem]
    context: list[ContextEntry]
    capped: bool


def skills_enabled() -> bool:
    return bool(getattr(settings, "HARNESS_SKILLS_ENABLED", False))


def require_skills() -> None:
    if not skills_enabled():
        raise HostedHarnessError(
            "skills_disabled", "skills are not enabled", status_code=404
        )


def scoped_agent(organization, workspace, agent_definition_id) -> AgentDefinition:
    agents = AgentDefinition.no_workspace_objects.filter(
        id=agent_definition_id, organization=organization, deleted=False
    )
    if workspace is not None:
        agents = agents.filter(Q(workspace=workspace) | Q(workspace__isnull=True))
    agent = agents.first()
    if agent is None:
        raise HostedHarnessError("agent_not_found", "Agent not found", status_code=404)
    return agent


def agent_version_of(
    agent: AgentDefinition, agent_version_id: object | None
) -> AgentVersion | None:
    versions = AgentVersion.no_workspace_objects.filter(
        agent_definition=agent, deleted=False
    )
    if agent_version_id is None:
        return versions.order_by("-version_number").first()
    version = versions.filter(id=agent_version_id).first()
    if version is None:
        raise HostedHarnessError(
            "agent_version_not_found", "Agent version not found", status_code=404
        )
    return version


def _frontmatter(text: str) -> tuple[dict[str, object] | None, str]:
    match = _FRONTMATTER.match(text)
    if match is None:
        return None, "frontmatter_missing"
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None, "frontmatter_invalid"
    if not isinstance(data, dict):
        return None, "frontmatter_invalid"
    return data, ""


def _skill_header(body: bytes, fallback: str) -> tuple[str, str, str]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return fallback, "", "not_text"
    data, problem = _frontmatter(text)
    if data is None:
        return fallback, "", problem
    name = data.get("name")
    if not isinstance(name, str) or not _NAME.match(name) or len(name) > 64:
        return fallback, "", "frontmatter_name_invalid"
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        return name, "", "frontmatter_description_missing"
    return name, description.strip(), ""


def _manifest(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    return [
        {
            "path": path,
            "size": len(files[path]),
            "sha256": hashlib.sha256(files[path]).hexdigest(),
        }
        for path in sorted(files)
    ]


def _files_hash(manifest: list[dict[str, object]]) -> str:
    pairs = [[entry["path"], entry["sha256"]] for entry in manifest]
    return hashlib.sha256(
        json.dumps(pairs, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _upload_key(runtime: HarnessAgentVersionRuntime) -> str | None:
    from simulate.services.hosted_harness_gateway import _source_object_key

    if runtime.source_kind != HarnessAgentVersionRuntime.SourceKind.ARCHIVE:
        return None
    archive_id = str((runtime.source or {}).get("archive_artifact_id") or "")
    if not archive_id:
        return None
    return _source_object_key(runtime.organization_id, archive_id)


def _source_members(tar: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for member in tar:
        if not member.isfile() or not member.name.startswith(_SOURCE_ROOT):
            continue
        path = member.name[len(_SOURCE_ROOT) :]
        if path and not _SCAN_SKIP_PARTS & set(PurePosixPath(path).parts):
            members[path] = member
    return members


def _read_members(
    tar: tarfile.TarFile, members: Mapping[str, tarfile.TarInfo]
) -> dict[str, bytes]:
    read: dict[str, bytes] = {}
    for path, member in members.items():
        source = tar.extractfile(member)
        read[path] = source.read() if source is not None else b""
    return read


def found_item(
    kind: str, path: str, name: str, files: Mapping[str, bytes]
) -> FoundItem:
    manifest = _manifest(files)
    description, problem = "", ""
    if kind == SkillKind.SKILL:
        name, description, problem = _skill_header(files["SKILL.md"], name)
    return FoundItem(
        kind=kind,
        path=path,
        name=name[:64],
        description=description,
        problem=problem,
        manifest=manifest,
        files_hash=_files_hash(manifest),
        size=sum(len(body) for body in files.values()),
    )


def _oversized_item(
    path: str, name: str, members: Mapping[str, tarfile.TarInfo]
) -> FoundItem:
    manifest = [
        {"path": rel, "size": members[rel].size, "sha256": ""}
        for rel in sorted(members)
    ]
    return FoundItem(
        kind=SkillKind.SKILL,
        path=path,
        name=name[:64],
        description="",
        problem="too_large",
        manifest=manifest,
        files_hash=_files_hash(
            [{"path": f"{e['path']}:{e['size']}", "sha256": ""} for e in manifest]
        ),
        size=sum(member.size for member in members.values()),
    )


def _skill_folders(members: Mapping[str, tarfile.TarInfo]) -> list[str]:
    folders = {
        PurePosixPath(path).parent.as_posix()
        for path in members
        if PurePosixPath(path).name == "SKILL.md"
    }
    folders.discard(".")
    return sorted(folders)[:SCAN_MAX_SKILLS]


def _skills(
    tar: tarfile.TarFile, members: Mapping[str, tarfile.TarInfo]
) -> list[FoundItem]:
    items: list[FoundItem] = []
    for folder in _skill_folders(members):
        inside = {
            path[len(folder) + 1 :]: member
            for path, member in members.items()
            if path.startswith(f"{folder}/")
        }
        fallback = PurePosixPath(folder).name.lower()
        if (
            len(inside) > MAX_FILES
            or sum(member.size for member in inside.values()) > MAX_VERSION_BYTES
        ):
            items.append(_oversized_item(folder, fallback, inside))
            continue
        items.append(
            found_item(SkillKind.SKILL, folder, fallback, _read_members(tar, inside))
        )
    return items


def _join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def _clean_relative(raw: str) -> str | None:
    path = PurePosixPath(raw.strip().strip("/"))
    if not raw.strip().strip("/") or path.is_absolute():
        return None
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def agent_folder_of(runtime: HarnessAgentVersionRuntime | None) -> str:
    source = (runtime.source if runtime is not None else None) or {}
    return _clean_relative(str(source.get("subfolder") or "")) or ""


def folder_chain(agent_folder: str) -> list[str]:
    parts = PurePosixPath(agent_folder).parts if agent_folder else ()
    return ["", *("/".join(parts[: index + 1]) for index in range(len(parts)))]


def _is_manifest(name: str) -> bool:
    return name in _MANIFESTS or (
        name.startswith("requirements") and name.endswith(".txt")
    )


def detect_runtime(
    tar: tarfile.TarFile, members: Mapping[str, tarfile.TarInfo], folders: list[str]
) -> str:
    wanted = set(folders)
    manifests = {
        path: member
        for path, member in members.items()
        if _is_manifest(PurePosixPath(path).name)
        and member.size <= MAX_FILE_BYTES
        and _parent(path) in wanted
    }
    text = (
        b"\n".join(_read_members(tar, manifests).values())
        .decode("utf-8", errors="replace")
        .lower()
    )
    if any(marker in text for marker in _CLAUDE_MARKERS):
        return ContextRuntime.CLAUDE
    if any(marker in text for marker in _CODEX_MARKERS):
        return ContextRuntime.CODEX
    return ContextRuntime.GENERIC


def _parent(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent


def import_targets(text: str) -> list[str]:
    targets: list[str] = []
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for match in _IMPORT.finditer(_CODE_SPAN.sub("", line)):
            target = match.group(1).replace("\\ ", " ").rstrip(_TRAILING)
            if "." in target or "/" in target:
                targets.append(target)
    return targets


def resolve_import(importer: str, target: str) -> str | None:
    if target.startswith(("/", "~")):
        return None
    parts: list[str] = []
    for part in (PurePosixPath(_parent(importer)) / target).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts) or None


def _context_name(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")[:64] or "context"


def _context_item(path: str, member: tarfile.TarInfo, body: bytes | None) -> FoundItem:
    name = _context_name(path)
    if body is None:
        manifest = [
            {"path": PurePosixPath(path).name, "size": member.size, "sha256": ""}
        ]
        return FoundItem(
            kind=SkillKind.CONTEXT,
            path=path,
            name=name,
            description="",
            problem="too_large",
            manifest=manifest,
            files_hash=_files_hash([{"path": f"{path}:{member.size}", "sha256": ""}]),
            size=member.size,
        )
    return found_item(SkillKind.CONTEXT, path, name, {PurePosixPath(path).name: body})


def _add_context(
    tar: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    chain: ContextChain,
    path: str,
    *,
    rule: str,
    on_demand: bool,
    folder: str,
    imported_from: str = "",
    follow_imports: bool,
    cap: int | None = None,
    depth: int = 0,
) -> None:
    member = members.get(path)
    if member is None or path in chain.seen or len(chain.entries) >= MAX_CONTEXT_FILES:
        return
    if cap is not None and chain.total + member.size > cap:
        chain.capped = True
        return
    chain.seen.add(path)
    body = (
        _read_members(tar, {path: member})[path]
        if member.size <= MAX_FILE_BYTES
        else None
    )
    item = _context_item(path, member, body)
    chain.entries.append(
        ContextEntry(
            item=item,
            rule=rule,
            on_demand=on_demand,
            folder=folder,
            imported_from=imported_from,
        )
    )
    chain.total += member.size
    if not follow_imports or body is None or depth >= MAX_IMPORT_HOPS:
        return
    for target in import_targets(body.decode("utf-8", errors="replace")):
        resolved = resolve_import(path, target)
        if resolved is None:
            logger.info("agent_context_import_refused", importer=path)
            continue
        _add_context(
            tar,
            members,
            chain,
            resolved,
            rule=_IMPORT_RULE,
            on_demand=on_demand,
            folder=folder,
            imported_from=path,
            follow_imports=True,
            depth=depth + 1,
        )


def _context_specs(
    runtime: str, members: Mapping[str, tarfile.TarInfo], folders: list[str]
) -> tuple[tuple[str, str], ...]:
    if runtime == ContextRuntime.CODEX:
        return _CODEX_FILES
    if runtime != ContextRuntime.CLAUDE:
        return _GENERIC_FILES
    has_claude = any(
        _join(folder, name) in members
        for folder in folders
        for name, _rule in _CLAUDE_FILES
    )
    return _CLAUDE_FILES if has_claude else _CLAUDE_AGENTS_FILES


def _always_on(
    tar: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    chain: ContextChain,
    runtime: str,
    folders: list[str],
    specs: tuple[tuple[str, str], ...],
) -> None:
    claude = runtime == ContextRuntime.CLAUDE
    for folder in folders:
        for name, rule in specs:
            path = _join(folder, name)
            if path not in members:
                continue
            _add_context(
                tar,
                members,
                chain,
                path,
                rule=rule,
                on_demand=False,
                folder=folder,
                follow_imports=claude,
                cap=CODEX_MAX_CONTEXT_BYTES
                if runtime == ContextRuntime.CODEX
                else None,
            )
            if runtime == ContextRuntime.CODEX:
                break
        if chain.capped:
            return


def _skipped_for_context(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(_IDE_PARTS & set(parts)) or "skills" in {
        parts[index + 1] for index, part in enumerate(parts[:-1]) if part == ".claude"
    }


def _below(folder: str, agent_folder: str) -> bool:
    if folder == agent_folder:
        return False
    return not agent_folder or folder.startswith(f"{agent_folder}/")


def _on_demand(
    tar: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    chain: ContextChain,
    runtime: str,
    agent_folder: str,
    specs: tuple[tuple[str, str], ...],
) -> None:
    if runtime == ContextRuntime.CODEX:
        return
    longest_first = sorted(specs, key=lambda spec: -len(spec[0]))
    for path in sorted(members):
        if _skipped_for_context(path):
            continue
        for name, rule in longest_first:
            if path != name and not path.endswith(f"/{name}"):
                continue
            folder = path[: -len(name)].rstrip("/")
            if _below(folder, agent_folder):
                _add_context(
                    tar,
                    members,
                    chain,
                    path,
                    rule=rule,
                    on_demand=True,
                    folder=folder,
                    follow_imports=runtime == ContextRuntime.CLAUDE,
                )
            break


def scan_upload(body: bytes, agent_folder: str = "") -> UploadScan:
    folders = folder_chain(agent_folder)
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        members = _source_members(tar)
        runtime = detect_runtime(tar, members, folders)
        specs = _context_specs(runtime, members, folders)
        chain = ContextChain()
        _always_on(tar, members, chain, runtime, folders, specs)
        _on_demand(tar, members, chain, runtime, agent_folder, specs)
        return UploadScan(
            runtime=runtime,
            skills=_skills(tar, members),
            context=chain.entries,
            capped=chain.capped,
        )


def _scan_source(key: str, agent_folder: str) -> tuple[str | None, UploadScan | None]:
    try:
        size = archive_size(key)
        if size is None:
            return AgentVersionSkillScanStatus.SOURCE_MISSING, None
        if size > SCAN_MAX_COMPRESSED_BYTES:
            return AgentVersionSkillScanStatus.TOO_LARGE, None
        return AgentVersionSkillScanStatus.SCANNED, scan_upload(
            get_archive(key), agent_folder
        )
    except (S3Error, tarfile.TarError, OSError) as exc:
        logger.warning("skill_scan_failed", source_key=key, error=type(exc).__name__)
        return None, None


def next_version_number(latest: tuple[int, str] | None, files_hash: str) -> int | None:
    if latest is None:
        return 1
    number, latest_hash = latest
    return None if latest_hash == files_hash else number + 1


def _versioned(version: AgentVersion, item: FoundItem) -> AgentSkillVersion:
    latest = (
        AgentSkillVersion.no_workspace_objects.filter(
            agent_definition_id=version.agent_definition_id,
            path=item.path,
            deleted=False,
        )
        .order_by("-version_number")
        .first()
    )
    number = next_version_number(
        (latest.version_number, latest.files_hash) if latest is not None else None,
        item.files_hash,
    )
    if number is None:
        return latest
    return AgentSkillVersion.no_workspace_objects.create(
        organization_id=version.organization_id,
        workspace_id=version.workspace_id,
        agent_definition_id=version.agent_definition_id,
        kind=item.kind,
        path=item.path,
        version_number=number,
        name=item.name,
        description=item.description,
        readable=not item.problem,
        problem=item.problem,
        files_hash=item.files_hash,
        files_manifest=item.manifest,
        size=item.size,
    )


def scan_agent_version(agent_version_id: object) -> AgentVersionSkillScan | None:
    existing = AgentVersionSkillScan.no_workspace_objects.filter(
        agent_version_id=agent_version_id
    ).first()
    if existing is not None:
        return existing
    version = AgentVersion.no_workspace_objects.filter(id=agent_version_id).first()
    runtime = HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version_id=agent_version_id
    ).first()
    key = _upload_key(runtime) if runtime is not None else None
    if version is None or key is None:
        return None
    status, found = _scan_source(key, agent_folder_of(runtime))
    if status is None:
        return None
    with transaction.atomic():
        AgentDefinition.no_workspace_objects.select_for_update().filter(
            id=version.agent_definition_id
        ).first()
        existing = AgentVersionSkillScan.no_workspace_objects.filter(
            agent_version_id=agent_version_id
        ).first()
        if existing is not None:
            return existing
        scan = AgentVersionSkillScan.no_workspace_objects.create(
            agent_version_id=agent_version_id,
            organization_id=version.organization_id,
            source_key=key,
            status=status,
            runtime=found.runtime if found is not None else ContextRuntime.GENERIC,
            context_capped=found.capped if found is not None else False,
        )
        rows = [_versioned(version, item) for item in (found.skills if found else [])]
        for position, entry in enumerate(found.context if found else []):
            row = _versioned(version, entry.item)
            rows.append(row)
            AgentVersionContextFile.no_workspace_objects.create(
                scan=scan,
                skill_version=row,
                position=position,
                rule=entry.rule,
                on_demand=entry.on_demand,
                folder=entry.folder,
                imported_from=entry.imported_from,
            )
        scan.skill_versions.set({row.id: row for row in rows}.values())
    logger.info(
        "agent_version_skills_scanned",
        agent_version_id=str(agent_version_id),
        status=status,
        runtime=scan.runtime,
        found=len(rows),
    )
    return scan


def scan_after_commit(agent_version_id: object) -> None:
    if skills_enabled():
        transaction.on_commit(lambda: scan_agent_version(agent_version_id), robust=True)


def _scan_of(version: AgentVersion | None) -> AgentVersionSkillScan | None:
    if version is None:
        return None
    return AgentVersionSkillScan.no_workspace_objects.filter(
        agent_version=version
    ).first()


def _scan_items(scan: AgentVersionSkillScan) -> list[AgentSkillVersion]:
    return list(scan.skill_versions.filter(deleted=False).order_by("kind", "path"))


def _context_files(scan: AgentVersionSkillScan) -> list[AgentVersionContextFile]:
    return list(
        AgentVersionContextFile.no_workspace_objects.filter(scan=scan, deleted=False)
        .select_related("skill_version")
        .order_by("position")
    )


def _context(scan: AgentVersionSkillScan) -> dict[str, object]:
    return {
        "runtime": scan.runtime,
        "capped": scan.context_capped,
        "files": [
            {
                "id": entry.skill_version.id,
                "name": entry.skill_version.name,
                "path": entry.skill_version.path,
                "version_number": entry.skill_version.version_number,
                "readable": entry.skill_version.readable,
                "problem": entry.skill_version.problem,
                "position": entry.position,
                "rule": entry.rule,
                "on_demand": entry.on_demand,
                "folder": entry.folder,
                "imported_from": entry.imported_from,
            }
            for entry in _context_files(scan)
        ],
    }


def _source_kind(version: AgentVersion) -> str | None:
    return (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=version)
        .values_list("source_kind", flat=True)
        .first()
    )


def dispatch_skills(agent_version_id: object | None) -> list[dict[str, object]]:
    if not skills_enabled() or agent_version_id is None:
        return []
    scan = AgentVersionSkillScan.no_workspace_objects.filter(
        agent_version_id=agent_version_id
    ).first()
    if scan is None:
        return []
    return [
        {
            "name": item.name,
            "version_number": item.version_number,
            "path": item.path,
            "kind": item.kind,
        }
        for item in _scan_items(scan)
        if item.readable
    ]


def receipt_skill_usage(call_data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    usage = (call_data or {}).get("skill_usage")
    if not isinstance(usage, Mapping):
        return None
    observed = usage.get("observed")
    return {
        "instrumented": bool(usage.get("instrumented")),
        "observed": (
            sorted({str(name) for name in observed})
            if isinstance(observed, list)
            else None
        ),
        "source": str(usage.get("source") or USAGE_SOURCE),
    }


def skill_name_from_input(raw: str) -> str | None:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, Mapping):
        return None
    for key in SKILL_INPUT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip().lstrip("/")
            return name.rsplit(":", 1)[-1] or None
    return None


def usage_sql() -> str:
    job_mark = cols.json_path(cols.RESOURCE_ATTRS, RUN_MARK_KEY, "String")
    call_mark = f"{cols.ATTRS_STRING}['{CALL_MARK_KEY}']"
    tool_name = f"{cols.ATTRS_STRING}['{TOOL_NAME_ATTR}']"
    tool_input = f"{cols.ATTRS_STRING}['{TOOL_INPUT_ATTR}']"
    is_skill = f"(name = '{SKILL_SPAN_NAME}' OR {tool_name} = '{SKILL_TOOL_NAME}')"
    return (
        f"SELECT {call_mark} AS call_execution_id, "
        "count() AS spans, "
        f"groupArrayIf({tool_input}, {is_skill}) AS skill_inputs "
        "FROM spans FINAL "
        "WHERE project_id = %(project_id)s "
        "AND start_time >= %(window_start)s "
        "AND start_time <= %(window_end)s "
        f"AND {job_mark} IN %(job_ids)s "
        f"AND {call_mark} IN %(call_ids)s "
        "GROUP BY call_execution_id"
    )


def _spans_usage(
    runs: list[HostedHarnessJob], call_ids: list[str]
) -> dict[str, tuple[int, list[str]]] | None:
    source = run_traces_source(runs[0].test_execution)
    if source is None:
        return {}
    params = {
        "project_id": source.project_id,
        "job_ids": tuple(str(run.id) for run in runs),
        "window_start": min(run.created_at for run in runs),
        "window_end": max(run.terminal_at or timezone.now() for run in runs),
        "call_ids": tuple(call_ids),
    }
    try:
        rows, _columns, _elapsed_ms = get_clickhouse_client().execute_read(
            usage_sql(), params, timeout_ms=READ_TIMEOUT_MS
        )
    except (CHError, TimeoutError) as exc:
        logger.warning(
            "skill_usage_read_failed",
            agent_version_id=str(runs[0].agent_version_id),
            error=type(exc).__name__,
        )
        return None
    return {
        str(call_id): (
            int(spans),
            sorted({name for name in map(skill_name_from_input, inputs) if name}),
        )
        for call_id, spans, inputs in rows
    }


def _calls_usage(
    runs: list[HostedHarnessJob], calls: list[CallExecution]
) -> dict[str, CallUsage] | None:
    receipts = {
        str(call.id): (call.call_metadata or {}).get("skill_usage") for call in calls
    }
    needs_spans = [
        call_id
        for call_id, receipt in receipts.items()
        if not isinstance(receipt, Mapping) or receipt.get("instrumented")
    ]
    spans = _spans_usage(runs, needs_spans) if needs_spans and runs else {}
    if spans is None:
        return None
    usage: dict[str, CallUsage] = {}
    for call_id, receipt in receipts.items():
        seen, observed = spans.get(call_id, (0, []))
        if isinstance(receipt, Mapping):
            instrumented = bool(receipt.get("instrumented"))
            if isinstance(receipt.get("observed"), list):
                observed = sorted({str(name) for name in receipt["observed"]})
        else:
            instrumented = seen > 0
        usage[call_id] = {
            "instrumented": instrumented,
            "observed": observed if instrumented else [],
        }
    return usage


def _version_instrumentation(version: AgentVersion) -> str:
    return (
        AgentVersionSetup.no_workspace_objects.filter(agent_version=version)
        .values_list("instrumentation", flat=True)
        .first()
        or "none"
    )


def _usage_state(calls: int, instrumented_calls: int, setup: str) -> str:
    if instrumented_calls:
        return UsageState.INSTRUMENTED
    if calls:
        return UsageState.NOT_INSTRUMENTED
    return UsageState.NO_CALLS if setup != "none" else UsageState.NOT_INSTRUMENTED


def _version_usage(version: AgentVersion) -> tuple[str, int, dict[str, int]]:
    runs = list(
        HostedHarnessJob.no_workspace_objects.filter(
            user_runs(),
            agent_version=version,
            test_execution__isnull=False,
            deleted=False,
        )
        .select_related("test_execution")
        .order_by("-created_at")[:MAX_USAGE_RUNS]
    )
    calls = list(
        CallExecution.no_workspace_objects.filter(
            test_execution_id__in=[run.test_execution_id for run in runs],
            status=CallExecution.CallStatus.COMPLETED,
        ).only("id", "test_execution_id", "call_metadata")
    )
    usage = _calls_usage(runs, calls)
    if usage is None:
        return UsageState.UNAVAILABLE, 0, {}
    instrumented = 0
    observed: dict[str, int] = defaultdict(int)
    for call_usage in usage.values():
        if not call_usage["instrumented"]:
            continue
        instrumented += 1
        for name in call_usage["observed"]:
            observed[name] += 1
    state = _usage_state(len(calls), instrumented, _version_instrumentation(version))
    return state, instrumented, dict(observed)


def agent_version_skills(
    agent: AgentDefinition, version: AgentVersion | None
) -> dict[str, object]:
    if version is None:
        return {"agent_version": None, "status": "no_version", "items": []}
    base: dict[str, object] = {
        "agent_version": {"id": version.id, "version_number": version.version_number},
        "source_kind": _source_kind(version),
    }
    if base["source_kind"] != HarnessAgentVersionRuntime.SourceKind.ARCHIVE:
        return {**base, "status": "unsupported_source", "items": []}
    scan = _scan_of(version)
    if scan is None:
        return {**base, "status": "not_scanned", "items": []}
    state, calls, observed = _version_usage(version)
    return {
        **base,
        "status": scan.status,
        "usage": {"state": state, "calls": calls},
        "context": _context(scan),
        "items": [
            {
                "id": item.id,
                "kind": item.kind,
                "name": item.name,
                "path": item.path,
                "description": item.description,
                "version_number": item.version_number,
                "readable": item.readable,
                "problem": item.problem,
                "observed_calls": (
                    observed.get(item.name, 0)
                    if state == UsageState.INSTRUMENTED and item.kind == SkillKind.SKILL
                    else None
                ),
            }
            for item in _scan_items(scan)
            if item.kind == SkillKind.SKILL
        ],
    }


def skill_files(
    version: AgentVersion, skill_version_id: object
) -> list[dict[str, str]]:
    scan = _scan_of(version)
    item = (
        scan.skill_versions.filter(id=skill_version_id, deleted=False).first()
        if scan is not None
        else None
    )
    if item is None:
        raise HostedHarnessError(
            "agent_skill_not_found",
            "this skill is not in this agent version",
            status_code=404,
        )
    prefix = f"{_SOURCE_ROOT}{item.path}"
    try:
        with tarfile.open(
            fileobj=io.BytesIO(get_archive(scan.source_key)), mode="r:gz"
        ) as tar:
            if item.kind == SkillKind.CONTEXT:
                wanted = {item.path: tar.getmember(prefix)}
            else:
                wanted = {
                    member.name[len(prefix) + 1 :]: member
                    for member in tar
                    if member.isfile() and member.name.startswith(f"{prefix}/")
                }
            read = _read_members(tar, wanted)
    except (S3Error, tarfile.TarError, KeyError, OSError) as exc:
        raise HostedHarnessError(
            "agent_skill_source_unavailable",
            "the uploaded folder this skill came from can no longer be read",
            status_code=422,
        ) from exc
    return [
        {"path": path, "content": body.decode("utf-8", errors="replace")}
        for path, body in sorted(read.items())
    ]


def call_skills(
    execution: TestExecution, call_execution_id: object
) -> dict[str, object] | None:
    if not skills_enabled():
        return None
    run = (
        HostedHarnessJob.no_workspace_objects.filter(
            test_execution=execution, deleted=False
        )
        .select_related("agent_version")
        .first()
    )
    scan = _scan_of(run.agent_version) if run is not None else None
    if scan is None:
        return {"state": "not_scanned", "items": []}
    call = CallExecution.no_workspace_objects.filter(
        id=call_execution_id, test_execution=execution
    ).first()
    usage = _calls_usage([run], [call]) if call is not None else {}
    call_usage = (usage or {}).get(str(call_execution_id))
    if usage is None:
        state = UsageState.UNAVAILABLE
    elif call_usage and call_usage["instrumented"]:
        state = UsageState.INSTRUMENTED
    else:
        state = UsageState.NOT_INSTRUMENTED
    observed = set(call_usage["observed"]) if call_usage else set()
    on_demand = {
        entry.skill_version_id for entry in _context_files(scan) if entry.on_demand
    }
    return {
        "state": state,
        "items": [
            {
                "name": item.name,
                "path": item.path,
                "kind": item.kind,
                "version_number": item.version_number,
                "observed": (
                    item.name in observed
                    if state == UsageState.INSTRUMENTED and item.kind == SkillKind.SKILL
                    else None
                ),
            }
            for item in _scan_items(scan)
            if item.readable and item.id not in on_demand
        ],
    }
