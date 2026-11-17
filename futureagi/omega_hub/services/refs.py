"""References, versions and lookups that follow moves (SPEC §2.3, §3.4)."""

from __future__ import annotations

import re

from omega_hub import semver
from omega_hub.errors import HubError, HubMoved
from omega_hub.models import Entry, EntryRedirect, Publisher, PublisherRedirect, Version

# Strict SemVer (omega_hub/semver.py): `\Z`, not `$`, so a trailing newline is refused too.
SEMVER_PATTERN = semver.PATTERN
SEMVER = re.compile(rf"^{SEMVER_PATTERN}\Z")
REF = re.compile(rf"^([a-z][a-z0-9-]{{1,38}})/([a-z][a-z0-9-]{{2,39}})(?:@({SEMVER_PATTERN}))?\Z")


def is_semver(value) -> bool:
    return isinstance(value, str) and SEMVER.match(value) is not None


def semver_key(value: str) -> str:
    match = SEMVER.match(value)
    if not match:
        raise HubError("OMEGA_HUB_INVALID", 400, f"{value!r} is not a plain MAJOR.MINOR.PATCH version")
    return ".".join(part.zfill(6) for part in match.groups())


def parse_ref(value: str) -> tuple[str, str, str | None]:
    match = REF.match(value or "")
    if not match:
        raise HubError("OMEGA_HUB_INVALID", 400, f"{value!r} is not a publisher/name[@version] reference")
    return match.group(1), match.group(2), match.group(3)


def version_taken(entry_filter: dict, version: str | None) -> bool:
    """Whether `version` is already a Version of the entry `entry_filter` names (ORM lookups on Version).

    A strict version is compared by `semver_key`, so a row stored before strict SemVer (`1.0.03`)
    still blocks `1.0.3`; anything else falls back to the exact string.
    """
    if not version:
        return False
    rows = Version.objects.filter(**entry_filter)
    if is_semver(version):
        return rows.filter(semver_key=semver_key(version)).exists()
    return rows.filter(version=version).exists()


def entry_path(handle: str, name: str) -> str:
    return f"/entries/{handle}/{name}"


def publisher_or_moved(handle: str) -> Publisher:
    publisher = Publisher.objects.filter(handle=handle).first()
    if publisher is not None:
        return publisher
    redirect = PublisherRedirect.objects.select_related("publisher").filter(from_handle=handle).first()
    if redirect is not None:
        new = redirect.publisher.handle
        raise HubMoved(old_prefix=f"/publishers/{handle}", new_prefix=f"/publishers/{new}", moved_to=new)
    raise HubError("OMEGA_HUB_NOT_FOUND", 404)


def entry_or_moved(handle: str, name: str) -> Entry:
    """The entry at `handle/name`, following an entry or publisher move with HubMoved (301)."""
    entry = Entry.objects.select_related("publisher", "latest_version").filter(
        publisher__handle=handle, name=name
    ).first()
    if entry is not None:
        return entry
    redirect = EntryRedirect.objects.select_related("entry__publisher").filter(
        from_publisher_handle=handle, from_name=name
    ).first()
    if redirect is None:
        moved_publisher = PublisherRedirect.objects.select_related("publisher").filter(from_handle=handle).first()
        if moved_publisher is not None:
            moved = Entry.objects.filter(publisher=moved_publisher.publisher, name=name).first()
            if moved is not None:
                redirect_entry = moved
            else:
                raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        else:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
    else:
        redirect_entry = redirect.entry
    new_ref = f"{redirect_entry.publisher.handle}/{redirect_entry.name}"
    raise HubMoved(old_prefix=entry_path(handle, name), new_prefix=entry_path(*new_ref.split("/")), moved_to=new_ref)


def reference_used(handle: str, name: str) -> bool:
    """A reference that exists or once existed is never reused (SPEC §2.3)."""
    return (
        Entry.objects.filter(publisher__handle=handle, name=name).exists()
        or EntryRedirect.objects.filter(from_publisher_handle=handle, from_name=name).exists()
    )


def handle_used(handle: str) -> bool:
    return (
        Publisher.objects.filter(handle=handle).exists()
        or PublisherRedirect.objects.filter(from_handle=handle).exists()
        or EntryRedirect.objects.filter(from_publisher_handle=handle).exists()
    )


def moved_to_for(handle: str, name: str) -> str | None:
    redirect = EntryRedirect.objects.select_related("entry__publisher").filter(
        from_publisher_handle=handle, from_name=name
    ).first()
    if redirect is None:
        return None
    return f"{redirect.entry.publisher.handle}/{redirect.entry.name}"


def approved_version(handle: str, name: str, version: str) -> Version | None:
    return Version.objects.select_related("entry__publisher").filter(
        entry__publisher__handle=handle, entry__name=name, version=version,
        status=Version.Status.APPROVED, entry__status=Entry.Status.LISTED,
    ).first()


def newest_live_version(entry: Entry) -> Version | None:
    """The highest approved, not yanked version: the entry's `latest`.

    Ties on `semver_key` exist only between rows stored before strict SemVer (`1.0.3` and `1.0.03`);
    the most recently approved one wins, so `latest` never flips between them.
    """
    return entry.versions.filter(status=Version.Status.APPROVED).order_by("-semver_key", "-approved_at", "-pk").first()
