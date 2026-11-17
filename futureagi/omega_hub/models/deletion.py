"""`on_delete` handlers for the Hub's references to platform accounts and organisations (HUB-09).

The platform hard-deletes members (`accounts/views/workspace_management.py` DeleteUserAPIView) and
can delete organisations. These handlers run inside Django's deletion collector, so every delete
path is covered, including ones added later, and they must not query anything the collector is
about to remove.

- A publisher outlives its owner. Its entries stay listed and installable, because their refs pin
  installs that already exist (SPEC §2.3), so the handle is kept. Everything else that describes the
  person goes: the display name becomes "Deleted account" and the profile fields are cleared.
  `erased_at` is set first, because the one-owner check constraint allows two empty owners only on
  an erased publisher, and the collector applies each field update as its own UPDATE in the order
  it was added.
- A report outlives its reporter (`Report.reporter` is SET_NULL): it is moderation evidence. The
  pseudonym stored when it was filed (`reporter_label`) still ties one account's reports together,
  and `services/erasure.py` removes the reporter's email and name from its text first.
"""

from __future__ import annotations

from django.utils import timezone

DELETED_DISPLAY_NAME = "Deleted account"

_CLEARED = (
    ("display_name", DELETED_DISPLAY_NAME),
    ("bio", ""),
    ("website", ""),
    ("github_login", ""),
    ("avatar_key", ""),
    ("verification_request", None),
)


def erase_publisher_owner(collector, field, sub_objs, using):
    """Tombstone the publishers of a deleted user or organisation instead of refusing the delete."""
    model = field.model
    if not sub_objs:
        return
    # Collection also runs for admin previews and before the refusal boundary.
    # Never schedule storage effects here; pre_delete captures avatars only
    # after the locked closure has passed the common guard.
    collector.add_field_update(
        model._meta.get_field("erased_at"), timezone.now(), sub_objs
    )
    for name, value in _CLEARED:
        collector.add_field_update(model._meta.get_field(name), value, sub_objs)
    collector.add_field_update(field, None, sub_objs)
