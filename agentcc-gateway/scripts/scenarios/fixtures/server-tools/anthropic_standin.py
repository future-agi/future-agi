"""Stand-in Anthropic upstream for 04_server_tools.sh.

The mock upstream (agentcc-mockup) never reports usage.server_tool_use, so the BYOK Anthropic
provider points here. POST /v1/messages answers a recorded Messages body:
messages_search.json (three web searches, server_tool_use.web_search_requests
3) when the request carries a web_search_* tool, else messages_plain.json
(the same tokens, no search). Every POST, to any path, is appended to
<record_dir>/received.jsonl, one JSON line per request: the path, the
x-api-key header (the scenario's fake BYOK key) and the body bytes as a JSON
string, so nothing is re-encoded; a path other than /v1/messages (Bedrock's
/model/<id>/converse) answers 404. GET /__health answers 200. Standard
library only; run with python3 -I.

    python3 -I anthropic_standin.py <port> <fixtures dir> <record dir> <owner pid>

It exits on its own once <owner pid> (the scenario's shell) is gone, so a
scenario that stops early never leaves it running.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def exit_with(owner):
    """Exit the process once the owner pid has exited."""
    while True:
        try:
            os.kill(owner, 0)
        except ProcessLookupError:
            os._exit(0)
        except PermissionError:
            pass
        time.sleep(1)


def read(path):
    with open(path, "rb") as f:
        return f.read().strip()


def searches(body):
    """Whether the request's tools include an Anthropic web search."""
    try:
        tools = json.loads(body).get("tools") or []
    except (ValueError, AttributeError):
        return False
    return any(str(t.get("type", "")).startswith("web_search_") for t in tools if isinstance(t, dict))


def main():
    port, fixtures, record_dir, owner = int(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
    threading.Thread(target=exit_with, args=(owner,), daemon=True).start()
    search = read(os.path.join(fixtures, "messages_search.json"))
    plain = read(os.path.join(fixtures, "messages_plain.json"))
    received = os.path.join(record_dir, "received.jsonl")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/__health":
                self._send(200, b'{"ok":true}')
            else:
                self._send(404, b'{"error":{"message":"not found"}}')

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            with open(received, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "path": self.path,
                    "api_key": self.headers.get("x-api-key", ""),
                    "body": body.decode("utf-8"),
                }) + "\n")
            if self.path != "/v1/messages":
                self._send(404, b'{"type":"error","error":{"type":"not_found_error","message":"not found"}}')
                return
            self._send(200, search if searches(body) else plain)

        def log_message(self, fmt, *args):  # keep the scenario output quiet
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
