"""Public entry shapes (SPEC §9.2). Only approved Version data; never a submission or a draft."""

from __future__ import annotations

from omega_hub import conf
from omega_hub.models import Entry, Like, Pick, Version
from omega_hub.services import counts
from omega_hub.services.community import monday

API = "/omega-hub/v1"


def publisher_brief(publisher) -> dict:
    return {"handle": publisher.handle, "displayName": publisher.display_name, "tier": publisher.tier}


def remix_ref(version: Version | None) -> str | None:
    if version is None:
        return None
    if version.remix_of_id:
        target = version.remix_of
        return f"{target.entry.publisher.handle}/{target.entry.name}@{target.version}"
    remix = version.manifest.get("remix_of")
    return remix if isinstance(remix, str) and "/" in remix else None


def public_manifest(manifest: dict) -> dict:
    """The manifest without the publisher's draft disclosure: only the confirmed text is public."""
    return {key: value for key, value in manifest.items() if key not in ("disclosure", "$schema")}


def picks_this_week(entry_ids) -> dict:
    return {
        pick.entry_id: {"week": pick.week.isoformat(), "why": pick.why}
        for pick in Pick.objects.filter(week=monday(), entry_id__in=list(entry_ids))
    }


def listing_version(entry: Entry) -> Version | None:
    """The version whose title, tagline and cover the entry shows: the latest installable one, or, when
    every version is yanked, the newest yanked one (the page still exists; exact versions still install)."""
    if entry.latest_version is not None:
        return entry.latest_version
    return entry.versions.filter(status=Version.Status.YANKED).order_by("-semver_key").first()


def card(entry: Entry, *, installs30d: int, pick=None, liked: bool | None = None) -> dict:
    latest = entry.latest_version
    shown = listing_version(entry)
    manifest = shown.manifest if shown else {}
    out = {
        "ref": f"{entry.publisher.handle}/{entry.name}",
        "publisher": publisher_brief(entry.publisher),
        "name": entry.name,
        "kind": entry.kind,
        "title": manifest.get("title"),
        "tagline": manifest.get("tagline"),
        "cover": manifest.get("cover"),
        "tags": list(entry.tags),
        "latestVersion": latest.version if latest else None,
        "likes": entry.like_count,
        "installs30d": installs30d,
        "noKeyNeeded": entry.no_key_needed,
        "pick": pick,
        "remixOf": remix_ref(latest),
        "ranked": entry.ranked,
    }
    if liked is not None:
        out["likedByMe"] = liked
    return out


def cards(entries, user=None) -> list[dict]:
    ids = [entry.pk for entry in entries]
    installs = counts.downloads_by_entry(30, entry_ids=ids)
    picks = picks_this_week(ids)
    liked = None
    if user is not None and getattr(user, "is_authenticated", False) and getattr(user, "pk", None):
        liked = set(Like.objects.filter(user=user, entry_id__in=ids).values_list("entry_id", flat=True))
    return [
        card(entry, installs30d=installs.get(entry.pk, 0), pick=picks.get(entry.pk),
             liked=(entry.pk in liked) if liked is not None else None)
        for entry in entries
    ]


def version_row(version: Version) -> dict:
    return {
        "version": version.version,
        "status": version.status,
        "publishedAt": version.approved_at.isoformat(),
        "notes": version.notes,
        "yankedReason": version.yanked_reason or None,
        "digest": version.digest,
        "rulesVersion": version.rules_version,
    }


def version_detail(version: Version) -> dict:
    entry = version.entry
    return {
        "ref": f"{entry.publisher.handle}/{entry.name}@{version.version}",
        "version": version.version,
        "manifest": public_manifest(version.manifest),
        "readme": version.readme,
        "files": version.files,
        "digest": version.digest,
        "bundleSha256": version.bundle_sha256,
        "bundleBytes": version.bundle_bytes,
        "capabilities": version.capabilities,
        "checkSummary": version.check_summary,
        "disclosure": version.disclosure,
        "rulesVersion": version.rules_version,
        "sourceCommit": version.source_commit,
        "status": version.status,
        "yankedReason": version.yanked_reason or None,
        "remixOf": remix_ref(version),
        "notes": version.notes,
        "approvedAt": version.approved_at.isoformat(),
        "publishedAt": version.approved_at.isoformat(),
        "hasCast": bool(version.cast_key),
    }


def entry_detail(entry: Entry, user=None) -> dict:
    card_data = cards([entry], user)[0]
    installs = counts.downloads_by_entry(30, entry_ids=[entry.pk]).get(entry.pk, 0)
    opens = counts.downloads_by_entry(30, sources=counts.OPEN_SOURCES, entry_ids=[entry.pk]).get(entry.pk, 0)
    from omega_hub.services.search import remixes_of

    return {
        **card_data,
        "unrankedReason": entry.unranked_reason or None,
        "firstPublishedAt": entry.first_published_at.isoformat() if entry.first_published_at else None,
        "installs30d": installs,
        "opens30d": opens,
        "installsDaily30": counts.daily_series([entry.pk]).get(entry.pk, [0] * 30),
        "remixCount": len(remixes_of(entry)),
        "versionCount": entry.versions.exclude(status=Version.Status.REMOVED).count(),
        "latest": version_detail(entry.latest_version) if entry.latest_version else None,
        "discord": conf.DISCORD_URL,
        "appUrl": f"{conf.app_url()}/{entry.publisher.handle}/{entry.name}",
    }


def tombstone(entry: Entry) -> dict:
    category = entry.removed_category or "publisher's request"
    return {"tombstone": {"ref": f"{entry.publisher.handle}/{entry.name}", "category": category,
                          "at": entry.removed_at.isoformat() if entry.removed_at else None}}
