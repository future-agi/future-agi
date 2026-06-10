# ruff: noqa: E402
"""Spot-check write bridge tools via setup -> call -> ORM assert -> compensate.

Two entry shapes are accepted in ROUNDTRIPS (PHASES.md:117 acceptance bar):

1. Legacy 3-tuple — ``(create_tool, create_args, delete_tool)``:
   creates a throwaway row through the create bridge, harvests the new id,
   deletes it through the delete bridge. Net-zero on the account.

2. Generalized dict::

       {
           "tool": "complete_queue_item",          # required
           "args": {...} ,                          # required (dict)
           "setup": ("create_x", {...}) | callable, # optional; callable(ctx)
           "assert_orm": callable(ctx, result) -> bool,  # optional ORM check
           "compensate": ("delete_x", {...}) | callable, # optional;
                                                         # callable(ctx, result)
       }

   ``setup`` runs before the tool call; ``assert_orm`` must verify the DB
   row / ORM side-effect (never the formatted reply); ``compensate`` returns
   the account to net-zero afterwards.

Entries are collected from the inline ROUNDTRIPS below plus every
``ai_tools/tests/live/writes_*.py`` module's ROUNDTRIPS list (per-packet
files, no merge contention).

Run: docker exec ws1-backend python -m ai_tools.tests.verify_writes
(a fresh process — so ORM assertions see exactly what the DB holds).
"""

import importlib
import os
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()

from accounts.models.user import User
from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from ai_tools.registry import registry

USER_EMAIL = "kartik.nvj@futureagi.com"

# (create_tool, create_args, delete_tool) — minimal valid create payloads.
ROUNDTRIPS = [
    (
        "create_prompt_folder",
        {"name": "bridge-writecheck-folder"},
        "delete_prompt_folder",
    ),
    (
        "create_persona",
        {
            "name": "bridge-writecheck-persona",
            "description": "throwaway write-check persona",
        },
        "delete_persona",
    ),
    (
        "create_eval_group",
        {"name": "bridge-writecheck-evalgroup", "eval_template_ids": []},
        "delete_eval_group",
    ),
    ("create_prompt_label", {"name": "bridge-writecheck-label"}, "delete_prompt_label"),
    (
        "create_knowledge_base",
        {"name": "bridge-writecheck-kb"},
        "delete_knowledge_base",
    ),
]


def _load_packet_roundtrips() -> list:
    """Collect ROUNDTRIPS lists from every ai_tools/tests/live/writes_*.py."""
    entries: list = []
    live_dir = Path(__file__).resolve().parent / "live"
    for path in sorted(live_dir.glob("writes_*.py")):
        try:
            mod = importlib.import_module(f"ai_tools.tests.live.{path.stem}")
        except Exception as e:
            print(f"[WARN] could not import {path.name}: {e}")
            continue
        entries.extend(getattr(mod, "ROUNDTRIPS", []))
    return entries


def _safe_run(tool_name: str, args: dict, ctx: ToolContext):
    """Run a registered tool, normalizing exceptions into an error result.

    Phase 3A: destructive tools answer the first call with a
    CONFIRMATION_REQUIRED preview (zero side effects). On the harness
    transport the client is the approver, so auto re-call once with
    confirm=True merged into identical args — exercising the exact
    two-phase path Falcon/MCP use.
    """
    t = registry.get(tool_name)
    if not t:
        return None
    try:
        result = t.run(args, ctx)
        if getattr(result, "error_code", None) == "CONFIRMATION_REQUIRED":
            print(f"  (confirmation preview received for {tool_name}; re-calling with confirm=true)")
            result = t.run({**args, "confirm": True}, ctx)
        return result
    except Exception as e:
        return type("R", (), {"is_error": True, "content": f"EXC {e}", "data": None})()


def _harvest_id(result) -> str | None:
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return None
    new_id = data.get("id") or (
        data.get("result", {}) if isinstance(data.get("result"), dict) else {}
    ).get("id")
    if not new_id:
        for v in data.values():
            if isinstance(v, str) and len(v) > 20 and "-" in v:
                new_id = v
                break
    return new_id


def _run_legacy_tuple(entry, ctx) -> None:
    create_name, args, delete_name = entry
    dt = registry.get(delete_name)
    cr = _safe_run(create_name, args, ctx)
    if cr is None:
        print(f"[SKIP] {create_name}: not registered")
        return
    if cr.is_error:
        print(f"[CREATE-ERR] {create_name}: {str(cr.content)[:110]}")
        return
    new_id = _harvest_id(cr)
    print(f"[CREATE-OK ] {create_name}: id={str(new_id)[:12]}")
    if new_id and dt:
        dr = _safe_run(delete_name, {"id": str(new_id)}, ctx)
        tag = "DELETE-OK " if not dr.is_error else "DELETE-ERR"
        print(f"[{tag}] {delete_name}: {str(dr.content)[:90]}")
    elif not dt:
        print(f"  (no delete tool {delete_name}; leaving row — clean up manually)")


def _run_step(step, ctx, result=None):
    """Run a setup/compensate step: (tool, args) tuple or a callable.

    Callables get (ctx) for setup and (ctx, result) for compensate.
    Returns (ok: bool, detail: str).
    """
    try:
        if callable(step):
            if result is None:
                step(ctx)
            else:
                step(ctx, result)
            return True, "callable ok"
        tool_name, args = step
        r = _safe_run(tool_name, args, ctx)
        if r is None:
            return False, f"tool {tool_name} not registered"
        if r.is_error:
            return False, f"{tool_name}: {str(r.content)[:90]}"
        return True, f"{tool_name} ok"
    except Exception as e:
        return False, f"EXC {e}"


def _run_dict_entry(entry: dict, ctx) -> None:
    name = entry["tool"]
    if not registry.get(name):
        print(f"[SKIP] {name}: not registered")
        return

    setup = entry.get("setup")
    if setup is not None:
        ok, detail = _run_step(setup, ctx)
        if not ok:
            print(f"[SETUP-ERR ] {name}: {detail}")
            return

    result = _safe_run(name, entry["args"], ctx)
    if result.is_error:
        print(f"[CALL-ERR  ] {name}: {str(result.content)[:110]}")
    else:
        print(f"[CALL-OK   ] {name}")

    assert_orm = entry.get("assert_orm")
    if assert_orm is not None and not result.is_error:
        try:
            passed = bool(assert_orm(ctx, result))
        except Exception as e:
            passed = False
            print(f"[ASSERT-EXC] {name}: {e}")
        print(f"[{'ASSERT-OK ' if passed else 'ASSERT-FAIL'}] {name}")

    compensate = entry.get("compensate")
    if compensate is not None:
        ok, detail = _run_step(compensate, ctx, result=result)
        tag = "COMP-OK   " if ok else "COMP-ERR  "
        print(f"[{tag}] {name}: {detail}")


def main():
    u = User.objects.select_related("organization").get(email=USER_EMAIL)
    ws = (
        Workspace.objects.filter(
            organization=u.organization, is_default=True, is_active=True
        ).first()
        or Workspace.objects.filter(organization=u.organization).first()
    )
    # transport="harness": the script operator is the human approver, so the
    # confirmation gate honors confirm=True against an existing preview
    # record (preview-first still enforced; see ai_tools/confirmations.py).
    ctx = ToolContext(
        user=u, organization=u.organization, workspace=ws, transport="harness"
    )

    entries = list(ROUNDTRIPS) + _load_packet_roundtrips()

    print("WRITE ROUND-TRIP SPOT CHECKS")
    print("=" * 70)
    for entry in entries:
        if isinstance(entry, dict):
            _run_dict_entry(entry, ctx)
        else:
            _run_legacy_tuple(entry, ctx)


if __name__ == "__main__":
    main()
