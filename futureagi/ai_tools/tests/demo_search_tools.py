# ruff: noqa: E402
"""Demonstrate search_tools: smart tool discovery over the full registry.

Runs several natural-language intents (mirroring TH-5467 tickets where Falcon
couldn't find the right tool) and prints the top matches search_tools returns.

Run in ws1-backend: python -m ai_tools.tests.demo_search_tools
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

QUERIES = [
    "create a knowledge base",  # TH-5383
    "list the items in an annotation queue",  # TH-5396
    "update the project sampling rate",  # TH-5416
    "add an API call column to a dataset",  # TH-5337
    "save a filtered view on a tracing project",  # TH-5414
    "create an evaluation that scores 0 to 1",  # TH-5254
    "what alert monitor metric types can I use",  # TH-5406
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
    tool = registry.get("search_tools")

    print("=" * 78)
    print(f"search_tools DEMO — registry has {registry.count()} tools")
    print("=" * 78)
    for q in QUERIES:
        r = tool.run({"query": q, "limit": 4}, ctx)
        names = [t["name"] for t in (r.data or {}).get("tools", [])]
        print(f"\nQ: {q}")
        print(f"   -> {names}")


if __name__ == "__main__":
    main()
