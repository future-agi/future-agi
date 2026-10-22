"""Migration 0051 encrypts every alert channel URL that carries a credential,
across every version, idempotently and reversibly, in bounded batches like
0047 and 0050.

The migration module is imported directly and run against the test database
with a stand-in ``apps``, as the 0047 tests do.
"""

import importlib
from types import SimpleNamespace

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.org_config_secrets import (
    ENCRYPTED_PREFIX,
    _decrypt,
    _encrypt,
    alert_url_carries_credential,
    iter_secret_slots,
)

migration_0050 = importlib.import_module(
    "agentcc.migrations.0050_encrypt_org_config_secret_aliases"
)


class _Lazy:
    """Migration 0051, imported on first use, so a missing module fails each
    test that needs it rather than the whole file."""

    def __getattr__(self, name):
        module = importlib.import_module(
            "agentcc.migrations.0051_encrypt_alert_channel_urls"
        )
        return getattr(module, name)


migration = _Lazy()

SLACK = "https://hooks.slack.com/services/T0MIG/B0MIG/migrationSlack"
DISCORD = "https://discord.com/api/webhooks/42/migrationDiscord"
QUERY = "https://alerts.migration.example/hook?token=migration"
PLAIN = "https://hooks.migration.example/alerts"


class _Apps:
    def get_model(self, app, model):
        return AgentccOrgConfig


def _org():
    from accounts.models import Organization

    return Organization.objects.create(name="alert url migration org").id


def _alerting():
    return {
        "enabled": True,
        "channels": [
            {"name": "slack", "type": "slack", "url": SLACK},
            {"name": "discord", "type": "webhook", "url": DISCORD},
            {
                "name": "query",
                "type": "webhook",
                "url": QUERY,
                "headers": {"X-Tok": "hdr"},
            },
            {"name": "plain", "type": "webhook", "url": PLAIN},
            {"name": "log", "type": "log"},
        ],
    }


def _urls(row):
    return {c["name"]: c.get("url") for c in row.alerting["channels"]}


def _sample_config():
    base = {s: {} for s in ("cache", "a2a", "audit", "alerting", "mcp")}
    base["alerting"] = _alerting()
    return SimpleNamespace(organization_id="org-1", **base)


def test_the_walks_and_the_service_find_the_same_leaves():
    # 0050 walks every other secret leaf and 0051 the credential URLs; with
    # both, no leaf the write path encrypts is left clear at rest.
    config = _sample_config()
    walked = {
        container[key] for container, key in migration_0050._walk_leaves(config)
    } | {container[key] for container, key in migration._walk_url_leaves(config)}
    sections = {
        s: getattr(config, s) for s in ("cache", "a2a", "audit", "alerting", "mcp")
    }
    assert walked == {slot.value for slot in iter_secret_slots(sections)}
    assert {c[k] for c, k in migration._walk_url_leaves(config)} == {
        SLACK,
        DISCORD,
        QUERY,
    }


@pytest.mark.parametrize(
    "channel_type,url",
    [
        ("slack", "https://hooks.example.com/alerts"),
        ("webhook", "https://alerts.example.com/hook?token=a"),
        ("webhook", "https://alerts.example.com/IncomingWebhook"),
        ("webhook", "https://[bad/hook"),
        ("webhook", "https://alerts.example.com/alerts"),
        ("webhook", "https://alerts.example.com/hook?"),
        ("webhook", "https://u:p@alerts.example.com/"),
        (None, "https://alerts.example.com/" + "a" * 25),
    ],
)
def test_the_migration_rule_is_the_services(channel_type, url):
    # The rule is inlined in the migration (so it keeps treating the same
    # URLs the same way); it matches the service's rule.
    assert migration._carries_credential(channel_type, url) is (
        alert_url_carries_credential(channel_type, url)
    )


def test_forwards_encrypts_every_version_idempotently_and_backwards_decrypts(db):
    org = _org()
    v1 = AgentccOrgConfig.no_workspace_objects.create(
        organization_id=org, version=1, is_active=False, alerting=_alerting()
    )
    v2 = AgentccOrgConfig.no_workspace_objects.create(
        organization_id=org, version=2, is_active=True, alerting=_alerting()
    )
    v3 = AgentccOrgConfig.all_objects.create(
        organization_id=org,
        version=3,
        is_active=False,
        deleted=True,
        alerting=_alerting(),
    )

    migration.forwards(_Apps(), None)
    for row in (v1, v2, v3):
        row.refresh_from_db()
        urls = _urls(row)
        for name, clear in (("slack", SLACK), ("discord", DISCORD), ("query", QUERY)):
            assert urls[name].startswith(ENCRYPTED_PREFIX), (row.version, name)
            assert _decrypt(urls[name]) == clear
        assert urls["plain"] == PLAIN
        assert urls["log"] is None
        # Headers are 0047's and 0050's; this migration leaves them as they are.
        assert row.alerting["channels"][2]["headers"] == {"X-Tok": "hdr"}
    # One org, one clear value -> one blob across versions (quiet diffs).
    assert _urls(v1)["slack"] == _urls(v2)["slack"]

    first = _urls(v2)
    migration.forwards(_Apps(), None)
    v2.refresh_from_db()
    assert _urls(v2) == first

    migration.backwards(_Apps(), None)
    for row in (v1, v2, v3):
        row.refresh_from_db()
        assert _urls(row) == {
            "slack": SLACK,
            "discord": DISCORD,
            "query": QUERY,
            "plain": PLAIN,
            "log": None,
        }


def test_a_url_already_encrypted_and_a_name_keyed_channel_map(db):
    already = _encrypt(SLACK)
    row = AgentccOrgConfig.no_workspace_objects.create(
        organization_id=_org(),
        version=1,
        is_active=True,
        alerting={
            "channels": {
                "a": {"type": "slack", "url": already},
                "b": {"type": "webhook", "url": DISCORD},
                "c": {"type": "webhook", "url": PLAIN},
            }
        },
    )
    migration.forwards(_Apps(), None)
    row.refresh_from_db()
    channels = row.alerting["channels"]
    assert channels["a"]["url"] == already
    assert _decrypt(channels["b"]["url"]) == DISCORD
    assert channels["c"]["url"] == PLAIN


def test_the_migration_is_batched_and_not_one_transaction():
    assert migration.Migration.atomic is False
    assert migration.BATCH_SIZE == migration_0050.BATCH_SIZE
    assert migration.Migration.dependencies == [
        ("prism", "0050_encrypt_org_config_secret_aliases")
    ]
