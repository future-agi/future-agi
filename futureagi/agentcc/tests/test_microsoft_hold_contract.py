"""Microsoft's hold answers the gateway with the contracts' Refusal: HTTP 409,
error microsoft_coming_soon and the sentence the caller's refusal carries
(``message`` of ``$defs/Refusal`` in mcp-upstream-token-v1 and
mcp-vendor-credential-v1), so an agent's tool call says what the hold says.

Real org, member, config, vault and binding rows; only outbound transport is
synthetic. Red before the contracts carried the hold: neither Refusal allowed
its error or a message, and neither golden had a hold.
"""

import json
from pathlib import Path

import pytest
from django.test import Client
from jsonschema import Draft202012Validator

from agentcc.services import microsoft_connect_gate as microsoft
from agentcc.tests import test_microsoft_connect_gate as gate
from agentcc.tests import test_microsoft_hold_existing as hold

pytestmark = pytest.mark.django_db
rig = gate.rig

EXAMPLES = Path(__file__).resolve().parents[3] / "api_contracts/gateway/examples"
TOKEN = "mcp-upstream-token-v1"
VENDOR = "mcp-vendor-credential-v1"


def refusal(contract):
    schema = json.loads((EXAMPLES / f"{contract}.schema.json").read_text())
    return Draft202012Validator({"$ref": "#/$defs/Refusal", "$defs": schema["$defs"]})


def golden_hold(contract):
    return json.loads((EXAMPLES / f"{contract}.json").read_text())["hold"]


def test_a_plain_hold_is_the_token_contracts_refusal(rig):
    """A grant from our own (held) platform client on a catalogue Microsoft
    server: the plain hold, with its one sentence."""
    org, user, config, _ = rig
    url = config.mcp["servers"][gate.OUTLOOK]["url"]
    hold.grant(org, user, gate.OUTLOOK, url, oauth_client_id=gate.PLATFORM_ID)
    response = hold.fetch_over_http(org, user, gate.OUTLOOK)
    assert response.status_code == 409
    refusal(TOKEN).validate(response.json())
    assert response.json() == {"error": gate.CODE, "message": gate.MESSAGE}


def test_a_stopped_grant_is_the_token_contracts_refusal_and_its_golden(rig):
    """A grant with no recorded issuing client from a Microsoft host: the
    hold says it stopped working and how to remove it, as the golden does."""
    org, user, config, _ = rig
    hold.own_server(config, "mail", hold.MICROSOFT_URL)
    hold.legacy_grant(org, user)
    response = hold.fetch_over_http(org, user, "mail")
    assert response.status_code == 409
    refusal(TOKEN).validate(response.json())
    assert response.json() == golden_hold(TOKEN)
    assert response.json()["message"] == microsoft.GRANT_MESSAGE


def test_a_held_binding_is_the_vendor_contracts_refusal_and_its_golden(rig):
    """A partner binding set up before the hold, which it now stops: the
    vendor credential answer says so, as the golden does."""
    org, user, config, _ = rig
    binding = hold.partner_binding(org, config, vendor="composio", kind="oauth_client")
    hold.member_connection(org, user, binding)
    response = Client().post(
        hold.VENDOR_PATH,
        json.dumps(hold.vendor_body(org, user, binding)),
        content_type="application/json",
        HTTP_X_AGENTCC_MCP_INTERNAL=gate.INTERNAL_SECRET,
    )
    assert response.status_code == 409
    refusal(VENDOR).validate(response.json())
    assert response.json() == golden_hold(VENDOR)
    assert response.json()["message"] == microsoft.BINDING_MESSAGE


@pytest.mark.parametrize("contract", [TOKEN, VENDOR])
@pytest.mark.parametrize("name", ["MESSAGE", "GRANT_MESSAGE", "BINDING_MESSAGE"])
def test_each_hold_sentence_fits_the_contracts_bound(contract, name):
    """Every sentence Django puts on a hold is a valid message; the bound the
    gateway cuts a message to (300 characters) refuses a longer or empty one."""
    validator = refusal(contract)
    validator.validate({"error": microsoft.CODE, "message": getattr(microsoft, name)})
    for message in ("", "x" * 301):
        assert not validator.is_valid({"error": microsoft.CODE, "message": message})
