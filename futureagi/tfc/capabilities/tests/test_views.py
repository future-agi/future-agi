from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import reload
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import Resolver404, clear_url_caches, resolve
from rest_framework.test import APIRequestFactory

from tfc.capabilities import service
from tfc.capabilities.views import CapabilitiesView
from tfc.licensing.types import (
    DeploymentFlavor,
    DeploymentLocation,
    LicenseSnapshot,
    LicenseState,
    LicenseType,
)


class _FakeResolver:
    def __init__(self, snapshot: LicenseSnapshot):
        self._snapshot = snapshot

    def get_snapshot(self) -> LicenseSnapshot:
        return self._snapshot


def _configure_active_license() -> None:
    snapshot = LicenseSnapshot(
        state=LicenseState.ACTIVE,
        license_type=LicenseType.PRODUCTION,
        license_id="lic_view",
        issued_to="View Corp",
        band="business",
        features=frozenset({"voice_sim"}),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    service.configure(
        flavor=DeploymentFlavor.SELF_HOSTED_EE,
        location=DeploymentLocation.SELF_HOSTED,
        license_resolver=_FakeResolver(snapshot),
    )


def test_capabilities_view_handles_missing_organization_for_staff_user():
    _configure_active_license()
    request = APIRequestFactory().get("/api/capabilities/")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=True)

    response = CapabilitiesView().get(request)

    assert response.status_code == 200
    assert response.data["deployment_flavor"] == "self_hosted_ee_image"
    assert response.data["display_mode"] == "enterprise"
    assert response.data["license"]["issued_to"] == "View Corp"


def test_capabilities_view_hides_license_details_for_non_admin_user():
    _configure_active_license()
    request = APIRequestFactory().get("/api/capabilities/")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=False)

    response = CapabilitiesView().get(request)

    assert response.status_code == 200
    assert "license" not in response.data
    assert "instance_id" not in response.data


def test_self_hosted_routes_expose_status_not_control_plane():
    import tfc.urls

    try:
        with patch(
            "tfc.deployment_telemetry.config.is_cloud_deployment",
            return_value=False,
        ):
            urls = reload(tfc.urls)
            clear_url_caches()
            assert resolve("/api/capabilities/", urlconf=urls).view_name == "capabilities"
            for path in (
                "/usage/ee/licenses/",
                "/v1/self-hosted/activations",
                "/v1/internal/licenses",
            ):
                with pytest.raises(Resolver404):
                    resolve(path, urlconf=urls)
    finally:
        reload(tfc.urls)
        clear_url_caches()
