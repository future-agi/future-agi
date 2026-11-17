# server.py: a small stdlib HTTP agent for the drawer's HTTP body template
# ({"message": "{{instruction}}"} -> {"reply": "..."}).
#   python3 server.py [--port 8787] [--verify-hmac SECRET]   (http://127.0.0.1:8787/reply)
#   python3 server.py --dry-run [--verify-hmac SECRET]       (no socket)
# It echoes the W3C traceparent Future AGI sends with every try, and with
# --verify-hmac it answers 401 unless X-FutureAGI-Signature verifies:
#   X-FutureAGI-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">
import hashlib
import hmac
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

TOLERANCE_S = 300  # reject signatures older than 5 minutes (replays)
TRACEPARENT = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


PORT = int(arg("--port", os.environ.get("PORT", "8787")))
SECRET = arg("--verify-hmac")


def reply(body):
    """Your agent goes here; this one answers deterministically."""
    text = str(body.get("message") or "").strip()
    out = {"reply": f"Thanks, I'm on it: {text[:80]}"}
    if body.get("row_id"):  # echoed when the body template sends one
        out["row_id"] = body["row_id"]
    return out


def sign(secret, t, raw):
    return hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()


def verify(secret, header, raw, now=None):
    """None when the signature verifies, else why it does not."""
    if not header:
        return "missing X-FutureAGI-Signature"
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    sigs = [p.split("=", 1)[1] for p in header.split(",") if p.startswith("v1=")]
    try:
        t = int(parts.get("t", ""))
    except ValueError:
        return "no t= in X-FutureAGI-Signature"
    if abs((now or time.time()) - t) > TOLERANCE_S:
        return "signature too old"
    want = sign(secret, t, raw)
    return None if any(hmac.compare_digest(want, s) for s in sigs) else "signature does not match the body"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, data, traceparent=None):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")  # for Test on one row
        # Allow whatever headers the request asks for (traceparent, X-…).
        asked = self.headers.get("Access-Control-Request-Headers")
        self.send_header("Access-Control-Allow-Headers", asked or "Content-Type, Authorization, traceparent")
        self.send_header("Access-Control-Expose-Headers", "traceparent")
        if traceparent:
            self.send_header("traceparent", traceparent)
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):  # the browser's CORS preflight
        self._send(204, {})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        # The log shows what was sent: method, path and headers.
        sent = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "connection")}
        print(json.dumps({"method": "POST", "path": self.path, "headers": sent}), file=sys.stderr, flush=True)
        tp = self.headers.get("traceparent")
        tp = tp if tp and TRACEPARENT.match(tp.strip()) else None
        if SECRET:
            why = verify(SECRET, self.headers.get("X-FutureAGI-Signature"), raw)
            if why:
                return self._send(401, {"error": why}, tp)
        out = reply(json.loads(raw or b"{}"))
        if tp:
            out["traceparent"] = tp  # the trace this answer belongs to
        self._send(200, out, tp)


if __name__ == "__main__":
    if "--dry-run" in sys.argv:  # sample_rows.jsonl through reply(), no socket
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "sample_rows.jsonl"), encoding="utf-8") as f:
            for row in (json.loads(line) for line in f if line.strip()):
                body = {"message": row.get("instruction"), "row_id": row["id"]}
                if SECRET:  # sign and verify each body the way a try is signed
                    raw = json.dumps(body).encode()
                    t = int(time.time())
                    ok = verify(SECRET, f"t={t},v1={sign(SECRET, t, raw)}", raw) is None
                    bad = verify(SECRET, f"t={t},v1={sign(SECRET, t, raw)}", raw + b" ") is not None
                    assert ok and bad, "signature check failed"
                print(json.dumps(reply(body)))
        sys.exit(0)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
