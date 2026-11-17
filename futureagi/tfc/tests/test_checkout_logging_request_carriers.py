"""Actual-route, late request carrier and multi-value metadata at the sinks.

Each scenario is observed in what the configured sinks emit (checkout_sinks),
not in the in-memory record.
"""

from urllib.parse import urlencode

import httpx
import pytest
from django.http import QueryDict
from django.test import Client, override_settings
from django.urls import resolve
from django.utils.datastructures import MultiValueDict

from tfc.logging.config import configure_structlog
from tfc.settings.settings import MIDDLEWARE as DEPLOYED
from tfc.tests.checkout_sinks import configured_sinks, dispatch

pytestmark = pytest.mark.integration


def capture():
    return configured_sinks("checkout.request.carrier.nonpropagating.sink")


def safe(sinks, supplied, checkout=True):
    assert sinks.emitted(), "a configured sink wrote nothing"
    if checkout:
        assert sinks.leaks(supplied) == [], (
            "checkout session value reached a configured sink"
        )
        assert sinks.structured(), "a production line is not one JSON document"
    else:
        assert supplied in sinks.text(), (
            "unrelated-route diagnostic was unexpectedly lost"
        )


@pytest.mark.django_db
@pytest.mark.parametrize("suffix,status", [("", 301), ("/missing", 404), ("//", 404)])
@pytest.mark.parametrize("mounted", [False, True])
def test_real_redirect_and_unresolved_shapes(suffix, status, mounted):
    supplied = "synthetic-route-session-value"
    route = "/agentcc/managed-credits/checkout/" + supplied
    assert resolve(route + "/").url_name == "agentcc-managed-credit-checkout-status"
    configure_structlog(cache_logger_on_first_use=False)
    with configured_sinks("django_structlog.middlewares.request") as (_, sinks):
        with override_settings(
            MIDDLEWARE=DEPLOYED,
            DEBUG=False,
            APPEND_SLASH=True,
            ALLOWED_HOSTS=["testserver"],
            SECURE_SSL_REDIRECT=False,
        ):
            response = Client().get(
                route + suffix, SCRIPT_NAME="/mounted" if mounted else ""
            )
    assert response.status_code == status
    assert response.wsgi_request.path_info == route + suffix
    if status == 301:
        assert response["Location"] == ("/mounted" if mounted else "") + route + "/"
    safe(sinks, supplied)


@pytest.mark.django_db
def test_real_security_disallowed_host_before_view():
    supplied = "synthetic-security-session-value"
    route = "/agentcc/managed-credits/checkout/" + supplied + "/"
    assert resolve(route).url_name == "agentcc-managed-credit-checkout-status"
    configure_structlog(cache_logger_on_first_use=False)
    with configured_sinks("django.security.DisallowedHost") as (_, sinks):
        with override_settings(
            MIDDLEWARE=DEPLOYED,
            DEBUG=False,
            APPEND_SLASH=True,
            ALLOWED_HOSTS=["testserver"],
            SECURE_SSL_REDIRECT=False,
        ):
            response = Client().get(route, HTTP_HOST="untrusted.invalid")
    assert response.status_code == 400
    assert response.wsgi_request.path == route
    safe(sinks, supplied)


@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("carrier_kind", ["httpx-request", "multi-value", "query-dict"])
@pytest.mark.parametrize("checkout", [True, False])
def test_late_standard_request_and_multivalue_carriers(
    boundary, carrier_kind, checkout
):
    supplied = "synthetic-carrier-session-value"
    prefix = "/agentcc/managed-credits/checkout" if checkout else "/ordinary"
    route = prefix + "/" + supplied + "/"
    if carrier_kind == "httpx-request":
        carrier = httpx.Request("GET", "http://localhost" + route)
        assert supplied in repr(carrier)
    elif carrier_kind == "multi-value":
        carrier = MultiValueDict({"target": [route, "ordinary-final-value"]})
        assert list(carrier.values()) == ["ordinary-final-value"]
    else:
        carrier = QueryDict(
            urlencode([("target", route), ("target", "ordinary-final-value")])
        )
        assert list(carrier.values()) == ["ordinary-final-value"]
    original = repr(carrier)
    with capture() as (source, sinks):
        record = source.makeRecord(
            source.name,
            40,
            __file__,
            1,
            "late dependency diagnostic",
            (),
            None,
            extra={"payload": carrier},
        )
        dispatch(source, sinks, record, boundary)
    assert repr(carrier) == original, "logging mutated the application carrier"
    safe(sinks, supplied, checkout)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["httpx-request", "query-dict"])
def test_actual_response_then_late_carrier(kind):
    supplied = "synthetic-late-route-session-value"
    route = "/agentcc/managed-credits/checkout/" + supplied
    configure_structlog(cache_logger_on_first_use=False)
    assert resolve(route + "/").url_name == "agentcc-managed-credit-checkout-status"
    with override_settings(
        MIDDLEWARE=DEPLOYED,
        DEBUG=False,
        APPEND_SLASH=True,
        ALLOWED_HOSTS=["testserver"],
        SECURE_SSL_REDIRECT=False,
    ):
        response = Client().get(route)
    assert response.status_code == 301
    assert response.wsgi_request.path == route
    assert response["Location"] == route + "/"
    if kind == "httpx-request":
        carrier = httpx.Request("GET", "http://localhost" + route)
    else:
        carrier = QueryDict(
            urlencode([("path", route), ("path", "ordinary-final-value")])
        )
    with capture() as (source, sinks):
        source.error("late access metadata", extra={"payload": carrier})
    safe(sinks, supplied)
