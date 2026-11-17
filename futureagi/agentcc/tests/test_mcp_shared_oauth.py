"""The organization's one shared account, signed in over OAuth (CN24): an
Owner or Admin signs it in once, and every key or person of the organization
that may use the server uses it, per call.

A shared server whose credential kind is oauth names no stored key: an Owner
or Admin starts the sign-in with ``mode: shared``, the vault stores the token
pair as the organization's row (scope ``shared``, kind ``oauth``, no user) and
the config names it, and the gateway fetches its token just in time with the
organization's request (``mcp-upstream-token-v1`` SharedRequest), refreshed
as a person's is. A member can neither start nor end that sign-in, and no
token is in any read, log or response.

Each test is one property. The vendor is ``FakeVendor``, answered in place of
the guarded fetcher; the gateway's push and cache purge, and its config read
(``gateway_reads``), are recorded, so no test reaches a network. Every value
here is a fake.
"""

import copy
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.utils import timezone
from structlog.testing import capture_logs

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_upstream_oauth import (
    AgentccMCPConnectState,
    AgentccMCPUpstreamTokenAudit,
)
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import gateway_client
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.credential_manager import decrypt_token
from agentcc.tests.mcp_upstream_oauth_helpers import (
    ADMIN_TOKEN,
    CALLBACK,
    CALLBACK_PATH,
    CLIENT_METADATA,
    CONSOLE,
    INTERNAL_SECRET,
    ISSUER,
    SERVER_URL,
    START,
    FakeVendor,
    member,
    seed_config,
)

pytestmark = pytest.mark.django_db

Status = AgentccMCPCredential.Status

CRM = "crm"
TOKEN_PATH = "/agentcc/internal/mcp/upstream-token/"
HEADER = "HTTP_X_AGENTCC_MCP_INTERNAL"
GATEWAY = "/agentcc/gateways/default"
CREDENTIALS = "/agentcc/mcp/credentials/"


@pytest.fixture(autouse=True)
def _settings(settings, monkeypatch):
    settings.AGENTCC_MCP_CONNECT_CALLBACK_URL = CALLBACK
    settings.AGENTCC_MCP_CONNECT_CONSOLE_URL = CONSOLE
    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, INTERNAL_SECRET)
    monkeypatch.setattr(gateway_client, "AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN)


@pytest.fixture
def vendor():
    fake = FakeVendor()
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        yield fake


@pytest.fixture
def purges():
    calls = []

    def purge(org_id, server_id, user_id=None):
        calls.append((org_id, server_id, user_id))
        return True

    with patch.object(connect.gateway_credentials, "purge", purge):
        yield calls


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


@pytest.fixture
def gateway_reads():
    """The gateway the console's config read asks (GET .../config/ calls
    health_check, then get_config): up, reporting only its own node blocks.
    Recorded in place of the client the view builds, so the read never leaves
    the test; each test that reads the config asserts it was asked."""
    client = MagicMock()
    client.get_config.return_value = {"org_sections_enforced": ["mcp"]}
    with patch("agentcc.views.gateway.get_gateway_client", return_value=client):
        yield client


def _servers(**extra):
    """The org's shared OAuth server ``crm`` at the vendor, a per-user one
    and a shared one with a stored key's slot, plus ``extra``."""
    return {
        CRM: {
            "url": SERVER_URL,
            "transport": "http",
            "credential": {"mode": "shared", "kind": "oauth"},
        },
        "issues": {
            "url": SERVER_URL,
            "transport": "http",
            "credential": {"mode": "per_user"},
        },
        "wiki": {
            "url": "https://mcp.wiki.example/mcp",
            "transport": "http",
            "credential": {"mode": "shared"},
        },
        **extra,
    }


@pytest.fixture
def config(organization, user):
    return seed_config(organization, _servers())


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _start(client, server_id=CRM, **body):
    return client.post(
        START.format(server_id), {"mode": "shared", **body}, format="json"
    )


def _connect(client, vendor):
    """A whole shared sign-in: the admin's start, the vendor's sign-in, the
    callback in the browser that started it."""
    response = _start(client)
    assert response.status_code == 200, response.content
    url = response.json()["result"]["authorize_url"]
    (name,) = [n for n in response.cookies if n.startswith(connect.COOKIE_PREFIX)]
    browser = Client()
    browser.cookies[name] = response.cookies[name].value
    code, state = vendor.authorize(url)
    return browser.get(CALLBACK_PATH, {"code": code, "state": state, "iss": ISSUER})


def _shared(org, server_id=CRM):
    return AgentccMCPCredential.objects.filter(
        organization=org, server_id=server_id, scope="shared", kind="oauth"
    )


def _ask(body):
    return Client().post(
        TOKEN_PATH,
        json.dumps(body),
        content_type="application/json",
        **{HEADER: INTERNAL_SECRET},
    )


def _shared_request(org, server_id=CRM):
    return {"org_id": str(org.pk), "server_id": server_id, "scope": "shared"}


# --- Property: an Owner or Admin signs the organization's one account in -----------


def test_an_admin_signs_in_the_organizations_one_account(
    auth_client, organization, user, config, vendor, purges, pushes
):
    response = _connect(auth_client, vendor)

    assert response.status_code == 200, response.content
    row = _shared(organization).get()
    assert (row.user_id, row.status, row.created_by_id) == (
        None,
        Status.ACTIVE,
        user.pk,
    )
    assert decrypt_token(row.encrypted_secret) == vendor.issued[-1]["access_token"]
    assert row.oauth_client_id == CLIENT_METADATA
    credential = _active(organization).mcp["servers"][CRM]["credential"]
    assert credential == {
        "mode": "shared",
        "kind": "oauth",
        "credential_id": str(row.pk),
        "state": "active",
    }
    # The push names the account and carries no secret for it.
    (pushed_crm,) = [p[1]["mcp"]["servers"][CRM] for p in pushes[-1:]]
    assert pushed_crm["credential"]["credential_id"] == str(row.pk)
    assert "secret" not in pushed_crm["credential"]
    assert purges[-1] == (str(organization.pk), CRM, None)


def test_signing_in_again_rotates_the_organizations_account_in_place(
    auth_client, organization, user, config, vendor, purges, pushes
):
    _connect(auth_client, vendor)
    first = _shared(organization).get()
    _connect(auth_client, vendor)

    again = _shared(organization).get()
    assert again.pk == first.pk and again.version == first.version + 1
    assert decrypt_token(again.encrypted_secret) == vendor.issued[-1]["access_token"]


# --- Property: a member neither starts nor ends the organization's sign-in ----------


def test_a_member_can_neither_start_nor_end_the_shared_sign_in(
    auth_client, organization, workspace, user, config, vendor, purges, pushes
):
    _connect(auth_client, vendor)
    _, member_client = member(organization, workspace)

    started = _start(member_client)
    ended = member_client.delete(f"/agentcc/mcp/connect/{CRM}/?mode=shared")

    assert (started.status_code, started.json()["code"]) == (403, "not_admin")
    assert ended.status_code == 403
    assert AgentccMCPConnectState.objects.count() == 0
    assert _shared(organization).get().status == Status.ACTIVE


def test_a_member_never_reads_the_organizations_token(
    auth_client,
    organization,
    workspace,
    user,
    config,
    vendor,
    purges,
    pushes,
    gateway_reads,
):
    _connect(auth_client, vendor)
    _, member_client = member(organization, workspace)
    issued = vendor.issued[-1]

    bodies = [
        member_client.get(CREDENTIALS).content.decode(),
        member_client.get(f"{CREDENTIALS}?server_id={CRM}").content.decode(),
        member_client.get(f"{GATEWAY}/config/").content.decode(),
    ]

    # The config read asked the (recorded) gateway, as the console's does.
    assert gateway_reads.health_check.call_count == 1
    assert gateway_reads.get_config.call_count == 1
    for body in bodies:
        assert issued["access_token"] not in body
        assert issued["refresh_token"] not in body


# --- Property: the shared sign-in is for a shared OAuth server only ------------------


@pytest.mark.parametrize(
    "server_id, code",
    [
        ("issues", "server_not_shared_oauth"),
        ("wiki", "server_not_shared_oauth"),
        ("missing", "server_not_found"),
    ],
)
def test_the_shared_sign_in_is_for_a_shared_oauth_server_only(
    auth_client, organization, config, vendor, server_id, code
):
    response = _start(auth_client, server_id)

    assert response.json()["code"] == code
    assert response.status_code in (400, 404)
    assert vendor.requests == []


def test_a_persons_sign_in_never_fills_the_organizations_slot(
    auth_client, organization, config, vendor
):
    response = auth_client.post(START.format(CRM), {}, format="json")

    assert (response.status_code, response.json()["code"]) == (
        400,
        "server_not_per_user",
    )
    assert vendor.requests == []


def test_a_connect_link_names_a_person_so_the_shared_sign_in_takes_none(
    auth_client, organization, user, config, vendor
):
    binding = connect.seal_binding(str(organization.pk), CRM, str(user.pk))

    response = _start(auth_client, binding=binding)

    assert (response.status_code, response.json()["code"]) == (
        400,
        "binding_not_shared",
    )
    assert vendor.requests == []


def test_the_admin_who_started_must_still_be_one_when_it_completes(
    auth_client, organization, user, config, vendor, purges, pushes
):
    from tfc.constants.levels import Level

    response = _start(auth_client)
    url = response.json()["result"]["authorize_url"]
    (name,) = [n for n in response.cookies if n.startswith(connect.COOKIE_PREFIX)]
    membership = user.organization_memberships.get(organization=organization)
    membership.level = Level.MEMBER
    membership.save(update_fields=["level"])
    browser = Client()
    browser.cookies[name] = response.cookies[name].value
    code, state = vendor.authorize(url)

    done = browser.get(CALLBACK_PATH, {"code": code, "state": state, "iss": ISSUER})

    assert done.status_code == 400
    assert not _shared(organization).exists()


# --- Property: the gateway gets the account's token only with the shared request ----


def test_the_gateway_gets_the_shared_token_with_the_organizations_request(
    auth_client, organization, user, config, vendor, purges, pushes
):
    _connect(auth_client, vendor)

    answer = _ask(_shared_request(organization)).json()

    assert answer["access_token"] == vendor.issued[-1]["access_token"]
    assert answer["credential_version"] == 1
    audit = AgentccMCPUpstreamTokenAudit.objects.latest("created_at")
    assert (audit.server_id, audit.user_id, audit.outcome) == (CRM, "", "ok")


def test_a_persons_request_never_gets_the_organizations_token(
    auth_client, organization, user, config, vendor, purges, pushes
):
    _connect(auth_client, vendor)

    person = _ask(
        {"org_id": str(organization.pk), "user_id": str(user.pk), "server_id": CRM}
    )
    other_server = _ask(_shared_request(organization, "issues"))

    assert person.json()["error"] == "no_credential"
    assert other_server.json()["error"] == "no_credential"


def test_another_organizations_request_never_gets_the_token(
    auth_client, organization, user, config, vendor, purges, pushes
):
    from accounts.models import Organization

    _connect(auth_client, vendor)
    other = Organization.objects.create(name="cn24-other-org")
    seed_config(other, _servers())

    answer = _ask(_shared_request(other)).json()

    assert answer["error"] == "no_credential"


def test_the_token_is_served_only_while_the_config_names_the_account(
    auth_client, organization, user, config, vendor, purges, pushes
):
    _connect(auth_client, vendor)
    active = _active(organization)
    mcp = copy.deepcopy(active.mcp)
    mcp["servers"][CRM]["credential"] = {"mode": "shared", "kind": "oauth"}
    AgentccOrgConfig.no_workspace_objects.filter(pk=active.pk).update(mcp=mcp)

    answer = _ask(_shared_request(organization)).json()

    assert answer["error"] == "no_credential"


def test_a_shared_token_close_to_expiry_is_refreshed_in_place(
    auth_client, organization, user, config, vendor, purges, pushes
):
    _connect(auth_client, vendor)
    row = _shared(organization).get()
    row.token_expires_at = timezone.now() + timedelta(seconds=30)
    row.save(update_fields=["token_expires_at"])

    answer = _ask(_shared_request(organization)).json()

    assert answer["access_token"] == vendor.issued[-1]["access_token"]
    assert answer["credential_version"] == 2
    row.refresh_from_db()
    assert decrypt_token(row.encrypted_refresh) == vendor.issued[-1]["refresh_token"]


def test_a_refused_refresh_marks_the_account_for_sign_in_again(
    auth_client, organization, user, config, vendor, purges, pushes
):
    _connect(auth_client, vendor)
    row = _shared(organization).get()
    row.token_expires_at = timezone.now() + timedelta(seconds=30)
    row.save(update_fields=["token_expires_at"])
    vendor.refresh_fails = True

    answer = _ask(_shared_request(organization)).json()

    assert answer["error"] == "needs_reauth"
    assert answer["connect_hint"].endswith(f"/agentcc/mcp/connect/{CRM}/start/")
    row.refresh_from_db()
    assert row.status == Status.NEEDS_REAUTH


# --- Property: an admin ends the sign-in, everywhere -------------------------------


def test_disconnect_revokes_the_organizations_account(
    auth_client, organization, user, config, vendor, purges, pushes
):
    _connect(auth_client, vendor)
    issued = vendor.issued[-1]
    purges.clear()

    response = auth_client.delete(f"/agentcc/mcp/connect/{CRM}/?mode=shared")

    assert response.status_code == 200, response.content
    row = _shared(organization).get()
    assert row.status == Status.REVOKED and row.encrypted_refresh is None
    assert vendor.revoked[-1]["token"] == issued["refresh_token"]
    credential = _active(organization).mcp["servers"][CRM]["credential"]
    assert (credential["kind"], credential["state"]) == ("oauth", "revoked")
    assert purges == [(str(organization.pk), CRM, None)]
    assert _ask(_shared_request(organization)).json()["error"] == "revoked"


# --- Property: a server signed in over OAuth takes no stored key --------------------


def test_a_stored_key_is_refused_on_a_shared_oauth_server(
    auth_client, organization, user, config, vendor, purges, pushes
):
    _connect(auth_client, vendor)

    created = auth_client.post(
        CREDENTIALS,
        {"server_id": CRM, "kind": "bearer", "value": "fake-cn24-static-not-real"},
        format="json",
    )
    console = auth_client.post(
        f"{GATEWAY}/update-mcp-server/",
        {
            "server_id": CRM,
            "config": {
                "url": SERVER_URL,
                "transport": "http",
                "credential": {"mode": "shared", "kind": "oauth"},
                "auth": {"type": "bearer", "token": "fake-cn24-static-not-real"},
            },
        },
        format="json",
    )

    assert created.status_code == 400, created.content
    assert console.status_code == 400, console.content
    row = _shared(organization).get()
    assert row.kind == "oauth" and row.status == Status.ACTIVE
    assert not AgentccMCPCredential.objects.filter(
        organization=organization, server_id=CRM, kind="bearer"
    ).exists()


def test_the_oauth_kind_is_saved_on_a_shared_credential_only(
    organization, user, config
):
    from agentcc.services.org_config_writer import write_config_version_with

    def updater(config):
        config.mcp["servers"]["issues"]["credential"] = {
            "mode": "per_user",
            "kind": "oauth",
        }

    with pytest.raises(OrgConfigRejected):
        write_config_version_with(organization, updater, user=user)


# --- Property: no token in any log line or response --------------------------------


def test_no_shared_token_in_any_log_or_response(
    auth_client, organization, user, config, vendor, purges, pushes, gateway_reads
):
    with capture_logs() as logs:
        done = _connect(auth_client, vendor)
        issued = dict(vendor.issued[-1])
        listed = auth_client.get(CREDENTIALS)
        configured = auth_client.get(f"{GATEWAY}/config/")
        gone = auth_client.delete(f"/agentcc/mcp/connect/{CRM}/?mode=shared")

    # The config read asked the (recorded) gateway, as the console's does.
    assert gateway_reads.health_check.call_count == 1
    assert gateway_reads.get_config.call_count == 1
    seen = repr(logs) + done.content.decode() + listed.content.decode()
    seen += configured.content.decode() + gone.content.decode() + json.dumps(pushes)
    assert issued["access_token"] not in seen
    assert issued["refresh_token"] not in seen


# --- Property: the kind never switches under an account the config names ----------


def test_the_oauth_kind_is_not_put_over_a_stored_key(
    auth_client, organization, user, config, pushes
):
    """A server whose config names a stored key keeps sending it: marking it
    signed in over OAuth is refused until the key is removed."""
    from agentcc.services.org_config_writer import write_config_version_with

    created = auth_client.post(
        CREDENTIALS,
        {"server_id": "wiki", "kind": "bearer", "value": "fake-cn24-wiki-not-real"},
        format="json",
    )
    assert created.status_code == 201, created.content

    def updater(config):
        config.mcp["servers"]["wiki"]["credential"]["kind"] = "oauth"

    with pytest.raises(OrgConfigRejected):
        write_config_version_with(organization, updater, user=user)
    console = auth_client.post(
        f"{GATEWAY}/update-mcp-server/",
        {
            "server_id": "wiki",
            "config": {
                "url": "https://mcp.wiki.example/mcp",
                "transport": "http",
                "credential": {"mode": "shared", "kind": "oauth"},
            },
        },
        format="json",
    )

    assert console.status_code == 400, console.content
    credential = _active(organization).mcp["servers"]["wiki"]["credential"]
    assert "kind" not in credential and credential["state"] == "active"


def test_the_oauth_kind_is_not_taken_off_a_signed_in_account(
    auth_client, organization, user, config, vendor, purges, pushes
):
    from agentcc.services.org_config_writer import write_config_version_with

    _connect(auth_client, vendor)

    def updater(config):
        config.mcp["servers"][CRM]["credential"].pop("kind")

    with pytest.raises(OrgConfigRejected):
        write_config_version_with(organization, updater, user=user)
    assert _active(organization).mcp["servers"][CRM]["credential"]["kind"] == "oauth"
