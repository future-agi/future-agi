"""One explicit selector policy and lossless, bounded text-file reads."""

from __future__ import annotations

import json

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.skills_hub import HubSkillVersion
from model_hub.selectors.skills_hub import scoped_skill, skills_for_request_scope
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.limits import DEFAULT_CHUNK_BYTES, MAX_CHUNK_BYTES
from model_hub.services.skills_hub.types import ResolvedSkill

# 25 maximum-length Unicode paths plus bounded identity metadata fit below 64 KiB.
MANIFEST_PAGE_SIZE = 25


def resolve(
    *,
    organization: Organization | None,
    workspace: Workspace | None,
    name: str,
    label: str | None = None,
    version: int | None = None,
) -> ResolvedSkill:
    """Resolve a live identity, defaulting to production without any fallback."""
    scope = skills_for_request_scope(organization, workspace)
    if label is not None and version is not None:
        raise SkillsHubError(
            "invalid_selector", "Choose a label or a version, not both.", status=400
        )
    if version is not None and (type(version) is not int or version < 1):
        raise SkillsHubError(
            "invalid_selector", "Choose a positive version number.", status=400
        )
    skill = scope.filter(name=name).first()
    if skill is None:
        raise SkillsHubError("skill_not_found", "Skill not found.", status=404)
    if not skill.latest_version_number:
        raise SkillsHubError(
            "version_not_found", "This skill has not been published.", status=404
        )
    requested: dict[str, str | int]
    if version is not None:
        resolved = skill.versions.filter(number=version).first()
        requested = {"version": version}
    else:
        label = "production" if label is None else label
        requested = {"label": label}
        if label == "latest":
            resolved = skill.versions.order_by("-number").first()
        else:
            pointer = skill.labels.select_related("version").filter(name=label).first()
            if pointer is None:
                raise SkillsHubError("label_not_found", "Label not found.", status=404)
            resolved = skill.versions.filter(pk=pointer.version_id).first()
    if resolved is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    from model_hub.services.skills_hub.v12.resolution_leases import (
        require_distribution_eligibility,
    )

    require_distribution_eligibility(resolved)
    return ResolvedSkill(skill=skill, version=resolved, requested=requested)


def manifest(
    resolved: ResolvedSkill,
    *,
    include_content: bool = False,
    include_frontmatter: bool = True,
    file_offset: int = 0,
    file_limit: int | None = None,
) -> dict:
    """Page an immutable file inventory without truncating individual metadata rows."""
    version = resolved.version
    if file_offset > version.file_count:
        raise SkillsHubError(
            "invalid_selector",
            "The file offset is past the end of this manifest.",
            status=400,
        )
    rows = version.files.order_by("path")
    if include_content:
        rows = rows.select_related("blob")
    end = (
        min(file_offset + file_limit, version.file_count)
        if file_limit is not None
        else version.file_count
    )
    return {
        "skill_id": resolved.skill.pk,
        "name": resolved.skill.name,
        "description": version.frontmatter["description"],
        "version": version.number,
        "version_id": version.pk,
        "digest": version.digest,
        "requested": resolved.requested,
        **({"frontmatter": version.frontmatter} if include_frontmatter else {}),
        "files": [
            {
                "path": row.path,
                "size_bytes": row.size_bytes,
                "sha256": row.sha256,
                **({"content": row.blob.content} if include_content else {}),
            }
            for row in rows[file_offset:end]
        ],
        "file_count": version.file_count,
        "file_offset": file_offset,
        "next_file_offset": end if end < version.file_count else None,
        "created_at": version.created_at,
    }


def read_file_chunk(
    version: HubSkillVersion,
    path: str,
    offset: int = 0,
    max_bytes: int = DEFAULT_CHUNK_BYTES,
) -> dict:
    """Legacy kind-checked read; context callers use the authorized snapshot seam."""
    from model_hub.services.skills_hub.v12.resolution_leases import (
        require_distribution_eligibility,
    )

    scoped_skill(version.skill)
    require_distribution_eligibility(version)
    return read_version_file_chunk(version, path, offset, max_bytes)


def read_version_file_chunk(
    version: HubSkillVersion,
    path: str,
    offset: int = 0,
    max_bytes: int = DEFAULT_CHUNK_BYTES,
) -> dict:
    """Kind-neutral byte-bounded reader; caller authorizes the immutable version."""
    if (
        type(offset) is not int
        or offset < 0
        or type(max_bytes) is not int
        or not 1 <= max_bytes <= MAX_CHUNK_BYTES
    ):
        raise SkillsHubError(
            "invalid_selector", "Choose a valid offset and chunk size.", status=400
        )
    row = version.files.filter(path=path).select_related("blob").first()
    if row is None:
        raise SkillsHubError(
            "version_not_found", "File not found in this version.", status=404
        )
    content = row.blob.content
    if offset > len(content):
        raise SkillsHubError(
            "invalid_selector", "The offset is past the end of this file.", status=400
        )
    # UTF-8 decoding drops only an incomplete final code point, never a full one.
    text = (
        content[offset : offset + max_bytes]
        .encode("utf-8")[:max_bytes]
        .decode("utf-8", errors="ignore")
    )
    # Escaped newlines/quotes can double JSON size. Reserve 4 KiB for metadata
    # and the API envelope so even a maximum-size chunk fits the MCP boundary.
    if len(json.dumps(text, ensure_ascii=False).encode("utf-8")) > 60 * 1024:
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if (
                len(json.dumps(text[:middle], ensure_ascii=False).encode("utf-8"))
                <= 60 * 1024
            ):
                low = middle
            else:
                high = middle - 1
        text = text[:low]
    if not text and offset < len(content):
        raise SkillsHubError(
            "invalid_selector",
            "The chunk size cannot fit the next character.",
            status=400,
        )
    end = offset + len(text)
    return {
        "path": row.path,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "total_chars": len(content),
        "offset": offset,
        "next_offset": end if end < len(content) else None,
        "eof": end == len(content),
        "text": text,
    }
