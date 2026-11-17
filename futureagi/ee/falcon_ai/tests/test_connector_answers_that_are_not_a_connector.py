"""A server that answers, but not as a connector, is told to the person.

An answer of the connector protocol is a JSON-RPC 2.0 answer to the request
that was sent, carrying a result or an error; in an event stream it is the
data line whose id is the request's id. Where a person checks whether a
connector works (the connection test, discovery of its tools and the
Authenticate step), any other answer, or a result without a tool list, tells
the person one sentence: the server answered, but not as a connector. When
more than one address is tried for a connector, that sentence is told when no
address answers as a connector and one of them answered outside the protocol.
The connector is not marked verified, its tool list stays as it is, and its
last error holds that sentence. A sign-in whose tool list, after a good token
exchange, is not a connector answer shows that sentence on its page, is not
marked verified and keeps its tokens.

A tool call given such an answer, or a result without content, hands the
model the fixed failure text. A connector's error answer (its error member)
fails discovery, the connection test, the Authenticate step and a tool call
with that path's fixed text, so its own message does not reach the model; a
tool's own error result (content with isError true) reaches the model as its
text. A sign-in whose token answer carries no access token fails with the
fixed text. A connector's answer is taken as one, and a plain text answer
fails with the path's fixed text.

An answer a call cannot use is logged with no address and no part of the
answer; a connector's answer is logged with the address that answered.

No outside network: the fixtures of test_connector_calls_through_the_address_check
answer name lookups from a table and make loopback connections only.
"""

import asyncio
import json
import secrets
from unittest.mock import patch

import pytest

from ee.falcon_ai import views_connectors
from ee.falcon_ai.credentials import decrypt_falcon_token
from ee.falcon_ai.mcp_proxy import MCPConnectorProxy
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests import test_connector_calls_through_the_address_check as calls

pytestmark = pytest.mark.django_db

_environment = calls._environment
persistent_test_key = calls.persistent_test_key
self_hosted = calls.self_hosted
names = calls.names
connects = calls.connects

NOT_A_CONNECTOR = (
    "This server answered, but not as a connector. Check the server address."
)
REQUEST_FAILED = "MCP request failed"
TOOL = calls.TOOL
TOOLS_HOST = calls.TOOLS_HOST
TOOL_ANSWERED = calls.TOOL_ANSWERED
TOOL_FAILED = calls.TOOL_FAILED
TEXT_BLOCKS = [{"type": "text", "text": calls.TOOL_ANSWER}]
OUTSIDE_MARKER = "front-page-value"
TOOL_ERROR_TEXT = "No record matches the request"
CALLS = ["sync", "async"]
STREAM_HEADERS = {"Content-Type": "text/event-stream"}
NOTICE = {
    "jsonrpc": "2.0",
    "method": "notifications/message",
    "params": {"level": "info", "data": "working on it"},
}


def json_answer(payload):
    return 200, calls.JSON_HEADERS, json.dumps(payload).encode()


def request_id(request):
    return json.loads(request.body or b"{}").get("id")


def outside_the_protocol(request):
    """A JSON object that is not an answer of the connector protocol."""
    return json_answer({"status": "ok", "page": OUTSIDE_MARKER})


def plain_text(request):
    return 200, {"Content-Type": "text/plain"}, b"Welcome"


def not_found(request):
    return 404, {"Content-Type": "text/plain"}, b"Not found"


def answers_with(result):
    """An answer of the connector protocol to the request that was sent."""

    def answer(request):
        payload = {"jsonrpc": "2.0", "id": request_id(request), "result": result}
        return json_answer(payload)

    return answer


def answers_another_request(result):
    """An answer of the connector protocol to a request that was not sent."""

    def answer(request):
        payload = {"jsonrpc": "2.0", "id": "another-request", "result": result}
        return json_answer(payload)

    return answer


def a_connectors_error(request):
    """A connector's error answer: the error member and no result."""
    error = {"code": -32601, "message": "Method not found"}
    return json_answer({"jsonrpc": "2.0", "id": request_id(request), "error": error})


def a_tools_error_result(request):
    """A tool's own error: a result with content that is marked an error."""
    content = [{"type": "text", "text": TOOL_ERROR_TEXT}]
    return answers_with({"content": content, "isError": True})(request)


def two_addresses(first, second):
    """``first`` answers at /api, and ``second`` at /api/mcp."""

    def answer(request):
        if request.target == "/api":
            return first(request)
        return second(request)

    return answer


def stream_of(*lines):
    """An event stream with one data line for each of ``lines``."""
    events = "".join(f"event: message\ndata: {line}\n\n" for line in lines)
    return 200, STREAM_HEADERS, events.encode()


def a_notice_then(answer):
    """A notification, then ``answer``'s message, in one event stream."""

    def respond(request):
        _, _, body = answer(request)
        return stream_of(json.dumps(NOTICE), body.decode())

    return respond


def only_a_notice(request):
    return stream_of(json.dumps(NOTICE))


def no_json_line(request):
    return stream_of("Welcome")


def at_once(answer):
    """A connector that answers every call with ``answer``."""
    return answer


def after_a_session(answer):
    """A connector that needs a session: a call is answered with ``answer``
    once initialize has handed out a session id."""
    sessions = calls.SessionAnswer()

    def respond(request):
        method = json.loads(request.body or b"{}").get("method")
        opened = request.headers.get("mcp-session-id") in sessions.sessions
        if opened and method in ("tools/list", "tools/call"):
            return answer(request)
        return sessions(request)

    return respond


NOT_A_TOOL_LIST = [
    pytest.param(outside_the_protocol, id="json-outside-the-protocol"),
    pytest.param(answers_with({}), id="no-tool-list"),
    pytest.param(answers_with({"tools": {"lookup": TOOL}}), id="tool-list-not-a-list"),
    pytest.param(
        answers_another_request({"tools": [TOOL]}), id="answer-to-another-request"
    ),
]

NOT_A_TOOL_ANSWER = [
    pytest.param(outside_the_protocol, id="json-outside-the-protocol"),
    pytest.param(answers_with({}), id="no-content"),
    pytest.param(
        answers_another_request({"content": TEXT_BLOCKS}),
        id="answer-to-another-request",
    ),
]

# A connector's error answer, and plain text, fail with the path's fixed text.
FIXED_FAILURES = [
    pytest.param(a_connectors_error, id="a-connectors-error"),
    pytest.param(plain_text, id="plain-text"),
]

# Where a person checks whether a connector works, with the status code each
# answers when the check fails.
CHECKS = [
    pytest.param(views_connectors.MCPConnectorTestView, 502, id="connection-test"),
    pytest.param(views_connectors.MCPConnectorDiscoverView, 502, id="discovery"),
    pytest.param(views_connectors.MCPConnectorAuthenticateView, 200, id="authenticate"),
]

# What each of those checks stores when the request fails: verified, last error.
STORED_AFTER_A_FAILED_REQUEST = {
    views_connectors.MCPConnectorTestView: (True, ""),
    views_connectors.MCPConnectorDiscoverView: (False, REQUEST_FAILED),
    views_connectors.MCPConnectorAuthenticateView: (False, "Connection failed"),
}

STREAMS_WITHOUT_A_TOOL_LIST = [
    pytest.param(only_a_notice, id="only-a-notification"),
    pytest.param(
        a_notice_then(answers_another_request({"tools": [TOOL]})),
        id="an-answer-to-another-request",
    ),
    pytest.param(no_json_line, id="no-json-line"),
]

STREAMS_WITHOUT_A_TOOL_ANSWER = [
    pytest.param(only_a_notice, id="only-a-notification"),
    pytest.param(
        a_notice_then(answers_another_request({"content": TEXT_BLOCKS})),
        id="an-answer-to-another-request",
    ),
]

# Answers a call cannot use, by the list the call reads from the result.
UNUSABLE_ANSWERS = [
    pytest.param("tools", outside_the_protocol, id="tool-list-json-outside"),
    pytest.param("tools", answers_with({}), id="no-tool-list"),
    pytest.param(
        "tools", answers_with({"tools": {"lookup": TOOL}}), id="tool-list-not-a-list"
    ),
    pytest.param("content", outside_the_protocol, id="tool-call-json-outside"),
    pytest.param("content", answers_with({}), id="no-content"),
]
FAILURE_EVENT = {"tools": "mcp_discovery_error", "content": "mcp_tool_call_error"}
FAILURE = {
    "tools": {"success": False, "error": NOT_A_CONNECTOR},
    "content": TOOL_FAILED,
}

SESSIONS = [
    pytest.param(at_once, id="no-session"),
    pytest.param(after_a_session, id="after-a-session"),
]


def call_tool(call, record):
    proxy = MCPConnectorProxy()
    if call == "async":
        return asyncio.run(proxy.execute_tool(record, "lookup", {}))
    return proxy.execute_tool_sync(record, "lookup", {})


def list_or_call(needs, call, proxy, record):
    """Discovery of the tool list, or a tool call, sync or async."""
    if needs == "content":
        if call == "async":
            return asyncio.run(proxy.execute_tool(record, "lookup", {}))
        return proxy.execute_tool_sync(record, "lookup", {})
    if call == "async":
        return asyncio.run(proxy.discover_tools(record))
    return proxy.discover_tools_sync(record)


def verified_connector(user, workspace, url):
    """A connector verified earlier, with its tool listed."""
    return calls.saved_connector(
        user,
        workspace,
        url,
        is_verified=True,
        discovered_tools=[TOOL],
        enabled_tool_names=[TOOL["name"]],
    )


def run_view(
    view, answer, names, user, workspace, record_for=verified_connector, path="/mcp"
):
    with calls.connector_server(names, answer) as (server, base):
        record = record_for(user, workspace, f"{base}{path}")
        response = calls.post_view(view, user, record)
    return record, response


def row(record):
    return MCPConnector._base_manager.get(pk=record.pk)


def mentions(data, *values):
    shown = json.dumps(data, default=str)
    return any(value in shown for value in values)


def assert_told_not_a_connector(stored):
    assert stored.is_verified is False
    assert stored.last_error == NOT_A_CONNECTOR
    assert stored.discovered_tools == [TOOL]
    assert stored.enabled_tool_names == [TOOL["name"]]


# A tool call hands the model the fixed failure text.


@pytest.mark.parametrize("answer", NOT_A_TOOL_ANSWER)
@pytest.mark.parametrize("call", CALLS)
def test_a_tool_call_answered_outside_the_connector_protocol_fails(
    call, answer, self_hosted, names, connects
):
    with calls.connector_server(names, answer) as (server, base):
        result = call_tool(call, calls.connector(f"{base}/mcp"))

    assert result == TOOL_FAILED


@pytest.mark.parametrize("call", CALLS)
def test_a_tool_call_answered_with_a_connectors_error_fails(
    call, self_hosted, names, connects
):
    with calls.connector_server(names, a_connectors_error) as (server, base):
        result = call_tool(call, calls.connector(f"{base}/mcp"))

    assert result == TOOL_FAILED


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        pytest.param(calls.tool_answer, TOOL_ANSWERED, id="connector-answer"),
        pytest.param(
            a_tools_error_result,
            {"content": TOOL_ERROR_TEXT, "is_error": True},
            id="a-tools-error-result",
        ),
        pytest.param(plain_text, TOOL_FAILED, id="plain-text"),
    ],
)
@pytest.mark.parametrize("call", CALLS)
def test_a_tool_call_hands_the_model_the_connectors_answer_or_the_failure_text(
    call, answer, expected, self_hosted, names, connects
):
    with calls.connector_server(names, answer) as (server, base):
        result = call_tool(call, calls.connector(f"{base}/mcp"))

    assert result == expected


@pytest.mark.parametrize(
    "first",
    [
        pytest.param(outside_the_protocol, id="json-outside-the-protocol"),
        pytest.param(plain_text, id="plain-text"),
    ],
)
def test_a_tool_call_reaches_the_connector_at_the_mcp_address_after_another_answer(
    first, self_hosted, names, connects
):
    answer = two_addresses(first, calls.tool_answer)
    with calls.connector_server(names, answer) as (server, base):
        result = MCPConnectorProxy().execute_tool_sync(
            calls.connector(f"{base}/api"), "lookup", {}
        )
        asked = [item.target for item in server.requests]

    assert result == TOOL_ANSWERED
    assert asked == ["/api", "/api/mcp"]


@pytest.mark.parametrize("call", CALLS)
def test_a_tool_call_reads_the_answer_after_a_notification(
    call, self_hosted, names, connects
):
    answer = a_notice_then(calls.tool_answer)
    with calls.connector_server(names, answer) as (server, base):
        result = call_tool(call, calls.connector(f"{base}/mcp"))

    assert result == TOOL_ANSWERED


@pytest.mark.parametrize("answer", STREAMS_WITHOUT_A_TOOL_ANSWER)
@pytest.mark.parametrize("call", CALLS)
def test_a_tool_call_from_a_stream_without_the_answer_fails(
    call, answer, self_hosted, names, connects
):
    with calls.connector_server(names, answer) as (server, base):
        result = call_tool(call, calls.connector(f"{base}/mcp"))

    assert result == TOOL_FAILED


# Where a person checks whether a connector works, they are told the server
# answered, but not as a connector.


@pytest.mark.parametrize("answer", NOT_A_TOOL_LIST)
def test_a_connection_test_says_the_server_answered_but_not_as_a_connector(
    answer, self_hosted, names, connects, user, workspace
):
    record, response = run_view(
        views_connectors.MCPConnectorTestView, answer, names, user, workspace
    )

    assert response.status_code == 502
    assert response.data["detail"] == NOT_A_CONNECTOR
    assert not mentions(response.data, TOOLS_HOST, OUTSIDE_MARKER)
    assert_told_not_a_connector(row(record))


@pytest.mark.parametrize("answer", NOT_A_TOOL_LIST)
def test_discovering_tools_says_the_server_answered_but_not_as_a_connector(
    answer, self_hosted, names, connects, user, workspace
):
    record, response = run_view(
        views_connectors.MCPConnectorDiscoverView, answer, names, user, workspace
    )

    assert response.status_code == 502
    assert response.data["detail"] == NOT_A_CONNECTOR
    assert not mentions(response.data, TOOLS_HOST, OUTSIDE_MARKER)
    assert_told_not_a_connector(row(record))


@pytest.mark.parametrize("answer", NOT_A_TOOL_LIST)
def test_the_authenticate_step_says_the_server_answered_but_not_as_a_connector(
    answer, self_hosted, names, connects, user, workspace
):
    record, response = run_view(
        views_connectors.MCPConnectorAuthenticateView, answer, names, user, workspace
    )

    assert response.status_code == 200
    assert response.data["status"] is False
    assert response.data["message"] == NOT_A_CONNECTOR
    assert_told_not_a_connector(row(record))


@pytest.mark.parametrize(("view", "code"), CHECKS)
def test_an_address_where_no_candidate_answers_as_a_connector_is_told(
    view, code, self_hosted, names, connects, user, workspace
):
    answer = two_addresses(outside_the_protocol, not_found)
    record, response = run_view(view, answer, names, user, workspace, path="/api")

    assert response.status_code == code
    assert response.data["status"] is False
    assert response.data["message"] == NOT_A_CONNECTOR
    assert_told_not_a_connector(row(record))


@pytest.mark.parametrize(("view", "code"), CHECKS)
def test_an_address_whose_candidates_fail_otherwise_tells_the_request_failed(
    view, code, self_hosted, names, connects, user, workspace
):
    answer = two_addresses(plain_text, not_found)
    record, response = run_view(view, answer, names, user, workspace, path="/api")

    assert response.status_code == code
    assert response.data["status"] is False
    assert response.data["message"] == REQUEST_FAILED
    stored = row(record)
    verified, last_error = STORED_AFTER_A_FAILED_REQUEST[view]
    assert stored.is_verified is verified
    assert stored.last_error == last_error
    assert stored.discovered_tools == [TOOL]


def test_discovering_tools_reads_the_answer_after_a_notification(
    self_hosted, names, connects, user, workspace
):
    record, response = run_view(
        views_connectors.MCPConnectorDiscoverView,
        a_notice_then(calls.tool_answer),
        names,
        user,
        workspace,
        calls.saved_connector,
    )

    assert response.status_code == 200
    assert response.data["discovered_count"] == 1
    stored = row(record)
    assert stored.is_verified is True
    assert stored.discovered_tools == [TOOL]


@pytest.mark.parametrize("answer", STREAMS_WITHOUT_A_TOOL_LIST)
def test_discovering_tools_from_a_stream_without_the_answer_is_told(
    answer, self_hosted, names, connects, user, workspace
):
    record, response = run_view(
        views_connectors.MCPConnectorDiscoverView, answer, names, user, workspace
    )

    assert response.status_code == 502
    assert response.data["detail"] == NOT_A_CONNECTOR
    assert_told_not_a_connector(row(record))


def test_listing_tools_without_waiting_says_the_server_answered_but_not_as_a_connector(
    self_hosted, names, connects, monkeypatch
):
    proxy, saved = calls.proxy_without_saves(monkeypatch)
    with calls.connector_server(names, outside_the_protocol) as (server, base):
        result = asyncio.run(proxy.discover_tools(calls.connector(f"{base}/mcp")))

    assert result == {"success": False, "error": NOT_A_CONNECTOR}
    assert saved.errors == [NOT_A_CONNECTOR]
    assert saved.discoveries == []


# The log of an answer a call cannot use carries no address and no part of the
# answer; a connector's answer is logged with the address that answered.


@pytest.mark.parametrize("session", SESSIONS)
@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize(("needs", "answer"), UNUSABLE_ANSWERS)
def test_an_answer_a_call_cannot_use_is_logged_without_the_address_or_the_answer(
    needs, answer, call, session, self_hosted, names, connects, monkeypatch
):
    proxy, _ = calls.proxy_without_saves(monkeypatch)
    with calls.connector_server(names, session(answer)) as (server, base):
        record = calls.connector(f"{base}/mcp")
        with patch("ee.falcon_ai.mcp_proxy.logger") as logger:
            outcome = list_or_call(needs, call, proxy, record)

    logged = str(logger.mock_calls)
    calls.check(TOOLS_HOST not in logged, "the log names the connector's address")
    calls.check("mcp_endpoint_success" not in logged, "logged as a success")
    for value in (OUTSIDE_MARKER, TOOL["description"]):
        calls.check(value not in logged, "the log carries the server's answer")
    assert FAILURE_EVENT[needs] in logged
    assert outcome == FAILURE[needs]


@pytest.mark.parametrize("session", SESSIONS)
@pytest.mark.parametrize("call", CALLS)
def test_a_connectors_answer_is_logged_with_the_address_that_answered(
    call, session, self_hosted, names, connects
):
    with calls.connector_server(names, session(calls.tool_answer)) as (server, base):
        record = calls.connector(f"{base}/mcp")
        with patch("ee.falcon_ai.mcp_proxy.logger") as logger:
            result = call_tool(call, record)

    assert result == TOOL_ANSWERED
    logger.debug.assert_any_call(
        "mcp_endpoint_success", url=f"{base}/mcp", connector=record.name
    )


# A connector's error answer and plain text fail with the path's fixed text.


@pytest.mark.parametrize("answer", FIXED_FAILURES)
def test_a_connection_test_answered_with_an_error_or_plain_text_fails(
    answer, self_hosted, names, connects, user, workspace
):
    record, response = run_view(
        views_connectors.MCPConnectorTestView, answer, names, user, workspace
    )

    assert response.status_code == 502
    assert response.data["detail"] == REQUEST_FAILED
    stored = row(record)
    assert stored.is_verified is True
    assert stored.last_error == ""
    assert stored.discovered_tools == [TOOL]


@pytest.mark.parametrize("answer", FIXED_FAILURES)
def test_discovering_tools_answered_with_an_error_or_plain_text_fails(
    answer, self_hosted, names, connects, user, workspace
):
    record, response = run_view(
        views_connectors.MCPConnectorDiscoverView, answer, names, user, workspace
    )

    assert response.status_code == 502
    assert response.data["detail"] == REQUEST_FAILED
    stored = row(record)
    assert stored.is_verified is False
    assert stored.last_error == REQUEST_FAILED
    assert stored.discovered_tools == [TOOL]


@pytest.mark.parametrize("answer", FIXED_FAILURES)
def test_the_authenticate_step_answered_with_an_error_or_plain_text_fails(
    answer, self_hosted, names, connects, user, workspace
):
    record, response = run_view(
        views_connectors.MCPConnectorAuthenticateView, answer, names, user, workspace
    )

    assert response.status_code == 200
    assert response.data["status"] is False
    assert response.data["message"] == REQUEST_FAILED
    stored = row(record)
    assert stored.is_verified is False
    assert stored.last_error == "Connection failed"
    assert stored.discovered_tools == [TOOL]


# A connector answer is taken as one.


def test_a_connection_test_answered_by_a_connector_succeeds(
    self_hosted, names, connects, user, workspace
):
    record, response = run_view(
        views_connectors.MCPConnectorTestView,
        calls.tool_answer,
        names,
        user,
        workspace,
        calls.saved_connector,
    )

    assert response.status_code == 200
    assert response.data["result"] == {"success": True, "status_code": 200}


def test_discovering_tools_answered_by_a_connector_lists_them(
    self_hosted, names, connects, user, workspace
):
    record, response = run_view(
        views_connectors.MCPConnectorDiscoverView,
        calls.tool_answer,
        names,
        user,
        workspace,
        calls.saved_connector,
    )

    assert response.status_code == 200
    assert response.data["discovered_count"] == 1
    stored = row(record)
    assert stored.is_verified is True
    assert stored.discovered_tools == [TOOL]


def test_the_authenticate_step_answered_by_a_connector_connects(
    self_hosted, names, connects, user, workspace
):
    record, response = run_view(
        views_connectors.MCPConnectorAuthenticateView,
        calls.tool_answer,
        names,
        user,
        workspace,
        calls.saved_connector,
    )

    assert response.status_code == 200
    assert response.data["message"] == "Connected successfully"
    stored = row(record)
    assert stored.is_verified is True
    assert stored.discovered_tools == [TOOL]


def test_listing_tools_without_waiting_lists_a_connectors_tools(
    self_hosted, names, connects, monkeypatch
):
    proxy, saved = calls.proxy_without_saves(monkeypatch)
    with calls.connector_server(names) as (server, base):
        result = asyncio.run(proxy.discover_tools(calls.connector(f"{base}/mcp")))

    assert result == {"success": True, "tools": [TOOL], "count": 1}
    assert saved.discoveries == [[TOOL]]


# A sign-in needs a token answer that carries an access token, and a tool list
# that is a connector answer.


def token_address_answers(token_answer, connector_answer):
    """``token_answer`` answers at the token address, and ``connector_answer``
    at the connector's address."""

    def answer(request):
        if request.target == "/token":
            return token_answer(request)
        return connector_answer(request)

    return answer


def no_access_token(request):
    return json_answer({"token_type": "Bearer", "expires_in": 3600})


def an_access_token(request):
    return json_answer({"access_token": calls.FRESH_ACCESS, "expires_in": 3600})


def sign_in(token_answer, names, user, workspace, connector_answer=calls.tool_answer):
    """The sign-in callback for a connector with its tool listed."""
    state = secrets.token_urlsafe(16)
    answer = token_address_answers(token_answer, connector_answer)
    with calls.connector_server(names, answer) as (server, base):
        record = calls.saved_connector(
            user,
            workspace,
            f"{base}/mcp",
            discovered_tools=[TOOL],
            enabled_tool_names=[TOOL["name"]],
            **calls.oauth_fields(
                f"{base}/token",
                oauth_state=state,
                oauth_code_verifier=secrets.token_urlsafe(48),
            ),
        )
        response = calls.callback(record, state)
    return record, response


@pytest.mark.parametrize(
    "token_answer",
    [
        pytest.param(outside_the_protocol, id="json-outside-the-protocol"),
        pytest.param(no_access_token, id="no-access-token"),
        pytest.param(plain_text, id="plain-text"),
    ],
)
def test_a_sign_in_whose_token_answer_carries_no_access_token_fails(
    token_answer, self_hosted, names, connects, user, workspace
):
    record, response = sign_in(token_answer, names, user, workspace)

    assert response.status_code == 502
    assert b"Token exchange failed" in response.content
    stored = row(record)
    assert stored.is_verified is False
    calls.check(stored.oauth_access_token == "", "an access token was saved")


def test_a_sign_in_whose_token_answer_carries_an_access_token_connects(
    self_hosted, names, connects, user, workspace
):
    record, response = sign_in(an_access_token, names, user, workspace)

    assert response.status_code == 200
    assert b"Connected successfully" in response.content
    assert row(record).is_verified is True


def test_a_sign_in_to_a_server_that_answers_but_not_as_a_connector_is_told(
    self_hosted, names, connects, user, workspace
):
    record, response = sign_in(
        an_access_token, names, user, workspace, outside_the_protocol
    )

    assert response.status_code == 200
    assert NOT_A_CONNECTOR.encode() in response.content
    assert b"status: 'error'" in response.content
    assert b"Connected successfully" not in response.content
    stored = row(record)
    assert_told_not_a_connector(stored)
    calls.check(
        decrypt_falcon_token(stored.oauth_access_token) == calls.FRESH_ACCESS,
        "the sign-in's access token is not kept",
    )


def test_a_sign_in_whose_tool_list_fails_otherwise_connects(
    self_hosted, names, connects, user, workspace
):
    record, response = sign_in(an_access_token, names, user, workspace, plain_text)

    assert response.status_code == 200
    assert b"Connected successfully" in response.content
    stored = row(record)
    assert stored.is_verified is True
    assert stored.last_error == ""
