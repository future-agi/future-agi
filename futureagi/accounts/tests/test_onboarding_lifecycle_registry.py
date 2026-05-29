from collections import Counter
from copy import deepcopy

import pytest
from django.core.exceptions import ImproperlyConfigured

from accounts.services.onboarding.constants import ONBOARDING_ACTIVATION_EVENTS
from accounts.services.onboarding.lifecycle_registry import (
    _validate_config,
    get_lifecycle_registry_config,
    lifecycle_campaigns,
)
from accounts.services.onboarding.lifecycle_template_contract import (
    SUPPORTED_LIFECYCLE_TEMPLATE_KEYS,
    template_file_path,
)


def _valid_lifecycle_config():
    return deepcopy(get_lifecycle_registry_config())


def _campaign(config, campaign_key):
    return next(
        campaign
        for campaign in config["campaigns"]
        if campaign["campaign_key"] == campaign_key
    )


def test_lifecycle_campaign_registry_is_editable_and_valid():
    campaigns = lifecycle_campaigns()
    keys = [campaign["campaign_key"] for campaign in campaigns]

    assert len(keys) == len(set(keys))
    assert {
        "welcome_choose_goal",
        "observe_connect_first",
        "observe_waiting_for_first_trace",
        "observe_first_trace_ready",
        "observe_next_loop",
        "observe_sample_bridge",
        "prompt_create_first",
        "prompt_add_failure_example",
        "agent_create_first",
        "agent_create_eval",
        "gateway_add_provider",
        "gateway_add_policy",
        "daily_quality_open_actions",
    }.issubset(set(keys))

    group_counts = Counter(campaign["campaign_group"] for campaign in campaigns)
    assert group_counts["welcome"] >= 2
    assert group_counts["prompt"] >= 5
    assert group_counts["agent"] >= 5
    assert group_counts["gateway"] >= 6
    assert group_counts["recovery"] >= 2
    assert group_counts["activation_success"] >= 2

    for campaign in campaigns:
        assert campaign["template_key"].endswith("_v1")
        assert campaign["send_flag"] == "onboarding_lifecycle_send_enabled"
        assert campaign["target_success_event"] in ONBOARDING_ACTIVATION_EVENTS


def test_sample_bridge_campaign_is_configured_as_sample_only():
    campaign = next(
        campaign
        for campaign in lifecycle_campaigns()
        if campaign["campaign_key"] == "observe_sample_bridge"
    )

    assert campaign["sample_policy"] == "sample_only"
    assert campaign["route_strategy"] == "sample_project"
    assert campaign["dry_run_flag"] == "onboarding_email_sample_bridge_enabled"


def test_prompt_campaigns_are_configured_as_real_only():
    campaigns = [
        campaign
        for campaign in lifecycle_campaigns()
        if campaign["campaign_group"] == "prompt"
    ]

    assert len(campaigns) == 5
    for campaign in campaigns:
        assert campaign["primary_path"] == "prompt"
        assert campaign["sample_policy"] == "real_only"
        assert campaign["route_strategy"] == "activation_recommendation"
        assert campaign["dry_run_flag"] == "onboarding_email_prompt_enabled"


def test_lifecycle_registry_rejects_target_action_success_event_mismatch():
    config = _valid_lifecycle_config()
    campaign = _campaign(config, "prompt_create_first")
    campaign["target_success_event"] = "trace_received"

    with pytest.raises(ImproperlyConfigured):
        _validate_config(config)


def test_lifecycle_registry_rejects_primary_path_target_action_mismatch():
    config = _valid_lifecycle_config()
    campaign = _campaign(config, "prompt_create_first")
    campaign["primary_path"] = "agent"

    with pytest.raises(ImproperlyConfigured):
        _validate_config(config)


def test_lifecycle_registry_rejects_unsupported_campaign_feature_flags():
    config = _valid_lifecycle_config()
    campaign = _campaign(config, "prompt_create_first")
    campaign["dry_run_flag"] = "onboarding_email_not_real"

    with pytest.raises(ImproperlyConfigured):
        _validate_config(config)


def test_lifecycle_registry_rejects_unknown_template_key():
    config = _valid_lifecycle_config()
    campaign = _campaign(config, "prompt_create_first")
    campaign["template_key"] = "prompt_create_missing_v1"

    with pytest.raises(ImproperlyConfigured):
        _validate_config(config)


def test_lifecycle_registry_rejects_campaign_group_without_subject():
    config = _valid_lifecycle_config()
    campaign = _campaign(config, "prompt_create_first")
    campaign["campaign_group"] = "unknown_group"

    with pytest.raises(ImproperlyConfigured):
        _validate_config(config)


def test_lifecycle_registry_rejects_digest_template_without_preview_requirement():
    config = _valid_lifecycle_config()
    campaign = _campaign(config, "daily_quality_open_actions")
    campaign.pop("requires_digest_preview", None)

    with pytest.raises(ImproperlyConfigured):
        _validate_config(config)


def test_lifecycle_template_contract_files_exist():
    missing = [
        template_key
        for template_key in SUPPORTED_LIFECYCLE_TEMPLATE_KEYS
        if not template_file_path(template_key).is_file()
    ]

    assert missing == []
