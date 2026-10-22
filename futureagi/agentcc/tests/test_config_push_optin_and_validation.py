"""
config_push: a rollout-only managed opt-in gets its managed credit account, and
save-time validation never assembles (decrypts) the per-org managed upstream
keys.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import (
    OrgConfigRejected,
    push_org_config,
    validate_org_config,
)

UPSTREAM_KEYS_MODULE = "agentcc.services.managed_upstream_keys"
CREDITS_MODULE = "ee.usage.services.managed_credits"
GATEWAY_CLIENT = "agentcc.services.config_push.get_gateway_client"


def _fake_module(name, **attrs):
    module = ModuleType(name)
    for attr, value in attrs.items():
        setattr(module, attr, value)
    return module


def _rollout(managed_models):
    return {
        "rollout": {
            "version": "v5",
            "percent": 10,
            "sticky_by": "session",
            "routing": {},
            "managed_models": managed_models,
        }
    }


def _config(user, **sections):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=user.organization, version=1, is_active=True, **sections
    )


def _push(config):
    client = MagicMock()
    with patch(GATEWAY_CLIENT, return_value=client):
        assert push_org_config(str(config.organization_id), config)
    client.set_org_config.assert_called_once()


@pytest.mark.integration
class TestRolloutOnlyOptInEnsuresAccount:
    @pytest.mark.parametrize(
        "sections, ensured",
        [
            ({"routing": _rollout({"enabled": True})}, True),
            ({"managed_models": {"enabled": True}}, True),
            ({"routing": _rollout({"enabled": False})}, False),
            ({}, False),
        ],
        ids=["rollout_only", "top_level", "rollout_off", "not_opted_in"],
    )
    def test_push_ensures_account_when_the_org_or_its_rollout_opts_in(
        self, user, django_capture_on_commit_callbacks, sections, ensured
    ):
        config = _config(user, **sections)
        ensure = MagicMock()
        credits = _fake_module(CREDITS_MODULE, ensure_account_pushed=ensure)

        with patch.dict(sys.modules, {CREDITS_MODULE: credits}):
            with django_capture_on_commit_callbacks(execute=True):
                _push(config)

        if ensured:
            ensure.assert_called_once_with(user.organization)
        else:
            ensure.assert_not_called()


@pytest.mark.integration
class TestValidationDoesNotAssembleUpstreamKeys:
    @pytest.mark.parametrize(
        "sections",
        [
            {"managed_models": {"enabled": True}},
            {"routing": _rollout({"enabled": True})},
        ],
        ids=["top_level", "rollout_only"],
    )
    def test_validate_never_calls_the_upstream_key_service(self, user, sections):
        config = _config(user, **sections)
        assemble = MagicMock(return_value={})
        module = _fake_module(UPSTREAM_KEYS_MODULE, assemble_for_push=assemble)

        with patch.dict(sys.modules, {UPSTREAM_KEYS_MODULE: module}):
            validate_org_config(config)
            assemble.assert_not_called()

            # The push still assembles them.
            _push(config)
        assemble.assert_called_once_with(str(config.organization_id))

    def test_validate_still_rejects_what_the_contract_refuses(self, user):
        # A key the contract refuses (as in test_org_config_rejected_at_save).
        config = _config(
            user,
            managed_models={"enabled": True},
            routing={"default_strategy": "round-robin"},
        )
        assemble = MagicMock(return_value={})
        module = _fake_module(UPSTREAM_KEYS_MODULE, assemble_for_push=assemble)

        with patch.dict(sys.modules, {UPSTREAM_KEYS_MODULE: module}):
            with pytest.raises(OrgConfigRejected):
                validate_org_config(config)
        assemble.assert_not_called()
