# ruff: noqa: E402
"""Phase 3A fresh-shell verification of the confirmation gate.

Run: docker exec ws1-backend bash -lc "cd /app/backend && PYTHONPATH=/app/backend python ai_tools/tests/verify_confirmations.py"

Checks:

1. A delete_* tool WITHOUT confirm returns CONFIRMATION_REQUIRED and has
   ZERO side effect (ORM row untouched).
2. A cold confirm=true (jailbreak probe) ALSO returns the preview.
3. confirmations.set_status(token, "approved") — simulating the Confirm
   button — then an identical re-call with confirm=true EXECUTES.
4. The executed result carries data["confirmed"]=True; record is consumed.
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()

from accounts.models.user import User
from accounts.models.workspace import Workspace
from ai_tools import confirmations
from ai_tools.base import ToolContext
from ai_tools.registry import registry
from model_hub.models.run_prompt import PromptFolder

USER_EMAIL = "kartik.nvj@futureagi.com"

u = User.objects.select_related("organization").get(email=USER_EMAIL)
ws = (
    Workspace.objects.filter(
        organization=u.organization, is_default=True, is_active=True
    ).first()
    or Workspace.objects.filter(organization=u.organization).first()
)
# Falcon transport (strict): only an approved record unlocks phase-2.
ctx = ToolContext(user=u, organization=u.organization, workspace=ws)

passed = []


def check(name, ok, detail=""):
    passed.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


# Seed throwaway folder
PromptFolder.all_objects.filter(name="3a-gate-check-folder").delete()
folder = PromptFolder.objects.create(
    name="3a-gate-check-folder", organization=u.organization, workspace=ws
)
fid = str(folder.id)

tool = registry.get("delete_prompt_folder")
check("delete_prompt_folder registered + destructive",
      tool is not None and tool.execution_policy == "destructive")
check("schema advertises confirm",
      "confirm" in tool.input_schema.get("properties", {}))

# 1. Phase-1: no confirm -> preview, zero side effect
r1 = tool.run({"id": fid}, ctx)
check("phase-1 returns CONFIRMATION_REQUIRED",
      r1.error_code == "CONFIRMATION_REQUIRED" and not r1.is_error)
check("phase-1 zero side effect (folder intact)",
      PromptFolder.objects.filter(id=fid).exists())
token = (r1.data or {}).get("confirmation", {}).get("token")
check("phase-1 carries token + preview", bool(token) and
      bool((r1.data or {}).get("confirmation", {}).get("preview")))

# 2. Jailbreak probe: cold confirm=true while record still pending
r2 = tool.run({"id": fid, "confirm": True}, ctx)
check("jailbreak probe blocked (pending -> CONFIRMATION_PENDING)",
      r2.error_code in ("CONFIRMATION_PENDING", "CONFIRMATION_REQUIRED"),
      f"({r2.error_code})")
check("jailbreak probe zero side effect",
      PromptFolder.objects.filter(id=fid).exists())

# 3. Simulate the Confirm button, then phase-2
confirmations.set_status(token, "approved")
r3 = tool.run({"id": fid, "confirm": True}, ctx)
check("approved phase-2 executes", not r3.is_error and r3.error_code is None,
      f"({r3.error_code} {str(r3.content)[:60]})")
check("folder actually deleted", not PromptFolder.objects.filter(id=fid).exists())
check("executed leg carries confirmed=true",
      isinstance(r3.data, dict) and r3.data.get("confirmed") is True)
rec = confirmations.get(token)
check("record consumed (single-use)", rec is not None and rec.get("status") == "consumed")

# 4. Replay: confirm=true again must NOT re-execute (fresh preview)
r4 = tool.run({"id": fid, "confirm": True}, ctx)
check("replay returns fresh preview, no re-execution",
      r4.error_code == "CONFIRMATION_REQUIRED")

# cleanup
PromptFolder.all_objects.filter(id=fid).delete()

print("=" * 60)
print(f"{'ALL PASS' if all(passed) else 'FAILURES PRESENT'} ({sum(passed)}/{len(passed)})")
raise SystemExit(0 if all(passed) else 1)
