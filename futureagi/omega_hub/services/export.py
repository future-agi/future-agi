"""The registry export `omega-hub-registry/v1` (SPEC §9.5) that feeds the docs site.

Listed entries with an approved latest version only. No likers, reporter, reviewer, private note or
unconfirmed disclosure. Cached 5 minutes; the ETag is a hash of the body without `generatedAt`.
"""

from __future__ import annotations

import hashlib
import json

from django.core.cache import cache
from django.utils import timezone

from omega_hub import conf, rules
from omega_hub.api.serializers.cards import picks_this_week, remix_ref
from omega_hub.api.serializers.publishers import avatar_url
from omega_hub.models import Entry, EntryRedirect, Pick, Publisher, Version
from omega_hub.services import cache_keys, counts
from omega_hub.services.search import publisher_totals, remixes_of

SCHEMA = "omega-hub-registry/v1"


def _date(value):
    return value.date().isoformat() if value else None


def _entry(entry: Entry, stats: dict, pick) -> dict:
    latest = entry.latest_version
    manifest = latest.manifest
    versions = entry.versions.exclude(status=Version.Status.REMOVED).order_by("-semver_key")
    return {
        "ref": f"{entry.publisher.handle}/{entry.name}", "publisher": entry.publisher.handle, "name": entry.name,
        "kind": entry.kind, "title": manifest.get("title"), "tagline": manifest.get("tagline"),
        "cover": manifest.get("cover"), "tags": list(entry.tags), "difficulty": manifest.get("difficulty"),
        "license": manifest.get("license"), "tier": entry.publisher.tier, "vendors": list(entry.vendors),
        "noKeyNeeded": entry.no_key_needed, "requires": manifest.get("requires") or {},
        "likes": entry.like_count, "installs30d": stats["installs30"].get(entry.pk, 0),
        "installs7d": stats["installs7"].get(entry.pk, 0), "likes7d": stats["likes7"].get(entry.pk, 0),
        "opens30d": stats["opens30"].get(entry.pk, 0), "installsDaily30": stats["daily"].get(entry.pk, [0] * 30),
        "remixOf": remix_ref(latest), "remixCount": len(remixes_of(entry)), "pick": pick,
        "ranked": entry.ranked, "unrankedReason": entry.unranked_reason or None,
        "firstPublishedAt": _date(entry.first_published_at),
        "latest": {
            "version": latest.version, "digest": latest.digest, "bundleSha256": latest.bundle_sha256,
            "publishedAt": latest.approved_at.isoformat(), "approvedAt": latest.approved_at.isoformat(),
            "capabilities": latest.capabilities, "checkSummary": latest.check_summary,
            "disclosure": latest.disclosure, "rulesVersion": latest.rules_version,
            "sourceCommit": latest.source_commit,
            "keys": [{"name": key.get("name"), "why": key.get("why"), "get": key.get("get"), "for": key.get("for")}
                     for key in manifest.get("keys") or [] if isinstance(key, dict)],
            "models": manifest.get("models") or {},
        },
        "versions": [{"version": v.version, "publishedAt": v.approved_at.isoformat(),
                      "yanked": v.yanked_reason or None if v.status == Version.Status.YANKED else None}
                     for v in versions],
    }


def build() -> dict:
    entries = list(Entry.objects.select_related("publisher", "latest_version__remix_of__entry__publisher").filter(
        status=Entry.Status.LISTED, latest_version__isnull=False).order_by("publisher__handle", "name"))
    ids = [entry.pk for entry in entries]
    stats = {
        "installs30": counts.downloads_by_entry(30, entry_ids=ids), "installs7": counts.downloads_by_entry(7, entry_ids=ids),
        "likes7": counts.likes_by_entry(7, entry_ids=ids),
        "opens30": counts.downloads_by_entry(30, sources=counts.OPEN_SOURCES, entry_ids=ids),
        "daily": counts.daily_series(ids),
    }
    picks = picks_this_week(ids)
    publishers = list(Publisher.objects.filter(pk__in={entry.publisher_id for entry in entries}).order_by("handle"))
    totals = publisher_totals(publishers)
    removed = Entry.objects.select_related("publisher").exclude(status=Entry.Status.LISTED).order_by("publisher__handle", "name")
    return {
        "schema": SCHEMA,
        "generatedAt": timezone.now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "registry": conf.public_api_url(),
        "app": conf.app_url(),
        "discord": conf.DISCORD_URL,
        "publishers": [{
            "handle": p.handle, "displayName": p.display_name, "tier": p.tier,
            "verifiedAt": p.verified_at.isoformat() if p.verified_at else None, "avatarUrl": avatar_url(p),
            "bio": p.bio, "website": p.website or None, **totals[p.pk], "firstPublishedAt": _date(p.first_published_at),
        } for p in publishers],
        "entries": [_entry(entry, stats, picks.get(entry.pk)) for entry in entries],
        "picks": [{"ref": pick.entry.ref, "week": pick.week.isoformat(), "why": pick.why}
                  for pick in Pick.objects.select_related("entry__publisher").filter(
                      entry_id__in=ids).order_by("-week", "created_at")],
        "tombstones": [{"ref": entry.ref, "category": entry.removed_category or "publisher's request",
                        "at": entry.removed_at.isoformat() if entry.removed_at else None} for entry in removed],
        "redirects": [{"from": f"{r.from_publisher_handle}/{r.from_name}", "to": r.entry.ref}
                      for r in EntryRedirect.objects.select_related("entry__publisher").order_by("from_publisher_handle", "from_name")],
        "rules": rules.get_rules(),
    }


def body_and_etag() -> tuple[bytes, str]:
    cached = cache.get(cache_keys.EXPORT)
    if cached is None:
        document = build()
        stable = {key: value for key, value in document.items() if key != "generatedAt"}
        etag = '"' + hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:32] + '"'
        body = json.dumps(document, ensure_ascii=False).encode("utf-8")
        cached = (body, etag)
        cache.set(cache_keys.EXPORT, cached, 300)
    return cached
