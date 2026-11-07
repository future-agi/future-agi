"""Literal collection routes precede UUID identity routes."""

from django.urls import path

from model_hub.views import skills_hub as views

urlpatterns = [
    path("", views.SkillListView.as_view(), name="skills-list"),
    path("templates/", views.SkillTemplatesView.as_view(), name="skills-templates"),
    path("checks/", views.SkillChecksView.as_view(), name="skills-checks"),
    path("publish/", views.SkillPushView.as_view(), name="skills-publish"),
    path("resolve/", views.SkillResolveView.as_view(), name="skills-resolve"),
    path("download/", views.SkillDownloadView.as_view(), name="skills-download"),
    path(
        "import/preview/",
        views.SkillImportPreviewView.as_view(),
        name="skills-import-preview",
    ),
    path("import/", views.SkillImportView.as_view(), name="skills-import"),
    path("<uuid:skill_id>/", views.SkillDetailView.as_view(), name="skills-detail"),
    path(
        "<uuid:skill_id>/versions/",
        views.SkillVersionsView.as_view(),
        name="skills-versions",
    ),
    path(
        "<uuid:skill_id>/versions/<int:number>/",
        views.SkillVersionView.as_view(),
        name="skills-version",
    ),
    path(
        "<uuid:skill_id>/versions/<int:number>/files/",
        views.SkillFileView.as_view(),
        name="skills-file",
    ),
    path(
        "<uuid:skill_id>/versions/<int:number>/download/",
        views.SkillVersionDownloadView.as_view(),
        name="skills-version-download",
    ),
    path(
        "<uuid:skill_id>/versions/<int:number>/restore/",
        views.SkillRestoreView.as_view(),
        name="skills-restore",
    ),
    path(
        "<uuid:skill_id>/compare/",
        views.SkillCompareView.as_view(),
        name="skills-compare",
    ),
    path(
        "<uuid:skill_id>/labels/", views.SkillLabelsView.as_view(), name="skills-labels"
    ),
    path(
        "<uuid:skill_id>/labels/history/",
        views.SkillLabelHistoryView.as_view(),
        name="skills-label-history",
    ),
    path(
        "<uuid:skill_id>/labels/<str:label>/",
        views.SkillLabelView.as_view(),
        name="skills-label",
    ),
    path("<uuid:skill_id>/draft/", views.SkillDraftView.as_view(), name="skills-draft"),
    path(
        "<uuid:skill_id>/draft/publish/",
        views.SkillDraftPublishView.as_view(),
        name="skills-draft-publish",
    ),
    path("<uuid:skill_id>/usage/", views.SkillUsageView.as_view(), name="skills-usage"),
]
