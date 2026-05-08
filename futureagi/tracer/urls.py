from django.urls import path  # type: ignore
from rest_framework.routers import DefaultRouter

from tracer.views.annotation import (
    BulkAnnotationView,
    GetAnnotationLabelsView,
    TraceAnnotationView,
)
from tracer.views.charts import ChartsView
from tracer.views.custom_eval_config import CustomEvalConfigView
from tracer.views.dashboard import DashboardViewSet, DashboardWidgetViewSet
from tracer.views.dataset import DatasetView
from tracer.views.error_analysis import (
    ErrorClusterDetailView,
    ErrorClusterFeedView,
    TraceErrorAnalysisView,
    TraceErrorTaskView,
)
from tracer.views.eval_task import EvalTaskView
from tracer.views.feed import (
    CreateLinearIssueView,
    FeedDeepAnalysisView,
    FeedDetailView,
    FeedListView,
    FeedOverviewView,
    FeedRootCauseView,
    FeedSidebarView,
    FeedStatsView,
    FeedTracesView,
    FeedTrendsView,
    LinearTeamsView,
)
from tracer.views.http_otlp import OTLPTraceHTTPView
from tracer.views.imagine_analysis import ImagineAnalysisView
from tracer.views.monitor import UserAlertMonitorLogView, UserAlertMonitorView
from tracer.views.observability_provider import (
    ObservabilityProviderViewSet,
    WebhookHandlerView,
)
from tracer.views.observation_span import ObservationSpanView
from tracer.views.otlp import OTLPHealthView, OTLPTraceView
from tracer.views.project import ProjectView
from tracer.views.project_version import ProjectVersionView
from tracer.views.replay_session import ReplaySessionView
from tracer.views.saved_view import SavedViewViewSet
from tracer.views.shared_link import SharedLinkViewSet, resolve_shared_link, query_shared_widget
from tracer.views.trace import GetUserCodeExampleView, TraceView, UsersView
from tracer.views.trace_session import TraceSessionView

router = DefaultRouter()

router.register(r"project", ProjectView, basename="project")
router.register(r"project-version", ProjectVersionView, basename="project-version")
router.register(r"trace", TraceView, basename="trace")
router.register(r"observation-span", ObservationSpanView, basename="observation-span")
router.register(r"trace-session", TraceSessionView, basename="trace-session")
router.register(r"eval-task", EvalTaskView, basename="eval-task")
router.register(r"user-alerts", UserAlertMonitorView, basename="user-alerts")
router.register(r"user-alert-logs", UserAlertMonitorLogView, basename="user-alert-logs")
router.register(
    r"custom-eval-config", CustomEvalConfigView, basename="custom-eval-config"
)
router.register(r"dataset", DatasetView, basename="dataset")
router.register(r"trace-annotation", TraceAnnotationView, basename="trace-annotation")
router.register(r"charts", ChartsView, basename="charts")
router.register(
    r"observability-provider",
    ObservabilityProviderViewSet,
    basename="observability-provider",
)
router.register(r"replay-session", ReplaySessionView, basename="replay-session")
router.register(r"saved-views", SavedViewViewSet, basename="saved-view")
router.register(r"shared-links", SharedLinkViewSet, basename="shared-link")
router.register(r"dashboard", DashboardViewSet, basename="dashboard")

urlpatterns = [
    # Imagine analysis — trigger + poll for dynamic analysis results
    path("imagine-analysis/", ImagineAnalysisView.as_view(), name="imagine-analysis"),
    # Agent graph — explicit path because @action doesn't register reliably with Granian reload
    path(
        "trace/agent_graph/",
        TraceView.as_view({"get": "agent_graph"}),
        name="trace-agent-graph",
    ),
    # Standard OTLP endpoints
    path("v1/traces", OTLPTraceView.as_view(), name="otlp-traces"),
    path("v1/traces/", OTLPTraceView.as_view(), name="otlp-traces-slash"),
    path("v1/health", OTLPHealthView.as_view(), name="otlp-health"),
    # Legacy endpoint (deprecated, use v1/traces)
    path("otlp/v1/traces", OTLPTraceHTTPView.as_view(), name="otel-traces-http-legacy"),
    path("bulk-annotation/", BulkAnnotationView.as_view(), name="bulk-annotations"),
    path(
        "get-annotation-labels/",
        GetAnnotationLabelsView.as_view(),
        name="get-annotation-labels",
    ),
    path("shared/<str:token>/", resolve_shared_link, name="resolve-shared-link"),
    path("shared/<str:token>/widget-query/", query_shared_widget, name="query-shared-widget"),
    path("users/", UsersView.as_view(), name="users"),
    path("users/get_code_example/", GetUserCodeExampleView.as_view(), name="users"),
    # Deprecated — replaced by /feed/ endpoints (TH-3816 Phase 5)
    # path(
    #     "trace-error-analysis/clusters/feed/",
    #     ErrorClusterFeedView.as_view(),
    #     name="error-clusters-feed",
    # ),
    # path(
    #     "trace-error-analysis/clusters/<str:cluster_id>/",
    #     ErrorClusterDetailView.as_view(),
    #     name="error-cluster-detail",
    # ),
    path(
        "trace-error-analysis/<str:trace_id>/",
        TraceErrorAnalysisView.as_view(),
        name="trace-error-analysis",
    ),
    path(
        "trace-error-task/<str:project_id>/",
        TraceErrorTaskView.as_view(),
        name="trace-error-task",
    ),
    # Error Feed API
    path("feed/issues/", FeedListView.as_view(), name="feed-issues-list"),
    path("feed/issues/stats/", FeedStatsView.as_view(), name="feed-issues-stats"),
    path(
        "feed/issues/<str:cluster_id>/",
        FeedDetailView.as_view(),
        name="feed-issue-detail",
    ),
    path(
        "feed/issues/<str:cluster_id>/overview/",
        FeedOverviewView.as_view(),
        name="feed-issue-overview",
    ),
    path(
        "feed/issues/<str:cluster_id>/traces/",
        FeedTracesView.as_view(),
        name="feed-issue-traces",
    ),
    path(
        "feed/issues/<str:cluster_id>/trends/",
        FeedTrendsView.as_view(),
        name="feed-issue-trends",
    ),
    path(
        "feed/issues/<str:cluster_id>/sidebar/",
        FeedSidebarView.as_view(),
        name="feed-issue-sidebar",
    ),
    path(
        "feed/issues/<str:cluster_id>/root-cause/",
        FeedRootCauseView.as_view(),
        name="feed-issue-root-cause",
    ),
    path(
        "feed/issues/<str:cluster_id>/deep-analysis/",
        FeedDeepAnalysisView.as_view(),
        name="feed-issue-deep-analysis",
    ),
    # Linear integration
    path(
        "feed/issues/<str:cluster_id>/create-linear-issue/",
        CreateLinearIssueView.as_view(),
        name="feed-create-linear-issue",
    ),
    path(
        "feed/integrations/linear/teams/",
        LinearTeamsView.as_view(),
        name="feed-linear-teams",
    ),
    path(
        "webhook/",
        WebhookHandlerView.as_view(),
        name="webhook-handler",
    ),
    # Dashboard widget nested routes
    path(
        "dashboard/<uuid:dashboard_pk>/widgets/",
        DashboardWidgetViewSet.as_view({"get": "list", "post": "create"}),
        name="dashboard-widget-list",
    ),
    path(
        "dashboard/<uuid:dashboard_pk>/widgets/preview/",
        DashboardWidgetViewSet.as_view({"post": "preview_query"}),
        name="dashboard-widget-preview",
    ),
    path(
        "dashboard/<uuid:dashboard_pk>/widgets/reorder/",
        DashboardWidgetViewSet.as_view({"post": "reorder"}),
        name="dashboard-widget-reorder",
    ),
    path(
        "dashboard/<uuid:dashboard_pk>/widgets/<uuid:pk>/",
        DashboardWidgetViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="dashboard-widget-detail",
    ),
    path(
        "dashboard/<uuid:dashboard_pk>/widgets/<uuid:pk>/query/",
        DashboardWidgetViewSet.as_view({"post": "execute_query"}),
        name="dashboard-widget-query",
    ),
    path(
        "dashboard/<uuid:dashboard_pk>/widgets/<uuid:pk>/duplicate/",
        DashboardWidgetViewSet.as_view({"post": "duplicate_widget"}),
        name="dashboard-widget-duplicate",
    ),
    *router.urls,
]
