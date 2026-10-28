"""The sandbox the improvement engine runs in during a fix: launch it, watch it, release it.

AgentFixWorkflow calls each function as one step; each decides what the platform saw."""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from simulate.models import AgentFixRun
from simulate.pydantic_schemas.agent_fix import (
    EngineCapability,
    EngineModels,
    FixRequest,
)
from simulate.services.agent_fix_runs import close_fix_run
from simulate.services.hosted_harness import HostedHarnessError, hash_secret
from simulate.services.hosted_harness_gateway import (
    _CHAT_RUNTIME_PATH,
    _hostname_from_url,
    _validate_resolved_egress_domains,
    platform_model_gateway,
)
from simulate.services.hosted_sandbox import (
    E2BSandboxRuntimeProvider,
    SandboxCommandRequest,
    SandboxLaunchSpec,
    SandboxNotFoundError,
    SandboxRuntimeProvider,
    get_sandbox_provider,
)
from simulate.temporal.types.agent_fix import AgentFixLaunch, Observation

logger = structlog.get_logger(__name__)

CAPABILITY_PATH = "/run/futureagi/agent-fix.json"
ENGINE_SESSION = "agent-fix-engine"
# Every watch renews the sandbox for this long, so a sandbox the platform lost
# track of deletes itself soon after.
LEASE_SECONDS = 20 * 60
_PROVIDER_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class EngineCredentials:
    token: str
    fence: str
    expires_at: datetime


def launch(fix_run_id: str) -> AgentFixLaunch:
    """Start the fix's engine in a new sandbox: the first time, or again from its
    checkpoint after the previous engine died. Says why instead when there is none."""
    refusal = _claim_launch(fix_run_id)
    if refusal is not None:
        return AgentFixLaunch(observation=refusal)
    release(fix_run_id)
    provider = _provider()
    models = platform_models()
    fix_run = AgentFixRun.no_workspace_objects.get(id=fix_run_id)
    sandbox = provider.create(
        _launch_spec(fix_run, models, provider),
        timeout=provider.create_timeout_seconds,
    )
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        sandbox_ref=sandbox.id, updated_at=timezone.now()
    )
    credentials = rotate_credentials(fix_run.id)
    if credentials is None:
        # The user stopped the run while its sandbox was created; the workflow deletes it.
        return AgentFixLaunch(observation=Observation.STOPPING)
    capability = EngineCapability(
        fix_run_id=str(fix_run.id),
        api_base=f"{_public_base_url()}/simulate/api/agent-fix-engine/{fix_run.id}/",
        token=credentials.token,
        fence=credentials.fence,
        expires_at=credentials.expires_at.isoformat(),
        models=models,
    )
    sandbox.fs.upload_file(capability.model_dump_json().encode(), CAPABILITY_PATH)
    prepared = sandbox.process.exec(f"chmod 0600 {CAPABILITY_PATH}", timeout=30)
    if prepared.exit_code:
        raise HostedHarnessError(
            "fix_sandbox_prepare_failed", prepared.result[-500:], status_code=502
        )
    sandbox.process.create_session(ENGINE_SESSION)
    # Only the template's PATH (node lives under /opt): every process in the sandbox
    # sees the environment, so credentials stay in the capability file.
    command = sandbox.process.execute_session_command(
        ENGINE_SESSION,
        SandboxCommandRequest(
            command=settings.AGENT_FIX_ENGINE_COMMAND,
            env={"PATH": _CHAT_RUNTIME_PATH},
            run_async=True,
        ),
    )
    logger.info("agent_fix_engine_started", fix_run=str(fix_run.id), sandbox=sandbox.id)
    return AgentFixLaunch(
        observation=Observation.RUNNING, command_id=str(command.cmd_id)
    )


def rotate_credentials(fix_run_id: str) -> EngineCredentials | None:
    """New token and fence for the sandbox about to run the engine; the previous
    sandbox's stop working. None when the run is no longer running."""
    token = secrets.token_urlsafe(32)
    fence = secrets.token_urlsafe(32)
    with transaction.atomic():
        fix_run = AgentFixRun.no_workspace_objects.select_for_update().get(
            id=fix_run_id
        )
        if fix_run.state != AgentFixRun.State.RUNNING:
            return None
        fix_run.token_hash = hash_secret(token)
        fix_run.fence_hash = hash_secret(fence)
        fix_run.token_expires_at = _deadline(fix_run)
        fix_run.sandbox_attempt += 1
        # A new engine has the report timeout to send its first report.
        fix_run.heartbeat_at = timezone.now()
        fix_run.save(
            update_fields=[
                "token_hash",
                "fence_hash",
                "token_expires_at",
                "sandbox_attempt",
                "heartbeat_at",
                "updated_at",
            ]
        )
    return EngineCredentials(
        token=token, fence=fence, expires_at=fix_run.token_expires_at
    )


def observe(fix_run_id: str, command_id: str) -> Observation:
    """One look at a running fix. The run's state, deadline and last report are read
    before the sandbox, so a sandbox API outage cannot hide a late run or a dead engine.
    Renews the sandbox's lease while the engine runs."""
    fix_run = AgentFixRun.no_workspace_objects.get(id=fix_run_id)
    settled = _settled(fix_run)
    if settled is not None:
        return settled
    now = timezone.now()
    if now >= _deadline(fix_run):
        close_fix_run(
            fix_run.id,
            AgentFixRun.State.FAILED,
            failure={
                "code": "fix_deadline",
                "message": "the fix ran out of the time it was given",
            },
        )
        return Observation.ENDED
    silent_for = now - fix_run.heartbeat_at
    if silent_for > timedelta(seconds=settings.AGENT_FIX_HEARTBEAT_TIMEOUT_SECONDS):
        logger.warning(
            "agent_fix_engine_silent",
            fix_run=str(fix_run.id),
            silent_seconds=int(silent_for.total_seconds()),
        )
        return Observation.ENGINE_GONE
    provider = _provider()
    try:
        sandbox = provider.get(
            fix_run.sandbox_ref, request_timeout=_PROVIDER_TIMEOUT_SECONDS
        )
    except SandboxNotFoundError:
        logger.warning("agent_fix_sandbox_gone", fix_run=str(fix_run.id))
        return Observation.ENGINE_GONE
    command = sandbox.process.get_session_command(
        ENGINE_SESSION, command_id, request_timeout=_PROVIDER_TIMEOUT_SECONDS
    )
    if command.exit_code is not None:
        logger.warning(
            "agent_fix_engine_exited",
            fix_run=str(fix_run.id),
            exit_code=command.exit_code,
        )
        return Observation.ENGINE_GONE
    provider.renew_ttl(sandbox, LEASE_SECONDS)
    return Observation.RUNNING


def release(fix_run_id: str) -> None:
    """Delete the fix's sandbox, if it has one. Safe to repeat."""
    sandbox_ref = AgentFixRun.no_workspace_objects.values_list(
        "sandbox_ref", flat=True
    ).get(id=fix_run_id)
    if not sandbox_ref:
        return
    provider = _provider()
    try:
        provider.delete(
            provider.get(sandbox_ref, request_timeout=_PROVIDER_TIMEOUT_SECONDS),
            timeout=120,
            wait=True,
        )
    except SandboxNotFoundError:
        pass
    AgentFixRun.no_workspace_objects.filter(
        id=fix_run_id, sandbox_ref=sandbox_ref
    ).update(sandbox_ref="", updated_at=timezone.now())
    logger.info(
        "agent_fix_sandbox_released", fix_run=str(fix_run_id), sandbox=sandbox_ref
    )


def platform_models() -> EngineModels:
    """AgentCC's OpenAI-compatible endpoint, on the key hosted runs use for every model."""
    url, key = platform_model_gateway()
    return EngineModels(base_url=f"{url.removesuffix('/v1')}/v1", api_key=key)


def _claim_launch(fix_run_id: str) -> Observation | None:
    """Take the run's next sandbox, or say why it gets none. The previous sandbox's
    credentials stop working here, before anything new exists."""
    with transaction.atomic():
        fix_run = AgentFixRun.no_workspace_objects.select_for_update().get(
            id=fix_run_id
        )
        settled = _settled(fix_run)
        if settled is not None:
            return settled
        if fix_run.state == AgentFixRun.State.QUEUED:
            fix_run.state = AgentFixRun.State.RUNNING
            fix_run.started_at = timezone.now()
        fix_run.token_hash = ""
        fix_run.fence_hash = ""
        fix_run.save(
            update_fields=[
                "state",
                "started_at",
                "token_hash",
                "fence_hash",
                "updated_at",
            ]
        )
    attempts = settings.AGENT_FIX_MAX_SANDBOX_ATTEMPTS
    if timezone.now() >= _deadline(fix_run):
        failure = {
            "code": "fix_deadline",
            "message": "the fix ran out of the time it was given",
        }
    elif fix_run.sandbox_attempt >= attempts:
        failure = {
            "code": "fix_engine_died",
            "message": f"the improvement engine stopped {attempts} times before it finished",
        }
    else:
        return None
    close_fix_run(fix_run.id, AgentFixRun.State.FAILED, failure=failure)
    return Observation.ENDED


def _settled(fix_run: AgentFixRun) -> Observation | None:
    """ENDED or STOPPING when the run no longer needs an engine."""
    if fix_run.state == AgentFixRun.State.STOPPING:
        return Observation.STOPPING
    if fix_run.state not in AgentFixRun.ACTIVE_STATES:
        return Observation.ENDED
    return None


def _deadline(fix_run: AgentFixRun) -> datetime:
    request = FixRequest.model_validate(fix_run.request)
    return fix_run.started_at + timedelta(minutes=request.max_minutes)


def _provider() -> SandboxRuntimeProvider:
    provider = get_sandbox_provider()
    if provider.name != "e2b":
        raise HostedHarnessError(
            "fix_sandbox_unavailable",
            "Fix my agent runs in an E2B sandbox; set HOSTED_SANDBOX_PROVIDER=e2b",
            status_code=503,
        )
    return provider


def _launch_spec(
    fix_run: AgentFixRun, models: EngineModels, provider: SandboxRuntimeProvider
) -> SandboxLaunchSpec:
    template = settings.AGENT_FIX_E2B_TEMPLATE_REFERENCE
    if not template:
        raise HostedHarnessError(
            "fix_sandbox_unavailable",
            "AGENT_FIX_E2B_TEMPLATE_REFERENCE is not set",
            status_code=503,
        )
    # The engine reaches only the platform's engine API and the model gateway.
    domains = {
        _hostname_from_url(_public_base_url()),
        _hostname_from_url(models.base_url),
    }
    _validate_resolved_egress_domains(domains, max_domains=provider.max_egress_domains)
    # E2B sizes a sandbox by its template; this only passes the provider's admission.
    cpu_units, memory_mb, disk_gb = E2BSandboxRuntimeProvider.configured_resources()
    return SandboxLaunchSpec(
        cpu_units=cpu_units,
        memory_mb=memory_mb,
        disk_gb=disk_gb,
        ttl_seconds=LEASE_SECONDS,
        os_user=settings.ALK_HOSTED_SANDBOX_OS_USER,
        labels={"futureagi.fix_run": str(fix_run.id)},
        allowed_domains=tuple(sorted(domains)),
        runtime_name=template,
    )


def _public_base_url() -> str:
    base = str(settings.HARNESS_PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        raise HostedHarnessError(
            "public_base_url_missing",
            "HARNESS_PUBLIC_BASE_URL is required for the engine to call back",
            status_code=503,
        )
    return base
