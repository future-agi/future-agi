"""Lossless bounded external skill metadata; availability never becomes a load."""

from __future__ import annotations

import json
from urllib.parse import quote, urlsplit
from uuid import UUID

MAX_METADATA_BYTES = 65536


def normalize_langfuse_skills(attributes: dict) -> dict:
    """Call before the Langfuse adapter strips its source namespace.

    Retain external identities and availability as external metadata. No Hub ID is
    inferred from a name, and no skill.load span or receipt is synthesized here.
    """
    kept = {}
    for key, value in attributes.items():
        if key in {
            "langfuse.project.id",
            "langfuse.trace.id",
            "langfuse.observation.id",
        } or key.startswith(("langfuse.skill.", "langfuse.skills.")):
            kept[key] = value
    raw = attributes.get("langfuse.observation.metadata")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, RecursionError):
            raw = None
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key.startswith(("fi.context.", "fi.skill.")) or key in {
                "skills",
                "available_skills",
                "loaded_skills",
            }:
                kept[key] = value
    for key, value in attributes.items():
        prefix = "langfuse.observation.metadata."
        if key.startswith(prefix):
            name = key[len(prefix) :]
            if name.startswith(("fi.context.", "fi.skill.")) or name in {
                "skills",
                "available_skills",
                "loaded_skills",
            }:
                kept[name] = value
    try:
        encoded = json.dumps(kept, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return {"fi.external.langfuse.skills_status": "invalid"}
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        return {"fi.external.langfuse.skills_status": "too_large"}
    if not kept:
        return {}
    return {
        "fi.external.langfuse.skills": kept,
        "fi.external.langfuse.skills_status": "retained",
    }


def hub_runtime_metadata(
    *,
    kind: str,
    resource_id: UUID,
    version_id: UUID,
    version: int,
    digest: str,
    hub_origin: str,
) -> dict:
    """Custom metadata for a new Hub identity, not an old Langfuse skill version."""
    if (
        kind not in {"skill", "agent_context"}
        or type(version) is not int
        or version < 1
    ):
        raise ValueError("Invalid Hub resource identity")
    origin = urlsplit(hub_origin)
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
        or origin.path not in {"", "/"}
    ):
        raise ValueError("Use the configured HTTPS Hub origin")
    import re

    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Invalid Hub digest")
    resource_id, version_id = UUID(str(resource_id)), UUID(str(version_id))
    prefix = "fi.context" if kind == "agent_context" else "fi.skill"
    route = "contexts/" if kind == "agent_context" else ""
    return {
        f"{prefix}.id": str(resource_id),
        f"{prefix}.version_id": str(version_id),
        f"{prefix}.version": version,
        f"{prefix}.digest": digest,
        f"{prefix}.url": f"{hub_origin.rstrip('/')}/dashboard/skills/{route}{resource_id}?version={version}",
    }


def external_trace_link(
    *, origin: str, project_id: str, trace_id: str, observation_id: str
) -> str:
    """Navigation only. Origin is connector-owned, never fetched by the capture API."""
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Invalid connector origin")
    if any(
        not isinstance(value, str) or not value or len(value) > 200
        for value in (project_id, trace_id, observation_id)
    ):
        raise ValueError("Invalid external trace identity")
    return f"{origin.rstrip('/')}/project/{quote(project_id, safe='')}/traces/{quote(trace_id, safe='')}?observation={quote(observation_id, safe='')}"
