"""Likes, reports, report resolution, appeals and picks (SPEC §5.5, §6.1, §6.4, §6.6)."""

from __future__ import annotations

from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from omega_hub import pseudonym
from omega_hub.errors import HubError
from omega_hub.models import AuditEvent, Entry, Like, Pick, Publisher, Report, Submission, Version
from omega_hub.services import audit, cache_keys, email_verification, moderation, notify

RESOLUTIONS = ("dismiss", "ask_publisher", "unrank_entry", "yank_version", "remove_entry", "suspend_publisher", "restore")
MAX_PICKS = 6


def _listed(entry: Entry) -> Entry:
    if entry.status != Entry.Status.LISTED:
        raise HubError("OMEGA_HUB_GONE", 410)
    return entry


@transaction.atomic
def like(user, entry: Entry) -> tuple[int, bool]:
    email_verification.require_verified(user)  # SPEC §6.1, decision 13 (HUB-25)
    _listed(entry)
    try:
        with transaction.atomic():
            Like.objects.create(user=user, entry=entry)
        Entry.objects.filter(pk=entry.pk).update(like_count=F("like_count") + 1)
    except IntegrityError:
        pass
    cache_keys.invalidate_listing()
    return Entry.objects.get(pk=entry.pk).like_count, True


@transaction.atomic
def unlike(user, entry: Entry) -> tuple[int, bool]:
    deleted, _ = Like.objects.filter(user=user, entry=entry).delete()
    if deleted:
        Entry.objects.filter(pk=entry.pk, like_count__gt=0).update(like_count=F("like_count") - 1)
    cache_keys.invalidate_listing()
    return Entry.objects.get(pk=entry.pk).like_count, False


@transaction.atomic
def open_report(user, entry: Entry, *, version=None, reason, details) -> Report:
    email_verification.require_verified(user)
    if reason not in Report.Reason.values:
        raise HubError("OMEGA_HUB_INVALID", 400, f"reason is one of: {', '.join(Report.Reason.values)}")
    details = moderation.text(details, "details", 20, 2000)
    if entry.status == Entry.Status.REMOVED:
        raise HubError("OMEGA_HUB_GONE", 410)
    version_obj = None
    if version:
        version_obj = Version.objects.filter(entry=entry, version=version).first()
        if version_obj is None:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404, f"no version {version}")
    report = Report.objects.create(entry=entry, version=version_obj, reporter=user, reporter_label=pseudonym.label_for(user),
                                   reason=reason, details=details)
    audit.record("report.opened", target_type="report", target_id=report.pk, actor=user, publisher=entry.publisher,
                 data={"ref": entry.ref, "reason": reason})
    return report


@transaction.atomic
def resolve_report(user, report: Report, *, action, note, category=None, reason=None, version=None) -> Report:
    report = Report.objects.select_for_update(of=("self",)).select_related("entry__publisher", "version", "reporter").get(pk=report.pk)
    if report.status != Report.Status.OPEN:
        raise HubError("OMEGA_HUB_BAD_STATE", 409, "the report is resolved")
    if action not in RESOLUTIONS:
        raise HubError("OMEGA_HUB_INVALID", 400, f"action is one of: {', '.join(RESOLUTIONS)}")
    note = moderation.text(note, "note")
    entry = report.entry
    if action == "ask_publisher":
        notify.moderation("a maintainer asks you to look at a problem", publisher=entry.publisher, ref=entry.ref, note=note)
    elif action == "unrank_entry":
        moderation.unrank(user, entry, reason or "under investigation", note)
    elif action == "yank_version":
        target = report.version or (Version.objects.filter(entry=entry, version=version).first() if version else None)
        if target is None:
            raise HubError("OMEGA_HUB_INVALID", 400, "name the version to yank")
        moderation.yank_version(user, target, note, by_maintainer=True)
    elif action == "remove_entry":
        moderation.remove_entry(user, entry, category or "breaks Hub rules", note)
    elif action == "suspend_publisher":
        moderation.suspend_publisher(user, entry.publisher, note)
    elif action == "restore":
        moderation.restore_entry(user, entry, note)
    report.status = Report.Status.DISMISSED if action == "dismiss" else Report.Status.ACTIONED
    report.resolution, report.resolved_by, report.resolved_at, report.resolution_note = action, user, timezone.now(), note
    report.save()
    audit.record("report.resolved", target_type="report", target_id=report.pk, actor=user, publisher=entry.publisher,
                 data={"action": action, "ref": entry.ref})
    if report.reporter_id:  # a deleted reporter has no address to tell (HUB-09)
        notify.report_resolved(report, action_taken=action != "dismiss")
    return report


def _original_actor(target_type: str, target_id: str):
    """Who made the decision being appealed."""
    if target_type == "submission":
        submission = Submission.objects.filter(pk=target_id).first()
        return submission.decided_by_id if submission else None
    actions = {"entry": ("entry.removed", "entry.unranked"), "publisher": ("publisher.suspended",),
               "version": ("version.yanked",)}.get(target_type)
    if actions is None:
        raise HubError("OMEGA_HUB_INVALID", 400, "targetType is submission, entry, version or publisher")
    event = AuditEvent.objects.filter(target_type=target_type, target_id=str(target_id), action__in=actions).order_by("-at").first()
    return event.actor_id if event else None


@transaction.atomic
def record_appeal(user, *, target_type, target_id, outcome, note) -> AuditEvent:
    if outcome not in ("upheld", "granted"):
        raise HubError("OMEGA_HUB_INVALID", 400, "outcome is upheld or granted")
    note = moderation.text(note, "note")
    original = _original_actor(target_type, str(target_id))
    if original is None:
        raise HubError("OMEGA_HUB_NOT_FOUND", 404, "no decision to appeal")
    if original == user.pk:
        raise HubError("OMEGA_HUB_APPEAL_SELF", 403)
    return audit.record(f"appeal.{outcome}", target_type=target_type, target_id=target_id, actor=user,
                        data={"note": note, "originalActor": str(original)})


def monday(value: date | None = None) -> date:
    value = value or timezone.now().date()
    return value - timedelta(days=value.weekday())


@transaction.atomic
def add_pick(user, entry: Entry, *, week, why) -> Pick:
    _listed(entry)
    if not entry.ranked:
        raise HubError("OMEGA_HUB_BAD_STATE", 409, "an unranked entry cannot be a pick")
    try:
        week_date = date.fromisoformat(str(week)) if week else monday()
    except ValueError as exc:
        raise HubError("OMEGA_HUB_INVALID", 400, "week is YYYY-MM-DD") from exc
    if week_date.weekday() != 0:
        raise HubError("OMEGA_HUB_INVALID", 400, "week is a Monday")
    why = moderation.text(why, "why", 20, 140)
    if Pick.objects.select_for_update().filter(week=week_date).count() >= MAX_PICKS:
        raise HubError("OMEGA_HUB_PICKS_FULL", 409)
    if Pick.objects.filter(entry=entry, week=week_date).exists():
        raise HubError("OMEGA_HUB_INVALID", 409, "already picked that week")
    pick = Pick.objects.create(entry=entry, week=week_date, why=why, picked_by=user)
    audit.record("pick.added", target_type="pick", target_id=pick.pk, actor=user, publisher=entry.publisher,
                 data={"ref": entry.ref, "week": week_date.isoformat()})
    cache_keys.invalidate_listing()
    return pick


@transaction.atomic
def remove_pick(user, pick_id) -> None:
    pick = Pick.objects.select_related("entry__publisher").filter(pk=pick_id).first()
    if pick is None:
        raise HubError("OMEGA_HUB_NOT_FOUND", 404)
    audit.record("pick.removed", target_type="pick", target_id=pick.pk, actor=user, publisher=pick.entry.publisher,
                 data={"ref": pick.entry.ref, "week": pick.week.isoformat()})
    pick.delete()
    cache_keys.invalidate_listing()


def publisher_by_handle(handle: str) -> Publisher:
    publisher = Publisher.objects.filter(handle=handle).first()
    if publisher is None:
        raise HubError("OMEGA_HUB_NOT_FOUND", 404)
    return publisher
