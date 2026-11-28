"""Literal purpose credential/facade routes, before legacy UUID routes."""

from django.urls import path

from model_hub.views.skills_context_foundation import (
    ConsumptionAccessView,
    ConsumptionFileView,
    ConsumptionManifestView,
    ConsumptionResolveView,
    ContextCredentialsView,
    ContextCredentialView,
)

urlpatterns = [
    path(
        "contexts/credentials/",
        ContextCredentialsView.as_view(),
        name="context-credentials",
    ),
    path(
        "contexts/credentials/<uuid:credential_id>/",
        ContextCredentialView.as_view(),
        name="context-credential",
    ),
]
for prefix, kind in (("consumption/", "skill"), ("contexts/", "agent_context")):
    urlpatterns += [
        path(
            prefix + "resolve/",
            ConsumptionResolveView.as_view(resource_kind=kind),
            name=f"{kind}-consume-resolve",
        ),
        path(
            prefix + "<uuid:resource_id>/versions/<int:number>/manifest/",
            ConsumptionManifestView.as_view(resource_kind=kind),
            name=f"{kind}-consume-manifest",
        ),
        path(
            prefix + "<uuid:resource_id>/versions/<int:number>/files/",
            ConsumptionFileView.as_view(resource_kind=kind),
            name=f"{kind}-consume-file",
        ),
        path(
            prefix + "<uuid:resource_id>/versions/<int:number>/access-check/",
            ConsumptionAccessView.as_view(resource_kind=kind),
            name=f"{kind}-consume-access",
        ),
    ]
