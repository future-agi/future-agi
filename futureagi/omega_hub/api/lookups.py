"""Object lookups shared by the views: they follow moves, and turn removals into 410 tombstones."""

from __future__ import annotations

from omega_hub.api.serializers.cards import tombstone
from omega_hub.errors import HubError
from omega_hub.models import Entry, Submission, Version
from omega_hub.services import refs


def live_entry(handle: str, name: str) -> Entry:
    entry = refs.entry_or_moved(handle, name)
    if entry.status != Entry.Status.LISTED:
        raise HubError("OMEGA_HUB_GONE", 410, extra=tombstone(entry))
    return entry


def any_entry(handle: str, name: str) -> Entry:
    return refs.entry_or_moved(handle, name)


def public_version(handle: str, name: str, version: str) -> Version:
    """An approved or yanked version of a listed entry, by exact version string."""
    entry = live_entry(handle, name)
    found = entry.versions.select_related("entry__publisher").filter(version=version).first()
    if found is None:
        raise HubError("OMEGA_HUB_NOT_FOUND", 404)
    if found.status == Version.Status.REMOVED:
        raise HubError("OMEGA_HUB_GONE", 410, extra=tombstone(entry))
    return found


def submission(submission_id) -> Submission:
    try:
        found = Submission.objects.select_related("publisher", "submitted_by").filter(pk=submission_id).first()
    except Exception:  # a malformed UUID
        found = None
    if found is None:
        raise HubError("OMEGA_HUB_NOT_FOUND", 404)
    return found
