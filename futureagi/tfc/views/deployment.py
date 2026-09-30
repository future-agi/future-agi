import os

from rest_framework.views import APIView

from tfc.ee_loader import is_cloud_env
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiTextErrorResponseSerializer,
    DeploymentInfoResponseSerializer,
)
from tfc.utils.general_methods import GeneralMethods


class DeploymentInfoView(APIView):
    """Public deployment-mode probe used by the frontend to gate UI.

    Returns ``{"mode": "oss"|"ee"|"cloud"}``. No auth — public config.
    """

    authentication_classes = []
    permission_classes = []

    @validated_request(
        responses={
            200: DeploymentInfoResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def get(self, request, *args, **kwargs):
        if is_cloud_env():
            mode = "cloud"
        elif os.environ.get("EE_LICENSE_KEY", ""):
            mode = "ee"
        else:
            mode = "oss"
        gm = GeneralMethods(request)
        return gm.success_response({"mode": mode})
