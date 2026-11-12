"""Checkout confidentiality includes unresolved URLs and every configured sink.

Each scenario is observed in what the configured sinks
emit (checkout_sinks), and an integration that consumes raw records (Sentry's
logging integration) is observed in the envelope it would send.
"""

import logging

import pytest
from django.core.exceptions import SuspiciousOperation
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import path, resolve

from tfc.logging.checkout import CheckoutLoggingMiddleware
from tfc.logging.config import configure_structlog
from tfc.tests.checkout_sentry import sentry_capture
from tfc.tests.checkout_sinks import configured_sinks

pytestmark = pytest.mark.integration

MIDDLEWARE = [
    "tfc.logging.checkout.CheckoutLoggingMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django_structlog.middlewares.RequestMiddleware",
]


def capture_sinks(name):
    return configured_sinks(name)


def assert_value_free(sinks, supplied):
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(supplied) == [], "supplied value reached a configured sink"
    assert sinks.structured(), "a production line is not one JSON document"


@override_settings(MIDDLEWARE=MIDDLEWARE, ALLOWED_HOSTS=["testserver"], DEBUG=False)
def test_real_checkout_disallowed_host_writes_no_session_to_any_sink():
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-host-boundary"
    route = f"/agentcc/managed-credits/checkout/{supplied}/"
    assert resolve(route).url_name == "agentcc-managed-credit-checkout-status"
    with capture_sinks("django.security.DisallowedHost") as (_, sinks):
        response = Client().get(route, HTTP_HOST="untrusted.invalid")
    assert response.status_code == 400
    assert_value_free(sinks, supplied)


@override_settings(MIDDLEWARE=MIDDLEWARE, DEBUG=False)
@pytest.mark.parametrize("append_slash, status", [(True, 301), (False, 404)])
@pytest.mark.parametrize("script_name", ["", "/mounted"])
def test_real_checkout_unslashed_path_is_redacted_before_resolution(
    append_slash, status, script_name
):
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-real-redirect"
    route = f"/agentcc/managed-credits/checkout/{supplied}"
    assert resolve(route + "/").url_name == "agentcc-managed-credit-checkout-status"
    with override_settings(APPEND_SLASH=append_slash):
        with capture_sinks("django_structlog.middlewares.request") as (_, sinks):
            response = Client().get(route, SCRIPT_NAME=script_name)
    assert response.status_code == status
    if append_slash:
        assert response["Location"] == script_name + route + "/"
    assert_value_free(sinks, supplied)


def rejecting_view(request, session_id):
    if request.GET.get("suspicious"):
        raise SuspiciousOperation(f"invalid checkout: {request.path}")
    return HttpResponse("unavailable", status=503)


urlpatterns = [
    path(
        "agentcc/managed-credits/checkout/<str:session_id>/",
        rejecting_view,
        name="agentcc-managed-credit-checkout-status",
    ),
    path("ordinary/<str:session_id>/", rejecting_view, name="ordinary"),
]


@override_settings(ROOT_URLCONF=__name__, MIDDLEWARE=MIDDLEWARE, DEBUG=False)
@pytest.mark.parametrize(
    "kind, source, status",
    [
        ("suspicious", "django.security.SuspiciousOperation", 400),
        ("csrf", "django.security.csrf", 403),
        ("unavailable", "django.request", 503),
    ],
)
@pytest.mark.parametrize("checkout", [True, False])
def test_rejected_requests_and_non_checkout_controls(kind, source, status, checkout):
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-rejected-path"
    prefix = "/agentcc/managed-credits/checkout" if checkout else "/ordinary"
    route = f"{prefix}/{supplied}/"
    middleware = MIDDLEWARE + ["django.middleware.csrf.CsrfViewMiddleware"]
    with override_settings(MIDDLEWARE=middleware):
        with capture_sinks(source) as (_, sinks):
            client = Client(enforce_csrf_checks=True)
            if kind == "csrf":
                response = client.post(route, {"csrfmiddlewaretoken": "invalid"})
            else:
                response = client.get(
                    route, {"suspicious": "1"} if kind == "suspicious" else {}
                )
    assert response.status_code == status
    assert sinks.emitted(), "a configured sink wrote nothing"
    if checkout:
        assert_value_free(sinks, supplied)
    else:
        assert supplied in sinks.text()


@pytest.mark.parametrize(
    "source", ["", "new.application.consumer", "django.security.NewFailure"]
)
@pytest.mark.parametrize("late", [False, True])
def test_every_logger_writes_no_session_to_its_sinks_even_without_propagation(
    source, late
):
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-new-consumer"
    request = RequestFactory().get(f"/agentcc/managed-credits/checkout/{supplied}")
    original_path = request.path
    # Created after configuration; a finite logger/handler allowlist cannot work.
    with capture_sinks(source) as (logger, sinks):

        def emit():
            logger.error("request %s", original_path, extra={"request": request})

        def respond(incoming):
            assert incoming is request
            assert incoming.path == original_path
            if not late:
                emit()
            return HttpResponse(status=503)

        response = CheckoutLoggingMiddleware(respond)(request)
        if late:
            emit()
    assert response.status_code == 503
    assert request.path == original_path
    assert_value_free(sinks, supplied)


def test_dispatch_integration_envelopes_carry_no_session(monkeypatch):
    # Sentry's logging integration inspects records around callHandlers,
    # independently of any configured handler or formatter. Its sink is the
    # envelope it would send, sanitized by the configured before-send hooks.
    import sentry_sdk

    supplied = "synthetic-dispatch-integration"
    request = RequestFactory().get(f"/agentcc/managed-credits/checkout/{supplied}/")
    with sentry_capture(monkeypatch) as (_, envelopes):
        logging.getLogger("django.security.DispatchProbe").error(
            "late %s", request.path, extra={"request": request}
        )
        sentry_sdk.flush()
    wire = b"".join(envelopes)
    assert wire, "the integration sent no event"
    assert supplied.encode() not in wire, "supplied value reached the envelope"
    assert request.path.endswith(supplied + "/")


@override_settings(ROOT_URLCONF=__name__)
@pytest.mark.parametrize("suffix", ["", "/", "//", "/not-a-route"])
def test_route_family_does_not_depend_on_successful_resolution(suffix):
    configure_structlog(cache_logger_on_first_use=False)
    supplied = "synthetic-unresolved-path"
    request = RequestFactory().get(
        f"/agentcc/managed-credits/checkout/{supplied}{suffix}"
    )
    with capture_sinks("django.request") as (_, sinks):

        def respond(incoming):
            logging.getLogger("django.request").error("failed %s", incoming.path)
            return HttpResponse(status=404)

        CheckoutLoggingMiddleware(respond)(request)
    assert_value_free(sinks, supplied)
