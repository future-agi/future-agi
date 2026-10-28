"""The sandbox the improvement engine runs in during a fix: launch it, watch it, release it.

AgentFixWorkflow calls each function as one step; each decides what the platform saw."""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import structlog
from django.conf import settings
from django.db import InterfaceError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from agentcc.services.gateway_client import GatewayClientError
from simulate.models import AgentFixRun
from simulate.pydantic_schemas.agent_fix import EngineCapability, EngineModels
from simulate.selectors.agent_fix import fix_deadline
from simulate.services import agent_fix_keys
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
    SandboxProviderConfigurationError,
    SandboxProviderError,
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
# What a later look can outlive: the sandbox API, the network or the database briefly
# unavailable. SandboxProviderConfigurationError is a SandboxProviderError but is not.
_TRANSIENT_WATCH_ERRORS = (
    SandboxProviderError,
    httpx.TransportError,
    OperationalError,
    InterfaceError,
)


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
    gateway_url = _model_gateway_url()
    fix_run = AgentFixRun.no_workspace_objects.get(id=fix_run_id)
    spec = _launch_spec(fix_run, gateway_url, provider)
    try:
        model_key = agent_fix_keys.mint(fix_run, _credentials_expiry(fix_run))
    except GatewayClientError:
        logger.exception("agent_fix_model_key_failed", fix_run=str(fix_run.id))
        close_fix_run(
            fix_run.id,
            AgentFixRun.State.FAILED,
            failure={
                "code": "fix_model_key_unavailable",
                "message": "AgentCC could not issue the improvement engine a model key",
            },
        )
        return AgentFixLaunch(observation=Observation.ENDED)
    sandbox = provider.create(spec, timeout=provider.create_timeout_seconds)
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
        models=EngineModels(base_url=gateway_url, api_key=model_key),
    )
    command_id = _start_engine(sandbox, capability)
    # Only a started engine uses up one of the run's attempts; a launch that failed
    # before this point is the workflow's to retry.
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        sandbox_attempt=F("sandbox_attempt") + 1, updated_at=timezone.now()
    )
    logger.info("agent_fix_engine_started", fix_run=str(fix_run.id), sandbox=sandbox.id)
    return AgentFixLaunch(observation=Observation.RUNNING, command_id=command_id)


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
        fix_run.token_expires_at = _credentials_expiry(fix_run)
        # A new engine has the report timeout to send its first report.
        fix_run.heartbeat_at = timezone.now()
        fix_run.save(
            update_fields=[
                "token_hash",
                "fence_hash",
                "token_expires_at",
                "heartbeat_at",
                "updated_at",
            ]
        )
    return EngineCredentials(
        token=token, fence=fence, expires_at=fix_run.token_expires_at
    )


def observe(fix_run_id: str, command_id: str) -> Observation:
    """One look at a running fix. Raises only what a later look can outlive; any other
    error ends the run as failed, since looking again would hit it again."""
    try:
        return _look(fix_run_id, command_id)
    except Exception as exc:
        if isinstance(exc, _TRANSIENT_WATCH_ERRORS) and not isinstance(
            exc, SandboxProviderConfigurationError
        ):
            raise
        logger.exception("agent_fix_watch_failed", fix_run=str(fix_run_id))
        close_fix_run(
            fix_run_id,
            AgentFixRun.State.FAILED,
            failure={
                "code": "fix_watch_failed",
                "message": "the platform could not watch the improvement engine",
            },
        )
        return Observation.ENDED


def _look(fix_run_id: str, command_id: str) -> Observation:
    """The run's state, deadline and last report are read before the sandbox, so a
    sandbox API outage cannot hide a late run or a dead engine. Renews the sandbox's
    lease while the engine runs."""
    fix_run = AgentFixRun.no_workspace_objects.get(id=fix_run_id)
    settled = _settled(fix_run)
    if settled is not None:
        return settled
    now = timezone.now()
    if now >= fix_deadline(fix_run):
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
    """Delete the fix's sandbox and revoke its model key, if it has them. Safe to repeat."""
    sandbox_ref = AgentFixRun.no_workspace_objects.values_list(
        "sandbox_ref", flat=True
    ).get(id=fix_run_id)
    if sandbox_ref:
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
    agent_fix_keys.revoke(fix_run_id)


def _start_engine(sandbox, capability: EngineCapability) -> str:
    """Write the capability file, readable by the engine's user alone, and start the
    engine on it. Returns the engine's command id."""
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
    return str(command.cmd_id)


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
    if timezone.now() >= fix_deadline(fix_run):
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


def _credentials_expiry(fix_run: AgentFixRun) -> datetime:
    """The engine's token and model key outlive the deadline by a grace period, so a
    final report sent near the deadline still lands. The workflow ends the run itself
    at the deadline."""
    grace = timedelta(seconds=settings.AGENT_FIX_TOKEN_GRACE_SECONDS)
    return fix_deadline(fix_run) + grace


def _model_gateway_url() -> str:
    """AgentCC's OpenAI-compatible endpoint, as a sandbox reaches it."""
    # Only the URL: the platform's own key never enters a sandbox.
    url, _platform_key = platform_model_gateway()
    return f"{url.removesuffix('/v1')}/v1"


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
    fix_run: AgentFixRun, gateway_url: str, provider: SandboxRuntimeProvider
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
        _hostname_from_url(gateway_url),
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
