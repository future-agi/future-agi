"""Literal release-review routes, independent of consumption credentials."""

from django.urls import path

from model_hub.views import skills_release_requests as v

urlpatterns = [
    path("release-requests/preview/", v.PacketPreviewView.as_view()),
    path("release-requests/", v.RequestListView.as_view()),
    path("release-requests/<uuid:request_id>/", v.RequestDetailView.as_view()),
    path(
        "release-requests/<uuid:request_id>/decisions/", v.RequestDecisionView.as_view()
    ),
    path("release-requests/<uuid:request_id>/execute/", v.RequestExecuteView.as_view()),
    path("release-requests/<uuid:request_id>/receipt/", v.RequestReceiptView.as_view()),
    path("review-inbox/", v.ReviewInboxView.as_view()),
    path("review-inbox/<uuid:request_id>/seen/", v.ReviewSeenView.as_view()),
]
