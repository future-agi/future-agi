"""Where the generator reads and writes in the catalog layout:

    apps/<app>/providers/<provider>.yaml          the rest provider record
    apps/<app>/providers/<provider>/tools.yaml    the curated overlay (input)
    apps/<app>/providers/<provider>/<spec>        the vendor spec it names
    apps/<app>/providers/<provider>/tools.generated.json   the output

The provider record is the single source of the spec's pin: its
upstream.spec url, format, sha256 and licence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .build import ProviderContext, build_tool
from .discovery import DiscoveryReader
from .l1 import check_provider
from .model import SpecReader, normalize_server
from .openapi3 import OpenAPI3Reader
from .overlay import parse_overlay
from .postman import PostmanReader
from .problems import GenError, Problems
from .secrets import find_secret_line
from .specfile import SpecError, load_spec
from .swagger2 import Swagger2Reader
from .yamlio import load_yaml

OVERLAY = "tools.yaml"
GENERATED = "tools.generated.json"
OUTPUT_SCHEMA = "connector-manifest/v1#tools-generated"
READERS = {
    "openapi-3": OpenAPI3Reader,
    "swagger-2": Swagger2Reader,
    "postman-2.1": PostmanReader,
    "google-discovery": DiscoveryReader,
}


@dataclass
class Provider:
    app_id: str
    provider_id: str
    record_path: Path
    dir: Path
    spec: dict[str, str]
    oauth2_scopes: list[str] | None
    default_posture: str
    items: list[dict]
    verify_tool: str
    query_credential_names: set[str]  # credential parts and constants placed in the query, lowercased


def overlays(catalog: Path, apps: list[str] | None = None) -> list[Path]:
    found = sorted(catalog.glob(f"apps/*/providers/*/{OVERLAY}"))
    if apps:
        found = [p for p in found if p.parents[2].name in apps]
    return found


def orphans(catalog: Path) -> list[Path]:
    """Generated files whose overlay is gone: nothing can regenerate them."""
    return [p for p in sorted(catalog.glob(f"apps/*/providers/*/{GENERATED}")) if not (p.parent / OVERLAY).exists()]


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def load_provider(overlay_path: Path, rel: str, probs: Problems) -> Provider | None:
    pdir = overlay_path.parent
    record = pdir.parent / f"{pdir.name}.yaml"
    app_id = pdir.parents[1].name
    where = rel.rsplit("/", 1)[0] + ".yaml"
    if not record.exists():
        probs.add(f"{where}: does not exist; tools.yaml sits beside a rest provider's record")
        return None
    try:
        doc = load_yaml(record.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        probs.add(f"{where}: {exc}")
        return None
    if not isinstance(doc, dict):
        probs.add(f"{where}: is not a provider record")
        return None
    get = lambda *keys: _dig(doc, keys)  # noqa: E731
    if get("schema") != "connector-manifest/v1#provider" or get("kind") != "rest":
        probs.add(f"{where}: is not a rest provider (schema connector-manifest/v1#provider, kind rest)")
        return None
    if get("tools", "source") != "operations":
        probs.add(f"{where}: tools.source must be operations")
    if get("tools", "generated") != GENERATED:
        probs.add(f"{where}: tools.generated must name {GENERATED}, the file this generator writes")
    spec = get("upstream", "spec")
    pinned = ("url", "format", "sha256", "licence")
    if not isinstance(spec, dict) or not all(isinstance(spec.get(k), str) and spec[k] for k in pinned):
        probs.add(f"{where}: upstream.spec must pin the spec: url, format, sha256 and licence")
        return None
    auth_kind = get("auth", "kind")
    scopes = get("auth", "oauth2", "scopes") if auth_kind == "oauth2" else None
    if auth_kind == "oauth2" and not isinstance(scopes, list):
        probs.add(f"{where}: auth.oauth2.scopes must be a list")
        scopes = []
    items = get("tools", "items")
    verify = get("auth", "verify", "tool")
    parts = get("auth", "credentialParts")
    query_names = {
        str(part["name"]).lower()
        for part in (parts if isinstance(parts, list) else [])
        if isinstance(part, dict) and part.get("in") == "query" and isinstance(part.get("name"), str)
    }
    return Provider(
        app_id=app_id,
        provider_id=pdir.name,
        record_path=record,
        dir=pdir,
        spec={k: spec[k] for k in ("url", "format", "sha256", "licence")},
        oauth2_scopes=[str(s) for s in scopes] if scopes is not None else None,
        default_posture=str(get("tools", "defaultPosture") or "ask"),
        items=_jsonable(items) if isinstance(items, list) else [],
        verify_tool=verify if isinstance(verify, str) else "",
        query_credential_names=query_names,
    )


def _dig(doc: Any, keys: tuple) -> Any:
    for k in keys:
        if not isinstance(doc, dict):
            return None
        doc = doc.get(k)
    return doc


def generate(overlay_path: Path, catalog: Path) -> dict:
    """The tools.generated.json document for one overlay. Raises GenError."""
    rel = overlay_path.relative_to(catalog).as_posix()
    probs = Problems(rel)
    text = overlay_path.read_text(encoding="utf-8")
    rule, line = find_secret_line(text)
    if rule:
        raise GenError([f"{rel}: line {line}: holds a value shaped like a {rule}; overlay data never holds a secret"])
    try:
        overlay = parse_overlay(load_yaml(text), probs)
    except ValueError as exc:
        raise GenError([f"{rel}: {exc}"]) from None
    provider = load_provider(overlay_path, rel, probs)
    probs.raise_if_any()
    assert overlay is not None and provider is not None
    spec_path = (provider.dir / overlay.spec).resolve()
    if not spec_path.is_relative_to(provider.dir.resolve()) or not spec_path.is_file():
        raise GenError([f"{rel}: spec {overlay.spec} is not a file inside {provider.dir.name}/"])
    try:
        doc = load_spec(spec_path, provider.spec["sha256"], provider.spec["format"])
    except SpecError as exc:
        raise GenError([f"{rel}: spec {overlay.spec}: {exc}"]) from None
    reader: SpecReader = READERS[provider.spec["format"]](doc)
    server = _server(reader, overlay, probs)
    if server:
        _vendor_host(server, catalog / "apps" / provider.app_id / "app.yaml", probs)
    ctx = ProviderContext(
        app_id=provider.app_id,
        oauth2_scopes=provider.oauth2_scopes,
        default_posture=provider.default_posture,
        server=server,
        credential_params=set(overlay.credential_params) | reader.credential_params(),
        query_credential_names=provider.query_credential_names,
    )
    refs = reader.refs()
    tools = [tool for t in overlay.tools if (tool := build_tool(reader, refs, t, ctx, probs))]
    probs.raise_if_any()
    probs.items += check_provider(tools, provider.items, provider.oauth2_scopes, provider.verify_tool, rel)
    out = {"schema": OUTPUT_SCHEMA, "spec": {"url": provider.spec["url"], "sha256": provider.spec["sha256"]}, "tools": tools}
    rule, line = find_secret_line(json.dumps(out, indent=2, ensure_ascii=False))
    if rule:
        probs.add(f"the output would hold a value shaped like a {rule}")
    probs.raise_if_any()
    return out


def _server(reader: SpecReader, overlay, probs: Problems) -> str:
    declared = [s for s in reader.servers() if s.startswith("https://")]
    if overlay.server:
        server = normalize_server(overlay.server)
        if declared and server not in declared:
            probs.add(f"server {server} is not one the spec declares ({', '.join(declared)})")
        elif not declared and not overlay.server_source:
            probs.add("the spec declares no https server, so server needs serverSource: the vendor page that names it")
        return server
    if not declared:
        probs.add("the spec declares no https server; set server and serverSource")
        return ""
    return declared[0]


def _vendor_host(server: str, app_yaml: Path, probs: Problems) -> None:
    """The runtime sends the user's token to the server, so its host must be
    one of the app's curated vendor domains (app.yaml vendor.domains) or
    under one."""
    host = (urlsplit(server).hostname or "").lower()
    try:
        app = load_yaml(app_yaml.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        probs.add(f"{app_yaml.name}: cannot be read ({exc}), so the server host cannot be checked")
        return
    domains = _dig(app, ("vendor", "domains"))
    domains = [d.lower() for d in domains if isinstance(d, str)] if isinstance(domains, list) else []
    if not any(host == d or host.endswith("." + d) for d in domains):
        probs.add(f"server {server}: host {host} is not under the app's vendor.domains ({', '.join(domains) or 'none'})")


def render(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def current(path: Path) -> Any:
    """The parsed output already on disk, or None. Compared as data, so a
    formatter that rewrites the file's layout is not drift."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
