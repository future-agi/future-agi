"""The socket ticket call answers by what happened to the person's session.

A session issued the way sign-in issues it gets a one-time ticket from the
ticket view, and the socket redeems that ticket once. A session the check
refuses gets 401 with the view's own no-store headers. A database fault while
the session is looked up is a server error, logged once by its class name.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import DatabaseError, OperationalError
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from structlog.testing import capture_logs

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenType,
)
from accounts.models.user import User
from accounts.services.token_service import issue_tokens
from tfc.middleware.jwt_auth import JWTAuthMiddleware

APP_ORIGIN = "https://app.socket-ticket.test"
SOCKET_PATH = "/ws/connect/"
SESSION_CHECK_FAILED = "ws_ticket_session_check_failed"
# synthetic test value
SYNTHETIC_UNDECODABLE = "synthetic-value-that-is-not-a-session"
# synthetic test value
SYNTHETIC_BASIC = "c3ludGhldGljOnZhbHVl"


@pytest.fixture(autouse=True)
def isolated_ticket_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"socket-ticket-{uuid.uuid4()}",
        }
    }
    settings.APP_BASE_URL = APP_ORIGIN
    cache.clear()
    yield
    cache.clear()


def _post_ticket(authorization, client=None):
    headers = {"HTTP_ORIGIN": APP_ORIGIN}
    if authorization is not None:
        headers["HTTP_AUTHORIZATION"] = authorization
    return (client or APIClient()).post(
        reverse("handoff-ws-ticket"),
        {"path": SOCKET_PATH, "query": {}},
        format="json",
        **headers,
    )


def _assert_never_cached(response):
    assert "no-store" in response["Cache-Control"]
    assert response["Pragma"] == "no-cache"
    assert response["Referrer-Policy"] == "no-referrer"


def _access_ids(user):
    return set(
        AuthToken.objects.filter(
            user=user, auth_type=AuthTokenType.ACCESS.value
        ).values_list("pk", flat=True)
    )


def _sign_in(user):
    """The session sign-in issues, and the access row it created."""
    before = _access_ids(user)
    session = issue_tokens(user)
    (row_id,) = _access_ids(user) - before
    return session, AuthToken.objects.get(pk=row_id)


def _carries(text, *values):
    """Whether any of the values appears in the text, without printing them."""
    return any(value and value in text for value in values)


@pytest.mark.django_db(transaction=True)
def test_a_signed_in_session_gets_a_ticket_the_socket_redeems_once(user):
    session, row = _sign_in(user)
    response = _post_ticket(f"Bearer {session['access']}")

    assert response.status_code == 200
    _assert_never_cached(response)
    body = response.json()
    assert set(body) == {"ticket"}

    middleware = JWTAuthMiddleware(None)
    ticket = body["ticket"]
    redeemed = asyncio.run(middleware.get_user_from_ticket(ticket, SOCKET_PATH))
    assert redeemed is not None, "the socket refused a ticket from a signed-in session"
    socket_user, _scope, session_id, parameters = redeemed
    assert socket_user.pk == user.pk
    assert session_id == str(row.pk)
    assert parameters == {}
    spent = asyncio.run(middleware.get_user_from_ticket(ticket, SOCKET_PATH))
    assert spent is None, "a socket ticket redeemed a second time"


REFUSED_SESSIONS = [
    "no header",
    "not a bearer header",
    "a bearer header without a value",
    "a bearer header with two values",
    "a value that does not decode",
    "a session row that does not exist",
    "an ended session",
    "a refresh session",
    "a session of another person",
    "a session idle too long",
    "an inactive person",
]


def _refused_authorization(case, user):
    if case == "no header":
        return None
    if case == "not a bearer header":
        return f"Basic {SYNTHETIC_BASIC}"
    if case == "a bearer header without a value":
        return "Bearer"
    if case == "a bearer header with two values":
        return f"Bearer {SYNTHETIC_UNDECODABLE} {SYNTHETIC_UNDECODABLE}"
    if case == "a value that does not decode":
        return f"Bearer {SYNTHETIC_UNDECODABLE}"
    session, row = _sign_in(user)
    if case == "a session row that does not exist":
        row.delete()
    elif case == "an ended session":
        AuthToken.objects.filter(pk=row.pk).update(is_active=False)
    elif case == "a refresh session":
        return f"Bearer {session['refresh']}"
    elif case == "a session of another person":
        other = User.objects.create_user(
            email=f"other-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="synthetic-password-123",
            name="Other Person",
            organization=user.organization,
        )
        return "Bearer " + generate_encrypted_message(
            {"user_id": str(other.pk), "id": str(row.pk)}
        )
    elif case == "a session idle too long":
        idle_since = timezone.now() - timedelta(
            minutes=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES + 1
        )
        AuthToken.objects.filter(pk=row.pk).update(last_used_at=idle_since)
    elif case == "an inactive person":
        User.objects.filter(pk=user.pk).update(is_active=False)
    return f"Bearer {session['access']}"


@pytest.mark.django_db
@pytest.mark.parametrize("case", REFUSED_SESSIONS)
def test_a_refused_session_gets_401_with_the_view_headers(user, case):
    authorization = _refused_authorization(case, user)

    with capture_logs() as logs:
        response = _post_ticket(authorization)

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    _assert_never_cached(response)
    assert [entry for entry in logs if entry.get("event") == SESSION_CHECK_FAILED] == []
    parts = (authorization or "").split()
    credential = parts[1] if len(parts) == 2 else ""
    assert _carries(response.content.decode(), credential) is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    "fault",
    [OperationalError, DatabaseError],
    ids=["database unavailable", "database error"],
)
def test_a_fault_in_the_session_lookup_is_a_server_error(user, fault):
    session, _row = _sign_in(user)

    with (
        patch.object(AuthToken.objects, "using", side_effect=fault("synthetic fault")),
        capture_logs() as logs,
    ):
        response = _post_ticket(
            f"Bearer {session['access']}",
            client=APIClient(raise_request_exception=False),
        )

    assert 500 <= response.status_code < 600
    entries = [entry for entry in logs if entry.get("event") == SESSION_CHECK_FAILED]
    assert len(entries) == 1
    assert entries[0] == {
        "event": SESSION_CHECK_FAILED,
        "log_level": "warning",
        "error_class": fault.__name__,
    }
    written = json.dumps(logs, default=str) + response.content.decode()
    assert _carries(written, session["access"], session["refresh"]) is False
