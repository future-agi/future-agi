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
        assert warnings[0]["message"]
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
