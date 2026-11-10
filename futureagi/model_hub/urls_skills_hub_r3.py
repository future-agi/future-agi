"""Shared round-three administration routes precede legacy resource routes."""

from django.urls import path

from model_hub.views.skills_hub_r3 import (
    DraftCheckpointRestoreView,
    DraftCheckpointsView,
    SpendPolicyView,
)
from model_hub.views.skills_workflows import (
    EvidenceAnnotationsView,
    EvidenceReferenceView,
    IntakeAcceptView,
    IntakePlanView,
    IntakePreviewView,
    IntakeReceiptsView,
    IntakeReconcileView,
    TraceCaptureAcceptView,
    TraceCapturePreviewView,
    TraceCaptureSourceView,
)

urlpatterns = [
    path("intake/preview/", IntakePreviewView.as_view(), name="skills-intake-preview"),
    path("intake/<uuid:plan_id>/", IntakePlanView.as_view(), name="skills-intake-plan"),
    path(
        "intake/<uuid:plan_id>/accept/",
        IntakeAcceptView.as_view(),
        name="skills-intake-accept",
    ),
    path(
        "intake/<uuid:plan_id>/reconcile/",
        IntakeReconcileView.as_view(),
        name="skills-intake-reconcile",
    ),
    path(
        "intake/<uuid:plan_id>/receipts/",
        IntakeReceiptsView.as_view(),
        name="skills-intake-receipts",
    ),
    path(
        "contexts/<uuid:context_id>/captures/source/",
        TraceCaptureSourceView.as_view(),
        name="context-capture-source",
    ),
    path(
        "contexts/<uuid:context_id>/captures/preview/",
        TraceCapturePreviewView.as_view(),
        name="context-capture-preview",
    ),
    path(
        "contexts/<uuid:context_id>/captures/<uuid:capture_id>/accept/",
        TraceCaptureAcceptView.as_view(),
        name="context-capture-accept",
    ),
    path(
        "evidence/<str:source>/<uuid:source_id>/",
        EvidenceReferenceView.as_view(),
        name="skills-evidence-reference",
    ),
    path(
        "evidence/<str:source>/<uuid:source_id>/annotations/",
        EvidenceAnnotationsView.as_view(),
        name="skills-evidence-annotations",
    ),
    path(
        "<uuid:resource_id>/draft/checkpoints/<uuid:checkpoint_id>/restore/",
        DraftCheckpointRestoreView.as_view(),
        name="skill-checkpoint-restore",
    ),
    path(
        "contexts/<uuid:resource_id>/draft/checkpoints/<uuid:checkpoint_id>/restore/",
        DraftCheckpointRestoreView.as_view(resource_kind="agent_context"),
        name="context-checkpoint-restore",
    ),
    path("spend-policy/", SpendPolicyView.as_view(), name="skills-spend-policy"),
    path(
        "<uuid:resource_id>/draft/checkpoints/",
        DraftCheckpointsView.as_view(),
        name="skill-checkpoints",
    ),
    path(
        "contexts/<uuid:resource_id>/draft/checkpoints/",
        DraftCheckpointsView.as_view(resource_kind="agent_context"),
        name="context-checkpoints",
    ),
]
