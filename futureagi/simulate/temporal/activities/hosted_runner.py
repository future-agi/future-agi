"""Hosted-runner activities for SimulationRunnerWorkflow (plan §9).

Run on the ``simulation_runner`` queue, polled by the dedicated simulation-runner
worker. ``run_hosted_sdk_job`` spawns the released SDK as a child process — the
runner orchestrates the SDK, it does not run simulation logic itself.

IMPORTANT: DB-touching activities recycle the ORM connection via ``_run_db``,
which calls ``close_old_connections()`` INSIDE the ``thread_sensitive`` executor
thread — the same thread that issues the query. Django connections are
thread-local, so recycling from the async activity body heals the wrong thread
and leaves the executor's stale (server-closed) handle in place (PgBouncer pool
safety).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone
from temporalio import activity

from simulate.temporal.types.hosted_runner import (
    BuildRunnerJobInput,
    BuildRunnerJobOutput,
    FinalizeRunnerInput,
    RunHostedJobInput,
    RunHostedJobOutput,
)

# Per-worker cap on concurrent child processes. Real capacity tuning (weighted
# units per §9.2) lands in Slice 4; this is a simple safety ceiling.
_MAX_CONCURRENCY = int(os.getenv("ALK_RUNNER_MAX_CONCURRENCY", "4"))
_child_semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

_SINK_ENV_BY_PURPOSE = {
    "api_key": "FI_API_KEY",
    "secret_key": "FI_SECRET_KEY",
    "internal_api_secret": "FI_INTERNAL_SUBMIT_SECRET",
}
_CHILD_SLOT_HEARTBEAT_SECONDS = float(
    os.getenv("ALK_RUNNER_SLOT_HEARTBEAT_SECONDS", "20")
)


async def _run_db(fn, /, *args, **kwargs):
    """Recycle the ORM connection and run ``fn`` on the query's own thread.

    ``close_old_connections()`` must fire INSIDE the ``thread_sensitive``
    executor thread that issues the query: Django connections are thread-local,
    so recycling from the async activity body heals the wrong thread and leaves
    the executor's stale (server-closed) handle in place.
    """

    def _call():
        close_old_connections()
        return fn(*args, **kwargs)

    return await sync_to_async(_call, thread_sensitive=True)()


@activity.defn(name="build_runner_job")
async def build_runner_job(input: BuildRunnerJobInput) -> BuildRunnerJobOutput:
    from simulate.services.hosted_runner import build_start_runner_job

    job = await _run_db(
        build_start_runner_job,
        test_execution_id=input.test_execution_id,
        run_test_id=input.run_test_id,
        scenario_ids=input.scenario_ids,
        mode=input.mode,
        call_execution_ids=list(input.call_execution_ids or []),
    )
    return BuildRunnerJobOutput(
        job_id=job["job_id"],
        run_id=job["metadata"]["run_id"],
        mode=job["mode"],
        job_json=json.dumps(job),
    )


@activity.defn(name="run_hosted_sdk_job")
async def run_hosted_sdk_job(input: RunHostedJobInput) -> RunHostedJobOutput:
    """Spawn the SDK child, stream its status lines as heartbeats, await exit."""
    job = json.loads(input.job_json)
    # Wait for child capacity before creating scratch state or leasing a scarce
    # DID. Heartbeats keep the Temporal activity alive while all local child
    # slots are occupied.
    await _acquire_child_slot()

    # SIP is the only mode that touches the DID pool (mirrors the native
    # _needs_phone gate). Lease before spawning, inject the slot into the job,
    # release in finally. Web/chat never lease.
    did_slot = None
    try:
        # Scratch + run-root setup can fail (disk full, bad ALK_RUNNER_RUN_ROOT).
        # Keep it INSIDE the try so the finally still releases the child slot
        # rather than leaking it — otherwise repeated setup failures exhaust the
        # semaphore and every later job blocks forever. (Acquire stays outside so
        # a cancel mid-acquire can't over-release a slot it never took.)
        #
        # Scratch holds only job.json + status.jsonl; the run artifacts
        # (report/events/submission) go to a durable, absolute run root OUTSIDE the
        # scratch so a failed run leaves evidence (§9.2 preserve crash artifacts).
        work_dir = tempfile.mkdtemp(prefix=f"alk-runner-{input.job_id}-")
        run_root = os.path.join(_runs_base(), input.job_id)
        os.makedirs(run_root, exist_ok=True)
        job_path = os.path.join(work_dir, "job.json")
        status_path = os.path.join(work_dir, "status.jsonl")

        if input.mode == "voice_sip":
            did_slot = await _acquire_did_slot(input.job_id)
            if did_slot:
                _inject_did_slot(job, did_slot)

        with open(job_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(job))

        python = os.getenv("ALK_RUNNER_PYTHON", "python")
        child_env = _child_environment(job)
        child_env.update(await _resolve_voice_secret_env(job))
        child_env["FI_RUN_ROOT"] = run_root
        last: dict[str, Any] = {"phase": "pending"}
        return_code = 1

        proc = await asyncio.create_subprocess_exec(
            python,
            "-m",
            "fi.simulate.hosted.child_entrypoint",
            job_path,
            "--status-file",
            status_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=child_env,
            cwd=work_dir,
        )
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                parsed = _parse_status_line(line)
                if parsed is not None:
                    last = parsed
                    activity.heartbeat(parsed.get("phase"))
                else:
                    # Surface the SDK child's engine logs + tracebacks (stderr is
                    # merged into stdout). These were previously dropped, hiding
                    # why a run produced no turns / failed. Truncate for sanity.
                    activity.logger.info(
                        "alk_child[%s] %s", input.job_id[:8], line[:2000]
                    )
            return_code = await proc.wait()
        except asyncio.CancelledError:
            _terminate(proc)
            raise
        finally:
            # Keep the scratch on failure for debugging; the run root is durable.
            if return_code == 0:
                shutil.rmtree(work_dir, ignore_errors=True)
            else:
                activity.logger.warning(
                    f"hosted job {input.job_id} exited {return_code}; "
                    f"artifacts preserved at {run_root} (scratch {work_dir})"
                )
    finally:
        if did_slot is not None:
            await _release_did_slot(did_slot)
        _child_semaphore.release()

    return RunHostedJobOutput(
        phase=last.get("phase", "failed"),
        return_code=return_code,
        report_hash=last.get("report_hash"),
        submission_status=last.get("submission_status"),
        detail=last.get("detail"),
    )


async def _acquire_child_slot() -> None:
    while True:
        try:
            await asyncio.wait_for(
                _child_semaphore.acquire(),
                timeout=_CHILD_SLOT_HEARTBEAT_SECONDS,
            )
            return
        except TimeoutError:
            activity.heartbeat("waiting_for_child_slot")


@activity.defn(name="finalize_hosted_execution")
async def finalize_hosted_execution(input: FinalizeRunnerInput) -> str:
    """Advance the TestExecution to a terminal state.

    On a failed child the execution is marked FAILED directly; otherwise the
    status is driven by the ingestion + eval rollup (``monitor_test_execution_for_chat``),
    which we trigger here so it settles even if the last PATCH already fired.
    """
    from simulate.models.test_execution import CallExecution, TestExecution

    if input.job_phase == "completed":
        from simulate.tasks.chat_sim import monitor_test_execution_for_chat

        def _fail_unsettled_ongoing() -> None:
            # A completed child means every result had its chance to land. A row
            # still ONGOING started its call but never got a result (a lost
            # result PATCH) — fail it so it can't display "ongoing" forever.
            # PENDING rows (never started — e.g. an over-provisioned batch) are
            # left untouched, unchanged from before this transition existed.
            CallExecution.objects.filter(
                test_execution_id=input.test_execution_id,
                status=CallExecution.CallStatus.ONGOING,
                deleted=False,
            ).update(
                status=CallExecution.CallStatus.FAILED,
                completed_at=timezone.now(),
            )

        await _run_db(_fail_unsettled_ongoing)
        await _run_db(monitor_test_execution_for_chat, input.test_execution_id)
        return "rolled_up"

    def _mark_terminal() -> str:
        execution = TestExecution.objects.get(id=input.test_execution_id)
        if input.job_phase == "cancelled":
            execution.status = TestExecution.ExecutionStatus.CANCELLED
            execution.save(update_fields=["status"])
            CallExecution.objects.filter(
                test_execution=execution,
                status__in=[
                    CallExecution.CallStatus.PENDING,
                    CallExecution.CallStatus.ONGOING,
                ],
                deleted=False,
            ).update(
                status=CallExecution.CallStatus.CANCELLED,
                completed_at=timezone.now(),
            )
            return "cancelled"
        if execution.status not in (
            TestExecution.ExecutionStatus.COMPLETED,
            TestExecution.ExecutionStatus.CANCELLED,
        ):
            execution.status = TestExecution.ExecutionStatus.FAILED
            execution.save(update_fields=["status"])
            CallExecution.objects.filter(
                test_execution=execution,
                status__in=[
                    CallExecution.CallStatus.PENDING,
                    CallExecution.CallStatus.ONGOING,
                ],
                deleted=False,
            ).update(
                status=CallExecution.CallStatus.FAILED,
                completed_at=timezone.now(),
            )
        return "failed"

    return await _run_db(_mark_terminal)


def _runs_base() -> str:
    return os.getenv("ALK_RUNNER_RUN_ROOT") or os.path.join(
        tempfile.gettempdir(), "alk-runner-runs"
    )


_CHILD_ENV_DENY_EXACT = frozenset(
    {
        "INTERNAL_API_SECRET",
        "SECRET_KEY",
        "DJANGO_SECRET_KEY",
        "DATABASE_URL",
        "PGPASSWORD",
    }
)
_CHILD_ENV_DENY_PREFIXES = (
    "POSTGRES_",
    "PGBOUNCER_",
    "TEMPORAL_",
    "CELERY_",
    "RABBITMQ_",
    "REDIS_",
    "CLICKHOUSE_",
)


def _is_backend_only_secret(key: str) -> bool:
    return key in _CHILD_ENV_DENY_EXACT or key.startswith(_CHILD_ENV_DENY_PREFIXES)


def _child_environment(job: dict[str, Any]) -> dict[str, str]:
    # Copy the worker env MINUS backend-only secrets the SDK child never needs
    # (DB creds, the internal API secret — the child instead receives that by
    # ref below via _SINK_ENV_BY_PURPOSE — plus Temporal/Celery/broker/cache
    # creds). A strict allowlist is the stronger posture but risks omitting a
    # provider/LiveKit var the child depends on; the denylist closes the named
    # leak without that breakage risk. TODO: tighten to an allowlist once the
    # child's required env is enumerated.
    env = {k: v for k, v in os.environ.items() if not _is_backend_only_secret(k)}
    # Child cwd is a mkdtemp dir; absolutize a relative key path so it resolves.
    creds = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds and not os.path.isabs(creds):
        env["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(creds)
    sink = job.get("sink") or {}
    if sink.get("api_url"):
        env["FI_BASE_URL"] = str(sink["api_url"])
    if sink.get("run_test_id"):
        env["FI_RUN_TEST_ID"] = str(sink["run_test_id"])
    if sink.get("test_execution_id"):
        env["FI_TEST_EXECUTION_ID"] = str(sink["test_execution_id"])

    for ref in (sink.get("secret_refs") or {}).values():
        value = _resolve_secret(ref)
        dest = _SINK_ENV_BY_PURPOSE.get(ref.get("purpose"))
        if dest and value:
            env[dest] = value

    target = (job.get("spec") or {}).get("target") or {}
    for ref in (target.get("secret_refs") or {}).values():
        value = _resolve_secret(ref)
        if value and ref.get("key"):
            env[str(ref["key"])] = value

    # The DID is allocated after the job is built, so it cannot ride the
    # normal metadata.secret_env path. The SDK's inbound originator reads this
    # exact env var when it asks Vapi to dial the leased simulator number.
    inbound_did = ((job.get("voice") or {}).get("params") or {}).get("inbound_did")
    if inbound_did:
        env["LIVEKIT_INBOUND_DID"] = str(inbound_did)
    return env


def _resolve_secret(ref: dict[str, Any]) -> str | None:
    # Slice 1 supports the env manager; platform/secret-manager resolution
    # (short-lived per-job tokens, §10.1) lands with the hosted deployment.
    if ref.get("manager") == "env" and ref.get("key"):
        return os.environ.get(str(ref["key"]))
    return None


async def _resolve_voice_secret_env(job: dict[str, Any]) -> dict[str, str]:
    """Resolve a voice job's ``metadata.secret_env`` refs into child env vars.

    Provider secrets (Vapi/Retell/LiveKit keys) come from ``ProviderCredentials``
    (decrypted); LiveKit system creds fall back to the worker env. Returns the
    env-var → value map to overlay on the child environment.
    """
    secret_env = ((job.get("metadata") or {}).get("secret_env")) or []
    if not secret_env:
        return {}

    def _resolve() -> dict[str, str]:
        close_old_connections()
        resolved: dict[str, str] = {}
        cache: dict[str, Any] = {}
        for ref in secret_env:
            key = ref.get("key")
            if not key:
                continue
            manager = ref.get("manager")
            if manager == "provider_credentials":
                value = _resolve_provider_credential(ref, cache)
            elif manager == "setting":
                value = getattr(
                    settings, ref.get("setting", ""), None
                ) or os.environ.get(str(ref.get("setting", "")))
            else:  # env passthrough
                value = os.environ.get(str(ref.get("source") or key))
            if value:
                resolved[str(key)] = str(value)
        return resolved

    return await sync_to_async(_resolve, thread_sensitive=True)()


def _resolve_provider_credential(
    ref: dict[str, Any], cache: dict[str, Any]
) -> str | None:
    from simulate.models.agent_definition import ProviderCredentials

    credential_id = ref.get("credential_id")
    if not credential_id:
        return None
    credentials = cache.get(credential_id)
    if credentials is None:
        try:
            credentials = ProviderCredentials.objects.get(id=credential_id)
        except ProviderCredentials.DoesNotExist:
            return None
        cache[credential_id] = credentials
    field = ref.get("field")
    if field == "api_secret":
        return credentials.get_api_secret()
    return credentials.get_api_key()


async def _acquire_did_slot(job_id: str) -> dict[str, Any] | None:
    """Lease one DID slot from the livekit-infra inbound-simulator pool
    (tasks #115-119). Returns a normalized slot dict (``did``,
    ``dispatch_rule_name``, ``slot_id``) or None when no lease script is
    configured — the SDK then
    provisions a per-run dispatch rule itself (sip_inbound default).

    The lease helper is a separate repo/script; we shell out to it so the
    backend keeps no LiveKit-infra import. Failures degrade to None rather than
    aborting the run, allowing the SDK to provision its own inbound rule.
    """
    script = os.getenv("ALK_SIM_SLOT_LEASE_SCRIPT")
    if not script:
        return None
    python = os.getenv("ALK_RUNNER_PYTHON", "python")
    try:
        proc = await asyncio.create_subprocess_exec(
            python,
            script,
            "acquire",
            "--run-id",
            job_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            activity.logger.warning(f"DID lease acquire failed for {job_id}: {out!r}")
            return None
        # The livekit-infra CLI emits indented JSON, not JSONL. Parsing only
        # its last line would see a bare ``}`` and silently disable leasing.
        slot = json.loads(out.decode("utf-8", "replace").strip())
        if not isinstance(slot, dict):
            return None
        # livekit-infra uses ``slot`` and ``phone_number``; keep the runner's
        # internal names stable for the job contract.
        if slot.get("slot") and not slot.get("slot_id"):
            slot["slot_id"] = slot["slot"]
        if slot.get("phone_number") and not slot.get("did"):
            slot["did"] = slot["phone_number"]
        return slot
    except Exception as exc:  # noqa: BLE001
        activity.logger.warning(f"DID lease acquire error for {job_id}: {exc}")
        return None


async def _release_did_slot(slot: dict[str, Any]) -> None:
    script = os.getenv("ALK_SIM_SLOT_LEASE_SCRIPT")
    slot_id = slot.get("slot_id") or slot.get("slot") or slot.get("did")
    if not script or not slot_id:
        return
    python = os.getenv("ALK_RUNNER_PYTHON", "python")
    try:
        proc = await asyncio.create_subprocess_exec(
            python,
            script,
            "release",
            "--slot",
            str(slot_id),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await proc.communicate()
    except Exception as exc:  # noqa: BLE001
        activity.logger.warning(f"DID lease release error for {slot_id}: {exc}")


def _inject_did_slot(job: dict[str, Any], slot: dict[str, Any]) -> None:
    """Attach the leased slot to a sip_inbound job before the child runs.

    Only sip_inbound consumes a leased DID (the target dials the simulator's
    number, routed by the dispatch rule). sip_outbound dials the target's own
    number over the outbound trunk and needs no pool slot.
    """
    transport = (
        (job.get("voice") or {}).get("agent_definition", {}).get("transport", {})
    )
    if transport.get("kind") != "sip_inbound":
        return
    rule = slot.get("dispatch_rule_name")
    if rule:
        transport["dispatch_rule_name"] = rule
    did = slot.get("did")
    if did:
        job.setdefault("voice", {}).setdefault("params", {})["inbound_did"] = did
        (job.setdefault("metadata", {}))["leased_did"] = did

    # A pool dispatch rule targets its slot's fixed room. ALK normally adds a
    # per-invocation suffix to managed room names, so a single-case leased run
    # must opt into the exact slot room or LiveKit rejects the rule as a
    # different destination. Multi-case inbound runs require SDK support for
    # sequential reuse of a leased room and intentionally keep the templated
    # runtime here.
    room_name = slot.get("room_name")
    dataset = (job.get("voice") or {}).get("scenario", {}).get("dataset") or []
    if room_name and len(dataset) == 1:
        runtime = job.setdefault("voice", {}).setdefault("livekit_runtime", {})
        runtime["room_name"] = str(room_name)
        runtime["room_name_verbatim"] = True


def _parse_status_line(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) and "phase" in parsed else None


def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
