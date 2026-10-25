"""What the Budgets page's Set Budget dialog pushes.

Every case posts the dialog's own body (SetBudgetDialog.jsx handleSave) to the
real set-budget view, captures the org config Django then sends to the
gateway, and checks its budgets section:

- budgets turn on when a level is set and the org never chose (enabled), an
  explicit off stays off, removing the last level turns them off;
- Alert Threshold (%) reaches the gateway as warn_threshold (0-1);
- When Limit Exceeded = Throttle sends the throttle and its throttle_rpm;
- of the two org-level dialog entries (Organization, Hard Limit) the later
  save wins, whole.

The pushed sections are pinned twice: inline here and in the gateway's
plugins/budget/testdata/console_push.json, which the gateway's budget console
tests (budget_console_test.go) decode through the admin contract and enforce. The
fixture must equal the inline values.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentcc.contracts.gateway_admin import BudgetsConfig as GatewayBudgetsConfig
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import _transform_budgets

GATEWAY = "/agentcc/gateways/default"
T1 = "2026-10-21T08:00:00.000Z"
T2 = "2026-10-21T09:00:00.000Z"
FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "agentcc-gateway/internal/plugins/budget/testdata/console_push.json"
)


def _dialog(limit, on_exceed, alert=80, saved_at=T1, **extra):
    """SetBudgetDialog.jsx handleSave's config for these field values."""
    return {
        "limit": limit,
        "alert_threshold": alert,
        "on_exceed": on_exceed,
        "saved_at": saved_at,
        **extra,
    }


# case -> (budgets stored before the saves, [(level, dialog config), ...], pushed)
CASES = {
    "org_block": (
        None,
        [("org_limit", _dialog(0.000001, "block"))],
        {
            "action": "block",
            "enabled": True,
            "hard_limit": True,
            "org_limit": 1e-06,
            "warn_threshold": 0.8,
        },
    ),
    "org_throttle": (
        None,
        [("org_limit", _dialog(0.000001, "throttle", throttle_rpm=2))],
        {
            "action": "throttle",
            "enabled": True,
            "hard_limit": False,
            "org_limit": 1e-06,
            "throttle_rpm": 2,
            "warn_threshold": 0.8,
        },
    ),
    "org_alert_50": (
        None,
        [("org_limit", _dialog(100, "warn", alert=50))],
        {
            "action": "warn",
            "enabled": True,
            "hard_limit": False,
            "org_limit": 100,
            "warn_threshold": 0.5,
        },
    ),
    "hard_limit_later": (
        None,
        [
            ("org_limit", _dialog(100, "block", saved_at=T1)),
            ("hard_limit", _dialog(0.000001, "block", alert=90, saved_at=T2)),
        ],
        {
            "action": "block",
            "enabled": True,
            "hard_limit": True,
            "org_limit": 1e-06,
            "warn_threshold": 0.9,
        },
    ),
    "org_limit_later": (
        None,
        [
            ("hard_limit", _dialog(0.000001, "block", alert=90, saved_at=T1)),
            ("org_limit", _dialog(100, "block", saved_at=T2)),
        ],
        {
            "action": "block",
            "enabled": True,
            "hard_limit": True,
            "org_limit": 100,
            "warn_threshold": 0.8,
        },
    ),
    "explicit_off": (
        {"enabled": False},
        [("org_limit", _dialog(0.000001, "block"))],
        {
            "action": "block",
            "enabled": False,
            "hard_limit": True,
            "org_limit": 1e-06,
            "warn_threshold": 0.8,
        },
    ),
}


@pytest.fixture
def pushed():
    """Every org config Django pushes, as the gateway would receive it."""
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


def _last_budgets(pushed):
    assert pushed.set_org_config.called, "nothing was pushed to the gateway"
    return pushed.set_org_config.call_args[0][1]["budgets"]


def _set_budget(auth_client, level, config):
    response = auth_client.post(
        f"{GATEWAY}/set-budget/", {"level": level, "config": config}, format="json"
    )
    assert response.status_code == 200, response.content
    assert response.json()["result"]["gateway_synced"] is True, response.content
    return response


@pytest.mark.integration
@pytest.mark.parametrize("case", sorted(CASES))
def test_budget_set_budget_push(case, organization, auth_client, pushed):
    stored, saves, want = CASES[case]
    if stored is not None:
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True, budgets=stored
        )
    for level, config in saves:
        _set_budget(auth_client, level, config)
    budgets = _last_budgets(pushed)
    assert budgets == want, json.dumps(budgets, sort_keys=True)
    # The section is one the gateway's strict contract reads.
    GatewayBudgetsConfig.model_validate(budgets, strict=True)
    # The dialog's own values stay stored as sent, so the console shows them.
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )
    for level, config in saves:
        assert active.budgets[level] == config


def test_budget_fixture_matches_the_push():
    """The gateway's copy of these pushes is the one pinned here."""
    if not FIXTURE.exists():
        pytest.skip(f"gateway fixture not in this checkout: {FIXTURE}")
    fixture = {
        k: v
        for k, v in json.loads(FIXTURE.read_text()).items()
        if not k.startswith("_")
    }
    assert fixture == {case: want for case, (_, _, want) in CASES.items()}


@pytest.mark.integration
def test_budget_removing_the_last_level_turns_budgets_off(
    organization, auth_client, pushed
):
    _set_budget(auth_client, "org_limit", _dialog(100, "block"))
    assert _last_budgets(pushed)["enabled"] is True
    response = auth_client.post(
        f"{GATEWAY}/remove-budget/", {"level": "org_limit"}, format="json"
    )
    assert response.status_code == 200, response.content
    budgets = _last_budgets(pushed)
    # Nothing is left to enforce: the section is empty or says off.
    assert budgets.get("enabled") in (None, False), budgets
    assert "org_limit" not in budgets


@pytest.mark.integration
def test_budget_explicit_on_without_levels_stays_on(organization, auth_client, pushed):
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        budgets={"enabled": True, "org_limit": {"limit": 5, "on_exceed": "warn"}},
    )
    response = auth_client.post(
        f"{GATEWAY}/remove-budget/", {"level": "org_limit"}, format="json"
    )
    assert response.status_code == 200, response.content
    assert _last_budgets(pushed)["enabled"] is True


def test_budget_team_level_threshold_and_throttle_reach_the_level():
    budgets = _transform_budgets(
        {
            "teams": {
                "eng": {
                    "limit": 50,
                    "action": "throttle",
                    "throttle_rpm": 3,
                    "alert_threshold": 60,
                    "saved_at": T1,
                },
                "ops": {"limit": 0},
            }
        }
    )
    assert budgets["enabled"] is True
    assert budgets["teams"]["eng"] == {
        "limit": 50,
        "action": "throttle",
        "hard": False,
        "throttle_rpm": 3,
        "alert_thresholds": [0.6],
    }
    GatewayBudgetsConfig.model_validate(budgets, strict=True)


def test_budget_only_zero_limits_is_not_a_configured_level():
    budgets = _transform_budgets({"org_limit": 0, "teams": {"ops": {"limit": 0}}})
    assert budgets["enabled"] is False


def test_budget_unstamped_entries_keep_the_organization_entry():
    """Org-level entries without saved_at: the push sends the Organization entry,
    whole, over Hard Limit, with that entry's alert threshold."""
    budgets = _transform_budgets(
        {
            "org_limit": {"limit": 100, "alert_threshold": 70, "on_exceed": "warn"},
            "hard_limit": {"limit": 5, "alert_threshold": 90, "on_exceed": "block"},
        }
    )
    assert budgets["org_limit"] == 100
    assert budgets["action"] == "warn" and budgets["hard_limit"] is False
    assert budgets["warn_threshold"] == 0.7
    assert budgets["enabled"] is True
    GatewayBudgetsConfig.model_validate(budgets, strict=True)


def test_budget_a_stamped_save_beats_an_unstamped_one():
    budgets = _transform_budgets(
        {
            "org_limit": {"limit": 100, "on_exceed": "warn"},
            "hard_limit": {"limit": 5, "on_exceed": "block", "saved_at": T1},
        }
    )
    assert budgets["org_limit"] == 5
    assert budgets["action"] == "block" and budgets["hard_limit"] is True


def test_budget_an_unreadable_saved_at_counts_as_unstamped():
    budgets = _transform_budgets(
        {
            "org_limit": {"limit": 100, "on_exceed": "warn", "saved_at": "yesterday"},
            "hard_limit": {"limit": 5, "on_exceed": "block", "saved_at": T1},
        }
    )
    assert budgets["org_limit"] == 5


@pytest.mark.parametrize(
    "alert,want", [(0, None), (101, None), ("80", None), (True, None), (100, 1.0)]
)
def test_budget_alert_threshold_outside_0_100_is_not_sent(alert, want):
    budgets = _transform_budgets(
        {"org_limit": {"limit": 10, "alert_threshold": alert, "on_exceed": "warn"}}
    )
    assert budgets.get("warn_threshold") == want
    GatewayBudgetsConfig.model_validate(budgets, strict=True)


@pytest.mark.parametrize("rpm", [0, -1, "2", 2.5, True])
def test_budget_a_throttle_rpm_that_is_not_a_whole_number_from_1_is_not_sent(rpm):
    budgets = _transform_budgets(
        {"org_limit": {"limit": 10, "on_exceed": "throttle", "throttle_rpm": rpm}}
    )
    assert "throttle_rpm" not in budgets
    GatewayBudgetsConfig.model_validate(budgets, strict=True)


def test_budget_org_config_flat_fields_are_untouched():
    """Org Config > Budgets stores the org level flat; the push keeps it."""
    stored = {
        "enabled": True,
        "org_limit": 40,
        "org_period": "daily",
        "warn_threshold": 0.6,
        "hard_limit": True,
    }
    budgets = _transform_budgets(stored)
    assert {k: v for k, v in budgets.items() if v is not None} == stored
