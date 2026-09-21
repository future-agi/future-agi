"""Kill only the sandbox belonging to one job, after proving it is that job's.

Runs inside the worker container, which is the only place with both the E2B SDK and the
credentials. Never lists-and-kills: the account is shared, and a blanket kill takes somebody
else's run down with ours.

    docker exec -e JOB=<prefix> futureagi-worker-simulation-runner-1 python kill_job_sbx.py
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()

from simulate.models.hosted_harness import HostedHarnessAttempt, HostedHarnessJob

prefix = os.environ["JOB"]
job = HostedHarnessJob.no_workspace_objects.filter(id__isnull=False).none()
for candidate in HostedHarnessJob.no_workspace_objects.all().order_by("-created_at")[:200]:
    if str(candidate.id).startswith(prefix):
        job = candidate
        break
else:
    raise SystemExit(f"no job starting {prefix}")

refs = [
    str(one.provider_ref)
    for one in HostedHarnessAttempt.no_workspace_objects.filter(job=job)
    if one.provider_ref
]
print("job", job.id, "state", job.state, "stage", job.current_stage)
print("sandboxes owned by this job:", refs)

if os.environ.get("DRY"):
    raise SystemExit("dry run: nothing killed")

from e2b import Sandbox

key = os.environ.get("E2B_API_KEY") or ""
for ref in refs:
    try:
        Sandbox.kill(ref, api_key=key)
        print("KILLED", ref)
    except Exception as exc:  # noqa: BLE001 - already gone is the ordinary case
        print("not killed", ref, type(exc).__name__, str(exc)[:120])
