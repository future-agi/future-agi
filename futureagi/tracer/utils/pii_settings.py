"""
Per-project PII redaction settings with Django cache.

Fail-open: if lookup fails, default to False (don't block ingestion).
"""

import structlog
from django.core.cache import cache

from tracer.models.project import Project

logger = structlog.get_logger(__name__)

_CACHE_TTL = 300  # 5 minutes


def _cache_key(org_id: str, project_name: str) -> str:
    return f"pii_redaction:{org_id}:{project_name}"


def get_pii_settings_for_projects(
    project_names: set[str],
    org_id: str,
) -> dict[str, bool]:
    """Return a mapping of project_name → pii_redaction_enabled.

    Checks Django cache first, bulk-queries DB for cache misses.
    """
    result: dict[str, bool] = {}
    uncached: set[str] = set()

    # 1. Check cache
    for name in project_names:
        cached = cache.get(_cache_key(org_id, name))
        if cached is not None:
            result[name] = cached
        else:
            uncached.add(name)

    if not uncached:
        return result

    # 2. Bulk DB lookup for cache misses
    try:
        rows = Project.objects.filter(
            name__in=uncached,
            organization_id=org_id,
        ).values("name", "metadata")
        for row in rows:
            name = row["name"]
            metadata = row.get("metadata") or {}
            enabled = bool(metadata.get("pii_redaction_enabled", False))
            result[name] = enabled
            cache.set(_cache_key(org_id, name), enabled, _CACHE_TTL)
            uncached.discard(name)

        # Projects not found in DB → default False, cache that too
        for name in uncached:
            result[name] = False
            cache.set(_cache_key(org_id, name), False, _CACHE_TTL)

    except Exception:
        logger.warning("pii_settings_lookup_failed", exc_info=True)
        # Fail-open: default to False for all uncached projects
        for name in uncached:
            result[name] = False

    return result


def invalidate_pii_cache(org_id: str, project_name: str) -> None:
    """Invalidate cached PII setting for a single project."""
    cache.delete(_cache_key(org_id, project_name))
