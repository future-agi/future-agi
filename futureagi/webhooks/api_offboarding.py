"""Offboarding (DESIGN.md sections 7.7 and 9.1, G1): a member who leaves, or drops below the level that may manage an
endpoint, has their enabled endpoints turned off with reason ``creator_removed``, with an audit entry naming the
member. An admin who turns one back on becomes its owner (``api_endpoint_ops.set_enabled``).

How each of FA's member changes reaches the check:

- **A membership row saved** (FA's member remove, role and workspace-level changes save the row): ``post_save`` on
  ``OrganizationMembership`` and ``WorkspaceMembership`` runs the check after the commit, for that member and org.
- **An account turned inactive** (``POST /accounts/user/deactivate/`` saves ``User.is_active = False`` when the person
  has no other org): ``post_save`` on ``User`` runs it after the commit, in every org where they own an endpoint.
- **An account deleted** (Manage Team's remove from organization, ``DELETE /accounts/team/users/<id>/``; the delete
  route without a workspace; any other deletion): ``pre_delete`` on ``User`` turns their enabled endpoints off inside
  the deletion's transaction, before ``Endpoint.owner`` becomes NULL, in every org except one being deleted with them
  (its endpoints go with it). Its audit entries carry no actor, so none points at a row being deleted.
- **The sweep**, :func:`reconcile_org`: bulk ``QuerySet.update()`` paths fire no signal (deactivating a person who is
  in another org too; a workspace revoke without an org-level change). It also turns off an enabled endpoint with no
  owner: every endpoint the API creates has one, so a NULL owner means the owner's row is gone. The ``webhooks-health``
  schedule (WH-1b) calls it: a merge gate. The admins' email is WH-1b's notices (section 13.3).
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from webhooks import audit
from webhooks.api_endpoint_ops import turn_off_manual
from webhooks.models import Endpoint
from webhooks.permissions import may_still_manage
from webhooks.store_settle import pause_backlog

log = logging.getLogger("webhooks.offboarding")

_OFF = {"status": {"before": "enabled", "after": "disabled_manual"}}


def _org(organization_id, cache: dict):
    from accounts.models.organization import Organization

    if organization_id not in cache:
        cache[organization_id] = Organization.objects.get(id=organization_id)
    return cache[organization_id]


def _turn_off(
    organization, ep_id: str, *, member_id, level: int, system=False, extra=None
) -> bool:
    """``disabled_manual`` / ``creator_removed`` and its audit entry, in one transaction; the backlog is paused after
    the commit. True when it changed."""
    with transaction.atomic():
        changed = turn_off_manual(
            ep_id, reason="creator_removed", actor_id=None, now=timezone.now()
        )
        if changed:
            audit.record(
                organization=organization,
                action=audit.ENDPOINT_DISABLED,
                target=ep_id,
                actor=None,
                system=system,
                changes=_OFF,
                metadata={
                    "reason": "creator_removed",
                    "member_id": member_id,
                    "member_level_now": level,
                    "actor": "system",
                    **(extra or {}),
                },
            )
            transaction.on_commit(lambda: pause_backlog(ep_id))
    return changed


def _logged(out: list[str]) -> list[str]:
    if out:
        log.info(
            "webhooks turned off: creator removed",
            extra={"metric": "webhooks.endpoints.creator_removed", "count": len(out)},
        )
    return out


def reconcile_owner(organization_id, user_id) -> list[str]:
    """Turn off the owner's enabled endpoints in this org that they may no longer manage. Returns their ids."""
    out, orgs = [], {}
    eps = Endpoint.objects.filter(
        organization_id=organization_id,
        owner_id=user_id,
        deleted=False,
        status="enabled",
    ).order_by("id")
    for ep in eps:
        ok, level = may_still_manage(user_id, ep)
        if not ok and _turn_off(
            _org(organization_id, orgs), ep.id, member_id=str(user_id), level=level
        ):
            out.append(ep.id)
    return _logged(out)


def reconcile_org(organization_id) -> list[str]:
    """The sweep: every owner of an enabled endpoint in the org, then every enabled endpoint left with no owner."""
    owners = (
        Endpoint.objects.filter(
            organization_id=organization_id, deleted=False, status="enabled"
        )
        .exclude(owner__isnull=True)
        .values_list("owner_id", flat=True)
        .distinct()
    )
    out: list[str] = []
    for uid in list(owners):
        out += reconcile_owner(organization_id, uid)
    ownerless = Endpoint.objects.filter(
        organization_id=organization_id,
        owner__isnull=True,
        deleted=False,
        status="enabled",
    ).order_by("id")
    orgs: dict = {}
    for ep_id in list(ownerless.values_list("id", flat=True)):
        if _turn_off(
            _org(organization_id, orgs),
            ep_id,
            member_id=None,
            level=0,
            extra={"owner": "deleted"},
        ):
            out.append(ep_id)
    return _logged(out)


def offboard_deleted_account(user_id, skip_organization_ids=frozenset()) -> list[str]:
    """The account is being deleted: turn off every enabled endpoint it owns, in every org but those being deleted
    with it. No actor on the audit entries (``audit.record(system=True)``); who asked is in the metadata."""
    from tfc.middleware.workspace_context import get_current_user
    from tfc.utils.audit import get_current_actor

    # Who asked: the session user (FA's audit middleware) or the API-key user (FA's authentication context).
    asker = get_current_actor() or get_current_user()
    extra = {"account": "deleted"}
    if getattr(asker, "pk", None) is not None and asker.pk != user_id:
        extra["requested_by"] = str(asker.pk)
    out, orgs = [], {}
    eps = (
        Endpoint.objects.filter(owner_id=user_id, deleted=False, status="enabled")
        .exclude(organization_id__in=list(skip_organization_ids))
        .order_by("id")
        .values_list("id", "organization_id")
    )
    for ep_id, org_id in list(eps):
        if _turn_off(
            _org(org_id, orgs),
            ep_id,
            member_id=str(user_id),
            level=0,
            system=True,
            extra=extra,
        ):
            out.append(ep_id)
    return _logged(out)


def _after_commit(fn, *args) -> None:
    def run():
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - never fails the member change; the sweep retries it
            log.exception(
                "webhooks offboarding check failed",
                extra={"metric": "webhooks.offboarding.failed"},
            )

    transaction.on_commit(run)


def _on_org_membership(sender, instance, **kwargs) -> None:
    if instance.user_id and instance.organization_id:
        _after_commit(reconcile_owner, instance.organization_id, instance.user_id)


def _on_ws_membership(sender, instance, **kwargs) -> None:
    from accounts.models.workspace import Workspace

    org_id = (
        Workspace.no_workspace_objects.filter(id=instance.workspace_id)
        .values_list("organization_id", flat=True)
        .first()
    )
    if instance.user_id and org_id:
        _after_commit(reconcile_owner, org_id, instance.user_id)


def _reconcile_account(user_id) -> None:
    orgs = (
        Endpoint.objects.filter(owner_id=user_id, deleted=False, status="enabled")
        .values_list("organization_id", flat=True)
        .distinct()
    )
    for org_id in list(orgs):
        reconcile_owner(org_id, user_id)


def _on_user_saved(
    sender, instance, created=False, update_fields=None, **kwargs
) -> None:
    if created or instance.is_active:
        return
    if update_fields is not None and "is_active" not in update_fields:
        return
    _after_commit(_reconcile_account, instance.pk)


def _organizations_being_deleted(origin) -> set:
    from accounts.models.organization import Organization

    if isinstance(origin, Organization):
        return {origin.pk}
    if isinstance(origin, QuerySet) and issubclass(origin.model, Organization):
        return set(origin.values_list("pk", flat=True))
    return set()


def _on_user_deleting(sender, instance, origin=None, **kwargs) -> None:
    """Inside the deletion's transaction (a savepoint): a failure here never blocks the deletion; the sweep's
    ownerless rule catches what it missed."""
    try:
        with transaction.atomic():
            offboard_deleted_account(instance.pk, _organizations_being_deleted(origin))
    except Exception:  # noqa: BLE001 - never fails the account deletion
        log.exception(
            "webhooks offboarding at account deletion failed",
            extra={"metric": "webhooks.offboarding.failed"},
        )


def connect() -> None:
    from django.db.models.signals import post_save, pre_delete

    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import WorkspaceMembership

    post_save.connect(
        _on_org_membership,
        sender=OrganizationMembership,
        dispatch_uid="webhooks.offboarding.org",
    )
    post_save.connect(
        _on_ws_membership,
        sender=WorkspaceMembership,
        dispatch_uid="webhooks.offboarding.ws",
    )
    post_save.connect(
        _on_user_saved, sender=User, dispatch_uid="webhooks.offboarding.user_saved"
    )
    pre_delete.connect(
        _on_user_deleting,
        sender=User,
        dispatch_uid="webhooks.offboarding.user_deleting",
    )
