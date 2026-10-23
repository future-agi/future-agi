"""An alert channel URL that carries a credential is a secret leaf: stored
encrypted, write-only, masked on every read path, kept on a blank or a
read-back mask, and decrypted only for the gateway push.

A Slack incoming-webhook URL is the credential for its channel. So is any
webhook URL with a token in its path or query (a Discord or Mattermost
webhook, a Zapier catch hook, ``?token=``). Readers of Full Config,
``org-configs/``, ``active/`` and the diff see such a URL only as a mask.

Which URLs are secret (``org_config_secrets.alert_url_carries_credential``,
one deterministic rule that errs towards secret): a ``slack`` channel's URL;
any channel URL that does not parse, or has a user or password, a query or a
fragment; and any URL with a path segment that is not a short lowercase word
(``alerts``, ``hooks``, ``on-call``). So ``https://hooks.example.com/alerts``
stays readable, and a path segment with a digit, an upper-case letter, a
``%`` or more than 24 characters makes the URL secret. A false positive is
harmless (the URL is masked and still delivered); the rule exists so the
plain ones stay readable.

Test values only, not real credentials.
"""

import json
import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import org_config_secrets
from agentcc.tests import test_org_config_secrets as base
from agentcc.tests.test_org_config_secrets import (  # noqa: F401 (fixtures)
    ADMIN_TOKEN,
    GATEWAY,
    MASK,
    ORG_CONFIGS,
    _active,
    _at,
    _ok,
    _pushed,
    _result,
    _role_client,
    _save,
    _stop_clients,
)
from tfc.constants.levels import Level

# Every push goes to a mock that records what it got; nothing is sent.
gateway = base.gateway

PLAIN_URL = "https://hooks.t.example/alerts"
OTHER_HOST_URL = "https://attacker.t.example/in"

SECTIONS_READ_BACK = ("cache", "routing", "alerting", "mcp", "a2a", "audit")


def _urls(tag):
    """One distinctive URL per credential shape, keyed by channel name."""
    return {
        "ops-slack": f"https://hooks.slack.com/services/T0TEST/B0TEST/{tag}SlackSecret",
        "discord": f"https://discord.com/api/webhooks/1234567890/{tag}DiscordTok",
        "query": f"https://alerts.t.example/hook?token={tag}q",
        "zapier": f"https://hooks.zapier.com/hooks/catch/123456/{tag[:6]}x9/",
    }


def _channels(urls):
    return [
        {"name": "ops-slack", "type": "slack", "url": urls["ops-slack"]},
        {"name": "discord", "type": "webhook", "url": urls["discord"]},
        {"name": "query", "type": "webhook", "url": urls["query"]},
        {"name": "zapier", "type": "webhook", "url": urls["zapier"]},
        {"name": "plain", "type": "webhook", "url": PLAIN_URL},
        {"name": "logger", "type": "log"},
    ]


SECRET_POINTERS = [f"/alerting/channels/{i}/url" for i in range(4)]
PLAIN_POINTER = "/alerting/channels/4/url"


def _found(text, urls):
    return sorted(name for name, url in urls.items() if url in text)


@pytest.fixture
def saved(organization, auth_client, gateway):
    """v1 with no alerting, then v2 with every channel, saved the way the
    Alerting tab saves (update-config)."""
    urls = _urls(uuid.uuid4().hex[:12])
    _ok(auth_client.post(ORG_CONFIGS, {"change_description": "t v1"}, format="json"))
    _ok(
        _save(auth_client, {"alerting": {"enabled": True, "channels": _channels(urls)}})
    )
    return urls


def _read_active(client):
    return _result(_ok(client.get(f"{ORG_CONFIGS}active/")))


def _editor_save(auth_client, mutate=None):
    """What the Org Config editor does: read active, post its sections back
    (``mutate`` edits the read-back body first)."""
    body = _read_active(auth_client)
    post = {name: body[name] for name in SECTIONS_READ_BACK if name in body}
    if mutate:
        mutate(post)
    post["change_description"] = "t editor save"
    return auth_client.post(ORG_CONFIGS, post, format="json")


def _read_channels(auth_client):
    return _read_active(auth_client)["alerting"]["channels"]


def _pushed_url(gateway, name):
    for channel in _pushed(gateway)[-1]["alerting"]["channels"]:
        if channel.get("name") == name:
            return channel.get("url")
    raise AssertionError(f"channel {name} not pushed")


def _stored_url(organization, name):
    for channel in _active(organization).alerting["channels"]:
        if channel.get("name") == name:
            return channel.get("url")
    raise AssertionError(f"channel {name} not stored")


@pytest.mark.integration
@pytest.mark.api
class TestNoReaderSeesACredentialURL:
    @pytest.mark.parametrize(
        "level", [Level.VIEWER, Level.MEMBER, Level.ADMIN], ids=str
    )
    def test_every_read_path_masks_every_credential_url(
        self, organization, auth_client, gateway, saved, level
    ):
        active = _active(organization)
        v1 = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, version=1, deleted=False
        )
        client = _role_client(organization, level)
        paths = {
            "Full Config": f"{GATEWAY}/config/",
            "org-configs list": ORG_CONFIGS,
            "org-configs active": f"{ORG_CONFIGS}active/",
            "org-configs retrieve": f"{ORG_CONFIGS}{active.id}/",
            "org-configs diff": f"{ORG_CONFIGS}{active.id}/diff/?compare_to={v1.id}",
            "org-configs diff (reverse)": (
                f"{ORG_CONFIGS}{v1.id}/diff/?compare_to={active.id}"
            ),
        }
        for label, path in paths.items():
            text = _ok(client.get(path)).content.decode()
            assert _found(text, saved) == [], label

    def test_the_owner_sees_none_either_and_the_plain_url_stays_readable(
        self, organization, auth_client, gateway, saved
    ):
        for path in (f"{GATEWAY}/config/", ORG_CONFIGS, f"{ORG_CONFIGS}active/"):
            text = _ok(auth_client.get(path)).content.decode()
            assert _found(text, saved) == [], path
            assert PLAIN_URL in text, path

    def test_each_reads_as_a_mask_with_a_set_flag(
        self, organization, auth_client, gateway, saved
    ):
        viewer = _role_client(organization, Level.VIEWER)
        for path in (f"{GATEWAY}/config/", f"{ORG_CONFIGS}active/"):
            body = _result(_ok(viewer.get(path)))
            assert set(SECRET_POINTERS) <= set(body["secrets_set"]), path
            assert PLAIN_POINTER not in body["secrets_set"], path
            for pointer in SECRET_POINTERS:
                assert org_config_secrets.is_mask(_at(body, pointer)), (path, pointer)
            assert _at(body, PLAIN_POINTER) == PLAIN_URL
            assert _at(body, "/alerting/channels/0/type") == "slack"
            assert "url" not in _at(body, "/alerting/channels/5")

    def test_a_name_keyed_channel_map_is_masked_and_pushed_in_clear(
        self, organization, auth_client, gateway
    ):
        # Channels as a name-keyed map, a shape update-config accepts.
        url = f"https://hooks.slack.com/services/T0/B0/{uuid.uuid4().hex}"
        _ok(auth_client.post(ORG_CONFIGS, {"change_description": "v1"}, format="json"))
        _ok(
            _save(
                auth_client,
                {"alerting": {"channels": {"ops": {"type": "slack", "url": url}}}},
            )
        )
        viewer = _role_client(organization, Level.VIEWER)
        body = _result(_ok(viewer.get(f"{ORG_CONFIGS}active/")))
        assert url not in json.dumps(body)
        assert "/alerting/channels/ops/url" in body["secrets_set"]
        assert _pushed_url(gateway, "ops") == url
        # Saved back as read, the map keeps it.
        _ok(_save(auth_client, {"alerting": body["alerting"]}))
        assert _pushed_url(gateway, "ops") == url

    def test_write_responses_carry_none(
        self, organization, auth_client, gateway, saved
    ):
        created = _ok(
            auth_client.post(
                ORG_CONFIGS,
                {"budgets": {"enabled": False}, "change_description": "t v3"},
                format="json",
            )
        )
        assert _found(created.content.decode(), saved) == []
        v2 = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, version=2, deleted=False
        )
        activated = _ok(auth_client.post(f"{ORG_CONFIGS}{v2.id}/activate/"))
        assert _found(activated.content.decode(), saved) == []


@pytest.mark.integration
@pytest.mark.api
class TestStoredEncryptedAndPushedInClear:
    def test_no_version_stores_one_in_clear(
        self, organization, auth_client, gateway, saved
    ):
        for config in AgentccOrgConfig.all_objects.filter(organization=organization):
            assert _found(json.dumps(config.alerting), saved) == [], config.version
        for name in saved:
            assert _stored_url(organization, name).startswith("enc::"), name
        assert _stored_url(organization, "plain") == PLAIN_URL

    def test_the_push_carries_each_in_clear(
        self, organization, auth_client, gateway, saved
    ):
        for name, url in saved.items():
            assert _pushed_url(gateway, name) == url, name
        assert _pushed_url(gateway, "plain") == PLAIN_URL
        sent = json.dumps(_pushed(gateway)[-1]["alerting"])
        assert MASK not in sent and "enc::" not in sent

    def test_the_bulk_sync_carries_each_in_clear(
        self, organization, auth_client, gateway, saved
    ):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
        with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
            body = _ok(client.get(f"{ORG_CONFIGS}bulk/")).json()["result"]
        channels = body[str(organization.id)]["alerting"]["channels"]
        sent = {channel["name"]: channel.get("url") for channel in channels}
        for name, url in saved.items():
            assert sent[name] == url, name


@pytest.mark.integration
@pytest.mark.api
class TestKeepOnBlankAndMask:
    def test_the_editor_save_keeps_every_url_verbatim(
        self, organization, auth_client, gateway, saved
    ):
        before = _active(organization)
        _ok(_editor_save(auth_client))
        after = _active(organization)
        assert after.version == before.version + 1
        for name in saved:
            assert _stored_url(organization, name) == next(
                c["url"] for c in before.alerting["channels"] if c["name"] == name
            ), name
            assert _pushed_url(gateway, name) == saved[name], name
        diff = _result(
            _ok(
                auth_client.get(f"{ORG_CONFIGS}{after.id}/diff/?compare_to={before.id}")
            )
        )
        assert diff["alerting"]["changed"] == {}, diff["alerting"]

    def test_the_alerting_tab_save_with_masks_keeps_every_url(
        self, organization, auth_client, gateway, saved
    ):
        channels = _read_channels(auth_client)
        _ok(_save(auth_client, {"alerting": {"enabled": True, "channels": channels}}))
        for name, url in saved.items():
            assert _pushed_url(gateway, name) == url, name

    def test_monitoring_add_channel_keeps_the_others_and_encrypts_the_new_one(
        self, organization, auth_client, gateway, saved
    ):
        # CreateChannelDialog: the channels as read plus the new one.
        new_url = f"https://hooks.slack.com/services/T0TEST/B0NEW/{uuid.uuid4().hex}"
        channels = _read_channels(auth_client) + [
            {"name": "new-slack", "type": "slack", "url": new_url}
        ]
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        for name, url in saved.items():
            assert _pushed_url(gateway, name) == url, name
        assert _pushed_url(gateway, "new-slack") == new_url
        assert _stored_url(organization, "new-slack").startswith("enc::")
        assert (
            new_url
            not in _ok(auth_client.get(f"{ORG_CONFIGS}active/")).content.decode()
        )

    def test_monitoring_delete_of_the_first_channel_keeps_each_others_own_url(
        self, organization, auth_client, gateway, saved
    ):
        # AlertingMonitoringSection's delete: the channels as read, minus one,
        # so every later channel moves up one index.
        channels = _read_channels(auth_client)[1:]
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        for name in ("discord", "query", "zapier"):
            assert _pushed_url(gateway, name) == saved[name], name
        assert _pushed_url(gateway, "plain") == PLAIN_URL

    def test_a_blank_keeps_the_channels_own_url(
        self, organization, auth_client, gateway, saved
    ):
        channels = _read_channels(auth_client)
        channels[0]["url"] = ""
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        assert _pushed_url(gateway, "ops-slack") == saved["ops-slack"]
        assert _stored_url(organization, "ops-slack").startswith("enc::")

    def test_a_blank_after_the_list_moved_keeps_that_channels_url_not_its_slots(
        self, organization, auth_client, gateway, saved
    ):
        # The first channel is removed and the next one (now at index 0) is
        # sent blank: it keeps its own URL, never the removed channel's.
        channels = _read_channels(auth_client)[1:]
        channels[0]["url"] = ""
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        assert _pushed_url(gateway, "discord") == saved["discord"]

    def test_a_new_url_replaces_and_null_removes(
        self, organization, auth_client, gateway, saved
    ):
        replaced = f"https://hooks.slack.com/services/T0TEST/B0TEST/{uuid.uuid4().hex}"
        channels = _read_channels(auth_client)
        channels[0]["url"] = replaced
        channels[1]["url"] = None
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        assert _pushed_url(gateway, "ops-slack") == replaced
        assert _pushed_url(gateway, "discord") is None
        body = _read_active(auth_client)
        assert "/alerting/channels/1/url" not in body["secrets_set"]
        assert replaced not in json.dumps(body)

    def test_a_clear_row_reads_masked_and_a_mask_save_encrypts_it(
        self, organization, auth_client, gateway
    ):
        # A stored row that 0030 has not encrypted, with a
        # user:password URL the save path refuses.
        tag = uuid.uuid4().hex[:10]
        clear_rows = {
            "ops-slack": f"https://hooks.slack.com/services/T0/B0/{tag}Stored",
            "userinfo": f"https://ops:{tag}@hooks.t.example/alerts",
        }
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            alerting={
                "enabled": True,
                "channels": [
                    {
                        "name": "ops-slack",
                        "type": "slack",
                        "url": clear_rows["ops-slack"],
                    },
                    {
                        "name": "userinfo",
                        "type": "webhook",
                        "url": clear_rows["userinfo"],
                    },
                ],
            },
        )
        viewer = _role_client(organization, Level.VIEWER)
        for path in (f"{GATEWAY}/config/", f"{ORG_CONFIGS}active/"):
            assert _found(_ok(viewer.get(path)).content.decode(), clear_rows) == [], (
                path
            )
        channels = _read_channels(auth_client)
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        for name, url in clear_rows.items():
            assert _pushed_url(gateway, name) == url, name
            assert _stored_url(organization, name).startswith("enc::"), name


@pytest.mark.integration
@pytest.mark.api
class TestTheOriginRuleStillHolds:
    def test_a_header_on_a_token_url_is_kept_by_its_mask(
        self, organization, auth_client, gateway
    ):
        tag = uuid.uuid4().hex[:10]
        url = f"https://alerts.t.example/hook?token={tag}"
        _ok(auth_client.post(ORG_CONFIGS, {"change_description": "v1"}, format="json"))
        _ok(
            _save(
                auth_client,
                {
                    "alerting": {
                        "channels": [
                            {
                                "name": "hook",
                                "type": "webhook",
                                "url": url,
                                "headers": {"X-Hook-Token": f"hdr-{tag}"},
                            }
                        ]
                    }
                },
            )
        )
        channels = _read_channels(auth_client)
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        channel = _pushed(gateway)[-1]["alerting"]["channels"][0]
        assert channel["url"] == url
        assert channel["headers"]["X-Hook-Token"] == f"hdr-{tag}"

        # The same origin with a new token keeps the header.
        channels = _read_channels(auth_client)
        channels[0]["url"] = f"https://alerts.t.example/hook?token={tag}-2"
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        assert (
            _pushed(gateway)[-1]["alerting"]["channels"][0]["headers"]["X-Hook-Token"]
            == f"hdr-{tag}"
        )

        # Another host with the header's mask kept is refused.
        pushes = len(_pushed(gateway))
        channels = _read_channels(auth_client)
        channels[0]["url"] = OTHER_HOST_URL
        response = _save(auth_client, {"alerting": {"channels": channels}})
        assert response.status_code == 400, response.content[:400]
        assert org_config_secrets.SECRET_ORIGIN_CHANGED in response.content.decode()
        assert len(_pushed(gateway)) == pushes

    def test_another_secrets_mask_pasted_as_a_url_is_refused(
        self, organization, auth_client, gateway, saved
    ):
        _ok(
            _save(
                auth_client,
                {
                    "mcp": {
                        "enabled": True,
                        "servers": {
                            "gh": {
                                "transport": "http",
                                "url": "https://mcp.t.example/gh/mcp",
                                "auth": {"type": "bearer", "token": "t-mcp-token"},
                            }
                        },
                    }
                },
            )
        )
        token_mask = _read_active(auth_client)["mcp"]["servers"]["gh"]["auth"]["token"]
        pushes = len(_pushed(gateway))
        for save in ("update-config", "editor"):
            channels = _read_channels(auth_client)
            channels[0]["url"] = token_mask
            if save == "update-config":
                response = _save(auth_client, {"alerting": {"channels": channels}})
            else:
                response = _editor_save(
                    auth_client,
                    lambda post, c=channels: post["alerting"].update(channels=c),
                )
            assert response.status_code == 400, (save, response.content[:400])
            assert "t-mcp-token" not in response.content.decode()
        assert len(_pushed(gateway)) == pushes
        assert _pushed_url(gateway, "ops-slack") == saved["ops-slack"]

    def test_a_url_mask_pasted_into_an_mcp_token_for_another_host_is_refused(
        self, organization, auth_client, gateway, saved
    ):
        slack_mask = _read_channels(auth_client)[0]["url"]
        pushes = len(_pushed(gateway))
        response = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "evil",
                "config": {
                    "transport": "http",
                    "url": "https://attacker.t.example/mcp",
                    "auth": {"type": "bearer", "token": slack_mask},
                },
            },
            format="json",
        )
        assert response.status_code == 400, response.content[:400]
        assert len(_pushed(gateway)) == pushes

    def test_a_url_mask_pasted_into_an_unbound_secret_is_refused(
        self, organization, auth_client, gateway, saved
    ):
        slack_mask = _read_channels(auth_client)[0]["url"]
        response = _save(
            auth_client,
            {"cache": {"s3": {"bucket": "b", "secret_access_key": slack_mask}}},
        )
        assert response.status_code == 400, response.content[:400]


@pytest.mark.integration
@pytest.mark.api
class TestTheURLCheckReadsTheClearValue:
    @pytest.mark.parametrize("path", ["update-config", "org-configs"])
    def test_a_malformed_slack_url_is_still_refused(
        self, organization, auth_client, gateway, path
    ):
        _ok(auth_client.post(ORG_CONFIGS, {"change_description": "v1"}, format="json"))
        alerting = {"channels": [{"name": "s", "type": "slack", "url": "ftp://x"}]}
        if path == "update-config":
            response = _save(auth_client, {"alerting": alerting})
        else:
            response = auth_client.post(
                ORG_CONFIGS, {"alerting": alerting}, format="json"
            )
        assert response.status_code == 400, response.content[:400]
        assert "alerting.channels" in response.content.decode()

    @pytest.mark.parametrize("path", ["update-config", "org-configs"])
    def test_a_new_well_formed_credential_url_saves_on_both_paths(
        self, organization, auth_client, gateway, path
    ):
        _ok(auth_client.post(ORG_CONFIGS, {"change_description": "v1"}, format="json"))
        url = f"https://hooks.slack.com/services/T0/B0/{uuid.uuid4().hex}"
        alerting = {"channels": [{"name": "s", "type": "slack", "url": url}]}
        if path == "update-config":
            _ok(_save(auth_client, {"alerting": alerting}))
        else:
            _ok(auth_client.post(ORG_CONFIGS, {"alerting": alerting}, format="json"))
        assert _pushed_url(gateway, "s") == url
        assert _stored_url(organization, "s").startswith("enc::")


# The rule, case by case: (channel type, URL, carries a credential).
RULE_CASES = [
    ("slack", "https://hooks.slack.com/services/T0/B0/abc", True),
    ("slack", "https://hooks.example.com/alerts", True),  # Slack type: always
    ("webhook", "https://discord.com/api/webhooks/123/AbC", True),
    ("webhook", "https://hooks.zapier.com/hooks/catch/123456/abcdef/", True),
    ("webhook", "https://chat.example.com/hooks/xq4tz8w9e3gfbc1ry", True),
    ("webhook", "https://alerts.example.com/hook?token=abc", True),
    ("webhook", "https://alerts.example.com/hook?", False),  # empty query
    ("webhook", "https://alerts.example.com/hook#frag", True),
    ("webhook", "https://u:p@alerts.example.com/hook", True),
    ("webhook", "https://alerts.example.com/IncomingWebhook", True),
    ("webhook", "https://alerts.example.com/a%2Fb", True),
    ("webhook", "https://n8n.example.com/webhook/0f8fad5b-d9cb-469f-a165", True),
    ("webhook", "https://alerts.example.com/" + "a" * 25, True),
    ("webhook", "https://[bad/hook", True),  # does not parse
    ("webhook", "https://alerts.example.com/alerts", False),
    ("webhook", "https://alerts.example.com/v/on-call/team_ops", False),
    ("webhook", "https://alerts.example.com", False),
    ("webhook", "https://alerts.example.com/", False),
    ("webhook", "http://10.0.0.5:9000/alerts", False),
    (None, "https://alerts.example.com/alerts", False),
    ("log", "", False),
]


@pytest.mark.parametrize("channel_type,url,secret", RULE_CASES)
def test_the_credential_rule(channel_type, url, secret):
    assert org_config_secrets.alert_url_carries_credential(channel_type, url) is secret
