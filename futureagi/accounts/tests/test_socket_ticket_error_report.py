"""An error report for a socket ticket session check that could not run.

When the session check meets an error the app's error handling does not turn
into an answer, such as a database error, the ticket call ends as a server
error and the app reports it. The report names the error's class and the
place it was raised, and carries no session value: none in a frame's
variables, the request, the extras or the breadcrumbs.

The report is built as the app builds one: a real ``sentry_sdk.Client`` with
local variables and request data on, log records kept as breadcrumbs, the
app's own event scrubber and before_send, and a transport that sends nothing.
It is taken when Django signals the request's uncaught error, as the Django
integration takes it, so its frames run from the request handler to the
error. The session values stay inside one helper, which hands the test only
counts, names and a yes-or-no answer.
"""

import json
import logging
import uuid
from typing import NamedTuple
from unittest.mock import patch

import pytest
import sentry_sdk
from django.core.cache import cache
from django.core.signals import got_request_exception
from django.db import DatabaseError
from django.urls import reverse
from rest_framework.test import APIClient
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport

from accounts.models.auth_token import AuthToken
from accounts.services.token_service import issue_tokens
from tfc.logging.sentry import _event_scrubber, _get_before_send

APP_ORIGIN = "https://app.socket-ticket.test"
SOCKET_PATH = "/ws/connect/"
TICKET_BODY = {"path": SOCKET_PATH, "query": {}}
SESSION_CHECK = ("accounts.views.handoff_exchange", "authenticate")
REPORT_RECEIVER = "socket-ticket-error-report"


@pytest.fixture(autouse=True)
def isolated_ticket_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"socket-ticket-report-{uuid.uuid4()}",
        }
    }
    settings.APP_BASE_URL = APP_ORIGIN
    cache.clear()
    yield
    cache.clear()


class _SendNothing(Transport):
    def capture_envelope(self, envelope):
        pass


class _AppReports:
    """Error reports through the app's pipeline, kept here instead of sent."""

    def __init__(self):
        self.events = []
        app_before_send = _get_before_send()

        def keep(event, hint):
            event = app_before_send(event, hint)
            if event is not None:
                self.events.append(event)
            return None

        self.client = sentry_sdk.Client(
            dsn="https://public@127.0.0.1:9/1",
            transport=_SendNothing,
            default_integrations=False,
            auto_enabling_integrations=False,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=None)],
            enable_backpressure_handling=False,
            send_default_pii=True,
            include_local_variables=True,
            include_source_context=True,
            max_request_body_size="medium",
            event_scrubber=_event_scrubber(),
            before_send=keep,
        )

    def report_uncaught(self, sender, request, **kwargs):
        """Report the request's uncaught error with the request on the event."""
        served = {
            "url": f"http://testserver{request.path}",
            "method": request.method,
            "headers": dict(request.headers),
            "data": dict(TICKET_BODY),
        }

        def add_request(event, hint):
            event["request"] = served
            return event

        with sentry_sdk.new_scope() as scope:
            scope.add_event_processor(add_request)
            scope.capture_exception()


class _Seen(NamedTuple):
    """What a report shows, without any session value."""

    status_code: int
    reports: int
    error_classes: list
    session_check_variables: list
    carries_session_value: bool


def _frames(event):
    return [
        frame
        for value in (event.get("exception") or {}).get("values") or []
        for frame in (value.get("stacktrace") or {}).get("frames") or []
    ]


def _carries(text, *values):
    """Whether any of the values appears in the text, without printing them."""
    return any(value and value in text for value in values)


def _report_a_database_error_in_the_session_check(user):
    """Ask for a ticket with a signed-in session while the session lookup meets
    a database error, and return what the error reports show."""
    session = issue_tokens(user)
    reports = _AppReports()
    got_request_exception.connect(
        reports.report_uncaught, weak=False, dispatch_uid=REPORT_RECEIVER
    )
    try:
        with (
            patch.object(
                AuthToken.objects, "using", side_effect=DatabaseError("synthetic fault")
            ),
            sentry_sdk.isolation_scope() as scope,
        ):
            scope.set_client(reports.client)
            response = APIClient(raise_request_exception=False).post(
                reverse("handoff-ws-ticket"),
                TICKET_BODY,
                format="json",
                HTTP_ORIGIN=APP_ORIGIN,
                HTTP_AUTHORIZATION=f"Bearer {session['access']}",
            )
    finally:
        got_request_exception.disconnect(dispatch_uid=REPORT_RECEIVER)
        reports.client.close()

    return _Seen(
        status_code=response.status_code,
        reports=len(reports.events),
        error_classes=[
            value.get("type")
            for event in reports.events
            for value in (event.get("exception") or {}).get("values") or []
        ],
        session_check_variables=[
            sorted(frame.get("vars") or {})
            for event in reports.events
            for frame in _frames(event)
            if (frame.get("module"), frame.get("function")) == SESSION_CHECK
        ],
        carries_session_value=_carries(
            json.dumps(reports.events, default=str),
            session["access"],
            session["refresh"],
        ),
    )


@pytest.mark.django_db
def test_an_error_report_for_a_session_check_that_could_not_run_carries_class_and_place(
    user,
):
    seen = _report_a_database_error_in_the_session_check(user)

    assert seen.status_code == 500
    assert seen.reports == 1
    assert seen.error_classes == ["DatabaseError"]
    # The place: the session check's own frame, with its variables reported.
    assert seen.session_check_variables, "no frame of the session check in the report"
    assert all("request" in names for names in seen.session_check_variables)
    assert seen.carries_session_value is False, "the report carries a session value"
