"""The parity program's hide switches (PARITY-PROGRAM.md, lane P22), server side.

Every switch is a part the release declares in OMEGA_RELEASED_PARTS once the lane that closes its gap
is accepted. Off, the UI keeps the hidden or honest state (sections/omega/shell/switches.js); each test
reads a switch in both states. Two of them withhold a whole product on hosted Future AGI: Sandboxes
and Deployments are not offered on futureagi.com (parity Q1) until `hosted_sandboxes` /
`hosted_deployments` is declared, and a self-hosted install never notices either.

The first two classes need no database; the rest run on the rollout fixtures (A admitted, B not).
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.urls import reverse

from omega_rollout import products as p
from omega_rollout import readiness, service
from omega_rollout.tests.conftest import admit, client_for
from tfc.capabilities import service as capability_service
from tfc.licensing.types import DeploymentFlavor, DeploymentLocation

CAPABILITIES = "/api/capabilities/"
SWITCHES = readiness.SWITCH_PARTS
HOSTED = {
    p.SANDBOXES: readiness.HOSTED_SANDBOXES,
    p.DEPLOYMENTS: readiness.HOSTED_DEPLOYMENTS,
}


@contextmanager
def located(location):
    """Run as hosted Future AGI (CLOUD) or a self-hosted install, then restore the test default."""
    flavor = (
        DeploymentFlavor.CLOUD
        if location == DeploymentLocation.CLOUD
        else DeploymentFlavor.OSS
    )
    capability_service.configure(flavor=flavor, location=location)
    try:
        # No plan row objects: on cloud only the switch may take a product away here.
        with patch(
            "ee.usage.services.entitlements.Entitlements.get_entitlement",
            return_value=None,
        ):
            yield
    finally:
        capability_service.configure(
            flavor=DeploymentFlavor.OSS, location=DeploymentLocation.SELF_HOSTED
        )


def release(settings, *parts):
    settings.OMEGA_RELEASED_PARTS = ",".join(parts)


class TestSwitchParts:
    def test_every_switch_is_declared_and_off_until_released(self, settings):
        assert set(SWITCHES) <= set(readiness.DECLARED_PARTS)
        assert set(SWITCHES) == {
            "deploy_no_downtime",
            "deploy_langgraph_js",
            "sdk_python",
            "sdk_typescript",
            "hosted_sandboxes",
            "hosted_deployments",
        }
        release(settings)
        for part in SWITCHES:
            assert readiness.part_ready(part) is False, part

    @pytest.mark.parametrize("part", SWITCHES)
    def test_each_switch_turns_on_alone(self, settings, part):
        release(settings, part)
        assert readiness.part_ready(part) is True
        for other in SWITCHES:
            if other != part:
                assert readiness.part_ready(other) is False, other

    def test_the_omega_servers_switches_are_known_here_and_never_ready(self, settings):
        release(settings, "usage_spend", "unchecked_mark", "sdk_python", "typo_part")
        with patch.object(readiness.logger, "warning") as warning:
            assert readiness.released_parts() == {"sdk_python"}
        warning.assert_called_once_with(
            "omega_readiness_unknown_parts", parts=["typo_part"]
        )
        assert readiness.part_ready("usage_spend") is False

    def test_the_console_release_parts_are_known_here_and_never_ready(self, settings):
        """One shared release line names the Console's server switches; none is a typo here."""
        console_parts = (
            "console_emergency_stop",
            "agent_ownership",
            "console_otlp",
            "console_signed_triggers",
            "sandbox_network_on",
        )
        release(settings, *console_parts, "typo_part")
        with patch.object(readiness.logger, "warning") as warning:
            assert readiness.released_parts() == frozenset()
        warning.assert_called_once_with(
            "omega_readiness_unknown_parts", parts=["typo_part"]
        )
        for part in console_parts:
            assert readiness.part_ready(part) is False, part


class TestHostedWithheld:
    """The predicate alone, in every combination (no database)."""

    @pytest.mark.parametrize("product", sorted(HOSTED))
    def test_hosted_off_withholds_and_on_offers(self, settings, product):
        release(settings)
        with located(DeploymentLocation.CLOUD):
            assert service._hosted_withheld(product) is True
            release(settings, HOSTED[product])
            assert service._hosted_withheld(product) is False

    @pytest.mark.parametrize("product", sorted(HOSTED))
    def test_self_hosted_never_withholds(self, settings, product):
        release(settings)
        with located(DeploymentLocation.SELF_HOSTED):
            assert service._hosted_withheld(product) is False

    def test_the_two_switches_are_independent(self, settings):
        release(settings, readiness.HOSTED_SANDBOXES)
        with located(DeploymentLocation.CLOUD):
            assert service._hosted_withheld(p.SANDBOXES) is False
            assert service._hosted_withheld(p.DEPLOYMENTS) is True

    @pytest.mark.parametrize("product", [p.CONSOLE, p.STUDIO_TAB, p.HUB])
    def test_other_products_are_never_withheld(self, settings, product):
        release(settings)
        with located(DeploymentLocation.CLOUD):
            assert service._hosted_withheld(product) is False


def features(person, workspace=None):
    response = client_for(person, workspace).get(CAPABILITIES)
    assert response.status_code == 200, response.content
    return response.json()["features"]


def listing(person):
    response = client_for(person).get(reverse("omega_rollout:early-access"))
    assert response.status_code == 200, response.content
    return {row["product"]: row for row in response.json()["products"]}


@pytest.mark.django_db
class TestCapabilitiesReportEverySwitch:
    def test_each_switch_part_is_reported_off_then_on(self, tenants, settings):
        release(settings)
        answer = features(tenants.a)
        for part in SWITCHES:
            row = answer[f"omega_part_{part}"]
            assert (
                row["allowed"] is False and row["reason_code"] == "PRODUCT_NOT_ADMITTED"
            )
        release(settings, *SWITCHES)
        answer = features(tenants.a)
        for part in SWITCHES:
            assert answer[f"omega_part_{part}"]["allowed"] is True, part


@pytest.mark.django_db
class TestHostedProducts:
    @pytest.mark.parametrize("product", sorted(HOSTED))
    def test_hosted_off_hides_an_admitted_product_everywhere(
        self, tenants, settings, product
    ):
        admit(tenants, product, stage=p.S2)
        admit(tenants, p.CONSOLE)
        release(settings)
        org, workspace = tenants.a.organization.pk, tenants.a.workspace.pk
        with located(DeploymentLocation.CLOUD):
            row = features(tenants.a)[f"omega_{product}"]
            assert row["allowed"] is False
            assert row["reason_code"] == "PRODUCT_NOT_ADMITTED"
            assert features(tenants.a)["omega_console"]["allowed"] is True
            claims = service.claim_set(org, workspace)
            assert product not in claims.products
            assert claims.products[p.CONSOLE] == p.STATE_ACTIVE
            # Taking requests at S2, yet nothing to ask for: not listed, and a request is refused.
            assert product not in listing(tenants.a)
            assert product not in listing(tenants.b)
            refused = client_for(tenants.b).post(
                reverse("omega_rollout:early-access"),
                {"product": product},
                format="json",
            )
            assert refused.status_code == 404, refused.content

    @pytest.mark.parametrize("product", sorted(HOSTED))
    def test_hosted_on_offers_it_by_rollout_as_before(self, tenants, settings, product):
        admit(tenants, product, stage=p.S2)
        release(settings, HOSTED[product])
        org, workspace = tenants.a.organization.pk, tenants.a.workspace.pk
        with located(DeploymentLocation.CLOUD):
            assert features(tenants.a)[f"omega_{product}"]["allowed"] is True
            assert features(tenants.b)[f"omega_{product}"]["allowed"] is False
            assert service.claim_set(org, workspace).products[product] == p.STATE_ACTIVE
            assert listing(tenants.a)[product]["admitted"] is True
            assert listing(tenants.b)[product]["admitted"] is False

    @pytest.mark.parametrize("product", sorted(HOSTED))
    def test_self_hosted_is_unchanged_with_the_switch_off(
        self, tenants, settings, product
    ):
        admit(tenants, product)
        release(settings)
        org, workspace = tenants.a.organization.pk, tenants.a.workspace.pk
        with located(DeploymentLocation.SELF_HOSTED):
            assert features(tenants.a)[f"omega_{product}"]["allowed"] is True
            assert service.claim_set(org, workspace).products[product] == p.STATE_ACTIVE

    # P22 r7a rows: the refusal's own reason, an Early access request in both states on hosted, and the
    # self-hosted listing and request with the switch off.

    @pytest.mark.parametrize("product", sorted(HOSTED))
    def test_the_hosted_refusal_is_named_hosted_withheld_and_the_switch_lifts_it(
        self, tenants, settings, product
    ):
        admit(tenants, product, stage=p.S2)
        release(settings)
        org, workspace = tenants.a.organization.pk, tenants.a.workspace.pk
        with located(DeploymentLocation.CLOUD):
            refused = service.admission(product, org, workspace)
            assert refused.admitted is False
            assert refused.reason == "hosted_withheld"
            release(settings, HOSTED[product])
            assert service.admission(product, org, workspace).admitted is True

    @pytest.mark.parametrize("product", sorted(HOSTED))
    def test_hosted_on_lists_it_and_takes_an_early_access_request(
        self, tenants, settings, product
    ):
        from omega_rollout.models import WaitlistRequest

        admit(tenants, product, stage=p.S2)
        release(settings, HOSTED[product])
        with located(DeploymentLocation.CLOUD):
            assert listing(tenants.b)[product]["admitted"] is False
            asked = client_for(tenants.b).post(
                reverse("omega_rollout:early-access"),
                {"product": product},
                format="json",
            )
            assert asked.status_code == 200, asked.content
            rows = {row["product"]: row for row in asked.json()["products"]}
            assert rows[product]["request_status"] == WaitlistRequest.REQUESTED
        assert WaitlistRequest.objects.filter(
            product=product, organization=tenants.b.organization
        ).exists()

    @pytest.mark.parametrize("product", sorted(HOSTED))
    def test_self_hosted_with_the_switch_off_lists_it_and_takes_a_request(
        self, tenants, settings, product
    ):
        from omega_rollout.models import WaitlistRequest

        admit(tenants, product, stage=p.S2)
        release(settings)
        with located(DeploymentLocation.SELF_HOSTED):
            assert listing(tenants.a)[product]["admitted"] is True
            assert listing(tenants.b)[product]["admitted"] is False
            asked = client_for(tenants.b).post(
                reverse("omega_rollout:early-access"),
                {"product": product},
                format="json",
            )
            assert asked.status_code == 200, asked.content
        assert WaitlistRequest.objects.filter(
            product=product, organization=tenants.b.organization
        ).exists()
