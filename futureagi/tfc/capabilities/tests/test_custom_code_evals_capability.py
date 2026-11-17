"""The capabilities answer says whether custom code evaluations run here.

Create evaluation reads it to show its Code tab on or turned off, with the
sentence the server gives; the frontend keeps no copy of that sentence.
"""

from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory

from agentic_eval.core_evals.fi_utils.custom_code_pause import PAUSED_MESSAGE
from tfc.capabilities import views
from tfc.capabilities.contracts import CapabilitiesResponseSerializer
from tfc.licensing.types import DeploymentFlavor, DeploymentLocation, LicenseState


@pytest.fixture
def capability_request(monkeypatch):
    # Isolate this entry from licensing and rollout storage.
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


def test_custom_code_evals_are_on_when_the_deployment_runs_them(
    settings, capability_request
):
    settings.CUSTOM_CODE_EVALS_PAUSED = False

    response = views.CapabilitiesView().get(capability_request)

    assert response.status_code == 200
    assert response.data["features"]["custom_code_evals"] == {
        "kind": "service",
        "display_name": "Custom code evaluations",
        "allowed": True,
        "reason_code": None,
        "requires_network": False,
        "oss_baseline": True,
    }


def test_custom_code_evals_are_off_with_the_server_sentence_when_paused(
    settings, capability_request
):
    settings.CUSTOM_CODE_EVALS_PAUSED = True

    response = views.CapabilitiesView().get(capability_request)

    assert response.status_code == 200
    assert response.data["features"]["custom_code_evals"] == {
        "kind": "service",
        "display_name": "Custom code evaluations",
        "allowed": False,
        "reason_code": "SERVICE_UNAVAILABLE",
        "reason": PAUSED_MESSAGE,
        "requires_network": False,
        "oss_baseline": True,
    }
    assert response.data["features"]["custom_code_evals"]["reason"] == (
        "Custom code evaluations are temporarily unavailable on Future AGI Cloud."
    )


def test_custom_code_evals_are_on_when_the_setting_is_missing(
    settings, capability_request
):
    del settings.CUSTOM_CODE_EVALS_PAUSED

    response = views.CapabilitiesView().get(capability_request)

    assert response.data["features"]["custom_code_evals"]["allowed"] is True


def test_other_features_carry_no_reason_text(settings, capability_request):
    settings.CUSTOM_CODE_EVALS_PAUSED = True

    response = views.CapabilitiesView().get(capability_request)

    assert "reason" not in response.data["features"]["dataset_stars"]


@pytest.mark.parametrize("paused", [False, True])
def test_custom_code_evals_are_a_service_state_and_stars_are_not(
    settings, capability_request, paused
):
    settings.CUSTOM_CODE_EVALS_PAUSED = paused

    response = views.CapabilitiesView().get(capability_request)

    features = response.data["features"]
    assert features["custom_code_evals"]["kind"] == "service"
    assert "kind" not in features["dataset_stars"]


def test_a_reason_is_valid_on_any_feature_entry():
    serializer = CapabilitiesResponseSerializer(
        data={
            "deployment_flavor": DeploymentFlavor.OSS.value,
            "display_mode": "oss",
            "license_state": LicenseState.MISSING.value,
            "features": {
                "custom_code_evals": {
                    "display_name": "Custom code evaluations",
                    "allowed": False,
                    "reason_code": "SERVICE_UNAVAILABLE",
                    "reason": PAUSED_MESSAGE,
                    "requires_network": False,
                    "oss_baseline": True,
                }
            },
            "experiment_run_sources": {
                "workspace_id": None,
                "sources": {
                    kind: {"available": True, "reason": None}
                    for kind in ("prompt", "agent", "http", "a2a", "mcp_tool")
                },
            },
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.data["features"]["custom_code_evals"]["reason"] == (
        PAUSED_MESSAGE
    )
