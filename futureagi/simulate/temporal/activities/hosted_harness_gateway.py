from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
import tempfile
from pathlib import Path

from django.utils import timezone
from temporalio import activity

from simulate.temporal.activities.hosted_runner import _run_db
from simulate.temporal.types.hosted_harness_gateway import (
    HostedHarnessAttemptInput,
    HostedHarnessAuthoringOutput,
    HostedHarnessGatewayInput,
    HostedHarnessLaunchOutput,
    HostedHarnessPollOutput,
)


def _extract_source_archive(body: bytes, destination: Path) -> Path:
    destination = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise RuntimeError("source archive contains a link")
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError("source archive contains an unsafe path")
        archive.extractall(destination)
    source = destination / "source"
    if not source.is_dir():
        raise RuntimeError("source archive has no source root")
    return source


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _authoring_stage_outputs(root: Path) -> list[dict]:
    """Return bounded, secret-safe ALK authoring snapshots for the live UI."""
    outputs: list[dict] = []
    contract = _read_json(root / "contract.json")
    if isinstance(contract, dict):
        tools = [
            {"name": str(tool.get("name") or "")}
            for tool in contract.get("tools", [])
            if isinstance(tool, dict) and tool.get("name")
        ]
        outputs.append(
            {
                "id": "contract",
                "kind": "contract",
                "title": "Agent contract",
                "summary": f"{len(tools)} tools · {contract.get('modality') or 'unknown'} modality",
                "data": {
                    "one_liner": str(contract.get("one_liner") or ""),
                    "modality": str(contract.get("modality") or "unknown"),
                    "runtime": contract.get("runtime") or {},
                    "tools": tools,
                    "hard_constraints": [
                        str(item) for item in contract.get("hard_constraints", [])
                    ],
                },
            }
        )
    environment = _read_json(root / "environment.json")
    if isinstance(environment, dict):
        services = [str(item) for item in environment.get("services", [])]
        outputs.append(
            {
                "id": "environment",
                "kind": "environment",
                "title": "Execution environment",
                "summary": f"{len(services)} services ready",
                "data": {
                    "services": services,
                    "project": str(environment.get("project") or ""),
                    "managed": bool(environment.get("managed")),
                },
            }
        )
    scenarios = _read_json(root / "scenarios.json")
    if isinstance(scenarios, list):
        data = [
            {
                "name": str(item.get("name") or "scenario"),
                "instruction": str(item.get("instruction") or ""),
                "use_case": str(item.get("use_case") or ""),
            }
            for item in scenarios
            if isinstance(item, dict)
        ]
        outputs.append(
            {
                "id": "scenarios",
                "kind": "scenarios",
                "title": "Generated scenarios",
                "summary": f"{len(data)} grounded scenarios",
                "data": data,
            }
        )
    return outputs


@activity.defn(name="author_hosted_harness_job")
async def author_hosted_harness_job(
    input: HostedHarnessGatewayInput,
) -> HostedHarnessAuthoringOutput:
    """Author the frozen bundle inside a Daytona sandbox before the execution launch.

    Contract/environment/scenario generation is model-heavy, so it runs in a throwaway sandbox
    (authoring credentials only) rather than on the control-plane worker. A job that already
    carries ``authoring_object_key`` is treated as authored and skipped.
    """
    from simulate.models import HostedHarnessJob
    from simulate.services.hosted_harness_gateway import (
        DaytonaHostedGateway,
        store_authoring_archive,
    )

    def _prepare() -> bool:
        job = HostedHarnessJob.no_workspace_objects.select_related("organization").get(
            id=input.job_id
        )
        metadata = (job.payload or {}).get("metadata") or {}
        if metadata.get("authoring_object_key"):
            return True
        job.current_stage = "understanding_agent"
        job.state = HostedHarnessJob.State.ADMITTED
        job.save(update_fields=["current_stage", "state", "updated_at"])
        return False

    def _author_and_store() -> None:
        job = HostedHarnessJob.no_workspace_objects.select_related("organization").get(
            id=input.job_id
        )
        body = DaytonaHostedGateway().author(job)
        store_authoring_archive(job, body)

    def _failed(detail: str) -> None:
        job = HostedHarnessJob.no_workspace_objects.get(id=input.job_id)
        job.state = HostedHarnessJob.State.FAILED
        job.current_stage = "failed"
        job.failure = {
            "domain": "simulator",
            "stage": "authoring",
            "code": "authoring_failed",
            "message": detail[:1000],
        }
        job.terminal_at = timezone.now()
        job.save(
            update_fields=[
                "state",
                "current_stage",
                "failure",
                "terminal_at",
                "updated_at",
            ]
        )

    try:
        cached = await _run_db(_prepare)
        if cached:
            return HostedHarnessAuthoringOutput(ready=True, state="admitted")
        # The sandbox authoring run blocks for minutes with no streamed output; keep the
        # Temporal activity alive with periodic heartbeats until it settles.
        task = asyncio.ensure_future(_run_db(_author_and_store))
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=20)
            except asyncio.TimeoutError:
                activity.heartbeat("ALK authoring in sandbox is active")
        await task
        return HostedHarnessAuthoringOutput(ready=True, state="admitted")
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        await _run_db(_failed, detail)
        return HostedHarnessAuthoringOutput(
            ready=False, state="failed", detail=detail[:1000]
        )


@activity.defn(name="launch_hosted_harness_job")
async def launch_hosted_harness_job(
    input: HostedHarnessGatewayInput,
) -> HostedHarnessLaunchOutput:
    from simulate.models import HostedHarnessJob
    from simulate.services.hosted_harness_gateway import DaytonaHostedGateway

    def _launch() -> str:
        job = HostedHarnessJob.no_workspace_objects.select_related("organization").get(
            id=input.job_id
        )
        attempt = DaytonaHostedGateway().launch(
            job, endpoint_base_url=input.endpoint_base_url
        )
        return str(attempt.id)

    attempt_id = await _run_db(_launch)
    return HostedHarnessLaunchOutput(attempt_id=attempt_id)


@activity.defn(name="poll_hosted_harness_attempt")
async def poll_hosted_harness_attempt(
    input: HostedHarnessAttemptInput,
) -> HostedHarnessPollOutput:
    from simulate.models import HostedHarnessAttempt, HostedHarnessJob
    from simulate.services.hosted_harness_gateway import DaytonaHostedGateway

    def _poll() -> tuple[bool, str, bool]:
        attempt = HostedHarnessAttempt.no_workspace_objects.select_related(
            "job", "job__organization"
        ).get(id=input.attempt_id)
        job = DaytonaHostedGateway().reconcile_completed(attempt)
        if job is None:
            return False, attempt.job.state, False
        retryable = job.state == HostedHarnessJob.State.RETRY_WAIT
        return (not retryable), job.state, retryable

    done, state, retryable = await _run_db(_poll)
    return HostedHarnessPollOutput(done=done, state=state, retryable=retryable)


@activity.defn(name="cancel_hosted_harness_attempt")
async def cancel_hosted_harness_attempt(
    input: HostedHarnessAttemptInput,
) -> HostedHarnessPollOutput:
    from simulate.models import HostedHarnessAttempt
    from simulate.services.hosted_harness_gateway import DaytonaHostedGateway

    def _cancel() -> str:
        attempt = HostedHarnessAttempt.no_workspace_objects.select_related(
            "job", "job__organization"
        ).get(id=input.attempt_id)
        reason = attempt.job.cancel_reason or "user_canceled"
        job = DaytonaHostedGateway().cancel(attempt.job, reason=reason)
        return job.state

    state = await _run_db(_cancel)
    return HostedHarnessPollOutput(done=True, state=state)
