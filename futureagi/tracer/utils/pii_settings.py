"""Identity-scoped privacy policies; unknown policies reject ingestion for retry."""

import structlog
from django.core.cache import cache

from tracer.utils.pii_scrubber import PIIScrubError

logger = structlog.get_logger(__name__)

_CACHE_TTL = 300  # 5 minutes
ProjectIdentity = str | tuple[str, str | None]


def _cache_key(org_id: str, project_id: str, include_modes: bool = False) -> str:
    # Versioned, ID-based namespaces never consume stale name-based policies.
    namespace = "pii_redaction_policy_v2" if include_modes else "pii_redaction_v2"
    return f"{namespace}:{org_id}:{project_id}"


def get_pii_settings_for_projects(
    project_names: set[ProjectIdentity],
    org_id: str,
    *,
    include_modes: bool = False,
    workspace_id=None,
    user_id=None,
    resolved_projects: dict | None = None,
) -> dict[ProjectIdentity, bool | dict]:
    """Resolve exactly the projects ingestion will use, then cache by project ID.

    Ingestion supplies (name, trace_type) identities. Name-only explicit callers
    retain the boolean contract and select the default experiment trace type.
    The ingestion resolver also handles invalid trace types, default workspaces,
    soft deletion and creation of genuinely new (privacy-disabled) projects.
    An unresolved project is NOT a new/disabled project: raise PIIScrubError into
    ingestion's existing bounded reject/retry path before any spans are stored.
    When supplied, resolved_projects is populated by identity so conversion can
    reuse these exact objects, rather than resolving a renamed namesake again.
    """
    from tracer.utils.otel import get_or_create_project

    result: dict[ProjectIdentity, bool | dict] = {}
    for identity in project_names:
        name, trace_type = identity if isinstance(identity, tuple) else (identity, "experiment")
        try:
            project = get_or_create_project(name, org_id, trace_type, user_id, workspace_id)
            if project is None:
                raise PIIScrubError("Project privacy policy could not be resolved.")
            # Legacy JSONField values may be scalars/lists, not privacy settings.
            metadata = project.metadata if isinstance(project.metadata, dict) else {}
            policy = {
                "enabled": bool(metadata.get("pii_redaction_enabled", False)),
                "mode": metadata.get("pii_redaction_mode", "mask_pii"),
            }
            setting = policy if include_modes else policy["enabled"]
            key = _cache_key(org_id, str(project.id), include_modes)
        except Exception as exc:
            logger.warning("pii_settings_lookup_failed")
            raise PIIScrubError("Project privacy policy could not be resolved.") from exc

        # Resolution already fetched authoritative metadata. Cache failures must
        # never discard that policy, and an old cached disabled policy must never
        # override a newer hide_io policy (including failed update invalidation).
        try:
            if cache.get(key) != setting:
                cache.set(key, setting, _CACHE_TTL)
        except Exception:
            logger.warning("pii_settings_cache_unavailable")
        result[identity] = setting
        if resolved_projects is not None:
            resolved_projects[identity] = project
    return result


def invalidate_pii_cache(org_id: str, project_id: str) -> None:
    """Invalidate both cached policy representations for the immutable project ID."""
    cache.delete(_cache_key(org_id, project_id))
    cache.delete(_cache_key(org_id, project_id, include_modes=True))
