"""Edition policy (tfc.capabilities.edition) for TH-8084.

Community (self-hosted, no usable licence): 1 organization, 1 workspace,
up to 3 organization members. A usable licence lifts all three. Cloud is
never subject to the rule, and commercial caps apply only on Cloud.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from tfc.capabilities import edition
from tfc.capabilities.edition import EditionResource
from tfc.capabilities.errors import CapabilityDenied, EnterpriseFeatureRequired
from tfc.capabilities.tests.edition_factories import (
    make_invite,
    make_member,
    make_org,
    make_owner,
    make_user,
    make_workspace,
)
from tfc.licensing.types import DenialReason

try:
    from ee.licensing.tests.fixtures import (  # noqa: F401
        install_license,
        test_signing_keypair,
    )
except ImportError:  # OSS lane: licence cases carry requires_ee and are skipped
    pass

pytestmark = [pytest.mark.edition_rule, pytest.mark.django_db]

USABLE = ("active", "grace", "trial")
NOT_USABLE = (
    "expired",
    "trial_expired",
    "not_yet_valid",
    "wrong_key",
    "tampered",
    "missing",
    "no_keys",
)

_USAGE_TABLES = (
    "usage_organizationsubscription",
    "usage_resourcelimits",
    "usage_ratelimit",
    "usage_planentitlement",
    "usage_subscriptiontier",
)


@pytest.fixture
def self_hosted(monkeypatch):
    """Pin the deployment to self-hosted whatever the local env says."""
    monkeypatch.setattr(edition, "is_cloud", lambda: False)


@pytest.fixture
def community(self_hosted, monkeypatch):
    """Self-hosted with no usable licence (the default test key is unverifiable)."""
    monkeypatch.setattr(edition, "enterprise_license_usable", lambda: False)


class TestCloudDetection:
    def test_constants_match_approved_boundary(self):
        """AC-08: Community is 1 organization, 1 workspace, up to 3 members."""
        assert edition.COMMUNITY_LIMITS == {
            EditionResource.ORGANIZATION: 1,
            EditionResource.WORKSPACE: 1,
            EditionResource.MEMBER: 3,
        }
        assert edition.CONTACT_EMAIL == "sales@futureagi.com"
        assert edition.ACTIVATION_ROUTE == "/dashboard/settings/ee-licenses"

    @pytest.mark.requires_ee
    def test_cloud_env_without_secret_is_self_hosted(self):
        """AC-13 / R14: CLOUD_DEPLOYMENT alone never makes an install Cloud."""
        from ee.usage.deployment import _detect_mode

        _detect_mode.cache_clear()
        try:
            with override_settings(CLOUD_DEPLOYMENT="US", CLOUD_DEPLOYMENT_SECRET=""):
                assert edition.is_cloud() is False
                assert edition.commercial_caps_apply() is False
        finally:
            _detect_mode.cache_clear()

    @pytest.mark.requires_ee
    def test_cloud_with_valid_secret_is_cloud(self, monkeypatch):
        """AC-13: secret-validated Cloud keeps commercial caps on."""
        from ee.usage import deployment

        monkeypatch.setattr(deployment, "_validate_cloud_secret", lambda s: True)
        deployment._detect_mode.cache_clear()
        try:
            with override_settings(CLOUD_DEPLOYMENT="US", CLOUD_DEPLOYMENT_SECRET="x"):
                assert edition.is_cloud() is True
                assert edition.commercial_caps_apply() is True
                assert edition.edition_rule_applies() is False
        finally:
            deployment._detect_mode.cache_clear()

    @pytest.mark.requires_ee
    def test_invalid_cloud_secret_logs_at_error_level(self, caplog):
        """R14 / C3: a Cloud env with a bad secret is logged at error level."""
        import logging

        from ee.usage.deployment import _detect_mode

        _detect_mode.cache_clear()
        try:
            with (
                override_settings(CLOUD_DEPLOYMENT="EU", CLOUD_DEPLOYMENT_SECRET="no"),
                caplog.at_level(logging.WARNING),
            ):
                _detect_mode()
        finally:
            _detect_mode.cache_clear()
        records = [
            r for r in caplog.records if "cloud_secret_invalid" in r.getMessage()
        ]
        assert records, "cloud_secret_invalid was not logged"
        assert all(r.levelno == logging.ERROR for r in records)


class TestCloudIsUnchanged:
    def test_cloud_never_applies_the_edition_rule(self, edition_cloud):
        """AC-13: on Cloud every creation is allowed by the edition policy."""
        owner = make_owner(make_org())
        make_org()
        make_workspace(owner.organization, owner)
        make_workspace(owner.organization, owner)
        for _ in range(4):
            make_member(owner.organization)

        assert edition.commercial_caps_apply() is True
        assert edition.edition_rule_applies() is False
        for resource in EditionResource:
            decision = edition.check_creation(
                resource,
                organization=owner.organization,
                new_member_emails=["new@example.com"],
            )
            assert decision.allowed is True
            assert decision.limit is None
            assert decision.license_state == "not_applicable"

    def test_cloud_creation_lock_takes_no_lock(self, edition_cloud):
        """AC-13: Cloud never contends on the edition advisory lock."""
        with CaptureQueriesContext(connection) as ctx:
            with edition.creation_lock():
                pass
        assert not any("pg_advisory" in q["sql"] for q in ctx.captured_queries)


class TestCommunityLimits:
    def test_first_organization_allowed_second_refused(self, community):
        """AC-08: a 2nd organization is refused on Community."""
        assert edition.check_creation(EditionResource.ORGANIZATION).allowed is True
        make_org()
        decision = edition.check_creation(EditionResource.ORGANIZATION)
        assert decision.allowed is False
        assert (decision.limit, decision.current, decision.requested) == (1, 1, 1)

    def test_first_workspace_allowed_second_refused(self, community):
        """AC-08: a 2nd workspace is refused on Community (instance-wide)."""
        owner = make_owner(make_org())
        assert edition.check_creation(EditionResource.WORKSPACE).allowed is True
        make_workspace(owner.organization, owner, is_default=True)
        decision = edition.check_creation(EditionResource.WORKSPACE)
        assert decision.allowed is False
        assert (decision.limit, decision.current) == (1, 1)

    def test_inactive_workspace_not_counted(self, community):
        """AC-08: only active workspaces count."""
        owner = make_owner(make_org())
        make_workspace(owner.organization, owner, is_active=False)
        assert edition.count(EditionResource.WORKSPACE) == 0
        assert edition.check_creation(EditionResource.WORKSPACE).allowed is True

    def test_third_member_allowed_fourth_refused(self, community):
        """AC-08: up to 3 members; the 4th is refused."""
        org = make_org()
        make_owner(org)
        make_member(org)
        ok = edition.check_creation(
            EditionResource.MEMBER, organization=org, new_member_emails=["c@x.io"]
        )
        assert ok.allowed is True
        make_member(org, "c@x.io")
        refused = edition.check_creation(
            EditionResource.MEMBER, organization=org, new_member_emails=["d@x.io"]
        )
        assert refused.allowed is False
        assert (refused.limit, refused.current, refused.requested) == (3, 3, 1)

    def test_pending_invite_holds_a_seat(self, community):
        """AC-08 / C8: pending, unexpired invites hold a seat."""
        org = make_org()
        make_owner(org)
        make_member(org)
        make_invite(org, "pending@x.io")
        assert edition.count(EditionResource.MEMBER, organization=org) == 3
        decision = edition.check_creation(
            EditionResource.MEMBER, organization=org, new_member_emails=["d@x.io"]
        )
        assert decision.allowed is False

    def test_expired_and_cancelled_invites_do_not_hold_seats(self, community):
        """AC-08: expired or cancelled invites free their seat."""
        org = make_org()
        make_owner(org)
        make_invite(org, "old@x.io", expired=True)
        make_invite(org, "gone@x.io", status="Cancelled")
        assert edition.count(EditionResource.MEMBER, organization=org) == 1

    def test_reinviting_a_pending_or_active_email_needs_no_new_seat(self, community):
        """AC-08: an email that already holds a seat is not counted again."""
        org = make_org()
        make_owner(org, "owner@x.io")
        make_member(org, "b@x.io")
        make_invite(org, "c@x.io")
        decision = edition.check_creation(
            EditionResource.MEMBER,
            organization=org,
            new_member_emails=["C@x.io", "b@x.io", "owner@x.io"],
        )
        assert decision.allowed is True
        assert decision.requested == 0

    def test_reviving_an_expired_invite_needs_a_seat(self, community):
        """AC-08: an expired invite revived by resend counts as a new seat."""
        org = make_org()
        make_owner(org)
        make_member(org)
        make_member(org)
        make_invite(org, "stale@x.io", expired=True)
        decision = edition.check_creation(
            EditionResource.MEMBER, organization=org, new_member_emails=["stale@x.io"]
        )
        assert decision.allowed is False
        assert decision.requested == 1

    def test_deactivated_member_frees_seat_and_reactivation_counts(self, community):
        """AC-08: deactivated members do not count; reactivating one is an add."""
        org = make_org()
        make_owner(org)
        make_member(org)
        gone = make_member(org, active=False)
        assert edition.count(EditionResource.MEMBER, organization=org) == 2
        assert (
            edition.check_creation(
                EditionResource.MEMBER, organization=org, new_member_emails=[gone.email]
            ).allowed
            is True
        )
        make_member(org)
        assert (
            edition.check_creation(
                EditionResource.MEMBER, organization=org, new_member_emails=[gone.email]
            ).allowed
            is False
        )

    def test_bulk_invite_is_all_or_nothing(self, community):
        """AC-08: a request that would exceed 3 is refused as a whole."""
        org = make_org()
        make_owner(org)
        decision = edition.check_creation(
            EditionResource.MEMBER,
            organization=org,
            new_member_emails=["a@x.io", "b@x.io", "c@x.io"],
        )
        assert decision.allowed is False
        assert (decision.current, decision.requested) == (1, 3)

    def test_members_are_counted_per_organization(self, community):
        """AC-08: member seats are scoped to the organization."""
        org_a, org_b = make_org(), make_org()
        for _ in range(3):
            make_member(org_a)
        make_owner(org_b)
        assert edition.count(EditionResource.MEMBER, organization=org_a) == 3
        assert edition.count(EditionResource.MEMBER, organization=org_b) == 1

    def test_legacy_fk_users_without_membership_are_not_counted(self, community):
        """AC-08: OrganizationMembership is the source of truth (R12)."""
        org = make_org()
        make_owner(org)
        make_user(organization=org)
        assert edition.count(EditionResource.MEMBER, organization=org) == 1


class TestOverLimitInstallsKeepEverything:
    def test_over_limit_counts_never_block_non_creation(self, community):
        """AC-11: over-limit installs keep everything; only new creation is blocked."""
        org = make_org()
        make_org()
        owner = make_owner(org)
        for _ in range(4):
            make_member(org)
        make_workspace(org, owner, is_default=True)
        make_workspace(org, owner)

        # Nothing new requested: allowed even though every count is over.
        existing = edition.check_creation(
            EditionResource.MEMBER, organization=org, new_member_emails=[owner.email]
        )
        assert existing.allowed is True
        assert existing.current == 5
        for resource in (EditionResource.ORGANIZATION, EditionResource.WORKSPACE):
            assert edition.check_creation(resource, organization=org).allowed is False

    def test_usage_summary_reports_over_limit(self, community):
        """AC-11: the summary flags an over-limit install without hiding data."""
        org = make_org()
        make_org()
        make_owner(org)
        summary = edition.usage_summary(org)
        assert summary["edition"] == "community"
        assert summary["over_limit"] is True
        assert summary["limits"]["organizations"] == {"limit": 1, "current": 2}
        assert summary["limits"]["members"] == {"limit": 3, "current": 1}


class TestEnterpriseGateError:
    def test_assert_can_create_raises_enterprise_gate(self, community):
        """AC-08: the refusal is an Enterprise gate, not a quota error."""
        make_org()
        with pytest.raises(EnterpriseFeatureRequired) as exc_info:
            edition.assert_can_create(EditionResource.ORGANIZATION)
        exc = exc_info.value
        assert isinstance(exc, CapabilityDenied)
        assert exc.status_code == 402
        assert exc.error_code == DenialReason.ENTERPRISE_FEATURE_REQUIRED.value
        assert exc.enterprise_gate == {
            "feature": "organizations",
            "edition": "community",
            "limit": 1,
            "current": 1,
            "requested": 1,
            "license_state": edition.license_state(),
            "contact": "sales@futureagi.com",
            "activation_route": "/dashboard/settings/ee-licenses",
        }
        message = str(exc.detail).lower()
        for banned in ("usage limit", "quota", "rate limit", "upgrade your plan"):
            assert banned not in message

    def test_assert_can_create_is_silent_when_allowed(self, community):
        """AC-08: allowed creation raises nothing."""
        edition.assert_can_create(EditionResource.ORGANIZATION)


class TestNeverReadsCloudRows:
    def test_no_subscription_or_limit_tables_are_queried(self, community):
        """AC-09: the edition rule never reads Cloud Free-tier rows or subscriptions."""
        org = make_org()
        owner = make_owner(org)
        make_workspace(org, owner, is_default=True)
        with CaptureQueriesContext(connection) as ctx:
            for resource in EditionResource:
                edition.check_creation(
                    resource, organization=org, new_member_emails=["z@x.io"]
                )
            edition.usage_summary(org)
        sql = " ".join(q["sql"] for q in ctx.captured_queries)
        for table in _USAGE_TABLES:
            assert table not in sql

    @pytest.mark.requires_ee
    def test_altering_free_tier_rows_changes_nothing(self, community):
        """AC-09: deleting or raising the Free-tier USERS row has no effect."""
        from ee.usage.models.usage import ResourceLimits

        org = make_org()
        for _ in range(3):
            make_member(org)
        before = edition.check_creation(
            EditionResource.MEMBER, organization=org, new_member_emails=["n@x.io"]
        )
        ResourceLimits.objects.filter(resource_type__name="users").update(limit=1000)
        raised = edition.check_creation(
            EditionResource.MEMBER, organization=org, new_member_emails=["n@x.io"]
        )
        ResourceLimits.objects.filter(resource_type__name="users").delete()
        deleted = edition.check_creation(
            EditionResource.MEMBER, organization=org, new_member_emails=["n@x.io"]
        )
        assert before == raised == deleted
        assert before.allowed is False


@pytest.mark.requires_ee
@pytest.mark.usefixtures("self_hosted")
class TestLicenceStates:
    @pytest.mark.parametrize("license_state", USABLE)
    def test_usable_licence_lifts_all_three_limits(
        self, install_license, license_state
    ):
        """AC-10: active, grace and trial licences lift orgs, workspaces and members."""
        snapshot = install_license(license_state)
        assert snapshot.is_usable
        org = make_org()
        owner = make_owner(org)
        make_workspace(org, owner, is_default=True)
        for _ in range(3):
            make_member(org)
        assert edition.enterprise_license_usable() is True
        assert edition.edition_rule_applies() is False
        for resource in EditionResource:
            decision = edition.check_creation(
                resource, organization=org, new_member_emails=["more@x.io"]
            )
            assert decision.allowed is True
            assert decision.limit is None

    @pytest.mark.parametrize("license_state", NOT_USABLE)
    def test_unusable_licence_keeps_the_rule(self, install_license, license_state):
        """AC-10: invalid, expired, not-yet-valid, tampered or missing licences do not lift."""
        snapshot = install_license(license_state)
        assert not snapshot.is_usable
        make_org()
        assert edition.enterprise_license_usable() is False
        decision = edition.check_creation(EditionResource.ORGANIZATION)
        assert decision.allowed is False
        assert decision.license_state == snapshot.live_state().value

    def test_expiry_at_runtime_restores_the_rule(self, install_license, monkeypatch):
        """AC-17: a licence that expires in a running process drops to Community."""
        import tfc.licensing.types as types_module

        snapshot = install_license("active")
        make_org()
        assert edition.check_creation(EditionResource.ORGANIZATION).allowed is True

        later = snapshot.expires_at + timedelta(days=1)

        class _Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return later

        monkeypatch.setattr(types_module, "datetime", _Later)
        decision = edition.check_creation(EditionResource.ORGANIZATION)
        assert decision.allowed is False
        assert decision.license_state == "expired"

    def test_heartbeat_and_activation_failures_do_not_change_edition(
        self, install_license, monkeypatch
    ):
        """AC-17 / C4: licence validation is offline; network failures never change edition."""
        import httpx

        from ee.licensing import activation_client, heartbeat

        install_license("active")
        make_org()

        def _boom(*args, **kwargs):
            raise httpx.ConnectError("unreachable")

        monkeypatch.setattr(heartbeat, "is_heartbeat_enabled", lambda: True)
        monkeypatch.setattr(httpx, "post", _boom)
        assert heartbeat.send_heartbeat() is False
        monkeypatch.setattr(activation_client, "_activate", lambda: None)
        activation_client.invalidate_token()
        assert activation_client.get_service_token() is None

        assert edition.enterprise_license_usable() is True
        assert edition.check_creation(EditionResource.ORGANIZATION).allowed is True


def test_license_state_reports_live_state_on_self_host(community):
    """AC-10: decisions carry the licence state for the gate payload."""
    assert edition.license_state() in {"missing", "invalid"}
