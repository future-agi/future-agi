"""The changed-only URL checks never lock a save out because two alert
channels, or two guardrail checks, share a name.

``reject_changed_bad_alerting_urls`` and ``reject_changed_bad_guardrail_urls``
let a malformed URL stand when the same name already holds it, so a save that
does not change it is not refused. Each name holds every URL stored under it:
with two same-named entries (the console does not stop it), an unchanged
malformed URL of either one (as stored)
never reads as new, so a gateway-view save of the org, one that touches
neither alerting nor guardrails included, is not answered 400.

A new malformed URL is still refused, under a shared name too, naming its own
field, and another name never vouches for one.

Test values only.
"""

import copy
import uuid

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import guardrail_urls
from agentcc.tests import test_org_config_secrets as base
from agentcc.tests.test_org_config_secrets import (  # noqa: F401 (fixtures)
    GATEWAY,
    _active,
    _pushed,
    _stop_clients,
)

gateway = base.gateway

STORED_BAD = "ftp://stored.dupname.example/alerts"
GOOD = "https://hooks.dupname.example/alerts"
NEW_BAD = "http:///no-host"


def _two_ops(first, second):
    return {
        "enabled": True,
        "channels": [
            {"name": "ops", "type": "webhook", "url": first},
            {"name": "ops", "type": "webhook", "url": second},
        ],
    }


def _two_hooks(first, second, key="rules"):
    return {
        key: [
            {"name": "h", "action": "log", "config": {"url": first}},
            {"name": "h", "action": "log", "config": {"url": second}},
        ]
    }


class TestAlerting:
    @pytest.mark.parametrize(
        "first,second", [(STORED_BAD, GOOD), (GOOD, STORED_BAD)], ids=["first", "last"]
    )
    def test_an_unchanged_stored_bad_url_under_a_shared_name_is_not_refused(
        self, first, second
    ):
        stored = _two_ops(first, second)
        guardrail_urls.reject_changed_bad_alerting_urls(copy.deepcopy(stored), stored)

    def test_a_new_bad_url_under_a_shared_name_is_still_refused(self):
        stored = _two_ops(STORED_BAD, GOOD)
        with pytest.raises(guardrail_urls.BadAlertingURL) as refused:
            guardrail_urls.reject_changed_bad_alerting_urls(
                _two_ops(STORED_BAD, NEW_BAD), stored
            )
        assert "alerting.channels[1].url" in str(refused.value)

    def test_another_name_does_not_vouch_for_a_bad_url(self):
        stored = _two_ops(STORED_BAD, GOOD)
        sent = copy.deepcopy(stored)
        sent["channels"].append({"name": "pager", "type": "webhook", "url": STORED_BAD})
        with pytest.raises(guardrail_urls.BadAlertingURL) as refused:
            guardrail_urls.reject_changed_bad_alerting_urls(sent, stored)
        assert "alerting.channels[2].url" in str(refused.value)


class TestGuardrails:
    @pytest.mark.parametrize("key", ["rules", "checks"])
    @pytest.mark.parametrize(
        "first,second", [(STORED_BAD, GOOD), (GOOD, STORED_BAD)], ids=["first", "last"]
    )
    def test_an_unchanged_stored_bad_url_under_a_shared_name_is_not_refused(
        self, key, first, second
    ):
        stored = _two_hooks(first, second, key)
        guardrail_urls.reject_changed_bad_guardrail_urls(copy.deepcopy(stored), stored)

    def test_a_new_bad_url_under_a_shared_name_is_still_refused(self):
        stored = _two_hooks(STORED_BAD, GOOD)
        with pytest.raises(guardrail_urls.BadGuardrailURL) as refused:
            guardrail_urls.reject_changed_bad_guardrail_urls(
                _two_hooks(STORED_BAD, NEW_BAD), stored
            )
        assert "guardrails.rules[1].config.url" in str(refused.value)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "action,body",
    [
        ("update-config", {"cache": {"enabled": True}}),
        (
            "set-budget",
            {"level": "org_limit", "config": {"limit": 10, "on_exceed": "warn"}},
        ),
    ],
    ids=["update-config-cache", "set-budget"],
)
def test_a_stored_row_with_a_bad_url_under_a_shared_channel_name_still_saves(
    organization, auth_client, gateway, action, body
):
    # A stored version holding two "ops" channels, the first with a malformed URL.
    alerting = _two_ops(STORED_BAD, GOOD)
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        alerting=copy.deepcopy(alerting),
        change_description=f"stored {uuid.uuid4().hex[:6]}",
    )
    response = auth_client.post(f"{GATEWAY}/{action}/", body, format="json")
    assert response.status_code == 200, response.content[:600]
    assert _active(organization).alerting == alerting
    assert [c["url"] for c in _pushed(gateway)[-1]["alerting"]["channels"]] == [
        STORED_BAD,
        GOOD,
    ]
