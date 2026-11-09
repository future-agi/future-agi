"""Privacy lookup uses the shared policy hierarchy without Redis delays/staleness."""

from unittest import mock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


@pytest.mark.parametrize("level, expected", [("org", 7), ("plan", 14), ("config", 21)])
def test_cached_and_uncached_resolution_share_precedence(
    observe_project, monkeypatch, level, expected
):
    from ee.usage.models.usage import PlanEntitlement
    from ee.usage.services import entitlements, metering

    org_id = str(observe_project.organization_id)
    feature = "retention_traces_days"
    fake = mock.Mock()
    fake.get.return_value = None
    monkeypatch.setattr(entitlements, "get_redis", lambda: fake)
    monkeypatch.setattr(metering, "get_redis", lambda: fake)
    monkeypatch.setattr(
        entitlements.BillingConfig,
        "get",
        lambda: mock.Mock(get_entitlement_default=lambda feature, plan: 21),
    )
    if level in ("org", "plan"):
        default = PlanEntitlement.objects.create(
            feature=feature, plan="free", organization=None, value_int=14
        )
        # BaseModel's request-context autofill also runs for nullable org fields.
        PlanEntitlement.objects.filter(pk=default.pk).update(organization=None)
    if level == "org":
        PlanEntitlement.objects.create(
            feature=feature, organization=observe_project.organization, value_int=7
        )
    assert entitlements.Entitlements.get_retention_days(org_id, "traces") == expected
    fake.reset_mock()
    monkeypatch.setattr(
        entitlements,
        "get_redis",
        mock.Mock(side_effect=AssertionError("privacy lookup must not touch Redis")),
    )
    monkeypatch.setattr(
        metering,
        "get_redis",
        mock.Mock(
            side_effect=AssertionError("privacy lookup must not touch plan Redis")
        ),
    )
    assert (
        entitlements.Entitlements.get_retention_days(org_id, "traces", use_cache=False)
        == expected
    )
    assert fake.mock_calls == []


def test_uncached_policy_sees_tighter_override_despite_stale_cache(
    observe_project, monkeypatch
):
    from ee.usage.models.usage import PlanEntitlement
    from ee.usage.services import entitlements

    fake = mock.Mock()
    fake.get.return_value = b"90"
    monkeypatch.setattr(entitlements, "get_redis", lambda: fake)
    PlanEntitlement.objects.create(
        feature="retention_traces_days",
        organization=observe_project.organization,
        value_int=7,
    )
    org_id = str(observe_project.organization_id)
    assert entitlements.Entitlements.get_retention_days(org_id, "traces") == 90
    assert (
        entitlements.Entitlements.get_retention_days(org_id, "traces", use_cache=False)
        == 7
    )
