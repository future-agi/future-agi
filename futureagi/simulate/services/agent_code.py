"""The code of an agent version, read from its stored archive: the file tree, one
file, and the files that changed against another version. The host is never
contacted; members are read from the tar and never extracted to disk."""

from __future__ import annotations

import hashlib
import io
import posixpath
import tarfile
from dataclasses import dataclass
from functools import cached_property, partial
from typing import Any

import structlog
from django.core.cache import cache
from minio.error import S3Error
from urllib3.exceptions import HTTPError

from simulate.models import AgentVersion, HarnessAgentVersionRuntime
from simulate.services.code_archives import stored_commit_archive
from simulate.services.hosted_harness import HostedHarnessError
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_storage_client

logger = structlog.get_logger(__name__)

MAX_FILE_BYTES = 1024 * 1024
MAX_TREE_ENTRIES = 5000
# How much of a member decides text from binary; a 1 MiB cap is applied by size.
_SNIFF_BYTES = 64 * 1024
_ROOT = "source/"
_CACHE_SECONDS = 24 * 3600
_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".txt": "plaintext",
    ".sh": "shell",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".env": "ini",
    ".ini": "ini",
    ".cfg": "ini",
    ".xml": "xml",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}

KIND_TEXT = "text"
KIND_BINARY = "binary"
KIND_TOO_LARGE = "too_large"


@dataclass(frozen=True)
class Entry:
    path: str
    size: int
    kind: str
    sha256: str


@dataclass(frozen=True)
class VersionCode:
    """A version's code: where it lives (an identity known before any download),
    the folder the agent is in, and a loader for the bytes, read at most once."""

    identity: str
    subfolder: str
    load: Any

    @cached_property
    def archive(self) -> bytes:
        archive = self.load()
        if archive is None:
            raise _unavailable()
        return archive


def _runtime(version: AgentVersion) -> HarnessAgentVersionRuntime | None:
    return (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=version)
        .defer("credential_blob", "diff")
        .first()
    )


def _no_code() -> HostedHarnessError:
    return HostedHarnessError(
        "no_code", "This version has no code to show", status_code=404
    )


def _unavailable() -> HostedHarnessError:
    return HostedHarnessError(
        "archive_unavailable",
        "Code is temporarily unavailable",
        status_code=503,
        retryable=True,
    )


def _read_object(key: str) -> bytes | None:
    response = None
    try:
        response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
        return response.read()
    except (S3Error, HTTPError):
        logger.warning("agent_code_object_unreadable", object_key=key, exc_info=True)
        return None
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def version_code(version: AgentVersion) -> VersionCode:
    """The stored archive a version's code is read from, with the folder the agent
    lives in; ``no_code`` for a version without one, ``archive_unavailable`` when
    the object cannot be read."""
    from simulate.services.hosted_harness_gateway import _source_object_key

    runtime = _runtime(version)
    if runtime is None:
        raise _no_code()
    source = runtime.source or {}
    kind = runtime.source_kind
    subfolder = str(source.get("subfolder") or "").strip("/")
    organization_id = version.organization_id
    if kind == HarnessAgentVersionRuntime.SourceKind.GITHUB:
        repository = str(source.get("repository") or "").lower()
        commit = str(source.get("commit_sha") or "").lower()
        if not repository or not commit:
            raise _no_code()
        return VersionCode(
            identity=f"github:{organization_id}:{repository}:{commit}",
            subfolder=subfolder,
            load=lambda: stored_commit_archive(
                organization_id=organization_id,
                repository=repository,
                commit_sha=commit,
            ),
        )
    if kind == HarnessAgentVersionRuntime.SourceKind.ARCHIVE:
        artifact = str(source.get("archive_artifact_id") or "")
        if not artifact:
            raise _no_code()
        return VersionCode(
            identity=f"archive:{organization_id}:{artifact}",
            subfolder=subfolder,
            load=lambda: _read_object(_source_object_key(organization_id, artifact)),
        )
    raise _no_code()


def safe_path(raw: str) -> str:
    """A path relative to the archive's ``source/`` root, or ``path_outside_archive``."""
    candidate = str(raw or "").strip()
    if (
        not candidate
        or candidate.startswith("/")
        or "\\" in candidate
        or any(part in ("", ".", "..") for part in candidate.split("/"))
    ):
        raise HostedHarnessError(
            "path_outside_archive",
            "the path must name a file inside the version's code",
            status_code=400,
            details={"path": raw},
        )
    return candidate


def _kind(head: bytes, size: int) -> str:
    if size > MAX_FILE_BYTES:
        return KIND_TOO_LARGE
    if b"\x00" in head:
        return KIND_BINARY
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        # A multi-byte character cut at the sniff boundary is still text.
        if len(head) < _SNIFF_BYTES:
            return KIND_BINARY
        try:
            head[:-4].decode("utf-8")
        except UnicodeDecodeError:
            return KIND_BINARY
    return KIND_TEXT


def archive_index(code: VersionCode) -> dict[str, Entry]:
    """Every regular file under ``source/``, by path relative to it. Cached by the
    archive's identity, which is known before any download, so a tree or a diff
    on a known archive never touches storage; the bytes are read once per miss."""
    key = f"agent-code:index:{code.identity}"
    cached = cache.get(key)
    if cached is not None:
        return {path: Entry(**entry) for path, entry in cached.items()}
    entries: dict[str, Entry] = {}
    with tarfile.open(fileobj=io.BytesIO(code.archive), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.startswith(_ROOT):
                continue
            path = member.name[len(_ROOT) :]
            if not path or any(part in ("", ".", "..") for part in path.split("/")):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            head = handle.read(_SNIFF_BYTES)
            digest = hashlib.sha256(head)
            for chunk in iter(partial(handle.read, 1 << 20), b""):
                digest.update(chunk)
            entries[path] = Entry(
                path=path,
                size=member.size,
                kind=_kind(head, member.size),
                sha256=digest.hexdigest(),
            )
    cache.set(
        key, {path: entry.__dict__ for path, entry in entries.items()}, _CACHE_SECONDS
    )
    return entries


def _within(path: str, subfolder: str) -> bool:
    return not subfolder or path == subfolder or path.startswith(subfolder + "/")


def tree(version: AgentVersion) -> dict[str, Any]:
    code = version_code(version)
    files = [
        {"path": entry.path, "size": entry.size, "kind": entry.kind}
        for entry in sorted(archive_index(code).values(), key=lambda one: one.path)
        if _within(entry.path, code.subfolder)
    ]
    return {
        "subfolder": code.subfolder,
        "files": files[:MAX_TREE_ENTRIES],
        "truncated": len(files) > MAX_TREE_ENTRIES,
    }


def read_file(version: AgentVersion, raw_path: str) -> dict[str, Any]:
    path = safe_path(raw_path)
    code = version_code(version)
    entry = archive_index(code).get(path)
    if entry is None:
        raise HostedHarnessError(
            "file_not_found", "no such file in this version", status_code=404
        )
    language = _LANGUAGES.get(posixpath.splitext(path)[1].lower(), "plaintext")
    if entry.kind != KIND_TEXT:
        return {
            "path": path,
            "size": entry.size,
            "kind": entry.kind,
            "language": language,
        }
    with tarfile.open(fileobj=io.BytesIO(code.archive), mode="r:gz") as tar:
        handle = tar.extractfile(_ROOT + path)
        content = handle.read().decode("utf-8") if handle is not None else ""
    return {
        "path": path,
        "size": entry.size,
        "kind": KIND_TEXT,
        "language": language,
        "content": content,
    }


def previous_version(version: AgentVersion) -> AgentVersion | None:
    """The version before this one by number; what ``changes`` compares against
    unless told otherwise."""
    return (
        AgentVersion.no_workspace_objects.filter(
            agent_definition_id=version.agent_definition_id,
            version_number__lt=version.version_number or 0,
        )
        .order_by("-version_number")
        .first()
    )


def changes(version: AgentVersion, against: AgentVersion | None) -> dict[str, Any]:
    """Files added, removed or modified in ``version`` since ``against``, under the
    version's folder, by each file's content hash; cached by the pair."""
    code = version_code(version)
    if against is None:
        return {"against": None, "files": [], "unavailable": "no_previous_version"}
    try:
        base = version_code(against)
    except HostedHarnessError as exc:
        if exc.code != "no_code":
            raise
        return {"against": str(against.id), "files": [], "unavailable": "no_code"}
    key = f"agent-code:changes:{base.identity}:{code.identity}:{code.subfolder}"
    cached = cache.get(key)
    if cached is not None:
        return {"against": str(against.id), "files": cached}
    before = {
        path: entry
        for path, entry in archive_index(base).items()
        if _within(path, code.subfolder)
    }
    after = {
        path: entry
        for path, entry in archive_index(code).items()
        if _within(path, code.subfolder)
    }
    files = [
        {"path": path, "status": "added"} for path in sorted(set(after) - set(before))
    ]
    files += [
        {"path": path, "status": "removed"} for path in sorted(set(before) - set(after))
    ]
    files += [
        {"path": path, "status": "modified"}
        for path in sorted(set(before) & set(after))
        if before[path].sha256 != after[path].sha256
    ]
    files.sort(key=lambda one: one["path"])
    cache.set(key, files, _CACHE_SECONDS)
    return {"against": str(against.id), "files": files}
