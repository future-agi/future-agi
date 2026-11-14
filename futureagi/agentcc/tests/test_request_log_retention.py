"""Request Logs retention: budget floor, clamp, purge, schedule and API."""

import uuid
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agentcc.models import (
    AgentccGuardrailFeedback,
    AgentccOrgConfig,
    AgentccRequestLog,
    AgentccRequestLogRetention,
)
from agentcc.services import request_log_retention as retention
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

URL = "/agentcc/request-log-retention/"
LOGS = AgentccRequestLog.all_objects
NOW = datetime(2026, 11, 14, 5, 0, tzinfo=UTC)
MONTH_START = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _gateway_yaml_without_budgets(settings):
    """Most tests are about org-config budgets: the operator states the
    gateway YAML has none, so the default current-month clamp is off. The
    tests of that clamp turn it back on."""
    settings.AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS = True


@pytest.mark.parametrize(
    ("budgets", "floor"),
    [
        (None, None),
        ({}, None),
        ({"enabled": False, "org_limit": 10, "org_period": "monthly"}, None),
        ({"org_limit": 10, "org_period": "daily"}, 1),
        ({"org_limit": 10, "org_period": "weekly"}, 7),
        ({"org_limit": 10, "org_period": "monthly"}, 31),
        # no period: default_period, then the gateway's monthly
        ({"default_period": "weekly", "org_limit": 10}, 7),
        ({"defaultPeriod": "daily", "org_limit": 10}, 1),
        ({"org_limit": 10}, 31),
        ({"org_limit": {"limit": 5, "period": "daily"}}, 1),
        ({"hard_limit": {"limit": 5, "period": "weekly"}}, 7),
        ({"organization": {"limit": 5, "period": "daily"}}, 1),
        (
            {
                "teams": {
                    "a": {"limit": 5, "period": "daily"},
                    "b": {"limit": 1, "period": "weekly"},
                }
            },
            7,
        ),
        ({"keys": {"k": {"per_model": {"gpt-4o": 2}, "period": "daily"}}}, 1),
        ({"users": {"u": {"limit": 0, "period": "monthly"}}}, None),  # no limit
        (
            {
                "org_limit": 10,
                "org_period": "daily",
                "tags": {"t": {"limit": 1, "period": "total"}},
            },
            "forever",
        ),
        ({"org_limit": 10, "org_period": "total"}, "forever"),
        ({"hard_limit": True, "org_limit": 10, "org_period": "daily"}, 1),
        # the contract's camelCase aliases, which the gateway enforces
        ({"enabled": True, "orgLimit": 500, "orgPeriod": "monthly"}, 31),
        ({"orgLimit": 10, "orgPeriod": "daily"}, 1),
        ({"orgLimit": 10, "defaultPeriod": "weekly"}, 7),
        ({"orgLimit": 10, "hardLimit": True, "orgPeriod": "weekly"}, 7),
        ({"keys": {"k": {"perModel": {"gpt-4o": 2}, "period": "weekly"}}}, 7),
        ({"teams": {"t": {"perModel": {"gpt-4o": 0}, "period": "weekly"}}}, None),
        # a period the gateway does not know counts as monthly, as in Go
        ({"org_limit": 100, "org_period": "month", "default_period": "daily"}, 31),
        ({"org_limit": 1, "default_period": "fortnightly"}, 31),
        ({"users": {"u": {"limit": 1, "period": "yearly"}}}, 31),
    ],
)
def test_budget_retention_floor(budgets, floor):
    assert retention.budget_retention_floor(budgets) == floor


def test_the_cutoff_keeps_the_current_month_under_a_monthly_budget():
    now = datetime(2026, 11, 14, 5, 0, tzinfo=UTC)
    monthly = {"org_limit": 10, "org_period": "monthly"}

    assert retention.effective_cutoff(1, monthly, now=now) == datetime(
        2026, 11, 1, tzinfo=UTC
    )
    assert retention.effective_cutoff(1, {}, now=now) == now - timedelta(days=1)
    assert retention.effective_cutoff(60, monthly, now=now) == now - timedelta(days=60)
    assert retention.effective_cutoff(None, {}, now=now) is None
    assert (
        retention.effective_cutoff(5, {"org_limit": 1, "org_period": "total"}, now=now)
        is None
    )


@pytest.mark.parametrize(
    "budgets",
    [
        # camelCase org keys beside a flat hard_limit budget: config push runs
        # _transform_budgets on the raw dict first (org_limit/org_period come
        # from hard_limit), then the contract's snake_case keys win over the
        # aliases, so the gateway counts 100 weekly, not 500 daily.
        {
            "orgLimit": 500,
            "orgPeriod": "daily",
            "hard_limit": {"limit": 100, "period": "weekly", "action": "block"},
        },
        {"orgLimit": 500, "organization": {"limit": 50, "period": "weekly"}},
        {"enabled": True, "orgLimit": 500, "orgPeriod": "monthly"},
        {"orgLimit": 10, "defaultPeriod": "weekly"},
    ],
)
def test_budget_periods_are_the_ones_config_push_sends(budgets):
    """Round 3: retention reads the budgets exactly as config push sends them
    to the gateway (transform first, then the contract's alias rules)."""
    from agentcc.contracts.gateway_admin import BudgetsConfig
    from agentcc.services.config_push import _transform_budgets

    pushed = BudgetsConfig.model_validate(_transform_budgets(budgets)).model_dump(
        exclude_none=True
    )

    assert retention.budget_periods(budgets) == retention.budget_periods(pushed)


def test_budgets_the_contract_refuses_still_resolve_in_push_order():
    """A dict push would refuse (a hard_limit budget with no action) was
    never sent; its periods still resolve in push's order, not alias-first."""
    budgets = {
        "orgLimit": 500,
        "orgPeriod": "daily",
        "hard_limit": {"limit": 100, "period": "weekly"},
    }

    assert retention.budget_periods(budgets) == {"weekly"}


def test_a_camel_case_budget_keeps_its_period():
    budgets = {"enabled": True, "orgLimit": 500, "orgPeriod": "monthly"}

    assert retention.effective_cutoff(1, budgets, now=NOW) == MONTH_START


def test_by_default_the_gateways_own_month_is_kept(settings):
    # Budgets in the gateway YAML are seeded from the current month.
    settings.AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS = False

    assert retention.effective_cutoff(1, {}, now=NOW) == MONTH_START
    daily = {"org_limit": 1, "org_period": "daily"}
    assert retention.effective_cutoff(1, daily, now=NOW) == MONTH_START
    # A longer retention is untouched.
    assert retention.effective_cutoff(60, {}, now=NOW) == NOW - timedelta(days=60)

    settings.AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS = True
    assert retention.effective_cutoff(1, {}, now=NOW) == NOW - timedelta(days=1)


def test_the_gateways_own_week_is_kept_when_it_began_last_month(settings):
    settings.AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS = False
    # Sunday 1 Nov 2026: the week began on Monday 26 Oct.
    first = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)

    assert retention.effective_cutoff(1, {}, now=first) == datetime(
        2026, 10, 26, tzinfo=UTC
    )


def test_the_gateway_yaml_setting_is_read_from_the_environment(monkeypatch):
    from django.core.exceptions import ImproperlyConfigured

    from tfc.settings import agentcc_request_logs

    monkeypatch.delenv("AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS", raising=False)
    assert agentcc_request_logs._boolean("AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS") is False
    monkeypatch.setenv("AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS", "true")
    assert agentcc_request_logs._boolean("AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS") is True
    monkeypatch.setenv("AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS", "maybe")
    with pytest.raises(ImproperlyConfigured):
        agentcc_request_logs._boolean("AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS")


def test_next_run_is_the_next_three_oclock_utc():
    assert retention.next_run_at(datetime(2026, 11, 14, 2, 59, tzinfo=UTC)) == datetime(
        2026, 11, 14, 3, tzinfo=UTC
    )
    assert retention.next_run_at(datetime(2026, 11, 14, 3, 0, tzinfo=UTC)) == datetime(
        2026, 11, 15, 3, tzinfo=UTC
    )


def _log(org, age, *, deleted=False, started=True, now=None):
    now = now or timezone.now()
    row = AgentccRequestLog.no_workspace_objects.create(
        organization=org,
        request_id=f"r-{uuid.uuid4().hex[:10]}",
        model="gpt-4o",
        started_at=now - age if started else None,
    )
    LOGS.filter(pk=row.pk).update(created_at=now - age, deleted=deleted)
    return row


def _keep(org, days):
    return AgentccRequestLogRetention.no_workspace_objects.create(
        organization=org, retention_days=days
    )


def _ids(org):
    return set(LOGS.filter(organization=org).values_list("pk", flat=True))


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name="Retention Other Org")


@pytest.mark.integration
class TestPurge:
    def test_old_rows_live_and_soft_deleted_go_with_their_feedback(self, organization):
        old = _log(organization, timedelta(days=3))
        old_deleted = _log(organization, timedelta(days=3), deleted=True)
        recent = _log(organization, timedelta(hours=1))
        AgentccGuardrailFeedback.no_workspace_objects.create(
            organization=organization,
            request_log=old,
            check_name="pii",
            feedback="correct",
        )
        kept_feedback = AgentccGuardrailFeedback.no_workspace_objects.create(
            organization=organization,
            request_log=recent,
            check_name="pii",
            feedback="correct",
        )
        _keep(organization, 1)

        (result,) = retention.run_retention()

        assert result["deleted"] == 2
        assert _ids(organization) == {recent.pk}
        assert not LOGS.filter(pk__in=[old.pk, old_deleted.pk]).exists()
        assert list(
            AgentccGuardrailFeedback.all_objects.filter(organization=organization)
        ) == [kept_feedback]
        row = AgentccRequestLogRetention.no_workspace_objects.get(
            organization=organization
        )
        assert row.last_run_deleted_rows == 2
        assert row.last_run_started_at and row.last_run_finished_at
        assert row.last_run_error == ""

    def test_other_orgs_keep_forever_orgs_and_orgs_without_a_choice_are_untouched(
        self, organization, other_org
    ):
        _keep(organization, 1)
        forever = Organization.objects.create(name="Keeps forever")
        AgentccRequestLogRetention.no_workspace_objects.create(
            organization=forever, retention_days=None
        )
        untouched = {
            org: {_log(org, timedelta(days=40)).pk, _log(org, timedelta(days=2)).pk}
            for org in (other_org, forever)
        }
        _log(organization, timedelta(days=40))

        retention.run_retention()

        for org, ids in untouched.items():
            assert _ids(org) == ids
        assert _ids(organization) == set()

    def test_rows_without_started_at_go_by_created_at(self, organization):
        undated_old = _log(organization, timedelta(days=5), started=False)
        undated_new = _log(organization, timedelta(minutes=5), started=False)

        result = retention.purge_org_request_logs(
            organization.id, timezone.now() - timedelta(days=1)
        )

        assert result == {"deleted": 1, "complete": True, "error": ""}
        assert _ids(organization) == {undated_new.pk}
        assert undated_old.pk not in _ids(organization)

    def test_batches_are_honoured(self, organization):
        for _ in range(5):
            _log(organization, timedelta(days=3))

        with patch.object(retention.time, "sleep") as pause:
            result = retention.purge_org_request_logs(
                organization.id, timezone.now() - timedelta(days=1), batch_size=2
            )

        assert result == {"deleted": 5, "complete": True, "error": ""}
        # Batches of 2, 2 and 1: a pause after each full one.
        assert pause.call_count == 2

    def test_a_spent_time_budget_leaves_the_rest_for_the_next_run(self, organization):
        for _ in range(3):
            _log(organization, timedelta(days=3))

        result = retention.purge_org_request_logs(
            organization.id, timezone.now() - timedelta(days=1), time_budget_s=0
        )

        assert result == {"deleted": 0, "complete": False, "error": ""}
        assert len(_ids(organization)) == 3

    def test_a_total_budget_skips_the_org(self, organization):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            budgets={"org_limit": 100, "org_period": "total"},
        )
        _keep(organization, 1)
        old = _log(organization, timedelta(days=90))

        (result,) = retention.run_retention()

        assert result["skipped"] == "total budget"
        assert _ids(organization) == {old.pk}

    @pytest.mark.parametrize(
        "budgets",
        [
            {"org_limit": 100, "org_period": "monthly"},
            {"enabled": True, "orgLimit": 500, "orgPeriod": "monthly"},
        ],
    )
    def test_a_monthly_budget_keeps_this_months_rows_with_one_day_retention(
        self, organization, budgets
    ):
        # A fixed mid-month "now": on the 1st, now - 1 day is already last
        # month, and the test could not tell the clamp from no clamp.
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True, budgets=budgets
        )
        _keep(organization, 1)
        this_month = _log(
            organization, NOW - datetime(2026, 11, 10, tzinfo=UTC), now=NOW
        )
        last_month = _log(
            organization, NOW - datetime(2026, 10, 30, tzinfo=UTC), now=NOW
        )

        retention.run_retention(now=NOW)

        assert _ids(organization) == {this_month.pk}
        assert last_month.pk not in _ids(organization)

    def test_by_default_a_run_keeps_the_current_month(self, organization, settings):
        settings.AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS = False
        _keep(organization, 1)
        this_month = _log(
            organization, NOW - datetime(2026, 11, 3, tzinfo=UTC), now=NOW
        )
        _log(organization, NOW - datetime(2026, 10, 20, tzinfo=UTC), now=NOW)

        (result,) = retention.run_retention(now=NOW)

        assert result["cutoff"] == MONTH_START.isoformat()
        assert _ids(organization) == {this_month.pk}

    def test_organizations_never_run_go_first(self, organization, other_org):
        ran = _keep(other_org, 30)
        AgentccRequestLogRetention.all_objects.filter(pk=ran.pk).update(
            last_run_finished_at=NOW - timedelta(days=1)
        )
        _keep(organization, 30)

        results = retention.run_retention(dry_run=True, now=NOW)

        assert [r["organization_id"] for r in results] == [
            str(organization.id),
            str(other_org.id),
        ]

    def test_without_any_retention_row_nothing_is_deleted(
        self, organization, other_org
    ):
        before = {
            org: {_log(org, timedelta(days=400)).pk}
            for org in (organization, other_org)
        }

        assert retention.run_retention() == []

        for org, ids in before.items():
            assert _ids(org) == ids

    def test_estimate_is_capped(self, organization):
        for age in (3, 4, 5):
            _log(organization, timedelta(days=age))
        _log(organization, timedelta(days=6), started=False)
        cutoff = timezone.now() - timedelta(days=1)

        assert retention.estimate_rows(organization.id, cutoff) == {
            "count": 4,
            "capped": False,
        }
        assert retention.estimate_rows(organization.id, cutoff, cap=2) == {
            "count": 2,
            "capped": True,
        }
        assert retention.estimate_rows(organization.id, None) == {
            "count": 0,
            "capped": False,
        }

    def test_the_command_dry_run_deletes_nothing_and_the_run_does(self, organization):
        _keep(organization, 1)
        _log(organization, timedelta(days=3))
        _log(organization, timedelta(hours=2))

        out = StringIO()
        call_command(
            "purge_agentcc_request_logs",
            "--dry-run",
            "--org",
            str(organization.id),
            stdout=out,
        )
        assert "would delete 1" in out.getvalue()
        assert len(_ids(organization)) == 2

        out = StringIO()
        call_command(
            "purge_agentcc_request_logs", "--org", str(organization.id), stdout=out
        )
        assert "Deleted 1 request log rows across 1 organizations." in out.getvalue()
        assert len(_ids(organization)) == 1


def test_the_retention_schedule_is_registered():
    from model_hub.apps import OPERATOR_STARTUP_MUTATION_COMMANDS
    from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES
    from tfc.temporal.schedules import ALL_SCHEDULES
    from tfc.temporal.schedules.config import ScheduleOverlapPolicy

    (config,) = [
        s for s in ALL_SCHEDULES if s.schedule_id == "agentcc-request-log-retention"
    ]
    activity = retention.purge_expired_agentcc_request_logs
    assert config.activity_name == activity._activity_name
    assert config.cron_expression == "0 3 * * *"
    assert config.catchup_window_seconds == 86400
    assert config.overlap_policy == ScheduleOverlapPolicy.SKIP
    assert config.queue == "default"
    assert activity._metadata["queue"] == "default"
    assert "agentcc.services.request_log_retention" in TEMPORAL_ACTIVITY_MODULES
    assert "purge_agentcc_request_logs" in OPERATOR_STARTUP_MUTATION_COMMANDS


@pytest.fixture
def member_client(user, workspace):
    member = User.objects.create_user(
        email=f"member-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Member",
        organization=user.organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=user.organization,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    client.set_workspace(workspace)
    yield client
    client.stop_workspace_injection()


@pytest.mark.integration
@pytest.mark.api
class TestAPI:
    def test_default_is_keep_forever(self, auth_client):
        response = auth_client.get(URL)

        assert response.status_code == 200, response.content
        result = response.json()["result"]
        assert result["retention_days"] is None
        assert result["min_retention_days"] is None
        assert result["keep_forever_required"] is False
        assert result["effective_cutoff"] is None
        assert result["estimated_rows"] == 0
        assert (
            result["next_run_at"].endswith("03:00:00Z")
            or "T03:00:00" in result["next_run_at"]
        )
        assert result["last_run"]["finished_at"] is None

    def test_admin_saves_and_sees_the_estimate(self, auth_client, user):
        org = user.organization
        _log(org, timedelta(days=3))
        _log(org, timedelta(days=4))
        _log(org, timedelta(hours=1))

        response = auth_client.put(URL, {"retention_days": 1}, format="json")

        assert response.status_code == 200, response.content
        result = response.json()["result"]
        assert result["retention_days"] == 1
        assert result["estimated_rows"] == 2
        assert result["estimate_capped"] is False
        assert result["effective_cutoff"] is not None
        row = AgentccRequestLogRetention.no_workspace_objects.get(organization=org)
        assert (row.retention_days, row.updated_by_id) == (1, user.id)

        response = auth_client.put(URL, {"retention_days": None}, format="json")
        assert response.status_code == 200
        assert response.json()["result"]["retention_days"] is None
        assert (
            AgentccRequestLogRetention.no_workspace_objects.filter(
                organization=org
            ).count()
            == 1
        )

    def test_preview_estimates_an_unsaved_value(self, auth_client, user):
        _log(user.organization, timedelta(days=10))

        assert (
            auth_client.get(URL, {"retention_days": 30}).json()["result"][
                "estimated_rows"
            ]
            == 0
        )
        assert (
            auth_client.get(URL, {"retention_days": 5}).json()["result"][
                "estimated_rows"
            ]
            == 1
        )
        assert not AgentccRequestLogRetention.no_workspace_objects.exists()

    def test_a_member_reads_but_cannot_change_it(self, member_client):
        assert member_client.get(URL).status_code == 200

        response = member_client.put(URL, {"retention_days": 30}, format="json")

        assert response.status_code == 403
        assert not AgentccRequestLogRetention.no_workspace_objects.exists()

    def test_below_the_budget_floor_is_refused(self, auth_client, user):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            budgets={"org_limit": 50, "org_period": "monthly"},
        )

        response = auth_client.put(URL, {"retention_days": 7}, format="json")

        assert response.status_code == 400
        assert (
            "Retention must be at least 31 days because a monthly budget is configured"
            in response.content.decode()
        )
        assert auth_client.get(URL).json()["result"]["min_retention_days"] == 31
        assert (
            auth_client.put(URL, {"retention_days": 31}, format="json").status_code
            == 200
        )

    def test_a_total_budget_requires_keep_forever(self, auth_client, user):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            budgets={"keys": {"k": {"limit": 5, "period": "total"}}},
        )

        assert auth_client.get(URL).json()["result"]["keep_forever_required"] is True
        response = auth_client.put(URL, {"retention_days": 365}, format="json")
        assert response.status_code == 400
        assert "kept forever" in response.content.decode()
        assert (
            auth_client.put(URL, {"retention_days": None}, format="json").status_code
            == 200
        )

    @pytest.mark.parametrize("value", [0, -1, "x", 40000])
    def test_invalid_values_are_refused(self, auth_client, value):
        assert (
            auth_client.put(URL, {"retention_days": value}, format="json").status_code
            == 400
        )

    def test_another_orgs_retention_is_not_shown(self, auth_client, other_org):
        _keep(other_org, 3)

        assert auth_client.get(URL).json()["result"]["retention_days"] is None


@pytest.mark.integration
def test_a_failed_batch_keeps_the_count_of_the_batches_before_it(organization):
    for _ in range(3):
        _log(organization, timedelta(days=3))
    _keep(organization, 1)

    # The dated pass deletes its rows; the undated pass then fails.
    with patch.dict(retention._PURGE_SQL, {"created_at": "SELECT broken FROM nowhere"}):
        (result,) = retention.run_retention()

    assert result["deleted"] == 3
    assert result["complete"] is False
    assert result["error"]
    row = AgentccRequestLogRetention.no_workspace_objects.get(organization=organization)
    assert row.last_run_deleted_rows == 3
    assert row.last_run_error


@pytest.mark.integration
@pytest.mark.api
def test_a_first_save_racing_another_does_not_fail(auth_client, user):
    from agentcc.views import request_log_retention as view

    # Another admin's first save lands between this one's read and its insert.
    _keep(user.organization, 10)
    with patch.object(view, "_retention", side_effect=[None, None]):
        response = auth_client.put(URL, {"retention_days": 30}, format="json")

    assert response.status_code == 200, response.content
    rows = AgentccRequestLogRetention.no_workspace_objects.filter(
        organization=user.organization
    )
    assert [r.retention_days for r in rows] == [30]
