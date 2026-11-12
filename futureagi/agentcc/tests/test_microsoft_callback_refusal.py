"""An in-flight Microsoft client change is a refusal, not a server error."""
from html import unescape
from unittest.mock import Mock

import pytest

from agentcc.tests import test_microsoft_connect_gate as gate

pytestmark = pytest.mark.django_db
rig = gate.rig


@pytest.mark.parametrize("change", ["removed", "replaced", "old-platform"])
def test_inflight_client_change_is_value_free_400(rig, settings, monkeypatch, change):
    org, user, _, fake = rig
    settings.MICROSOFT_CONNECT_ENABLED = True
    registration = gate.registration(org, user)
    started = gate.oauth.start(org, user, gate.OUTLOOK, individual_authenticated=True)
    code, state = fake.authorize(started.authorize_url)
    if change == "removed":
        registration.status = "revoked"
        registration.save(update_fields=["status"])
    elif change == "replaced":
        registration.oauth_client_id = "fixture-replaced-client"
        registration.save(update_fields=["oauth_client_id"])
    else:
        gate.State.objects.filter(pk=started.connect_id).update(
            client_id=gate.PLATFORM_ID, client_kind="platform",
        )
    spies = []
    for module, name in [
        (gate.clients, "_org_secret"), (gate.clients, "_env_client"),
        (gate.clients, "_clients_file"), (gate.oauth, "decrypt_token"),
        (gate.oauth, "encrypt_token"), (gate.selection, "post"),
    ]:
        spy = Mock(wraps=getattr(module, name))
        monkeypatch.setattr(module, name, spy)
        spies.append(spy)
    before = len(fake.requests)
    browser = gate.Client()
    browser.cookies[started.cookie_name] = started.cookie_value
    response = browser.get("/agentcc/mcp/connect/callback/", {
        "state": state, "code": code, "iss": gate.ISSUER,
    })
    assert response.status_code == 400
    text = unescape(response.content.decode())
    assert "The organization's OAuth app changed while you were signing in." in text
    assert gate.PLATFORM_ID not in text and gate.SECRET not in text
    assert "fixture-org-client" not in text
    assert response["Cache-Control"] == "no-store"
    assert not gate.State.objects.filter(pk=started.connect_id).exists()
    assert not gate.Credential.objects.filter(organization=org, kind="oauth").exists()
    assert len(fake.requests) == before
    assert all(spy.call_count == 0 for spy in spies)


@pytest.mark.parametrize("error_type", [
    gate.clients.OrgClientRequired, gate.clients.OrgClientUnreadable,
    gate.clients.PlatformClientMisconfigured,
])
def test_exchange_maps_each_client_resolution_error(rig, settings, monkeypatch, error_type):
    org, user, _, fake = rig
    settings.MICROSOFT_CONNECT_ENABLED = True
    gate.registration(org, user)
    started = gate.oauth.start(org, user, gate.OUTLOOK, individual_authenticated=True)
    row = gate.State.objects.get(pk=started.connect_id)
    # Fault injection at the resolver, not an authority bypass.
    monkeypatch.setattr(gate.selection, "issuing", Mock(side_effect=error_type("fixture")))
    with gate.no_effects(monkeypatch, fake):
        assert gate.oauth._exchange_code(row, "fixture-code", org) == (None, "client_changed")
