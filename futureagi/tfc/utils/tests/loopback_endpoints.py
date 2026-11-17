"""Real loopback servers for tests of guarded model endpoints.

Only 127.0.0.1 and ::1 are ever bound or contacted. ``LoopbackServer``
answers every request with what its ``answer`` callable returns and records
the request; ``redirector``, ``metadata_stand_in``, ``model_server`` and
``recording_proxy`` build the shapes the SSRF tests need.

``public-a.test`` stands in for an attacker's PUBLIC host: the tests map it
to 127.0.0.1 with ``FakeResolver`` and ``treat_as_public`` tells the address
policy that 127.0.0.1 (only that address) is public. ``[::1]`` keeps its real
class, so on a hosted deployment it is refused like 169.254.169.254, which it
stands in for.
"""

import base64
import http.server
import json
import socket
import threading

from tfc.utils import ssrf_guard

PUBLIC_NAME = "public-a.test"
LOOPBACK_V4 = "127.0.0.1"
SECRET_MARKER = "SECRET-MARKER-123"
METADATA_BODY = json.dumps(
    {
        "Code": "Success",
        "AccessKeyId": "ASIA-MARKER",
        "SecretAccessKey": SECRET_MARKER,
        "Token": "TOKEN-MARKER",
    }
).encode()
MODEL_ANSWER = "public model answer"


def completion_body(content=MODEL_ANSWER, model="gpt-x"):
    return json.dumps(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1700000000,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
    ).encode()


IMAGE_BODY = json.dumps(
    {
        "created": 1700000000,
        "data": [{"b64_json": base64.b64encode(b"png-bytes").decode("ascii")}],
    }
).encode()


class RecordedRequest:
    def __init__(self, method, target, headers, body):
        self.method = method
        self.target = target
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body

    def __repr__(self):
        return f"<{self.method} {self.target}>"


class LoopbackServer:
    """A threaded HTTP server on 127.0.0.1 (or ::1) for one test.

    ``answer(request) -> (status, headers, body)``; the default answers a
    chat completion. Use as a context manager, or call ``close()``.
    """

    def __init__(self, answer=None, ipv6=False):
        self.requests = []
        server = self
        answer = answer or (lambda request: (200, _json_headers(), completion_body()))

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                request = RecordedRequest(
                    self.command, self.path, dict(self.headers), body
                )
                server.requests.append(request)
                status, headers, payload = answer(request)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                if payload:
                    self.wfile.write(payload)

            do_GET = _serve
            do_POST = _serve
            do_PUT = _serve
            do_CONNECT = _serve

            def log_message(self, *args):
                pass

        class _Server(http.server.ThreadingHTTPServer):
            address_family = socket.AF_INET6 if ipv6 else socket.AF_INET
            daemon_threads = True

        self._server = _Server(("::1" if ipv6 else LOOPBACK_V4, 0), _Handler)
        self.port = self._server.server_address[1]
        self.host = "[::1]" if ipv6 else LOOPBACK_V4
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base(self):
        return f"http://{self.host}:{self.port}"

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


def _json_headers():
    return {"Content-Type": "application/json"}


def model_server(body=None, status=200, image_body=IMAGE_BODY):
    """An OpenAI-compatible model on 127.0.0.1: chat completions anywhere,
    images under ``/images/generations``."""

    def answer(request):
        if "images/generations" in request.target:
            return status, _json_headers(), image_body
        return status, _json_headers(), body or completion_body()

    return LoopbackServer(answer)


def metadata_stand_in():
    """A metadata-like service on [::1] that hands out a credential to
    anyone who asks: what a redirect to 169.254.169.254 would reach."""
    return LoopbackServer(
        lambda request: (200, _json_headers(), METADATA_BODY), ipv6=True
    )


def redirector(target_url, status=302):
    """A public-looking endpoint that answers every request with a redirect."""
    return LoopbackServer(lambda request: (status, {"Location": target_url}, b""))


def recording_proxy(body=None):
    """An HTTP proxy on 127.0.0.1 that records every request and forwards
    nothing: an absolute-form request gets a chat completion back, CONNECT
    gets 403 (no tunnel), so the test sees what reached the proxy."""

    def answer(request):
        if request.method == "CONNECT":
            return 403, {}, b""
        return 200, _json_headers(), body or completion_body()

    return LoopbackServer(answer)


def treat_as_public(monkeypatch, address=LOOPBACK_V4):
    """Make the address policy treat ``address`` (only) as a public address,
    so a loopback server can stand in for a public host on hosted."""
    real = ssrf_guard.endpoint_address_allowed

    def allowed(ip, policy):
        if str(ip) == address:
            return True
        return real(ip, policy)

    monkeypatch.setattr(ssrf_guard, "endpoint_address_allowed", allowed)


PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def clear_proxy_env(monkeypatch):
    """Tests decide the proxy environment themselves (a CI host with
    HTTP_PROXY set must not route around the fakes)."""
    for name in PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
