"""A password-reset link is its own kind of token, not a login session.

Until this change the link mailed by /accounts/password-reset-initiate/ was an
ordinary ACCESS AuthToken, the same artifact as a login bearer token, and
/accounts/password-reset-confirm/ checked only that the row was an active,
recent ACCESS token whose payload named the uid. So any login (or support
impersonation) bearer token set its owner's password through the anonymous
confirm endpoint and then revoked the owner's sessions, and a reset link
authenticated API requests as its user.

These tests pin the boundary both ways, keep the legitimate reset working end
to end, and require every refused link to get one and the same answer.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from accounts.authentication import (
    decode_token,
    decrypt_message,
    generate_encrypted_message,
)
from accounts.models import User
from accounts.models.auth_token import AuthToken, AuthTokenType
from tfc.constants.roles import OrganizationRoles

OLD_PASSWORD = "testpassword123"  # conftest's user fixture
NEW_PASSWORD = "BrandNewPass123!"
OTHER_PASSWORD = "AnotherPass456!"
REFUSED_AS_BEARER = (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


@pytest.fixture(autouse=True)
def _fresh_cache():
    # Per-IP limits on initiate/login live in the cache; start every test clean.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def other_user(db, organization):
    return User.objects.create_user(
        email=f"other-{uuid.uuid4().hex[:8]}@futureagi.com",
        password=OLD_PASSWORD,
        name="Other User",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )


def _uid(user):
    return urlsafe_base64_encode(force_bytes(user.pk))


def _confirm(client, uid, token, password=NEW_PASSWORD, repeat=None):
    return client.post(
        f"/accounts/password-reset-confirm/{uid}/{token}/",
        {
            "new_password": password,
            "repeat_password": password if repeat is None else repeat,
        },
        format="json",
    )


def _request_reset_link(user):
    """The uid and token Cloud mails after forget-password-view."""
    with (
        patch("accounts.views.signup.is_oss", return_value=False),
        patch("accounts.views.signup.email_helper") as mailed,
    ):
        response = APIClient().post(
            "/accounts/password-reset-initiate/",
            {"email": user.email},
            format="json",
        )
    assert response.status_code == status.HTTP_200_OK, response.content
    context = mailed.call_args.args[2]
    return context["uid"], context["token"]


def _login(user, password=OLD_PASSWORD):
    response = APIClient().post(
        "/accounts/token/",
        {"email": user.email, "password": password},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()


def _row_id(token):
    return decrypt_message(token)["id"]


def _age(token, **delta):
    then = timezone.now() - timedelta(**delta)
    AuthToken.objects.filter(id=_row_id(token)).update(
        created_at=then, last_used_at=then
    )


def _unknown_link(user):
    """A well-formed link that names no token row: the reference refusal."""
    return generate_encrypted_message(
        {"user_id": str(user.id), "id": str(uuid.uuid4())}
    )


def _refusal(response):
    return response.status_code, response.json()


def _assert_password(user, password):
    user.refresh_from_db()
    assert check_password(password, user.password)


def _bearer(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client.get("/accounts/user-info/")


@pytest.mark.integration
@pytest.mark.api
class TestSessionTokensCannotResetPasswords:
    def test_login_access_token_is_refused_at_reset_confirm(self, api_client, user):
        access = _login(user)["access"]

        response = _confirm(api_client, _uid(user), access)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        assert _refusal(response) == _refusal(
            _confirm(api_client, _uid(user), _unknown_link(user))
        )
        _assert_password(user, OLD_PASSWORD)
        # Refused before anything else ran: the victim's session is untouched.
        assert _bearer(access).status_code == status.HTTP_200_OK

    def test_login_refresh_token_is_refused_at_reset_confirm(self, api_client, user):
        refresh = _login(user)["refresh"]

        response = _confirm(api_client, _uid(user), refresh)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        assert _refusal(response) == _refusal(
            _confirm(api_client, _uid(user), _unknown_link(user))
        )
        _assert_password(user, OLD_PASSWORD)

    def test_support_impersonation_token_is_refused_at_reset_confirm(
        self, api_client, user
    ):
        from accounts.services.sos_service import start_sos_session

        access = start_sos_session(user, source="test")["access"]

        response = _confirm(api_client, _uid(user), access)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        _assert_password(user, OLD_PASSWORD)


@pytest.mark.integration
@pytest.mark.api
class TestResetLinksDoNotAuthenticate:
    def test_reset_link_is_refused_as_bearer_token(self, api_client, user):
        uid, token = _request_reset_link(user)

        assert _bearer(token).status_code in REFUSED_AS_BEARER

        # The refusal neither consumed the link nor kept it from its purpose.
        assert _confirm(api_client, uid, token).status_code == status.HTTP_200_OK
        _assert_password(user, NEW_PASSWORD)

    @pytest.mark.parametrize("touch", [True, False])
    def test_reset_link_is_refused_by_decode_token(self, user, touch):
        _, token = _request_reset_link(user)

        with pytest.raises(AuthenticationFailed):
            decode_token(token, touch=touch)
        with pytest.raises(AuthenticationFailed):
            decode_token(token, transport="websocket", touch=touch)

    def test_reset_link_is_not_a_live_websocket_session(self, user):
        from tfc.middleware.ws_session_guard import session_is_live

        _, token = _request_reset_link(user)

        assert session_is_live(_row_id(token), user.id) is False


@pytest.mark.integration
@pytest.mark.api
class TestLegitimateReset:
    def test_request_then_confirm_resets_and_sign_in_works(self, api_client, user):
        session = _login(user)["access"]
        uid, token = _request_reset_link(user)

        response = _confirm(api_client, uid, token)

        assert response.status_code == status.HTTP_200_OK, response.content
        _assert_password(user, NEW_PASSWORD)
        assert _login(user, NEW_PASSWORD)["access"]
        old = APIClient().post(
            "/accounts/token/",
            {"email": user.email, "password": OLD_PASSWORD},
            format="json",
        )
        assert old.status_code != status.HTTP_200_OK
        # Sessions from before the reset are signed out.
        assert _bearer(session).status_code in REFUSED_AS_BEARER

    def test_reset_link_is_single_use(self, api_client, user):
        uid, token = _request_reset_link(user)
        assert _confirm(api_client, uid, token).status_code == status.HTTP_200_OK

        again = _confirm(api_client, uid, token, password=OTHER_PASSWORD)

        assert again.status_code == status.HTTP_403_FORBIDDEN, again.content
        _assert_password(user, NEW_PASSWORD)
        assert AuthToken.objects.get(id=_row_id(token)).is_active is False

    def test_a_reset_retires_the_users_other_outstanding_links(self, api_client, user):
        uid, first = _request_reset_link(user)
        _, second = _request_reset_link(user)
        assert _confirm(api_client, uid, second).status_code == status.HTTP_200_OK

        response = _confirm(api_client, uid, first, password=OTHER_PASSWORD)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        _assert_password(user, NEW_PASSWORD)

    def test_user_correctable_errors_do_not_burn_the_link(self, api_client, user):
        uid, token = _request_reset_link(user)

        mismatch = _confirm(api_client, uid, token, repeat="Different123!")
        weak = _confirm(api_client, uid, token, password="short")

        assert mismatch.status_code == status.HTTP_400_BAD_REQUEST
        assert weak.status_code == status.HTTP_400_BAD_REQUEST
        assert _confirm(api_client, uid, token).status_code == status.HTTP_200_OK
        _assert_password(user, NEW_PASSWORD)

    def test_link_inside_the_hour_the_email_promises_still_works(
        self, api_client, user
    ):
        uid, token = _request_reset_link(user)
        _age(token, minutes=59)

        assert _confirm(api_client, uid, token).status_code == status.HTTP_200_OK
        _assert_password(user, NEW_PASSWORD)


@pytest.mark.integration
@pytest.mark.api
class TestPasswordResetSessionRevocation:
    """H14-auth-security-001: remembered browsers must not undo a reset."""

    @pytest.fixture(autouse=True)
    def _external_services(self):
        with (
            patch("accounts.views.user.verify_recaptcha", return_value=True),
            patch("accounts.views.user.record_hubspot_login"),
            patch("accounts.views.user.track_mixpanel_event"),
            patch("accounts.views.signup.track_mixpanel_event"),
            patch("tfc.deployment_telemetry.events.record_event"),
        ):
            yield

    @staticmethod
    def _refresh(token):
        # jwt/utils.js sends this request without the rejected access bearer.
        return APIClient().post(
            "/accounts/token/refresh/",
            {"refresh": token, "recaptcha_response": "test-recaptcha"},
            format="json",
        )

    @pytest.mark.parametrize("warm_cache", [True, False], ids=["warm", "cold"])
    def test_remembered_browser_cannot_refresh_after_reset(self, user, warm_cache):
        session = _login(user)
        before_reset = self._refresh(session["refresh"])
        assert before_reset.status_code == status.HTTP_200_OK, before_reset.content
        refreshed_access = before_reset.json()["access"]
        assert _bearer(refreshed_access).status_code == status.HTTP_200_OK

        credentials = [
            ("access", session["access"]),
            ("access", refreshed_access),
            ("refresh", session["refresh"]),
        ]
        for kind, credential in credentials:
            key = f"{kind}_token_{_row_id(credential)}"
            assert cache.get(key) is not None
            if not warm_cache:
                cache.delete(key)

        # Browser B resets while signed out; do not sign in again yet, since
        # login separately retires old refresh rows and would mask the defect.
        uid, link = _request_reset_link(user)
        reset = _confirm(APIClient(), uid, link)
        assert reset.status_code == status.HTTP_200_OK, reset.content
        _assert_password(user, NEW_PASSWORD)

        # Browser A's rejected dashboard bearer triggers the remembered-login
        # refresh request. No replacement bearer may be issued for the retry.
        assert _bearer(session["access"]).status_code in REFUSED_AS_BEARER
        count_before_retry = AuthToken.objects.filter(user=user).count()
        retry = self._refresh(session["refresh"])
        assert retry.status_code == status.HTTP_400_BAD_REQUEST, retry.content
        assert "access" not in retry.json()
        assert AuthToken.objects.filter(user=user).count() == count_before_retry

        for kind, credential in credentials:
            row_id = _row_id(credential)
            assert AuthToken.objects.get(id=row_id).is_active is False
            assert cache.get(f"{kind}_token_{row_id}") is None
        assert _bearer(refreshed_access).status_code in REFUSED_AS_BEARER
        assert _confirm(APIClient(), uid, link).status_code == status.HTTP_403_FORBIDDEN

        # Revocation applies to old sessions, not future password-authenticated
        # sessions: the new password still permits both login and refresh.
        replacement = _login(user, NEW_PASSWORD)
        assert _bearer(replacement["access"]).status_code == status.HTTP_200_OK
        assert self._refresh(replacement["refresh"]).status_code == status.HTTP_200_OK

    def test_reset_revokes_every_refresh_but_not_another_users_session(
        self, user, other_user
    ):
        session = _login(user)
        other_session = _login(other_user)
        extra_row = AuthToken.objects.create(
            user=user,
            auth_type=AuthTokenType.REFRESH.value,
            is_active=True,
            last_used_at=timezone.now(),
        )
        extra_refresh = generate_encrypted_message(
            {"user_id": str(user.id), "id": str(extra_row.id)}
        )
        cache.set(
            f"refresh_token_{extra_row.id}",
            {"token": extra_refresh, "user": user},
            timeout=60,
        )
        assert self._refresh(extra_refresh).status_code == status.HTTP_200_OK
        uid, link = _request_reset_link(user)
        assert _confirm(APIClient(), uid, link).status_code == status.HTTP_200_OK

        for refresh in (session["refresh"], extra_refresh):
            row_id = _row_id(refresh)
            assert AuthToken.objects.get(id=row_id).is_active is False
            assert cache.get(f"refresh_token_{row_id}") is None
            assert self._refresh(refresh).status_code == status.HTTP_400_BAD_REQUEST

        for kind in ("access", "refresh"):
            row_id = _row_id(other_session[kind])
            assert AuthToken.objects.get(id=row_id).is_active is True
            assert cache.get(f"{kind}_token_{row_id}") is not None
        assert self._refresh(other_session["refresh"]).status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestForeignAndStaleLinks:
    def test_link_for_user_a_with_uid_of_user_b_is_refused(
        self, api_client, user, other_user
    ):
        _, token = _request_reset_link(user)

        response = _confirm(api_client, _uid(other_user), token)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        _assert_password(user, OLD_PASSWORD)
        _assert_password(other_user, OLD_PASSWORD)

    def test_link_whose_row_belongs_to_another_user_is_refused(
        self, api_client, user, other_user
    ):
        """The token row binds the link: B's claims over A's row are refused."""
        _, token = _request_reset_link(user)
        crossed = generate_encrypted_message(
            {"user_id": str(other_user.id), "id": _row_id(token)}
        )

        response = _confirm(api_client, _uid(other_user), crossed)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        _assert_password(other_user, OLD_PASSWORD)
        _assert_password(user, OLD_PASSWORD)

    def test_expired_link_is_refused(self, api_client, user):
        uid, token = _request_reset_link(user)
        _age(token, days=3)

        response = _confirm(api_client, uid, token)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        _assert_password(user, OLD_PASSWORD)

    def test_link_older_than_the_hour_the_email_promises_is_refused(
        self, api_client, user
    ):
        # reset_password.html: "The link is valid for 1 hour."
        uid, token = _request_reset_link(user)
        _age(token, minutes=61)

        response = _confirm(api_client, uid, token)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        _assert_password(user, OLD_PASSWORD)

    def test_inactive_link_is_refused(self, api_client, user):
        uid, token = _request_reset_link(user)
        AuthToken.objects.filter(id=_row_id(token)).update(is_active=False)

        response = _confirm(api_client, uid, token)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        _assert_password(user, OLD_PASSWORD)


@pytest.mark.integration
@pytest.mark.api
class TestOneRefusalForEveryBadLink:
    """No oracle: the answer never says which kind of bad link it was."""

    @pytest.mark.parametrize(
        "kind",
        [
            "login_access",
            "login_refresh",
            "undecryptable",
            "foreign_uid",
            "expired",
            "inactive",
            "used",
        ],
    )
    def test_every_bad_link_gets_the_unknown_link_refusal(
        self, api_client, user, other_user, kind
    ):
        uid = _uid(user)
        if kind == "login_access":
            token = _login(user)["access"]
        elif kind == "login_refresh":
            token = _login(user)["refresh"]
        elif kind == "undecryptable":
            token = "gAAAAABnot-a-real-fernet-token"
        else:
            uid, token = _request_reset_link(user)
            if kind == "foreign_uid":
                uid = _uid(other_user)
            elif kind == "expired":
                _age(token, days=3)
            elif kind == "inactive":
                AuthToken.objects.filter(id=_row_id(token)).update(is_active=False)
            elif kind == "used":
                assert _confirm(api_client, uid, token).status_code == 200

        response = _confirm(api_client, uid, token, password=OTHER_PASSWORD)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        assert _refusal(response) == _refusal(
            _confirm(api_client, uid, _unknown_link(user), password=OTHER_PASSWORD)
        )
