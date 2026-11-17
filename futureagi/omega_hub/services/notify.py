"""Plain-text emails (SPEC §11), sent from `transaction.on_commit` so a rollback sends nothing.

Every moderation email names the appeal route (SPEC §3.4).
"""

from __future__ import annotations

import structlog
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from omega_hub import conf, rules
from omega_hub.services import outbox

logger = structlog.get_logger(__name__)


def _deliver_message(
    subject: str,
    body: str,
    from_email: str | None,
    recipients: list[str],
    fail_silently: bool = False,
) -> int:
    return send_mail(subject, body, from_email or getattr(settings, "DEFAULT_FROM_EMAIL", None), recipients, fail_silently=fail_silently)


def _send(template: str, recipients: list[str], context: dict) -> None:
    recipients = sorted({address for address in recipients if address})
    if not recipients:
        return
    contacts = rules.contacts()
    context = {"app_url": conf.app_url(), "security": contacts.get("security"), "discord": contacts.get("discord"), **context}
    subject = render_to_string(f"omega_hub/email/{template}.subject.txt", context).strip()
    body = render_to_string(f"omega_hub/email/{template}.txt", context)

    outbox.enqueue(template=template, recipients=recipients, subject=subject, body=body)


def maintainer_emails() -> list[str]:
    from django.contrib.auth import get_user_model

    from omega_hub.permissions import is_maintainer

    users = get_user_model().objects.filter(is_active=True, user_permissions__codename="hub_maintainer").distinct()
    return [user.email for user in users if is_maintainer(user)]


def publisher_emails(publisher) -> list[str]:
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level

    if publisher.owner_user_id:
        return [publisher.owner_user.email]
    memberships = OrganizationMembership.no_workspace_objects.select_related("user").filter(
        organization_id=publisher.owner_org_id, is_active=True
    )
    return [m.user.email for m in memberships if m.level_or_legacy >= Level.ADMIN]


def _submitter_email(submission) -> str | None:
    return submission.submitted_by.email if submission.submitted_by_id else None


def _sub_ctx(submission) -> dict:
    return {"submission": submission, "ref": f"{submission.publisher.handle}/{submission.name or submission.hint_name or '?'}",
            "version": submission.version or submission.hint_version or "?",
            "status_url": f"{conf.app_url()}/submissions/{submission.pk}"}


def checks_failed(submission) -> None:
    _send("checks_failed", [_submitter_email(submission)], _sub_ctx(submission))


def check_error(submission) -> None:
    _send("check_error", [_submitter_email(submission), *maintainer_emails()], _sub_ctx(submission))


def decision(submission, version) -> None:
    ctx = _sub_ctx(submission)
    if version is not None:
        draft = version.disclosure_draft
        ctx.update({"version_obj": version, "disclosure": version.disclosure,
                    "disclosure_changed": draft != version.disclosure,
                    "entry_url": f"{conf.app_url()}/{submission.publisher.handle}/{version.entry.name}"})
        _send("approved", [_submitter_email(submission)], ctx)
    elif submission.decision == "reject":
        _send("rejected", [_submitter_email(submission)], ctx)
    else:
        _send("changes_requested", [_submitter_email(submission)], ctx)


def moderation(action: str, *, publisher, ref: str, reason: str = "", note: str = "") -> None:
    """Yank by a maintainer, unrank, removal, move, suspension, verification, ask_publisher."""
    _send("moderation", publisher_emails(publisher), {"action": action, "ref": ref, "reason": reason, "note": note})


def report_resolved(report, action_taken: bool) -> None:
    _send("report_resolved", [report.reporter.email], {"action_taken": action_taken, "ref": report.entry.ref})


def rules_changed(publisher, summary: str, version: int) -> None:
    _send("rules_changed", publisher_emails(publisher), {"summary": summary, "version": version})


def ops_recipients() -> list[str]:
    """Active maintainers plus the on-call route OMEGA_HUB_OPS_ALERT_EMAILS (conf.ops_alert_emails)."""
    return sorted({*maintainer_emails(), *conf.ops_alert_emails()})


def ops_alert(alert: dict) -> bool:
    recipients = ops_recipients()
    if not recipients:
        # Until maintainers are granted (`omega_hub_maintainer grant`) or OMEGA_HUB_OPS_ALERT_EMAILS is
        # set, a page has nobody to reach: say so on every check rather than drop it silently. The
        # caller retries next check.
        logger.error("omega_hub_ops_alert_unrouted", alert=alert.get("id"),
                     reason="no active hub_maintainer and no OMEGA_HUB_OPS_ALERT_EMAILS")
        return False
    subject = f"Omega Hub alert: {alert.get('id', 'operations')}"
    body = f"{alert.get('message', 'Hub operations alert')}\n\n{alert}"
    return outbox.enqueue(template="ops_alert", recipients=recipients, subject=subject, body=body) is not None
