"""Labelled loopback LLM STANDIN; only the model boundary is substituted."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def value(column, description, index):
    """Native JSON values per declared type; the first row uses falsy and empty values."""
    first = index == 1
    return {
        "boolean": not first,
        "integer": index - 1,
        "json": {} if first else {"nested": [1, True, None], "label": description},
        "array": [] if first else ["a", 2, False],
    }.get(column["data_type"], f"STANDIN | {description} | {column['name']} | {index}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        last = body["messages"][-1]["content"]
        if isinstance(last, list):
            last = "".join(item.get("text", "") for item in last)
        try:
            config = json.loads(last)
            description = config["requirements"]["description"]
            rows = [{column["name"]: value(column, description, i + 1)
                     for column in config["columns"]} for i in range(config["count"])]
            content = json.dumps({"rows": rows})
            print(json.dumps({"standin": True, "model": body["model"], "generation": config}), flush=True)
        except (ValueError, KeyError, TypeError):
            content = "STANDIN validation response"
        response = {"id": "standin-generation", "object": "chat.completion", "created": 1,
                    "model": body["model"], "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 7, "total_tokens": 14}}
        data = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("127.0.0.1", 18082), Handler).serve_forever()
