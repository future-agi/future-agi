"""Which tools the live smoke may call.

Read-only by default; a write only against a sandbox tenant (CONNECTORS-PLAN
CN13; scaling.md §5 L4).

- The manifest decides. Its annotations are the reviewed ones (L1 requires
  all four, and a read-only tool is never destructive), so a tool is
  read-only when its manifest says readOnlyHint and not destructiveHint.
- A tool the manifest does not declare is never called, whatever the vendor
  server says about it.
- A live annotation may narrow, never widen: a tool the manifest calls
  read-only is not called when the live server explicitly says it is not
  read-only, or that it is destructive. An absent live hint changes nothing
  (vendors often omit them; the manifest's review stands).
- A write tool, destructive ones included, is called only when the run
  allows writes (--allow-writes) and the keys entry says the test account is
  a sandbox tenant (sandbox: true). Either alone is not enough.
"""

from __future__ import annotations

from dataclasses import dataclass


def manifest_read_only(tool: dict) -> bool:
    a = tool.get("annotations") or {}
    return a.get("readOnlyHint") is True and a.get("destructiveHint") is not True


def live_widens(live_tool: dict | None) -> bool:
    """Whether a live tool's own annotations say it writes."""
    if not isinstance(live_tool, dict):
        return False
    a = live_tool.get("annotations")
    if not isinstance(a, dict):
        return False
    if a.get("readOnlyHint") is False:
        return True
    return a.get("readOnlyHint") is not True and a.get("destructiveHint") is True


@dataclass(frozen=True)
class Decision:
    allowed: bool
    write: bool = False
    reason: str | None = None  # why not, when not allowed


def decide(
    name: str,
    manifest_tools: dict,
    live_tools: dict | None,
    allow_writes: bool,
    sandbox: bool,
) -> Decision:
    """May the smoke call tool name? live_tools is the live list by name, or
    None when it was not listed (an HTTP-only check)."""
    tool = manifest_tools.get(name)
    if tool is None:
        return Decision(False, reason="not_in_manifest")
    if live_tools is not None and name not in live_tools:
        return Decision(False, reason="not_in_live_list")
    if manifest_read_only(tool):
        if live_tools is not None and live_widens(live_tools.get(name)):
            return Decision(False, reason="live_annotations_widen")
        return Decision(True)
    if not allow_writes:
        return Decision(False, write=True, reason="writes_not_allowed")
    if not sandbox:
        return Decision(False, write=True, reason="write_needs_sandbox")
    return Decision(True, write=True)


def widened(manifest_tools: dict, live_tools: dict) -> list:
    """The manifest's read-only tools whose live annotations say they write."""
    return sorted(
        n
        for n, t in manifest_tools.items()
        if manifest_read_only(t) and n in live_tools and live_widens(live_tools[n])
    )
