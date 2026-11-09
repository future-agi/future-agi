"""The capability answer the UI reads (`GET /api/capabilities/`) and the platform route
(`/accounts/omega/observe-target/`) for A, A2 and B; fail closed (RC-07); no PostHog dependency
(RC-09); and plan inclusion combined by AND on cloud."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import DatabaseError

from accounts.models import OrgApiKey
from omega_rollout import ops
from omega_rollout import products as p
from omega_rollout.tests.conftest import admit, client_for
from tfc.capabilities import service as capability_service
from tfc.licensing.types import DeploymentFlavor, DeploymentLocation

pytestmark = pytest.mark.django_db
CAPABILITIES = "/api/capabilities/"


def features(person, workspace=None):
    response = client_for(person, workspace).get(CAPABILITIES)
    assert response.status_code == 200, response.content
    return {
        product: response.json()["features"][f"omega_{product}"]
        for product in p.HOSTED_PRODUCTS
    }


def test_a_is_allowed_a2_and_b_are_not_admitted(tenants):
    admit(tenants, p.CONSOLE)
    a, a2, b = features(tenants.a), features(tenants.a, tenants.a2), features(tenants.b)
    assert a["console"] == {**a["console"], "allowed": True, "reason_code": None}
    for answer in (a2["console"], b["console"], a["sandboxes"]):
        assert (
            answer["allowed"] is False
            and answer["reason_code"] == "PRODUCT_NOT_ADMITTED"
        )


def test_a_paused_product_reads_paused_for_a_and_still_hidden_for_b(tenants):
    admit(tenants, p.DEPLOYMENTS)
    ops.kill(p.DEPLOYMENTS, actor=tenants.operator.user, reason="drill")
    assert features(tenants.a)["deployments"]["reason_code"] == "PRODUCT_PAUSED"
    assert features(tenants.b)["deployments"]["reason_code"] == "PRODUCT_NOT_ADMITTED"


def test_an_unreadable_store_fails_closed_and_the_rest_of_capabilities_still_answers(
    tenants,
):
    admit(tenants, p.CONSOLE)
    with patch(
        "omega_rollout.models.ProductRollout.objects.all",
        side_effect=DatabaseError("down"),
    ):
        answer = features(tenants.a)
    assert all(
        row["allowed"] is False and row["reason_code"] == "RESOLVER_UNAVAILABLE"
        for row in answer.values()
    )


def test_no_posthog_dependency(tenants, monkeypatch):
    """RC-09: with PostHog unreachable and analytics off, every decision is unchanged."""
    admit(tenants, p.CONSOLE)
    before = (features(tenants.a), features(tenants.a, tenants.a2), features(tenants.b))
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)

    def unreachable(*args, **kwargs):
        raise ConnectionError("posthog unreachable")

    monkeypatch.setattr(
        "analytics.posthog_util.PostHogTracker.is_feature_enabled", unreachable
    )
    monkeypatch.setattr(
        "analytics.posthog_util.PostHogTracker.get_feature_flag_payload", unreachable
    )
    with patch("posthog.Posthog", side_effect=unreachable):
        after = (
            features(tenants.a),
            features(tenants.a, tenants.a2),
            features(tenants.b),
        )
    assert after == before
    # And no rollout module imports an analytics or flag client at all.
    root = Path(__file__).resolve().parents[1]
    for source in root.rglob("*.py"):
        if "tests" in source.parts:
            continue
        for node in ast.walk(ast.parse(source.read_text())):
            names = [a.name for a in getattr(node, "names", [])] + [
                getattr(node, "module", "") or ""
            ]
            assert not any(
                "posthog" in n or n.startswith("analytics") for n in names
            ), source


def test_cloud_plan_row_can_only_take_a_product_away(tenants):
    admit(tenants, p.CONSOLE)
    capability_service.configure(
        flavor=DeploymentFlavor.CLOUD, location=DeploymentLocation.CLOUD
    )
    try:
        with patch(
            "ee.usage.services.entitlements.Entitlements.get_entitlement",
            return_value=None,
        ):
            assert features(tenants.a)["console"]["allowed"] is True
        with patch(
            "ee.usage.services.entitlements.Entitlements.get_entitlement",
            return_value=False,
        ):
            assert (
                features(tenants.a)["console"]["reason_code"] == "PRODUCT_NOT_ADMITTED"
            )
        with patch(
            "ee.usage.services.entitlements.Entitlements.get_entitlement",
            return_value=True,
        ):
            assert (
                features(tenants.b)["console"]["reason_code"] == "PRODUCT_NOT_ADMITTED"
            )
    finally:
        capability_service.configure(
            flavor=DeploymentFlavor.OSS, location=DeploymentLocation.SELF_HOSTED
        )


def _key_client(person, workspace):
    from rest_framework.test import APIClient

    key = OrgApiKey.no_workspace_objects.create(
        organization=person.organization,
        workspace=workspace,
        user=person.user,
        type="user",
        name="observe",
        enabled=True,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


def test_observe_target_is_a_platform_route(tenants):
    admit(tenants, p.CONSOLE)
    url = "/accounts/omega/observe-target/?project_id=00000000-0000-4000-8000-000000000001"
    # A: the rollout lets it through to the view's own answer (no such project: 404 not_found).
    a = _key_client(tenants.a, tenants.a.workspace).get(url)
    assert a.status_code == 404 and a.json()["code"] != p.REASON_NOT_ADMITTED
    for client in (
        _key_client(tenants.a, tenants.a2),
        _key_client(tenants.b, tenants.b.workspace),
    ):
        response = client.get(url)
        assert (
            response.status_code == 404
            and response.json()["code"] == p.REASON_NOT_ADMITTED
        )


def test_an_unreadable_store_is_read_once_per_capabilities_request(tenants):
    admit(tenants, p.CONSOLE)
    with patch(
        "omega_rollout.models.ProductRollout.objects.all",
        side_effect=DatabaseError("down"),
    ) as rows:
        features(tenants.a)
    assert rows.call_count == 1
