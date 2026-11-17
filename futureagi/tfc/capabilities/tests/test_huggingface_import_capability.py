"""Hugging Face availability describes an installed dependency, not a license."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework.test import APIRequestFactory

from tfc.capabilities import views
from tfc.licensing.types import DeploymentFlavor, DeploymentLocation, LicenseState
from tfc.utils import lazy_extras


@pytest.mark.parametrize("available", [True, False])
def test_huggingface_import_capability_matches_package_import(monkeypatch, available):
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
    load = Mock(return_value=SimpleNamespace())
    if not available:
        load.side_effect = ImportError("datasets is unavailable")
    monkeypatch.setattr(lazy_extras, "load_extra", load)
    request = APIRequestFactory().get("/api/capabilities/")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=False)

    response = views.CapabilitiesView().get(request)

    assert response.status_code == 200
    feature = response.data["features"]["huggingface_import"]
    assert feature["kind"] == "service"
    assert feature["display_name"] == "Hugging Face import"
    assert feature["allowed"] is available
    assert feature["reason_code"] == (None if available else "SERVICE_UNAVAILABLE")
    assert feature["requires_network"] is True
    assert feature["oss_baseline"] is True
    if available:
        assert "reason" not in feature
    else:
        assert feature["reason"] == (
            "This install lacks the datasets package needed for Hugging Face import."
        )
    load.assert_called_once_with("datasets", "ml")
    assert response["Cache-Control"] == "private, no-store"
