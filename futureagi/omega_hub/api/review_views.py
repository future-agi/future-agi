"""The review queue and decisions (SPEC §5.2-§5.4, §9.4). Maintainers only."""

from __future__ import annotations

import posixpath

from rest_framework.response import Response

from accounts.authentication import workspace_read_only
from omega_hub import rules
from omega_hub.api.base import MaintainerView, body, bytes_response
from omega_hub.api.lookups import submission as find_submission
from omega_hub.api.serializers.cards import version_row
from omega_hub.api.serializers.submissions import for_submitter, queue_row
from omega_hub.errors import HubError
from omega_hub.models import Entry, Report, Submission
from omega_hub.services import bundles, review, storage
from omega_hub.services.checks import latest_job
from omega_hub.services.publish import flag_ids, report_step


class ReviewQueueView(MaintainerView):
    def get(self, request):
        params = request.query_params
        status = params.get("status") or "in_review"
        if status not in ("in_review", "check_error"):
            raise HubError("OMEGA_HUB_INVALID", 400, "status is in_review or check_error")
        rows = Submission.objects.select_related("publisher", "reviewer").filter(status=status).order_by("created_at")
        if params.get("tier"):
            rows = rows.filter(publisher__tier=params["tier"])
        if params.get("channel"):
            rows = rows.filter(channel=params["channel"])
        if params.get("update") in ("1", "true"):
            rows = rows.filter(previous_version__isnull=False)
        if params.get("update") in ("0", "false"):
            rows = rows.filter(previous_version__isnull=True)
        out = [queue_row(row) for row in rows]
        if params.get("kind"):
            out = [row for row in out if row["kind"] == params["kind"]]
        if params.get("flags") in ("1", "true"):
            out = [row for row in out if row["flags"] > 0]
        return Response({"count": len(out), "results": out})


def _bundle_files(submission: Submission):
    job = latest_job(submission)
    if job is None or not job.canonical_bundle_key:
        return None
    return bundles.read_canonical(storage.read_bytes(job.canonical_bundle_key))


class ReviewSubmissionView(MaintainerView):
    def get(self, request, submission_id):
        submission = find_submission(submission_id)
        job = latest_job(submission)
        files = _bundle_files(submission)
        publisher = submission.publisher
        log = storage.read_bytes(job.log_key).decode("utf-8", "replace") if job and job.log_key else None
        previous = submission.previous_version
        return Response({
            **for_submitter(submission),
            "submittedBy": ({"id": str(submission.submitted_by_id), "email": submission.submitted_by.email}
                            if submission.submitted_by_id else None),
            "reviewer": submission.reviewer.email if submission.reviewer_id else None,
            "reviewStartedAt": submission.review_started_at.isoformat() if submission.review_started_at else None,
            "manifest": submission.manifest,
            "draftDisclosure": (submission.manifest or {}).get("disclosure"),
            "readme": bundles.readme_of(files) if files else None,
            "files": bundles.file_list(files) if files else [],
            "report": job.report if job else None,
            "job": {"id": str(job.pk), "status": job.status, "attempt": job.attempt} if job else None,
            "flags": flag_ids(job.report if job else None),
            "observed": report_step(job.report if job else None, "capabilities").get("observed") or {},
            "log": log,
            "previousVersion": version_row(previous) if previous else None,
            "remixOf": (submission.manifest or {}).get("remix_of"),
            "publisherHistory": {
                "handle": publisher.handle, "tier": publisher.tier,
                "entryCount": Entry.objects.filter(publisher=publisher, status=Entry.Status.LISTED).count(),
                "pastRejections": Submission.objects.filter(publisher=publisher, status=Submission.Status.REJECTED).count(),
                "openReports": Report.objects.filter(entry__publisher=publisher, status=Report.Status.OPEN).count(),
            },
            "rulesVersion": rules.current_version(),
            "checklist": rules.get_rules()["checklist"],
        })


class ReviewDiffView(MaintainerView):
    def get(self, request, submission_id):
        job = latest_job(find_submission(submission_id))
        step = report_step(job.report if job else None, "diff")
        return Response({"previous": step.get("previous"), "diff": step.get("diff")})


class ReviewFileView(MaintainerView):
    """A submission file as plain text; never rendered as HTML (SPEC §5.2)."""

    def get(self, request, submission_id, path):
        clean = posixpath.normpath(path)
        if clean != path or clean.startswith(("/", "..")):
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        files = _bundle_files(find_submission(submission_id))
        data = bundles.file_bytes(files, path) if files else None
        if data is None:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        return bytes_response(data[: 64 * 1024], "text/plain; charset=utf-8",
                              {"Content-Security-Policy": "default-src 'none'", "Cache-Control": "no-store"})


@workspace_read_only
class ReviewStartView(MaintainerView):
    take_over = False

    def post(self, request, submission_id):
        submission = review.start_review(request.user, find_submission(submission_id), take_over=self.take_over)
        return Response(queue_row(submission))


class ReviewTakeOverView(ReviewStartView):
    take_over = True


@workspace_read_only
class ReviewDecisionView(MaintainerView):
    def post(self, request, submission_id):
        submission = review.decide(request.user, find_submission(submission_id), body(request))
        return Response(for_submitter(submission))


@workspace_read_only
class ReviewRecheckView(MaintainerView):
    def post(self, request, submission_id):
        submission = review.recheck(request.user, find_submission(submission_id))
        return Response(for_submitter(submission))
