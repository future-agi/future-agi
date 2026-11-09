"""Human authoring is distinct from the isolated consumption credential facade."""

from django.urls import path

from model_hub.views import skills_context_authoring as v

urlpatterns = [
    path("contexts/", v.ContextListView.as_view()),
    path("contexts/capabilities/", v.ContextCapabilitiesView.as_view()),
    path("contexts/campaigns/", v.CampaignListView.as_view()),
    path("contexts/campaigns/<uuid:campaign_id>/", v.CampaignDetailView.as_view()),
    path(
        "contexts/campaigns/<uuid:campaign_id>/targets/",
        v.CampaignTargetsView.as_view(),
    ),
    path(
        "contexts/campaigns/<uuid:campaign_id>/targets/<uuid:binding_id>/plan/",
        v.CampaignPlanView.as_view(),
    ),
    path(
        "contexts/campaigns/<uuid:campaign_id>/targets/<uuid:binding_id>/claim/",
        v.CampaignClaimView.as_view(),
    ),
    path(
        "contexts/campaigns/<uuid:campaign_id>/targets/<uuid:binding_id>/attempts/",
        v.CampaignAttemptView.as_view(),
    ),
    path(
        "contexts/campaigns/<uuid:campaign_id>/authorize/",
        v.CampaignAuthorizeView.as_view(),
    ),
    path(
        "contexts/campaigns/<uuid:campaign_id>/pause/",
        v.CampaignStatusView.as_view(campaign_action="pause"),
    ),
    path(
        "contexts/campaigns/<uuid:campaign_id>/resume/",
        v.CampaignStatusView.as_view(campaign_action="resume"),
    ),
    path(
        "contexts/campaigns/<uuid:campaign_id>/cancel/",
        v.CampaignStatusView.as_view(campaign_action="cancel"),
    ),
    path(
        "contexts/campaigns/<uuid:campaign_id>/rollback/preview/",
        v.CampaignRollbackView.as_view(),
    ),
    path(
        "contexts/campaigns/<uuid:campaign_id>/targets/<uuid:binding_id>/observations/",
        v.CampaignObservationsView.as_view(),
    ),
    path("contexts/<uuid:context_id>/", v.ContextDetailView.as_view()),
    path("contexts/<uuid:context_id>/draft/", v.ContextDraftView.as_view()),
    path("contexts/<uuid:context_id>/draft/files/", v.ContextDraftFileView.as_view()),
    path(
        "contexts/<uuid:context_id>/release/review/",
        v.ContextReleaseReviewView.as_view(),
    ),
    path("contexts/<uuid:context_id>/draft/start/", v.ContextDraftStartView.as_view()),
    path(
        "contexts/<uuid:context_id>/dependencies/preview/",
        v.ContextDependenciesPreviewView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/dependencies/apply/",
        v.ContextDependenciesApplyView.as_view(),
    ),
    path("contexts/<uuid:context_id>/versions/", v.ContextVersionsView.as_view()),
    path(
        "contexts/<uuid:context_id>/versions/<int:number>/",
        v.ContextVersionView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/draft/head-review/",
        v.ContextHeadReviewView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/draft/acknowledge-head/",
        v.ContextHeadAcknowledgeView.as_view(),
    ),
    path("contexts/<uuid:context_id>/draft/publish/", v.ContextPublishView.as_view()),
]
