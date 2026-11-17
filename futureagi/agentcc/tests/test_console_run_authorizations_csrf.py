"""Real-session auth and CSRF boundaries on the Console authorization URL.

A Django login cookie is not the browser bearer credential. If DRF session
authentication is enabled later, unsafe cookie-only requests must fail CSRF
before the authorization service can mint a grant.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import SESSION_KEY, get_user
from django.test import override_settings
from django.urls import resolve, reverse
from rest_framework.authentication import SessionAuthentication
from rest_framework.settings import api_settings
from rest_framework.test import APIClient
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication
from accounts.models.service_grant import ServiceGrant
from agentcc.services import console_run_auth
from agentcc.tests.test_console_run_authorizations import LISTED, register_key
from agentcc.views.console_run import ConsoleRunAuthorizationsView

pytestmark = pytest.mark.django_db

TEST_AUTHENTICATION_CLASSES = ("accounts.authentication.APIKeyAuthentication",)
CROSS_SITE_HEADERS = {
    "HTTP_ORIGIN": "https://cross-site.example",
    "HTTP_REFERER": "https://cross-site.example/attack",
}


@pytest.fixture(scope="module")
def authorization_url():
    url = reverse("agentcc-console-run-authorizations")
    assert resolve(url).func.view_class is ConsoleRunAuthorizationsView
    return url


@pytest.fixture
def session_client(user, settings):
    settings.AUTHENTICATION_BACKENDS = ("django.contrib.auth.backends.ModelBackend",)
    settings.CSRF_TRUSTED_ORIGINS = []
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
    assert user.is_active
    assert client.session[SESSION_KEY] == str(user.pk)
    assert settings.SESSION_COOKIE_NAME in client.cookies
    assert settings.CSRF_COOKIE_NAME not in client.cookies
    return client


@pytest.fixture
def console_model_listing(settings):
    settings.AGENTCC_DELEGATED_CONSOLE_RUN_ENABLED = True
    register_key()
    assert not ServiceGrant.objects.filter(kind="console-run-auth").exists()
    with patch.object(
        console_run_auth, "list_console_models", return_value=LISTED
    ) as listing:
        yield listing


def assert_cookie_only_session(response, user, settings):
    request = response.wsgi_request
    assert request.session[SESSION_KEY] == str(user.pk)
    # DRF can replace request.user with AnonymousUser after rejecting auth;
    # reload the Django user from the real session, not that DRF result.
    assert get_user(request).pk == user.pk
    assert settings.SESSION_COOKIE_NAME in request.COOKIES
    assert settings.CSRF_COOKIE_NAME not in request.COOKIES
    assert request._dont_enforce_csrf_checks is False
    for header in (
        "HTTP_AUTHORIZATION",
        "HTTP_X_API_KEY",
        "HTTP_X_SECRET_KEY",
        settings.CSRF_HEADER_NAME,
    ):
        assert header not in request.META


def test_cookie_only_session_cannot_mint_with_default_authentication(
    authorization_url, session_client, console_model_listing, user, settings
):
    assert (
        tuple(settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"])
        == TEST_AUTHENTICATION_CLASSES
    )
    assert tuple(
        type(auth) for auth in ConsoleRunAuthorizationsView().get_authenticators()
    ) == (APIKeyAuthentication,)

    response = session_client.post(
        authorization_url,
        {"models": ["gpt-4o-mini"]},
        format="json",
        secure=True,
        **CROSS_SITE_HEADERS,
    )

    assert_cookie_only_session(response, user, settings)
    assert response.status_code in (401, 403)
    assert response.data["code"] == "not_authenticated"
    assert not ServiceGrant.objects.filter(kind="console-run-auth").exists()
    console_model_listing.assert_not_called()


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        pytest.param(
            CROSS_SITE_HEADERS,
            "Origin checking failed",
            id="untrusted-origin",
        ),
        pytest.param(
            {"HTTP_REFERER": "https://cross-site.example/attack"},
            "Referer checking failed",
            id="untrusted-referer",
        ),
        pytest.param(
            {"HTTP_ORIGIN": "https://testserver"},
            "CSRF cookie not set",
            id="same-origin-without-csrf-token",
        ),
    ],
)
def test_session_authentication_enforces_csrf_before_minting(
    authorization_url,
    session_client,
    console_model_listing,
    user,
    settings,
    headers,
    reason,
):
    original_framework = settings.REST_FRAMEWORK
    original_authentication_classes = APIView.authentication_classes
    assert (
        tuple(original_framework["DEFAULT_AUTHENTICATION_CLASSES"])
        == TEST_AUTHENTICATION_CLASSES
    )
    try:
        with override_settings(
            REST_FRAMEWORK={
                **original_framework,
                "DEFAULT_AUTHENTICATION_CLASSES": (
                    *original_framework["DEFAULT_AUTHENTICATION_CLASSES"],
                    "rest_framework.authentication.SessionAuthentication",
                ),
            }
        ):
            api_settings.reload()
            # APIView also captures the default classes at import time. Refresh
            # its inherited default without replacing any authentication logic.
            with patch.object(
                APIView,
                "authentication_classes",
                api_settings.DEFAULT_AUTHENTICATION_CLASSES,
            ):
                assert tuple(
                    type(auth)
                    for auth in ConsoleRunAuthorizationsView().get_authenticators()
                ) == (APIKeyAuthentication, SessionAuthentication)
                response = session_client.post(
                    authorization_url,
                    {"models": ["gpt-4o-mini"]},
                    format="json",
                    secure=True,
                    **headers,
                )
    finally:
        # override_settings and patch restore their values even on failure;
        # discard any DRF settings cached while the override was active too.
        api_settings.reload()

    assert settings.REST_FRAMEWORK is original_framework
    assert APIView.authentication_classes is original_authentication_classes
    assert tuple(api_settings.DEFAULT_AUTHENTICATION_CLASSES) == (APIKeyAuthentication,)
    assert_cookie_only_session(response, user, settings)
    assert response.status_code == 403
    assert response.data["code"] == "permission_denied"
    assert response.data["detail"].startswith("CSRF Failed:")
    assert reason in response.data["detail"]
    assert not ServiceGrant.objects.filter(kind="console-run-auth").exists()
    console_model_listing.assert_not_called()


def test_browser_session_positive_control_mints_through_real_url(
    authorization_url, session_client, console_model_listing, user, workspace
):
    # As in the neighboring call() helper, forced auth needs the request
    # context normally supplied by bearer authentication. Only this positive
    # control supplies it; the cookie-only tests use unmodified authentication.
    session_client.force_authenticate(user=user)
    original_initialize_request = ConsoleRunAuthorizationsView.initialize_request

    def initialize_browser_request(view, request, *args, **kwargs):
        request = original_initialize_request(view, request, *args, **kwargs)
        request.organization = user.organization
        request.workspace = workspace
        request.credential_provenance = "interactive_session"
        return request

    with patch.object(
        ConsoleRunAuthorizationsView,
        "initialize_request",
        new=initialize_browser_request,
    ):
        response = session_client.post(
            authorization_url,
            {"models": ["gpt-4o-mini"]},
            format="json",
            secure=True,
        )

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    result = response.data["result"]
    assert result["run_authorization"].startswith("fe1r.")
    grant = ServiceGrant.objects.get(kind="console-run-auth")
    assert grant.org == user.organization_id
    assert grant.user == user.pk
    assert grant.workspace == workspace.pk
    assert grant.model_ids == "gpt-4o-mini"
    assert grant.ee == "ee-prod-1"
    assert grant.sub == "chat:" + result["run_id"]
    assert grant.exp == result["exp"]
    console_model_listing.assert_called_once_with(user.organization, workspace)
