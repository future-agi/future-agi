"""Readings in the sandbox: find the agents in a source, or read one version's agent
into its document and raw contract, with agent-learning-kit's hosted authoring entry
point and no build. The sandbox writes files and exits; the platform reads them."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from simulate.models import (
    AgentDefinition,
    AgentScan,
    HarnessAgentVersionRuntime,
)
from simulate.models.agent_setup import AgentVersionSetup
from simulate.serializers.harness_job import HarnessJobCreateSerializer
from simulate.services.agent_contracts import store_version_contract
from simulate.services.agent_reading import (
    READING_DIR,
    ReadingResultInvalid,
    collect_reading,
    collect_scan,
)
from simulate.services.agent_setup import (
    AgentStoreError,
    store_agent,
    store_setup_failure,
)
from simulate.services.harness_credentials import agent_version_secret_ref
from simulate.services.harness_usage import (
    record_agent_reading_usage,
    require_harness_authoring,
)
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import (
    _CHAT_RUNTIME_PATH,
    _alk_source_overlay,
    _authoring_egress_domains,
    _platform_simulator_material,
    _validate_resolved_egress_domains,
    acquire_source_archive,
)
from simulate.services.hosted_sandbox import (
    E2BSandboxRuntimeProvider,
    SandboxLaunchSpec,
    SandboxRuntimeProvider,
    get_sandbox_provider,
)

logger = structlog.get_logger(__name__)

Status = HarnessAgentVersionRuntime.PreflightStatus
Trigger = HarnessAgentVersionRuntime.Trigger
SourceKind = HarnessAgentVersionRuntime.SourceKind

ENTRYPOINT = (
    "python -m fi.alk.harness.hosted_authoring_entrypoint /work/job.json "
    f"--source /work/source --output {READING_DIR}"
)
READ_ARGS = "--read-agent-only"
# The "find the agents" command is settled on the agent-learning-kit side after the
# reading; this is the one place it changes.
SCAN_ARGS = "--find-agents"
COST_FILE = f"{READING_DIR}/cost.json"
COST_MAX_BYTES = 1_048_576
_OUTPUT_TAIL = 2000
_PREPARE_TIMEOUT = 120
# A crashed reading that left no failure file; the next attempt may succeed.
CRASHED = "agent_reading_crashed"
# The reading finished but not with the files agreed on; retried as well.
INVALID = "agent_reading_invalid"


@dataclass(frozen=True)
class ReadOutcome:
    """What one reading left on the version: its document stored, or why not."""

    status: str
    code: str = ""
    message: str = ""
    retryable: bool = False
    spend: dict[str, Any] | None = None


@dataclass(frozen=True)
class SandboxRun:
    exit_code: int
    output: str
    sandbox_id: str
    sandbox: Any = field(repr=False)


def _provider() -> SandboxRuntimeProvider:
    return get_sandbox_provider()


def _launch_spec(provider: SandboxRuntimeProvider, labels: dict[str, str]):
    simulator_env = _platform_simulator_material()
    domains = set(_authoring_egress_domains(simulator_env, "", provider_profile=False))
    _validate_resolved_egress_domains(domains, max_domains=provider.max_egress_domains)
    if provider.name == "e2b":
        cpu_units, memory_mb, disk_gb = E2BSandboxRuntimeProvider.configured_resources()
    else:
        cpu_units, memory_mb, disk_gb = 2, 4096, 10
    timeout = int(getattr(settings, "ALK_HOSTED_READING_TIMEOUT", 900))
    spec = SandboxLaunchSpec(
        cpu_units=cpu_units,
        memory_mb=memory_mb,
        disk_gb=disk_gb,
        ttl_seconds=max(600, timeout + 300),
        os_user=str(getattr(settings, "ALK_HOSTED_SANDBOX_OS_USER", "svc-control")),
        labels=labels,
        allowed_domains=tuple(sorted(domains)),
    )
    env = {
        name: value
        for name, value in simulator_env.items()
        if name not in {"LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}
    }
    env["PATH"] = _CHAT_RUNTIME_PATH
    return spec, env, timeout


@contextmanager
def _sandbox(source_archive: bytes, job_document: Mapping[str, Any], labels):
    """A sandbox with the source and job document in place, deleted on the way out."""
    provider = _provider()
    spec, env, timeout = _launch_spec(provider, labels)
    sandbox = provider.create(spec, timeout=provider.create_timeout_seconds)
    try:
        sandbox.fs.upload_file(source_archive, "/work/source.tar.gz")
        sandbox.fs.upload_file(
            json.dumps(job_document, sort_keys=True, separators=(",", ":")).encode(),
            "/work/job.json",
        )
        overlay = _alk_source_overlay()
        if overlay is not None:
            sandbox.fs.upload_file(overlay, "/work/alk-src.tar.gz")
            env = {**env, "PYTHONPATH": "/work/alk-src"}
        prepared = sandbox.process.exec(
            (
                "mkdir -p /work/alk-src && tar -xzf /work/alk-src.tar.gz -C /work/alk-src"
                " && rm /work/alk-src.tar.gz && "
                if overlay is not None
                else ""
            )
            + "tar -xzf /work/source.tar.gz -C /work && rm /work/source.tar.gz && "
            "chown -R svc-control:svc-control /work/source && "
            "chmod -R a-w /work/source && chmod 0600 /work/job.json",
            timeout=_PREPARE_TIMEOUT,
        )
        if prepared.exit_code:
            raise HostedHarnessError(
                "reading_source_prepare_failed",
                str(prepared.result or "")[-500:],
                status_code=502,
                retryable=True,
            )
        yield sandbox, env, timeout
    finally:
        try:
            provider.delete(sandbox, timeout=120, wait=True)
        except Exception:  # noqa: BLE001 - the lease deletes a sandbox left behind
            logger.warning("reading_sandbox_not_deleted", sandbox=str(sandbox.id))


def run_reading(
    source_archive: bytes,
    job_document: Mapping[str, Any],
    *,
    args: str,
    labels: dict[str, str],
    collect: Callable[[SandboxRun], Any],
) -> Any:
    """Run one reading command in a fresh sandbox and hand its files to ``collect``
    before the sandbox goes away."""
    with _sandbox(source_archive, job_document, labels) as (sandbox, env, timeout):
        run = sandbox.process.exec(
            f"rm -rf {READING_DIR} && {ENTRYPOINT} {args}", env=env, timeout=timeout
        )
        return collect(
            SandboxRun(
                exit_code=int(run.exit_code or 0),
                output=str(run.result or "")[-_OUTPUT_TAIL:],
                sandbox_id=str(sandbox.id),
                sandbox=sandbox,
            )
        )


def _spend(sandbox) -> dict[str, Any] | None:
    from simulate.services.agent_reading import _json_object

    try:
        return _json_object(sandbox, COST_FILE, COST_MAX_BYTES)
    except ReadingResultInvalid:
        return None


def _crashed(run: SandboxRun) -> dict[str, Any]:
    return {
        "domain": "agent",
        "stage": "understanding_agent",
        "code": CRASHED,
        "message": run.output or f"the reading exited with {run.exit_code}",
        "retryable": True,
        "details": {"exit_code": run.exit_code},
    }


def _invalid(error: ReadingResultInvalid) -> dict[str, Any]:
    return {
        "domain": "agent",
        "stage": "understanding_agent",
        "code": INVALID,
        "message": f"{error.code}: {error.path}",
        "retryable": True,
        "details": {"file": error.path, "reason": error.code},
    }


def _job_defaults() -> dict[str, Any]:
    fields = HarnessJobCreateSerializer().fields
    return {
        name: fields[name].run_validation({})
        for name in ("runtime", "security", "retry")
    }


def _source_document(kind: str, source: Mapping[str, Any], access: Mapping[str, Any]):
    if kind == SourceKind.ARCHIVE:
        return {
            "kind": "archive",
            "archive_artifact_id": source.get("archive_artifact_id"),
        }
    document = {
        "kind": "github",
        "repository": source.get("repository"),
        "visibility": access.get("visibility") or "public",
    }
    for name in ("ref", "commit_sha", "subfolder"):
        if source.get(name):
            document[name] = source[name]
    return document


def reading_job_document(
    definition: AgentDefinition, runtime: HarnessAgentVersionRuntime
) -> dict[str, Any]:
    """The job a reading runs on: the version's source and settings, credential
    names only, the same shape a build's job carries."""
    from simulate.services.harness_agent_versions import version_credential_names

    payload = runtime.agent_payload or {}
    names = version_credential_names(runtime)
    agent = {
        "connector": str(payload.get("connector") or "auto"),
        "config": dict(payload.get("config") or {}),
        "secret_refs": {name: agent_version_secret_ref(name) for name in names},
    }
    for name in ("mode", "call_direction"):
        if payload.get(name):
            agent[name] = payload[name]
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": _source_document(
            runtime.source_kind,
            runtime.source or {},
            payload.get("source_access") or {},
        ),
        "agent": agent,
        "scenario_count": 1,
        "artifacts": {"level": "standard"},
        **_job_defaults(),
        "metadata": {
            "name": definition.agent_name,
            "authoring_key": definition.agent_name,
            "generic_harness_v1": True,
            "environment_value_names": sorted(name.upper() for name in names),
            "agent_definition_id": str(definition.id),
            "agent_version_id": str(runtime.agent_version_id),
            "reading": "agent",
        },
    }


def scan_job_document(scan: AgentScan) -> dict[str, Any]:
    source = scan.source or {}
    kind = SourceKind.ARCHIVE if source.get("kind") == "archive" else SourceKind.GITHUB
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": _source_document(kind, source, source),
        "agent": {"connector": "auto", "config": {}, "secret_refs": {}},
        "scenario_count": 1,
        "artifacts": {"level": "standard"},
        **_job_defaults(),
        "metadata": {
            "name": str(source.get("repository") or "upload"),
            "generic_harness_v1": True,
            "environment_value_names": [],
            "reading": "scan",
        },
    }


def version_source(runtime: HarnessAgentVersionRuntime) -> dict[str, Any]:
    """The source as the acquirer reads it: identity plus how it is reached."""
    access = (runtime.agent_payload or {}).get("source_access") or {}
    return {
        "kind": str(runtime.source_kind),
        **{name: value for name, value in (runtime.source or {}).items() if value},
        **{
            name: access[name]
            for name in ("visibility", "installation_id")
            if access.get(name)
        },
    }


def _channel(document: Mapping[str, Any]) -> str:
    Types = AgentDefinition.AgentTypeChoices
    return Types.VOICE if document.get("type") == "voice" else Types.TEXT


def _first_reading(runtime: HarnessAgentVersionRuntime) -> bool:
    return runtime.trigger == Trigger.CONNECT or runtime.parent_id is None


def read_version(version_id: UUID | str) -> ReadOutcome:
    """Read one version's agent in the sandbox and store what it found. A refusal
    or a channel change fails the version for good; anything the next attempt may
    outlive is raised for the caller to retry."""
    runtime = (
        HarnessAgentVersionRuntime.no_workspace_objects.select_related(
            "agent_definition", "organization"
        )
        .defer("credential_blob")
        .filter(agent_version_id=version_id)
        .first()
    )
    if runtime is None or runtime.preflight_status != Status.PENDING:
        return ReadOutcome("skipped", code="not_pending")
    definition = runtime.agent_definition
    try:
        require_harness_authoring(str(runtime.organization_id))
    except HostedHarnessError as exc:
        return ReadOutcome("failed", code=exc.code, message=exc.message)
    source_archive, _commit = acquire_source_archive(
        runtime.organization_id, version_source(runtime), label=str(version_id)
    )
    labels = {
        "futureagi.agent_version": str(version_id),
        "futureagi.reading": "agent",
    }

    def collect(
        run: SandboxRun,
    ) -> tuple[dict | None, dict | None, dict | None, dict | None]:
        try:
            reading = collect_reading(run.sandbox)
        except ReadingResultInvalid as error:
            failure = _crashed(run) if run.exit_code else _invalid(error)
            return failure, None, None, _spend(run.sandbox)
        if reading.failure is not None:
            return reading.failure, None, None, _spend(run.sandbox)
        if run.exit_code:
            return _crashed(run), None, None, _spend(run.sandbox)
        return None, reading.agent, reading.contract, _spend(run.sandbox)

    failure, agent, contract, spend = run_reading(
        source_archive,
        reading_job_document(definition, runtime),
        args=READ_ARGS,
        labels=labels,
        collect=collect,
    )
    _bill(runtime, spend)
    if failure is not None:
        return ReadOutcome(
            "failed",
            code=str(failure["code"]),
            message=str(failure["message"])[:2000],
            retryable=bool(failure["retryable"]),
            spend=spend,
        )
    return _store_reading(runtime, definition, agent, contract, spend)


def _bill(runtime: HarnessAgentVersionRuntime, spend: dict[str, Any] | None) -> None:
    if not spend:
        return
    try:
        record_agent_reading_usage(
            organization_id=runtime.organization_id,
            workspace_id=runtime.workspace_id,
            subject_id=runtime.agent_version_id,
            spend=spend,
        )
    except Exception:  # noqa: BLE001 - a pricing gap never loses the reading
        logger.exception(
            "agent_reading_not_billed", agent_version_id=str(runtime.agent_version_id)
        )


def _unwrap(agent: Mapping[str, Any]) -> tuple[dict[str, Any], str] | None:
    """The document and SDK version from ``agent.json``, in the callback's shape."""
    document, sdk_version = agent.get("agent"), agent.get("sdk_version")
    if isinstance(document, dict) and isinstance(sdk_version, str) and sdk_version:
        return document, sdk_version
    return None


def _store_reading(
    runtime: HarnessAgentVersionRuntime,
    definition: AgentDefinition,
    agent: dict[str, Any],
    contract: dict[str, Any],
    spend: dict[str, Any] | None,
) -> ReadOutcome:
    unwrapped = _unwrap(agent)
    if unwrapped is None:
        return ReadOutcome(
            "failed",
            code=INVALID,
            message="agent.json is not {sdk_version, agent}",
            retryable=True,
            spend=spend,
        )
    document, sdk_version = unwrapped
    channel = _channel(document)
    with transaction.atomic():
        locked = (
            HarnessAgentVersionRuntime.no_workspace_objects.select_for_update()
            .defer("credential_blob")
            .get(id=runtime.id)
        )
        if locked.preflight_status != Status.PENDING:
            return ReadOutcome("skipped", code="not_pending", spend=spend)
        current = AgentDefinition.no_workspace_objects.get(id=definition.id)
        if _first_reading(locked) or not _agent_read_before(definition.id):
            if current.agent_type != channel:
                AgentDefinition.no_workspace_objects.filter(id=definition.id).update(
                    agent_type=channel, updated_at=timezone.now()
                )
        elif current.agent_type != channel:
            message = (
                "This version is a "
                f"{'voice' if channel == AgentDefinition.AgentTypeChoices.VOICE else 'chat'}"
                " agent; the agent was connected as "
                f"{'voice' if current.agent_type == AgentDefinition.AgentTypeChoices.VOICE else 'chat'}"
            )
            store_setup_failure(
                agent_version_id=locked.agent_version_id,
                code="channel_changed",
                message=message,
            )
            return ReadOutcome(
                "failed", code="channel_changed", message=message, spend=spend
            )
        try:
            store_agent(
                agent_version_id=locked.agent_version_id,
                agent=document,
                sdk_version=sdk_version,
                origin=AgentVersionSetup.Origin.READING,
            )
        except AgentStoreError as exc:
            return ReadOutcome(
                "failed",
                code=exc.code,
                message="the reading's document could not be stored",
                retryable=exc.code not in ("agent_invalid", "agent_too_large"),
                spend=spend,
            )
    store_version_contract(
        organization_id=runtime.organization_id,
        agent_version_id=runtime.agent_version_id,
        contract=contract,
    )
    logger.info(
        "agent_reading_stored",
        agent_version_id=str(runtime.agent_version_id),
        agent_definition_id=str(definition.id),
        channel=channel,
        tools=len(document.get("tools") or []),
    )
    return ReadOutcome("stored", spend=spend)


def _agent_read_before(definition_id: UUID) -> bool:
    return AgentVersionSetup.no_workspace_objects.filter(
        agent_version__agent_definition_id=definition_id,
        status=AgentVersionSetup.Status.STORED,
    ).exists()


def scan_source(scan_id: UUID | str) -> None:
    """Find the agents in a scan's source and keep the list on the scan. Anything the
    next attempt may outlive is raised for the caller to retry."""
    scan = AgentScan.no_workspace_objects.select_related("organization").get(id=scan_id)
    if scan.status not in (AgentScan.Status.PENDING, AgentScan.Status.RUNNING):
        return
    AgentScan.no_workspace_objects.filter(id=scan.id).update(
        status=AgentScan.Status.RUNNING, started_at=timezone.now()
    )
    try:
        require_harness_authoring(str(scan.organization_id))
        source = dict(scan.source or {})
        if source.get("kind") == "github" and not source.get("commit_sha"):
            from simulate.services.agent_versions import branch_head_of

            source["commit_sha"] = branch_head_of(
                scan.organization_id, scan.workspace_id, source
            )
        source_archive, commit = acquire_source_archive(
            scan.organization_id, source, label=f"scan-{scan.id}"
        )
    except HostedHarnessError as exc:
        if exc.retryable:
            raise
        _finish_scan(scan, AgentScan.Status.FAILED, failure=_platform_failure(exc))
        return
    if commit:
        source["commit_sha"] = commit
        AgentScan.no_workspace_objects.filter(id=scan.id).update(source=source)

    def collect(run: SandboxRun):
        try:
            found = collect_scan(run.sandbox)
        except ReadingResultInvalid as error:
            return (
                (_crashed(run) if run.exit_code else _invalid(error)),
                None,
                _spend(run.sandbox),
            )
        if found.failure is not None:
            return found.failure, None, _spend(run.sandbox)
        if run.exit_code:
            return _crashed(run), None, _spend(run.sandbox)
        return None, found.scan, _spend(run.sandbox)

    failure, report, spend = run_reading(
        source_archive,
        scan_job_document(scan),
        args=SCAN_ARGS,
        labels={"futureagi.agent_scan": str(scan.id), "futureagi.reading": "scan"},
        collect=collect,
    )
    if spend:
        try:
            record_agent_reading_usage(
                organization_id=scan.organization_id,
                workspace_id=scan.workspace_id,
                subject_id=scan.id,
                spend=spend,
            )
        except Exception:  # noqa: BLE001 - a pricing gap never loses the scan
            logger.exception("agent_scan_not_billed", scan_id=str(scan.id))
    if failure is not None:
        _finish_scan(scan, AgentScan.Status.FAILED, failure=failure)
        return
    status = {
        "found": AgentScan.Status.FOUND,
        "none": AgentScan.Status.NONE,
        "error": AgentScan.Status.ERROR,
    }[str(report["status"])]
    _finish_scan(scan, status, report=report)


def _platform_failure(exc: HostedHarnessError) -> dict[str, Any]:
    return {
        "domain": "environment",
        "stage": "acquiring_source",
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
        "details": dict(exc.details or {}),
    }


def _finish_scan(scan: AgentScan, status: str, *, report=None, failure=None) -> None:
    AgentScan.no_workspace_objects.filter(id=scan.id).update(
        status=status,
        report=report,
        failure=failure,
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )
    logger.info(
        "agent_scan_finished",
        scan_id=str(scan.id),
        status=status,
        agents=len((report or {}).get("agents") or []),
        code=(failure or {}).get("code"),
    )
