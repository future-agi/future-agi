"""Cap policy is validated on complete config versions under the org lock."""

from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.cap_policy import CapPolicyRejected, validate_cap_policy
from agentcc.services.config_push import OrgConfigRejected, validate_org_config
from agentcc.services.org_config_writer import (
    activate_config_version,
    write_config_version,
    write_config_version_with,
)
from agentcc.tests.test_g8_cap_chokepoints import cap_sections


@pytest.mark.parametrize("enabled", [None, False])
def test_cap_disabled_policy_keeps_legacy_semantics(enabled):
    config = {"budgets": {"enabled": False, "fail_mode": "open"}}
    if enabled is not None:
        config["budgets"]["admission_caps_enabled"] = enabled
    validate_cap_policy(config)


@pytest.mark.parametrize("limit", [0, 0.000001, 1000000000])
def test_cap_zero_and_finite_limits(limit):
    config = cap_sections()
    config["budgets"]["agents"]["agent-a"]["limit"] = limit
    validate_cap_policy(config)


@pytest.mark.parametrize(
    "limit", [None, -1, float("inf"), float("nan"), True, "1", 1000000001]
)
def test_cap_unsafe_limits_rejected(limit):
    config = cap_sections()
    config["budgets"]["agents"]["agent-a"]["limit"] = limit
    with pytest.raises(CapPolicyRejected):
        validate_cap_policy(config)


@pytest.mark.parametrize(
    "patch",
    [
        {"period": "monthly"},
        {"hard": False},
        {"action": "warn"},
        {"action": "throttle"},
        {"per_model": {}},
        {"include_byok": False},
        {"throttle_rpm": 1},
        {"alert_thresholds": []},
    ],
)
def test_cap_unsupported_new_map_semantics(patch):
    config = cap_sections()
    config["budgets"]["key_ids"]["key-a"].update(patch)
    with pytest.raises(CapPolicyRejected):
        validate_cap_policy(config)


@pytest.mark.parametrize("change", ["binding", "ceiling", "unpriced"])
def test_cap_rollout_cannot_relax_envelope(change):
    config = cap_sections()
    config["routing"]["agents"]["max_run_usd"] = 1
    candidate = deepcopy(config["routing"])
    config["routing"]["rollout"] = {"routing": candidate}
    validate_cap_policy(config)
    if change == "binding":
        candidate["agents"]["key_bindings"].clear()
    elif change == "ceiling":
        candidate["agents"]["max_run_usd"] = 2
    else:
        candidate["agents"]["unpriced_under_cap"] = "allow"
    with pytest.raises(CapPolicyRejected):
        validate_cap_policy(config)


@pytest.mark.integration
@pytest.mark.django_db
class TestCentralCapPolicy:
    def _prior(self, organization):
        return AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True, **cap_sections()
        )

    def _unchanged(self, prior, push):
        prior.refresh_from_db()
        assert prior.is_active
        assert prior.budgets == cap_sections()["budgets"]
        assert (
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=prior.organization
            ).count()
            == 1
        )
        push.assert_not_called()

    def test_valid_policy_passes_complete_contract(self, organization):
        prior = self._prior(organization)
        validate_org_config(prior)
        push = MagicMock(return_value=True)
        current, _ = write_config_version(
            organization, {"cache": {"enabled": False}}, push=push
        )
        # First write adds server-owned time and activation order;
        # all configured ceilings and behavior remain unchanged.
        activated = deepcopy(current.budgets)
        assert activated.pop("policy_revision") > 0
        for level in ("agents", "key_ids"):
            for cap in activated[level].values():
                assert cap.pop("activated_at")
        assert activated == prior.budgets
        push.assert_called_once()

    @pytest.mark.parametrize(
        "mode,patch",
        [
            ("merge", {"budgets": {"fail_mode": "open"}}),
            ("merge", {"budgets": {"enabled": False}}),
            ("merge", {"budgets": {"agents": {"agent-a": {"limit": True}}}}),
            ("merge", {"routing": {"agents": {"maxRunUSD": "1"}}}),
            ("merge", {"budgets": {"no_max_tokens": None}}),
            ("merge", {"routing": {"agents": {"unpriced_under_cap": "allow"}}}),
            (
                "replace_sections",
                {"routing": {"agents": {"unpriced_under_cap": "allow"}}},
            ),
        ],
    )
    def test_invalid_patch_preserves_version_and_push(self, organization, mode, patch):
        prior = self._prior(organization)
        push = MagicMock(return_value=True)
        with pytest.raises(OrgConfigRejected):
            write_config_version(organization, patch, mode=mode, push=push)
        self._unchanged(prior, push)

    def test_callback_side_effects_roll_back(self, organization):
        prior = self._prior(organization)
        push = MagicMock(return_value=True)

        def updater(config):
            AgentccOrgConfig.no_workspace_objects.filter(pk=prior.pk).update(
                change_description="must roll back"
            )
            config.budgets["fail_mode"] = "open"

        before = prior.change_description
        with pytest.raises(OrgConfigRejected):
            write_config_version_with(organization, updater, push=push)
        self._unchanged(prior, push)
        assert prior.change_description == before

    def test_enabling_over_stored_fail_open_is_rejected(self, organization):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            budgets={"enabled": True, "fail_mode": "open", "no_max_tokens": "clamp"},
        )
        push = MagicMock(return_value=True)
        with pytest.raises(OrgConfigRejected):
            write_config_version(
                organization, {"budgets": {"admission_caps_enabled": True}}, push=push
            )
        assert (
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=organization, is_active=True
            )
            .get()
            .version
            == 1
        )
        push.assert_not_called()

    def test_inactive_invalid_target_never_deactivates_prior(self, organization):
        prior = self._prior(organization)
        invalid = cap_sections()
        invalid["budgets"]["enabled"] = False
        target = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=2, is_active=False, **invalid
        )
        push = MagicMock(return_value=True)
        with pytest.raises(OrgConfigRejected):
            activate_config_version(target, push=push)
        prior.refresh_from_db()
        target.refresh_from_db()
        assert prior.is_active and not target.is_active
        push.assert_not_called()
