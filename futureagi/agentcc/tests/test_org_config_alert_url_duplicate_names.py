"""Two alert channels may share a name, and each keeps its own credential URL.

The console does not stop duplicate names (the Alerting tab's add-channel
only trims the name), and the stored ciphertexts of two such channels differ
whenever their URLs do, or when each was encrypted by its own save (Fernet
uses a random IV). Migration 0051 stores one blob per org and URL, so a
version with two same-named channels holding different credential URLs is in
that state the moment 0051 has run.

Every gateway-view save of such an org (update-config, set- and
remove-budget, toggle- and update-guardrail, update- and remove-mcp-server,
update-mcp-guardrails) saves, keeps each stored ciphertext verbatim and pushes
each channel's own URL; a blank sent for one channel keeps that channel's own
URL.

Test values only, not real credentials.
"""

import importlib
import json
import uuid

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push, org_config_secrets
from agentcc.tests import test_org_config_secrets as base
from agentcc.tests.test_org_config_secrets import (  # noqa: F401 (fixtures)
    GATEWAY,
    ORG_CONFIGS,
    _active,
    _ok,
    _pushed,
    _result,
    _save,
    _stop_clients,
)

# Every push goes to a mock that records what it got; nothing is sent.
gateway = base.gateway


def _migration_0051():
    return importlib.import_module("agentcc.migrations.0051_encrypt_alert_channel_urls")


MCP_SERVER = {"transport": "http", "url": "https://mcp.dup.example/docs/mcp"}

# Every gateway-view write that saves through _update_org_config, none of
# which touches alerting.
UNRELATED_SAVES = {
    "update-config-cache": ("update-config", {"cache": {"enabled": True}}),
    "set-budget": (
        "set-budget",
        {"level": "org_limit", "config": {"limit": 100, "on_exceed": "warn"}},
    ),
    "remove-budget": ("remove-budget", {"level": "org_limit"}),
    "toggle-guardrail": (
        "toggle-guardrail",
        {"name": "pii-detector", "enabled": False},
    ),
    "update-guardrail": (
        "update-guardrail",
        {"name": "pii-detector", "config": {"enabled": False, "action": "log"}},
    ),
    "update-mcp-server": (
        "update-mcp-server",
        {
            "server_id": "search",
            "config": {
                "transport": "http",
                "url": "https://mcp.dup.example/search/mcp",
            },
        },
    ),
    "remove-mcp-server": ("remove-mcp-server", {"server_id": "docs"}),
    "update-mcp-guardrails": ("update-mcp-guardrails", {"config": {"enabled": True}}),
}


def _slack(tag, n):
    return f"https://hooks.slack.com/services/T0DUP/B0DUP/{tag}Ops{n}Secret"


class _Apps:
    def get_model(self, app, model):
        return AgentccOrgConfig


def _stored(organization):
    return _active(organization).alerting["channels"]


def _decrypted_once(organization):
    """Each stored channel URL decrypted one layer, as the push reads it."""
    return [
        org_config_secrets.clear_url(c.get("url")) if c.get("url") else c.get("url")
        for c in _stored(organization)
    ]


def _pushed_urls(gateway):
    return [c.get("url") for c in _pushed(gateway)[-1]["alerting"]["channels"]]


def _built_urls(organization):
    payload = config_push._build_payload(str(organization.id), _active(organization))
    return [c.get("url") for c in payload["alerting"]["channels"]]


def _read_channels(auth_client):
    body = _result(_ok(auth_client.get(f"{ORG_CONFIGS}active/")))
    return body["alerting"]["channels"]


def _seed_by_update_config(auth_client, channels):
    _ok(auth_client.post(ORG_CONFIGS, {"change_description": "dup v1"}, format="json"))
    _ok(
        _save(
            auth_client,
            {
                "alerting": {"enabled": True, "channels": channels},
                "mcp": {"enabled": True, "servers": {"docs": MCP_SERVER}},
            },
        )
    )


@pytest.fixture
def two_ops(organization, auth_client, gateway):
    """Two slack channels named "ops" with different credential URLs, saved
    the way the Alerting tab saves (update-config)."""
    tag = uuid.uuid4().hex[:10]
    urls = [_slack(tag, 1), _slack(tag, 2)]
    _seed_by_update_config(
        auth_client,
        [
            {"name": "ops", "type": "slack", "url": urls[0]},
            {"name": "ops", "type": "slack", "url": urls[1]},
        ],
    )
    return urls


def _unrelated_save(auth_client, which):
    action, body = UNRELATED_SAVES[which]
    return auth_client.post(f"{GATEWAY}/{action}/", body, format="json")


def _assert_saved_and_both_kept(response, organization, gateway, urls, before):
    assert response.status_code == 200, response.content[:600]
    assert "must be an absolute http" not in response.content.decode()
    assert _decrypted_once(organization) == urls
    # Unchanged ciphertext is kept verbatim, never wrapped again.
    assert [c["url"] for c in _stored(organization)] == before
    assert _pushed_urls(gateway) == urls
    assert _built_urls(organization) == urls


@pytest.mark.integration
@pytest.mark.api
class TestAnUnrelatedSaveIsNeverLockedOut:
    @pytest.mark.parametrize("which", list(UNRELATED_SAVES))
    def test_two_same_named_channels_with_different_urls(
        self, organization, auth_client, gateway, two_ops, which
    ):
        before = [c["url"] for c in _stored(organization)]
        assert all(url.startswith("enc::") for url in before)
        assert _decrypted_once(organization) == two_ops

        response = _unrelated_save(auth_client, which)

        _assert_saved_and_both_kept(response, organization, gateway, two_ops, before)

    def test_the_same_url_encrypted_by_two_saves(
        self, organization, auth_client, gateway
    ):
        # One URL, two blobs: Fernet's random IV makes each save's ciphertext
        # its own.
        url = _slack(uuid.uuid4().hex[:10], 1)
        _seed_by_update_config(
            auth_client, [{"name": "ops", "type": "slack", "url": url}]
        )
        channels = _read_channels(auth_client) + [
            {"name": "ops", "type": "slack", "url": url}
        ]
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        before = [c["url"] for c in _stored(organization)]
        assert before[0] != before[1]

        response = _unrelated_save(auth_client, "set-budget")

        _assert_saved_and_both_kept(response, organization, gateway, [url, url], before)

    @pytest.mark.parametrize("which", ["set-budget", "update-config-cache"])
    def test_a_stored_row_after_migration_0051(
        self, organization, auth_client, gateway, which
    ):
        # A version written directly with two same-named channels' plain URLs,
        # then 0051, with no user action.
        tag = uuid.uuid4().hex[:10]
        urls = [_slack(tag, 1), _slack(tag, 2)]
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            alerting={
                "enabled": True,
                "channels": [
                    {"name": "ops", "type": "slack", "url": urls[0]},
                    {"name": "ops", "type": "slack", "url": urls[1]},
                ],
            },
        )
        _migration_0051().forwards(_Apps(), None)
        before = [c["url"] for c in _stored(organization)]
        assert all(url.startswith("enc::") for url in before)

        response = _unrelated_save(auth_client, which)

        _assert_saved_and_both_kept(response, organization, gateway, urls, before)

    def test_an_unreadable_second_url_is_kept_as_stored_and_left_out_of_the_push(
        self, organization, auth_client, gateway, two_ops
    ):
        # The second channel's URL is encrypted under a key this deployment
        # does not hold (as after an INTEGRATION_ENCRYPTION_KEY rotation).
        unreadable = base._other_key_token(two_ops[1])
        active = _active(organization)
        active.alerting["channels"][1]["url"] = unreadable
        active.save(update_fields=["alerting"])
        before = [c["url"] for c in _stored(organization)]

        response = _unrelated_save(auth_client, "set-budget")

        assert response.status_code == 200, response.content[:600]
        assert [c["url"] for c in _stored(organization)] == before
        assert _pushed_urls(gateway) == [two_ops[0], None]


@pytest.mark.integration
@pytest.mark.api
class TestEachSameNamedChannelKeepsItsOwnURL:
    def test_the_alerting_tab_save_with_masks(
        self, organization, auth_client, gateway, two_ops
    ):
        channels = _read_channels(auth_client)
        assert len({c["url"] for c in channels}) == 2
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        assert _decrypted_once(organization) == two_ops
        assert _pushed_urls(gateway) == two_ops

    def test_a_blank_on_the_second_keeps_the_seconds_url(
        self, organization, auth_client, gateway, two_ops
    ):
        channels = _read_channels(auth_client)
        channels[1]["url"] = ""
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        assert _decrypted_once(organization) == two_ops
        assert _pushed_urls(gateway) == two_ops

    def test_blanks_on_both_keep_each_its_own(
        self, organization, auth_client, gateway, two_ops
    ):
        channels = _read_channels(auth_client)
        for channel in channels:
            channel["url"] = ""
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        assert _decrypted_once(organization) == two_ops
        assert _pushed_urls(gateway) == two_ops

    def test_blanks_after_a_channel_before_them_was_deleted(
        self, organization, auth_client, gateway
    ):
        # Monitoring's delete sends the channels as read minus one, so both
        # "ops" channels move up one index; sent blank, the first "ops" keeps
        # the first "ops" URL and the second the second, never the removed
        # channel's or each other's.
        tag = uuid.uuid4().hex[:10]
        urls = [_slack(tag, 1), _slack(tag, 2)]
        removed = _slack(tag, 0)
        _seed_by_update_config(
            auth_client,
            [
                {"name": "pager", "type": "slack", "url": removed},
                {"name": "ops", "type": "slack", "url": urls[0]},
                {"name": "ops", "type": "slack", "url": urls[1]},
            ],
        )
        channels = _read_channels(auth_client)[1:]
        for channel in channels:
            channel["url"] = ""
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        assert _decrypted_once(organization) == urls
        assert _pushed_urls(gateway) == urls
        assert removed not in json.dumps(_pushed(gateway)[-1])

    def test_a_blank_on_a_channel_that_had_a_plain_url_keeps_nothing(
        self, organization, auth_client, gateway
    ):
        # The first "ops" holds a plain (readable) URL, so it is not a stored
        # secret: a blank there clears it and never takes the
        # second "ops" channel's credential URL.
        tag = uuid.uuid4().hex[:10]
        secret = _slack(tag, 2)
        _seed_by_update_config(
            auth_client,
            [
                {
                    "name": "ops",
                    "type": "webhook",
                    "url": "https://hooks.dup.example/alerts",
                },
                {"name": "ops", "type": "slack", "url": secret},
            ],
        )
        channels = _read_channels(auth_client)
        channels[0]["url"] = ""
        _ok(_save(auth_client, {"alerting": {"channels": channels}}))
        assert _decrypted_once(organization) == ["", secret]
        assert _pushed_urls(gateway)[1] == secret
        assert _pushed_urls(gateway)[0] in ("", None)

    def test_a_name_keyed_channel_map_is_unchanged(
        self, organization, auth_client, gateway
    ):
        # A name-keyed map cannot hold two channels of one name; its blank
        # keeps the URL stored under the same key.
        tag = uuid.uuid4().hex[:10]
        url = _slack(tag, 1)
        _ok(auth_client.post(ORG_CONFIGS, {"change_description": "v1"}, format="json"))
        _ok(
            _save(
                auth_client,
                {"alerting": {"channels": {"ops": {"type": "slack", "url": url}}}},
            )
        )
        _ok(
            _save(
                auth_client,
                {"alerting": {"channels": {"ops": {"type": "slack", "url": ""}}}},
            )
        )
        stored = _active(organization).alerting["channels"]["ops"]["url"]
        assert org_config_secrets.clear_url(stored) == url
