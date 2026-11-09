"""The credential parts each provider of the connector catalog declares, as
Django's push applies them (AM-C22, the CW2 preamble).

This is the gateway's credtemplate.Catalog (catalogparts.FromManifest over
the catalog the gateway embeds), read from CN6's Django copy of the bundle
(connector-catalog-bundle/v1): for <app>/<provider>, the provider's version
and its auth.credentialParts, in declared order. ``agentcc-catalog check``
keeps the copy byte-identical to the bundle the gateway embeds
(dist/catalog.json), so Django and the gateway place a catalog_ref server's
key by the same records. mcp_credential_placement.check_catalog_secret
applies them.

A copy that is missing or is not a bundle is no catalog (None), so every
catalog_ref is refused, as a gateway with no catalog refuses it. A provider
record that cannot be read, or that is repeated, is left out, so its
catalog_ref is refused as one the catalog does not have. Never a value is
logged: the records hold none but constants. No Django import.
"""

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

BUNDLE_SCHEMA = "connector-catalog-bundle/v1"


@dataclass(frozen=True)
class Declared:
    """One credential part a provider declares (credtemplate.Declared): a
    secret part, which the push carries, or a non-secret part, whose
    constant value the catalog carries and the push never does."""

    where: str  # header, query or url_variable
    name: str
    secret: bool
    value: str = ""  # non-secret parts only
    prefix: str = ""  # header parts only


class CatalogParts:
    """The declared parts of every provider of one bundle, by (app, provider)."""

    def __init__(self, providers):
        self._providers = dict(providers)

    def credential_parts(self, app, provider):
        """``(version, (Declared, ...))`` for the provider, or None when the
        catalog does not have it."""
        return self._providers.get((app, provider))

    def __len__(self):
        return len(self._providers)


def _text(value):
    return value if isinstance(value, str) else None


def _declared(part):
    """A Declared for one auth.credentialParts entry, or None when it cannot
    be read."""
    if not isinstance(part, dict):
        return None
    where, name, secret = (
        _text(part.get("in")),
        _text(part.get("name")),
        part.get("secret"),
    )
    value, prefix = part.get("value", ""), part.get("prefix", "")
    if not where or not name or not isinstance(secret, bool):
        return None
    if not isinstance(value, str) or not isinstance(prefix, str):
        return None
    return Declared(where, name, secret, value, prefix)


def _provider(record):
    """``((app, provider), (version, parts))`` for one provider record, or None
    when it cannot be read."""
    if not isinstance(record, dict):
        return None
    app, provider, version = (_text(record.get(k)) for k in ("app", "id", "version"))
    auth = record.get("auth")
    if not app or not provider or not version or not isinstance(auth, dict):
        return None
    raw_parts = auth.get("credentialParts", [])
    if not isinstance(raw_parts, list):
        return None
    parts = tuple(_declared(part) for part in raw_parts)
    if any(part is None for part in parts):
        return None
    return (app, provider), (version, parts)


def from_providers(records):
    """The CatalogParts of a bundle's ``providers`` records. A record that
    cannot be read, and every copy of a repeated (app, provider), is left
    out."""
    providers, repeated = {}, set()
    for record in records if isinstance(records, list) else []:
        entry = _provider(record)
        if entry is None:
            logger.warning("connector_catalog_parts_record_unreadable")
            continue
        key, parts = entry
        if key in providers:
            repeated.add(key)
        providers[key] = parts
    for key in repeated:
        logger.warning(
            "connector_catalog_parts_provider_repeated", provider="/".join(key)
        )
        providers.pop(key)
    return CatalogParts(providers)


def parse(raw):
    """The CatalogParts of a bundle document, or None when it is not a
    connector-catalog-bundle/v1 document with a providers list."""
    if not isinstance(raw, dict) or raw.get("schema") != BUNDLE_SCHEMA:
        return None
    if not isinstance(raw.get("providers"), list):
        return None
    return from_providers(raw["providers"])


_cache_lock = threading.Lock()
_cache = None  # ((path, mtime_ns, size, ino), CatalogParts | None)


def load(path):
    """The CatalogParts of the bundle at ``path``, read once per file change
    (keyed as connector_catalog.load_bundle keys its cache), or None when it
    is missing or is not a bundle."""
    global _cache
    path = Path(path)
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino)
        with _cache_lock:
            if _cache is not None and _cache[0] == key:
                return _cache[1]
        parts = parse(json.loads(path.read_bytes()))
    except (OSError, ValueError) as exc:
        logger.warning(
            "connector_catalog_parts_unavailable",
            path=str(path),
            error_type=type(exc).__name__,
        )
        return None
    if parts is None:
        logger.warning(
            "connector_catalog_parts_unavailable",
            path=str(path),
            error_type="not_a_bundle",
        )
    with _cache_lock:
        _cache = (key, parts)
    return parts
