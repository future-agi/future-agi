# ruff: noqa: E402
"""Verify which bridge tools actually return data against the live DB.

Read-only: only exercises list_* / get_* style tools (no create/update/delete).
For detail (get_*) tools, ids are harvested in priority order:

  1. ``binding.id_source`` (A7) — the named tool is run and its first row id
     is used (covers custom/APIView actions whose ViewSet has no sibling
     list tool).
  2. Sibling-list pairing by identical ``binding.viewset_class`` (legacy).
  3. ``SEED_IDS`` merged from ``ai_tools/tests/live/seed_ids_*.py`` modules
     (per-packet files, no merge contention). Values are either a bare id
     string (passed as the tool's pk_field) or a full params dict (for
     tools that also need path_kwargs).

Run: docker exec ws1-backend python -m ai_tools.tests.verify_bridges
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


def _load_seed_ids() -> dict:
    """Merge SEED_IDS dicts from every ai_tools/tests/live/seed_ids_*.py."""
    seeds: dict = {}
    live_dir = Path(__file__).resolve().parent / "live"
    for path in sorted(live_dir.glob("seed_ids_*.py")):
        try:
            mod = importlib.import_module(f"ai_tools.tests.live.{path.stem}")
        except Exception as e:
            print(f"[WARN] could not import {path.name}: {e}")
            continue
        seeds.update(getattr(mod, "SEED_IDS", {}))
    return seeds


def _harvest_first_id(result) -> str | None:
    """Pull the first row id out of a list-style ToolResult."""
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return None
    candidates = [data]
    for v in data.values():
        if isinstance(v, dict):
            candidates.append(v)
    for container in candidates:
        for v in container.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "id" in v[0]:
                return v[0]["id"]
    return None


def main():
    user = User.objects.select_related("organization").get(email=USER_EMAIL)
    ws = (
        Workspace.objects.filter(
            organization=user.organization, is_default=True, is_active=True
        ).first()
        or Workspace.objects.filter(organization=user.organization).first()
    )
    ctx = ToolContext(user=user, organization=user.organization, workspace=ws)

    bridges = [t for t in registry.list_all() if hasattr(t, "binding")]
    list_tools = [
        t
        for t in bridges
        if t.binding.action == "list"
        or (t.binding.method == "GET" and not t.binding.detail)
    ]
    detail_tools = [
        t for t in bridges if t.binding.detail and t.binding.method == "GET"
    ]

    print(f"Total bridge tools: {len(bridges)}")
    print(f"List-style: {len(list_tools)}  Detail-GET: {len(detail_tools)}")
    print("=" * 70)

    ok, err, empty = [], [], []
    # map viewset_class -> real id harvested from THAT viewset's list tool
    ids_by_viewset = {}
    # map tool name -> first row id that tool returned (for id_source lookup)
    ids_by_tool = {}
    seed_ids = _load_seed_ids()

    def _run(t, params):
        try:
            r = t.run(params, ctx)
            return r.is_error, r
        except Exception as e:
            return True, type("R", (), {"content": f"EXC: {e}", "data": None})()

    print("\n--- LIST TOOLS ---")
    for t in sorted(list_tools, key=lambda x: x.name):
        is_err, r = _run(t, {})
        if is_err:
            err.append(t.name)
            status = "ERR"
        else:
            ok.append(t.name)
            status = "OK "
            rid = _harvest_first_id(r)
            if rid:
                ids_by_viewset.setdefault(t.binding.viewset_class, rid)
                ids_by_tool[t.name] = rid
        snippet = str(getattr(r, "content", ""))[:80].replace("\n", " ")
        print(f"  [{status}] {t.name:<40} {snippet}")

    def _resolve_detail_id(t):
        """id_source (A7) -> sibling-list pairing -> SEED_IDS."""
        id_src = getattr(t.binding, "id_source", None)
        if id_src:
            if id_src in ids_by_tool:
                return ids_by_tool[id_src], f"id_source:{id_src}"
            src_tool = registry.get(id_src)
            if src_tool is not None:
                is_err, r = _run(src_tool, {})
                if not is_err:
                    rid = _harvest_first_id(r)
                    if rid:
                        ids_by_tool[id_src] = rid
                        return rid, f"id_source:{id_src}"
        rid = ids_by_viewset.get(t.binding.viewset_class)
        if rid:
            return rid, "sibling-list"
        if t.name in seed_ids:
            return seed_ids[t.name], "seed"
        return None, None

    print("\n--- DETAIL (GET) TOOLS (id via id_source / sibling list / seeds) ---")
    for t in sorted(detail_tools, key=lambda x: x.name):
        rid, source = _resolve_detail_id(t)
        if not rid:
            empty.append(t.name)
            print(
                f"  [NODATA] {t.name:<40} (no id_source/sibling rows/seed to test with)"
            )
            continue
        if isinstance(rid, dict):
            params = dict(rid)  # full params seed (pk + path_kwargs etc.)
            shown = next(iter(params.values()), "")
        else:
            pk_field = t.binding.pk_field or "id"
            params = {pk_field: str(rid)}
            shown = rid
        is_err, r = _run(t, params)
        status = "ERR" if is_err else "OK "
        (err if is_err else ok).append(t.name)
        snippet = str(getattr(r, "content", ""))[:80].replace("\n", " ")
        print(f"  [{status}] {t.name:<40} (id={str(shown)[:8]} via {source}) {snippet}")

    print("\n" + "=" * 70)
    print(
        f"WORKING: {len(ok)}   FAILING: {len(err)}   NODATA(untestable): {len(empty)}"
    )
    print(f"\nREAL FAILURES ({len(err)}):")
    for n in err:
        print(f"  {n}")


if __name__ == "__main__":
    main()
