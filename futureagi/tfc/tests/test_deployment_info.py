"""GET /api/deployment-info/: the mode the frontend gates Cloud and EE UI on."""

import pytest
from rest_framework.test import APIRequestFactory

from tfc.views.deployment import DeploymentInfoView

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "deployment, license_key, mode",
    [
        ("US", "", "cloud"),
        (" us ", "", "cloud"),
        ("false", "key", "ee"),
        ("false", "", "oss"),
        ("", "", "oss"),
    ],
)
def test_reads_the_region_as_is_cloud_env_does(
    monkeypatch, deployment, license_key, mode
):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", deployment)
    monkeypatch.setenv("EE_LICENSE_KEY", license_key)

    request = APIRequestFactory().get("/api/deployment-info/")
    response = DeploymentInfoView.as_view()(request)

    assert response.status_code == 200
    assert response.data["result"] == {"mode": mode}
