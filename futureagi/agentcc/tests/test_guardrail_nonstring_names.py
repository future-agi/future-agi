"""A guardrail check or alert channel whose name is not a string is refused
with a 400 that names the field, and the callback-URL checks never crash on
one.

The URL checks (agentcc/services/guardrail_urls.py) compare a save against
what is stored by the check's or channel's name, and policy sync merges
checks by name. So:

- the URL checks key a name that is not a string by its type and repr, so
  they never crash and never mix it up with a string name;
- a check name that is not a string is refused where it enters: the
  org-config write serializer and ``validate_org_config`` (every org-config
  save: update-config, POST org-configs, activation) answer
  ``guardrails.rules[i].name must be a string``, the guardrail policy
  serializer ``checks[i].name must be a string``; an alert channel's name
  is refused by the gateway contract (``alerting.channels.<i>.name``);
- a policy stored with such a name can be repaired: renaming the check,
  switching the policy off or deleting it saves and syncs.
"""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.serializers.org_config import AgentccOrgConfigWriteSerializer
from agentcc.services import guardrail_urls
from agentcc.services.config_push import OrgConfigRejected, validate_org_config

GATEWAY = "/agentcc/gateways/default"
UPDATE_CONFIG = f"{GATEWAY}/update-config/"
ORG_CONFIGS = "/agentcc/org-configs/"
POLICIES = "/agentcc/guardrail-policies/"

OK = "https://hooks.example.test/in"
BAD = "ftp://hooks.example.test/in"
NAMES = [["x"], {"a": 1}]
NAME_IDS = ["list", "dict"]


def _check(name, url=OK):
    return {"name": name, "enabled": True, "action": "log", "config": {"url": url}}


def _channel(name, url=OK):
    return {"name": name, "type": "webhook", "url": url}


@contextmanager
def _gateway():
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _versions(organization):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization)
        .order_by("version")
        .values_list("version", "is_active")
    )


def _assert_refused_naming(response, *fragments):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    body = json.dumps(response.json())
    for fragment in fragments:
        assert fragment in body, body
    assert "unhashable" not in body, body


# ----------------------------------------------------------------------------
# The URL checks never crash on such a name.
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES, ids=NAME_IDS)
@pytest.mark.parametrize("shape", ["rules", "checks"])
def test_guardrail_url_check_takes_a_nonstring_name(name, shape):
    good = {shape: [_check(name)]}
    bad = {shape: [_check(name, BAD)]}

    # New and stored, a well-formed URL passes.
    guardrail_urls.reject_changed_bad_guardrail_urls(good, {})
    guardrail_urls.reject_changed_bad_guardrail_urls({}, good)
    guardrail_urls.reject_changed_bad_guardrail_urls(good, good)
    # A malformed URL it adds is still refused by its field path.
    with pytest.raises(guardrail_urls.BadGuardrailURL) as refused:
        guardrail_urls.reject_changed_bad_guardrail_urls(bad, {})
    assert refused.value.param == f"guardrails.{shape}[0].config.url"
    # The same malformed URL, stored and left alone, does not block the save.
    guardrail_urls.reject_changed_bad_guardrail_urls(bad, bad)


def test_a_nonstring_name_is_not_taken_for_a_string_name():
    # A malformed URL stored under the list name ["x"] does not excuse the
    # same URL under the string name "['x']".
    stored = {"rules": [_check(["x"], BAD)]}
    with pytest.raises(guardrail_urls.BadGuardrailURL):
        guardrail_urls.reject_changed_bad_guardrail_urls(
            {"rules": [_check("['x']", BAD)]}, stored
        )
    stored_channels = {"channels": [_channel(["x"], BAD)]}
    with pytest.raises(guardrail_urls.BadAlertingURL):
        guardrail_urls.reject_changed_bad_alerting_urls(
            {"channels": [_channel("['x']", BAD)]}, stored_channels
        )


@pytest.mark.parametrize("name", NAMES, ids=NAME_IDS)
def test_alerting_url_check_takes_a_nonstring_name(name):
    good = {"channels": [_channel(name)]}
    bad = {"channels": [_channel(name, BAD)]}

    guardrail_urls.reject_changed_bad_alerting_urls(good, {})
    guardrail_urls.reject_changed_bad_alerting_urls({}, good)
    with pytest.raises(guardrail_urls.BadAlertingURL) as refused:
        guardrail_urls.reject_changed_bad_alerting_urls(bad, {})
    assert refused.value.param == "alerting.channels[0].url"
    guardrail_urls.reject_changed_bad_alerting_urls(bad, bad)


@pytest.mark.parametrize("name", NAMES, ids=NAME_IDS)
def test_policy_url_check_takes_a_nonstring_name(name):
    guardrail_urls.reject_changed_bad_policy_check_urls([_check(name)], None)
    guardrail_urls.reject_changed_bad_policy_check_urls([], [_check(name)])
    with pytest.raises(guardrail_urls.BadGuardrailURL) as refused:
        guardrail_urls.reject_changed_bad_policy_check_urls([_check(name, BAD)], [])
    assert refused.value.param == "checks[0].config.url"
    guardrail_urls.reject_changed_bad_policy_check_urls(
        [_check(name, BAD)], [_check(name, BAD)]
    )


# ----------------------------------------------------------------------------
# Where a name enters, one that is not a string is refused naming the field.
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", [*NAMES, 7, None], ids=[*NAME_IDS, "int", "null"])
@pytest.mark.parametrize("shape", ["rules", "checks"])
def test_write_serializer_refuses_a_nonstring_check_name(name, shape):
    s = AgentccOrgConfigWriteSerializer(data={"guardrails": {shape: [_check(name)]}})

    assert not s.is_valid()
    assert f"guardrails.{shape}[0].name must be a string" in str(s.errors["guardrails"])


@pytest.mark.parametrize("name", [*NAMES, 7, None], ids=[*NAME_IDS, "int", "null"])
def test_policy_serializer_refuses_a_nonstring_check_name(name):
    s = AgentccGuardrailPolicySerializer(
        data={"name": "p", "checks": [_check("ok"), _check(name)]}
    )

    assert not s.is_valid()
    assert "checks[1].name must be a string" in str(s.errors["checks"])


def test_string_names_still_save():
    assert AgentccOrgConfigWriteSerializer(
        data={
            "guardrails": {"rules": [_check("my-hook")]},
            "alerting": {"channels": [_channel("ops")]},
        }
    ).is_valid()
    assert AgentccGuardrailPolicySerializer(
        data={"name": "p", "checks": [_check("my-hook")]}
    ).is_valid()


@pytest.mark.integration
@pytest.mark.parametrize("name", NAMES, ids=NAME_IDS)
@pytest.mark.parametrize("shape", ["rules", "checks"])
def test_validate_org_config_refuses_a_nonstring_check_name(organization, name, shape):
    config = AgentccOrgConfig(
        organization=organization,
        version=9,
        is_active=False,
        guardrails={shape: [_check("ok"), _check(name)]},
    )

    for check_guardrails in (True, False):
        with pytest.raises(OrgConfigRejected) as refused:
            validate_org_config(config, check_guardrails=check_guardrails)
        assert f"guardrails.{shape}[1].name must be a string" in str(refused.value)


# ----------------------------------------------------------------------------
# Every save path, end to end: 400 naming the field, nothing stored or pushed.
# ----------------------------------------------------------------------------


# (section, key, the field the 400 names). An alert channel's name is refused
# by the gateway contract, which names it in its own form.
SHAPES = [
    ("guardrails", "rules", "guardrails.rules[0].name"),
    ("guardrails", "checks", "guardrails.checks[0].name"),
    ("alerting", "channels", "alerting.channels.0.name"),
]
SHAPE_IDS = ["rules", "checks-list", "alert-channel"]


@pytest.mark.integration
@pytest.mark.api
class TestSavesRefuseANonstringName:
    @pytest.mark.parametrize("name", NAMES, ids=NAME_IDS)
    @pytest.mark.parametrize("section,key,field", SHAPES, ids=SHAPE_IDS)
    def test_update_config(self, auth_client, organization, name, section, key, field):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        entry = _channel(name) if section == "alerting" else _check(name)
        payload = {section: {key: [entry]}}

        with _gateway() as gateway:
            response = auth_client.post(UPDATE_CONFIG, payload, format="json")

        _assert_refused_naming(response, field)
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("name", NAMES, ids=NAME_IDS)
    @pytest.mark.parametrize("section,key,field", SHAPES, ids=SHAPE_IDS)
    def test_post_org_configs(
        self, auth_client, organization, name, section, key, field
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        entry = _channel(name) if section == "alerting" else _check(name)
        payload = {section: {key: [entry]}}

        with _gateway() as gateway:
            response = auth_client.post(ORG_CONFIGS, payload, format="json")

        _assert_refused_naming(response, field)
        assert _versions(organization) == [(1, True)]
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization
        ).exists()
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("name", NAMES, ids=NAME_IDS)
    def test_policy_create(self, auth_client, organization, name):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )

        with _gateway() as gateway:
            response = auth_client.post(
                POLICIES, {"name": "p", "checks": [_check(name)]}, format="json"
            )

        _assert_refused_naming(response, "checks[0].name must be a string")
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization, name="p"
        ).exists()
        gateway.set_org_config.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestAPolicyStoredWithANonstringNameCanBeRepaired:
    """A policy stored with a non-string name makes its org's policy syncs
    fail until it is repaired.

    The org here also holds a healthy policy, so each repair pushes that
    policy's check; removing an org's last policy is covered by
    test_guardrail_section_emptied.py."""

    @pytest.fixture
    def stored(self, organization):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization, name="healthy", checks=[_check("other")]
        )
        return AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="nonstring-name",
            checks=[_check(["x"]), _check("kept")],
        )

    def test_its_syncs_fail_until_it_is_repaired(self, auth_client, stored):
        with _gateway() as gateway:
            response = auth_client.post(f"{POLICIES}sync/", {}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is False
        gateway.set_org_config.assert_not_called()

    def test_renaming_the_check_saves_and_syncs(
        self, auth_client, organization, stored
    ):
        with _gateway() as gateway:
            response = auth_client.patch(
                f"{POLICIES}{stored.id}/",
                {"checks": [_check("renamed"), _check("kept")]},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        stored.refresh_from_db()
        assert [c["name"] for c in stored.checks] == ["renamed", "kept"]
        pushed = gateway.set_org_config.call_args.args[1]
        assert set(pushed["guardrails"]["checks"]) == {"renamed", "kept", "other"}

    def test_editing_it_with_the_name_still_unchanged_is_refused(
        self, auth_client, organization, stored
    ):
        with _gateway() as gateway:
            response = auth_client.patch(
                f"{POLICIES}{stored.id}/",
                {"checks": [_check(["x"]), _check("kept", "https://other.test/x")]},
                format="json",
            )

        _assert_refused_naming(response, "checks[0].name must be a string")
        gateway.set_org_config.assert_not_called()

    def test_deleting_the_policy_syncs(self, auth_client, organization, stored):
        with _gateway() as gateway:
            response = auth_client.delete(f"{POLICIES}{stored.id}/")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        pushed = gateway.set_org_config.call_args.args[1]
        assert set(pushed["guardrails"]["checks"]) == {"other"}

    def test_switching_the_policy_off_syncs(self, auth_client, organization, stored):
        with _gateway() as gateway:
            response = auth_client.patch(
                f"{POLICIES}{stored.id}/", {"is_active": False}, format="json"
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        pushed = gateway.set_org_config.call_args.args[1]
        assert set(pushed["guardrails"]["checks"]) == {"other"}
