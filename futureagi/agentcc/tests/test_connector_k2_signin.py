"""Sign-in for FutureAGI's own K2 connectors on a hosted runtime (W4b, G14b):
connect, callback, the gateway's token fetch, refresh and Disconnect, each
through the real routes against a loopback authorization server that
implements the endpoints the provider declares (``connector_k2_helpers``).

Gmail and Google Calendar sign in through the organization's own Google app,
Outlook and Teams through its own Microsoft app at the org's tenant, once the
deployment runs its connector runtime (``AGENTCC_CONNECTORS_RUNTIME_URL``).
Each test is one property. Nothing reaches a vendor; every value is a fake.
The registration and refusal properties are in
``test_connector_k2_registration``.
"""

import json
from datetime import timedelta

import pytest
from django.utils import timezone
from structlog.testing import capture_logs

from agentcc.tests import connector_k2_flow as flow
from agentcc.tests.connector_k2_flow import GMAIL, OUTLOOK
from agentcc.tests.connector_k2_helpers import (
    GOOGLE_CLIENT_ID,
    MICROSOFT_CLIENT_ID,
    MICROSOFT_SECRET,
    RUNTIME,
    SECRETS,
    SINGLE_TENANT,
    k2_entry,
)
from agentcc.tests.test_mcp_connections import LIST
from agentcc.tests.test_mcp_personal_boundary import bearer
from agentcc.tests.test_mcp_personal_lifecycle import _person

pytestmark = pytest.mark.django_db

k2_settings = flow.k2_settings
servers = flow.servers
k2_bundle = flow.k2_bundle
runtime = flow.runtime
purges = flow.purges


@pytest.fixture(autouse=True)
def _k2(k2_settings, k2_bundle, purges, runtime):
    """Every test here runs with the connector runtime on."""
    return runtime


def _expire_soon(org, user, server_id):
    row = flow.account(org, user, server_id)
    row.token_expires_at = timezone.now() + timedelta(seconds=30)
    row.save(update_fields=["token_expires_at"])
    return row


# --- Google: connect, callback, token, refresh, revoke ------------------------------


def test_google_signs_in_refreshes_and_revokes_through_the_declared_endpoints(
    auth_client, organization, user, servers, purges
):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    flow.register(organization, user)

    url, page = flow.sign_in(auth_client, servers, GMAIL)
    asked = flow.query(url)
    fetched = flow.fetch(organization, user, GMAIL)
    row = _expire_soon(organization, user, GMAIL)
    refreshed = flow.fetch(organization, user, GMAIL)
    gone = auth_client.delete(f"/agentcc/mcp/connect/{GMAIL}/")

    assert url.startswith(servers.origin + "/google/o/oauth2/v2/auth?")
    assert asked["client_id"] == GOOGLE_CLIENT_ID
    assert asked["code_challenge_method"] == "S256"
    assert (asked["access_type"], asked["prompt"]) == ("offline", "consent")
    assert "resource" not in asked
    assert "https://www.googleapis.com/auth/gmail.readonly" in asked["scope"].split()
    assert page.status_code == 200
    assert row.origin == RUNTIME
    assert row.oauth_client_id == GOOGLE_CLIENT_ID
    assert fetched["access_token"] == servers.issued[0]["access_token"]
    assert refreshed["access_token"] == servers.issued[1]["access_token"]
    assert servers.refreshed == ["google"]
    posts = [r for r in servers.requests if r[0] == "POST"]
    assert [(r[2], r[4], r[5]) for r in posts] == [
        ("token", "post", True),
        ("token", "post", True),
        ("revoke", "post", True),
    ]
    assert all("resource" not in r[3] for r in posts)
    assert gone.status_code == 200
    assert servers.revoked == [servers.issued[0]["refresh_token"]]
    assert flow.account(organization, user, GMAIL).status == "revoked"
    assert (str(organization.pk), GMAIL, str(user.pk)) in purges


def test_google_calendar_shares_the_google_registration(
    auth_client, organization, user, servers
):
    calendar = "google-calendar"
    flow.seed(organization, **{calendar: k2_entry(calendar)})
    flow.register(organization, user)

    url, page = flow.sign_in(auth_client, servers, calendar)

    assert flow.query(url)["client_id"] == GOOGLE_CLIENT_ID
    assert "calendar" in flow.query(url)["scope"]
    assert page.status_code == 200
    assert "access_token" in flow.fetch(organization, user, calendar)


@pytest.fixture
def microsoft_connect_on(settings):
    """Microsoft connect switched on, as a deployment that lifts the hold.

    It is off by default (owner hold); the hold itself is covered by
    test_microsoft_connect_gate.py and test_microsoft_coming_soon_render.py.
    """
    settings.MICROSOFT_CONNECT_ENABLED = True


# --- Microsoft: the org's tenant, rotating refresh, no revocation endpoint --------------


def test_microsoft_signs_in_at_the_orgs_tenant_and_records_no_revocation(
    auth_client, organization, user, servers, microsoft_connect_on
):
    entry = k2_entry(OUTLOOK, url_variables={"tenant": SINGLE_TENANT})
    flow.seed(organization, **{OUTLOOK: entry})
    flow.register(
        organization, user, "microsoft", MICROSOFT_CLIENT_ID, MICROSOFT_SECRET
    )

    url, page = flow.sign_in(auth_client, servers, OUTLOOK)
    _expire_soon(organization, user, OUTLOOK)
    refreshed = flow.fetch(organization, user, OUTLOOK)
    gone = auth_client.delete(f"/agentcc/mcp/connect/{OUTLOOK}/")

    assert url.startswith(
        f"{servers.origin}/microsoft/{SINGLE_TENANT}/oauth2/v2.0/authorize?"
    )
    assert "offline_access" in flow.query(url)["scope"].split()
    assert page.status_code == 200
    assert set(servers.tenants) == {SINGLE_TENANT}
    assert refreshed["access_token"] == servers.issued[1]["access_token"]
    assert gone.status_code == 200
    assert [r for r in servers.requests if r[2] == "revoke"] == []
    outcome = flow.account(organization, user, OUTLOOK).oauth_revoke_outcome
    assert outcome == "unconfirmed"


def test_microsoft_teams_defaults_to_the_multitenant_endpoint(
    auth_client, organization, user, servers, microsoft_connect_on
):
    teams = "microsoft-teams"
    flow.seed(organization, **{teams: k2_entry(teams)})
    flow.register(
        organization, user, "microsoft", MICROSOFT_CLIENT_ID, MICROSOFT_SECRET
    )

    url, page = flow.sign_in(auth_client, servers, teams)

    assert url.startswith(f"{servers.origin}/microsoft/organizations/oauth2/v2.0/")
    assert page.status_code == 200


# --- Two members, their own tokens -----------------------------------------------------


def test_each_member_is_served_only_their_own_token(
    auth_client, organization, user, workspace, servers
):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    flow.register(organization, user)
    mate = _person(organization, workspace)

    flow.sign_in(auth_client, servers, GMAIL, subject="owner")
    flow.sign_in(bearer(mate), servers, GMAIL, subject="mate")
    mine = flow.fetch(organization, user, GMAIL)["access_token"]
    theirs = flow.fetch(organization, mate, GMAIL)["access_token"]

    assert mine == servers.token_for("owner")[0]["access_token"]
    assert theirs == servers.token_for("mate")[0]["access_token"]
    assert mine != theirs


# --- No secret or token in a log line or a response ------------------------------------------


def test_no_client_secret_or_token_reaches_a_log_or_a_response(
    auth_client, organization, user, servers
):
    flow.seed(organization, gmail=k2_entry(GMAIL))
    flow.register(organization, user)

    with capture_logs() as logs:
        url, page = flow.sign_in(auth_client, servers, GMAIL)
        rows = auth_client.get(LIST)
        gone = auth_client.delete(f"/agentcc/mcp/connect/{GMAIL}/")

    issued = [v for t in servers.issued for k, v in t.items() if k.endswith("_token")]
    text = json.dumps(logs, default=str) + url + page.content.decode()
    text += rows.content.decode() + gone.content.decode()
    assert issued
    assert [s for s in SECRETS + tuple(issued) if s in text] == []
