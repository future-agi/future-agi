# ruff: noqa: E402
"""TH-5254 live demonstration: scored evals created via Falcon are no longer
mislabeled pass/fail.

Creates eval templates through the same create_eval_template tool Falcon uses,
against the live ws1 DB, and prints the persisted output_type_normalized +
config output + choices for each — proving the UI will now render the score
type correctly.

Run in ws1-backend: python -m ai_tools.tests.demo_th5254
"""

import os
import uuid

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()

from accounts.models.user import User
from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from ai_tools.registry import registry
from model_hub.models.evals_metric import EvalTemplate

USER_EMAIL = "kartik.nvj@futureagi.com"


def main():
    u = User.objects.select_related("organization").get(email=USER_EMAIL)
    ws = (
        Workspace.objects.filter(
            organization=u.organization, is_default=True, is_active=True
        ).first()
        or Workspace.objects.filter(organization=u.organization).first()
    )
    ctx = ToolContext(user=u, organization=u.organization, workspace=ws)
    tool = registry.get("create_eval_template")

    suffix = uuid.uuid4().hex[:6]
    cases = [
        (
            "SCORED (0-1 scale, no explicit type)",
            {
                "name": f"th5254-relevance-{suffix}",
                "criteria": "Rate how relevant {{output}} is to {{input}} on a scale of 0 to 1.",
                "required_keys": ["input", "output"],
            },
        ),
        (
            "SCORED (0-100 rating, no explicit type)",
            {
                "name": f"th5254-helpful-{suffix}",
                "criteria": "Give a score from 0-100 for how helpful {{output}} is.",
                "required_keys": ["output"],
            },
        ),
        (
            "BINARY (genuine yes/no)",
            {
                "name": f"th5254-validjson-{suffix}",
                "criteria": "Determine whether {{output}} is valid JSON. Answer Pass or Fail.",
                "required_keys": ["output"],
            },
        ),
        (
            "SCORED language but EXPLICIT pass_fail (must be respected)",
            {
                "name": f"th5254-explicit-{suffix}",
                "criteria": "Rate on a scale of 0 to 1 whether {{output}} is toxic.",
                "required_keys": ["output"],
                "output_type": "pass_fail",
            },
        ),
    ]

    print("=" * 78)
    print("TH-5254 LIVE DEMONSTRATION — eval creation from Falcon (output type)")
    print(f"User: {u.email}   Workspace: {ws.name if ws else '-'}")
    print("=" * 78)
    created_ids = []
    for label, args in cases:
        r = tool.run(args, ctx)
        if r.is_error:
            print(f"\n[{label}]\n  ERROR: {r.content[:160]}")
            continue
        tid = r.data["id"]
        created_ids.append(tid)
        tmpl = EvalTemplate.objects.get(id=tid)
        print(f"\n[{label}]")
        print(f"  name                    : {tmpl.name}")
        print(
            f"  output_type_normalized  : {tmpl.output_type_normalized}   <-- UI renders this"
        )
        print(f"  config['output']        : {tmpl.config.get('output')}")
        print(f"  choices                 : {tmpl.choices}")

    # Clean up the demo rows (net-zero on the account)
    EvalTemplate.objects.filter(id__in=created_ids).delete()
    print("\n" + "=" * 78)
    print(
        "EXPECTED: scored cases -> output_type_normalized='percentage' (output='score'),"
    )
    print("binary -> 'pass_fail', explicit pass_fail -> respected as 'pass_fail'.")
    print(f"(cleaned up {len(created_ids)} demo templates)")


if __name__ == "__main__":
    main()
