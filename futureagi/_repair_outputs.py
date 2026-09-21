"""Rebuild one job's stage outputs from its sealed authoring archive.

The live poll had overwritten them with a partial mid-rewrite snapshot, so the coverage grid
rendered half the suite. The archive is intact; this republishes from it.
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()

from simulate.models.hosted_harness import HostedHarnessJob
from simulate.services.hosted_harness_gateway import (
    UPLOAD_BUCKET_NAME,
    authoring_stage_outputs_from_archive,
    get_storage_client,
)

prefix = os.environ["JOB"]
job = next(
    one
    for one in HostedHarnessJob.no_workspace_objects.all().order_by("-created_at")[:200]
    if str(one.id).startswith(prefix)
)
key = ((job.payload or {}).get("metadata") or {}).get("authoring_object_key")
print("job", job.id, "archive", key)

response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
try:
    body = response.read()
finally:
    response.close()
    response.release_conn()

rebuilt = authoring_stage_outputs_from_archive(body, scenario_limit=job.scenario_count)
produced = {one.get("kind") for one in rebuilt}
kept = [
    one
    for one in (job.stage_outputs or [])
    if isinstance(one, dict) and one.get("kind") not in produced
]
order = {"contract": 0, "environment": 1, "scenarios": 2, "coverage": 3}
job.stage_outputs = sorted(rebuilt + kept, key=lambda one: order.get(one.get("kind"), len(order)))
job.save(update_fields=["stage_outputs", "updated_at"])

for one in job.stage_outputs:
    rows = one.get("data")
    size = len(rows) if isinstance(rows, list) else "-"
    print(f"  {one.get('kind'):12} rows={size} :: {one.get('summary', '')[:60]}")
