"""Publisher shapes (SPEC §6.5, §9.2). No followers, no activity feed, no third-party avatar."""

from __future__ import annotations

from omega_hub import conf


def avatar_url(publisher) -> str | None:
    if not publisher.avatar_key:
        return None
    return f"{conf.public_api_url()}/publishers/{publisher.handle}/avatar"


def profile(publisher, totals: dict | None = None) -> dict:
    totals = totals or {"entryCount": 0, "likes": 0, "installs30d": 0}
    return {
        "handle": publisher.handle,
        "displayName": publisher.display_name,
        "kind": publisher.kind,
        "tier": publisher.tier,
        "bio": publisher.bio,
        "website": publisher.website or None,
        "githubLogin": publisher.github_login or None,
        "avatarUrl": avatar_url(publisher),
        "verifiedAt": publisher.verified_at.isoformat() if publisher.verified_at else None,
        "firstPublishedAt": publisher.first_published_at.date().isoformat() if publisher.first_published_at else None,
        **totals,
    }


def owned(publisher) -> dict:
    """What the publisher's own owners see in /me and settings."""
    return {
        **profile(publisher),
        "id": str(publisher.pk),
        "organizationId": str(publisher.owner_org_id) if publisher.owner_org_id else None,
        "verificationRequested": publisher.verification_request is not None,
        "suspended": publisher.suspended_at is not None,
    }
