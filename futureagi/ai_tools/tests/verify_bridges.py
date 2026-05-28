# ruff: noqa: E402
"""Verify which bridge tools actually return data against the live DB.

Read-only: only exercises list_* / get_* style tools (no create/update/delete).
For detail (get_*) tools, first calls the sibling list_* to obtain a real id.

Run: docker exec ws1-backend python -m ai_tools.tests.verify_bridges
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()

from accounts.models.user import User
from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from ai_tools.registry import registry

USER_EMAIL = "kartik.nvj@futureagi.com"


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
            if getattr(r, "data", None) and isinstance(r.data, dict):
                for v in r.data.values():
                    if (
                        isinstance(v, list)
                        and v
                        and isinstance(v[0], dict)
                        and "id" in v[0]
                    ):
                        ids_by_viewset[t.binding.viewset_class] = v[0]["id"]
                        break
        snippet = str(getattr(r, "content", ""))[:80].replace("\n", " ")
        print(f"  [{status}] {t.name:<40} {snippet}")

    print("\n--- DETAIL (GET) TOOLS (id paired from same ViewSet's list) ---")
    for t in sorted(detail_tools, key=lambda x: x.name):
        rid = ids_by_viewset.get(t.binding.viewset_class)
        if not rid:
            empty.append(t.name)
            print(
                f"  [NODATA] {t.name:<40} (no rows in this workspace to test against)"
            )
            continue
        pk_field = t.binding.pk_field or "id"
        is_err, r = _run(t, {pk_field: str(rid)})
        status = "ERR" if is_err else "OK "
        (err if is_err else ok).append(t.name)
        snippet = str(getattr(r, "content", ""))[:80].replace("\n", " ")
        print(f"  [{status}] {t.name:<40} (id={str(rid)[:8]}) {snippet}")

    print("\n" + "=" * 70)
    print(
        f"WORKING: {len(ok)}   FAILING: {len(err)}   NODATA(untestable): {len(empty)}"
    )
    print(f"\nREAL FAILURES ({len(err)}):")
    for n in err:
        print(f"  {n}")


if __name__ == "__main__":
    main()
