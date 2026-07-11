# ruff: noqa: E402
"""TH-5467 live verification harness.

Discovers the real tool names for the simulation/agent/run-test area and
live-verifies the 'already closed by bridge' cluster against the test account.

Run in ws1-backend:
    python -m ai_tools.tests.verify_th5467
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()

from accounts.models.user import User
from accounts.models.workspace import Workspace
from ai_tools.registry import registry

USER_EMAIL = "kartik.nvj@futureagi.com"


def discover():
    print("=" * 78)
    print("DISCOVERY — tools by keyword")
    print("=" * 78)
    keywords = [
        "run_test",
        "test_execution",
        "agent",
        "version",
        "scenario",
        "persona",
        "optimization",
        "eval_task",
        "trace_score",
        "queue",
        "annotation",
        "alert",
        "sampling",
        "export",
        "knowledge",
        "saved_view",
        "call",
    ]
    allnames = sorted(t.name for t in registry.list_all())
    for kw in keywords:
        hits = [n for n in allnames if kw in n]
        print(f"\n[{kw}] ({len(hits)})")
        for n in hits:
            print(f"   {n}")


def main():
    discover()


if __name__ == "__main__":
    main()
