"""Local-only OpenAI-format STANDIN; no upstream access or product-state writes."""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

lock = threading.Lock()
calls = {}
released = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, value):
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path)
        group = parse_qs(path.query).get("group", [""])[0]
        if path.path == "/health":
            return self.reply(200, {"kind": "LLM STANDIN", "upstreams": []})
        if path.path == "/calls":
            with lock:
                return self.reply(200, {"count": calls.get(group, 0)})
        self.reply(404, {})

    def do_POST(self):
        path = urlparse(self.path)
        if path.path == "/release":
            group = parse_qs(path.query).get("group", [""])[0]
            with lock:
                released.setdefault(group, threading.Event()).set()
            return self.reply(200, {"released": group})
        if path.path not in ("/v1/chat/completions", "/chat/completions"):
            return self.reply(404, {})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        text = json.dumps(body.get("messages", []))
        marker = re.search(r"CONTROL:([a-zA-Z0-9-]+):(selected|sibling)", text)
        if "CONTROL_JUDGE" in text:
            if marker:
                with lock:
                    group = f"{marker.group(0)}:judge"
                    calls[group] = calls.get(group, 0) + 1
            answer = json.dumps({"result": "Pass", "explanation": "STANDIN judge"})
        elif marker:
            group = marker.group(0)
            with lock:
                calls[group] = calls.get(group, 0) + 1
                count = calls[group]
                phase = "drain" if count <= 20 else "finish"
                event = released.setdefault(f"{group}:{phase}", threading.Event())
            if marker.group(2) == "selected" and count > 10:
                if not event.wait(600):
                    return self.reply(503, {"error": "STANDIN barrier expired"})
            answer = group
        else:
            answer = "STANDIN ready"
        self.reply(200, {
            "id": "standin-completion", "object": "chat.completion",
            "created": int(time.time()), "model": body.get("model", "gpt-4o"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 7, "total_tokens": 14},
        })


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
