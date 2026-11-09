"""Read the connector catalog: providers, their tools and their cassettes.

The catalog is agentcc-gateway/catalog/connectors (CONNECTORS-PLAN §2.3),
validated on every commit by the Go loader (internal/connectorcatalog/
manifest, L0 and L1). This reader takes only what a live smoke needs and
reads the YAML the way that loader does: an unquoted date stays text.
Cassettes are agentcc-cassette/v1 files (cmd/agentcc-cassette) under a
provider's fixtures/ directory: an MCP cassette's tools/call exchanges are
what the live smoke repeats; an HTTP cassette (K2's vendor REST exchanges) is
only counted, because it records no vendor host to repeat it against.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised by the CLI's message
    yaml = None

CASSETTE_FORMAT = "agentcc-cassette/v1"
APP_ID = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]{1,23}$")


class CatalogError(ValueError):
    """The catalog cannot be read."""


_LOADER = None


def _loader():
    global _LOADER
    if yaml is None:
        raise CatalogError("PyYAML is required: python3 -m pip install PyYAML==6.0.3")
    if _LOADER is not None:
        return _LOADER

    class Loader(yaml.SafeLoader):
        """SafeLoader that keeps dates as text, as the Go loader does."""

    Loader.yaml_implicit_resolvers = {
        first: [
            (tag, regexp)
            for tag, regexp in resolvers
            if tag != "tag:yaml.org,2002:timestamp"
        ]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    _LOADER = Loader
    return Loader


def load_yaml(path: Path):
    loader = _loader()
    try:
        with path.open("rb") as fh:
            doc = yaml.load(fh, Loader=loader)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError:
        raise CatalogError(f"{path}: not valid YAML") from None
    if not isinstance(doc, dict):
        raise CatalogError(f"{path}: must be a YAML mapping")
    return doc


@dataclass
class Call:
    """One recorded tools/call: its arguments and what the server answered."""

    cassette: str
    tool: str
    arguments: dict
    result: object | None  # the JSON-RPC result, None when it was an error


@dataclass
class Provider:
    app: str
    id: str
    path: Path
    record: dict
    generated_tools: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    # K2 REST recordings: counted, not compared live
    http_cassettes: list = field(default_factory=list)
    invalid_cassettes: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.app}/{self.id}"

    @property
    def kind(self) -> str:
        return self.record.get("kind", "")

    @property
    def transport(self) -> dict:
        return self.record.get("transport") or {}

    @property
    def auth(self) -> dict:
        return self.record.get("auth") or {}

    @property
    def tools(self) -> dict:
        return self.record.get("tools") or {}

    @property
    def snapshot(self) -> dict | None:
        return self.tools.get("snapshot")

    @property
    def upstream_spec(self) -> dict | None:
        return (self.record.get("upstream") or {}).get("spec")

    def manifest_tools(self) -> dict:
        """The tools the manifest declares (curated and generated), by name."""
        out = {}
        for tool in list(self.tools.get("items") or []) + list(self.generated_tools):
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                out[tool["name"]] = tool
        return out


def _read_cassette(path: Path, rel: str, provider: Provider) -> None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        provider.invalid_cassettes.append(rel)
        return
    if not isinstance(doc, dict) or doc.get("format") != CASSETTE_FORMAT:
        return  # another fixture file, not a cassette
    kind = doc.get("kind")
    if kind == "mcp" and isinstance(doc.get("mcp"), dict):
        calls = []
        for x in doc["mcp"].get("exchanges") or []:
            if not isinstance(x, dict) or x.get("method") != "tools/call":
                continue
            params = x.get("params") or {}
            name = params.get("name") if isinstance(params, dict) else None
            args = params.get("arguments") if isinstance(params, dict) else None
            if not isinstance(name, str):
                provider.invalid_cassettes.append(rel)
                return
            result = x.get("result") if "error" not in x else None
            calls.append(
                Call(rel, name, args if isinstance(args, dict) else {}, result)
            )
        provider.calls.extend(calls)
        return
    if kind == "http" and isinstance(doc.get("http"), dict):
        provider.http_cassettes.append(rel)
        return
    provider.invalid_cassettes.append(rel)


def _read_provider(app: str, path: Path) -> Provider:
    record = load_yaml(path)
    pid = path.stem
    if record.get("app") != app or record.get("id") != pid:
        raise CatalogError(
            f"{path}: app and id must equal the directory and file names"
        )
    provider = Provider(app=app, id=pid, path=path, record=record)
    side = path.parent / pid
    generated = (record.get("tools") or {}).get("generated")
    if generated:
        try:
            doc = json.loads((side / generated).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise CatalogError(f"{side / generated}: unreadable") from None
        provider.generated_tools = [
            t for t in doc.get("tools") or [] if isinstance(t, dict)
        ]
    fixtures = side / "fixtures"
    if fixtures.is_dir():
        for cassette in sorted(fixtures.rglob("*.json")):
            _read_cassette(cassette, cassette.relative_to(side).as_posix(), provider)
    return provider


def load(root: Path) -> dict:
    """Every provider in the catalog, by app id, in id order."""
    root = Path(root)
    if not root.is_dir():
        raise CatalogError(f"{root}: no such catalog directory")
    apps = {}
    apps_dir = root / "apps"
    if not apps_dir.is_dir():
        return apps
    for app_dir in sorted(p for p in apps_dir.iterdir() if p.is_dir()):
        app = app_dir.name
        if not APP_ID.match(app):
            raise CatalogError(f"{app_dir}: not an app id")
        providers = []
        prov_dir = app_dir / "providers"
        if prov_dir.is_dir():
            for path in sorted(prov_dir.glob("*.yaml")):
                if not PROVIDER_ID.match(path.stem):
                    raise CatalogError(f"{path}: not a provider id")
                providers.append(_read_provider(app, path))
        apps[app] = providers
    return apps
