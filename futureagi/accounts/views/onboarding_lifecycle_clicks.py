from __future__ import annotations

from django.shortcuts import redirect
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from accounts.services.onboarding.lifecycle_clicks import resolve_lifecycle_click


class OnboardingLifecycleClickView(APIView):
    swagger_schema = None
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        _send_log, route = resolve_lifecycle_click(
            request.query_params.get("token", "")
        )
        return redirect(route)
