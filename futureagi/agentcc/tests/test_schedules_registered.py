"""The gateway's alert, webhook-delivery and batch schedules are registered.

``register_temporal_schedules`` (and the embedded worker) register exactly
``tfc.temporal.schedules.ALL_SCHEDULES``. The worker learns each activity by
importing ``tfc.temporal.schedules`` (the package ``__init__`` imports every
schedule module), so a schedule module that ``__init__`` does not import has
neither its schedule nor its activity registered.
"""

import json
import os
import subprocess
import sys

GATEWAY_SCHEDULES = {
    "agentcc-alerts-evaluator": "evaluate_agentcc_email_alerts_activity",
    "agentcc-webhook-delivery": "deliver_agentcc_webhook_events_activity",
    "agentcc-batches": "poll_agentcc_batches_activity",
}


def test_gateway_schedules_are_in_all_schedules():
    from tfc.temporal.schedules import ALL_SCHEDULES

    ids = [schedule.schedule_id for schedule in ALL_SCHEDULES]
    assert len(ids) == len(set(ids)), "a schedule id is registered twice"
    missing = sorted(set(GATEWAY_SCHEDULES) - set(ids))
    assert missing == [], f"not in ALL_SCHEDULES: {missing}"
    batches = next(s for s in ALL_SCHEDULES if s.schedule_id == "agentcc-batches")
    assert batches.interval_seconds == 60
    assert batches.activity_name == "poll_agentcc_batches_activity"


def test_importing_the_schedules_package_registers_every_activity():
    """In a fresh interpreter, as the worker starts: import only the package,
    then every gateway schedule's activity must be in the drop-in registry."""
    code = (
        "import json, django; django.setup();"
        "import tfc.temporal.schedules as s;"
        "from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY as r;"
        "print(json.dumps({x.schedule_id: (x.activity_name, x.activity_name in r)"
        " for x in s.ALL_SCHEDULES if x.schedule_id.startswith('agentcc-')}))"
    )
    env = dict(os.environ)
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        timeout=300,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    found = json.loads(out.stdout.strip().splitlines()[-1])
    for schedule_id in GATEWAY_SCHEDULES:
        assert schedule_id in found, f"{schedule_id} not registered: {found}"
        activity, registered = found[schedule_id]
        assert registered, f"{schedule_id}: activity {activity} not registered"
