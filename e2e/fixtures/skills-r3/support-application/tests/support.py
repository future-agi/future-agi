"""Test-only registry over real HTTP; its responses pass the actual SDK verifier."""

import hashlib
import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4

from ctxhub_support.store import canonical
from fi.context.snapshot import FileMetadata, manifest_digest

BINDING = "00000000-0000-4000-8000-000000000100"
RESOURCE = "00000000-0000-4000-8000-000000000101"
VERSION = "00000000-0000-4000-8000-000000000102"
SESSION = "00000000-0000-4000-8000-000000000103"
LEASE = "00000000-0000-4000-8000-000000000104"
TOKEN = "00000000-0000-4000-8000-000000000105.synthetic-context-only"
RUNTIME = "sha256:" + "a" * 64


def metadata(files):
    return [
        FileMetadata(
            path, hashlib.sha256(content.encode()).hexdigest(), len(content.encode())
        )
        for path, content in sorted(files.items())
    ]


class FixtureRegistry:
    def __init__(self):
        catalog = json.loads(Path(os.environ["CTXHUB_CATALOG"]).read_text())
        assert len(catalog["skills"]) == 15
        self.catalog = catalog
        self.files = {
            "AGENTS.md": "Follow the reviewed synthetic policies. Load the relevant skill before using a business tool.\n"
        }
        links = []
        # The binary member remains in intake inventory; only supported text
        # members are attached. Do not silently claim 15 executable members.
        for skill in catalog["skills"]:
            if skill["unsupported"]:
                continue
            files = {item["path"]: item["content"] for item in skill["files"]}
            mount = "skills/" + skill["name"] + "/"
            self.files.update(
                {mount + path: content for path, content in files.items()}
            )
            links.append(
                {
                    "skill_id": skill["id"],
                    "version_id": str(uuid4()),
                    "version": 1,
                    "requested": {"version": 1},
                    "mount": mount,
                    "digest": manifest_digest(metadata(files)),
                }
            )
        self.files[".futureagi/context.json"] = canonical(
            {
                "schema": 1,
                "kind": "agent_context",
                "name": "synthetic-support",
                "entrypoint": "AGENTS.md",
                "owned_paths": ["AGENTS.md"],
                "links": sorted(links, key=lambda link: link["mount"]),
            }
        )
        self.digest = manifest_digest(metadata(self.files))
        self.available = True
        self.allowed = True
        self.lost_mapping = False
        self.mutate_resume = False
        self.corrupt_file = False
        self.opens = []
        self.requests = []
        self.fail_check = None
        self.checks = 0

    def handle(self, method, path, headers, body):
        from urllib.parse import parse_qs, urlsplit

        url = urlsplit(path)
        query = parse_qs(url.query)
        self.requests.append({"method": method, "path": url.path})
        if headers.get("Authorization") != "Context " + TOKEN:
            return 401, {
                "status": False,
                "error": {"code": "context_credential_invalid"},
            }
        if not self.available:
            return 503, {"status": False, "error": {"code": "skills_unavailable"}}
        prefix = f"/model-hub/skills/contexts/bindings/{BINDING}"
        if method == "POST" and url.path == prefix + "/sessions/":
            self.opens.append(body)
            if self.lost_mapping and body["resume_session_id"]:
                return 409, {
                    "status": False,
                    "error": {"code": "thread_context_unknown"},
                }
            return 200, {
                "status": True,
                "result": {
                    "session_id": SESSION,
                    "version_id": str(uuid4())
                    if self.mutate_resume and body["resume_session_id"]
                    else VERSION,
                    "version": 1,
                    "digest": self.digest,
                    "requested": {"label": "production"},
                    "binding_revision": 1,
                    "label_event_id": None,
                    "manifest_url": f"/model-hub/skills/contexts/{RESOURCE}/versions/1/manifest/",
                    "history_mode": "resumed" if body["resume_session_id"] else "fresh",
                    "access_check_url": prefix + f"/sessions/{SESSION}/access-check/",
                    "resolution_id": LEASE,
                    "resolution_expires_at": "2099-01-01T00:00:00Z",
                },
            }
        if method == "POST" and url.path.endswith("/access-check/"):
            self.checks += 1
            if not self.allowed or self.checks == self.fail_check:
                return 403, {"status": False, "error": {"code": "access_revoked"}}
            return 200, {
                "status": True,
                "result": {
                    "allowed": True,
                    "version_id": VERSION,
                    "digest": self.digest,
                    "checked_at": "2030-01-01T00:00:00Z",
                },
            }
        if headers.get("X-Context-Resolution") != LEASE:
            return 403, {"status": False, "error": {"code": "resolution_required"}}
        if url.path.endswith("/manifest/"):
            offset = int(query["file_offset"][0])
            limit = int(query["file_limit"][0])
            end = min(offset + limit, len(self.files))
            return 200, {
                "status": True,
                "result": {
                    "resource_id": RESOURCE,
                    "kind": "agent_context",
                    "name": "synthetic-support",
                    "description": "Synthetic support",
                    "version": 1,
                    "version_id": VERSION,
                    "digest": self.digest,
                    "requested": {"label": "production"},
                    "entrypoint": "AGENTS.md",
                    "descriptor_path": ".futureagi/context.json",
                    "files": [
                        {
                            "path": item.path,
                            "sha256": item.sha256,
                            "size_bytes": item.size_bytes,
                        }
                        for item in metadata(self.files)[offset:end]
                    ],
                    "file_count": len(self.files),
                    "file_offset": offset,
                    "next_file_offset": end if end < len(self.files) else None,
                    "resolution_id": LEASE,
                    "resolution_expires_at": "2099-01-01T00:00:00Z",
                },
            }
        if url.path.endswith("/files/"):
            name = query["path"][0]
            text = self.files[name]
            raw = text.encode()
            offset = int(query["offset"][0])
            content = text[offset:]
            return 200, {
                "status": True,
                "result": {
                    "resource_id": RESOURCE,
                    "version_id": VERSION,
                    "digest": self.digest,
                    "path": name,
                    "text": content + ("changed" if self.corrupt_file else ""),
                    "offset": offset,
                    "total_chars": len(text),
                    "next_offset": None,
                    "eof": True,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                },
            }
        return 404, {"status": False, "error": {"code": "unrecorded_route"}}

    def server(self):
        registry = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def handle_request(self):
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    or b"{}"
                )
                status, response = registry.handle(
                    self.command, self.path, self.headers, body
                )
                data = canonical(response).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = handle_request
            do_POST = handle_request

        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


@contextmanager
def serving(server):
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
