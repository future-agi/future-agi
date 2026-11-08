"""Kind-aware immutable snapshot reads above the common version/file backbone."""

from __future__ import annotations

import json

from model_hub.models.skills_hub import HubSkillVersion
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12.limits import DESCRIPTOR_PATH


def version_definition(version: HubSkillVersion) -> dict:
    if version.skill.kind == "skill":
        return {
            "kind": "skill",
            "name": version.skill.name,
            "description": version.frontmatter["description"],
            "entrypoint": "SKILL.md",
            "descriptor_path": None,
        }
    if version.skill.kind != "agent_context":
        raise SkillsHubError(
            "unsupported_dependency_kind", "Unsupported resource kind.", status=400
        )
    row = version.files.filter(path=DESCRIPTOR_PATH).select_related("blob").first()
    try:
        descriptor = json.loads(row.blob.content) if row is not None else None
    except (TypeError, ValueError):
        descriptor = None
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("schema") != 1
        or descriptor.get("kind") != "agent_context"
        or descriptor.get("name") != version.skill.name
        or descriptor.get("entrypoint") != "AGENTS.md"
        or not isinstance(descriptor.get("description"), str)
    ):
        raise SkillsHubError(
            "invalid_context_definition",
            "The retained context descriptor is invalid.",
            status=422,
        )
    return {**descriptor, "descriptor_path": DESCRIPTOR_PATH}


def snapshot_version(version: HubSkillVersion) -> dict:
    """Caller has authorized scope/distribution. Never substitutes current labels."""
    definition = version_definition(version)
    return {
        "subject_kind": version.skill.kind,
        "harness": (
            "contexts-text-v1.2"
            if version.skill.kind == "agent_context"
            else "skills-text-v1.1"
        ),
        "skill_id": str(version.skill_id),
        "version_id": str(version.pk),
        "version": version.number,
        "digest": version.digest,
        "name": definition["name"],
        "description": definition["description"],
        "entrypoint": definition["entrypoint"],
        "files": [
            {"path": row.path, "content": row.blob.content}
            for row in version.files.select_related("blob").order_by("path")
        ],
    }


def immutable_manifest(
    *,
    version: HubSkillVersion,
    requested: dict,
    file_offset: int = 0,
    file_limit: int = 25,
) -> dict:
    if (
        type(file_offset) is not int
        or type(file_limit) is not int
        or not 0 <= file_offset <= version.file_count
        or not 1 <= file_limit <= 25
    ):
        raise SkillsHubError(
            "invalid_selector", "Use a valid manifest page.", status=400
        )
    definition = version_definition(version)
    dependencies = list(version.hub_skill_context_dependencies.all())
    files = []
    for row in version.files.order_by("path")[file_offset : file_offset + file_limit]:
        origin = {
            "kind": "owned",
            "skill_id": None,
            "version_id": None,
            "source_path": None,
        }
        if row.path == definition["descriptor_path"]:
            origin["kind"] = "generated"
        for dependency in dependencies:
            prefix = dependency.mount.rstrip("/") + "/"
            if row.path.startswith(prefix):
                origin = {
                    "kind": "linked",
                    "skill_id": str(dependency.child_skill_id),
                    "version_id": str(dependency.child_version_id),
                    "source_path": row.path[len(prefix) :],
                }
                break
        files.append(
            {
                "path": row.path,
                "sha256": row.sha256,
                "size_bytes": row.size_bytes,
                "origin": origin,
            }
        )
    end = min(file_offset + file_limit, version.file_count)
    return {
        "resource_id": str(version.skill_id),
        "kind": version.skill.kind,
        "name": definition["name"],
        "description": definition["description"],
        "version": version.number,
        "version_id": str(version.pk),
        "digest": version.digest,
        "requested": requested,
        "entrypoint": definition["entrypoint"],
        "descriptor_path": definition["descriptor_path"],
        "files": files,
        "file_count": version.file_count,
        "file_offset": file_offset,
        "next_file_offset": end if end < version.file_count else None,
        "created_at": version.created_at,
    }
