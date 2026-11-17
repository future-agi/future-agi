"""Loopback STANDIN for chat and embedding models, not application endpoints."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body):
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.reply({"status": "healthy", "standin": True})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/model/v1/embed":
            texts = body["text"]
            print(json.dumps({"standin": "embedding", "texts": texts}), flush=True)
            self.reply({"embeddings": [[1.0] + [0.0] * 383 for _ in texts]})
            return
        last = body["messages"][-1]["content"]
        if isinstance(last, list):
            last = "".join(item.get("text", "") for item in last)
        try:
            config = json.loads(last)
            source = config["reference_rows"][0]["values"]["question"]
            passage = config.get("passages", [{"text": "", "citation": ""}])[0]
            rows = [{"question": f"STANDIN | {source} | {config['requirements']['description']}",
                     "context": passage["text"], "citation": passage["citation"]}
                    for _ in range(config["count"])]
            content = json.dumps({"rows": rows})
            print(json.dumps({"standin": "chat", "generation": config}), flush=True)
        except (ValueError, KeyError, TypeError, IndexError):
            content = "STANDIN validation response"
        self.reply({"id": "standin-more", "object": "chat.completion", "created": 1,
                    "model": body["model"], "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 7, "total_tokens": 14}})


ThreadingHTTPServer(("0.0.0.0", 18083), Handler).serve_forever()
