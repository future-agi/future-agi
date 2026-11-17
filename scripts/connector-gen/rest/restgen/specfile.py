"""Reading a vendor spec: the pinned bytes, their format, and no secrets.

A spec is stored as the bytes its URL served, optionally gzip-compressed
(`.gz`), so the pin is the sha256 of the decompressed bytes and a formatter
that rewrites JSON or YAML on commit cannot move it. Nothing here touches
the network: a spec is only ever read from a file."""

from __future__ import annotations

import gzip
import hashlib
import io
from pathlib import Path
from typing import Any

from .secrets import find_secret_line
from .yamlio import load_document

MAX_STORED_BYTES = 16 << 20  # the catalog loader reads no larger file
MAX_SPEC_BYTES = 64 << 20  # Microsoft Graph's full v1.0 description is 44 MB

FORMATS = ("openapi-3", "swagger-2", "postman-2.1", "google-discovery")
POSTMAN_21 = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


class SpecError(ValueError):
    pass


def read_bytes(path: Path) -> bytes:
    """The spec's bytes as served: decompressed when the file ends in .gz."""
    size = path.stat().st_size
    if size > MAX_STORED_BYTES:
        raise SpecError(f"is {size} bytes; a stored spec is at most {MAX_STORED_BYTES}")
    data = path.read_bytes()
    if path.suffix != ".gz":
        if len(data) > MAX_SPEC_BYTES:
            raise SpecError(f"is larger than {MAX_SPEC_BYTES} bytes")
        return data
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
        try:
            raw = gz.read(MAX_SPEC_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise SpecError(f"is not a valid gzip file ({exc})") from None
    if len(raw) > MAX_SPEC_BYTES:
        raise SpecError(f"decompresses to more than {MAX_SPEC_BYTES} bytes")
    return raw


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_format(doc: Any) -> str:
    """The format a parsed spec is written in, or "" when none of the four."""
    if not isinstance(doc, dict):
        return ""
    openapi = doc.get("openapi")
    if isinstance(openapi, str) and openapi.startswith("3."):
        return "openapi-3"
    if doc.get("swagger") == "2.0":
        return "swagger-2"
    info = doc.get("info")
    if isinstance(info, dict) and info.get("schema") == POSTMAN_21:
        return "postman-2.1"
    if doc.get("kind") == "discovery#restDescription" and doc.get("discoveryVersion"):
        return "google-discovery"
    return ""


# Specs parsed in this process, by (sha256, format), the oldest dropped
# first: Outlook and Teams share Microsoft Graph's 44 MB description, which
# takes seconds to parse. Readers never change a parsed document.
_parsed: dict[tuple[str, str], dict] = {}
_KEEP_PARSED = 4


def load_spec(path: Path, sha256: str, fmt: str) -> dict:
    """Read, pin-check, secret-scan and parse a spec declared as fmt.

    Raises SpecError naming what is wrong; a secret is named by its rule and
    line, never quoted."""
    if fmt not in FORMATS:
        raise SpecError(f"format {fmt!r} is not one of {', '.join(FORMATS)}")
    raw = read_bytes(path)
    actual = sha256_hex(raw)
    if actual != sha256:
        raise SpecError(
            f"sha256 of the spec bytes is {actual}, but the provider pins {sha256} "
            "(re-pin upstream.spec after reviewing the new spec, then regenerate)"
        )
    if (actual, fmt) in _parsed:
        return _parsed[(actual, fmt)]
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SpecError("is not UTF-8") from None
    rule, line = find_secret_line(text)
    if rule:
        raise SpecError(
            f"line {line}: holds a value shaped like a {rule}; a spec with a "
            "secret-shaped value is never stored or used (pick the vendor's "
            "variant without examples, or another spec)"
        )
    try:
        doc = load_document(text)
    except ValueError as exc:
        raise SpecError(str(exc)) from None
    found = detect_format(doc)
    if found != fmt:
        raise SpecError(
            f"is declared {fmt} in upstream.spec.format, but reads as "
            f"{found or 'none of ' + ', '.join(FORMATS)}"
        )
    while len(_parsed) >= _KEEP_PARSED:
        del _parsed[next(iter(_parsed))]
    _parsed[(actual, fmt)] = doc
    return doc
