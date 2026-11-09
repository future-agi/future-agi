"""Shared round-three administration routes precede legacy resource routes."""

from django.urls import path

from model_hub.views.skills_hub_r3 import (
    DraftCheckpointRestoreView,
    DraftCheckpointsView,
    SpendPolicyView,
)

urlpatterns = [
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
