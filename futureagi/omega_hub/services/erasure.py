"""The Hub erasure service (HUB-09, READINESS E02): what happens to Hub records when a platform
account or organisation is deleted.

The platform deletes members and organisations with `Model.delete()` (for example
`accounts/views/workspace_management.py` DeleteUserAPIView), so the Hub hooks into deletion itself
and every delete path is covered:

- publishers are tombstoned by their `on_delete` handler (`models/deletion.py`): the entries stay
  listed and installable under "Deleted account";
- reports stay as moderation evidence: before the reporter becomes NULL, their email address and
  name are removed from the report text, and the pseudonym stored when the report was filed remains;
- audit rows stay: their actor becomes NULL and the pseudonym written with each row remains;
- likes are deleted with the account and the entries' like counts are recounted;
- Hub tokens and the email verification are deleted with the account;
- stored avatars of tombstoned publishers are deleted from storage after the commit;
- one `account.erased` audit row records what was done, under the account's pseudonym only.

What is kept on purpose: the publisher handle (refs pin existing installs) and the audit rows of
maintainer grants, which name the granted account's email as the accountability record of who could
approve code (SPEC §5.1).
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete, pre_delete
from django.dispatch import receiver

from omega_hub import pseudonym

REDACTED = "[deleted account]"
_LIKED = "_omega_hub_liked_entries"


def forget_avatars(publishers, using) -> None:
    """Delete the stored avatars of publishers being tombstoned, once the deletion commits."""
    keys = [p.avatar_key for p in publishers if p.avatar_key]
    if not keys:
        return

    def remove():
        from omega_hub.services import storage

        store = storage.get_store()
        for key in keys:
            store.delete(key)

    transaction.on_commit(remove, using=using)


def redact(text: str, user) -> str:
    """The text without the account's email address or name, as whole words (case-insensitive)."""
    for value in sorted({user.email or "", user.name or ""}, key=len, reverse=True):
        value = value.strip()
        if len(value) >= 3:
            text = re.sub(
                rf"(?<!\w){re.escape(value)}(?!\w)", REDACTED, text, flags=re.IGNORECASE
            )
    return text


def _redact_reports(user) -> int:
    from omega_hub.models import Report

    reports = list(Report.objects.filter(reporter=user))
    for report in reports:
        changed = {}
        cleaned = redact(report.details, user)
        if cleaned != report.details:
            changed["details"] = cleaned
        if not report.reporter_label:
            changed["reporter_label"] = pseudonym.label_for(user)
        if changed:
            Report.objects.filter(pk=report.pk).update(**changed)
    return len(reports)


def recount_likes(entry_ids, *, using="default") -> None:
    from omega_hub.models import Entry, Like
    from omega_hub.services import cache_keys

    if not entry_ids:
        return
    live = (
        Like.objects.filter(entry_id=OuterRef("pk"))
        .values("entry_id")
        .annotate(n=Count("id"))
        .values("n")
    )
    Entry.objects.filter(pk__in=list(entry_ids)).update(
        like_count=Coalesce(Subquery(live), 0)
    )
    transaction.on_commit(cache_keys.invalidate_listing, using=using)


@receiver(pre_delete, sender=get_user_model(), dispatch_uid="omega_hub_erase_account")
def erase_account(sender, instance, using, **kwargs):
    from omega_hub.models import Like, Publisher
    from omega_hub.services import audit

    label = pseudonym.label_for(instance)
    setattr(
        instance,
        _LIKED,
        set(Like.objects.filter(user=instance).values_list("entry_id", flat=True)),
    )
    publishers = Publisher._base_manager.using(using).filter(owner_user=instance)
    forget_avatars(publishers, using)
    handles = list(publishers.values_list("handle", flat=True))
    reports = _redact_reports(instance)
    if not (handles or reports or getattr(instance, _LIKED)):
        return
    audit.record(
        "account.erased",
        target_type="account",
        target_id=label,
        actor=None,
        actor_kind="system",
        data={
            "account": label,
            "publishers": handles,
            "reports": reports,
            "likes": len(getattr(instance, _LIKED)),
        },
    )


@receiver(post_delete, sender=get_user_model(), dispatch_uid="omega_hub_recount_likes")
def recount_after_erase(sender, instance, using, **kwargs):
    recount_likes(getattr(instance, _LIKED, ()), using=using)


@receiver(
    pre_delete,
    sender="accounts.Organization",
    dispatch_uid="omega_hub_erase_organization",
)
def erase_organization(sender, instance, using, **kwargs):
    from omega_hub.models import Publisher
    from omega_hub.services import audit

    publishers = Publisher._base_manager.using(using).filter(owner_org=instance)
    forget_avatars(publishers, using)
    handles = list(publishers.values_list("handle", flat=True))
    if handles:
        audit.record(
            "organization.erased",
            target_type="organization",
            target_id=pseudonym.label_for(instance),
            actor=None,
            actor_kind="system",
            data={"publishers": handles},
        )
