"""Isolated Hugging Face HTTP STANDIN; only synthetic JSON datasets are served.

The application and its datasets/huggingface_hub libraries are not patched.
The Hub metadata, ref resolution, and file downloads terminate here.
"""

import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

PINS = {name: hashlib.sha1(name.encode()).hexdigest() for name in ("v1", "v2")}


def revision(ref):
    if ref in ("main", "v1", PINS["v1"]):
        return "v1"
    if ref in ("v2", PINS["v2"]):
        return "v2"
    raise ValueError("Unknown revision")


def files(repo, rev):
    count = 2 if rev == "v1" else 3
    rows = [{"text": f"{repo.split('/')[-1]}-{rev}-{i}"} for i in range(count)]
    data = "".join(json.dumps(row) + "\n" for row in rows).encode()
    readme = f"""---
configs:
- config_name: default
  data_files:
  - split: train
    path: train.jsonl
dataset_info:
  features:
  - name: text
    dtype: string
  splits:
  - name: train
    num_bytes: {len(data)}
    num_examples: {count}
  download_size: {len(data)}
  dataset_size: {len(data)}
---
Synthetic Hugging Face STANDIN dataset.
""".encode()
    return {"README.md": readme, "train.jsonl": data}


class Handler(BaseHTTPRequestHandler):
    def send(self, value, status=200, headers=None):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        if path == "/health":
            return self.send({"standin": "Hugging Face Hub"})
        if path == "/datasets-json":
            repo = "fixture/" + query.get("search", ["revisions"])[0]
            return self.send({"numTotalItems": 1, "datasets": [self.summary(repo)]})
        if path == "/api/datasets":
            return self.send([self.summary(query.get("id", ["fixture/revisions"])[0])])
        parts = path.strip("/").split("/")
        try:
            if parts[:2] == ["api", "datasets"] and len(parts) >= 4:
                repo = "/".join(parts[2:4])
                action = parts[4] if len(parts) > 4 else "revision"
                ref = parts[5] if len(parts) > 5 else "main"
                rev = revision(ref)
                data = files(repo, rev)
                if action == "revision":
                    return self.send({**self.summary(repo), "sha": PINS[rev], "private": False,
                                      "gated": False, "siblings": [{"rfilename": f} for f in data]})
                entries = [{"type": "file", "path": name, "size": len(body),
                            "oid": hashlib.sha1(body).hexdigest()} for name, body in data.items()]
                if action == "paths-info":
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                    entries = [e for e in entries if e["path"] in body.get("paths", [])]
                if action in ("tree", "paths-info"):
                    if len(parts) > 6:
                        entries = []
                    return self.send(entries)
            if parts[0] == "datasets" and len(parts) >= 6 and parts[3] == "resolve":
                repo = "/".join(parts[1:3])
                rev = revision(parts[4])
                data = files(repo, rev)
                name = "/".join(parts[5:])
                if name not in data:
                    return self.send({"error": "File not found"}, 404, {"X-Error-Code": "EntryNotFound"})
                body = data[name]
                return self.send(body, headers={"ETag": '"' + hashlib.sha1(body).hexdigest() + '"',
                                               "X-Repo-Commit": PINS[rev], "Accept-Ranges": "bytes"})
        except ValueError:
            return self.send({"error": "Unknown revision"}, 404, {"X-Error-Code": "RevisionNotFound"})
        return self.send({"error": "Unknown fixture route"}, 404, {"X-Error-Code": "EntryNotFound"})

    @staticmethod
    def summary(repo):
        return {"id": repo, "name": repo, "author": "fixture", "downloads": 1,
                "likes": 0, "tags": ["text"], "description": "Hugging Face STANDIN"}


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
