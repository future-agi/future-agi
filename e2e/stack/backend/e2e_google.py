"""Browser-scoped social-login visibility, installed only in the E2E image."""

from tfc.views.deployment import DeploymentInfoView


class GoogleFixtureDeploymentInfoView(DeploymentInfoView):
    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        # Only the owned Google flow uses this synthetic browser identity. Keep the ordinary OSS
        # deployment-info response for every other browser and API caller.
        if request.headers.get("User-Agent") == "FutureAGI-E2E-Google-Fixture":
            response.data["result"]["mode"] = "cloud"
        return response
