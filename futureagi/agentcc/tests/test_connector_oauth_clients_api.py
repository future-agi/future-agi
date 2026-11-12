"""The organization's own OAuth clients (CN11): the vault rows CN3's resolver
reads, and the admin API that writes them.

An org admin stores the org's own OAuth client for a catalog connector's
vendor, for one MCP server or for a client group, as an ``oauth_client`` row
of M14's vault (scope shared, Django only). Admins can read its client ID; its
secret is write-only, never pushed to the gateway and never in a read, a log
line or an error report.

The API module is on the site (``agentcc/urls.py``) with the connect flow
that reads its rows (M25x's, through CN3's resolver; test_mcp_connect_clients.py
proves a connect signs in through them), so these tests call the app's own
routes. The gateway is faked at its HTTP client, as in M14's tests. Every
value here is a fake.
"""

import copy
import json
import logging
import uuid
from functools import partial
from unittest.mock import MagicMock, patch

import pytest
import sentry_sdk
from django.urls import reverse
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
from sentry_sdk.transport import Transport
from structlog.testing import capture_logs

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import connector_oauth_clients as clients
from agentcc.services import mcp_credentials as vault
from agentcc.services.credential_manager import decrypt_token
from agentcc.tests.test_mcp_credentials import (
    FakeGateway,
    _golden,
    _member_client,
    _org_with_owner,
    faked_probe,
)
from tfc.logging.sentry import SENSITIVE_KEY_SUBSTRINGS, _get_before_send

pytestmark = [pytest.mark.integration]

Kind = AgentccMCPCredential.Kind
Status = AgentccMCPCredential.Status

CLIENTS = "/agentcc/connectors/oauth-clients/"
CREDENTIALS = "/agentcc/mcp/credentials/"
GATEWAY = "/agentcc/gateways/default"
ORG_CONFIGS = "/agentcc/org-configs/"
PROBE = "/agentcc/mcp/probe/"

CLIENT_ID = "fake-cn11-group-client-id"
CLIENT_SECRET = "fake-cn11-group-secret-not-real"
SERVER_CLIENT_ID = "fake-cn11-server-client-id"
SERVER_SECRET = "fake-cn11-server-secret-not-real"
ROTATED_SECRET = "fake-cn11-rotated-secret-not-real"
STATIC_SECRET = "fake-cn11-static-bearer-not-real"
PLATFORM_ID = "fake-cn11-platform-client-id"
PLATFORM_SECRET = "fake-cn11-platform-secret-not-real"
SECRETS = (CLIENT_SECRET, SERVER_SECRET, ROTATED_SECRET, PLATFORM_SECRET)

LINEAR_URL = "https://mcp.linear.example.com/mcp"
GITHUB_URL = "https://mcp.github.example.com/mcp"
TOKEN_URL = {
    "token_url": "https://auth.linear.example.com/oauth/token",
    "revocation_url": None,
}
ALL_REGISTRATIONS = ("preregistered", "cimd", "dcr")


@pytest.fixture
def pushes():
    """Every org config the gateway was sent, as (org_id, payload)."""
    client = MagicMock()
    sent = []
    client.set_org_config.side_effect = lambda org_id, payload: sent.append(
        (org_id, copy.deepcopy(payload))
    )
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield sent


@pytest.fixture(autouse=True)
def _no_platform_clients(monkeypatch):
    """No platform client and no Omega decoy in the environment."""
    import os

    for name in list(os.environ):
        if name.startswith(("AGENTCC_CONNECTOR_CLIENT", "OMEGA_INTEGRATION_")):
            monkeypatch.delenv(name)


def _seed(org, servers=None):
    servers = (
        servers
        if servers is not None
        else {
            "linear-eng": {"url": LINEAR_URL, "transport": "http"},
            "github": {"url": GITHUB_URL, "transport": "http"},
        }
    )
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, mcp={"servers": servers}
    )


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _create(client, expect=201, **body):
    body = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, **body}
    response = client.post(CLIENTS, body, format="json")
    assert response.status_code == expect, response.json()
    return response.json()["result"] if expect == 201 else response.json()


def _row(row_id):
    return AgentccMCPCredential.objects.get(pk=row_id)


def _dump(value):
    return json.dumps(value, default=str)


def _platform_env(monkeypatch, group):
    id_name, secret_name = clients.env_names(group)
    monkeypatch.setenv(id_name, PLATFORM_ID)
    monkeypatch.setenv(secret_name, PLATFORM_SECRET)


# --- The contract's tests -----------------------------------------------------


def test_oauth_client_secret_write_only(
    auth_client, user, workspace, pushes, caplog, gateway_unreachable
):
    """Admins read the client ID; the secret goes in and never comes out, in
    any response, log line or stored config, and members read nothing."""
    org = user.organization
    _seed(org)
    caplog.set_level(logging.DEBUG)
    bodies = []
    with capture_logs() as logs:
        group = _create(auth_client, client_group="linear", label="Eng Linear app")
        server = _create(
            auth_client,
            server_id="linear-eng",
            client_id=SERVER_CLIENT_ID,
            client_secret=SERVER_SECRET,
        )
        bodies += [group, server]
        listed = auth_client.get(CLIENTS)
        detail = auth_client.get(f"{CLIENTS}{group['id']}/")
        rotated = auth_client.post(
            f"{CLIENTS}{group['id']}/rotate/",
            {"client_secret": ROTATED_SECRET},
            format="json",
        )
        bodies += [listed.json(), detail.json(), rotated.json()]
        for read in (
            CREDENTIALS,
            f"{GATEWAY}/config/",
            f"{ORG_CONFIGS}active/",
            ORG_CONFIGS,
        ):
            bodies.append(auth_client.get(read).json())
        revoked = auth_client.delete(f"{CLIENTS}{server['id']}/")
        bodies.append(revoked.json())

    assert (listed.status_code, detail.status_code) == (200, 200)
    assert (rotated.status_code, revoked.status_code) == (200, 200)
    # Admins read the client ID, the key and the label; the secret is only "set".
    assert {k: group[k] for k in ("client_group", "server_id", "client_id")} == {
        "client_group": "linear",
        "server_id": None,
        "client_id": CLIENT_ID,
    }
    assert (group["label"], group["has_secret"], group["status"]) == (
        "Eng Linear app",
        True,
        "active",
    )
    assert (server["server_id"], server["client_group"]) == ("linear-eng", None)
    assert detail.json()["result"]["client_id"] == CLIENT_ID
    assert sorted(c["client_id"] for c in listed.json()["results"]) == sorted(
        [CLIENT_ID, SERVER_CLIENT_ID]
    )
    assert rotated.json()["result"]["version"] == 2
    assert revoked.json()["result"]["status"] == "revoked"
    for body in (group, server, detail.json()["result"]):
        assert "client_secret" not in body and "secret" not in body
    # No secret anywhere a read, a log line or a config version reaches.
    rendered = _dump(bodies)
    assert [s for s in SECRETS if s in rendered] == []
    assert [s for s in SECRETS if s in _dump(logs)] == []
    assert [s for s in SECRETS if s in caplog.text] == []
    stored = _dump(
        list(
            AgentccOrgConfig._base_manager.filter(organization=org).values_list(
                "mcp", flat=True
            )
        )
    )
    assert [s for s in SECRETS if s in stored] == []
    # Org clients are Django's alone: no write of one pushes anything.
    assert pushes == []
    # The vault holds them encrypted, so the checks above could have seen them.
    group_row, server_row = _row(group["id"]), _row(server["id"])
    assert group_row.encrypted_secret.startswith("enc::")
    assert decrypt_token(group_row.encrypted_secret) == ROTATED_SECRET
    assert decrypt_token(server_row.encrypted_secret) == SERVER_SECRET
    assert (group_row.kind, group_row.scope, group_row.user_id) == (
        Kind.OAUTH_CLIENT,
        "shared",
        None,
    )
    assert (group_row.server_id, server_row.server_id) == (
        "group:linear",
        "server:linear-eng",
    )
    # M14's credential list, which members read, does not carry org clients.
    credentials = auth_client.get(CREDENTIALS).json()["results"]
    assert credentials == []

    # Members read nothing of them, the client ID included, and write nothing.
    member = _member_client(org, workspace)
    before = list(
        AgentccMCPCredential.objects.filter(organization=org).values_list(
            "pk", "status", "version"
        )
    )
    calls = [
        member.get(CLIENTS),
        member.get(f"{CLIENTS}{group['id']}/"),
        member.post(
            CLIENTS,
            {
                "client_group": "notion",
                "client_id": "fake-cn11-member-id",
                "client_secret": "fake-cn11-member-secret-not-real",
            },
            format="json",
        ),
        member.post(
            f"{CLIENTS}{group['id']}/rotate/",
            {"client_secret": "fake-cn11-member-secret-not-real"},
            format="json",
        ),
        member.delete(f"{CLIENTS}{group['id']}/"),
    ]
    for response in calls:
        assert response.status_code == 403, response.json()
        assert CLIENT_ID not in _dump(response.json())
    after = list(
        AgentccMCPCredential.objects.filter(organization=org).values_list(
            "pk", "status", "version"
        )
    )
    assert after == before
    assert gateway_unreachable, "this path asks the gateway, which is not running"


def test_oauth_client_never_pushed(auth_client, user, pushes):
    """An org client never reaches the gateway: not by the push, even when a
    config entry is made to reference it, and not by a probe."""
    org = user.organization
    _seed(org)
    static = vault.create(
        org, "github", kind="bearer", value=STATIC_SECRET, actor=user
    ).credential
    client = _create(auth_client, server_id="linear-eng")
    group = _create(
        auth_client,
        client_group="linear",
        client_id=SERVER_CLIENT_ID,
        client_secret=SERVER_SECRET,
    )
    # References to the org clients in state active, written past every
    # writer, as a forged or corrupted config would hold them: one on the
    # server the client was added for, and one on an entry keyed as the
    # vault keys the group's client, so only the kind keeps it out.
    forged = copy.deepcopy(_active(org).mcp)
    forged["servers"]["linear-eng"]["credential"] = {
        "mode": "shared",
        "credential_id": client["id"],
        "state": "active",
    }
    forged["servers"]["group:linear"] = {
        "url": LINEAR_URL,
        "transport": "http",
        "credential": {
            "mode": "shared",
            "credential_id": group["id"],
            "state": "active",
        },
    }
    AgentccOrgConfig.no_workspace_objects.filter(
        organization=org, is_active=True
    ).update(mcp=forged)

    assembled = vault.assemble_for_push(org.pk)
    response = auth_client.post(f"{GATEWAY}/reload/", {}, format="json")

    assert response.status_code == 200, response.json()
    assert set(assembled) == {"github"}
    payload = pushes[-1][1]["mcp"]["servers"]
    assert payload["github"]["credential"]["secret"]["value"] == STATIC_SECRET
    assert "secret" not in payload["linear-eng"]["credential"]
    assert "secret" not in payload["group:linear"]["credential"]
    # The push did carry a secret, so it could have carried the client's.
    assert STATIC_SECRET in _dump(pushes)
    assert not any(s in _dump(pushes) for s in (CLIENT_SECRET, SERVER_SECRET))
    assert static.pk != uuid.UUID(client["id"])
    # The probe resolves static credentials only: an org client is not found.
    with faked_probe(FakeGateway(_golden("mcp-probe-v1")["results"][0])) as gateway:
        probe = auth_client.post(
            PROBE, {"url": LINEAR_URL, "credential_id": client["id"]}, format="json"
        )
    assert probe.status_code == 404, probe.json()
    assert gateway.calls == []


def test_resolver_sees_org_client(auth_client, user, monkeypatch):
    """CN3's resolver reads what the API writes: the org's client wins over
    the platform client, a server's client over its group's, a rotation is
    used on the next connect and a revocation hands back to the platform
    client. Another org never gets this org's client."""
    org = user.organization
    _seed(org)
    _platform_env(monkeypatch, "linear")
    group = _create(auth_client, client_group="linear")
    server = _create(
        auth_client,
        server_id="linear-eng",
        client_id=SERVER_CLIENT_ID,
        client_secret=SERVER_SECRET,
    )

    ref = clients.resolve(org, "linear", None, **TOKEN_URL)
    assert (ref.source, ref.origin, ref.client_group) == ("org", "group", "linear")
    assert (ref.client_id, ref.credential_id) == (CLIENT_ID, group["id"])
    assert ref.token_exchange_secret() == CLIENT_SECRET
    assert (
        clients.client_source(
            ref, registration=ALL_REGISTRATIONS, client_group="linear", **TOKEN_URL
        )
        == "org"
    )
    for_server = clients.resolve(
        org, "linear", None, server_id="linear-eng", **TOKEN_URL
    )
    assert (for_server.origin, for_server.client_id, for_server.credential_id) == (
        "server",
        SERVER_CLIENT_ID,
        server["id"],
    )
    assert for_server.token_exchange_secret() == SERVER_SECRET

    rotated = auth_client.post(
        f"{CLIENTS}{group['id']}/rotate/",
        {"client_secret": ROTATED_SECRET},
        format="json",
    )
    assert rotated.status_code == 200, rotated.json()
    after_rotation = clients.resolve(org, "linear", None, **TOKEN_URL)
    assert (after_rotation.client_id, after_rotation.credential_id) == (
        CLIENT_ID,
        group["id"],
    )
    assert after_rotation.token_exchange_secret() == ROTATED_SECRET

    other_org, _ = _org_with_owner("CN11 other org")
    other = clients.resolve(other_org, "linear", None, **TOKEN_URL)
    assert (other.source, other.client_id) == ("platform", PLATFORM_ID)

    assert auth_client.delete(f"{CLIENTS}{group['id']}/").status_code == 200
    after_revoke = clients.resolve(org, "linear", None, **TOKEN_URL)
    assert (after_revoke.source, after_revoke.client_id) == ("platform", PLATFORM_ID)
    # The server's own client still serves its server.
    still = clients.resolve(org, "linear", None, server_id="linear-eng", **TOKEN_URL)
    assert (still.source, still.client_id) == ("org", SERVER_CLIENT_ID)


# --- Writes ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, status, code",
    [
        ({"client_group": "linear", "server_id": "linear-eng"}, 400, None),
        ({}, 400, None),
        ({"client_group": "Linear"}, 400, "invalid_connector_oauth_client"),
        ({"client_group": "l"}, 400, "invalid_connector_oauth_client"),
        ({"server_id": "nope"}, 404, "mcp_server_not_found"),
        ({"server_id": "fagi.web"}, 400, "invalid_connector_oauth_client"),
        ({"server_id": "Linear-Eng"}, 400, "invalid_connector_oauth_client"),
        (
            {"client_group": "linear", "client_id": " fake-cn11-padded-id"},
            400,
            "invalid_connector_oauth_client",
        ),
        (
            {"client_group": "linear", "client_id": "fake-cn11\nid"},
            400,
            "invalid_connector_oauth_client",
        ),
        (
            {"client_group": "linear", "client_secret": CLIENT_SECRET + "\n"},
            400,
            "invalid_connector_oauth_client",
        ),
        (
            {"client_group": "linear", "client_secret": " " + CLIENT_SECRET},
            400,
            "invalid_connector_oauth_client",
        ),
        (
            {"client_group": "linear", "client_secret": "enc::" + CLIENT_SECRET},
            400,
            "invalid_connector_oauth_client",
        ),
        ({"client_group": "linear", "client_secret": ""}, 400, None),
        ({"client_group": "linear", "client_secret": "x" * 8193}, 400, None),
        ({"client_group": "linear", "secret": CLIENT_SECRET}, 400, None),
        ({"client_group": "linear", "label": "x" * 256}, 400, None),
    ],
    ids=[
        "both-keys",
        "no-key",
        "upper-case-group",
        "short-group",
        "unknown-server",
        "managed-server",
        "bad-server-id",
        "padded-client-id",
        "control-in-client-id",
        "secret-newline",
        "secret-leading-space",
        "secret-ciphertext",
        "blank-secret",
        "long-secret",
        "unknown-field",
        "long-label",
    ],
)
def test_create_refusals(auth_client, user, pushes, body, status, code):
    org = user.organization
    _seed(org)
    before = _dump(_active(org).mcp)

    response = _create(auth_client, expect=status, **body)

    if code is not None:
        assert response["code"] == code
    assert CLIENT_SECRET not in _dump(response)
    assert AgentccMCPCredential.objects.filter(organization=org).count() == 0
    assert _dump(_active(org).mcp) == before and pushes == []


def test_one_active_client_per_key(auth_client, user):
    org = user.organization
    _seed(org)
    first = _create(auth_client, client_group="linear")
    _create(auth_client, server_id="linear-eng")  # a server's key is its own

    twice = _create(auth_client, expect=400, client_group="linear")
    assert twice["code"] == "connector_oauth_client_exists"
    assert CLIENT_SECRET not in _dump(twice)
    # Revoked, the key takes a new client (a new client ID is a new client).
    assert auth_client.delete(f"{CLIENTS}{first['id']}/").status_code == 200
    second = _create(auth_client, client_group="linear", client_id="fake-cn11-new-id")
    assert second["id"] != first["id"] and second["client_id"] == "fake-cn11-new-id"
    assert _row(first["id"]).status == Status.REVOKED


def test_rotate_and_revoke_rules(auth_client, user):
    org = user.organization
    _seed(org)
    created = _create(auth_client, client_group="linear")
    rotate = f"{CLIENTS}{created['id']}/rotate/"

    for body in (
        {"client_secret": ROTATED_SECRET + " "},
        {"client_secret": ROTATED_SECRET, "client_id": "fake-cn11-other-id"},
        {},
    ):
        refused = auth_client.post(rotate, body, format="json")
        assert refused.status_code == 400, refused.json()
        assert ROTATED_SECRET not in _dump(refused.json())
    assert _row(created["id"]).version == 1
    assert decrypt_token(_row(created["id"]).encrypted_secret) == CLIENT_SECRET

    ok = auth_client.post(rotate, {"client_secret": ROTATED_SECRET}, format="json")
    result = ok.json()["result"]
    assert (result["version"], result["client_id"]) == (2, CLIENT_ID)
    assert result["rotated_at"]
    assert decrypt_token(_row(created["id"]).encrypted_secret) == ROTATED_SECRET

    assert auth_client.delete(f"{CLIENTS}{created['id']}/").status_code == 200
    again = auth_client.delete(f"{CLIENTS}{created['id']}/")
    late = auth_client.post(rotate, {"client_secret": CLIENT_SECRET}, format="json")
    assert (again.status_code, late.status_code) == (400, 400)
    assert again.json()["code"] == late.json()["code"] == "mcp_credential_not_active"
    # The revoked client still reads, as revoked.
    detail = auth_client.get(f"{CLIENTS}{created['id']}/").json()["result"]
    assert detail["status"] == "revoked"


def test_org_scoped(auth_client, user):
    org = user.organization
    _seed(org)
    own = _create(auth_client, client_group="linear")
    other_org, owner = _org_with_owner("CN11 Other Org")
    _seed(other_org)
    theirs = vault.create_oauth_client(
        other_org,
        client_group="linear",
        client_id="fake-cn11-their-id",
        client_secret=vault.SealedSecret("fake-cn11-their-secret-not-real"),
        actor=owner,
    )

    listed = auth_client.get(CLIENTS).json()["results"]
    assert [c["id"] for c in listed] == [own["id"]]
    for response in (
        auth_client.get(f"{CLIENTS}{theirs.pk}/"),
        auth_client.post(
            f"{CLIENTS}{theirs.pk}/rotate/",
            {"client_secret": ROTATED_SECRET},
            format="json",
        ),
        auth_client.delete(f"{CLIENTS}{theirs.pk}/"),
        auth_client.get(f"{CLIENTS}{uuid.uuid4()}/"),
    ):
        assert response.status_code == 404, response.json()
        assert "fake-cn11-their-id" not in _dump(response.json())
    theirs.refresh_from_db()
    assert (theirs.status, theirs.version) == (Status.ACTIVE, 1)


def test_rows_that_are_not_org_clients_are_not_this_apis(
    auth_client, user, gateway_unreachable
):
    """The API reads and writes oauth_client rows only: an upstream credential
    named by its ID answers 404 here."""
    org = user.organization
    _seed(org)
    static = vault.create(
        org, "github", kind="bearer", value=STATIC_SECRET, actor=user
    ).credential

    for response in (
        auth_client.get(f"{CLIENTS}{static.pk}/"),
        auth_client.post(
            f"{CLIENTS}{static.pk}/rotate/",
            {"client_secret": ROTATED_SECRET},
            format="json",
        ),
        auth_client.delete(f"{CLIENTS}{static.pk}/"),
    ):
        assert response.status_code == 404, response.json()
    static.refresh_from_db()
    assert (static.status, static.version) == (Status.ACTIVE, 1)
    assert auth_client.get(CLIENTS).json()["results"] == []
    assert gateway_unreachable, "this path asks the gateway, which is not running"


def test_m14_routes_leave_org_clients_alone(auth_client, user):
    """M14's credential routes are for upstream credentials: they neither list
    an org client nor rotate or revoke it."""
    org = user.organization
    _seed(org)
    created = _create(auth_client, client_group="linear")

    listed = auth_client.get(CREDENTIALS).json()["results"]
    rotated = auth_client.post(
        f"{CREDENTIALS}{created['id']}/rotate/",
        {"value": ROTATED_SECRET},
        format="json",
    )
    revoked = auth_client.delete(f"{CREDENTIALS}{created['id']}/")

    assert listed == []
    for response in (rotated, revoked):
        assert response.status_code == 400, response.json()
        assert response.json()["code"] == "mcp_credential_is_oauth_client"
        assert ROTATED_SECRET not in _dump(response.json())
    row = _row(created["id"])
    assert (row.status, row.version) == (Status.ACTIVE, 1)
    assert decrypt_token(row.encrypted_secret) == CLIENT_SECRET


# --- A server's client never follows it to another origin ----------------------


def test_removing_the_server_revokes_its_client(auth_client, user, pushes):
    org = user.organization
    _seed(org)
    server_client = _create(auth_client, server_id="linear-eng")
    group_client = _create(
        auth_client, client_group="linear", client_id=SERVER_CLIENT_ID
    )

    removed = auth_client.post(
        f"{GATEWAY}/remove-mcp-server/", {"server_id": "linear-eng"}, format="json"
    )

    assert removed.status_code == 200, removed.json()
    assert _row(server_client["id"]).status == Status.REVOKED
    assert _row(group_client["id"]).status == Status.ACTIVE
    # Added again, at another origin, the server does not inherit the client.
    readded = auth_client.post(
        f"{GATEWAY}/update-mcp-server/",
        {
            "server_id": "linear-eng",
            "config": {
                "url": "https://mcp.elsewhere.example.com/mcp",
                "transport": "http",
            },
        },
        format="json",
    )
    assert readded.status_code == 200, readded.json()
    ref = clients.resolve(org, "linear", None, server_id="linear-eng", **TOKEN_URL)
    assert (ref.origin, ref.client_id) == ("group", SERVER_CLIENT_ID)


def test_moving_the_server_revokes_its_client(
    auth_client, user, pushes, django_capture_on_commit_callbacks
):
    """A console save that moves a server to another scheme, host or port
    revokes the org client added for that server, in the same write, and
    logs it once the write commits; a path change keeps it."""
    org = user.organization
    _seed(org)
    created = _create(auth_client, server_id="linear-eng")

    def save(url):
        with django_capture_on_commit_callbacks(execute=True):
            return auth_client.post(
                f"{GATEWAY}/update-mcp-server/",
                {
                    "server_id": "linear-eng",
                    "config": {"url": url, "transport": "http"},
                },
                format="json",
            )

    with capture_logs() as logs:
        same_origin = save("https://mcp.linear.example.com/v2/mcp")
    assert same_origin.status_code == 200, same_origin.json()
    assert _row(created["id"]).status == Status.ACTIVE
    assert not [e for e in logs if e["event"] == "connector_oauth_client_revoked"]

    with capture_logs() as logs:
        moved = save("https://mcp.elsewhere.example.com/mcp")
    assert moved.status_code == 200, moved.json()
    assert _row(created["id"]).status == Status.REVOKED
    (event,) = [e for e in logs if e["event"] == "connector_oauth_client_revoked"]
    assert (event["credential_id"], event["reason"]) == (
        created["id"],
        "server_moved",
    )
    assert (
        clients.resolve(org, "linear", None, server_id="linear-eng", **TOKEN_URL)
        is None
    )


def test_moving_the_server_keeps_the_client_when_the_write_is_refused(
    auth_client, user, pushes, django_capture_on_commit_callbacks
):
    """The revocation is part of the config write: a refused write keeps the
    client and logs no revocation."""
    org = user.organization
    _seed(org)
    created = _create(auth_client, server_id="linear-eng")

    with capture_logs() as logs, django_capture_on_commit_callbacks(execute=True):
        refused = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "linear-eng",
                "config": {"url": "ftp://mcp.elsewhere.example.com/mcp"},
            },
            format="json",
        )

    assert refused.status_code == 400, refused.json()
    assert _row(created["id"]).status == Status.ACTIVE
    assert not [e for e in logs if e["event"] == "connector_oauth_client_revoked"]


def test_readding_the_server_revokes_a_client_left_behind(auth_client, user, pushes):
    """A generic save that drops a server leaves its client (no hook there
    yet, R3), but a console save that adds the server again revokes it: the
    client was added for a server at an origin this one may not share."""
    org = user.organization
    _seed(org)
    created = _create(auth_client, server_id="linear-eng")
    dropped = copy.deepcopy(_active(org).mcp)
    del dropped["servers"]["linear-eng"]

    generic = auth_client.post(
        f"{GATEWAY}/update-config/", {"mcp": dropped}, format="json"
    )
    assert generic.status_code == 200, generic.json()
    assert _row(created["id"]).status == Status.ACTIVE

    readded = auth_client.post(
        f"{GATEWAY}/update-mcp-server/",
        {
            "server_id": "linear-eng",
            "config": {
                "url": "https://mcp.elsewhere.example.com/mcp",
                "transport": "http",
            },
        },
        format="json",
    )

    assert readded.status_code == 200, readded.json()
    assert _row(created["id"]).status == Status.REVOKED
    assert (
        clients.resolve(org, "linear", None, server_id="linear-eng", **TOKEN_URL)
        is None
    )


@pytest.mark.parametrize(
    "change, revoked",
    [
        ({"url": "https://mcp.linear.example.com/v2/mcp?x=1"}, False),
        ({"url": "HTTPS://MCP.LINEAR.EXAMPLE.COM:443/mcp"}, False),
        ({"url": "http://mcp.linear.example.com/mcp"}, True),
        ({"url": "https://mcp.linear.example.com:8443/mcp"}, True),
        ({"url": "https://other.example.com/mcp"}, True),
        (None, True),
    ],
    ids=["path-query", "same-origin-spelled", "scheme", "port", "host", "removed"],
)
def test_generic_writes_revoke_clients_left_behind(user, change, revoked):
    """The rule the generic writers apply (R3, TestGenericWritersRevoke
    ClientsLeftBehind), as a unit: a server the write removes or moves to
    another origin loses the org client added for it; other servers' and
    groups' clients stay."""
    org = user.organization
    stored = {
        "servers": {
            "linear-eng": {"url": LINEAR_URL},
            "github": {"url": GITHUB_URL},
        }
    }
    _seed(org, stored["servers"])
    sealed = vault.SealedSecret(SERVER_SECRET)
    moved = vault.create_oauth_client(
        org, server_id="linear-eng", client_id="a", client_secret=sealed, actor=user
    )
    kept = vault.create_oauth_client(
        org, server_id="github", client_id="b", client_secret=sealed, actor=user
    )
    group = vault.create_oauth_client(
        org, client_group="linear", client_id="c", client_secret=sealed, actor=user
    )
    value = copy.deepcopy(stored)
    if change is None:
        del value["servers"]["linear-eng"]
    else:
        value["servers"]["linear-eng"].update(change)

    ids = vault.revoke_server_clients_left_behind(org, value, stored)

    assert ids == ([str(moved.pk)] if revoked else [])
    expected = Status.REVOKED if revoked else Status.ACTIVE
    assert _row(moved.pk).status == expected
    assert _row(kept.pk).status == _row(group.pk).status == Status.ACTIVE


@pytest.mark.integration
class TestGenericWritersRevokeClientsLeftBehind:
    """An org OAuth client never follows its server to another origin: the
    generic config writers
    (write_config_version_with, which update-config and POST org-configs/
    use, and activate_config_version) revoke, under the org's lock and in the
    write's transaction, the org client of a server the write removes or
    moves to another origin; other servers' and groups' clients stay."""

    def _clients(self, org, user):
        sealed = vault.SealedSecret(SERVER_SECRET)
        linear = vault.create_oauth_client(
            org, server_id="linear-eng", client_id="a", client_secret=sealed, actor=user
        )
        github = vault.create_oauth_client(
            org, server_id="github", client_id="b", client_secret=sealed, actor=user
        )
        group = vault.create_oauth_client(
            org, client_group="linear", client_id="c", client_secret=sealed, actor=user
        )
        return linear, github, group

    @pytest.mark.parametrize(
        "change",
        [None, {"url": "https://other.example.com/mcp"}],
        ids=["removed", "moved"],
    )
    def test_a_write_revokes(self, user, pushes, change):
        from agentcc.services.org_config_writer import write_config_version_with

        org = user.organization
        _seed(org)
        linear, github, group = self._clients(org, user)

        def updater(config):
            mcp = copy.deepcopy(config.mcp)
            if change is None:
                del mcp["servers"]["linear-eng"]
            else:
                mcp["servers"]["linear-eng"].update(change)
            config.mcp = mcp

        write_config_version_with(org, updater, description="generic write")

        assert _row(linear.pk).status == Status.REVOKED
        assert _row(github.pk).status == _row(group.pk).status == Status.ACTIVE

    def test_a_write_that_keeps_the_origin_keeps_the_client(self, user, pushes):
        from agentcc.services.org_config_writer import write_config_version_with

        org = user.organization
        _seed(org)
        linear, _, _ = self._clients(org, user)

        def updater(config):
            mcp = copy.deepcopy(config.mcp)
            mcp["servers"]["linear-eng"]["url"] = LINEAR_URL + "?tools=all"
            config.mcp = mcp

        write_config_version_with(org, updater, description="same origin")

        assert _row(linear.pk).status == Status.ACTIVE

    def test_an_activation_revokes(self, user, pushes):
        from agentcc.services.org_config_writer import activate_config_version

        org = user.organization
        older = AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=1,
            is_active=False,
            mcp={"servers": {"github": {"url": GITHUB_URL, "transport": "http"}}},
        )
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=2,
            is_active=True,
            mcp={
                "servers": {
                    "linear-eng": {"url": LINEAR_URL, "transport": "http"},
                    "github": {"url": GITHUB_URL, "transport": "http"},
                }
            },
        )
        linear, github, group = self._clients(org, user)

        activate_config_version(older)

        assert _row(linear.pk).status == Status.REVOKED
        assert _row(github.pk).status == _row(group.pk).status == Status.ACTIVE


# --- Error reports (the app's Sentry reports frame locals) -------------------------

APP_DENYLIST = list(DEFAULT_DENYLIST) + [
    k for k in SENSITIVE_KEY_SUBSTRINGS if k not in DEFAULT_DENYLIST
]


class _SendNothing(Transport):
    def capture_envelope(self, envelope):
        pass


class _ErrorReports:
    """Sentry events built by a real ``sentry_sdk.Client`` with local variables
    on, through the app's scrubber and before_send ("app") or no scrubbing at
    all ("unscrubbed"). The client is bound to a scope per capture, and its
    transport sends nothing."""

    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.events = []
        if pipeline == "app":
            scrubber = EventScrubber(denylist=APP_DENYLIST, recursive=True)
            app_before_send = _get_before_send()
        else:
            scrubber = EventScrubber(denylist=[], send_default_pii=True)
            app_before_send = None

        def keep(event, hint):
            if app_before_send is not None:
                event = app_before_send(event, hint)
            if event is not None:
                self.events.append(event)
            return None

        self.client = sentry_sdk.Client(
            dsn="https://public@127.0.0.1:9/1",
            transport=_SendNothing,
            default_integrations=False,
            enable_backpressure_handling=False,
            send_default_pii=True,
            include_local_variables=True,
            include_source_context=True,
            event_scrubber=scrubber,
            before_send=keep,
        )

    def report(self, call, expected):
        sent = len(self.events)
        try:
            call()
        except expected:
            with sentry_sdk.new_scope() as scope:
                scope.set_client(self.client)
                scope.capture_exception()
        else:
            pytest.fail("expected an exception")
        assert len(self.events) == sent + 1
        return self.events[-1]


@pytest.fixture(params=["app", "unscrubbed"])
def error_reports(request):
    reports = _ErrorReports(request.param)
    yield reports
    reports.client.close()


def _frames(event):
    return [
        frame
        for exception in event["exception"]["values"]
        for frame in exception["stacktrace"]["frames"]
    ]


def _frame_functions(event):
    return [frame["function"] for frame in _frames(event)]


# Frames that exist only because a test drives the call: the test clients'
# (the repo's WorkspaceAwareAPIClient lives in conftest), which build the
# request from the plaintext body, and this module's own.
_TEST_MODULES = ("django.test", "rest_framework.test", "conftest", __name__)


def _app_frames(event):
    """The report's frames from the app's own code and the frameworks serving
    the request, as production would report them."""
    return [
        frame
        for frame in _frames(event)
        if not (frame.get("module") or "").startswith(_TEST_MODULES)
    ]


class _StoreFailed(RuntimeError):
    pass


def test_secret_never_in_error_reports(auth_client, user, error_reports):
    """No frame of the write path holds a client secret in clear, under any
    name: a refusal's report and an unexpected failure's are clean even with
    no scrubber at all."""
    org = user.organization
    _seed(org)
    # A frame's locals do reach a report, so the checks below can fail.
    canary = error_reports.report(
        partial(_raise_holding, CLIENT_SECRET), ZeroDivisionError
    )
    assert CLIENT_SECRET in _dump(canary)

    bad = vault.SealedSecret(CLIENT_SECRET + "\n")
    refusal = error_reports.report(
        partial(
            vault.create_oauth_client,
            org,
            client_group="linear",
            client_id=CLIENT_ID,
            client_secret=bad,
            actor=user,
        ),
        vault.MCPCredentialError,
    )
    assert "create_oauth_client" in _frame_functions(refusal)
    assert CLIENT_SECRET not in _dump(refusal)

    created = _create(auth_client, client_group="linear")
    row = _row(created["id"])
    refused_rotation = error_reports.report(
        partial(
            vault.rotate_oauth_client,
            row,
            vault.SealedSecret(" " + ROTATED_SECRET),
            user,
        ),
        vault.MCPCredentialError,
    )
    assert ROTATED_SECRET not in _dump(refused_rotation)

    # An unexpected failure inside a write through the API: the view's and
    # the service's frames hold the secret sealed only.
    with patch.object(
        AgentccMCPCredential.objects, "create", side_effect=_StoreFailed("db down")
    ):
        failure = error_reports.report(
            partial(
                auth_client.post,
                CLIENTS,
                {
                    "server_id": "linear-eng",
                    "client_id": SERVER_CLIENT_ID,
                    "client_secret": SERVER_SECRET,
                },
                format="json",
            ),
            _StoreFailed,
        )
    app_frames = _app_frames(failure)
    functions = [frame["function"] for frame in app_frames]
    assert "post" in functions and "create_oauth_client" in functions
    # The view's frame holds the validated request, with the secret sealed.
    # Through the app's pipeline, before_send also scrubs frame locals (the
    # release line's fba969ecdc), so the sealed value reads as the scrubber's
    # [Filtered] there; with no scrubbing it reads as the seal's <redacted>.
    (view,) = [
        f
        for f in app_frames
        if (f["module"], f["function"])
        == ("agentcc.views.connector_oauth_clients", "post")
    ]
    if error_reports.pipeline == "app":
        assert "[Filtered]" in _dump(view["vars"])
    else:
        assert "<redacted>" in _dump(view["vars"])
    assert SERVER_SECRET not in _dump(app_frames)


def test_encryption_failure_is_a_server_error(user, error_reports):
    """A vault that cannot encrypt refuses the write as a server fault: no
    row, and a report whose frames, encrypt_token's among them, never hold
    the secret."""
    org = user.organization
    _seed(org)

    def failing_encrypt(value):
        held = value  # noqa: F841 - a frame that holds the secret, if reported
        raise ValueError("no key")

    with patch(
        "agentcc.services.mcp_credentials.encrypt_token", side_effect=failing_encrypt
    ):
        report = error_reports.report(
            partial(
                vault.create_oauth_client,
                org,
                client_group="linear",
                client_id=CLIENT_ID,
                client_secret=vault.SealedSecret(CLIENT_SECRET),
                actor=user,
            ),
            RuntimeError,
        )

    assert "failing_encrypt" not in _frame_functions(report)
    assert "ValueError" in _dump(report["exception"]["values"][-1]["value"])
    assert CLIENT_SECRET not in _dump(report)
    assert AgentccMCPCredential.objects.filter(organization=org).count() == 0


def _raise_holding(value):
    held = value  # noqa: F841 - read by the report
    raise ZeroDivisionError("canary")


# --- Routes ------------------------------------------------------------------------


def test_routes_reverse_under_agentcc():
    """On the site, under ``agentcc/`` as every other module's, the routes
    are these."""
    some_id = uuid.uuid4()
    assert reverse("agentcc-connector-oauth-clients") == CLIENTS
    assert (
        reverse(
            "agentcc-connector-oauth-client-detail",
            kwargs={"credential_id": some_id},
        )
        == f"{CLIENTS}{some_id}/"
    )
    assert (
        reverse(
            "agentcc-connector-oauth-client-rotate",
            kwargs={"credential_id": some_id},
        )
        == f"{CLIENTS}{some_id}/rotate/"
    )


def test_list_paginates(auth_client, user):
    org = user.organization
    _seed(org)
    for group in ("linear", "notion", "google"):
        _create(auth_client, client_group=group, client_id=f"fake-cn11-{group}")

    first = auth_client.get(CLIENTS, {"limit": 2}).json()
    second = auth_client.get(CLIENTS, {"limit": 2, "page": 2}).json()

    assert first["count"] == 3 and len(first["results"]) == 2
    assert len(second["results"]) == 1
    groups = [c["client_group"] for c in first["results"] + second["results"]]
    assert groups == ["google", "linear", "notion"]
    bad = auth_client.get(CLIENTS, {"limit": 0})
    unknown = auth_client.get(CLIENTS, {"server": "x"})
    assert (bad.status_code, unknown.status_code) == (400, 400)


def test_needs_a_login(api_client):
    """No session: 401 or 403, never a list."""
    response = api_client.get(CLIENTS)
    assert response.status_code in (401, 403)
    assert "results" not in response.json()
