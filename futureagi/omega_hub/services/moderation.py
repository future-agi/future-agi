"""Graded exits (SPEC §3.3, §5.5): yank, unlist, unrank, remove, restore, suspend, verify."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from omega_hub.errors import HubError
from omega_hub.models import Entry, Publisher, Version
from omega_hub.services import audit, blocklist, cache_keys, notify, tokens
from omega_hub.services.publish import refresh_entry

UNRANK_REASONS = ("rules change not yet met", "quality", "misleading listing", "under investigation", "other")


def text(value, name: str, low: int = 1, high: int = 2000) -> str:
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        raise HubError("OMEGA_HUB_INVALID", 400, f"{name} is {low}-{high} characters")
    return value.strip()


@transaction.atomic
def yank_version(user, version: Version, reason, *, by_maintainer: bool) -> Version:
    reason = text(reason, "reason", 4, 500)
    version = Version.objects.select_for_update(of=("self",)).select_related("entry__publisher").get(pk=version.pk)
    if version.status != Version.Status.APPROVED:
        raise HubError("OMEGA_HUB_BAD_STATE", 409, f"version is {version.status}")
    version.status = Version.Status.YANKED
    version.yanked_reason, version.yanked_by, version.yanked_at = reason, user, timezone.now()
    version.save()
    refresh_entry(version.entry)
    audit.record("version.yanked", target_type="version", target_id=version.pk, actor=user,
                 publisher=version.entry.publisher,
                 data={"ref": f"{version.entry.ref}@{version.version}", "reason": reason, "byMaintainer": by_maintainer})
    if by_maintainer:
        notify.moderation("yanked", publisher=version.entry.publisher, ref=f"{version.entry.ref}@{version.version}", reason=reason)
    return version


@transaction.atomic
def unlist_entry(user, entry: Entry, note: str = "") -> Entry:
    entry = Entry.objects.select_for_update(of=("self",)).select_related("publisher").get(pk=entry.pk)
    if entry.status != Entry.Status.LISTED:
        raise HubError("OMEGA_HUB_BAD_STATE", 409, f"entry is {entry.status}")
    entry.status = Entry.Status.UNLISTED
    entry.removed_category, entry.removed_at = "publisher's request", timezone.now()
    entry.save()
    audit.record("entry.unlisted", target_type="entry", target_id=entry.pk, actor=user, publisher=entry.publisher,
                 data={"ref": entry.ref, "note": (note or "")[:500]})
    cache_keys.invalidate_listing()
    return entry


@transaction.atomic
def unrank(user, entry: Entry, reason, note) -> Entry:
    if reason not in UNRANK_REASONS:
        raise HubError("OMEGA_HUB_INVALID", 400, f"reason is one of: {', '.join(UNRANK_REASONS)}")
    note = text(note, "note")
    entry = Entry.objects.select_for_update(of=("self",)).select_related("publisher").get(pk=entry.pk)
    if not entry.ranked:
        raise HubError("OMEGA_HUB_BAD_STATE", 409, "already unranked")
    entry.ranked = False
    entry.unranked_reason, entry.unranked_note, entry.unranked_by, entry.unranked_at = reason, note, user, timezone.now()
    entry.save()
    audit.record("entry.unranked", target_type="entry", target_id=entry.pk, actor=user, publisher=entry.publisher,
                 data={"ref": entry.ref, "reason": reason})
    notify.moderation("not ranked", publisher=entry.publisher, ref=entry.ref, reason=reason)
    cache_keys.invalidate_listing()
    return entry


@transaction.atomic
def rerank(user, entry: Entry, note) -> Entry:
    note = text(note, "note")
    entry = Entry.objects.select_for_update(of=("self",)).select_related("publisher").get(pk=entry.pk)
    if entry.ranked:
        raise HubError("OMEGA_HUB_BAD_STATE", 409, "already ranked")
    entry.ranked = True
    entry.unranked_reason = entry.unranked_note = ""
    entry.save()
    audit.record("entry.reranked", target_type="entry", target_id=entry.pk, actor=user, publisher=entry.publisher,
                 data={"ref": entry.ref})
    cache_keys.invalidate_listing()
    return entry


@transaction.atomic
def remove_entry(user, entry: Entry, category, note, *, notify_publisher: bool = True) -> Entry:
    category = blocklist.check_category(category)
    note = text(note, "note")
    entry = Entry.objects.select_for_update(of=("self",)).select_related("publisher").get(pk=entry.pk)
    if entry.status == Entry.Status.REMOVED:
        raise HubError("OMEGA_HUB_BAD_STATE", 409, "already removed")
    entry.status = Entry.Status.REMOVED
    entry.removed_category, entry.removed_note, entry.removed_by, entry.removed_at = category, note, user, timezone.now()
    entry.save()
    entry.versions.update(status=Version.Status.REMOVED)
    audit.record("entry.removed", target_type="entry", target_id=entry.pk, actor=user, publisher=entry.publisher,
                 data={"ref": entry.ref, "category": category})
    blocklist.block_entry(entry, user, category, note)
    if notify_publisher:
        notify.moderation("removed", publisher=entry.publisher, ref=entry.ref, reason=category)
    cache_keys.invalidate_listing()
    return entry


@transaction.atomic
def restore_entry(user, entry: Entry, note) -> Entry:
    note = text(note, "note")
    entry = Entry.objects.select_for_update(of=("self",)).select_related("publisher").get(pk=entry.pk)
    if entry.status == Entry.Status.LISTED:
        raise HubError("OMEGA_HUB_BAD_STATE", 409, "the entry is listed")
    if entry.publisher.suspended_at is not None:
        raise HubError("OMEGA_HUB_PUBLISHER_SUSPENDED", 409, "unsuspend the publisher first")
    for version in entry.versions.all():
        version.status = Version.Status.YANKED if version.yanked_at else Version.Status.APPROVED
        version.save(update_fields=["status", "updated_at"])
    entry.status = Entry.Status.LISTED
    entry.removed_category = entry.removed_note = ""
    entry.removed_at = None
    entry.save()
    refresh_entry(entry)
    audit.record("entry.restored", target_type="entry", target_id=entry.pk, actor=user, publisher=entry.publisher,
                 data={"ref": entry.ref})
    blocklist.lift_entry(entry, user)
    return entry


@transaction.atomic
def suspend_publisher(user, publisher: Publisher, note) -> Publisher:
    note = text(note, "note")
    publisher = Publisher.objects.select_for_update().get(pk=publisher.pk)
    if publisher.tier == Publisher.Tier.OFFICIAL:
        raise HubError("OMEGA_HUB_FORBIDDEN", 403, "the Official publisher cannot be suspended")
    if publisher.suspended_at is not None:
        raise HubError("OMEGA_HUB_BAD_STATE", 409, "already suspended")
    publisher.suspended_at, publisher.suspended_reason = timezone.now(), note
    publisher.save()
    audit.record("publisher.suspended", target_type="publisher", target_id=publisher.pk, actor=user,
                 publisher=publisher, data={"handle": publisher.handle})
    for entry in Entry.objects.filter(publisher=publisher).exclude(status=Entry.Status.REMOVED):
        remove_entry(user, entry, "breaks Hub rules", note, notify_publisher=False)
    tokens.revoke_all_for_publisher(publisher, user)
    notify.moderation("publisher suspended", publisher=publisher, ref=publisher.handle, reason="breaks Hub rules")
    return publisher


@transaction.atomic
def unsuspend_publisher(user, publisher: Publisher, note) -> Publisher:
    text(note, "note")
    publisher = Publisher.objects.select_for_update().get(pk=publisher.pk)
    if publisher.suspended_at is None:
        raise HubError("OMEGA_HUB_BAD_STATE", 409, "not suspended")
    publisher.suspended_at, publisher.suspended_reason = None, ""
    publisher.save()
    audit.record("publisher.unsuspended", target_type="publisher", target_id=publisher.pk, actor=user,
                 publisher=publisher, data={"handle": publisher.handle})
    return publisher


@transaction.atomic
def set_verified(user, publisher: Publisher, verified: bool, note) -> Publisher:
    note = text(note, "note")
    publisher = Publisher.objects.select_for_update().get(pk=publisher.pk)
    if publisher.tier == Publisher.Tier.OFFICIAL:
        raise HubError("OMEGA_HUB_FORBIDDEN", 403, "the Official tier is set by the seed only")
    publisher.tier = Publisher.Tier.VERIFIED if verified else Publisher.Tier.COMMUNITY
    publisher.verified_at = timezone.now() if verified else None
    publisher.verified_by = user if verified else None
    publisher.verification_note = note
    publisher.verification_request = None
    publisher.save()
    audit.record("publisher.verified" if verified else "publisher.unverified", target_type="publisher",
                 target_id=publisher.pk, actor=user, publisher=publisher, data={"handle": publisher.handle})
    notify.moderation("verified publisher" if verified else "verification removed", publisher=publisher,
                      ref=publisher.handle, note=note)
    cache_keys.invalidate_listing()
    return publisher
