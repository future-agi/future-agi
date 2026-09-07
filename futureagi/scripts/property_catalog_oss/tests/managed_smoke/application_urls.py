"""Scoped catalog integration routes, not a full application startup test.

Register the unmodified application viewset through DRF's normal router. Keep
the configured middleware and authentication. The full tfc.urls import also
initializes unrelated NLTK downloaders and must be verified separately in the
release image; this fixture must not fake those dependencies or enable egress.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from tracer.views.dashboard import DashboardViewSet

router = DefaultRouter()
router.register("dashboard", DashboardViewSet, basename="dashboard")
urlpatterns = [path("tracer/", include(router.urls))]
