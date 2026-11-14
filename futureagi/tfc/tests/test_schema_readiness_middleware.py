import logging
from unittest.mock import Mock, patch

import pytest
from django.db import connection
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings

from tfc.middleware.schema_readiness import SchemaReadinessMiddleware
from tfc.schema_readiness import SchemaReadinessChecker, SchemaState
from tfc.settings.settings import MIDDLEWARE as DEPLOYED_MIDDLEWARE
from tfc.settings.settings import STARTUP_SCHEMA_GUARD_ENABLED

CHECK = "tfc.middleware.schema_readiness.schema_readiness.check"
GUARD = "tfc.middleware.schema_readiness.SchemaReadinessMiddleware"


@pytest.fixture
def deployed_client():
    with override_settings(
        MIDDLEWARE=DEPLOYED_MIDDLEWARE,
        STARTUP_SCHEMA_GUARD_ENABLED=True,
        CORS_ALLOW_ALL_ORIGINS=False,
        CORS_ALLOWED_ORIGINS=["https://app.example.com"],
    ):
        yield Client()


def test_guard_is_installed_before_session_auth_and_other_db_middleware():
    assert GUARD in DEPLOYED_MIDDLEWARE
    position = DEPLOYED_MIDDLEWARE.index(GUARD)
    assert DEPLOYED_MIDDLEWARE[:position] == [
        "django.middleware.security.SecurityMiddleware",
        "whitenoise.middleware.WhiteNoiseMiddleware",
        "corsheaders.middleware.CorsMiddleware",
        "tfc.middleware.cors_origin.OriginCheckMiddleware",
    ]
    for middleware in (
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "accounts.authentication.AuthMonitoringMiddleware",
    ):
        assert position < DEPLOYED_MIDDLEWARE.index(middleware)


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/agentcc/org-configs/bulk/"),
        ("get", "/agentcc/api-keys/bulk/"),
        ("get", "/api/setup-checks/"),
        ("post", "/accounts/signup/"),
        ("post", "/accounts/token/"),
    ],
)
def test_pending_returns_retryable_envelope_before_session_or_auth(
    deployed_client, method, path
):
    with (
        patch(CHECK, return_value=SchemaState.PENDING),
        patch(
            "django.contrib.sessions.middleware.SessionMiddleware.process_request",
            side_effect=AssertionError(
                "Session middleware reached before schema readiness"
            ),
        ) as session,
        patch(
            "django.contrib.auth.middleware.AuthenticationMiddleware.process_request",
            side_effect=AssertionError(
                "Authentication reached before schema readiness"
            ),
        ) as auth,
    ):
        response = getattr(deployed_client, method)(
            path, HTTP_ORIGIN="https://app.example.com"
        )

    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert response["Access-Control-Allow-Origin"] == "https://app.example.com"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response.json() == {
        "status": False,
        "type": "service_unavailable",
        "code": "initializing",
        "detail": "The platform is still initializing; retry shortly.",
        "message": "The platform is still initializing; retry shortly.",
        "error": "The platform is still initializing; retry shortly.",
        "result": "The platform is still initializing; retry shortly.",
    }
    session.assert_not_called()
    auth.assert_not_called()


@pytest.mark.django_db
def test_health_still_answers_without_checking_schema(deployed_client):
    with patch(CHECK, return_value=SchemaState.PENDING) as check:
        response = deployed_client.get("/health/")

    assert response.status_code == 200
    assert response.json()["status"] is True
    assert "Retry-After" not in response
    check.assert_not_called()


def test_cors_preflight_still_answers_without_checking_schema(deployed_client):
    with patch(CHECK, return_value=SchemaState.PENDING) as check:
        response = deployed_client.options(
            "/agentcc/api-keys/bulk/",
            HTTP_ORIGIN="https://app.example.com",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )

    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == "https://app.example.com"
    assert "GET" in response["Access-Control-Allow-Methods"]
    check.assert_not_called()


@override_settings(STARTUP_SCHEMA_GUARD_ENABLED=True)
def test_options_that_is_not_a_preflight_waits_for_the_schema():
    # DRF authenticates OPTIONS too, which reads tables that may not exist yet.
    downstream = Mock(return_value=HttpResponse("options response"))
    request = RequestFactory().options(
        "/agentcc/api-keys/bulk/", HTTP_X_API_KEY="org-api-key"
    )
    with patch(CHECK, return_value=SchemaState.PENDING):
        response = SchemaReadinessMiddleware(downstream)(request)

    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    downstream.assert_not_called()


def test_pending_responses_are_not_logged_as_errors(deployed_client):
    request_logger = logging.getLogger("django.request")
    with (
        patch(CHECK, return_value=SchemaState.PENDING),
        patch.object(request_logger, "error") as error,
        patch.object(request_logger, "warning") as warning,
        patch.object(request_logger, "debug") as debug,
    ):
        response = deployed_client.get("/agentcc/api-keys/bulk/")

    assert response.status_code == 503
    error.assert_not_called()
    warning.assert_not_called()
    debug.assert_called_once()


@pytest.mark.django_db
def test_unavailable_preserves_setup_diagnostics(deployed_client):
    from tfc.views.setup_checks import CHECKS

    probes = {check["id"]: True for check in CHECKS}
    probes["database"] = False
    with (
        patch(CHECK, return_value=SchemaState.UNAVAILABLE),
        patch("tfc.views.setup_checks.is_oss", return_value=True),
        patch("tfc.views.setup_checks._run_probes", return_value=probes),
    ):
        response = deployed_client.get("/api/setup-checks/")

    assert response.status_code == 200
    row = next(c for c in response.json()["result"]["checks"] if c["id"] == "database")
    assert row["status"] == "failed"
    assert "Retry-After" not in response


@pytest.mark.django_db
def test_real_missing_recorder_blocks_bulk_then_recovers_in_the_same_process(
    deployed_client,
):
    checker = SchemaReadinessChecker()
    clock = Mock(return_value=0)
    # The rename rolls back with the test transaction, even if an assertion fails.
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE django_migrations RENAME TO guard_saved_migrations")
    with (
        patch("tfc.middleware.schema_readiness.schema_readiness", checker),
        patch("tfc.schema_readiness.monotonic", clock),
    ):
        pending = deployed_client.get("/agentcc/api-keys/bulk/")
        assert pending.status_code == 503
        assert pending.json()["code"] == "initializing"
        assert deployed_client.get("/health/").json()["status"] is True

        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE guard_saved_migrations RENAME TO django_migrations"
            )
        clock.return_value = 5
        recovered = deployed_client.get("/agentcc/api-keys/bulk/")

    assert checker.check() is SchemaState.READY
    assert recovered.status_code == 403
    assert recovered.json()["code"] == "permission_denied"
    assert "Retry-After" not in recovered


@pytest.mark.parametrize("state", [SchemaState.READY, SchemaState.UNAVAILABLE])
@override_settings(STARTUP_SCHEMA_GUARD_ENABLED=True)
def test_non_pending_passes_the_same_request_and_response_through(state):
    downstream = Mock(return_value=HttpResponse("diagnostic response", status=200))
    request = RequestFactory().get("/api/setup-checks/")
    with patch(CHECK, return_value=state):
        response = SchemaReadinessMiddleware(downstream)(request)

    assert response is downstream.return_value
    downstream.assert_called_once_with(request)


def test_disabled_by_default_does_not_check_or_change_requests():
    assert STARTUP_SCHEMA_GUARD_ENABLED is False
    downstream = Mock(return_value=HttpResponse("normal response"))
    request = RequestFactory().get("/api/setup-checks/")
    with override_settings(STARTUP_SCHEMA_GUARD_ENABLED=False), patch(CHECK) as check:
        response = SchemaReadinessMiddleware(downstream)(request)

    assert response is downstream.return_value
    downstream.assert_called_once_with(request)
    check.assert_not_called()
