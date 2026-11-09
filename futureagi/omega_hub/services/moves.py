"""Moves (SPEC §3.4): rename an entry, transfer it, or rename a publisher. Ids never change, so likes,
downloads, picks and remix links follow; the old reference redirects forever."""

from __future__ import annotations

from django.db import transaction

from omega_hub import reserved
from omega_hub.errors import HubError
from omega_hub.models import Entry, EntryRedirect, Publisher, PublisherRedirect
from omega_hub.services import audit, cache_keys, notify
from omega_hub.services.publish import refresh_entry
from omega_hub.services.publishers import check_handle
from omega_hub.services.refs import reference_used


def _note(note) -> str:
    if not isinstance(note, str) or not note.strip():
        raise HubError("OMEGA_HUB_INVALID", 400, "a note is required")
    return note.strip()


@transaction.atomic
def move_entry(user, entry: Entry, *, to_publisher=None, to_name=None, note) -> Entry:
    note = _note(note)
    entry = Entry.objects.select_for_update(of=("self",)).select_related("publisher").get(pk=entry.pk)
    target_publisher = entry.publisher
    if to_publisher not in (None, "", entry.publisher.handle):
        target_publisher = Publisher.objects.filter(handle=to_publisher).first()
        if target_publisher is None:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404, f"no publisher {to_publisher}")
    name = to_name or entry.name
    if not reserved.NAME.match(name):
        raise HubError("OMEGA_HUB_INVALID", 400, "toName is 3-40 lowercase letters, digits and hyphens")
    if target_publisher.pk == entry.publisher_id and name == entry.name:
        raise HubError("OMEGA_HUB_INVALID", 400, "the move changes nothing")
    if reference_used(target_publisher.handle, name):
        raise HubError("OMEGA_HUB_REF_TAKEN", 409)
    old_ref = entry.ref
    EntryRedirect.objects.create(from_publisher_handle=entry.publisher.handle, from_name=entry.name, entry=entry,
                                 moved_by=user, note=note)
    old_publisher = entry.publisher
    entry.publisher, entry.name = target_publisher, name
    entry.save()
    refresh_entry(entry)
    audit.record("entry.moved", target_type="entry", target_id=entry.pk, actor=user, publisher=target_publisher,
                 data={"from": old_ref, "to": entry.ref})
    notify.moderation("moved", publisher=old_publisher, ref=old_ref, reason=f"now {entry.ref}", note=note)
    cache_keys.invalidate_listing()
    return entry


@transaction.atomic
def rename_publisher(user, publisher: Publisher, *, to_handle, note) -> Publisher:
    note = _note(note)
    publisher = Publisher.objects.select_for_update().get(pk=publisher.pk)
    if publisher.tier == Publisher.Tier.OFFICIAL:
        raise HubError("OMEGA_HUB_FORBIDDEN", 403, "the Official publisher keeps its handle")
    check_handle(to_handle)
    old = publisher.handle
    for entry in Entry.objects.filter(publisher=publisher):
        EntryRedirect.objects.create(from_publisher_handle=old, from_name=entry.name, entry=entry, moved_by=user, note=note)
    PublisherRedirect.objects.create(from_handle=old, publisher=publisher)
    publisher.handle = to_handle
    publisher.save(update_fields=["handle", "updated_at"])
    for entry in Entry.objects.filter(publisher=publisher):
        refresh_entry(entry)
    audit.record("publisher.moved", target_type="publisher", target_id=publisher.pk, actor=user, publisher=publisher,
                 data={"from": old, "to": to_handle})
    notify.moderation("renamed", publisher=publisher, ref=old, reason=f"now {to_handle}", note=note)
    cache_keys.invalidate_listing()
    return publisher
