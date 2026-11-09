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
import contextlib
import functools
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
from temporalio.exceptions import ApplicationError

from simulate.temporal.types.hosted_runner import (
    BuildRunnerJobInput,
    BuildRunnerJobOutput,
    FinalizeRunnerInput,
    RunHostedJobInput,
    RunHostedJobOutput,
)
from tfc.ee_loader import is_cloud_env

# Per-worker cap on concurrent child processes. Real capacity tuning (weighted
# units per §9.2) lands in Slice 4; this is a simple safety ceiling.
_MAX_CONCURRENCY = int(os.getenv("ALK_RUNNER_MAX_CONCURRENCY", "4"))
_child_semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

_SINK_ENV_BY_PURPOSE = {
    "api_key": "FI_API_KEY",
    "secret_key": "FI_SECRET_KEY",
    "internal_api_secret": "FI_INTERNAL_SUBMIT_SECRET",
}

# Frozen source -> destination contract: the builder's internal submit secret
# and the two explicit sink aliases retained by the r1 compatibility tests.
# A purpose alone is never authority to read an arbitrary worker credential.
_SINK_SOURCE_DESTINATIONS = frozenset(
    {
        ("INTERNAL_API_SECRET", "FI_INTERNAL_SUBMIT_SECRET"),
        ("SINK_API_TOKEN", "FI_API_KEY"),
        ("SINK_SECRET", "FI_SECRET_KEY"),
    }
)


@functools.lru_cache(maxsize=1)
def _customer_provider_env_keys() -> frozenset[str]:
    """Env keys that carry a CUSTOMER provider's secret. Derived from the profile
    table so a new provider cannot silently miss the C5 rule; LiveKit falls out
    because its api_key_env is None."""
    from simulate.services.hosted_runner import _PROVIDER_PROFILES

    return frozenset(
        p["api_key_env"] for p in _PROVIDER_PROFILES.values() if p.get("api_key_env")
    ) | {"VAPI_PUBLIC_API_KEY"}


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
    from simulate.services.hosted_runner import (
        build_start_runner_job,
        child_run_seconds,
    )
    from simulate.temporal.constants import (
        HOSTED_RUNNER_CHAT_MODE,
        HOSTED_RUNNER_CHAT_TIMEOUT_SECONDS,
    )

    job = await _run_db(
        build_start_runner_job,
        test_execution_id=input.test_execution_id,
        run_test_id=input.run_test_id,
        scenario_ids=input.scenario_ids,
        mode=input.mode,
        call_execution_ids=list(input.call_execution_ids or []),
    )
    # "or {}" — an explicit "params": null must not crash child_run_seconds,
    # which indexes into params via .get().
    voice_params = (job.get("voice") or {}).get("params") or {}
    # Keyed on mode, matching the workflow's own branch (D15) — not on
    # whether params happen to be present, so the two decisions can't drift.
    run_seconds = (
        HOSTED_RUNNER_CHAT_TIMEOUT_SECONDS
        if job["mode"] == HOSTED_RUNNER_CHAT_MODE
        else child_run_seconds(voice_params)
    )
    return BuildRunnerJobOutput(
        job_id=job["job_id"],
        run_id=job["metadata"]["run_id"],
        mode=job["mode"],
        job_json=json.dumps(job),
        run_seconds=run_seconds,
    )


def _check_child_launch_hold() -> None:
    """Refuse new children; this startup setting does not stop existing ones.

    No runtime-reloadable worker config is available here. Operators must set
    HOSTED_RUNNER_LAUNCH_HOLD=true before starting/replacing every runner worker;
    changing a deployment environment does not update an existing process.
    """
    if getattr(settings, "HOSTED_RUNNER_LAUNCH_HOLD", False):
        raise ApplicationError(
            "Hosted simulation child launches are on hold. Try again after the hold is lifted.",
            type="hosted_runner_launch_held",
            non_retryable=True,
        )


@activity.defn(name="run_hosted_sdk_job")
async def run_hosted_sdk_job(input: RunHostedJobInput) -> RunHostedJobOutput:
    """Spawn the SDK child, stream its status lines as heartbeats, await exit."""
    _check_child_launch_hold()
    from simulate.temporal.constants import HOSTED_RUNNER_CHAT_MODE

    # Chat has no budget to derive, matching build_runner_job's own
    # chat/not-chat split; this activity never replays, so — unlike the
    # workflow — it is safe to refuse here, before any child slot or DID
    # lease is touched.
    has_budget = isinstance(input.run_seconds, (int, float)) and input.run_seconds > 0
    if input.mode != HOSTED_RUNNER_CHAT_MODE and not has_budget:
        raise ApplicationError(
            "this run was built by an older worker and carries no time "
            "budget; start it again",
            type="hosted_run_budget_missing",
            non_retryable=True,
        )
    job = json.loads(input.job_json)
    # Wait for child capacity before creating scratch state or leasing a scarce
    # DID. Heartbeats keep the Temporal activity alive while all local child
    # slots are occupied.
    await _acquire_child_slot()

    # SIP is the only mode that touches the DID pool (mirrors the native
    # _needs_phone gate). Lease before spawning, inject the slot into the job,
    # release in finally. Web/chat never lease.
    did_slot = None
    work_dir: str | None = None
    proc: asyncio.subprocess.Process | None = None
    try:
        _check_child_launch_hold()
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
            did_slot = await _acquire_did_slot(input.job_id, input.run_seconds)
            if did_slot:
                _inject_did_slot(job, did_slot)

        # An originator job with no leased DID has nothing to dial — fail before
        # spawning the child instead of a hung readiness wait for a call that
        # never arrives; keyed on the injected DID, not the lease result, since
        # a lease can succeed with a slot carrying no phone number.
        voice = job.get("voice") or {}
        # "or {}" throughout, incl. transport — an explicit "transport": null must
        # not raise AttributeError instead of falling through the guard.
        transport = (voice.get("agent_definition") or {}).get("transport") or {}
        originator = transport.get("inbound_call_originator")
        # leased_did lives in metadata, not voice.params — the SDK splats
        # voice.params into a closed-kwarg function and a stray key raises TypeError.
        inbound_did = (job.get("metadata") or {}).get("leased_did")
        # A whitespace-only DID is not a phone number — treat it the
        # same as absent rather than letting it satisfy the guard.
        if isinstance(inbound_did, str) and not inbound_did.strip():
            inbound_did = None
        if originator and not inbound_did:
            raise ApplicationError(
                "Outbound simulation needs a leased phone number (DID) for the "
                f"{originator} originator but none was injected; check that "
                "ALK_SIM_SLOT_LEASE_SCRIPT is configured on the runner worker "
                "and that the leased pool slot carries a phone number",
                type="inbound_originator_requires_leased_did",
                non_retryable=True,
            )

        # A leased slot that names a routing rule but no room can never route
        # a call to the simulator; catch this before spawning, not only when
        # the child's dispatch check fails mid-run.
        kind = transport.get("kind")  # the same transport dict the DID guard reads
        rule = str(did_slot.get("dispatch_rule_name") or "").strip() if did_slot else ""
        room = str(did_slot.get("room_name") or "").strip() if did_slot else ""
        if did_slot and kind == "sip_inbound" and rule and not room:
            # A malformed lease is exactly the case this guard exists for, so
            # the id itself can be missing too — fall back rather than
            # rendering the literal "None" into an operator-facing message.
            slot_id = did_slot.get("slot_id") or did_slot.get("slot") or "<unknown>"
            raise ApplicationError(
                f"Leased phone-number slot {slot_id} names a routing rule "
                "but no room, so calls on it cannot reach the simulator; "
                "check the pool lease script output",
                type="leased_slot_requires_room",
                non_retryable=True,
            )

        with open(job_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(job))

        python = os.getenv("ALK_RUNNER_PYTHON", "python")
        # Worker-local sidecar, never taken from the job payload or sent to the
        # child. Every ref records its actual manager/source before resolution.
        origins: dict[str, tuple[str, str, str]] = {}
        child_env = _child_environment(job, origins=origins)
        try:
            child_env.update(await _resolve_voice_secret_env(job, origins=origins))
        except ApplicationError as exc:
            if exc.type != "vapi_public_control_key_required":
                raise
            # The workflow surfaces outcome.detail, whereas an activity raise
            # is wrapped as a generic ActivityError. Return the actionable
            # refusal, with no child created; the outer finally frees resources.
            return RunHostedJobOutput(phase="failed", return_code=1, detail=str(exc))
        child_env["FI_RUN_ROOT"] = run_root
        last: dict[str, Any] = {"phase": "pending"}
        return_code = 1

        # Enforce the credential classes on the exact spawn environment, after
        # every sink, target and metadata overlay (including future overlays).
        child_env = _final_child_environment(job, child_env, origins=origins)
        _check_child_launch_hold()
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
            await _terminate(proc)
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
        # A typed raise above (the DID guard, or credential resolution) exits
        # before the child is ever spawned, so the inner try/finally's rmtree
        # (only reached once create_subprocess_exec succeeds) never runs and
        # the scratch dir leaks. `proc is None` here means exactly that: no
        # child was started, so nothing else could still be reading work_dir.
        if proc is None and work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)
            with contextlib.suppress(OSError):
                os.rmdir(run_root)
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
        # A run already scoring is not turned failed by a failed runner
        # child: its finished calls are being graded, and the settle ends it.
        scoring = execution.status == TestExecution.ExecutionStatus.EVALUATING
        if execution.status not in (
            TestExecution.ExecutionStatus.COMPLETED,
            TestExecution.ExecutionStatus.CANCELLED,
            TestExecution.ExecutionStatus.EVALUATING,
        ):
            execution.status = TestExecution.ExecutionStatus.FAILED
            execution.save(update_fields=["status"])
        if execution.status not in (
            TestExecution.ExecutionStatus.COMPLETED,
            TestExecution.ExecutionStatus.CANCELLED,
        ):
            # The child is gone, so no result can land on a call it left
            # open. A scoring run can hold such calls too (an eval rerun of a
            # finished call moves a run still in transport into scoring), and
            # nothing would ever end the run while they stay open.
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
        if scoring:
            from simulate.services.scoring_status import settle_run

            # Its scoring may be closed already, with those calls the only
            # thing still holding it.
            settle_run(execution.id)
        return "failed"

    return await _run_db(_mark_terminal)


def _runs_base() -> str:
    return os.getenv("ALK_RUNNER_RUN_ROOT") or os.path.join(
        tempfile.gettempdir(), "alk-runner-runs"
    )


# Closed, case-sensitive inheritance contract for the built-in hosted branches.
# Reviewed against ALK c95d01efe3 (local origin/dev; deployed pin governs).
# No prefix matching: new SDK dependencies require an explicit review here.
# FI_* job/sink fields are injected below, never taken from ambient aliases.
# VAPI/RETELL customer keys remain resolved per job, not inherited.
_CHILD_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_COLLATE",
        "LC_MESSAGES",
        "LC_MONETARY",
        "LC_NUMERIC",
        "LC_TIME",
        "TZ",
        "TMPDIR",
        "PYTHONUNBUFFERED",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "PYTHONDONTWRITEBYTECODE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        # INTERIM v1.56.1 gateway-only voice parity (main 357 / RL 1840).
        # Replace this exact pair with the per-job proxy token when it lands.
        # No tenant code runs here; never admit other AGENTCC_* names.
        "AGENTCC_API_KEY",
        "AGENTCC_BASE_URL",
        "SIMULATOR_LLM_API_KEY",
        "SIMULATOR_LLM_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "SIMULATOR_LLM_API_KEY_HEADER",
        "GOOGLE_CLOUD_PROJECT",
        "VERTEX_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "VERTEX_LOCATION",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "SIMULATOR_LLM_THINKING",
        "SIMULATOR_LLM_FALLBACK_MODELS",
        "ELEVEN_API_KEY",
        "ELEVENLABS_API_KEY",
        "DEEPGRAM_API_KEY",
        "CARTESIA_API_KEY",
        "FI_HOSTED_DISPATCH_ACK",
        "ALK_VOICE_MAX_CASE_CONCURRENCY",
        "HARNESS_BACKGROUND_NOISE",
        "HARNESS_BACKGROUND_NOISE_VOLUME",
        "HARNESS_VOICEMAIL_CLIP",
        "HARNESS_VOICEMAIL_CLIP_TRANSCRIPT",
        "HARNESS_VOICEMAIL_CLIP_HAS_TONE",
        "HARNESS_CALLER_BARGE_IN_RATE",
        "HARNESS_CALL_DIRECTION",
        "SIMULATOR_VOICE_PROVIDER",
        "SIMULATOR_STT_MODEL",
        "SIMULATOR_TTS_MODEL",
        "SIMULATOR_TTS_VOICE_ID",
        "HARNESS_VOICEMAIL_STYLE",
        "HARNESS_ANSWERED_BY",
        "SIMULATOR_LLM_PROVIDER",
        "SIMULATOR_LLM_MODEL",
        "LIVEKIT_INBOUND_TRUNK_ID",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "VAPI_API_BASE_URL",
        "VAPI_ASSISTANT_ID",
        "VAPI_PHONE_NUMBER_ID",
        "RETELL_API_BASE_URL",
        "RETELL_AGENT_ID",
        "RETELL_FROM_NUMBER",
        "RETELL_API_URL",
        "RETELL_LIVEKIT_URL",
    }
)


# A source contract is not a denylist: unknown names never authorize a lookup.
# Retain the reviewed SDK runtime inputs above (including platform LiveKit and
# simulator credentials and the interim exact gateway pair). Customer account
# credentials below are env-only and self-host-only; never widen to a prefix.
_CHILD_ENV_OPERATOR_SOURCES = frozenset(
    {"VAPI_API_KEY", "VAPI_PUBLIC_API_KEY", "RETELL_API_KEY"}
)
# Only these settings have a reviewed secret-env use/fallback. An env contract
# name is not automatically permission to read a same-named Django setting.
_CHILD_ENV_SETTING_SOURCES = frozenset({"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"})


def _is_backend_only_secret(name: str) -> bool:
    """Forbidden destination classes, even when a job explicitly names them.

    Matching is case-insensitive; only the two exact SDK gateway spellings are
    exceptions. Sink aliases (FI_SECRET_KEY/FI_INTERNAL_SUBMIT_SECRET) and
    customer provider credentials are deliberately not generic secret matches.
    """
    upper = name.upper()
    if upper.startswith("AGENTCC_"):
        return name not in {"AGENTCC_API_KEY", "AGENTCC_BASE_URL"}
    return (
        upper.startswith(
            (
                "PG",
                "POSTGRES",
                "REDIS",
                "CLICKHOUSE",
                "TEMPORAL",
                "CELERY",
                "CH_",
                "CH25_",
            )
        )
        or bool(
            set(upper.split("_"))
            & {
                "PG",
                "POSTGRES",
                "REDIS",
                "CLICKHOUSE",
                "TEMPORAL",
                "CELERY",
                "CH",
                "DATABASE",
                "PGBOUNCER",
            }
        )
        or "ENCRYPTION_KEY" in upper
        or upper
        in {
            "SECRET_KEY",
            "DJANGO_SECRET_KEY",
            "INTERNAL_API_SECRET",
            "ALK_CAB_GUEST_POC_PIN",
            "ALK_UNSAFE_INPROCESS_CODE_ACTORS",
        }
    )


def _secret_ref_origin(ref: dict[str, Any], scope: str) -> tuple[str, str, str]:
    """Describe the exact lookup, not the (possibly aliased) destination name."""
    manager = str(ref.get("manager") or "")
    if manager == "provider_credentials":
        source = ref.get("credential_id")
    elif manager == "setting":
        source = ref.get("setting")
    else:
        source = ref.get("source") or ref.get("key")
    return manager, str(source or ""), scope


def _secret_source_allowed(origin: tuple[str, str, str], destination: str) -> bool:
    """Two-sided policy shared by lookup and the exact final spawn boundary."""
    manager, source, scope = origin
    if scope == "sink":
        return manager == "env" and (source, destination) in _SINK_SOURCE_DESTINATIONS
    if manager == "provider_credentials":
        # This source is a customer record ID, not a worker env/setting name.
        return bool(source)
    cloud = is_cloud_env(getattr(settings, "CLOUD_DEPLOYMENT", "") or "")
    if manager == "setting":
        allowed = source in _CHILD_ENV_SETTING_SOURCES
    elif manager == "env":
        allowed = source in _CHILD_ENV_ALLOWLIST or (
            not cloud and source in _CHILD_ENV_OPERATOR_SOURCES
        )
    else:
        return False
    # The existing backend-class gate is deliberately redundant with the
    # closed contract. New names must pass BOTH, not just miss a denylist.
    if not allowed or _is_backend_only_secret(source):
        return False
    if cloud and source.upper() in {
        key.upper() for key in _customer_provider_env_keys()
    }:
        return False
    return True


def _final_child_environment(
    job: dict[str, Any],
    env: dict[str, str],
    *,
    origins: dict[str, tuple[str, str, str]],
) -> dict[str, str]:
    """Check destination classes AND worker-local source provenance at spawn.

    Dropping rather than refusing preserves authorized sink/customer mappings
    in older jobs. The sidecar is mandatory here; it is never job-supplied.
    Never log values or entire references. Self-host operators own their worker
    account; Cloud must only use resolved customer keys.
    """
    if not is_cloud_env(getattr(settings, "CLOUD_DEPLOYMENT", "") or ""):
        # A declared but unresolved customer ref must not fall back. Successful
        # per-job resolution wins over the operator's compatibility value.
        key = "VAPI_PUBLIC_API_KEY"
        if key not in env and key not in _secret_env_ref_keys(job):
            value = os.environ.get(key)
            if value:
                env[key] = value
    refs = list((job.get("metadata") or {}).get("secret_env") or [])
    target = (job.get("spec") or {}).get("target") or {}
    refs.extend((target.get("secret_refs") or {}).values())
    # Include denied ref names already stripped by an earlier helper so those
    # drops are recorded too. Sink SOURCE names are not child destinations.
    names = set(env) | {str(ref["key"]) for ref in refs if ref.get("key")}
    denied = sorted(
        {name for name in names if _is_backend_only_secret(name)}
        | {
            destination
            for destination, origin in origins.items()
            if not _secret_source_allowed(origin, destination)
            or (
                origin[0] in {"env", "setting"}
                and not isinstance(env.get(destination), str)
            )
        }
    )
    for name in denied:
        env.pop(name, None)
    if denied:
        activity.logger.warning("Dropped forbidden hosted child env names: %s", denied)
    return env


def _secret_env_ref_keys(job: dict[str, Any]) -> set[str]:
    """Every env-var NAME any secret ref in this job claims to supply.

    WHY: an unresolved ref must yield NO value — never whatever the worker
    process happens to already have under that name — so these names are
    scrubbed from the inherited copy before the resolved values are overlaid.
    """
    keys: set[str] = set()
    for ref in (job.get("metadata") or {}).get("secret_env") or []:
        if ref.get("key"):
            keys.add(str(ref["key"]))
    for ref in ((job.get("sink") or {}).get("secret_refs") or {}).values():
        if ref.get("key"):
            keys.add(str(ref["key"]))
        destination = _SINK_ENV_BY_PURPOSE.get(ref.get("purpose"))
        if destination:
            keys.add(destination)
    target = (job.get("spec") or {}).get("target") or {}
    for ref in (target.get("secret_refs") or {}).values():
        if ref.get("key"):
            keys.add(str(ref["key"]))
    return keys


def _child_environment(
    job: dict[str, Any], *, origins: dict[str, tuple[str, str, str]] | None = None
) -> dict[str, str]:
    # Start empty except for reviewed runtime names. Unknown worker names,
    # backend credentials and unsafe code-execution toggles are not inherited.
    env = {k: v for k, v in os.environ.items() if k in _CHILD_ENV_ALLOWLIST}
    # Keep the existing customer-provider invariant even if a future allowlist
    # edit accidentally adds a provider-profile key: only per-job credential
    # resolution may supply it. LiveKit keys are platform runtime credentials,
    # not customer-profile keys, so they remain eligible for inheritance.
    for name in _customer_provider_env_keys():
        env.pop(name, None)
    # A job-declared secret ref (provider key, sink/target secret) must never
    # be satisfied by simply inheriting the worker's own value for that name —
    # pop it here so only a successful resolution (below, or the metadata
    # overlay in run_hosted_sdk_job) can put it back.
    for name in _secret_env_ref_keys(job):
        env.pop(name, None)
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
        dest = _SINK_ENV_BY_PURPOSE.get(ref.get("purpose"))
        if dest:
            value = _resolve_secret(
                ref, destination=dest, scope="sink", origins=origins
            )
            if value:
                env[dest] = value

    target = (job.get("spec") or {}).get("target") or {}
    for ref in (target.get("secret_refs") or {}).values():
        value = _resolve_secret(ref, origins=origins)
        if value and ref.get("key"):
            env[str(ref["key"])] = value

    # The DID is allocated after the job is built, so it cannot ride the
    # normal metadata.secret_env path. The SDK's inbound originator reads this
    # exact env var when it asks Vapi to dial the leased simulator number.
    # Read from metadata (never voice.params — the SDK splats params as kwargs).
    inbound_did = (job.get("metadata") or {}).get("leased_did")
    env.pop("LIVEKIT_INBOUND_DID", None)
    if inbound_did:
        env["LIVEKIT_INBOUND_DID"] = str(inbound_did)
    # SecretRef names are job overlays, not permission to enable local code.
    env.pop("ALK_UNSAFE_INPROCESS_CODE_ACTORS", None)
    return env


def _resolve_secret(
    ref: dict[str, Any],
    *,
    destination: str | None = None,
    scope: str = "target",
    origins: dict[str, tuple[str, str, str]] | None = None,
) -> str | None:
    destination = destination or str(ref.get("key") or "")
    origin = _secret_ref_origin(ref, scope)
    if origins is not None and destination:
        origins[destination] = origin
    # Cloud public control credentials remain customer-owned. Self-host may
    # explicitly resolve only the exact reviewed operator env source names.
    if (
        destination == "VAPI_PUBLIC_API_KEY"
        and is_cloud_env(getattr(settings, "CLOUD_DEPLOYMENT", "") or "")
    ) or not _secret_source_allowed(origin, destination):
        return None
    if origin[0] == "env" and destination:
        return os.environ.get(origin[1])
    return None


async def _resolve_voice_secret_env(
    job: dict[str, Any], *, origins: dict[str, tuple[str, str, str]] | None = None
) -> dict[str, str]:
    """Resolve a voice job's ``metadata.secret_env`` refs into child env vars.

    Provider secrets (Vapi/Retell/LiveKit keys) come from ``ProviderCredentials``
    (decrypted); LiveKit system creds fall back to the worker env. Returns the
    env-var → value map to overlay on the child environment.
    """
    secret_env = ((job.get("metadata") or {}).get("secret_env")) or []

    def _resolve() -> dict[str, str]:
        close_old_connections()
        resolved: dict[str, str] = {}
        cache: dict[str, Any] = {}
        for ref in secret_env:
            key = ref.get("key")
            if not key:
                continue
            manager = ref.get("manager")
            # On Cloud, customer-account destinations must use per-job
            # records, never the platform's worker keys. Self-host operators
            # are the customer; the closed source contract below still limits
            # their env refs to exact reviewed names, never arbitrary prefixes.
            # LiveKit system runtime credentials remain distinct from these
            # customer-provider credentials.
            if (
                str(key) in _customer_provider_env_keys()
                and manager != "provider_credentials"
                and is_cloud_env(getattr(settings, "CLOUD_DEPLOYMENT", "") or "")
            ):
                source = str(ref.get("source") or ref.get("setting") or key)
                raise ApplicationError(
                    f"{key} for a customer job must resolve via "
                    f"provider_credentials, not any other manager "
                    f"(source={source})",
                    type="provider_credentials_missing",
                    non_retryable=True,
                )
            origin = _secret_ref_origin(ref, "metadata")
            if origins is not None:
                origins[str(key)] = origin
            if not _secret_source_allowed(origin, str(key)):
                continue
            if manager == "provider_credentials":
                value = _resolve_provider_credential(ref, cache)
            elif manager == "setting":
                value = getattr(settings, origin[1], None)
                # Missing/empty settings retain the reviewed same-name env
                # fallback. Aggregates and other non-strings are never coerced
                # or rescued by Python truthiness (e.g. an empty list).
                if value is None or (isinstance(value, str) and not value):
                    value = os.environ.get(origin[1])
            else:  # only the explicitly admitted env manager can reach here
                value = os.environ.get(origin[1])
            if manager in {"env", "setting"} and not isinstance(value, str):
                continue
            if value:
                resolved[str(key)] = str(value)
        _check_vapi_control_key(job, resolved, cache)
        return resolved

    return await sync_to_async(_resolve, thread_sensitive=True)()


def _check_vapi_control_key(
    job: dict[str, Any], resolved: dict[str, str], cache: dict[str, Any]
) -> None:
    """Refuse only explicitly saved enabled control auth without a per-job key.

    UNKNOWN policy runs as at v1.56.1: the SDK discovers provider policy and its
    generic vapi_call_start_failed path remains unchanged (main 435). Never
    borrow a Cloud worker key or infer a new error code from exception text.
    """
    transport = ((job.get("voice") or {}).get("agent_definition") or {}).get(
        "transport"
    ) or {}
    if (
        not is_cloud_env(getattr(settings, "CLOUD_DEPLOYMENT", "") or "")
        or transport.get("inbound_call_originator") != "vapi"
    ):
        return
    refs = (job.get("metadata") or {}).get("secret_env") or []
    credential_ids = {
        ref.get("credential_id")
        for ref in refs
        if ref.get("key") == "VAPI_API_KEY"
        and ref.get("manager") == "provider_credentials"
    }
    requires_key = False
    for credential_id in credential_ids:
        credential = cache.get(credential_id)
        if credential is not None and credential.provider_type == "vapi":
            config = credential.config_json or {}
            plan = config.get("monitorPlan") if isinstance(config, dict) else None
            requires_key = requires_key or (
                isinstance(plan, dict)
                and plan.get("controlAuthenticationEnabled") is True
            )
            if (
                len(credential_ids) == 1
                and resolved.get("VAPI_PUBLIC_API_KEY")
                and any(
                    ref.get("key") == "VAPI_PUBLIC_API_KEY"
                    and ref.get("manager") == "provider_credentials"
                    and ref.get("credential_id") == credential_id
                    for ref in refs
                )
            ):
                return
    if not requires_key:
        return
    raise ApplicationError(
        "This test can't run on hosted Future AGI yet: the Vapi assistant has control "
        "authentication turned on, and saving its Vapi public key here is coming soon. "
        "No call was started. Until then, test a Vapi assistant without control "
        "authentication, or run the test on a self-hosted install, where the operator "
        "sets the Vapi public key.",
        type="vapi_public_control_key_required",
        non_retryable=True,
    )


def _resolve_provider_credential(
    ref: dict[str, Any], cache: dict[str, Any]
) -> str | None:
    from simulate.models.agent_definition import ProviderCredentials

    credential_id = ref.get("credential_id")
    env_key = str(ref.get("key") or "")
    if not credential_id:
        return None
    credentials = cache.get(credential_id)
    if credentials is None:
        try:
            credentials = ProviderCredentials.objects.get(id=credential_id)
        except ProviderCredentials.DoesNotExist:
            # Never fall through to the worker's own env — a stale/deleted ref
            # would otherwise let the child dial on the platform's own key.
            raise ApplicationError(
                f"provider credential {credential_id} (for env {env_key}) "
                "does not exist",
                type="provider_credentials_missing",
                non_retryable=True,
            ) from None
        cache[credential_id] = credentials
    field = ref.get("field")
    if env_key == "VAPI_PUBLIC_API_KEY" or field == "public_api_key":
        # The public control key is saved in this customer's target config,
        # not the worker environment, private API key or LiveKit api_secret.
        if (
            env_key != "VAPI_PUBLIC_API_KEY"
            or field != "public_api_key"
            or credentials.provider_type != "vapi"
        ):
            raise ApplicationError(
                "Invalid Vapi public control credential reference",
                type="provider_credentials_missing",
                non_retryable=True,
            )
        config = credentials.config_json or {}
        plan = config.get("monitorPlan", {}) if isinstance(config, dict) else None
        authenticated = (
            plan.get("controlAuthenticationEnabled", False)
            if isinstance(plan, dict)
            else None
        )
        if type(authenticated) is not bool:
            raise ApplicationError(
                "Invalid Vapi control authentication configuration",
                type="provider_credentials_missing",
                non_retryable=True,
            )
        if not authenticated:
            return None
        public_key = config.get("public_api_key")
        value = public_key.strip() if isinstance(public_key, str) else None
        if not value:
            raise ApplicationError(
                "This test can't run on hosted Future AGI yet: the Vapi assistant has "
                "control authentication turned on, and saving its Vapi public key here "
                "is coming soon. No call was started. Until then, test a Vapi "
                "assistant without control authentication, or run the test on a "
                "self-hosted install, where the operator sets the Vapi public key.",
                type="vapi_public_control_key_required",
                non_retryable=True,
            )
    else:
        value = (
            credentials.get_api_secret()
            if field == "api_secret"
            else credentials.get_api_key()
        )
    if not value:
        raise ApplicationError(
            f"provider credential {credential_id} has no {field or 'api_key'} "
            f"(for env {env_key})",
            type="provider_credentials_missing",
            non_retryable=True,
        )
    return value


async def _acquire_did_slot(job_id: str, run_seconds: int) -> dict[str, Any] | None:
    """Lease one DID slot from the livekit-infra inbound-simulator pool
    (tasks #115-119). Returns a normalized slot dict (``did``,
    ``dispatch_rule_name``, ``slot_id``) or None when no lease script is
    configured — the SDK then
    provisions a per-run dispatch rule itself (sip_inbound default).

    The lease helper is a separate repo/script; we shell out to it so the
    backend keeps no LiveKit-infra import. Failures degrade to None rather than
    aborting the run, allowing the SDK to provision its own inbound rule.
    """
    # Local import: a module-level one would run before simulate.temporal's
    # package __init__ finishes if this module were ever imported first.
    from simulate.temporal.constants import HOSTED_RUNNER_PARENT_SLACK_SECONDS

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
            "--ttl",
            str(run_seconds + HOSTED_RUNNER_PARENT_SLACK_SECONDS),
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
        # Ownership tag so the release call can arm the script's guard against
        # freeing a slot another run now holds.
        slot["run_id"] = job_id
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
    argv = [python, script, "release", "--slot", str(slot_id)]
    run_id = slot.get("run_id")
    if run_id:
        argv.extend(["--run-id", str(run_id)])
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            code: Any = proc.returncode
            try:
                parsed = json.loads(out.decode("utf-8", "replace").strip())
                if isinstance(parsed, dict) and parsed.get("code"):
                    code = parsed["code"]
            except ValueError:
                pass
            # Never log the phone number — only our own slot id and the
            # script's own error code.
            activity.logger.warning(f"DID lease release failed for {slot_id}: {code}")
    except Exception as exc:  # noqa: BLE001
        activity.logger.warning(f"DID lease release error for {slot_id}: {exc}")


def _inject_did_slot(job: dict[str, Any], slot: dict[str, Any]) -> None:
    """Attach the leased slot to a sip_inbound job before the child runs.

    Only sip_inbound consumes a leased DID (the target dials the simulator's
    number, routed by the dispatch rule). sip_outbound dials the target's own
    number over the outbound trunk and needs no pool slot.
    """
    # "or {}" throughout, incl. transport — an explicit "transport": null (or
    # "agent_definition": null) must not raise AttributeError, matching the
    # activity's own guard a few lines up.
    transport = ((job.get("voice") or {}).get("agent_definition") or {}).get(
        "transport"
    ) or {}
    if transport.get("kind") != "sip_inbound":
        return
    rule = slot.get("dispatch_rule_name")
    if rule:
        transport["dispatch_rule_name"] = rule
    did = slot.get("did")
    # Store the STRIPPED value, not just check it for blankness — an
    # unstripped DID (e.g. " +15557654321 ") would reach LIVEKIT_INBOUND_DID
    # and metadata.leased_did verbatim, the same class of bug room_name has.
    if isinstance(did, str):
        did = did.strip() or None
    if did:
        # metadata only — voice.params is splatted as kwargs by the SDK, which
        # has no inbound_did parameter and would raise TypeError. An explicit
        # "metadata": null must not raise either, so replace rather than rely
        # on setdefault (which only fires when the key is absent, not None).
        metadata = job.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            job["metadata"] = metadata
        metadata["leased_did"] = did

    # Pin the pool room only where the build guard and D12 budget already
    # cover it (a single case, or a multi-case run with an originator) —
    # reuse only where the guard and budget apply; any other multi-case job
    # keeps the templated runtime exactly as today.
    room_name = slot.get("room_name")
    # Store the STRIPPED value, not just check it — an unstripped pin (e.g.
    # " sim-slot-01 ") will not match the pool rule's destination downstream.
    if isinstance(room_name, str):
        room_name = room_name.strip() or None
    # "or {}" on scenario too — an explicit "scenario": null must not raise.
    scenario = (job.get("voice") or {}).get("scenario") or {}
    dataset = scenario.get("dataset") or []
    if room_name and (len(dataset) == 1 or transport.get("inbound_call_originator")):
        runtime = job.setdefault("voice", {}).setdefault("livekit_runtime", {})
        runtime["room_name"] = str(room_name)
        runtime["room_name_verbatim"] = True


def _parse_status_line(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) and "phase" in parsed else None


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                return
            await proc.wait()
