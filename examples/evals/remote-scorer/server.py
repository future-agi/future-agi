"""A Future AGI remote scorer, stdlib only.

Future AGI POSTs each row as JSON to your URL and signs it:
  X-FutureAGI-Signature: t=<unix>,v1=<hex>[,v1=<hex>]
  v1 = HMAC-SHA256(secret, "<t>.<raw body>"); a second v1 while a secret rotates.
This server checks the signature (either v1, any secret you pass), rejects a t
older than 5 minutes, and answers {"score", "reason"} by its own rule: an order
id like A-1234 in the output scores 0.7, anything else 0.2.

  python3 -I server.py --secret <hex> [--secret <old hex>] [--port 8787]
  python3 -I server.py --self-test
Flags for trying failures: --answer <n> (always answer that score, e.g. 1.4)
and --fail-first <k> (503 for the first k requests).
"""
import argparse, hashlib, hmac, json, re, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_AGE_S = 300  # receivers reject a t older than 5 minutes
ORDER_ID = re.compile(r"A-\d{4}")


def sign(secret, t, body):
    return hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()


def verify(header, body, secrets, now=None):
    """None when the signature is good, else why not."""
    parts = [p.split("=", 1) for p in (header or "").split(",") if "=" in p]
    ts = [v for k, v in parts if k.strip() == "t"]
    sigs = [v.strip() for k, v in parts if k.strip() == "v1"]
    if not ts or not sigs:
        return "no signature"
    try:
        t = int(ts[0])
    except ValueError:
        return "bad t"
    if abs((now or time.time()) - t) > MAX_AGE_S:
        return "t is older than 5 minutes"
    good = [sign(s, t, body) for s in secrets]
    if any(hmac.compare_digest(g, s) for g in good for s in sigs):
        return None
    return "signature does not match"


def score(payload, answer=None):
    if answer is not None:
        return {"score": answer, "reason": "fixed answer (--answer)"}
    output = str(payload.get("output") or "")
    found = ORDER_ID.search(output)
    if found:
        return {"score": 0.7, "reason": f"names order id {found.group(0)}"}
    return {"score": 0.2, "reason": "names no order id"}


def make_handler(secrets, answer=None, fail_first=0):
    state = {"seen": 0}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, obj):
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):  # the browser's preflight (Test on one row from localhost)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", self.headers.get("Access-Control-Request-Headers", "*"))
            self.end_headers()

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            with lock:
                state["seen"] += 1
                n = state["seen"]
            if n <= fail_first:
                return self._send(503, {"error": f"failing the first {fail_first} requests"})
            why = verify(self.headers.get("X-FutureAGI-Signature"), body, secrets) if secrets else None
            if why:
                return self._send(401, {"error": why})
            try:
                payload = json.loads(body)
            except ValueError:
                return self._send(400, {"error": "body is not JSON"})
            self._send(200, score(payload, answer))

        def log_message(self, *a):
            pass

    return Handler


def self_test():
    secret, old = "a" * 64, "b" * 64
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler([secret, old]))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/score"

    def post(body, header):
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "X-FutureAGI-Signature": header})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    body = json.dumps({"row": {"id": "r12"}, "output": "Order A-1234 ships Monday"}).encode()
    t = int(time.time())
    checks = [
        (post(body, f"t={t},v1={sign(secret, t, body)}"), (200, 0.7)),
        (post(body, f"t={t},v1={sign('c' * 64, t, body)},v1={sign(old, t, body)}"), (200, 0.7)),
        (post(body.replace(b"A-1234", b"later"), f"t={t},v1={sign(secret, t, body)}"), (401, None)),
        (post(body, f"t={t - 301},v1={sign(secret, t - 301, body)}"), (401, None)),
    ]
    for (status, data), (want, want_score) in checks:
        assert status == want, (status, data)
        assert want_score is None or data["score"] == want_score, data
    assert score({"output": "no id here"})["score"] == 0.2
    srv.shutdown()
    print("self-test ok: good signature, rotation (either v1), tampered body and stale t")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--secret", action="append", default=[], help="signing secret; pass twice while rotating")
    ap.add_argument("--answer", type=float)
    ap.add_argument("--fail-first", type=int, default=0)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.secret:
        print("no --secret: signatures are not checked", file=sys.stderr)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.secret, a.answer, a.fail_first))
    print(f"scoring on http://127.0.0.1:{a.port}/score")
    srv.serve_forever()


if __name__ == "__main__":
    main()
