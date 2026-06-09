# ruff: noqa: E402
"""Spot-check write bridge tools via create -> delete round-trips.

Creates a throwaway row through the create bridge, then deletes it through the
delete bridge. Net-zero on the account. Proves create/update/delete bridges
actually execute (verify_bridges.py only covers reads).

Run: docker exec ws1-backend python -m ai_tools.tests.verify_writes
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


def main():
    u = User.objects.select_related("organization").get(email=USER_EMAIL)
    ws = (
        Workspace.objects.filter(
            organization=u.organization, is_default=True, is_active=True
        ).first()
        or Workspace.objects.filter(organization=u.organization).first()
    )
    ctx = ToolContext(user=u, organization=u.organization, workspace=ws)

    print("WRITE ROUND-TRIP SPOT CHECKS")
    print("=" * 70)
    for create_name, args, delete_name in ROUNDTRIPS:
        ct = registry.get(create_name)
        dt = registry.get(delete_name)
        if not ct:
            print(f"[SKIP] {create_name}: not registered")
            continue
        try:
            cr = ct.run(args, ctx)
        except Exception as e:
            cr = type(
                "R", (), {"is_error": True, "content": f"EXC {e}", "data": None}
            )()
        if cr.is_error:
            print(f"[CREATE-ERR] {create_name}: {str(cr.content)[:110]}")
            continue
        # harvest created id
        new_id = None
        data = cr.data
        if isinstance(data, dict):
            new_id = data.get("id") or (
                data.get("result", {}) if isinstance(data.get("result"), dict) else {}
            ).get("id")
            if not new_id:
                for v in data.values():
                    if isinstance(v, str) and len(v) > 20 and "-" in v:
                        new_id = v
                        break
        print(f"[CREATE-OK ] {create_name}: id={str(new_id)[:12]}")
        if new_id and dt:
            try:
                dr = dt.run({"id": str(new_id)}, ctx)
                tag = "DELETE-OK " if not dr.is_error else "DELETE-ERR"
                print(f"[{tag}] {delete_name}: {str(dr.content)[:90]}")
            except Exception as e:
                print(f"[DELETE-ERR] {delete_name}: EXC {e}")
        elif not dt:
            print(f"  (no delete tool {delete_name}; leaving row — clean up manually)")


if __name__ == "__main__":
    main()
