"""``GET /webhooks/ops/stats/`` (staff only): the gauges the health pass computed last, with each ops alert's state
(DESIGN.md section 13.2). Install-wide counts only: no endpoint, URL, secret or body of any tenant.

Not part of the public OpenAPI: the webhooks API is section 10's routes; this is an operations route for staff
(FA's ``swagger_auto_schema(auto_schema=None)`` convention)."""

from __future__ import annotations

from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.utils.api_errors import build_error_envelope
from webhooks.models import OpsSnapshot

NOT_COMPUTED = "The health pass has not run yet."


class OpsStatsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(auto_schema=None)
    def get(self, request):
        snap = OpsSnapshot.objects.filter(id="stats").first()
        if snap is None:
            # FA's common error envelope (as every webhooks refusal, ``webhooks.api_errors``), with ``computed_at``
            # still present so a dashboard reads one key either way.
            return Response(
                build_error_envelope(
                    NOT_COMPUTED,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    extra={"computed_at": None},
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({**snap.data, "computed_at": snap.computed_at.isoformat()})
