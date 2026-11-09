"""Publishing (SPEC §9.3): submit, list my submissions, read one, fetch the raw upload, withdraw,
yank and unlist. Hub tokens may submit, read their publisher's submissions and yank (SPEC §9.1)."""

from __future__ import annotations

from django.db.models import Q
from rest_framework.response import Response

from accounts.authentication import workspace_read_only
from omega_hub.api.base import HubView, body, bytes_response
from omega_hub.api.lookups import any_entry, live_entry, submission as find_submission
from omega_hub.api.serializers.submissions import for_submitter
from omega_hub.errors import HubError
from omega_hub.models import Submission
from omega_hub.permissions import can_act_for_publisher, is_hub_token, is_maintainer, require_publisher
from omega_hub.services import community, intake, moderation, publishers as publisher_service, storage
from omega_hub.throttles import HubSubmitThrottle, HubUserReadThrottle


def can_see_submission(request, submission: Submission) -> bool:
    if is_hub_token(request):
        return request.auth.publisher_id == submission.publisher_id
    user = request.user
    return (
        submission.submitted_by_id == user.pk
        or can_act_for_publisher(user, submission.publisher)
        or is_maintainer(user)
    )


def visible_submission(request, submission_id) -> Submission:
    found = find_submission(submission_id)
    if not can_see_submission(request, found):
        raise HubError("OMEGA_HUB_NOT_FOUND", 404)  # do not confirm another tenant's submission exists
    return found


@workspace_read_only
class SubmissionsView(HubView):
    token_allowed = True
    # The body cap (body_limit.py) runs first; this bounds how often one account may upload at all.
    throttle_classes = [HubUserReadThrottle, HubSubmitThrottle]

    def get(self, request):
        if is_hub_token(request):
            rows = Submission.objects.filter(publisher=request.auth.publisher)
        else:
            mine = [p.pk for p in publisher_service.publishers_for(request.user)]
            rows = Submission.objects.filter(Q(submitted_by=request.user) | Q(publisher_id__in=mine))
        status = request.query_params.get("status")
        if status:
            rows = rows.filter(status=status)
        rows = rows.select_related("publisher").order_by("-created_at")[:200]
        return Response([for_submitter(row) for row in rows])

    def post(self, request):
        # With no checker configured, intake.receive refuses the upload with 503
        # OMEGA_HUB_CHECKER_NOT_CONFIGURED and stores nothing: a configuration error says what
        # is missing, never a 404 (main-session ruling 3, 2026-10-21).
        data = request.data
        handle = data.get("publisher") or (request.auth.publisher.handle if is_hub_token(request) else None)
        if not handle:
            raise HubError("OMEGA_HUB_INVALID", 400, "publisher is required")
        publisher = community.publisher_by_handle(handle)
        require_publisher(request, publisher)
        upload = request.FILES.get("bundle")
        if upload is None:
            raise HubError("OMEGA_HUB_INVALID", 400, "bundle is required (multipart)")
        if upload.size > intake.MAX_RAW_BYTES:
            raise HubError("OMEGA_HUB_BUNDLE_TOO_LARGE", 413)
        channel = data.get("channel") or ("cli" if is_hub_token(request) else "web")
        submission = intake.receive(
            request.user, publisher, data=upload.read(), channel=channel, name=data.get("name"),
            version=data.get("version"), notes=data.get("notes") or "", github_commit=data.get("githubCommit"),
        )
        return Response({"id": str(submission.pk), "status": submission.status, "url": for_submitter(submission)["url"]},
                        status=201)


class SubmissionView(HubView):
    token_allowed = True

    def get(self, request, submission_id):
        return Response(for_submitter(visible_submission(request, submission_id)))


class SubmissionBundleView(HubView):
    def get(self, request, submission_id):
        found = visible_submission(request, submission_id)
        if found.submitted_by_id != request.user.pk and not is_maintainer(request.user):
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        if not found.raw_key or not storage.get_store().exists(found.raw_key):
            raise HubError("OMEGA_HUB_GONE", 410)
        return bytes_response(storage.read_bytes(found.raw_key), "application/octet-stream",
                              {"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{found.pk}.upload"'})


@workspace_read_only
class SubmissionWithdrawView(HubView):
    token_allowed = True

    def post(self, request, submission_id):
        found = visible_submission(request, submission_id)
        return Response(for_submitter(intake.withdraw(request.user, found)))


@workspace_read_only
class YankView(HubView):
    token_allowed = True

    def post(self, request, publisher, name, version):
        entry = live_entry(publisher, name)
        require_publisher(request, entry.publisher)
        found = entry.versions.filter(version=version).first()
        if found is None:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        yanked = moderation.yank_version(request.user, found, body(request).get("reason"), by_maintainer=False)
        return Response({"version": yanked.version, "status": yanked.status, "yankedReason": yanked.yanked_reason})


@workspace_read_only
class UnlistView(HubView):
    def post(self, request, publisher, name):
        entry = any_entry(publisher, name)
        require_publisher(request, entry.publisher)
        entry = moderation.unlist_entry(request.user, entry, body(request).get("note") or "")
        return Response({"ref": entry.ref, "status": entry.status})

