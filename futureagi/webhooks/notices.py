"""The customer notices of DESIGN.md sections 7.7 and 13.3: "Failing" (at most once per 24 h per endpoint) and
"Turned off" (once per turn-off), by email through ``tfc.utils.email.email_helper`` to the endpoint's owner and the
workspace's admins (the org's admins for an org-wide endpoint); "Creator removed" (G1: once per offboarding turn-off,
one email per member and workspace) to those admins only. They move to notify's inbox once that ships.

The health pass sends them, never a settle: no email I/O inside a transaction that holds a state row. Each notice is
claimed first with one conditional UPDATE (so two health passes never send it twice), then sent; a send that fails is
given back and logged, so the next pass tries again.

A notice never carries a receiver's body or a non-public address: only the endpoint's name, its masked URL
(``url_display``), the last status code and the times.

**Every send is bounded.** ``email_helper`` sends synchronously and FA sets no ``EMAIL_TIMEOUT``, so an SMTP server
that stops answering would hold the health activity's thread (a worker thread the dispatcher loops share) without
end. Each send runs on its own daemon thread and is waited for at most ``SEND_TIMEOUT_S``; one that does not finish is
given back and logged (``webhooks.notice.stuck``), and while it is still stuck no other send starts in this process
(each notice is given back at once), so at most one thread is ever held. A stuck send that completes later may make
that one notice arrive twice: a duplicate email rather than a lost one.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from django.db.models import F, Q

from webhooks.core import health
from webhooks.core.schedule import _ms
from webhooks.health_state import state_of
from webhooks.models import Endpoint, EndpointState
from webhooks.store_rows import ms_of

log = logging.getLogger("webhooks.notices")

SEND_TIMEOUT_S = 20.0
_stuck: threading.Thread | None = None
"""The send that did not finish in time, while it is still running."""


class SendStuck(Exception):
    """A notice send did not finish within ``SEND_TIMEOUT_S``, or an earlier one is still stuck."""


REASON_TEXT = {
    "failing": "it answered with nothing but failures for 24 hours",
    "gone_410": "it answered 410 Gone",
    "unsafe_address": "its host resolved to an address that is not allowed, three times in a row",
}


def recipients(ep: Endpoint) -> list[str]:
    """The owner, then the workspace admins (org admins and owners for an org-wide endpoint); active users only."""
    from accounts.models import User
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    out: list[str] = []
    if ep.owner_id:
        owner = User.objects.filter(id=ep.owner_id, is_active=True).first()
        if owner:
            out.append(owner.email)
    org_admins = OrganizationMembership.no_workspace_objects.filter(
        organization_id=ep.organization_id,
        is_active=True,
        level__gte=Level.ADMIN,
        user__is_active=True,
    ).values_list("user__email", flat=True)
    out += list(org_admins)
    out += list(
        User.objects.filter(
            organization_id=ep.organization_id,
            is_active=True,
            organization_role__in=[OrganizationRoles.OWNER, OrganizationRoles.ADMIN],
        ).values_list("email", flat=True)
    )
    if ep.workspace_id:
        out += list(
            WorkspaceMembership.no_workspace_objects.filter(
                workspace_id=ep.workspace_id, is_active=True, user__is_active=True
            )
            .filter(
                Q(level__gte=Level.ADMIN)
                | Q(level__isnull=True, role=OrganizationRoles.WORKSPACE_ADMIN)
            )
            .values_list("user__email", flat=True)
        )
    seen: set[str] = set()
    return [e for e in out if e and not (e.lower() in seen or seen.add(e.lower()))]


def _deliver(subject: str, template: str, context: dict, to: list[str]) -> None:
    """``email_helper`` on its own daemon thread, waited for at most ``SEND_TIMEOUT_S``; its error is raised here."""
    global _stuck
    from tfc.utils.email import email_helper

    if _stuck is not None and _stuck.is_alive():
        raise SendStuck
    box: dict = {}

    def send() -> None:
        try:
            email_helper(subject, template, context, to)
        except Exception as exc:  # noqa: BLE001 - handed to the caller
            box["error"] = exc

    t = threading.Thread(target=send, name="webhooks-notice-send", daemon=True)
    t.start()
    t.join(SEND_TIMEOUT_S)
    if t.is_alive():
        _stuck = t
        raise SendStuck
    if "error" in box:
        raise box["error"]


def _send(subject: str, template: str, context: dict, to: list[str]) -> bool:
    if not to:
        log.warning(
            "webhooks notice has no recipient",
            extra={"metric": "webhooks.notice.no_recipient", "template": template},
        )
        return True
    try:
        _deliver(subject, template, context, to)
        return True
    except SendStuck:
        log.error(
            "webhooks notice given back: a send did not finish in time",
            extra={
                "metric": "webhooks.notice.stuck",
                "template": template,
                "timeout_s": SEND_TIMEOUT_S,
            },
        )
        return False
    except Exception as exc:  # noqa: BLE001 - given back; the next pass retries
        log.error(
            "webhooks notice could not be sent",
            extra={
                "metric": "webhooks.notice.failed",
                "template": template,
                "error_type": type(exc).__name__,
            },
        )
        return False


def _context(ep: Endpoint, st: EndpointState) -> dict:
    return {
        "endpoint_name": ep.name,
        "endpoint_url": ep.url_display,
        "endpoint_host": ep.url_host,
        "last_status_code": st.last_status_code,
        "last_outcome": st.last_outcome,
        "failing_since": st.failing_since,
        "consecutive_failed_attempts": st.consecutive_failed_attempts,
    }


def send_failing(now: datetime, scale: float = 1.0) -> int:
    """The "Failing" email for every live, enabled endpoint that is failing now and got none in the last 24 h."""
    now_ms = ms_of(now)
    every = timedelta(milliseconds=_ms(health.FAILING_NOTICE_EVERY_S, scale))
    sent = 0
    for st in EndpointState.objects.filter(
        consecutive_failed_attempts__gte=3,
        endpoint__status="enabled",
        endpoint__deleted=False,
    ).select_related("endpoint"):
        decision = health.decide(state_of(st), now_ms, scale)
        last = ms_of(st.last_failing_notice_at)
        if not health.failing_notice_due(decision["failing"], last, now_ms, scale):
            continue
        claimed = (
            EndpointState.objects.filter(endpoint_id=st.endpoint_id)
            .filter(
                Q(last_failing_notice_at__isnull=True)
                | Q(last_failing_notice_at__lte=now - every)
            )
            .update(last_failing_notice_at=now)
        )
        if not claimed:
            continue
        ep = st.endpoint
        ok = _send(
            f"Webhook failing: {ep.name}",
            "webhooks/endpoint_failing.html",
            _context(ep, st),
            recipients(ep),
        )
        if not ok:
            EndpointState.objects.filter(
                endpoint_id=st.endpoint_id, last_failing_notice_at=now
            ).update(last_failing_notice_at=st.last_failing_notice_at)
            continue
        sent += 1
        log.info(
            "webhooks failing notice sent",
            extra={"metric": "webhooks.notice.failing", "endpoint_id": ep.id},
        )
    return sent


def _off_not_noticed(**endpoint_filter):
    """State rows of live endpoints turned off (``disabled_at``) after their last turned-off notice."""
    return (
        EndpointState.objects.filter(
            endpoint__deleted=False,
            endpoint__disabled_at__isnull=False,
            **{f"endpoint__{k}": v for k, v in endpoint_filter.items()},
        )
        .filter(
            Q(last_disabled_notice_at__isnull=True)
            | Q(last_disabled_notice_at__lt=F("endpoint__disabled_at"))
        )
        .select_related("endpoint")
    )


def _off_stamp(ep: Endpoint, now: datetime) -> datetime:
    """What a sent turned-off notice records: never earlier than the turn-off it covers. A turn-off can be later than
    the pass's ``now``: the offboarding sweep (step 0 of the pass) stamps its own time, and a dispatcher may settle a
    410 while the pass runs. Stamping ``now`` then would leave the notice older than its turn-off, and the next pass
    would send it again."""
    return max(now, ep.disabled_at) if ep.disabled_at else now


def _claim_off_notice(ep: Endpoint, now: datetime) -> bool:
    """One conditional UPDATE: two health passes never send the same turned-off notice twice."""
    return bool(
        EndpointState.objects.filter(endpoint_id=ep.id)
        .filter(
            Q(last_disabled_notice_at__isnull=True)
            | Q(last_disabled_notice_at__lt=ep.disabled_at)
        )
        .update(last_disabled_notice_at=_off_stamp(ep, now))
    )


def _give_back_off_notice(ep: Endpoint, now: datetime, previous) -> None:
    EndpointState.objects.filter(
        endpoint_id=ep.id, last_disabled_notice_at=_off_stamp(ep, now)
    ).update(last_disabled_notice_at=previous)


def send_turned_off(now: datetime) -> int:
    """The "Turned off" email, once for each automatic turn-off (``disabled_at`` newer than the last notice)."""
    sent = 0
    for st in _off_not_noticed(status="disabled_auto"):
        ep = st.endpoint
        previous = st.last_disabled_notice_at
        if not _claim_off_notice(ep, now):
            continue
        context = {
            **_context(ep, st),
            "reason": REASON_TEXT.get(ep.disabled_reason, ep.disabled_reason),
            "disabled_at": ep.disabled_at,
        }
        if not _send(
            f"Webhook turned off: {ep.name}",
            "webhooks/endpoint_turned_off.html",
            context,
            recipients(ep),
        ):
            _give_back_off_notice(ep, now, previous)
            continue
        sent += 1
        log.info(
            "webhooks turned-off notice sent",
            extra={
                "metric": "webhooks.notice.turned_off",
                "endpoint_id": ep.id,
                "reason": ep.disabled_reason,
            },
        )
    return sent


def _member_and_scope(ep: Endpoint) -> tuple[str, str, str]:
    """The removed member's name and address (the address only to leave it out), and the workspace's (or org's)
    name."""
    from accounts.models import Organization, User
    from accounts.models.workspace import Workspace

    name, email = (
        User.objects.filter(id=ep.owner_id).values_list("name", "email").first()
        if ep.owner_id
        else None
    ) or ("", "")
    scope = (
        Workspace.all_objects.filter(id=ep.workspace_id)
        .values_list("name", flat=True)
        .first()
        if ep.workspace_id
        else Organization.objects.filter(id=ep.organization_id)
        .values_list("name", flat=True)
        .first()
    )
    return name or "A member", email or "", scope or "the workspace"


def send_creator_removed(now: datetime) -> int:
    """The "Creator removed" email (section 7.7, G1): for endpoints turned off with reason ``creator_removed``, one
    email per removed member and workspace, to the admins (never the member), listing the endpoints. Once per
    turn-off: the same claim as "Turned off" (``last_disabled_notice_at``)."""
    groups: dict[tuple, list[tuple[Endpoint, datetime | None]]] = {}
    for st in _off_not_noticed(
        status="disabled_manual", disabled_reason="creator_removed"
    ):
        ep = st.endpoint
        previous = st.last_disabled_notice_at
        if _claim_off_notice(ep, now):
            key = (ep.organization_id, ep.workspace_id, ep.owner_id)
            groups.setdefault(key, []).append((ep, previous))
    sent = 0
    for eps in groups.values():
        first = eps[0][0]
        member, member_email, scope = _member_and_scope(first)
        to = [e for e in recipients(first) if e.lower() != member_email.lower()]
        context = {
            "member_name": member,
            "scope_name": scope,
            "count": len(eps),
            "endpoints": [{"name": ep.name, "url": ep.url_display} for ep, _ in eps],
        }
        if not _send(
            f"Webhooks turned off: {member} left {scope}",
            "webhooks/endpoint_creator_removed.html",
            context,
            to,
        ):
            for ep, previous in eps:
                _give_back_off_notice(ep, now, previous)
            continue
        sent += 1
        log.info(
            "webhooks creator-removed notice sent",
            extra={
                "metric": "webhooks.notice.creator_removed",
                "endpoints": len(eps),
            },
        )
    return sent
