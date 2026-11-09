"""The one writer for org-config versions: merge and replace_sections patches,
version numbering, validation before activation, the push, and warnings."""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.org_config_writer import (
    GATEWAY_SYNC_FAILED,
    MANAGED_MODELS_CHANGE,
    MERGE,
    REPLACE_SECTIONS,
    UnknownConfigSections,
    activate_config_version,
    deep_merge,
    is_gateway_synced,
    write_config_version,
    write_config_version_with,
)

DEFAULT_PUSH = "agentcc.services.config_push.push_org_config"


def _config(org, version=1, is_active=True, **sections):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=version, is_active=is_active, **sections
    )


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _versions(org):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=org)
        .order_by("version")
        .values_list("version", "is_active")
    )


def _pushed():
    return MagicMock(return_value=True)


class TestDeepMerge:
    def test_null_deletes_and_absent_keeps(self):
        base = {"strategy": "round_robin", "auto": {"preference": "cost"}, "x": 1}

        merged = deep_merge(base, {"auto": None, "x": 2})

        assert merged == {"strategy": "round_robin", "x": 2}

    def test_null_inside_a_dict_the_base_lacks_is_not_stored(self):
        merged = deep_merge({}, {"auto": {"preference": "cost", "candidates": None}})

        assert merged == {"auto": {"preference": "cost"}}

    def test_does_not_mutate_its_inputs(self):
        base = {"auto": {"preference": "cost"}}
        patch_ = {"auto": {"candidates": ["a"]}}

        deep_merge(base, patch_)

        assert base == {"auto": {"preference": "cost"}}
        assert patch_ == {"auto": {"candidates": ["a"]}}


@pytest.mark.integration
class TestMergeMode:
    def test_merge_null_deletes_absent_keeps(self, organization):
        _config(
            organization,
            routing={"strategy": "round_robin", "auto": {"preference": "cost"}},
            cache={"enabled": True, "default_ttl": 60},
            managed_models={"enabled": True},
        )

        config, warnings = write_config_version(
            organization,
            {"routing": {"auto": None, "fallback_enabled": True}, "cache": None},
            mode=MERGE,
            push=_pushed(),
        )

        assert config.routing == {"strategy": "round_robin", "fallback_enabled": True}
        # A null section is cleared back to the model default.
        assert config.cache == {}
        # Sections the patch leaves out are kept as they were.
        assert config.managed_models == {"enabled": True}
        assert warnings == []
        assert _active(organization).pk == config.pk

    def test_merge_without_an_active_version_starts_from_defaults(self, organization):
        config, _ = write_config_version(
            organization,
            {"routing": {"strategy": "round_robin"}},
            mode=MERGE,
            push=_pushed(),
        )

        assert config.version == 1
        assert config.routing == {"strategy": "round_robin"}
        assert config.cost_tracking == {"enabled": True}
        assert config.managed_models == {}


@pytest.mark.integration
class TestReplaceSectionsMode:
    def test_absent_sections_are_copied_and_present_ones_replaced(self, organization):
        _config(
            organization,
            routing={"strategy": "round_robin", "auto": {"preference": "cost"}},
            budgets={"enabled": True, "org_limit": 10},
            managed_models={"enabled": True},
        )

        config, _ = write_config_version(
            organization,
            {"routing": {"strategy": "least_latency"}},
            mode=REPLACE_SECTIONS,
            push=_pushed(),
        )

        assert config.routing == {"strategy": "least_latency"}
        assert config.budgets == {"enabled": True, "org_limit": 10}
        assert config.managed_models == {"enabled": True}

    @pytest.mark.parametrize("cleared", [{}, None])
    def test_empty_or_null_clears_a_section(self, organization, cleared):
        _config(
            organization,
            routing={"strategy": "round_robin"},
            managed_models={"enabled": True},
        )

        config, _ = write_config_version(
            organization,
            {"managed_models": cleared},
            mode=REPLACE_SECTIONS,
            push=_pushed(),
        )

        assert config.managed_models == {}
        assert config.routing == {"strategy": "round_robin"}


@pytest.mark.integration
class TestVersioning:
    def test_new_version_deactivates_the_old_one(self, organization, user):
        prior = _config(organization, version=1)

        config, _ = write_config_version(
            organization,
            {"cache": {"enabled": True}},
            description="Turn on the cache",
            user=user,
            push=_pushed(),
        )

        prior.refresh_from_db()
        assert prior.is_active is False
        assert _versions(organization) == [(1, False), (2, True)]
        assert config.change_description == "Turn on the cache"
        assert config.created_by_id == user.id

    def test_after_a_rollback_takes_the_next_free_version(self, organization):
        _config(organization, version=1, is_active=True)
        for version in (2, 3):
            _config(organization, version=version, is_active=False)

        config, _ = write_config_version(organization, {}, push=_pushed())

        assert config.version == 4
        assert _active(organization).version == 4

    def test_default_description_names_the_version(self, organization):
        config, _ = write_config_version(organization, {}, push=_pushed())

        assert config.change_description == "Config update v1"

    def test_copy_backfills_cost_tracking(self, organization):
        _config(organization, cost_tracking={})

        config, _ = write_config_version(organization, {}, push=_pushed())

        assert config.cost_tracking == {"enabled": True}

    def test_updater_entry_point_edits_a_copy_of_the_active_version(self, organization):
        prior = _config(organization, routing={"strategy": "round_robin"})

        def updater(config):
            config.routing["strategy"] = "least_latency"

        config, _ = write_config_version_with(organization, updater, push=_pushed())

        prior.refresh_from_db()
        assert prior.routing == {"strategy": "round_robin"}
        assert config.routing == {"strategy": "least_latency"}


@pytest.mark.integration
class TestRejectedPatches:
    def test_a_version_the_contract_rejects_never_becomes_active(self, organization):
        _config(organization, routing={"strategy": "round_robin"})
        push = _pushed()

        with pytest.raises(OrgConfigRejected) as rejected:
            write_config_version(
                organization,
                {"routing": {"default_strategy": "round-robin"}},
                push=push,
            )

        assert "routing.default_strategy" in str(rejected.value)
        assert _versions(organization) == [(1, True)]
        push.assert_not_called()

    def test_unknown_sections_are_refused(self, organization):
        with pytest.raises(UnknownConfigSections) as unknown:
            write_config_version(
                organization, {"routing": {}, "colour": {}, "size": 1}, push=_pushed()
            )

        assert str(unknown.value) == "Unknown config fields: colour, size"
        assert _versions(organization) == []

    def test_unknown_mode_is_refused(self, organization):
        with pytest.raises(ValueError, match="overwrite"):
            write_config_version(organization, {}, mode="overwrite", push=_pushed())

    def test_a_patch_must_be_an_object(self, organization):
        with pytest.raises(TypeError):
            write_config_version(organization, ["routing"], push=_pushed())


@pytest.mark.integration
class TestPush:
    def test_push_sees_the_saved_active_version(self, organization):
        _config(organization)
        seen = []

        def push(config):
            seen.append((config.version, _active(organization).version))
            return True

        config, warnings = write_config_version(organization, {}, push=push)

        assert seen == [(2, 2)]
        assert is_gateway_synced(warnings)

    def test_failed_push_keeps_the_version_and_warns(self, organization):
        _config(organization)

        config, warnings = write_config_version(
            organization, {}, push=MagicMock(return_value=False)
        )

        assert _active(organization).pk == config.pk
        assert [w["code"] for w in warnings] == [GATEWAY_SYNC_FAILED]
        assert (
            warnings[0]["message"]
            == "Config saved but gateway sync failed. A gateway connected to this app retries fetching saved configs during its periodic sync."
        )
        assert is_gateway_synced(warnings) is False

    def test_pushes_through_config_push_by_default(self, organization):
        with patch(DEFAULT_PUSH, return_value=True) as default_push:
            config, warnings = write_config_version(organization, {})

        default_push.assert_called_once_with(str(organization.id), config)
        assert warnings == []


@pytest.mark.integration
class TestManagedModelsWarning:
    @pytest.mark.parametrize(
        ("before", "after", "expected"),
        [
            ({}, {"enabled": True}, [(False, True)]),
            ({"enabled": True}, {"enabled": False}, [(True, False)]),
            ({"enabled": True}, None, [(True, False)]),
            ({"enabled": True}, {"enabled": True}, []),
            ({}, {"enabled": False}, []),
        ],
    )
    def test_warns_only_when_enabled_flips(self, organization, before, after, expected):
        _config(organization, managed_models=before)

        _, warnings = write_config_version(
            organization,
            {"managed_models": after},
            mode=REPLACE_SECTIONS,
            push=_pushed(),
        )

        assert [
            (w["from"], w["to"]) for w in warnings if w["code"] == MANAGED_MODELS_CHANGE
        ] == expected

    def test_first_version_that_opts_in_warns(self, organization):
        _, warnings = write_config_version(
            organization, {"managed_models": {"enabled": True}}, push=_pushed()
        )

        assert warnings == [{"code": MANAGED_MODELS_CHANGE, "from": False, "to": True}]


@pytest.mark.integration
class TestActivate:
    def test_activate_swaps_the_active_version_and_warns(self, organization):
        current = _config(organization, version=1, managed_models={"enabled": True})
        older = _config(organization, version=2, is_active=False)
        push = _pushed()

        config, warnings = activate_config_version(older, push=push)

        current.refresh_from_db()
        assert config.pk == older.pk
        assert _versions(organization) == [(1, False), (2, True)]
        assert warnings == [{"code": MANAGED_MODELS_CHANGE, "from": True, "to": False}]
        push.assert_called_once_with(older)

    def test_activate_refuses_a_rejected_version(self, organization):
        _config(organization, version=1)
        stale = _config(
            organization,
            version=2,
            is_active=False,
            routing={"default_strategy": "round-robin"},
        )
        push = _pushed()

        with pytest.raises(OrgConfigRejected):
            activate_config_version(stale, push=push)

        assert _versions(organization) == [(1, True), (2, False)]
        push.assert_not_called()

    def test_activating_the_active_version_changes_nothing(self, organization):
        current = _config(organization, version=1, managed_models={"enabled": True})
        push = _pushed()

        config, warnings = activate_config_version(current, push=push)

        assert config.pk == current.pk
        assert warnings == []
        push.assert_not_called()

    def test_failed_push_on_activate_warns(self, organization):
        _config(organization, version=1)
        older = _config(organization, version=2, is_active=False)

        _, warnings = activate_config_version(older, push=MagicMock(return_value=False))

        assert [w["code"] for w in warnings] == [GATEWAY_SYNC_FAILED]


@pytest.fixture
def activation_seam(organization):
    """Real, already-stamped cap policy plus a bound shared synthetic grant."""
    from datetime import UTC, datetime

    from agentcc.models.cap_activation import (
        AgentccCapActivation,
        AgentccCapPolicyRevision,
    )
    from agentcc.models.mcp_credential import AgentccMCPCredential
    from agentcc.services.credential_manager import encrypt_token

    activated = datetime(2026, 10, 27, tzinfo=UTC)
    stamp = "2026-10-27T00:00:00Z"
    for level in ("agent:agent-a", "key_id:key-a"):
        AgentccCapActivation.objects.create(
            organization=organization, level=level, activated_at=activated
        )
    AgentccCapPolicyRevision.objects.create(organization=organization, revision=42)
    grant = AgentccMCPCredential.objects.create(
        organization=organization,
        server_id="docs",
        scope="shared",
        kind="bearer",
        origin="https://docs.example.com:443",
        encrypted_secret=encrypt_token("test-only-w12-activation-seam"),
        grant_revision=1,
    )
    config = _config(
        organization,
        budgets={
            "enabled": True,
            "admission_caps_enabled": True,
            "no_max_tokens": "clamp",
            "policy_revision": 42,
            "agents": {
                "agent-a": {"limit": 0.05, "period": "daily", "activated_at": stamp}
            },
            "key_ids": {
                "key-a": {"limit": 0.05, "period": "daily", "activated_at": stamp}
            },
        },
        routing={"agents": {"key_bindings": {"key-a": "agent-a"}}},
        mcp={
            "servers": {
                "docs": {
                    "url": "https://docs.example.com/mcp",
                    "transport": "http",
                    "credential": {
                        "credential_id": str(grant.pk),
                        "mode": "shared",
                        "kind": "bearer",
                        "state": "active",
                    },
                }
            }
        },
    )
    return config, grant


@pytest.fixture
def activation_observers(activation_seam):
    """Observe the real validators/stamper/save, never replace their behaviour."""
    from contextlib import ExitStack
    from types import SimpleNamespace

    from agentcc.services import org_config_writer as writer

    with ExitStack() as stack:
        observed = SimpleNamespace(push=_pushed())
        for name in (
            "_refuse_activated_origin_moves",
            "stamp_cap_activation",
            "audit_capture_change",
            "require_capture_admin",
            "_refuse_activated_mcp_secrets",
        ):
            setattr(
                observed,
                name,
                stack.enter_context(
                    patch.object(writer, name, wraps=getattr(writer, name))
                ),
            )
        observed.validate = stack.enter_context(
            patch.object(
                writer.config_push,
                "validate_org_config",
                wraps=writer.config_push.validate_org_config,
            )
        )
        observed.save = stack.enter_context(
            patch.object(
                AgentccOrgConfig,
                "save",
                autospec=True,
                side_effect=AgentccOrgConfig.save,
            )
        )
        yield observed


def _activation_durable_state(org):
    from agentcc.models.cap_activation import (
        AgentccCapActivation,
        AgentccCapPolicyRevision,
    )

    return (
        list(
            AgentccOrgConfig.no_workspace_objects.filter(organization=org)
            .order_by("version")
            .values("id", "version", "is_active", "budgets", "mcp", "updated_at")
        ),
        list(
            AgentccCapPolicyRevision.objects.filter(organization=org).values_list(
                "revision", flat=True
            )
        ),
        list(
            AgentccCapActivation.objects.filter(organization=org)
            .order_by("level")
            .values_list("level", "activated_at")
        ),
    )


@pytest.mark.integration
@pytest.mark.django_db
class TestW12CapActivationSeam:
    @pytest.mark.parametrize("status", ["revoked", "expired", "needs_reauth"])
    def test_invalid_grant_with_valid_caps_refuses(self, activation_seam, status):
        from agentcc.services import config_push
        from agentcc.services.org_config_writer import MCPConfigRejected

        config, grant = activation_seam
        config_push.validate_org_config(config, stored_routing=config.routing)
        type(grant).objects.filter(pk=grant.pk).update(status=status)
        push = _pushed()
        before = _activation_durable_state(config.organization)
        with pytest.raises(MCPConfigRejected) as rejected:
            activate_config_version(config, push=push)
        assert rejected.value.code == "mcp_credential_origin_changed"
        assert _activation_durable_state(config.organization) == before
        push.assert_not_called()

    def test_invalid_caps_with_valid_grant_refuses(self, activation_seam):
        from agentcc.services.org_config_writer import _refuse_activated_origin_moves

        config, _ = activation_seam
        _refuse_activated_origin_moves(config, config)
        config.budgets["fail_mode"] = "open"
        config.save(update_fields=["budgets"])
        before = _activation_durable_state(config.organization)
        push = _pushed()
        with pytest.raises(OrgConfigRejected, match="budgets.fail_mode"):
            activate_config_version(config, push=push)
        assert _activation_durable_state(config.organization) == before
        push.assert_not_called()

    def test_unchanged_same_active_validates_without_push_or_revision_advance(
        self, activation_seam, activation_observers
    ):
        config, _ = activation_seam
        observed = activation_observers
        before = _activation_durable_state(config.organization)
        result, warnings = activate_config_version(config, push=observed.push)
        assert result.pk == config.pk
        assert warnings == []
        assert _activation_durable_state(config.organization) == before
        assert result.budgets["policy_revision"] == 42
        observed._refuse_activated_origin_moves.assert_called_once()
        observed.validate.assert_called_once_with(result, stored_routing=result.routing)
        observed.stamp_cap_activation.assert_called_once_with(
            result, advance_revision=False
        )
        observed.require_capture_admin.assert_not_called()
        observed._refuse_activated_mcp_secrets.assert_not_called()
        observed.save.assert_not_called()
        observed.audit_capture_change.assert_not_called()
        observed.push.assert_not_called()

    def test_stamping_only_change_persists(self, activation_seam, activation_observers):
        config, _ = activation_seam
        observed = activation_observers
        del config.budgets["agents"]["agent-a"]["activated_at"]
        AgentccOrgConfig.no_workspace_objects.filter(pk=config.pk).update(
            budgets=config.budgets
        )
        before = _activation_durable_state(config.organization)
        pushed_budgets = []

        def push(saved):
            from copy import deepcopy

            stored = AgentccOrgConfig.no_workspace_objects.get(pk=saved.pk)
            assert stored.budgets == saved.budgets
            pushed_budgets.append(deepcopy(stored.budgets))
            return True

        observed.push.side_effect = push
        result, warnings = activate_config_version(config, push=observed.push)
        config.refresh_from_db()
        assert (
            config.budgets["agents"]["agent-a"].get("activated_at")
            == "2026-10-27T00:00:00Z"
        )
        assert config.budgets["policy_revision"] == 42
        assert _activation_durable_state(config.organization)[1:] == before[1:]
        assert _versions(config.organization) == [(1, True)]
        assert warnings == []
        assert pushed_budgets == [config.budgets]
        observed.save.assert_called_once_with(
            result, update_fields=["budgets", "updated_at"]
        )
        observed.stamp_cap_activation.assert_called_once_with(
            result, advance_revision=False
        )
        observed.validate.assert_called_once_with(result, stored_routing=result.routing)
        observed.audit_capture_change.assert_not_called()
        observed.push.assert_called_once_with(result)

    @pytest.mark.parametrize("failure", ["grant", "caps", "both"])
    @pytest.mark.parametrize("same_active", [True, False])
    def test_failures_have_zero_save_push_or_audit_effect(
        self, activation_seam, activation_observers, failure, same_active
    ):
        from agentcc.services.org_config_writer import MCPConfigRejected

        config, grant = activation_seam
        observed = activation_observers
        if not same_active:
            AgentccOrgConfig.no_workspace_objects.filter(pk=config.pk).update(
                is_active=False
            )
            _config(config.organization, version=2)
            observed.save.reset_mock()  # Only the activation is under observation.
        if failure in ("grant", "both"):
            type(grant).objects.filter(pk=grant.pk).update(status="revoked")
        if failure in ("caps", "both"):
            config.budgets["fail_mode"] = "open"
            AgentccOrgConfig.no_workspace_objects.filter(pk=config.pk).update(
                budgets=config.budgets
            )
        before = _activation_durable_state(config.organization)
        error = OrgConfigRejected if failure == "caps" else MCPConfigRejected
        with pytest.raises(error) as rejected:
            activate_config_version(config, push=observed.push)
        if failure == "caps":
            assert "budgets.fail_mode" in str(rejected.value)
        else:
            assert rejected.value.code == "mcp_credential_origin_changed"
        assert _activation_durable_state(config.organization) == before
        observed.save.assert_not_called()
        observed.stamp_cap_activation.assert_not_called()
        observed.audit_capture_change.assert_not_called()
        observed.push.assert_not_called()
