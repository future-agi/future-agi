"""Postman Collection v2.1 reader (postman-2.1).

A collection carries no types: query and path inputs are strings, and a raw
JSON body's schema is inferred from the example the vendor saved. The
server is the request's host after collection variables are filled in
(Adyen's `{{baseUrl}}`). A request whose host keeps an unfilled variable,
or is not https, declares no server, so it is not yet refused: it is
generated against the provider's server (README.md, "Known gaps")."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from .model import Body, Operation, OpRef, Param, SpecReader, normalize_server
from .schemaconv import infer

_VAR = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_IGNORED_HEADERS = {"content-type", "accept", "authorization"}


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("content")
    return value.strip() if isinstance(value, str) else ""


class PostmanReader(SpecReader):
    format = "postman-2.1"

    def __init__(self, doc: dict):
        self.doc = doc
        self.vars = {
            v["key"]: v.get("value")
            for v in doc.get("variable") or []
            if isinstance(v, dict) and isinstance(v.get("key"), str)
        }
        self._refs: list[OpRef] = []
        self._walk(doc.get("item") or [], doc.get("auth"))

    def _fill(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            value = self.vars.get(m.group(1))
            return value if isinstance(value, str) and value else m.group(0)

        return _VAR.sub(sub, text)

    def _split(self, url: Any) -> tuple[str, str]:
        """(server, path) of a request URL, the path with {name} variables."""
        if isinstance(url, str):
            url = {"raw": url}
        if not isinstance(url, dict):
            return "", ""
        host = url.get("host")
        if isinstance(host, list):
            host_text = ".".join(str(h) for h in host)
            protocol = url.get("protocol")
            if protocol and "://" not in host_text:
                host_text = f"{protocol}://{host_text}"
            segments = [str(s) for s in url.get("path") or []]
        else:
            raw = str(url.get("raw") or "").split("?", 1)[0]
            if "://" in raw:
                start = raw.index("://") + 3
            elif raw.startswith("{{"):
                start = raw.find("}}") + 2
            else:
                start = 0
            end = raw.find("/", start)
            host_text = raw if end < 0 else raw[:end]
            segments = [s for s in ("" if end < 0 else raw[end:]).split("/") if s]
        server = self._fill(host_text)
        parts = urlsplit(server)
        server_path = parts.path.rstrip("/")
        server = normalize_server(f"{parts.scheme}://{parts.netloc}{server_path}") if parts.netloc else server
        segments = [self._fill(s) for s in segments]
        path = "/" + "/".join("{" + s[1:] + "}" if s.startswith(":") else s for s in segments)
        return server, path

    def _walk(self, items: list, auth: Any) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("item"), list):
                self._walk(item["item"], item.get("auth") or auth)
                continue
            req = item.get("request")
            if not isinstance(req, dict):
                continue
            server, path = self._split(req.get("url"))
            name = str(item.get("name", ""))
            self._refs.append(
                OpRef(str(req.get("method", "GET")).upper(), path, {"name": name}, (item, server, req.get("auth") or auth))
            )

    def servers(self) -> list[str]:
        seen: list[str] = []
        for ref in self._refs:
            server = ref.raw[1]
            if server.startswith("https://") and "{{" not in server and server not in seen:
                seen.append(server)
        return seen

    def credential_params(self) -> set[str]:
        names: set[str] = set()
        for ref in self._refs:
            names |= _auth_names(ref.raw[2])
        return names

    def refs(self) -> list[OpRef]:
        return list(self._refs)

    def operation(self, ref: OpRef) -> Operation:
        item, server, auth = ref.raw
        req = item["request"]
        url = req.get("url") if isinstance(req.get("url"), dict) else {}
        params: list[Param] = []
        for v in url.get("variable") or []:
            if isinstance(v, dict) and isinstance(v.get("key"), str):
                params.append(Param(v["key"], "path", True, _text(v.get("description")), {"type": "string"}))
        named = {p.name for p in params}
        for seg in re.findall(r"\{([^{}]+)\}", ref.path):
            if seg not in named:
                params.append(Param(seg, "path", True, "", {"type": "string"}))
        for q in url.get("query") or []:
            if isinstance(q, dict) and isinstance(q.get("key"), str):
                params.append(Param(q["key"], "query", False, _text(q.get("description")), {"type": "string"}))
        credential = _auth_names(auth)
        for h in req.get("header") or []:
            if not isinstance(h, dict) or h.get("disabled") or not isinstance(h.get("key"), str):
                continue
            if h["key"].lower() in _IGNORED_HEADERS or h["key"] in credential:
                continue
            params.append(Param(h["key"], "header", False, _text(h.get("description")), {}, "a header cannot be a tool input"))
        return Operation(
            method=ref.method,
            path=ref.path,
            label=f"{ref.method} {ref.path} ({ref.ids['name']})",
            params=params,
            body=self._body(req.get("body")),
            accepted_scopes=None,
            response_schema=lambda: _example_response(item),
            servers=[server] if server.startswith("https://") and "{{" not in server else [],
        )

    def _body(self, body: Any) -> Body | None:
        if not isinstance(body, dict) or body.get("mode") in (None, "none"):
            return None
        mode = body.get("mode")
        if mode == "raw" and not str(body.get("raw") or "").strip():
            return None  # an empty raw body: the request sends none
        out = Body(True, "", {})
        if mode != "raw":
            out.unsupported = f"the request body is {mode}, not JSON"
            return out
        raw = body.get("raw")
        try:
            value = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
        except json.JSONDecodeError:
            value = None
        if not isinstance(value, (dict, list)):
            out.unsupported = "the saved example body is not JSON"
            return out
        out.schema = infer(value)
        return out


def _auth_names(auth: Any) -> set[str]:
    """Where a collection's auth puts the credential: an apikey's key name."""
    if not isinstance(auth, dict) or auth.get("type") != "apikey":
        return set()
    for entry in auth.get("apikey") or []:
        if isinstance(entry, dict) and entry.get("key") == "key" and isinstance(entry.get("value"), str):
            return {entry["value"]}
    return {"key"}


def _example_response(item: dict) -> dict | None:
    for resp in item.get("response") or []:
        if not isinstance(resp, dict) or not str(resp.get("code", "")).startswith("2"):
            continue
        try:
            value = json.loads(resp.get("body") or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, (dict, list)):
            return infer(value)
    return None
