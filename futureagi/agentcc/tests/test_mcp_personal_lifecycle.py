"""A person's own OAuth accounts: who may manage them, and a cleanup that can
never lock anyone out of signing in again. Every request goes through the
real routes with real bearer or key authentication; the vendor and the
gateway's cache purge are local fakes, so nothing reaches a network.

- A view-only member, or a member demoted to Viewer, lists, starts and
  disconnects their own account; no shared or ambiguous write opens to them.
- A vendor revocation that keeps failing never blocks a new sign-in. The
  gateway's eviction of the old account must still finish first.
- Each cleanup step leaves a receipt. Removing a server records the same
  obligations as Disconnect, and a shared Disconnect whose eviction failed
  can be retried.
- A blank Disconnect selector is refused. Where the panel says a catalogue
  app's sign-in is not served, the start and the token fetch refuse too.

Receipts and the stored revocation outcome are read through the app registry
and ``getattr``, so this file collects on a build that has neither, and each
test fails there on what it asserts.
"""

import copy
import uuid
from collections import Counter
from unittest.mock import MagicMock, patch

import pytest
from django.apps import apps
from django.test import Client

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.services import mcp_credentials as vault
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_upstream_tokens
from agentcc.tests import test_mcp_connections as own
from agentcc.tests.mcp_upstream_oauth_helpers import (
    CALLBACK_PATH,
    ISSUER,
    SERVER_ID,
    SERVER_URL,
    START,
    FakeVendor,
    _response,
)
from agentcc.tests.test_mcp_personal_boundary import bearer, counts, key_client
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db
_settings = own._settings
config = own.config
purges = own.purges

DISCONNECT = own.DISCONNECT.format(SERVER_ID)
START_PATH = START.format(SERVER_ID)
SHARED_OAUTH = "shared-oauth"
GATEWAY = "/agentcc/gateways/default"
REPLACEMENT_ID = "lifecycle-replacement-client-id"
REPLACEMENT_SECRET = "lifecycle-replacement-client-secret-not-real"


@pytest.fixture
def pushes():
    """Every org config the gateway was sent, recorded instead of sent."""
    client = MagicMock()
    sent = []
    client.set_org_config.side_effect = lambda org_id, payload: sent.append(
        (org_id, copy.deepcopy(payload))
    )
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield sent


def _person(org, workspace, *, viewer=False):
    """A new member of ``org`` with a membership in ``workspace``: view-only
    in both when ``viewer``."""
    clear_workspace_context()
    if viewer:
        level, role = Level.VIEWER, OrganizationRoles.MEMBER_VIEW_ONLY
        ws_level, ws_role = Level.WORKSPACE_VIEWER, OrganizationRoles.WORKSPACE_VIEWER
    else:
        level, role = Level.MEMBER, OrganizationRoles.MEMBER
        ws_level, ws_role = Level.WORKSPACE_MEMBER, OrganizationRoles.WORKSPACE_MEMBER
    person = User.objects.create_user(
        email=f"lifecycle-{uuid.uuid4().hex[:10]}@example.test",
        password="lifecycle-only-password-123",
        name="Lifecycle Person",
        organization=org,
        organization_role=role,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=person, organization=org, role=role, level=level, is_active=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=person,
        workspace=workspace,
        role=ws_role,
        level=ws_level,
        organization_membership=membership,
        is_active=True,
    )
    return person


def _client(person, org, auth):
    if auth == "bearer":
        return bearer(person)
    return key_client(person, org, auth)


def _demote(person, org):
    OrganizationMembership.all_objects.filter(user=person, organization=org).update(
        level=Level.VIEWER, role=OrganizationRoles.MEMBER_VIEW_ONLY
    )
    WorkspaceMembership.all_objects.filter(user=person).update(
        level=Level.WORKSPACE_VIEWER, role=OrganizationRoles.WORKSPACE_VIEWER
    )


def _listed(client, server_id=SERVER_ID):
    """The caller's row for ``server_id``, or None when it is not listed."""
    response = client.get(own.LIST)
    assert response.status_code == 200, response.content
    rows = [
        r
        for r in response.json()["result"]["connections"]
        if r["server_id"] == server_id
    ]
    return rows[0] if rows else None


def _account(person):
    return Credential.objects.get(scope="user", kind="oauth", user=person)


def _token(org, person):
    return mcp_upstream_tokens.upstream_token(str(org.pk), str(person.pk), SERVER_ID)


def _receipts(**filters):
    """The cleanup receipts matching ``filters``; none on a build without
    receipts."""
    try:
        model = apps.get_model("prism", "AgentccMCPCleanupReceipt")
    except LookupError:
        return []
    return list(model.objects.filter(**filters).order_by("created_at"))


def _steps(receipts):
    return Counter((r.trigger, r.step, r.outcome, r.reason) for r in receipts)


def _vendor(**kwargs):
    return FakeVendor(confidential={own.ORG_CLIENT_ID: own.ORG_SECRET}, **kwargs)


# --- Property: a view-only member manages their own account --------------------------


@pytest.mark.parametrize("auth", ["bearer", "user", "mcp"])
def test_a_viewer_lists_starts_and_disconnects_their_own_account(
    organization, user, workspace, config, auth
):
    fake = FakeVendor()
    viewer = _person(organization, workspace, viewer=True)
    client = _client(viewer, organization, auth)
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        assert client.get(own.LIST).status_code == 200
        started = client.post(START_PATH, {}, format="json")
        assert started.status_code == 200, started.content
        own._connect(client, fake)
        assert _account(viewer).status == "active"
        assert _listed(client)["connected"] is True
        gone = client.delete(DISCONNECT)
    assert gone.status_code == 200, gone.content
    assert _account(viewer).status == "revoked"
    assert len(fake.revoked) == 1
    assert _token(organization, viewer).outcome == "revoked"


@pytest.mark.parametrize("auth", ["bearer", "user"])
def test_a_member_demoted_to_viewer_still_disconnects_their_own_account(
    organization, user, workspace, config, auth
):
    fake = FakeVendor()
    mate = _person(organization, workspace)
    client = _client(mate, organization, auth)
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        own._connect(client, fake)
        _demote(mate, organization)
        listed = _listed(client)
        assert listed["connected"] is True and listed["disconnect_url"]
        gone = client.delete(listed["disconnect_url"])
    assert gone.status_code == 200, gone.content
    assert _account(mate).status == "revoked"
    assert fake.revoked
    assert _token(organization, mate).outcome == "revoked"


@pytest.mark.parametrize("auth", ["bearer", "user"])
def test_a_viewer_reaches_no_shared_or_ambiguous_write(
    organization, user, workspace, config, auth
):
    fake = FakeVendor()
    viewer = _person(organization, workspace, viewer=True)
    client = _client(viewer, organization, auth)
    before = counts()
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        refused = {
            "shared_start": client.post(
                START.format(SHARED_OAUTH), {"mode": "shared"}, format="json"
            ),
            "shared_start_on_personal": client.post(
                START_PATH, {"mode": "shared"}, format="json"
            ),
            "start_mode_variant": client.post(
                START_PATH, {"mode": "SHARED"}, format="json"
            ),
            "shared_disconnect": client.delete(
                own.DISCONNECT.format(SHARED_OAUTH) + "?mode=shared"
            ),
            "disconnect_mode_variant": client.delete(DISCONNECT + "?mode=Shared"),
        }
    codes = {name: r.status_code for name, r in refused.items()}
    assert all(code in (400, 403) for code in codes.values()), codes
    for name in ("shared_start", "shared_start_on_personal", "shared_disconnect"):
        assert codes[name] == 403, codes
    assert counts() == before
    assert not fake.requests
    assert not Credential.objects.filter(kind="oauth").exists()


# --- Property: a vendor revocation never locks a person out --------------------------


@pytest.mark.parametrize("failure", ["org_app_replaced", "revocation_refused_400"])
def test_a_vendor_that_refuses_revocation_never_locks_out_a_new_sign_in(
    organization, user, workspace, config, failure
):
    app = own._org_app(organization, user)
    fake = _vendor()
    refusing = {"on": failure == "revocation_refused_400"}

    def fetch(url, **kwargs):
        if refusing["on"] and url.endswith("/revoke"):
            return _response(400, {"error": "unsupported_token_type"})
        return fake.fetch(url, **kwargs)

    mate = _person(organization, workspace)
    client = bearer(mate)
    with patch.object(connect.mcp_safe_fetch, "fetch", fetch):
        own._connect(client, fake)
        old = _account(mate)
        if failure == "org_app_replaced":
            # The documented way to change the client id: revoke, then create.
            vault.revoke_oauth_client(app, user)
            fake.confidential[REPLACEMENT_ID] = REPLACEMENT_SECRET
            vault.create_oauth_client(
                organization,
                server_id=SERVER_ID,
                client_id=REPLACEMENT_ID,
                client_secret=vault.SealedSecret(REPLACEMENT_SECRET),
                actor=user,
            )
        gone = client.delete(DISCONNECT)
        assert gone.status_code == 200, gone.content
        again = client.post(START_PATH, {}, format="json")
        assert again.status_code == 200, again.content
        refusing["on"] = False
        own._connect(client, fake)
    old.refresh_from_db()
    assert (old.status, old.oauth_revoke_pending, old.gateway_purge_pending) == (
        "revoked",
        False,
        False,
    )
    assert old.encrypted_refresh is None
    assert getattr(old, "oauth_revoke_outcome", None) == "unconfirmed"
    result = gone.json()["result"]
    assert result["cleanup_pending"] is False
    assert result.get("upstream_revocation") == "unconfirmed"
    listed = _listed(client)
    assert (listed["connected"], listed["cleanup_pending"]) == (True, False)
    assert listed.get("upstream_revocation") is None
    served = _token(organization, mate)
    assert served.outcome == "ok"
    assert served.access.reveal() == fake.issued[-1]["access_token"]
    assert served.access.reveal() != fake.issued[0]["access_token"]
    reason = "http_401" if failure == "org_app_replaced" else "http_400"
    assert _steps(_receipts(credential_id=old.pk)) == Counter(
        {
            ("disconnect", "vendor_revocation", "unconfirmed", reason): 1,
            ("disconnect", "gateway_purge", "confirmed", ""): 1,
        }
    )


@pytest.mark.parametrize("recovers", [True, False])
def test_a_vendor_outage_keeps_retrying_without_blocking_a_new_sign_in(
    organization, user, workspace, config, recovers
):
    own._org_app(organization, user)
    fake = _vendor()
    outage = {"on": True}

    def fetch(url, **kwargs):
        if outage["on"] and url.endswith("/revoke"):
            return _response(503, {"error": "temporarily_unavailable"})
        return fake.fetch(url, **kwargs)

    mate = _person(organization, workspace)
    client = bearer(mate)
    with patch.object(connect.mcp_safe_fetch, "fetch", fetch):
        own._connect(client, fake)
        old = _account(mate)
        old_refresh = fake.issued[-1]["refresh_token"]
        first = client.delete(DISCONNECT)
        listed = _listed(client)
        retry = client.delete(DISCONNECT)
        again = client.post(START_PATH, {}, format="json")
        assert first.json()["result"]["cleanup_pending"] is True
        assert listed["cleanup_pending"] is True and listed["disconnect_url"]
        assert listed["start"] == {"state": "ready", "reason": "org_oauth_app"}
        assert again.status_code == 200, again.content
        outage["on"] = not recovers
        own._connect(client, fake)
    assert first.json()["result"]["gateway_synced"] is True
    assert first.json()["result"].get("upstream_revocation") == "pending"
    assert retry.json()["result"].get("upstream_revocation") == "pending"
    assert listed.get("upstream_revocation") == "pending"
    assert listed["start_url"] == START_PATH
    old.refresh_from_db()
    assert old.oauth_revoke_pending is False and old.encrypted_refresh is None
    outcome = "confirmed" if recovers else "unconfirmed"
    assert getattr(old, "oauth_revoke_outcome", None) == outcome
    assert [f["token"] for f in fake.revoked] == ([old_refresh] if recovers else [])
    after = _listed(client)
    assert (after["connected"], after["cleanup_pending"]) == (True, False)
    served = _token(organization, mate)
    assert served.outcome == "ok"
    assert served.access.reveal() == fake.issued[-1]["access_token"]
    assert served.access.reveal() != fake.issued[0]["access_token"]
    assert _steps(_receipts(credential_id=old.pk, step="vendor_revocation")) == Counter(
        {
            ("disconnect", "vendor_revocation", "pending", "http_503"): 2,
            (
                "superseded",
                "vendor_revocation",
                outcome,
                "" if recovers else "http_503",
            ): 1,
        }
    )


def test_a_pending_gateway_eviction_still_comes_before_a_new_sign_in(
    organization, user, workspace, config
):
    fake = FakeVendor()
    mate = _person(organization, workspace)
    client = bearer(mate)
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        own._connect(client, fake)
        old = _account(mate)
        with patch.object(connect.gateway_credentials, "purge", lambda *a, **k: False):
            first = client.delete(DISCONNECT)
            blocked = client.post(START_PATH, {}, format="json")
            listed = _listed(client)
            refused = _token(organization, mate)
        retried = client.delete(DISCONNECT)
        again = client.post(START_PATH, {}, format="json")
    assert (
        first.json()["result"]["gateway_synced"],
        first.json()["result"]["cleanup_pending"],
    ) == (False, True)
    assert blocked.status_code == 409 and blocked.json()["code"] == "cleanup_pending"
    assert listed["start"] == {"state": "unavailable", "reason": "cleanup_pending"}
    assert listed["start_url"] is None and listed["disconnect_url"]
    assert refused.outcome == "revoked"
    assert retried.json()["result"]["cleanup_pending"] is False
    assert again.status_code == 200, again.content
    assert _steps(_receipts(credential_id=old.pk, step="gateway_purge")) == Counter(
        {
            ("disconnect", "gateway_purge", "pending", "purge_failed"): 1,
            ("disconnect", "gateway_purge", "confirmed", ""): 1,
        }
    )


# --- Property: removing a server leaves each person's cleanup like Disconnect -------


def test_removing_a_server_records_each_persons_cleanup_like_disconnect(
    auth_client, organization, user, workspace, config, pushes
):
    own._org_app(organization, user)
    fake = _vendor()
    mate = _person(organization, workspace)
    client = bearer(mate)
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        own._connect(client, fake)
        removed = auth_client.post(
            f"{GATEWAY}/remove-mcp-server/", {"server_id": SERVER_ID}, format="json"
        )
        assert removed.status_code == 200, removed.content
        row = _account(mate)
        assert (row.status, row.oauth_revoke_pending, row.gateway_purge_pending) == (
            "revoked",
            True,
            True,
        )
        assert row.encrypted_refresh is not None
        listed = _listed(client)
        assert listed is not None and listed["cleanup_pending"] is True
        assert listed["connected"] is False and listed["disconnect_url"]
        assert _token(organization, mate).outcome == "revoked"
        recorded = _steps(_receipts(credential_id=row.pk))
        gone = client.delete(listed["disconnect_url"])
    assert recorded == Counter(
        {
            ("server_removed", "vendor_revocation", "pending", "recorded"): 1,
            ("server_removed", "gateway_purge", "pending", "recorded"): 1,
        }
    )
    assert gone.status_code == 200, gone.content
    result = gone.json()["result"]
    assert (result["gateway_synced"], result["cleanup_pending"]) == (True, False)
    # The org app was removed with its server, so the vendor refuses a
    # revocation by the client that issued the token: recorded, not hidden.
    assert result.get("upstream_revocation") == "unconfirmed"
    row.refresh_from_db()
    assert (row.oauth_revoke_pending, row.gateway_purge_pending) == (False, False)
    assert row.encrypted_refresh is None
    assert _listed(client) is None


# --- Property: a shared Disconnect whose eviction failed is retried -----------------


def test_a_shared_disconnect_whose_eviction_failed_can_be_retried(
    organization, user, config, pushes
):
    fake = FakeVendor()
    owner = bearer(user)
    # At the vendor's URL, so the shared sign-in discovers the fake.
    own._set_server(
        organization,
        SHARED_OAUTH,
        {
            "url": SERVER_URL,
            "transport": "http",
            "credential": {"mode": "shared", "kind": "oauth"},
        },
    )
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        started = owner.post(
            START.format(SHARED_OAUTH), {"mode": "shared"}, format="json"
        )
        assert started.status_code == 200, started.content
        (name,) = [n for n in started.cookies if n.startswith(connect.COOKIE_PREFIX)]
        browser = Client()
        browser.cookies[name] = started.cookies[name].value
        code, state = fake.authorize(started.json()["result"]["authorize_url"])
        page = browser.get(CALLBACK_PATH, {"code": code, "state": state, "iss": ISSUER})
        assert page.status_code == 200, page.content
        path = own.DISCONNECT.format(SHARED_OAUTH) + "?mode=shared"
        with patch.object(connect.gateway_credentials, "purge", lambda *a, **k: False):
            first = owner.delete(path)
        retry = owner.delete(path)
    assert first.status_code == 200, first.content
    assert (
        first.json()["result"]["gateway_synced"],
        first.json()["result"]["cleanup_pending"],
    ) == (False, True)
    assert retry.status_code == 200, retry.content
    assert (
        retry.json()["result"]["gateway_synced"],
        retry.json()["result"]["cleanup_pending"],
    ) == (True, False)
    row = Credential.objects.get(server_id=SHARED_OAUTH, scope="shared", kind="oauth")
    assert row.status == "revoked" and row.gateway_purge_pending is False
    assert len(fake.revoked) == 1


# --- Property: the Disconnect selector is never blank -------------------------------


@pytest.mark.parametrize("viewer", [False, True])
def test_a_blank_disconnect_selector_is_refused(
    organization, user, workspace, config, viewer
):
    fake = FakeVendor()
    mate = _person(organization, workspace, viewer=viewer)
    client = bearer(mate)
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        own._connect(client, fake)
        blank = client.delete(DISCONNECT + "?mode=")
    assert blank.status_code == 400, blank.content
    assert _account(mate).status == "active"
    assert not fake.revoked


# --- Property: a sign-in the panel holds back is refused by the API too ------------


@pytest.mark.parametrize("ref", ["retired-vendor/retired-app@1.0.0", "asana/mcp@0.9.0"])
def test_a_catalogue_sign_in_the_panel_holds_is_refused_at_start_and_by_the_token_fetch(
    organization, user, workspace, config, ref
):
    own._org_app(organization, user)
    fake = _vendor()
    mate = _person(organization, workspace)
    newcomer = _person(organization, workspace)
    client = bearer(mate)
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        own._connect(client, fake)
        in_flight = bearer(newcomer).post(START_PATH, {}, format="json")
        assert in_flight.status_code == 200, in_flight.content
        entry = {**own._servers()[SERVER_ID], "catalog_ref": ref}
        own._set_server(organization, SERVER_ID, entry)
        before = len(fake.requests)
        listed = _listed(client)
        started = client.post(START_PATH, {}, format="json")
        fresh = bearer(newcomer).post(START_PATH, {}, format="json")
        assert len(fake.requests) == before
        token = _token(organization, mate)
        (name,) = [n for n in in_flight.cookies if n.startswith(connect.COOKIE_PREFIX)]
        browser = Client()
        browser.cookies[name] = in_flight.cookies[name].value
        code, state = fake.authorize(in_flight.json()["result"]["authorize_url"])
        page = browser.get(CALLBACK_PATH, {"code": code, "state": state, "iss": ISSUER})
        gone = client.delete(DISCONNECT)
    assert listed["start"] == {"state": "unavailable", "reason": "sign_in_not_served"}
    assert started.status_code == 409, started.content
    assert started.json()["code"] == "sign_in_not_served"
    assert fresh.status_code == 409 and fresh.json()["code"] == "sign_in_not_served"
    assert token.outcome != "ok" and token.access is None
    assert "connect_hint" not in token.body()
    assert page.status_code == 400
    assert not Credential.objects.filter(
        scope="user", kind="oauth", user=newcomer
    ).exists()
    # The account stays manageable: Disconnect still ends it.
    assert gone.status_code == 200, gone.content
    assert _account(mate).status == "revoked"
