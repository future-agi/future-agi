"""Whether the platform GitHub App may reach a repository through an installation id.

Nothing yet records which organization installed the platform's GitHub App on which
GitHub account. Where one App serves several organizations, any of them could name
another's installation id, so private repositories reached through an installation
are refused there. A self-hosted install with one organization that configured its
own App keeps them, and without the App configured nothing changes.
"""

from __future__ import annotations

from django.conf import settings

from tfc.ee_loader import is_cloud_env

PRIVATE_GITHUB_UNAVAILABLE_MESSAGE = (
    "Private GitHub repositories are not available on Future AGI cloud. "
    "Use a public repository or upload the code."
)


def _live_org_ids() -> list:
    """At most two organization ids: enough to tell one organization from several.

    Organization has no soft-delete flag, so every row is a live organization.
    """
    from accounts.models import Organization

    return list(Organization.objects.values_list("id", flat=True)[:2])


def platform_github_app_refused() -> bool:
    """Refuse when the platform GitHub App is configured on a multi-tenant deployment.

    Multi-tenant means a Future AGI Cloud deployment (``CLOUD_DEPLOYMENT``) or two or
    more organizations in the database. Asked afresh on every call, never cached; if
    the organizations cannot be read, it refuses.
    """
    app_id = getattr(settings, "GITHUB_APP_ID", "")
    private_key = getattr(settings, "GITHUB_APP_PRIVATE_KEY", "")
    if not app_id or not private_key:
        return False
    if is_cloud_env(getattr(settings, "CLOUD_DEPLOYMENT", "") or ""):
        return True
    try:
        return len(_live_org_ids()) >= 2
    except Exception:
        return True
