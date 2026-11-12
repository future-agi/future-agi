"""The list must know whether this deployment can store personal stars."""

from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory

from tfc.capabilities import views
from tfc.licensing.types import DeploymentFlavor, DeploymentLocation, LicenseState


@pytest.fixture
def capability_request(monkeypatch):
    # Isolate this read-only capability from licensing and rollout storage.
    monkeypatch.setattr(views, "FEATURE_REGISTRY", {})
    monkeypatch.setattr(views.CapabilitiesView, "_rollout_admissions", lambda *args: {})
    monkeypatch.setattr(
        views.service, "get_deployment_flavor", lambda: DeploymentFlavor.OSS
    )
    monkeypatch.setattr(
        views.service, "get_deployment_location", lambda: DeploymentLocation.SELF_HOSTED
    )
    monkeypatch.setattr(
        views.CapabilitiesView, "_get_license_state", lambda self: LicenseState.MISSING
    )
    request = APIRequestFactory().get("/api/capabilities/")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=False)
    return request


@pytest.mark.parametrize("flag", [False, None, "true", "false", 1, True])
def test_w19_star_capability_matches_exact_server_gate(settings, capability_request, flag):
    settings.DSX_DATASET_STARS_ENABLED = flag

    response = views.CapabilitiesView().get(capability_request)

    assert response.status_code == 200
    feature = response.data["features"]["dataset_stars"]
    assert feature["allowed"] is (flag is True)
    assert feature["display_name"] == "Star datasets"
    assert feature["reason_code"] == (None if flag is True else "SERVICE_UNAVAILABLE")
    assert feature["requires_network"] is False
    assert feature["oss_baseline"] is True
    assert settings.DSX_DATASET_STARS_ENABLED == flag


def test_w19_star_capability_is_off_when_setting_missing(settings, capability_request):
    del settings.DSX_DATASET_STARS_ENABLED

    response = views.CapabilitiesView().get(capability_request)

    assert response.data["features"]["dataset_stars"]["allowed"] is False
