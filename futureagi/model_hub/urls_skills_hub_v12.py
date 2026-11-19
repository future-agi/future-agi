"""Context administration and purpose-bound sessions; no consumption shadowing."""

from django.urls import path

from model_hub.views import skills_context_testing as testing
from model_hub.views import skills_hub_v12 as views
from model_hub.views import skills_native_runs as native
from model_hub.views.skills_context_dependents import SkillContextDependentsView

# J3: whole-context test APIs stay separate from the leaf-only routes.
urlpatterns = [
    path("<uuid:skill_id>/context-dependents/", SkillContextDependentsView.as_view()),
    # J2: reviewed context cases must not use the isolated leaf suite route.
    path(
        "contexts/<uuid:context_id>/tests/",
        testing.ContextTestSuiteView.as_view(),
        name="context-test-suite",
    ),
    path(
        "contexts/<uuid:context_id>/test-runs/preview/",
        testing.ContextTestPreviewView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/test-runs/", testing.ContextTestRunsView.as_view()
    ),
    path(
        "contexts/<uuid:context_id>/test-runs/<uuid:run_id>/",
        testing.ContextTestRunView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/test-runs/<uuid:run_id>/results/",
        testing.ContextTestResultsView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/test-runs/<uuid:run_id>/attempts/<uuid:attempt_id>/",
        testing.ContextTestAttemptView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/test-runs/<uuid:run_id>/share/",
        testing.ContextTestShareView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/test-runs/<uuid:run_id>/cancel/",
        testing.ContextTestCancelView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/test-runs/<uuid:run_id>/files/",
        testing.ContextTestFileView.as_view(),
    ),
    # J2: matched native runs of one qualified application, and the authenticated
    # guest channel of the hosted native runtime (attempt bearer + fence).
    path(
        "contexts/<uuid:context_id>/native/targets/", native.NativeTargetsView.as_view()
    ),
    path(
        "contexts/<uuid:context_id>/native/runtime-configuration/",
        native.NativeRuntimeConfigurationView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/native/targets/<uuid:binding_id>/",
        native.NativeTargetView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/native/preview/", native.NativePreviewView.as_view()
    ),
    path("contexts/<uuid:context_id>/native/runs/", native.NativeRunsView.as_view()),
    path(
        "contexts/<uuid:context_id>/native/runs/<uuid:evidence_id>/",
        native.NativeRunView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/native/runs/<uuid:evidence_id>/share/",
        native.NativeRunShareView.as_view(),
    ),
    path(
        "native/attempts/<uuid:attempt_id>/health/",
        native.NativeGuestHealthView.as_view(),
    ),
    path(
        "native/attempts/<uuid:attempt_id>/observations/",
        native.NativeGuestObservationView.as_view(),
    ),
    path(
        "native/attempts/<uuid:attempt_id>/failure/",
        native.NativeGuestFailureView.as_view(),
    ),
    path(
        "contexts/<uuid:context_id>/consumers/",
        views.ContextConsumersView.as_view(),
        name="context-consumers",
    ),
    path(
        "contexts/<uuid:context_id>/versions/<int:number>/inspection/",
        views.VersionInspectionView.as_view(),
        name="context-inspection",
    ),
    path(
        "contexts/<uuid:context_id>/versions/<int:number>/inspection/files/",
        views.VersionInspectionFileView.as_view(),
        name="context-inspection-file",
    ),
    path(
        "<uuid:context_id>/versions/<int:number>/inspection/",
        views.VersionInspectionView.as_view(resource_kind="skill"),
        name="skill-inspection",
    ),
    path(
        "<uuid:context_id>/versions/<int:number>/inspection/files/",
        views.VersionInspectionFileView.as_view(resource_kind="skill"),
        name="skill-inspection-file",
    ),
    path(
        "contexts/<uuid:context_id>/bindings/",
        views.ContextBindingsView.as_view(),
        name="context-bindings",
    ),
    path(
        "contexts/bindings/<uuid:binding_id>/",
        views.ContextBindingView.as_view(),
        name="context-binding",
    ),
    path(
        "contexts/bindings/<uuid:binding_id>/sessions/",
        views.ContextSessionsView.as_view(),
        name="context-sessions",
    ),
    path(
        "contexts/bindings/<uuid:binding_id>/sessions/<uuid:session_id>/access-check/",
        views.ContextSessionAccessView.as_view(),
        name="context-session-access",
    ),
    path(
        "contexts/bindings/<uuid:binding_id>/receipts/",
        views.ContextReceiptsView.as_view(),
        name="context-receipts",
    ),
    path(
        "contexts/<uuid:context_id>/release-state/",
        views.ContextReleaseStateView.as_view(),
        name="context-release-state",
    ),
    path(
        "version-safety/<uuid:version_id>/",
        views.VersionSafetyView.as_view(),
        name="skills-version-safety",
    ),
]
