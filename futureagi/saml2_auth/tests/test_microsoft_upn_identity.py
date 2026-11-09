"""The Microsoft sign-in callback must resolve accounts by userPrincipalName only.

The callback signs the browser in as whichever account the Graph /me profile
names. Graph's `mail` is a directory attribute any tenant admin can set to any
address, verified by nobody, and the login used the multi-tenant /common
authority, so a foreign tenant could set `mail` to a victim's address and be
signed into the victim's account (T6 #10b). The UPN suffix has to be one of
the user's tenant's verified domains, so these tests pin, per release-lead's
round-3 ruling:

- the account is found or created by the normalised UPN, never by `mail`;
- when the UPN matches no account but `mail` (or a case variant) does, the
  sign-in is refused with the generic message, never a second account;
- guest (#EXT#), missing and malformed UPNs are refused;
- the authority is /organizations (work and school accounts only);
- refusals take the existing error redirect and mint no session.

DB-free: the account table, sign-up, session minting and cache are faked, and
Microsoft's token and Graph endpoints are mocked, so nothing touches a
database or the network.
"""

from __future__ import annotations

import base64
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import RequestFactory
from structlog.testing import capture_logs

from accounts.models.user import User
from saml2_auth import views

ORGANIZATIONS_AUTHORITY = "https://login.microsoftonline.com/organizations/oauth2/v2.0"
SIGN_IN_REFUSED = (
    "We couldn't sign you in with Microsoft. Sign in with your email, "
    "or contact support."
)


def _account(email):
    return MagicMock(id=uuid.uuid4(), email=email, is_active=True)


class _FakeQuerySet:
    def __init__(self, rows):
        self._rows = rows

    def exists(self):
        return bool(self._rows)


class _FakeUserManager:
    """Just enough of User.objects for the callback: exact get, iexact filter."""

    def __init__(self, accounts):
        self.accounts = {account.email: account for account in accounts}

    def _match(self, **lookup):
        ((field, value),) = lookup.items()
        if field == "email":
            return [a for e, a in self.accounts.items() if e == value]
        if field == "email__iexact":
            return [a for e, a in self.accounts.items() if e.lower() == value.lower()]
        raise AssertionError(f"unexpected user lookup {lookup!r}")

    def get(self, **lookup):
        rows = self._match(**lookup)
        if not rows:
            raise User.DoesNotExist()
        if len(rows) > 1:
            raise User.MultipleObjectsReturned()
        return rows[0]

    def filter(self, **lookup):
        return _FakeQuerySet(self._match(**lookup))


class _FakeUserModel:
    DoesNotExist = User.DoesNotExist
    MultipleObjectsReturned = User.MultipleObjectsReturned

    def __init__(self, accounts):
        self.objects = _FakeUserManager(accounts)


@contextmanager
def _callback_harness(graph_profile, accounts):
    token_response = MagicMock(status_code=200)
    token_response.json.return_value = {"access_token": "eyJ.graph-access-token"}
    graph_response = MagicMock(status_code=200)
    graph_response.json.return_value = graph_profile
    signed_up = _account("signed-up@placeholder.test")
    auth_token_model = MagicMock()
    auth_token_model.objects.create.return_value = MagicMock(id=uuid.uuid4())
    with (
        patch.object(views.requests, "post", return_value=token_response) as post,
        patch.object(views.requests, "get", return_value=graph_response),
        patch.object(views, "User", _FakeUserModel(accounts)),
        patch.object(views, "first_signup", return_value=signed_up) as first_signup,
        patch.object(views, "AuthToken", auth_token_model),
        patch.object(
            views, "generate_encrypted_message", return_value="encrypted-session"
        ),
        patch.object(views, "cache"),
        patch.object(views, "get_mixpanel_properties", return_value={}),
        patch.object(views, "track_mixpanel_event"),
    ):
        yield post, first_signup, auth_token_model, signed_up


def _call_callback(graph_profile, accounts=()):
    request = RequestFactory().get(
        "/saml2_auth/microsoft/callback/", {"code": "authorization-code"}
    )
    request.session = {}
    with (
        capture_logs() as logs,
        _callback_harness(graph_profile, accounts) as (
            post,
            first_signup,
            auth_token_model,
            signed_up,
        ),
    ):
        response = views.MicrosoftCallbackView.as_view()(request)
    created = auth_token_model.objects.create.call_args_list
    session_user = created[0].kwargs["user"] if created else None
    return {
        "response": response,
        "location": response["Location"],
        "post": post,
        "first_signup": first_signup,
        "sessions_minted": len(created),
        "session_user": session_user,
        "signed_up": signed_up,
        "logs": logs,
    }


def _reason(location):
    encoded = parse_qs(urlsplit(location).query).get("reason", [""])[0]
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()


def _assert_refused(result):
    assert result["response"].status_code == 302
    assert result["location"].startswith(views.default_error_next_url)
    assert "sso_token=" not in result["location"]
    assert result["sessions_minted"] == 0
    result["first_signup"].assert_not_called()
    assert _reason(result["location"]) == SIGN_IN_REFUSED


def _assert_signed_in_as(result, account):
    assert result["response"].status_code == 302
    assert "sso_token=encrypted-session" in result["location"]
    assert result["sessions_minted"] == 1
    assert result["session_user"] is account


# --- the cross-tenant takeover -------------------------------------------------


def test_foreign_tenant_mail_claiming_a_victim_account_is_refused():
    victim = _account("victim@victim.test")

    result = _call_callback(
        {
            "mail": "victim@victim.test",
            "userPrincipalName": "attacker@attacker.test",
            "displayName": "Attacker",
        },
        accounts=[victim],
    )

    assert result["session_user"] is not victim
    _assert_refused(result)


def test_mail_matching_an_account_in_another_case_is_still_refused():
    victim = _account("victim@victim.test")

    result = _call_callback(
        {"mail": "Victim@Victim.Test", "userPrincipalName": "attacker@attacker.test"},
        accounts=[victim],
    )

    _assert_refused(result)


def test_upn_account_wins_over_the_account_mail_names():
    victim = _account("victim@victim.test")
    attacker = _account("attacker@attacker.test")

    result = _call_callback(
        {"mail": "victim@victim.test", "userPrincipalName": "attacker@attacker.test"},
        accounts=[victim, attacker],
    )

    _assert_signed_in_as(result, attacker)
    result["first_signup"].assert_not_called()


def test_refusal_log_names_a_reason_and_no_address():
    result = _call_callback(
        {"mail": "victim@victim.test", "userPrincipalName": "attacker@attacker.test"},
        accounts=[_account("victim@victim.test")],
    )

    refusals = [e for e in result["logs"] if e.get("event") == "oauth_identity_refused"]
    assert refusals == [
        {
            "event": "oauth_identity_refused",
            "log_level": "warning",
            "provider": "microsoft",
            "reason": "mail_matches_other_account",
        }
    ]
    rendered = repr(result["logs"])
    assert "victim@victim.test" not in rendered
    assert "attacker@attacker.test" not in rendered


# --- UPNs that cannot name an account -----------------------------------------


@pytest.mark.parametrize(
    "upn",
    [
        "victim_victim.test#EXT#@attackertenant.onmicrosoft.com",
        "victim_victim.test#ext#@attackertenant.onmicrosoft.com",
    ],
)
def test_guest_upn_is_refused(upn):
    result = _call_callback({"mail": None, "userPrincipalName": upn})

    _assert_refused(result)


def test_guest_upn_is_refused_even_when_an_account_has_that_address():
    upn = "victim_victim.test#ext#@attackertenant.onmicrosoft.com"

    result = _call_callback(
        {"mail": None, "userPrincipalName": upn}, accounts=[_account(upn)]
    )

    _assert_refused(result)


@pytest.mark.parametrize(
    "profile",
    [
        {"mail": "person@contoso.test"},
        {"mail": "person@contoso.test", "userPrincipalName": None},
        {"mail": "person@contoso.test", "userPrincipalName": ""},
        {"mail": "person@contoso.test", "userPrincipalName": "   "},
    ],
    ids=["absent", "null", "empty", "blank"],
)
def test_missing_upn_is_refused_even_when_mail_names_an_account(profile):
    result = _call_callback(profile, accounts=[_account("person@contoso.test")])

    _assert_refused(result)


@pytest.mark.parametrize(
    "upn",
    ["no-at-sign", "person@second@contoso.test", "@contoso.test", "person@"],
)
def test_upn_without_exactly_one_at_and_two_halves_is_refused(upn):
    result = _call_callback({"mail": None, "userPrincipalName": upn})

    _assert_refused(result)


# --- normal sign-in and sign-up -----------------------------------------------


def test_user_whose_upn_is_their_account_email_signs_in():
    person = _account("person@contoso.test")

    result = _call_callback(
        {"mail": "person@contoso.test", "userPrincipalName": "person@contoso.test"},
        accounts=[person],
    )

    _assert_signed_in_as(result, person)
    assert result["location"].startswith(views.default_next_url)
    result["first_signup"].assert_not_called()


def test_upn_is_stripped_and_lowercased_before_the_lookup():
    person = _account("person@contoso.test")

    result = _call_callback(
        {"mail": None, "userPrincipalName": "  Person@Contoso.Test "},
        accounts=[person],
    )

    _assert_signed_in_as(result, person)
    result["first_signup"].assert_not_called()


def test_brand_new_user_is_created_by_upn_not_mail():
    result = _call_callback(
        {
            "mail": "alias@contoso.test",
            "userPrincipalName": "Person@Contoso.Test",
            "displayName": "Person",
        }
    )

    result["first_signup"].assert_called_once()
    assert result["first_signup"].call_args.args[0] == {
        "full_name": "Person",
        "email": "person@contoso.test",
    }
    _assert_signed_in_as(result, result["signed_up"])
    assert result["location"].startswith(views.get_started_url)


def test_mailbox_less_user_is_created_by_upn():
    result = _call_callback(
        {"mail": None, "userPrincipalName": "person@contoso.test", "displayName": "P"}
    )

    assert result["first_signup"].call_args.args[0]["email"] == "person@contoso.test"
    _assert_signed_in_as(result, result["signed_up"])


def test_case_variant_of_the_upn_is_not_duplicated():
    result = _call_callback(
        {"mail": None, "userPrincipalName": "person@contoso.test"},
        accounts=[_account("Person@Contoso.test")],
    )

    _assert_refused(result)


# --- authority ----------------------------------------------------------------


def test_microsoft_authority_is_work_and_school_accounts_only():
    assert views.MICROSOFT_OAUTH_URL == ORGANIZATIONS_AUTHORITY

    result = _call_callback(
        {"mail": None, "userPrincipalName": "person@contoso.test"},
        accounts=[_account("person@contoso.test")],
    )
    assert result["post"].call_args.args[0] == f"{ORGANIZATIONS_AUTHORITY}/token"


def test_microsoft_authorization_url_uses_the_organizations_authority():
    request = RequestFactory().get("/saml2_auth/login/", {"provider": "microsoft"})

    response = views.Auth0LoginView.as_view()(request)

    assert response.status_code == 200
    url = response.data["result"]["url"]
    assert url.startswith(f"{ORGANIZATIONS_AUTHORITY}/authorize?")
