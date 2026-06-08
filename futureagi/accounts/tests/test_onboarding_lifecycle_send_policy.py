from copy import deepcopy

import pytest
from django.core.exceptions import ImproperlyConfigured

from accounts.services.onboarding.lifecycle_registry import lifecycle_campaigns
from accounts.services.onboarding.lifecycle_send_policy import (
    _validate_config,
    eligibility_frequency_caps,
    external_lifecycle_delivery_campaign_groups,
    external_lifecycle_delivery_channels,
    get_lifecycle_send_policy,
    lifecycle_preference_group_field,
    send_frequency_caps,
    send_non_cloud_suppression_reason,
)


def _valid_send_policy():
    return deepcopy(get_lifecycle_send_policy())


def test_lifecycle_send_policy_exposes_current_preference_groups():
    assert lifecycle_preference_group_field("prompt") == (
        "first_action_recovery_enabled"
    )
    assert lifecycle_preference_group_field("agent") == "first_action_recovery_enabled"
    assert lifecycle_preference_group_field("gateway") == (
        "first_action_recovery_enabled"
    )
    assert lifecycle_preference_group_field("eval") == "first_action_recovery_enabled"
    assert lifecycle_preference_group_field("voice") == "first_action_recovery_enabled"
    assert lifecycle_preference_group_field("sample") == "sample_bridge_enabled"
    assert lifecycle_preference_group_field("next_loop") == "next_loop_enabled"
    assert lifecycle_preference_group_field("activation_success") == (
        "daily_digest_enabled"
    )
    assert lifecycle_preference_group_field("unknown") is None


def test_lifecycle_send_policy_covers_all_campaign_groups():
    configured_groups = {
        campaign["campaign_group"] for campaign in lifecycle_campaigns()
    }
    mapped_groups = set(get_lifecycle_send_policy()["preference_groups"])

    assert configured_groups <= mapped_groups


def test_lifecycle_send_policy_exposes_frequency_caps_in_send_order():
    eligibility_caps = eligibility_frequency_caps()
    send_caps = send_frequency_caps()

    assert [cap["id"] for cap in eligibility_caps] == [
        "user_7d",
        "user_24h",
        "workspace_24h",
        "campaign_user_7d",
    ]
    assert eligibility_caps[0] == {
        "id": "user_7d",
        "scope": "user",
        "window_hours": 168,
        "limit": 3,
        "reason": "frequency_cap_user_7d",
    }
    assert [cap["id"] for cap in send_caps] == [
        "user_24h",
        "group_72h",
        "daily_digest_24h",
        "dormant_reactivation_lifetime",
        "recovery_group_lifetime",
    ]
    dormant_cap = send_caps[3]
    assert dormant_cap["scope"] == "campaign_key"
    assert dormant_cap["window_hours"] is None
    assert dormant_cap["campaign_keys"] == ["dormant_reactivation"]
    assert send_caps[-1]["window_hours"] is None
    assert send_caps[-1]["campaign_groups"] == ["recovery", "first_signal", "next_loop"]


def test_lifecycle_send_policy_exposes_environment_and_external_delivery_rules():
    assert send_non_cloud_suppression_reason() == "cloud_deployment_required"
    assert external_lifecycle_delivery_channels() == ("slack", "webhook")
    assert external_lifecycle_delivery_campaign_groups() == (
        "welcome",
        "recovery",
        "sample",
        "first_signal",
        "prompt",
        "agent",
        "gateway",
        "eval",
        "voice",
        "next_loop",
        "activation_success",
    )


def test_lifecycle_send_policy_rejects_unknown_preference_field():
    config = _valid_send_policy()
    config["preference_groups"]["prompt"] = "not_a_real_preference"

    with pytest.raises(ImproperlyConfigured):
        _validate_config(config)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("scope", "organization"),
        ("window_hours", 0),
        ("limit", 0),
        ("reason", ""),
    ),
)
def test_lifecycle_send_policy_rejects_invalid_frequency_caps(field, value):
    config = _valid_send_policy()
    config["eligibility_frequency_caps"][0][field] = value

    with pytest.raises(ImproperlyConfigured):
        _validate_config(config)


def test_lifecycle_send_policy_rejects_invalid_campaign_key_filter():
    config = _valid_send_policy()
    config["send_frequency_caps"][0]["campaign_keys"] = [""]

    with pytest.raises(ImproperlyConfigured):
        _validate_config(config)


def test_lifecycle_send_policy_rejects_unknown_external_channel():
    config = _valid_send_policy()
    config["external_delivery"]["channels"].append("sms")

    with pytest.raises(ImproperlyConfigured):
        _validate_config(config)
