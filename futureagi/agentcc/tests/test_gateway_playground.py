"""The console's Try it request: POST /agentcc/gateways/{id}/test-playground/
takes optional temperature, max_tokens and top_p, and test-playground-stream
relays the same request to the console as server-sent events."""

import asyncio
import json
import select
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest
from django.core.handlers.asgi import ASGIHandler
from rest_framework import status

from agentcc.services.gateway_client import GatewayClient, GatewayClientError

GATEWAY = "/agentcc/gateways/default"
TEST_ADMIN = "test_only_playground_admin_pair_00000000"


@pytest.fixture(autouse=True)
def delegated_contract(settings):
    settings.AGENTCC_DELEGATED_PLAYGROUND_ENABLED = True
    settings.SECRET_KEY = "test_only_playground_signing_pair_00000000"


def _assert_no_key(client):
    client.create_key.assert_not_called()
    client.revoke_key.assert_not_called()
    client.send_chat_completion.assert_not_called()


def _client():
    client = MagicMock()
    client.send_delegated_chat_completion.return_value = (
        200,
        {"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
        {"x-agentcc-request-id": "req_1", "content-type": "application/json"},
    )
    return client


class _FakeStream:
    """An httpx streaming response: status, headers and SSE lines."""

    def __init__(self, status_code, headers, lines=(), body=b""):
        self.status_code = status_code
        self.headers = headers
        self._lines = list(lines)
        self._body = body

    def iter_lines(self):
        yield from self._lines

    def read(self):
        return self._body

    def json(self):
        return json.loads(self._body) if self._body else {}


def _streaming(response, sent=None):
    @contextmanager
    def stream_delegated_chat_completion(delegation, **kwargs):
        if sent is not None:
            sent.append({**kwargs, "delegation": delegation})
        yield response

    return stream_delegated_chat_completion


def _events(response):
    """The (event, data) pairs of a text/event-stream response body."""
    text = b"".join(response.streaming_content).decode()
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        name, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        events.append((name, json.loads(data)))
    return events


def _chunk(content=None, usage=None, metadata=None):
    chunk = {"object": "chat.completion.chunk", "choices": []}
    if content is not None:
        chunk["choices"] = [{"index": 0, "delta": {"content": content}}]
    if usage is not None:
        chunk["usage"] = usage
    if metadata is not None:
        chunk["agentcc_metadata"] = metadata
    return f"data: {json.dumps(chunk)}"


@pytest.mark.integration
@pytest.mark.api
class TestPlaygroundParameters:
    @patch("agentcc.views.gateway.get_gateway_client")
    def test_sends_temperature_max_tokens_and_top_p(self, mock_get_client, auth_client):
        client = _client()
        mock_get_client.return_value = client
        response = auth_client.post(
            f"{GATEWAY}/test-playground/",
            {
                "prompt": "hello",
                "model": "openai/gpt-4o-mini",
                "temperature": 0.2,
                "max_tokens": 64,
                "top_p": 0.9,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        kwargs = client.send_delegated_chat_completion.call_args.kwargs
        assert kwargs["params"] == {"temperature": 0.2, "max_tokens": 64, "top_p": 0.9}
        assert client.send_delegated_chat_completion.call_args.args[0].claims[
            "user"
        ] == str(response.wsgi_request.user.pk)
        _assert_no_key(client)

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_leaves_parameters_out_when_not_given(self, mock_get_client, auth_client):
        client = _client()
        mock_get_client.return_value = client
        response = auth_client.post(
            f"{GATEWAY}/test-playground/",
            {"prompt": "hello", "model": "openai/gpt-4o-mini"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert client.send_delegated_chat_completion.call_args.kwargs["params"] == {}
        _assert_no_key(client)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("temperature", 2.5),
            ("temperature", -0.1),
            ("top_p", 1.5),
            ("max_tokens", 0),
        ],
    )
    @patch("agentcc.views.gateway.get_gateway_client")
    def test_refuses_out_of_range_values(
        self, mock_get_client, auth_client, field, value
    ):
        client = _client()
        mock_get_client.return_value = client
        response = auth_client.post(
            f"{GATEWAY}/test-playground/",
            {"prompt": "hello", field: value},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()["details"]
        client.send_delegated_chat_completion.assert_not_called()
        _assert_no_key(client)


class TestChatBody:
    def test_body_carries_only_the_known_parameters(self):
        body = GatewayClient.chat_body(
            prompt="hi",
            model="m",
            system_prompt="be brief",
            params={"temperature": 0, "max_tokens": 5, "top_p": 1, "seed": 7},
        )
        assert body == {
            "model": "m",
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hi"},
            ],
            "temperature": 0,
            "max_tokens": 5,
            "top_p": 1,
        }

    def test_stream_body_asks_for_the_usage_chunk(self):
        body = GatewayClient.chat_body(prompt="hi", model="m", stream=True)
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}


@pytest.mark.integration
@pytest.mark.api
class TestPlaygroundStream:
    @patch("agentcc.views.gateway.get_gateway_client")
    def test_relays_the_answer_as_it_arrives_then_the_totals(
        self, mock_get_client, auth_client
    ):
        client = _client()
        sent = []
        client.stream_delegated_chat_completion = _streaming(
            _FakeStream(
                200,
                {
                    "content-type": "text/event-stream",
                    "X-Agentcc-Request-Id": "req_9",
                    "x-agentcc-billing-mode": "managed",
                    "server": "agentcc",
                },
                [
                    "",
                    _chunk("Hel"),
                    "",
                    _chunk("lo"),
                    _chunk(
                        usage={"prompt_tokens": 3, "completion_tokens": 2},
                        metadata={
                            "cost": 0.000021,
                            "cost_source": "catalog",
                            "latency_ms": 412,
                            "request_id": "req_9",
                            "billing_mode": "managed",
                        },
                    ),
                    "data: [DONE]",
                ],
            ),
            sent,
        )
        mock_get_client.return_value = client
        response = auth_client.post(
            f"{GATEWAY}/test-playground-stream/",
            {
                "prompt": "hello",
                "model": "openai/gpt-4o-mini",
                "system_prompt": "be brief",
                "temperature": 0.5,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"].startswith("text/event-stream")
        assert response["Cache-Control"] == "no-cache"
        assert response["X-Accel-Buffering"] == "no"
        # Neither constructing nor consuming the response mints a key.
        _assert_no_key(client)
        events = _events(response)
        assert [name for name, _ in events] == ["meta", "delta", "delta", "done"]
        meta = events[0][1]
        assert meta["status_code"] == 200
        assert meta["model"] == "openai/gpt-4o-mini"
        assert meta["guardrail_headers"] == {
            "X-Agentcc-Request-Id": "req_9",
            "x-agentcc-billing-mode": "managed",
        }
        assert "".join(data["content"] for name, data in events if name == "delta") == (
            "Hello"
        )
        done = events[-1][1]
        assert done["usage"] == {"prompt_tokens": 3, "completion_tokens": 2}
        assert done["agentcc_metadata"]["cost"] == 0.000021
        assert done["agentcc_metadata"]["request_id"] == "req_9"
        _assert_no_key(client)
        assert sent[0]["params"] == {"temperature": 0.5}
        assert sent[0]["system_prompt"] == "be brief"
        assert "api_key" not in sent[0]
        assert sent[0]["delegation"].claims["kind"] == "tryit"
        assert sent[0]["delegation"].claims["user"] == str(
            response.wsgi_request.user.pk
        )

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_a_refusal_arrives_as_one_result_event(self, mock_get_client, auth_client):
        # The gateway blocks with 403 and error.type guardrail_error, the same
        # answer the non-streamed test reads as blocked.
        blocked = {
            "error": {
                "message": "Request blocked by guardrail pii-detection",
                "type": "guardrail_error",
                "code": "content_blocked",
            }
        }
        client = _client()
        client.stream_delegated_chat_completion = _streaming(
            _FakeStream(
                403,
                {"content-type": "application/json", "x-agentcc-guardrail": "pii"},
                body=json.dumps(blocked).encode(),
            )
        )
        mock_get_client.return_value = client
        response = auth_client.post(
            f"{GATEWAY}/test-playground-stream/",
            {"prompt": "my ssn", "model": "openai/gpt-4o-mini"},
            format="json",
        )
        events = _events(response)
        assert [name for name, _ in events] == ["result"]
        result = events[0][1]
        assert result["status_code"] == 403
        assert result["blocked"] is True
        assert result["warned"] is False
        assert result["body"] == blocked
        assert result["guardrail_headers"] == {"x-agentcc-guardrail": "pii"}
        _assert_no_key(client)

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_a_403_that_is_not_a_guardrail_block_is_not_blocked(
        self, mock_get_client, auth_client
    ):
        client = _client()
        client.stream_delegated_chat_completion = _streaming(
            _FakeStream(
                403,
                {"content-type": "application/json"},
                body=json.dumps(
                    {"error": {"message": "model not allowed", "type": "permission"}}
                ).encode(),
            )
        )
        mock_get_client.return_value = client
        response = auth_client.post(
            f"{GATEWAY}/test-playground-stream/",
            {"prompt": "hello", "model": "openai/gpt-4o-mini"},
            format="json",
        )
        events = _events(response)
        assert [name for name, _ in events] == ["result"]
        result = events[0][1]
        assert result["status_code"] == 403
        assert result["blocked"] is False
        _assert_no_key(client)

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_a_warned_stream_names_the_action_and_checks_as_the_test_does(
        self, mock_get_client, auth_client
    ):
        # A check that warned lets the stream through: 2xx with
        # x-agentcc-guardrail-triggered, the action and the check names.
        client = _client()
        client.stream_delegated_chat_completion = _streaming(
            _FakeStream(
                200,
                {
                    "content-type": "text/event-stream",
                    "x-agentcc-guardrail-triggered": "true",
                    "x-agentcc-guardrail-action": "warn",
                    "x-agentcc-guardrails-triggered": "secret-detection,pii-detection",
                },
                [_chunk("ok"), "data: [DONE]"],
            )
        )
        mock_get_client.return_value = client
        response = auth_client.post(
            f"{GATEWAY}/test-playground-stream/",
            {"prompt": "hello", "model": "openai/gpt-4o-mini"},
            format="json",
        )
        events = _events(response)
        assert [name for name, _ in events] == ["meta", "delta", "done"]
        for name, data in (events[0], events[-1]):
            assert data["warned"] is True, name
            assert data["blocked"] is False, name
            assert data["guardrail_action"] == "warn", name
            assert data["triggered_checks"] == ["pii-detection", "secret-detection"]
        _assert_no_key(client)

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_a_gateway_failure_mid_stream_is_an_error_event(
        self, mock_get_client, auth_client
    ):
        client = _client()

        class _Broken(_FakeStream):
            def iter_lines(self):
                yield _chunk("par")
                raise GatewayClientError("Gateway request timed out: read")

        client.stream_delegated_chat_completion = _streaming(
            _Broken(200, {"content-type": "text/event-stream"})
        )
        mock_get_client.return_value = client
        response = auth_client.post(
            f"{GATEWAY}/test-playground-stream/",
            {"prompt": "hello", "model": "openai/gpt-4o-mini"},
            format="json",
        )
        events = _events(response)
        assert [name for name, _ in events] == ["meta", "delta", "error"]
        assert "timed out" in events[-1][1]["message"]
        _assert_no_key(client)

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_an_error_chunk_ends_the_stream(self, mock_get_client, auth_client):
        client = _client()
        client.stream_delegated_chat_completion = _streaming(
            _FakeStream(
                200,
                {"content-type": "text/event-stream"},
                [
                    _chunk("a"),
                    'data: {"error": {"message": "upstream reset", "type": "upstream_error"}}',
                    _chunk("never"),
                ],
            )
        )
        mock_get_client.return_value = client
        response = auth_client.post(
            f"{GATEWAY}/test-playground-stream/",
            {"prompt": "hello", "model": "openai/gpt-4o-mini"},
            format="json",
        )
        events = _events(response)
        assert [name for name, _ in events] == ["meta", "delta", "error"]
        assert events[-1][1]["message"] == "upstream reset"
        _assert_no_key(client)

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_refuses_bad_parameters_before_minting_a_key(
        self, mock_get_client, auth_client
    ):
        client = _client()
        mock_get_client.return_value = client
        response = auth_client.post(
            f"{GATEWAY}/test-playground-stream/",
            {"prompt": "hello", "top_p": 2},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        client.create_key.assert_not_called()

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_a_stream_closed_before_its_first_event_makes_no_gateway_call(
        self, mock_get_client, auth_client
    ):
        # The browser can leave before the first piece is sent: the server
        # then closes the response without ever reading it.
        client = _client()
        client.stream_delegated_chat_completion = MagicMock(
            side_effect=_streaming(
                _FakeStream(200, {"content-type": "text/event-stream"}, [_chunk("a")])
            )
        )
        mock_get_client.return_value = client
        response = auth_client.post(
            f"{GATEWAY}/test-playground-stream/",
            {"prompt": "hello", "model": "openai/gpt-4o-mini"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        response.close()
        client.stream_delegated_chat_completion.assert_not_called()
        _assert_no_key(client)

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_says_why_when_delegation_cannot_be_issued(
        self, mock_get_client, auth_client
    ):
        from tfc.service_envelope import EnvelopeRejected

        client = _client()
        mock_get_client.return_value = client
        with patch(
            "agentcc.views.gateway.for_chat", side_effect=EnvelopeRejected()
        ) as issued:
            response = auth_client.post(
                f"{GATEWAY}/test-playground-stream/",
                {"prompt": "hello", "model": "openai/gpt-4o-mini"},
                format="json",
            )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "active person and workspace" in response.json()["detail"]
        issued.assert_called_once()
        client.stream_delegated_chat_completion.assert_not_called()
        _assert_no_key(client)


class _FakeGateway(BaseHTTPRequestHandler):
    """A loopback delegated gateway: records envelope and grant headers and
    streams chunked SSE. It waits for the test after the first answer piece."""

    protocol_version = "HTTP/1.1"
    state = None

    def log_message(self, *args):
        pass

    def _json(self, code, data):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _chunk(self, text):
        raw = text.encode()
        self.wfile.write(f"{len(raw):X}\r\n".encode() + raw + b"\r\n")
        self.wfile.flush()

    def do_DELETE(self):
        self.state["revoked"].append(self.path)
        self._json(200, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/-/keys":
            self.state["created"].append(self.path)
            self._json(409, {"error": "automatic keys are unavailable"})
            return
        self.state["chat"] = body
        self.state["auth"] = self.headers.get("Authorization")
        self.state["envelope"] = self.headers.get("X-FA-Envelope")
        self.state["grant"] = self.headers.get("X-FA-Grant")
        self.state["route"] = self.path
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("X-Agentcc-Request-Id", "req_e2e")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self._chunk(_chunk("Hel") + "\n\n")
        self.state["released"] = self.state["release"].wait(10)
        self._chunk(_chunk("lo") + "\n\n")
        if self.state.get("watch_close"):
            # Did the console hang up instead of reading on? It sends nothing
            # after its request, so a readable socket means it closed.
            try:
                ready, _, _ = select.select([self.connection], [], [], 3)
                self.state["closed_early"] = bool(ready) and (
                    self.connection.recv(1, socket.MSG_PEEK) == b""
                )
            finally:
                # The test reads the answer on another thread: it is in.
                self.state["watched"].set()
            if self.state["closed_early"]:
                return
        usage = {"prompt_tokens": 3, "completion_tokens": 2}
        metadata = {"cost": 0.00001, "cost_source": "catalog", "request_id": "req_e2e"}
        self._chunk(_chunk(usage=usage, metadata=metadata) + "\n\n")
        self._chunk("data: [DONE]\n\n")
        self._chunk("")


@pytest.fixture
def fake_gateway():
    state = {
        "created": [],
        "revoked": [],
        "release": threading.Event(),
        "released": None,
        "watched": threading.Event(),
    }
    handler = type("Gateway", (_FakeGateway,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    state["release"].set()
    server.shutdown()
    server.server_close()


@pytest.mark.integration
@pytest.mark.api
class TestPlaygroundStreamOverHttp:
    # The real client, httpx streaming and the relay against a real HTTP
    # stream: the first piece of the answer reaches the console while the
    # gateway is still writing, not when it has finished.
    def test_the_first_piece_arrives_before_the_gateway_finishes(
        self, auth_client, fake_gateway
    ):
        client = GatewayClient(base_url=fake_gateway["url"], admin_token=TEST_ADMIN)
        with patch("agentcc.views.gateway.get_gateway_client", return_value=client):
            response = auth_client.post(
                f"{GATEWAY}/test-playground-stream/",
                {"prompt": "hello", "model": "openai/gpt-4o-mini", "top_p": 0.5},
                format="json",
            )
            assert response.status_code == status.HTTP_200_OK
            pieces = iter(response.streaming_content)
            first = next(pieces).decode()
            second = next(pieces).decode()
            assert first.startswith("event: meta")
            assert second.startswith("event: delta") and '"Hel"' in second
            # The gateway is still holding the rest of the answer.
            assert fake_gateway["released"] is None
            fake_gateway["release"].set()
            rest = b"".join(pieces).decode()
        assert fake_gateway["released"] is True
        assert '"lo"' in rest
        assert "event: done" in rest and '"req_e2e"' in rest
        assert fake_gateway["chat"]["stream"] is True
        assert fake_gateway["chat"]["stream_options"] == {"include_usage": True}
        assert fake_gateway["chat"]["top_p"] == 0.5
        assert fake_gateway["auth"] == "Bearer " + TEST_ADMIN
        assert fake_gateway["route"] == "/-/delegated/v1/chat/completions"
        assert fake_gateway["envelope"].startswith("fe1.")
        assert fake_gateway["grant"].startswith("fe1.")
        assert fake_gateway["created"] == fake_gateway["revoked"] == []
        assert fake_gateway["grant"] not in rest


class _Served(ASGIHandler):
    """Django's ASGI handler serving a response the view already made. The
    request path (middleware, routing, auth) ran through the test client;
    this runs what the ASGI server production uses (granian --interface asgi
    tfc.asgi:application) does after it: the response's send, the client's
    disconnect and the response's close."""

    def __init__(self, response):  # no middleware to load: the view has run
        self._response = response

    async def run_get_response(self, request):
        self._response._handler_class = self.__class__
        return self._response


_SCOPE = {
    "type": "http",
    "asgi": {"version": "3.0"},
    "http_version": "1.1",
    "method": "POST",
    "scheme": "http",
    "path": f"{GATEWAY}/test-playground-stream/",
    "raw_path": f"{GATEWAY}/test-playground-stream/".encode(),
    "query_string": b"",
    "headers": [],
    "client": ("127.0.0.1", 50000),
    "server": ("testserver", 80),
}


class _Browser:
    """The browser's end of an ASGI connection: what it has received, and
    whether it has gone (Stop, or the tab closed)."""

    def __init__(self):
        self.body = b""
        self.changed = asyncio.Event()
        self.gone = asyncio.Event()
        self._asked = False

    async def receive(self):
        if not self._asked:
            self._asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self.gone.wait()
        return {"type": "http.disconnect"}

    async def send(self, message):
        if message["type"] == "http.response.body":
            self.body += message.get("body", b"")
            self.changed.set()

    async def wait_for(self, text, timeout):
        """True once ``text`` has arrived, False if it has not in time."""

        async def arrived():
            while text not in self.body.decode():
                self.changed.clear()
                await self.changed.wait()

        try:
            await asyncio.wait_for(arrived(), timeout)
            return True
        except TimeoutError:
            return False


def _open_stream(auth_client, fake_gateway):
    client = GatewayClient(base_url=fake_gateway["url"], admin_token=TEST_ADMIN)
    with patch("agentcc.views.gateway.get_gateway_client", return_value=client):
        response = auth_client.post(
            f"{GATEWAY}/test-playground-stream/",
            {"prompt": "hello", "model": "openai/gpt-4o-mini"},
            format="json",
        )
    assert response.status_code == status.HTTP_200_OK
    # The test client wraps streaming_content in its own closing iterator;
    # an ASGI server gets the response as the view made it. Serve that.
    response.streaming_content = response._events
    assert response._iterator is response._events
    return response


@pytest.mark.integration
@pytest.mark.api
class TestPlaygroundStreamOverAsgi:
    # Production serves Django through ASGI. There a StreamingHttpResponse
    # over a sync iterator is read to the end before its first byte is sent
    # (StreamingHttpResponse.__aiter__), so these drive Django's own
    # ASGIHandler with the real client against a gateway still writing.
    def test_the_browser_gets_the_first_piece_while_the_gateway_still_writes(
        self, auth_client, fake_gateway
    ):
        response = _open_stream(auth_client, fake_gateway)

        async def serve():
            browser = _Browser()
            served = asyncio.create_task(
                _Served(response)(_SCOPE, browser.receive, browser.send)
            )
            try:
                await browser.wait_for('"Hel"', timeout=5)
                before = browser.body.decode()
                released = fake_gateway["released"]
            finally:
                fake_gateway["release"].set()
                await asyncio.wait_for(served, 20)
            return before, released, browser.body.decode()

        before, released, everything = asyncio.run(serve())
        assert before.startswith("event: meta")
        assert "event: delta" in before and '"Hel"' in before
        # ...while the gateway was still holding the rest of the answer.
        assert released is None
        assert '"lo"' in everything
        assert "event: done" in everything and '"req_e2e"' in everything
        assert fake_gateway["created"] == fake_gateway["revoked"] == []
        assert fake_gateway["route"] == "/-/delegated/v1/chat/completions"
        assert fake_gateway["grant"] not in everything

    def test_stop_ends_the_delegated_gateway_read_without_minting(
        self, auth_client, fake_gateway
    ):
        fake_gateway["watch_close"] = True
        response = _open_stream(auth_client, fake_gateway)

        async def serve():
            browser = _Browser()
            served = asyncio.create_task(
                _Served(response)(_SCOPE, browser.receive, browser.send)
            )
            try:
                await browser.wait_for('"Hel"', timeout=5)
                before_stop = browser.body.decode()
                browser.gone.set()
                await asyncio.sleep(0.2)  # the server sees the disconnect
            finally:
                fake_gateway["release"].set()  # the gateway sends its next piece
                await asyncio.wait_for(served, 20)
            return before_stop, browser.body.decode()

        before_stop, everything = asyncio.run(serve())
        assert '"Hel"' in before_stop
        # Nothing reaches the browser after it left, and the console hung up
        # on the gateway instead of reading the answer to its end...
        assert everything == before_stop
        # (The gateway's handler thread tells: served ends as the console
        # hangs up, which can be before that thread has looked. It looks for
        # 3 s at most.)
        assert fake_gateway["watched"].wait(5)
        assert fake_gateway.get("closed_early") is True
        # ...without ever creating an API key or exposing the grant.
        assert fake_gateway["created"] == fake_gateway["revoked"] == []
        assert fake_gateway["route"] == "/-/delegated/v1/chat/completions"
        assert fake_gateway["grant"] not in everything
