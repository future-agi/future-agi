"""The blocklist (SPEC §3.4, §9.5 `omega-hub-blocklist/v1`). Public rows never carry the note."""

from __future__ import annotations

import hashlib
import json
import re

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from omega_hub.errors import HubError
from omega_hub.models import BlockedDigest, Entry
from omega_hub.services import audit, cache_keys

SCHEMA = "omega-hub-blocklist/v1"
CATEGORIES = ("security issue", "licence issue", "breaks Hub rules", "publisher's request")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def check_category(category) -> str:
    if category not in CATEGORIES:
        raise HubError("OMEGA_HUB_INVALID", 400, f"category is one of: {', '.join(CATEGORIES)}")
    return category


def block_entry(entry: Entry, user, category: str, note: str) -> int:
    rows = [
        BlockedDigest(digest=version.digest, bundle_sha256=version.bundle_sha256, entry=entry, version=version,
                      category=category, note=note, added_by=user)
        for version in entry.versions.all()
    ]
    BlockedDigest.objects.bulk_create(rows)
    audit.record("blocklist.added", target_type="entry", target_id=entry.pk, actor=user, publisher=entry.publisher,
                 data={"ref": entry.ref, "versions": [row.version.version for row in rows], "category": category})
    cache_keys.invalidate_blocklist()
    return len(rows)


def lift_entry(entry: Entry, user) -> int:
    rows = list(BlockedDigest.objects.filter(entry=entry, lifted_at__isnull=True))
    now = timezone.now()
    for row in rows:
        row.lifted_at, row.lifted_by = now, user
        row.save(update_fields=["lifted_at", "lifted_by"])
    if rows:
        audit.record("blocklist.lifted", target_type="entry", target_id=entry.pk, actor=user,
                     publisher=entry.publisher, data={"ref": entry.ref, "rows": len(rows)})
        cache_keys.invalidate_blocklist()
    return len(rows)


@transaction.atomic
def add_bare(user, *, digest=None, bundle_sha256=None, category, note) -> BlockedDigest:
    check_category(category)
    if digest is None and bundle_sha256 is None:
        raise HubError("OMEGA_HUB_INVALID", 400, "give digest or bundleSha256")
    if digest is not None and not DIGEST.match(str(digest)):
        raise HubError("OMEGA_HUB_INVALID", 400, "digest is sha256:<64 hex>")
    if bundle_sha256 is not None and not SHA256.match(str(bundle_sha256)):
        raise HubError("OMEGA_HUB_INVALID", 400, "bundleSha256 is 64 hex characters")
    if not isinstance(note, str) or not note.strip():
        raise HubError("OMEGA_HUB_INVALID", 400, "a note is required")
    row = BlockedDigest.objects.create(digest=digest, bundle_sha256=bundle_sha256, category=category,
                                       note=note.strip(), added_by=user)
    audit.record("blocklist.added", target_type="blocked_digest", target_id=row.pk, actor=user,
                 data={"digest": digest, "bundleSha256": bundle_sha256, "category": category})
    cache_keys.invalidate_blocklist()
    return row


@transaction.atomic
def lift(user, block_id) -> BlockedDigest:
    row = BlockedDigest.objects.filter(pk=block_id).first()
    if row is None:
        raise HubError("OMEGA_HUB_NOT_FOUND", 404)
    if row.lifted_at is None:
        row.lifted_at, row.lifted_by = timezone.now(), user
        row.save(update_fields=["lifted_at", "lifted_by"])
        audit.record("blocklist.lifted", target_type="blocked_digest", target_id=row.pk, actor=user,
                     data={"digest": row.digest, "bundleSha256": row.bundle_sha256})
        cache_keys.invalidate_blocklist()
    return row


def is_blocked(digest: str | None = None, bundle_sha256: str | None = None) -> bool:
    active = BlockedDigest.objects.filter(lifted_at__isnull=True)
    return bool(digest and active.filter(digest=digest).exists()) or bool(
        bundle_sha256 and active.filter(bundle_sha256=bundle_sha256).exists())


def build_document() -> dict:
    by_entry: dict[str, dict] = {}
    bare = []
    rows = BlockedDigest.objects.select_related("entry__publisher", "version").filter(lifted_at__isnull=True).order_by("added_at")
    for row in rows:
        if row.entry_id:
            item = by_entry.setdefault(str(row.entry_id), {
                "ref": row.entry.ref, "versions": [], "digests": [], "bundleSha256": [],
                "category": row.category, "at": row.added_at.date().isoformat(),
            })
            if row.version_id:
                item["versions"].append(row.version.version)
            if row.digest:
                item["digests"].append(row.digest)
            if row.bundle_sha256:
                item["bundleSha256"].append(row.bundle_sha256)
        else:
            bare.append({"digest": row.digest, "bundleSha256": row.bundle_sha256, "category": row.category,
                         "at": row.added_at.date().isoformat()})
    latest = rows.order_by("-added_at").first()
    return {"schema": SCHEMA, "generatedAt": (latest.added_at if latest else timezone.now()).replace(microsecond=0).isoformat(),
            "entries": sorted(by_entry.values(), key=lambda item: item["ref"]), "digests": bare}


def document_and_etag() -> tuple[bytes, str]:
    cached = cache.get(cache_keys.BLOCKLIST)
    if cached is None:
        doc = build_document()
        # generatedAt is the newest row's time, so the ETag changes only when the list does.
        body = json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")
        cached = (body, '"' + hashlib.sha256(body).hexdigest()[:32] + '"')
        cache.set(cache_keys.BLOCKLIST, cached, 300)
    return cached
