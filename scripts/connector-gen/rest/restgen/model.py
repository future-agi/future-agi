"""The normalized operation model every spec reader produces.

A reader indexes a spec's operations cheaply (OpRef) and converts one fully
(Operation) only when the overlay selects it: Microsoft Graph's Teams
module alone has 885 paths."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

PATH_PARAM = re.compile(r"\{([^{}]+)\}")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


@dataclass
class Param:
    """One input of an operation, its schema already JSON Schema 2020-12."""

    name: str
    location: str  # path | query | header | cookie
    required: bool
    description: str
    schema: dict
    # Why the parameter cannot be carried as a tool input, or "" when it can
    # (a header, an object in a query, a non-JSON body field, ...).
    unsupported: str = ""
    # A serialization note appended to whatever description the tool uses.
    note: str = ""


@dataclass
class Body:
    """The JSON request body of an operation."""

    required: bool
    description: str
    schema: dict
    unsupported: str = ""


@dataclass
class Operation:
    method: str  # upper case
    path: str  # relative to the server's base path; {name} placeholders
    label: str  # how messages name it
    params: list[Param]
    body: Body | None
    # Scopes the spec says the operation accepts; None when it says nothing.
    accepted_scopes: list[str] | None
    # The 2xx response body schema (2020-12, read side), or None if unknown.
    response_schema: Callable[[], dict | None] = field(repr=False, default=lambda: None)
    # The servers the operation may be called on (its own, else the spec's).
    servers: list[str] = field(default_factory=list)


@dataclass
class OpRef:
    """An operation's identity, before conversion."""

    method: str
    path: str
    ids: dict[str, str]  # operationId | id | name -> value, as the format has
    raw: Any = field(repr=False, default=None)


class SpecReader:
    """What every format reader provides."""

    format = ""

    def servers(self) -> list[str]:
        """Declared API servers: https://host[/base], no trailing slash."""
        raise NotImplementedError

    def credential_params(self) -> set[str]:
        """Parameter names the spec's own security schemes say carry the
        credential (an apiKey in a query or header)."""
        return set()

    def refs(self) -> list[OpRef]:
        raise NotImplementedError

    def operation(self, ref: OpRef) -> Operation:
        raise NotImplementedError


def normalize_server(url: str) -> str:
    return url.rstrip("/")


def base_path(server: str) -> str:
    """The path part of a server URL, without a trailing slash."""
    return urlsplit(server).path.rstrip("/")


def origin(server: str) -> str:
    parts = urlsplit(server)
    return f"{parts.scheme}://{parts.netloc}"


def find_refs(refs: list[OpRef], matcher: dict[str, str]) -> list[OpRef]:
    """Operations that match every key the overlay gave."""
    out = []
    for ref in refs:
        ok = True
        for key, want in matcher.items():
            if key == "method":
                ok = ref.method == want.upper()
            elif key == "path":
                ok = ref.path == want
            else:
                ok = ref.ids.get(key) == want
            if not ok:
                break
        if ok:
            out.append(ref)
    return out
