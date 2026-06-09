import os

from rest_framework.views import APIView

from tfc.utils.general_methods import GeneralMethods


class DeploymentInfoView(APIView):
    """Public deployment-mode probe used by the frontend to gate UI.

    Returns ``{"mode": "oss"|"ee"|"cloud"}``. No auth — public config.
    """

    authentication_classes = []
    permission_classes = []

    def get(self, request, *args, **kwargs):
        if os.environ.get("CLOUD_DEPLOYMENT", "") in ("US", "EU", "DEV"):
            mode = "cloud"
        elif os.environ.get("EE_LICENSE_KEY", ""):
            mode = "ee"
        else:
            mode = "oss"
        gm = GeneralMethods(request)
        return gm.success_response({"mode": mode})
