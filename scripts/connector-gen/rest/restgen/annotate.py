"""A tool's four MCP annotations, its posture and its scopes.

Derived where HTTP decides (a GET reads; a PUT, a PATCH and a DELETE write
and are never read-only; a DELETE destroys; a read, a PUT and a DELETE are
idempotent), stated by the reviewer where only a person knows (whether a
write destroys data, whether a POST only reads, whether the tool reaches an
open world), and checked as CN1's L1 checks them (manifest/l1.go): a write
is never auto, a destructive tool is denied by default."""

from __future__ import annotations

import re

from .model import Operation
from .overlay import ToolOverlay

SCOPE = re.compile(r"^[!-~]{1,256}$")
_IDEMPOTENT = ("GET", "PUT", "DELETE")
# The only methods a tool can read with: a GET always reads; a POST reads
# when the reviewer says so (a search or free/busy query sent as a body).
# Every other method (PUT, PATCH, DELETE) changes the resource it names.
_CAN_READ = ("GET", "POST")


def annotations_for(method: str, t: ToolOverlay, fail) -> dict[str, bool]:
    given = t.annotations
    if method == "GET":
        if given.get("readOnlyHint") is False:
            fail("annotations.readOnlyHint: a GET is read-only")
        read_only = True
    elif method in _CAN_READ:
        read_only = given.get("readOnlyHint", False)
    else:
        if given.get("readOnlyHint") is True:
            fail(f"annotations.readOnlyHint: a {method} writes, so it is never read-only")
        read_only = False
    if method == "DELETE":
        # A DELETE is destructive whatever the overlay says; a wrong hint is
        # a problem, and the posture check below sees it as destructive.
        if "destructiveHint" not in given:
            fail("annotations.destructiveHint: a DELETE tool writes; the reviewer states whether it destroys data")
        elif given["destructiveHint"] is not True:
            fail("annotations.destructiveHint: a DELETE is destructive")
        destructive = True
    elif read_only:
        if given.get("destructiveHint") is True:
            fail("annotations.destructiveHint: a read-only tool destroys nothing")
        destructive = False
    elif "destructiveHint" not in given:
        fail(f"annotations.destructiveHint: a {method} tool writes; the reviewer states whether it destroys data")
        destructive = False
    else:
        destructive = given["destructiveHint"]
    if "openWorldHint" not in given:
        fail("annotations.openWorldHint: the reviewer states whether the tool reaches an open world")
    return {
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": given.get("idempotentHint", read_only or method in _IDEMPOTENT),
        "openWorldHint": given.get("openWorldHint", True),
    }


def check_posture(t: ToolOverlay, ann: dict[str, bool], default_posture: str, fail) -> None:
    posture = t.posture or default_posture
    if ann["destructiveHint"] and posture != "deny":
        fail(f"posture: the tool is destructive, so its posture is deny (it is {posture})")
    elif not ann["readOnlyHint"] and posture == "auto":
        fail("posture: the tool writes, so its posture is ask or deny, not auto")


def check_scopes(t: ToolOverlay, op: Operation, oauth2_scopes: list[str] | None, fail) -> None:
    if oauth2_scopes is None:
        if t.scopes is not None:
            fail("scopes: the provider does not use oauth2, so its tools list no scopes")
        return
    if t.scopes is None:
        fail("scopes: an oauth2 provider's tool lists the scopes it needs (scopes: [] when none)")
        return
    if t.scopes and not op.accepted_scopes and not t.scope_source:
        # Microsoft Graph's description says `azureaadv2: []` everywhere: a
        # scope the spec does not name is a fact that needs its own source.
        fail(f"scopes: the spec names no scopes for {op.label}; add scopeSource with the vendor page that lists them")
    for s in t.scopes:
        if not SCOPE.match(s):
            fail(f"scopes: {s!r} is not a valid scope")
        elif s not in oauth2_scopes:
            fail(f"scopes: {s} is not in the provider's auth.oauth2.scopes")
        elif op.accepted_scopes and s not in op.accepted_scopes and not t.scope_source:
            fail(
                f"scopes: the spec says {op.label} accepts {', '.join(op.accepted_scopes)}, not {s} "
                "(add scopeSource with the vendor page that says otherwise)"
            )
