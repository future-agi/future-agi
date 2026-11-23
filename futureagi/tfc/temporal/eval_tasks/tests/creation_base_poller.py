"""Qualification-only poller. Run with python -I; import product code from BASE only."""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

BASE = "ae0bf483a052580a2e57da454d51b5a43b90fbc2"
BASE_TREE = "cc4f5c151073af40a9a867a72a524cfa28efe3ac"


def verify_source(checkout, tree):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", checkout, *args], text=True
        ).strip()

    assert re.fullmatch("[0-9a-f]{40}", tree)
    assert Path(git("rev-parse", "--show-toplevel")).resolve() == checkout
    assert (
        git("write-tree") == tree
    )  # Allows a reviewed, applied --index candidate patch.
    git("diff", "--exit-code", "--quiet")  # Unstaged overrides invalidate the pin.
    assert not git("ls-files", "--others", "--exclude-standard")
    if tree == BASE_TREE:
        assert git("rev-parse", "HEAD") == BASE


async def main(checkout, role, target, namespace, queue, ready):
    verify_source(checkout, BASE_TREE)
    app = checkout / "futureagi"
    sys.path.insert(0, str(app))
    assert os.environ["DJANGO_SETTINGS_MODULE"] == "tfc.settings.test"
    assert os.environ["PG_DB"] == os.environ["PG_TEST_DB"]
    import django

    django.setup()
    from temporalio.client import Client
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    from tfc.temporal.eval_tasks import activities, get_activities, workflows

    for module in (activities, workflows):
        assert Path(module.__file__).resolve() == app.joinpath(
            *module.__name__.split(".")
        ).with_suffix(".py")
    assert role in ("workflow", "activities")
    identity = f"base-{BASE}"
    client = await Client.connect(target, namespace=namespace, identity=identity)
    async with Worker(
        client,
        task_queue=queue,
        identity=identity,
        workflows=[workflows.HistoricalEvalTaskWorkflow] if role == "workflow" else [],
        activities=get_activities() if role == "activities" else [],
        workflow_runner=UnsandboxedWorkflowRunner(),
        max_cached_workflows=0,
    ):
        Path(ready).touch(exist_ok=False)
        await asyncio.to_thread(sys.stdin.buffer.read)  # Parent owns the write end.


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]).resolve(), *sys.argv[2:]))
