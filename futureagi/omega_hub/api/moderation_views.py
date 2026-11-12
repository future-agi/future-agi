"""Maintainer moderation (SPEC §5.5, §9.4): reports, removal, unrank, moves, likers, blocklist,
appeals, picks, publisher verification and suspension, and the audit log."""

from __future__ import annotations

from rest_framework.response import Response

from accounts.authentication import workspace_read_only
from omega_hub.api.base import MaintainerView, body, paginate
from omega_hub.api.serializers.publishers import owned
from omega_hub.errors import HubError
from omega_hub.models import AuditEvent, BlockedDigest, Entry, Like, Pick, Publisher, Report
from omega_hub.services import blocklist, community, moderation, moves


def _entry(handle, name) -> Entry:
    entry = Entry.objects.select_related("publisher").filter(publisher__handle=handle, name=name).first()
    if entry is None:
        raise HubError("OMEGA_HUB_NOT_FOUND", 404)
    return entry


def _entry_state(entry: Entry) -> dict:
    return {"ref": entry.ref, "status": entry.status, "ranked": entry.ranked,
            "unrankedReason": entry.unranked_reason or None, "removedCategory": entry.removed_category or None}


class ReviewReportsView(MaintainerView):
    def get(self, request):
        status = request.query_params.get("status") or "open"
        rows = Report.objects.select_related("entry__publisher", "version", "reporter").filter(status=status)
        return Response([{
            "id": str(r.pk), "entry": r.entry.ref, "version": r.version.version if r.version_id else None,
            "reason": r.reason, "details": r.details, "reporter": r.reporter.email if r.reporter_id else None,
            "reporterLabel": r.reporter_label or None, "status": r.status,
            "createdAt": r.created_at.isoformat(), "resolution": r.resolution or None,
        } for r in rows])


@workspace_read_only
class ReportResolveView(MaintainerView):
    def post(self, request, report_id):
        report = Report.objects.filter(pk=report_id).first()
        if report is None:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        data = body(request)
        report = community.resolve_report(request.user, report, action=data.get("action"), note=data.get("note"),
                                          category=data.get("category"), reason=data.get("reason"),
                                          version=data.get("version"))
        return Response({"id": str(report.pk), "status": report.status, "resolution": report.resolution})


@workspace_read_only
class EntryActionView(MaintainerView):
    # Not `action`: drf-yasg treats an APIView with an `action` attribute as a viewset.
    hub_action = ""

    def post(self, request, publisher, name):
        entry, data, user = _entry(publisher, name), body(request), request.user
        if self.hub_action == "remove":
            entry = moderation.remove_entry(user, entry, data.get("category"), data.get("note"))
        elif self.hub_action == "restore":
            entry = moderation.restore_entry(user, entry, data.get("note"))
        elif self.hub_action == "unrank":
            entry = moderation.unrank(user, entry, data.get("reason"), data.get("note"))
        elif self.hub_action == "rerank":
            entry = moderation.rerank(user, entry, data.get("note"))
        elif self.hub_action == "move":
            entry = moves.move_entry(user, entry, to_publisher=data.get("toPublisher"), to_name=data.get("toName"),
                                     note=data.get("note"))
        return Response(_entry_state(entry))


class LikersView(MaintainerView):
    def get(self, request, publisher, name):
        likes = Like.objects.select_related("user").filter(entry=_entry(publisher, name)).order_by("-created_at")
        return Response([{"user": like.user.email, "at": like.created_at.isoformat()} for like in likes])


@workspace_read_only
class PublisherRenameView(MaintainerView):
    def post(self, request, handle):
        publisher = community.publisher_by_handle(handle)
        data = body(request)
        return Response(owned(moves.rename_publisher(request.user, publisher, to_handle=data.get("toHandle"),
                                                     note=data.get("note"))))


def _block_row(row: BlockedDigest) -> dict:
    return {"id": str(row.pk), "digest": row.digest, "bundleSha256": row.bundle_sha256,
            "entry": row.entry.ref if row.entry_id else None, "version": row.version.version if row.version_id else None,
            "category": row.category, "note": row.note, "addedBy": row.added_by.email if row.added_by_id else None,
            "addedAt": row.added_at.isoformat(), "liftedAt": row.lifted_at.isoformat() if row.lifted_at else None}


@workspace_read_only
class ReviewBlocklistView(MaintainerView):
    def get(self, request):
        rows = BlockedDigest.objects.select_related("entry__publisher", "version", "added_by").all()
        return Response([_block_row(row) for row in rows])

    def post(self, request):
        data = body(request)
        row = blocklist.add_bare(request.user, digest=data.get("digest"), bundle_sha256=data.get("bundleSha256"),
                                 category=data.get("category"), note=data.get("note"))
        return Response(_block_row(row), status=201)


@workspace_read_only
class BlocklistLiftView(MaintainerView):
    def post(self, request, block_id):
        return Response(_block_row(blocklist.lift(request.user, block_id)))


@workspace_read_only
class AppealsView(MaintainerView):
    def post(self, request):
        data = body(request)
        event = community.record_appeal(request.user, target_type=data.get("targetType"), target_id=data.get("targetId"),
                                        outcome=data.get("outcome"), note=data.get("note"))
        return Response({"id": str(event.pk), "action": event.action}, status=201)


@workspace_read_only
class MaintainerYankView(MaintainerView):
    def post(self, request, publisher, name, version):
        found = _entry(publisher, name).versions.filter(version=version).first()
        if found is None:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        found = moderation.yank_version(request.user, found, body(request).get("reason"), by_maintainer=True)
        return Response({"version": found.version, "status": found.status})


def _pick_row(pick: Pick) -> dict:
    return {"id": str(pick.pk), "entry": pick.entry.ref, "week": pick.week.isoformat(), "why": pick.why,
            "pickedBy": pick.picked_by.email if pick.picked_by_id else None}


@workspace_read_only
class ReviewPicksView(MaintainerView):
    def get(self, request):
        return Response([_pick_row(p) for p in Pick.objects.select_related("entry__publisher", "picked_by")[:200]])

    def post(self, request):
        data = body(request)
        handle, _, name = str(data.get("entry") or "").partition("/")
        pick = community.add_pick(request.user, _entry(handle, name), week=data.get("week"), why=data.get("why"))
        return Response(_pick_row(pick), status=201)


@workspace_read_only
class ReviewPickView(MaintainerView):
    def delete(self, request, pick_id):
        community.remove_pick(request.user, pick_id)
        return Response(status=204)


class ReviewPublishersView(MaintainerView):
    def get(self, request):
        rows = Publisher.objects.all()
        if request.query_params.get("verification") == "requested":
            rows = rows.filter(verification_request__isnull=False)
        return Response([{**owned(p), "verificationRequest": p.verification_request} for p in rows])


@workspace_read_only
class PublisherActionView(MaintainerView):
    # Not `action`: drf-yasg treats an APIView with an `action` attribute as a viewset.
    hub_action = ""

    def post(self, request, handle):
        publisher, note, user = community.publisher_by_handle(handle), body(request).get("note"), request.user
        if self.hub_action in ("verify", "unverify"):
            publisher = moderation.set_verified(user, publisher, self.hub_action == "verify", note)
        elif self.hub_action == "suspend":
            publisher = moderation.suspend_publisher(user, publisher, note)
        elif self.hub_action == "unsuspend":
            publisher = moderation.unsuspend_publisher(user, publisher, note)
        return Response(owned(publisher))


class AuditView(MaintainerView):
    def get(self, request):
        rows = AuditEvent.objects.select_related("actor").all()
        params = request.query_params
        for param, field in (("targetType", "target_type"), ("targetId", "target_id"), ("action", "action")):
            if params.get(param):
                rows = rows.filter(**{field: params[param]})
        return paginate(request, list(rows[:2000]), lambda chunk: [{
            "id": str(e.pk), "at": e.at.isoformat(), "actor": e.actor.email if e.actor_id else None,
            "actorLabel": e.actor_label or None, "actorKind": e.actor_kind, "action": e.action, "targetType": e.target_type, "targetId": e.target_id,
            "data": e.data,
        } for e in chunk])
