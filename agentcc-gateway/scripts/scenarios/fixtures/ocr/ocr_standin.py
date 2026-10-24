"""Stand-in Mistral OCR upstream for 05_ocr.sh.

The mock upstream (agentcc-mockup) serves no /v1/ocr, so the OCR provider points here. POST /v1/ocr
answers the recorded Mistral OCR body (ocr_response.json) and appends what it
received to <record_dir>/received.jsonl, one JSON line per request: the path,
the Authorization header (the scenario's fake BYOK key) and the body bytes as
a JSON string, so nothing is re-encoded. GET /__health answers
200. Standard library only; run with python3 -I.

    python3 -I ocr_standin.py <port> <response file> <record dir> <owner pid>

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


def main():
    port, response_file, record_dir, owner = int(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
    threading.Thread(target=exit_with, args=(owner,), daemon=True).start()
    with open(response_file, "rb") as f:
        response = f.read().strip()
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
            if self.path != "/v1/ocr":
                self._send(404, b'{"error":{"message":"not found"}}')
                return
            with open(received, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization", ""),
                    "body": body.decode("utf-8"),
                }) + "\n")
            self._send(200, response)

        def log_message(self, fmt, *args):  # keep the scenario output quiet
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
