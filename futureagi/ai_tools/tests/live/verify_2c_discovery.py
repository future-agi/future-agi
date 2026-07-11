# ruff: noqa: E402, T201
"""Phase 2C live-verify: the discovery flow on the real registry.

Fresh-shell harness (PHASES.md 2C acceptance, the conv da9d6bb1 pattern):
  1. overview question        -> full capability map (categories + counts)
  2. 'stop a running experiment'   -> stop_experiment found (top results)
  3. 'review annotation items'     -> review tools found (top results)

Run:
    docker exec ws1-backend python -m ai_tools.tests.live.verify_2c_discovery
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()

from ai_tools.base import ToolContext
from ai_tools.tools.context.search_tools import SearchToolsInput, SearchToolsTool

ctx = ToolContext(user=None, organization=None, workspace=None, transport="harness")
search = SearchToolsTool()
failures = []

# 1. Overview question → capability map
res = search.execute(
    SearchToolsInput(query="what tools do I have, show all capabilities"), ctx
)
data = res.data or {}
total = data.get("total_tools")
cats = data.get("categories")
if data.get("tools"):
    # keyword hits + overview words → map is appended to content instead
    has_map = "full toolset" in (res.content or "")
else:
    has_map = bool(total and cats)
print(f"[1] overview → capability map: has_map={has_map} total={total} cats={cats}")
if not (has_map or (total and cats)):
    failures.append("capability map missing for overview question")

# 2. stop a running experiment
res = search.execute(SearchToolsInput(query="stop a running experiment"), ctx)
names = [t["name"] for t in (res.data or {}).get("tools", [])]
print(f"[2] 'stop a running experiment' → {names[:5]}")
if "stop_experiment" not in names[:5]:
    failures.append(f"stop_experiment not in top-5: {names[:5]}")

# 3. review annotation items
res = search.execute(SearchToolsInput(query="review annotation items"), ctx)
names = [t["name"] for t in (res.data or {}).get("tools", [])]
print(f"[3] 'review annotation items' → {names[:5]}")
if not {"review_queue_item", "bulk_review_queue_items"} & set(names[:5]):
    failures.append(f"review tools not in top-5: {names[:5]}")

if failures:
    print("\nFAIL:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nPASS: 2C discovery flow live-verified")
