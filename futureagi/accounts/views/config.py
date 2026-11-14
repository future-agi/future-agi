from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    PublicConfigResponseSerializer,
)


def _parse_regions():
    """Parse AVAILABLE_REGIONS env var (comma-separated code:label:app_url entries)."""
    regions = []
    if not settings.AVAILABLE_REGIONS:
        return regions
    for entry in settings.AVAILABLE_REGIONS.split(","):
        parts = entry.strip().split(":", 2)
        if len(parts) >= 3:
            regions.append(
                {
                    "code": parts[0],
                    "label": parts[1],
                    "app_url": parts[2],
                }
            )
    return regions


@swagger_auto_schema(
    method="get", responses={200: PublicConfigResponseSerializer, **ACCOUNTS_ERROR_RESPONSES}
)
@api_view(["GET"])
@permission_classes([AllowAny])
def public_config(request):
    """
    Public (unauthenticated) endpoint returning platform config.
    Used by the frontend to decide whether to show region UI.

    Self-hosted returns cloud=false with no region info.
    Cloud returns the current region and available regions list.

    """
    try:
        from ee.usage.deployment import DeploymentMode

        is_cloud = DeploymentMode.is_cloud()
    except ImportError:
        is_cloud = False

    return Response(
        {
            "status": True,
            "result": {
                "cloud": is_cloud,
                "region": settings.REGION if is_cloud else None,
                "available_regions": _parse_regions() if is_cloud else [],
            },
        }
    )
