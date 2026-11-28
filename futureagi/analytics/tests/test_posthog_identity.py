from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.urls import resolve

from analytics.posthog_util import posthog_tracker
from tfc.middleware.posthog_middleware import PostHogMiddleware
from tfc.settings.settings import default_error_next_url


@pytest.fixture
def client_spy():
    with patch.object(posthog_tracker, "client") as client:
        yield client


@pytest.fixture
def user():
    return SimpleNamespace(
        id=uuid4(),
        email="user@example.com",
        name="Example",
        is_authenticated=True,
        organization_id=uuid4(),
        default_workspace_id=uuid4(),
    )


def test_profile_uses_the_app_user_id(client_spy, user):
    posthog_tracker.identify_user(user)

    client_spy.set.assert_called_once_with(
        distinct_id=str(user.id), properties={"email": user.email, "name": user.name}
    )


def test_request_uses_the_app_user_id_and_keeps_groups(client_spy, rf, user):
    request = rf.get("/accounts/user-info/")
    request.user = user
    request.resolver_match = resolve(request.path)
    middleware = PostHogMiddleware(lambda _: HttpResponse(status=403))
    with patch("tfc.deployment_telemetry.events.record_request_event"):
        assert middleware(request).status_code == 403

    client_spy.capture.assert_called_once()
    args, kwargs = client_spy.capture.call_args
    assert args == ("api_request",)
    assert kwargs["distinct_id"] == str(user.id)
    assert kwargs["properties"]["status_code"] == 403
    assert kwargs["groups"] == {
        "organization": str(user.organization_id),
        "workspace": str(user.default_workspace_id),
    }


@pytest.mark.parametrize(
    ("path", "status", "redirect", "error_code"),
    [
        ("/accounts/signup/", 201, "", None),
        ("/accounts/token/", 401, "", "auth_request_failed"),
        ("/accounts/activate/uid/link/", 400, "", "auth_request_failed"),
        ("/saml2_auth/github/callback/", 302, "?sso_token=test", None),
        (
            "/saml2_auth/auth/callback/",
            302,
            f"{default_error_next_url}&reason=detail",
            "auth_denied",
        ),
    ],
)
def test_anonymous_auth_outcomes_only_send_template_status_and_code(
    client_spy, rf, path, status, redirect, error_code
):
    request = rf.get(path, {"code": "query-value"})
    request.user = AnonymousUser()
    request.resolver_match = resolve(path)
    response = HttpResponse("response detail", status=status)
    if redirect:
        response["Location"] = redirect

    middleware = PostHogMiddleware(lambda _: response)
    middleware._capture_api_event(request, response, 1)

    client_spy.capture.assert_called_once_with(
        "api_request",
        distinct_id=None,
        properties={
            "path": f"/{request.resolver_match.route}",
            "status_code": status,
            "error_code": error_code,
        },
        groups={},
    )


@pytest.mark.parametrize("route", ["password-reset-confirm", "accept-invitation"])
def test_link_outcomes_do_not_send_raw_paths(client_spy, rf, user, route):
    path = f"/accounts/{route}/uid/link/"
    request = rf.get(path)
    request.user = user
    response = HttpResponse(status=403)
    middleware = PostHogMiddleware(lambda _: response)

    request.resolver_match = resolve(path)
    middleware._capture_api_event(request, response, 1)
    assert client_spy.capture.call_args.kwargs["properties"] == {
        "path": f"/accounts/{route}/<uidb64>/<token>/",
        "status_code": 403,
        "error_code": "auth_request_failed",
    }

    request.resolver_match = None
    middleware._capture_api_event(request, response, 1)
    assert client_spy.capture.call_args.kwargs["properties"]["path"] == "unresolved"


def test_anonymous_capture_requires_a_resolved_auth_view(client_spy, rf):
    response = HttpResponse(status=403)
    middleware = PostHogMiddleware(lambda _: response)
    for path, matched in (
        ("/accounts/token/missing/", False),
        ("/accounts/user-info/", True),
        ("/accounts/token/", True),
    ):
        request = rf.get(path)
        request.user = AnonymousUser()
        request.resolver_match = resolve(path) if matched else None
        middleware._capture_api_event(request, response, 1)

    client_spy.capture.assert_called_once()
    properties = client_spy.capture.call_args.kwargs["properties"]
    assert properties["path"] == "/accounts/token/"
