"""SIEM OFF through the installed middleware, with real recognized credentials.

Restore the production middleware list (test settings normally trim it); no
middleware stubs, URLconf replacement or forced DRF authentication. Fixture writes
and middleware construction finish before counters start. The accepted contract
is the canonical DEBUG=False 404 builder plus no-store, NOT equivalence to an
arbitrary unknown path carrying bad credentials (that may still be 401/403).
"""

import hashlib
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import middleware as auth_middleware
from django.db import connections
from django.db.models.manager import BaseManager
from django.db.models.query import QuerySet
from django.http import Http404
from django.middleware.common import CommonMiddleware
from django.test import Client, RequestFactory
from django.utils import timezone
from django.views.defaults import page_not_found
from rest_framework.views import APIView

from accounts.tests.personal_keys import session_client_for
from agentcc import middleware_ee_agent_key as ee_key
from agentcc.models import AgentccAPIKey
from agentcc.models.mcp_service_provisioning import AgentccEEAgentKey
from agentcc.tests.test_siem_early_boundary import PATTERNS, ROUTES
from agentcc.urls_siem import urlpatterns
from agentcc.views.siem import _SIEMView
from tfc.settings import settings as application_settings

pytestmark = pytest.mark.integration
CREDENTIALS = [
    "none",
    "invalid-bearer",
    "invalid-gateway-bearer",
    "invalid-gateway-api-key",
    "live-bearer",
    "live-api-key",
    "revoked-bearer",
    "revoked-api-key",
    "django-session",
    "access-session",
]
ROUTE_IDS = [
    "connection",
    "destinations",
    "destination",
    "rotate",
    "deliveries",
    "retry",
    "discard",
]


@pytest.fixture
def installed_stack(settings):
    settings.DEBUG = False
    settings.APPEND_SLASH = True
    settings.AGENTCC_SIEM_EXPORT_ENABLED = False
    # test.py drops AuthMonitoringMiddleware and other installed middleware.
    # Restore the actual production chain, not a hand-written test approximation.
    settings.MIDDLEWARE = list(application_settings.MIDDLEWARE)
    middleware = list(settings.MIDDLEWARE)
    gate = middleware.index("agentcc.mcp_features.MCPSubfeatureMiddleware")
    assert gate == 1
    for name in (
        "django.middleware.common.CommonMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "tfc.middleware.auth_monitoring.AuthMonitoringMiddleware",
        "agentcc.middleware_ee_agent_key.EEAgentKeyScopeMiddleware",
    ):
        assert middleware.index(name) > gate
    assert settings.ROOT_URLCONF == "tfc.urls"
    assert [str(p.pattern) for p in urlpatterns] == PATTERNS


@pytest.fixture
def credentials(user, workspace):
    """Persist live/revoked EE bindings and a real Django session before counting."""
    raw = {}
    for status in (AgentccAPIKey.ACTIVE, AgentccAPIKey.REVOKED):
        token = f"sk-agentcc-siem-boundary-fixture-{status}"
        row = AgentccAPIKey.unscoped_objects.create(
            organization=user.organization,
            workspace=workspace,
            user=user,
            gateway_key_id=f"siem-boundary-{status}",
            name=f"SIEM boundary {status}",
            key_hash=hashlib.sha256(token.encode()).hexdigest(),
            status=status,
            expires_at=timezone.now() + timedelta(days=1),
        )
        AgentccEEAgentKey.objects.create(
            organization=user.organization,
            workspace=workspace,
            api_key=row,
        )
        # Real persisted recognition, not a mocked live_agent_key return value.
        assert AgentccAPIKey._base_manager.filter(
            key_hash=row.key_hash,
            ee_agent_key__isnull=False,
        ).exists()
        raw[status] = token
    assert ee_key.live_agent_key(raw[AgentccAPIKey.ACTIVE]) is not None
    assert ee_key.live_agent_key(raw[AgentccAPIKey.REVOKED]) is None
    session = Client()
    session.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
    assert session.session["_auth_user_id"] == str(user.pk)
    access_session = session_client_for(user, user.organization, workspace)
    return raw, session, access_session


def client_for(kind, credentials):
    raw, session, access_session = credentials
    if kind == "django-session":
        return session
    if kind == "access-session":
        return access_session
    token = {
        "none": "",
        "invalid-bearer": "invalid",
        "invalid-gateway-bearer": "sk-agentcc-invalid-boundary-fixture",
        "invalid-gateway-api-key": "sk-agentcc-invalid-boundary-fixture",
        "live-bearer": raw[AgentccAPIKey.ACTIVE],
        "live-api-key": raw[AgentccAPIKey.ACTIVE],
        "revoked-bearer": raw[AgentccAPIKey.REVOKED],
        "revoked-api-key": raw[AgentccAPIKey.REVOKED],
    }[kind]
    if not token:
        return Client()
    if kind.endswith("api-key"):
        return Client(HTTP_X_API_KEY=token)
    return Client(HTTP_AUTHORIZATION=f"Bearer {token}")


@contextmanager
def boundary_counters():
    """Spies call their originals if reached: never replace a middleware chain."""
    targets = (
        (ee_key.EEAgentKeyScopeMiddleware, "__call__"),
        (auth_middleware.AuthenticationMiddleware, "process_request"),
        (auth_middleware, "get_user"),
        (CommonMiddleware, "process_request"),
        (APIView, "initialize_request"),
        (ee_key, "live_agent_key"),
        (BaseManager, "get_queryset"),
        (QuerySet, "_fetch_all"),
        (QuerySet, "filter"),
        (QuerySet, "select_related"),
    )
    queries = []

    def record(execute, sql, params, many, context):
        queries.append(context["connection"].alias)
        return execute(sql, params, many, context)

    with ExitStack() as stack:
        spies = [
            stack.enter_context(
                patch.object(
                    obj,
                    attr,
                    autospec=True,
                    side_effect=getattr(obj, attr),
                )
            )
            for obj, attr in targets
        ]
        for connection in connections.all():
            stack.enter_context(connection.execute_wrapper(record))
        yield spies, queries


def canonical_404():
    return page_not_found(RequestFactory().get("/agentcc/unknown-route/"), Http404())


def assert_refused(response, canonical):
    assert response.status_code == 404
    assert response.content == canonical.content
    # Preserve every normal header of the actual canonical builder. Downstream
    # session/auth/CORS response processing is deliberately not executed OFF.
    for name, value in canonical.headers.items():
        assert response[name] == value
    assert response["Cache-Control"] == "no-store"
    assert "Location" not in response
    assert not response.cookies


@pytest.mark.django_db
@pytest.mark.parametrize("provisioning", [False, True], ids=["ee-off", "ee-on"])
@pytest.mark.parametrize("credential", CREDENTIALS)
@pytest.mark.parametrize("slash", [True, False], ids=["slashed", "slashless"])
@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_off_all_routes_before_auth_queries_and_ee_entry(
    route,
    slash,
    credential,
    provisioning,
    installed_stack,
    credentials,
    settings,
):
    settings.EE_SERVICE_PROVISIONING_ENABLED = provisioning
    client = client_for(credential, credentials)
    client.handler.load_middleware()
    expected = canonical_404()
    with boundary_counters() as (spies, queries):
        response = client.get(route if slash else route[:-1])
    assert_refused(response, expected)
    for spy in spies:
        spy.assert_not_called()
    assert queries == []


def test_off_installed_stack_needs_no_database(installed_stack):
    """No django_db marker: even a DB connection is forbidden by pytest-django."""
    client = Client(HTTP_X_API_KEY="sk-agentcc-invalid-boundary-fixture")
    client.handler.load_middleware()
    expected = canonical_404()
    with boundary_counters() as (spies, queries):
        response = client.get("/agentcc/siem/destinations/test-connection")
    assert_refused(response, expected)
    for spy in spies:
        spy.assert_not_called()
    assert queries == []


@pytest.mark.django_db
@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_on_canonical_reaches_existing_view_auth(route, installed_stack, settings):
    settings.AGENTCC_SIEM_EXPORT_ENABLED = True
    with patch.object(
        _SIEMView, "dispatch", autospec=True, side_effect=_SIEMView.dispatch
    ) as dispatch:
        response = Client().get(route)
    assert response.status_code in (401, 403)
    assert dispatch.call_count == 1
    assert response.get("Cache-Control") != "no-store"


@pytest.mark.django_db
@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_on_slashless_common_redirect_unchanged(route, installed_stack, settings):
    settings.AGENTCC_SIEM_EXPORT_ENABLED = True
    response = Client().get(route[:-1])
    assert response.status_code == 301
    assert response["Location"] == route


@pytest.mark.django_db
@pytest.mark.parametrize("provisioning", [False, True], ids=["ee-off", "ee-on"])
def test_unknown_bad_credentials_differential_is_intentional(
    provisioning,
    installed_stack,
    credentials,
    settings,
):
    settings.EE_SERVICE_PROVISIONING_ENABLED = provisioning
    client = client_for("live-bearer", credentials)
    # Real EE middleware, querysets and credential binding; no auth bypass.
    response = client.get("/agentcc/unknown-route/")
    assert response.status_code == (403 if provisioning else 401)
    assert_refused(client.get("/agentcc/siem/destinations/"), canonical_404())
    settings.AGENTCC_SIEM_EXPORT_ENABLED = True
    assert client.get("/agentcc/siem/destinations/").status_code == response.status_code


@pytest.mark.django_db
def test_on_valid_session_reaches_existing_list(installed_stack, credentials, settings):
    settings.AGENTCC_SIEM_EXPORT_ENABLED = True
    _, _, client = credentials
    with patch.object(
        _SIEMView, "dispatch", autospec=True, side_effect=_SIEMView.dispatch
    ) as dispatch:
        response = client.get("/agentcc/siem/destinations/")
    assert response.status_code == 200
    assert dispatch.call_count == 1
