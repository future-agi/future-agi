"""
URL configuration for tfc project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from django.views.static import serve
from drf_yasg import openapi
from drf_yasg.views import get_schema_view

from tfc.ee_loader import ee_feature_enabled, has_ee
from tfc.views.deployment import DeploymentInfoView
from tfc.views.health import (
    AuthenticatedHealthView,
    HealthCheckView,
    LangfuseCompatTracesView,
)
from tfc.views.socket import CallWebsocketView
from tracer.views.clickhouse_health import ClickHouseHealthView
from tracer.views.langfuse_ingestion import LangfuseIngestionView
from tracer.views.otlp import OTLPHealthView, OTLPTraceView
from tracer.views.span_attributes import (
    SpanAttributeDetailView,
    SpanAttributeKeysView,
    SpanAttributeValuesView,
)

info_api = openapi.Info(
    title="Future AGI Management API",
    default_version="v1",
    description="The endpoints defined below allow users to programmatically carry out various actions on the Future AGI platform.",
    terms_of_service="https://futureagi.com/legal",
    contact=openapi.Contact(email="help@futureagi.com"),
    license=openapi.License(
        name="Apache 2.0", url="http://www.apache.org/licenses/LICENSE-2.0.html"
    ),
)
SWAGGER_URL = "http://localhost:8000"
schema_view = get_schema_view(info_api, public=True, url=SWAGGER_URL)

urlpatterns = [
    path("", RedirectView.as_view(url="/docs/", permanent=False), name="root"),
    # ===========================================
    # Standard OTLP Endpoints (OpenTelemetry Protocol)
    # https://opentelemetry.io/docs/specs/otlp/
    # ===========================================
    re_path(r"^v1/traces/?$", OTLPTraceView.as_view(), name="otlp-traces"),
    path("v1/health", OTLPHealthView.as_view(), name="otlp-health"),
    # ===========================================
    # Application Routes
    # ===========================================
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("model-hub/", include("model_hub.urls")),
    path("simulate/", include("simulate.urls")),
    path("agent-playground/", include("agent_playground.urls")),
    re_path(
        r"^swagger(?P<format>\.json|\.yaml)$",
        schema_view.without_ui(cache_timeout=0),
        name="schema-json",
    ),
    path("docs/", schema_view.with_ui("swagger", cache_timeout=0), name="swagger"),
    path("sdk/", include("sdk.urls")),
    path("tracer/", include("tracer.urls")),
    # Langfuse SDK compat: the SDK defaults to /api/public/otel/v1/traces
    path("api/public/otel/", include("tracer.otel_compat_urls")),
    # Langfuse-compatible endpoints (used by Vapi for credential validation)
    path(
        "api/public/health", AuthenticatedHealthView.as_view(), name="api-public-health"
    ),
    path(
        "api/public/traces",
        LangfuseCompatTracesView.as_view(),
        name="api-public-traces",
    ),
    path(
        "api/public/ingestion",
        LangfuseIngestionView.as_view(),
        name="api-public-ingestion",
    ),
    path("integrations/", include("integrations.urls")),
    path("agentcc/", include("agentcc.urls")),
    path("ai-tools/", include("ai_tools.urls")),
    path("mcp/", include("mcp_server.urls")),
    path(
        "falcon-ai/",
        include(
            "ee.falcon_ai.urls"
            if ee_feature_enabled("ee.falcon_ai")
            else "tfc.ee_stub_urls"
        ),
    ),
    path("saml2_auth/", include("saml2_auth.urls")),
    path("call-websocket/", CallWebsocketView.as_view(), name="call-websocket"),
    path("health/", HealthCheckView.as_view(), name="health-check"),
    path(
        "api/health/clickhouse/",
        ClickHouseHealthView.as_view(),
        name="health-clickhouse",
    ),
    # Span attribute discovery APIs (ClickHouse)
    path(
        "api/traces/span-attribute-keys/",
        SpanAttributeKeysView.as_view(),
        name="span-attribute-keys",
    ),
    path(
        "api/traces/span-attribute-values/",
        SpanAttributeValuesView.as_view(),
        name="span-attribute-values",
    ),
    path(
        "api/traces/span-attribute-detail/",
        SpanAttributeDetailView.as_view(),
        name="span-attribute-detail",
    ),
    path(
        "api/deployment-info/",
        DeploymentInfoView.as_view(),
        name="deployment-info",
    ),
]

if has_ee("ee.usage"):
    urlpatterns += [path("usage/", include("ee.usage.urls"))]

urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# Debug toolbar (only when DEBUG=True and installed)
if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    import debug_toolbar

    urlpatterns = [path("__debug__/", include(debug_toolbar.urls))] + urlpatterns

# Custom static file serving for staging/production when DEBUG=False
if not settings.DEBUG:
    # Add custom static file serving for staging/production
    urlpatterns += [
        re_path(
            r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT}
        ),
    ]
