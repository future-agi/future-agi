"""Omega Hub routes, mounted by the integrator at `omega-hub/` (SPEC §9, §15 item 2).

There is no route for comments, discussions, questions, replies or threads (SPEC §2.2).
"""

from django.urls import path

from omega_hub.api import checker_views as checker
from omega_hub.api import me_views as me
from omega_hub.api import moderation_views as mod
from omega_hub.api import ops_views as ops
from omega_hub.api import public_views as public
from omega_hub.api import review_views as review
from omega_hub.api import submission_views as subs

E = "v1/entries/<str:publisher>/<str:name>"
V = f"{E}/versions/<str:version>"
R = "v1/review"
J = "internal/checker/jobs/<uuid:job_id>"

app_name = "omega_hub"

urlpatterns = [
    # Public reads (SPEC §9.2)
    path("v1/entries", public.EntriesView.as_view(), name="entries"),
    path(E, public.EntryView.as_view(), name="entry"),
    path(f"{E}/versions", public.EntryVersionsView.as_view(), name="entry-versions"),
    path(V, public.VersionView.as_view(), name="version"),
    path(f"{V}/files/<path:path>", public.VersionFileView.as_view(), name="version-file"),
    path(f"{V}/bundle", public.VersionBundleView.as_view(), name="version-bundle"),
    path(f"{V}/cast", public.VersionCastView.as_view(), name="version-cast"),
    path(f"{E}/remixes", public.EntryRemixesView.as_view(), name="entry-remixes"),
    path("v1/publishers", me.PublishersCreateView.as_view(), name="publishers"),
    path("v1/publishers/<str:handle>", me.PublisherView.as_view(), name="publisher"),
    path("v1/publishers/<str:handle>/avatar", me.PublisherAvatarView.as_view(), name="publisher-avatar"),
    path("v1/picks", public.PicksView.as_view(), name="picks"),
    path("v1/tags", public.TagsView.as_view(), name="tags"),
    path("v1/export", public.ExportView.as_view(), name="export"),
    path("v1/rules", public.RulesView.as_view(), name="rules"),
    path("v1/blocklist", public.BlocklistView.as_view(), name="blocklist"),
    # Signed in (SPEC §9.3)
    path("v1/me", me.MeView.as_view(), name="me"),
    path("v1/me/tokens", me.TokensView.as_view(), name="tokens"),
    path("v1/me/tokens/<uuid:token_id>", me.TokenView.as_view(), name="token"),
    path("v1/me/likes", me.MyLikesView.as_view(), name="my-likes"),
    path("v1/me/email-verification", me.EmailVerificationView.as_view(), name="email-verification"),
    path("v1/me/email-verification/confirm", me.EmailVerificationConfirmView.as_view(), name="email-verification-confirm"),
    path("v1/publishers/<str:handle>/verification-request", me.VerificationRequestView.as_view(), name="verification-request"),
    path("v1/submissions", subs.SubmissionsView.as_view(), name="submissions"),
    path("v1/submissions/<uuid:submission_id>", subs.SubmissionView.as_view(), name="submission"),
    path("v1/submissions/<uuid:submission_id>/bundle", subs.SubmissionBundleView.as_view(), name="submission-bundle"),
    path("v1/submissions/<uuid:submission_id>/withdraw", subs.SubmissionWithdrawView.as_view(), name="submission-withdraw"),
    path(f"{E}/like", me.LikeView.as_view(), name="like"),
    path(f"{V}/yank", subs.YankView.as_view(), name="yank"),
    path(f"{E}/unlist", subs.UnlistView.as_view(), name="unlist"),
    path("v1/reports", me.ReportsView.as_view(), name="reports"),
    # Maintainers (SPEC §9.4)
    path(f"{R}/queue", review.ReviewQueueView.as_view(), name="review-queue"),
    path(f"{R}/submissions/<uuid:submission_id>", review.ReviewSubmissionView.as_view(), name="review-submission"),
    path(f"{R}/submissions/<uuid:submission_id>/diff", review.ReviewDiffView.as_view(), name="review-diff"),
    path(f"{R}/submissions/<uuid:submission_id>/files/<path:path>", review.ReviewFileView.as_view(), name="review-file"),
    path(f"{R}/submissions/<uuid:submission_id>/start", review.ReviewStartView.as_view(), name="review-start"),
    path(f"{R}/submissions/<uuid:submission_id>/take-over", review.ReviewTakeOverView.as_view(), name="review-take-over"),
    path(f"{R}/submissions/<uuid:submission_id>/decision", review.ReviewDecisionView.as_view(), name="review-decision"),
    path(f"{R}/submissions/<uuid:submission_id>/recheck", review.ReviewRecheckView.as_view(), name="review-recheck"),
    path(f"{R}/reports", mod.ReviewReportsView.as_view(), name="review-reports"),
    path(f"{R}/reports/<uuid:report_id>/resolve", mod.ReportResolveView.as_view(), name="review-report-resolve"),
    path(f"{R}/entries/<str:publisher>/<str:name>/remove", mod.EntryActionView.as_view(hub_action="remove"), name="review-remove"),
    path(f"{R}/entries/<str:publisher>/<str:name>/restore", mod.EntryActionView.as_view(hub_action="restore"), name="review-restore"),
    path(f"{R}/entries/<str:publisher>/<str:name>/unrank", mod.EntryActionView.as_view(hub_action="unrank"), name="review-unrank"),
    path(f"{R}/entries/<str:publisher>/<str:name>/rerank", mod.EntryActionView.as_view(hub_action="rerank"), name="review-rerank"),
    path(f"{R}/entries/<str:publisher>/<str:name>/move", mod.EntryActionView.as_view(hub_action="move"), name="review-move"),
    path(f"{R}/entries/<str:publisher>/<str:name>/likers", mod.LikersView.as_view(), name="review-likers"),
    path(f"{R}/publishers/<str:handle>/rename", mod.PublisherRenameView.as_view(), name="review-publisher-rename"),
    path(f"{R}/blocklist", mod.ReviewBlocklistView.as_view(), name="review-blocklist"),
    path(f"{R}/blocklist/<uuid:block_id>/lift", mod.BlocklistLiftView.as_view(), name="review-blocklist-lift"),
    path(f"{R}/appeals", mod.AppealsView.as_view(), name="review-appeals"),
    path(f"{R}/versions/<str:publisher>/<str:name>/<str:version>/yank", mod.MaintainerYankView.as_view(), name="review-yank"),
    path(f"{R}/picks", mod.ReviewPicksView.as_view(), name="review-picks"),
    path(f"{R}/picks/<uuid:pick_id>", mod.ReviewPickView.as_view(), name="review-pick"),
    path(f"{R}/publishers", mod.ReviewPublishersView.as_view(), name="review-publishers"),
    path(f"{R}/publishers/<str:handle>/verify", mod.PublisherActionView.as_view(hub_action="verify"), name="review-verify"),
    path(f"{R}/publishers/<str:handle>/unverify", mod.PublisherActionView.as_view(hub_action="unverify"), name="review-unverify"),
    path(f"{R}/publishers/<str:handle>/suspend", mod.PublisherActionView.as_view(hub_action="suspend"), name="review-suspend"),
    path(f"{R}/publishers/<str:handle>/unsuspend", mod.PublisherActionView.as_view(hub_action="unsuspend"), name="review-unsuspend"),
    path(f"{R}/audit", mod.AuditView.as_view(), name="review-audit"),
    # Checker (SPEC §9.4); `context` is the lane's addition, see openapi.yaml
    path("internal/ops/metrics", ops.MetricsView.as_view(), name="ops-metrics"),
    path("internal/checker/claim", checker.ClaimView.as_view(), name="checker-claim"),
    path(f"{J}/bundle", checker.JobBundleView.as_view(), name="checker-bundle"),
    path(f"{J}/previous-bundle", checker.JobPreviousBundleView.as_view(), name="checker-previous-bundle"),
    path(f"{J}/context", checker.JobContextView.as_view(), name="checker-context"),
    path(f"{J}/heartbeat", checker.JobHeartbeatView.as_view(), name="checker-heartbeat"),
    path(f"{J}/result", checker.JobResultView.as_view(), name="checker-result"),
]
