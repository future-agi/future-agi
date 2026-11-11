"""Literal release-review routes, independent of consumption credentials."""

from django.urls import path

from model_hub.views import skills_release_requests as v

urlpatterns = [
    path("release-requests/options/", v.ReleaseOptionsView.as_view()),
    path("release-requests/inspection/", v.ReleaseInspectionView.as_view()),
    path(
        "release-requests/native-evidence/<uuid:evidence_id>/share/",
        v.NativeEvidenceShareView.as_view(),
    ),
    path("release-requests/preview/", v.PacketPreviewView.as_view()),
    path("release-requests/", v.RequestListView.as_view()),
    path("release-requests/<uuid:request_id>/", v.RequestDetailView.as_view()),
    path(
        "release-requests/<uuid:request_id>/decisions/", v.RequestDecisionView.as_view()
    ),
    path("release-requests/<uuid:request_id>/execute/", v.RequestExecuteView.as_view()),
    path("release-requests/<uuid:request_id>/receipt/", v.RequestReceiptView.as_view()),
    path("release-requests/<uuid:request_id>/renewal/", v.RequestRenewalView.as_view()),
    path("review-inbox/", v.ReviewInboxView.as_view()),
    path("review-inbox/<uuid:request_id>/seen/", v.ReviewSeenView.as_view()),
]
