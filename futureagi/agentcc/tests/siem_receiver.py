"""A loopback HTTP receiver standing in for an organization's SIEM collector.

It binds 127.0.0.1 on a free port, records every request it receives (path,
headers and raw body) and answers each with the next status of its script, so
delivery tests prove what crossed the wire. Nothing here reaches another host:
the transport seam is patched to admit this receiver's own URL only.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# A receiver's answer body. Deliveries must never store or echo it.
RECEIVER_BODY = b'{"echo":"receiver-answer-must-not-be-stored"}'


class Receiver:
    def __init__(self, statuses=None, retry_after=None):
        self.requests = []
        self.statuses = list(statuses or [])
        self.retry_after = retry_after
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server's method name
                length = int(self.headers.get("Content-Length") or 0)
                receiver.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": self.rfile.read(length),
                    }
                )
                status = receiver.statuses.pop(0) if receiver.statuses else 200
                self.send_response(status)
                if receiver.retry_after is not None and status == 429:
                    self.send_header("Retry-After", str(receiver.retry_after))
                if 300 <= status < 400:
                    self.send_header("Location", "http://127.0.0.1:9/elsewhere")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(RECEIVER_BODY)))
                self.end_headers()
                self.wfile.write(RECEIVER_BODY)

            def log_message(self, *args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def bodies(self):
        return b"\n".join(request["body"] for request in self.requests)


def admit_loopback(monkeypatch, receiver):
    """Let the SIEM transport reach this receiver, and nothing else.

    Production refuses http, loopback and private addresses; tests replace
    that one seam with a check that admits the receiver's own origin only,
    and a session that ignores proxy environment variables. On a tree without
    the SIEM transport there is nothing to patch: the test then fails on the
    product's own answers, not on this helper.
    """
    try:
        from agentcc.services import siem_transport
    except ImportError:
        return
    import requests

    def check(url):
        if url != receiver.url and not url.startswith(receiver.url + "/"):
            raise ValueError("the test transport admits only the loopback receiver")

    def session():
        http = requests.Session()
        http.trust_env = False
        return http

    monkeypatch.setattr(siem_transport, "check_destination_url", check)
    monkeypatch.setattr(siem_transport, "open_session", session)
