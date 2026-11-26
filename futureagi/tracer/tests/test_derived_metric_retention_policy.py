"""Metrics delegates source policy; it does not define retention defaults."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from tfc.capabilities import retention
from tfc.licensing.types import DeploymentFlavor


@pytest.fixture
def cloud_policy(monkeypatch):
    monkeypatch.setattr(
        retention, "get_deployment_flavor", lambda: DeploymentFlavor.CLOUD
    )
    monkeypatch.setattr(
        retention, "has_ee", lambda module: module == "ee.usage.services.entitlements"
    )


def test_settings_resolves_the_maintained_policy_adapter(settings):
    assert (
        import_string(settings.METRICS_RETENTION_DAYS_PROVIDER)
        is retention.current_trace_retention_days
    )


@pytest.mark.parametrize("available", [False, True])
def test_oss_has_no_invented_retention_fallback(monkeypatch, available):
    monkeypatch.setattr(
        retention, "get_deployment_flavor", lambda: DeploymentFlavor.OSS
    )
    monkeypatch.setattr(retention, "has_ee", lambda _: available)
    with pytest.raises(ImproperlyConfigured, match="EE trace-retention policy"):
        retention.current_trace_retention_days(uuid4())


@pytest.mark.parametrize(
    "flavor", [DeploymentFlavor.CLOUD, DeploymentFlavor.SELF_HOSTED_EE]
)
def test_maintained_policy_is_available_in_enterprise_flavors(monkeypatch, flavor):
    monkeypatch.setattr(retention, "get_deployment_flavor", lambda: flavor)
    monkeypatch.setattr(retention, "has_ee", lambda _: True)
    with patch(
        "ee.usage.services.entitlements.Entitlements.get_retention_days",
        return_value=30,
        create=True,
    ) as get_retention_days:
        organization = uuid4()
        assert retention.current_trace_retention_days(organization) == 30
    get_retention_days.assert_called_once_with(
        str(organization), "traces", use_cache=False
    )


def test_cloud_without_the_source_policy_fails_closed(monkeypatch, cloud_policy):
    monkeypatch.setattr(retention, "has_ee", lambda _: False)
    with pytest.raises(
        ImproperlyConfigured, match="maintained EE trace-retention policy"
    ):
        retention.current_trace_retention_days(uuid4())


@pytest.mark.parametrize("days", [None, True, False, "30", 0, -2])
def test_malformed_policy_cannot_become_unlimited(cloud_policy, days):
    with patch(
        "ee.usage.services.entitlements.Entitlements.get_retention_days",
        return_value=days,
        create=True,
    ):
        with pytest.raises(ImproperlyConfigured, match="Invalid current"):
            retention.current_trace_retention_days(uuid4())


@pytest.mark.requires_ee
@pytest.mark.django_db
def test_current_policy_rereads_real_org_overrides_and_isolates_tenants(
    cloud_policy, workspace
):
    from accounts.models import Organization
    from ee.usage.models.usage import PlanEntitlement
    from ee.usage.services import entitlements

    org = workspace.organization
    other = Organization.objects.create(name="Synthetic retention control")
    policy = PlanEntitlement.objects.create(
        organization=org, plan="free", feature="retention_traces_days", value_int=30
    )
    PlanEntitlement.objects.create(
        organization=other, plan="free", feature="retention_traces_days", value_int=90
    )
    # Only the optional transport cache is replaced here. Policy resolution and
    # changes use real PG rows. Native Redis invalidation is an integration probe.
    with (
        patch.object(entitlements, "_cache_get", return_value=entitlements.CACHE_MISS),
        patch.object(entitlements, "_cache_setex"),
    ):

        def read():
            return retention.current_trace_retention_days(org.id)

        for days in (30, 7, 60, -1):
            policy.value_int = days
            policy.save(update_fields=["value_int"])
            assert read() == days
            assert retention.current_trace_retention_days(other.id) == 90
