"""Export an installed SDK's reviewed local plan for the campaign executor.

Called with Python -I so repository code cannot shadow SDK imports. Only the
review document and changed file bytes are returned, never resolution leases
or credentials. Export itself neither uploads local files nor authorizes Git.
"""

import json
import sys
from pathlib import Path

from fi.context.campaigns import target_plan
from fi.context.journal import parse_plan

plan_path, repository_id, base_commit, binding_revision, campaign_revision = sys.argv[1:]
plan = parse_plan(Path(plan_path).read_bytes())
wire = target_plan(
    plan,
    repository_id=repository_id,
    base_commit=base_commit,
    binding_revision=int(binding_revision),
    campaign_revision=int(campaign_revision),
)
changed = {row["path"] for row in wire["changes"]}
contents = {
    change.path: change.after
    for change in plan.changes
    if change.path in changed and change.after is not None
}
print(json.dumps({"plan": wire, "contents": contents}))
