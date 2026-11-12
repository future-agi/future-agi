"""Real catalog API policy projection, including an active shared grant."""
import copy

import pytest

from agentcc.tests import test_microsoft_connect_gate as gate
from agentcc.views.connector_catalog import ConnectorAppSerializer

pytestmark = pytest.mark.django_db
rig = gate.rig


def catalog_app(org, user, app_id):
    response = gate.bearer(user).get("/agentcc/connectors/catalog/")
    assert response.status_code == 200
    app = next(row for row in response.json()["results"] if row["id"] == app_id)
    serializer = ConnectorAppSerializer(data=app)
    assert serializer.is_valid(), serializer.errors
    return app


@pytest.mark.parametrize("already_added", [False, True])
def test_gate_off_catalog_keeps_disabled_provider_and_reason(rig, monkeypatch, already_added):
    org, user, config, fake = rig
    if not already_added:
        config.mcp["servers"] = {}
        config.save(update_fields=["mcp"])
    with gate.no_effects(monkeypatch, fake):
        app = catalog_app(org, user, gate.OUTLOOK)
    assert app["status"] == "coming_soon"
    assert app["default_provider"] is None
    assert app["providers"]
    for provider in app["providers"]:
        assert provider["connect"] == {
            "served": False, "state": "unavailable", "reason": gate.CODE,
        }
    assert bool(app["servers"]) is already_added
    assert app["connected"] is False


@pytest.mark.parametrize("app_id,group,enabled", [
    (gate.OUTLOOK, "microsoft", False),
    (gate.OUTLOOK, "microsoft", True),
    ("gmail", "google", False),
])
def test_active_shared_grant_truthful_while_held(
    rig, monkeypatch, settings, app_id, group, enabled,
):
    org, user, config, fake = rig
    gate.registration(org, user, group)
    row = gate.token_row(org, user, server=app_id, shared=True)
    row.oauth_client_id = "fixture-org-client"
    row.save(update_fields=["oauth_client_id"])
    entry = config.mcp["servers"][app_id]
    entry["credential"] = {
        "mode": "shared", "kind": "oauth", "state": "active",
        "credential_id": str(row.pk),
    }
    config.save(update_fields=["mcp"])
    before = copy.deepcopy(config.mcp)
    assert gate.vault.pushes_credential(config.mcp, row)
    settings.MICROSOFT_CONNECT_ENABLED = enabled
    # Gate ON legitimately resolves org registration metadata for the provider
    # card. The OFF projection must not read any secret at all.
    if group == "microsoft" and not enabled:
        with gate.no_effects(monkeypatch, fake):
            app = catalog_app(org, user, app_id)
        server = next(item for item in app["servers"] if item["server_id"] == app_id)
        assert app["status"] == "coming_soon"
        assert app["connected"] is False and server["connected"] is False
        assert server["start"] == {"state": "unavailable", "reason": gate.CODE}
        assert server["start_url"] is None
        assert server["enabled"] is True and server["credential_mode"] == "shared"
    else:
        app = catalog_app(org, user, app_id)
        server = next(item for item in app["servers"] if item["server_id"] == app_id)
        assert app["connected"] is True and server["connected"] is True
        assert "status" not in app
    config.refresh_from_db()
    row.refresh_from_db()
    assert config.mcp == before
    assert row.status == "active" and row.version == 1
