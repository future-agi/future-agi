"""Operational scrape endpoints for the platform monitor."""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework.views import APIView

from omega_hub.auth import MetricsAuthentication
from omega_hub.errors import hub_exception_handler
from omega_hub.permissions import IsMetrics
from omega_hub.services.metrics import prometheus


class MetricsView(APIView):
    """Prometheus text format with a read-only metrics credential.

    The checker token can claim jobs and post verdicts, so it must never be
    accepted by this endpoint.
    """

    authentication_classes = [MetricsAuthentication]
    permission_classes = [IsMetrics]
    throttle_classes: list = []
    versioning_class = None

    def get_exception_handler(self):
        return hub_exception_handler

    def get(self, request):
        response = HttpResponse(prometheus(), content_type="text/plain; version=0.0.4; charset=utf-8")
        response["Cache-Control"] = "no-store"
        return response


__all__ = ["MetricsView"]
