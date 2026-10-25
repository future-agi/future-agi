"""Stand-in embeddings upstream for 10_embeddings.sh.

The mock upstream (agentcc-mockup) serves neither Cohere's /v2/embed nor Gemini's :embedContent, so
the scenario's Cohere and Gemini providers point here. One process stands in
for three hosts, told apart by a path prefix:

  POST /host-a/v2/embed, /host-b/v2/embed   Cohere v2 embed: two 2-dim float
      vectors' worth (one per text) and billed_units.input_tokens. A request
      for the model embed-multilingual-v3.0 gets 503 on either host: it is the
      scenario's target that is down.
  POST /host-g/v1beta/models/<model>:embedContent   Gemini embed: one 3-dim
      vector and no usageMetadata (the upstream reports no usage).
  GET /__health   200.

Every POST appends one JSON line to <record dir>/received.jsonl: the host
prefix, the path (never the query string, which carries the Gemini key), the
model and Cohere's input_type or Gemini's taskType from the body. Standard
library only; run with python3 -I.

    python3 -I embed_standin.py <port> <record dir> <owner pid>

It exits on its own once <owner pid> (the scenario's shell) is gone.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

DOWN_MODEL = "embed-multilingual-v3.0"


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
    port, record_dir, owner = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    threading.Thread(target=exit_with, args=(owner,), daemon=True).start()
    received = os.path.join(record_dir, "received.jsonl")
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/__health":
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": {"message": "not found"}})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            path = urlsplit(self.path).path
            host, _, rest = path.lstrip("/").partition("/")
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            model = body.get("model", "")
            if host == "host-g":
                model = rest.rsplit("/", 1)[-1].split(":", 1)[0]
            with lock, open(received, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "host": host,
                            "path": "/" + rest,
                            "model": model,
                            "input_type": body.get("input_type", ""),
                            "task_type": body.get("taskType", ""),
                        }
                    )
                    + "\n"
                )

            if host in ("host-a", "host-b") and rest == "v2/embed":
                if model == DOWN_MODEL:
                    self._send(503, {"message": "service unavailable"})
                    return
                texts = body.get("texts") or [""]
                self._send(
                    200,
                    {
                        "id": "scn-embeddings",
                        "embeddings": {"float": [[0.25, -0.5] for _ in texts]},
                        "texts": texts,
                        "meta": {"billed_units": {"input_tokens": 8}},
                    },
                )
                return
            if host == "host-g" and rest.endswith(":embedContent"):
                self._send(200, {"embedding": {"values": [0.1, 0.2, 0.3]}})
                return
            self._send(404, {"error": {"message": "not found"}})

        def log_message(self, fmt, *args):  # keep the scenario output quiet
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
