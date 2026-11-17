"""The request-logging path field is redacted by route match before logging.

Source fix, defence in depth, for the checkout
status route (agentcc/urls_billing.py, checkout/<str:session_id>/):
- django_structlog request_started / request_finished / request_failed: the
  "request" field, as handed to the logger (structlog capture_logs replaces
  every processor, so nothing downstream can repair it);
- the Granian access log record, as built by Granian's own log function;
- the runserver access log record (django.server).
Other routes keep their fields unchanged.
"""

import logging
import time

import pytest
import structlog
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import path

from tfc.settings.settings import MIDDLEWARE as DEPLOYED

pytestmark = pytest.mark.integration
SUPPLIED = "synthetic-request-field-session-0428"
CHECKOUT = "/agentcc/managed-credits/checkout/" + SUPPLIED
REQUEST_EVENTS = ("request_started", "request_finished", "request_failed")


def status_view(request, session_id):
    if request.GET.get("fail"):
        raise RuntimeError("synthetic failure")
    return HttpResponse("ok")


urlpatterns = [
    path(
        "agentcc/managed-credits/checkout/<str:session_id>/",
        status_view,
        name="agentcc-managed-credit-checkout-status",
    ),
    path("ordinary/<str:session_id>/", status_view, name="ordinary"),
]


def request_events(route, *, query=None, script_name=""):
    with override_settings(
        ROOT_URLCONF=__name__,
        MIDDLEWARE=DEPLOYED,
        DEBUG=False,
        APPEND_SLASH=True,
        ALLOWED_HOSTS=["testserver"],
        SECURE_SSL_REDIRECT=False,
    ):
        client = Client(raise_request_exception=False)
        with structlog.testing.capture_logs() as events:
            response = client.get(route, query or {}, SCRIPT_NAME=script_name)
    return response, [event for event in events if event["event"] in REQUEST_EVENTS]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "shape, route, query, script_name, status, names",
    [
        ("slashed", CHECKOUT + "/", None, "", 200, REQUEST_EVENTS[:2]),
        ("unslashed", CHECKOUT, None, "", 301, REQUEST_EVENTS[:2]),
        ("query", CHECKOUT + "/", {"x": "1"}, "", 200, REQUEST_EVENTS[:2]),
        ("mounted", CHECKOUT + "/", None, "/mounted", 200, REQUEST_EVENTS[:2]),
        ("failed", CHECKOUT + "/", {"fail": "1"}, "", 500, REQUEST_EVENTS[::2]),
    ],
)
def test_request_events_name_no_checkout_session(
    shape, route, query, script_name, status, names
):
    response, events = request_events(route, query=query, script_name=script_name)
    assert response.status_code == status
    assert [event["event"] for event in events] == list(names)
    for event in events:
        field = event["request"]
        assert SUPPLIED not in field, (shape, event["event"], field)
        assert "/agentcc/managed-credits/checkout/[redacted]" in field
        assert field.startswith("GET " + script_name)
    if query:
        suffix = "?" + next(iter(query)) + "=1"
        assert all(event["request"].endswith(suffix) for event in events)


@pytest.mark.django_db
def test_unrelated_request_events_are_unchanged():
    route = "/ordinary/" + SUPPLIED + "/"
    response, events = request_events(route)
    assert response.status_code == 200
    assert [event["request"] for event in events] == ["GET " + route] * 2


class Recorder(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def delivered(name, emit):
    """Records as the access logger delivers them to its first handler."""
    logger = logging.getLogger(name)
    recorder = Recorder()
    saved = (logger.handlers[:], logger.propagate, logger.level)
    logger.handlers = [recorder]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        emit()
    finally:
        logger.handlers, logger.propagate, level = saved
        logger.setLevel(level)
    return recorder.records


def granian_request(route):
    from granian.log import DEFAULT_ACCESSLOG_FMT, log_request_builder

    request = {
        "addr_remote": "127.0.0.1",
        "path": route,
        "qs": "",
        "method": "GET",
        "scheme": "http",
        "protocol": "HTTP/1.1",
        "headers": lambda name: None,
    }
    log = log_request_builder(DEFAULT_ACCESSLOG_FMT)
    log(time.time(), time.perf_counter(), request, 301)


@pytest.mark.parametrize("checkout", [True, False])
def test_granian_access_record_path(checkout):
    route = CHECKOUT if checkout else "/ordinary/" + SUPPLIED
    (record,) = delivered("granian.access", lambda: granian_request(route))
    assert type(record.args) is dict
    if checkout:
        assert record.args["path"] == "/agentcc/managed-credits/checkout/[redacted]"
        assert SUPPLIED not in str(record.args)
    else:
        assert record.args["path"] == route
    assert record.args["status"] == 301


@pytest.mark.parametrize("checkout", [True, False])
def test_runserver_access_record_request_line(checkout):
    route = CHECKOUT + "/" if checkout else "/ordinary/" + SUPPLIED + "/"
    line = "GET " + route + " HTTP/1.1"
    (record,) = delivered(
        "django.server",
        lambda: logging.getLogger("django.server").info(
            '"%s" %s %s', line, "200", "2", extra={"status_code": 200}
        ),
    )
    if checkout:
        redacted = "GET /agentcc/managed-credits/checkout/[redacted]/ HTTP/1.1"
        assert record.args[0] == redacted
        assert SUPPLIED not in str(record.args)
    else:
        assert record.args[0] == line
    assert record.args[1:] == ("200", "2")
