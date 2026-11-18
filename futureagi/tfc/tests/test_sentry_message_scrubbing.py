"""Exception values and log messages are scrubbed on every event, not only on
the Vertex credential path.

Events go through a real Sentry client with the production ``before_send``
and are checked as the serialized envelopes it would send.
"""

import json
import logging

import pytest
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from tfc.logging.sentry import _before_breadcrumb
from tfc.tests.sentry_capture import sentry_capture

LOGGER = "tfc.tests.synthetic_scrubbing"
SENTINELS = (
    "SENTINEL-pw-7f3a",
    "SENTINEL-bearer-91c2",
    "SENTINEL-key-55d0",
    "SENTINEL-private-0b1e",
)


@pytest.fixture
def transport():
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        before_breadcrumb=_before_breadcrumb,
    ) as transport:
        yield transport


def _sent(transport) -> str:
    return json.dumps(transport.events)


def _assert_no_sentinel(transport):
    sent = _sent(transport)
    for sentinel in SENTINELS:
        assert sentinel not in sent


def test_credentials_in_a_non_vertex_exception_message_are_not_sent(transport):
    try:
        raise ConnectionError(
            f"login failed: password={SENTINELS[0]} host=db.internal "
            f"Authorization: Bearer {SENTINELS[1]}"
        )
    except ConnectionError as error:
        sentry_sdk.capture_exception(error)

    assert len(transport.events) == 1
    _assert_no_sentinel(transport)
    value = transport.events[0]["exception"]["values"][-1]
    assert value["type"] == "ConnectionError"
    assert value["value"].startswith("login failed: password=[Filtered]")
    assert "host=db.internal" in value["value"]
    assert value["stacktrace"]["frames"][-1]["function"] == (
        "test_credentials_in_a_non_vertex_exception_message_are_not_sent"
    )


def test_a_credential_document_in_an_exception_message_is_not_sent(transport):
    try:
        raise ValueError(json.dumps({"private_key": SENTINELS[3], "type": "svc"}))
    except ValueError as error:
        sentry_sdk.capture_exception(error)

    _assert_no_sentinel(transport)
    assert transport.events[0]["exception"]["values"][-1]["type"] == "ValueError"


def test_credentials_in_a_log_message_are_not_sent(transport):
    logging.getLogger(LOGGER).error(
        "provider rejected api_key=%s for org %s", SENTINELS[2], "org-synthetic"
    )
    logging.getLogger(LOGGER).error(
        f"upstream call failed with x-api-key={SENTINELS[2]}"
    )

    assert len(transport.events) == 2
    _assert_no_sentinel(transport)
    logentry = transport.events[0]["logentry"]
    assert "org-synthetic" in json.dumps(logentry)
    assert "provider rejected api_key=" in json.dumps(logentry)


def test_an_ordinary_error_keeps_its_message(transport):
    try:
        raise ZeroDivisionError("division by zero in row 3 (max_tokens=4096)")
    except ZeroDivisionError as error:
        sentry_sdk.capture_exception(error)
    logging.getLogger(LOGGER).error(
        "eval batch %s failed after %d retries", "batch-7", 3
    )

    value = transport.events[0]["exception"]["values"][-1]
    assert value["value"] == "division by zero in row 3 (max_tokens=4096)"
    logentry = transport.events[1]["logentry"]
    assert logentry["message"] == "eval batch %s failed after %d retries"
    assert logentry["params"] == ["batch-7", 3]
