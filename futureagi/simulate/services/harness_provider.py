"""Public execution-backend switch for the hosted ALK harness control plane.

``HARNESS_PROVIDER=hosted`` persists jobs and starts the platform-managed gateway.
``HARNESS_PROVIDER=sandbox`` proxies to an out-of-process ALK sandbox server.
The hosted gateway independently selects Daytona or E2B through
``HOSTED_SANDBOX_PROVIDER``; the public v1.6 request contract does not change.
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from simulate.models import (
    HostedHarnessConversation,
    HostedHarnessJob,
    HostedHarnessReceipt,
    HostedHarnessScenario,
    HostedHarnessStageOutput,
    TestExecution,
)
from simulate.services.hosted_harness_conversation import serialize_conversation
from tfc.utils.api_errors import build_error_envelope

logger = logging.getLogger(__name__)

_SOURCE_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".rar", ".7z")
_SOURCE_ARCHIVE_DETAIL = (
    "upload the expanded project folder; archives are not supported"
)


def _rejected_archive_upload(files) -> Response | None:
    """Refuse a lone archive: nothing unpacks it, so it would ship as an opaque blob."""
    if len(files) == 1 and str(files[0].name).lower().endswith(
        _SOURCE_ARCHIVE_SUFFIXES
    ):
        return Response(
            {"detail": _SOURCE_ARCHIVE_DETAIL},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


_E164_PHONE = re.compile(r"^\+[1-9]\d{6,14}$")


def _validate_phone_connectivity(payload) -> None:
    """A phone-only target uses platform telephony, never customer SIP credentials."""
    agent = payload["agent"]
    if agent["connector"] != "phone" and not (
        agent["connector"] in {"vapi", "retell"}
        and agent.get("mode") == "connect_only"
        and (agent.get("config") or {}).get("phone_number")
    ):
        return
    from simulate.services.phone_telephony import platform_phone_telephony

    telephony = platform_phone_telephony()
    missing = [name for name, value in telephony.items() if not value]
    if missing:
        from simulate.services.hosted_harness import HostedHarnessError

        raise HostedHarnessError(
            "phone_dialer_not_configured",
            "Platform outbound calling is not configured: " + ", ".join(missing),
            status_code=503,
        )
    if not _E164_PHONE.fullmatch(telephony["SIP_OUTBOUND_FROM_NUMBER"]):
        from simulate.services.hosted_harness import HostedHarnessError

        raise HostedHarnessError(
            "phone_dialer_caller_id_invalid",
            "Platform SIP_OUTBOUND_FROM_NUMBER must be an E.164 caller ID",
            status_code=503,
        )


def get_harness_provider():
    """Return the configured public harness backend."""
    name = str(getattr(settings, "HARNESS_PROVIDER", "hosted") or "hosted").lower()
    if name == "sandbox":
        return SandboxHarnessProvider()
    return HostedHarnessProvider()


def _usage_limit_response(exc: Exception) -> Response | None:
    """Translate EE metering refusals into the standard structured 402 response."""
    try:
        from ee.usage.exceptions import UsageLimitExceeded
    except ImportError:
        return None
    if not isinstance(exc, UsageLimitExceeded):
        return None
    from tfc.utils.general_methods import GeneralMethods

    return GeneralMethods().usage_limit_response(exc.check_result)


def request_organization(request):
    user = getattr(request, "user", None)
    return getattr(request, "organization", None) or getattr(user, "organization", None)


def request_workspace(request):
    workspace = getattr(request, "workspace", None)
    if workspace is not None:
        return workspace

    # Session-authenticated UI requests do not always pass through the API-key
    # authentication hook that materializes ``request.workspace``. Resolve the
    # explicit workspace header here as a safe fallback, while preserving the
    # same organization and membership checks as the authentication layer.
    workspace_id = request.headers.get("X-Workspace-Id")
    if not workspace_id:
        return None

    from rest_framework.exceptions import PermissionDenied

    from accounts.models.workspace import Workspace

    organization = request_organization(request)
    workspace = (
        Workspace.no_workspace_objects.select_related("organization")
        .filter(id=workspace_id, organization=organization, is_active=True)
        .first()
    )
    if workspace is None or not request.user.can_access_workspace(workspace):
        raise PermissionDenied("Access denied to this workspace")
    return workspace


def scope_jobs(queryset, request):
    """Apply the exact tenant scope captured by the submitting request."""
    workspace = request_workspace(request)
    if workspace is None:
        return queryset.filter(workspace__isnull=True)
    return queryset.filter(workspace=workspace)


def _scoped_job(request, pk):
    """One job, or None, resolved under the caller's organization and workspace."""
    organization = request_organization(request)
    if organization is None:
        return None
    return (
        scope_jobs(
            HostedHarnessJob.no_workspace_objects.filter(organization=organization),
            request,
        )
        .filter(id=pk)
        .first()
    )


def _validate_secret_refs_hosted(secret_refs: dict) -> None:
    """Reject secret references that the managed gateway cannot materialize.

    Platform-vault target-provider references work for every managed sandbox provider. Other
    managers return a typed error rather than failing later inside a sandbox.
    """
    from simulate.services.hosted_harness import HostedHarnessError

    for alias, ref in secret_refs.items():
        manager = ref.get("manager", "")
        if manager != "platform-vault":
            raise HostedHarnessError(
                "secret_manager_unsupported",
                f"secret manager {manager!r} for alias {alias!r} is not supported "
                "by the hosted provider; use platform-vault target_provider refs",
                status_code=422,
            )


def _validate_known_hosted_egress(payload: dict[str, Any], callback_url: str) -> None:
    """Reject an egress set unsupported by the configured sandbox provider."""
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.services.hosted_harness_gateway import (
        _authoring_ttl_seconds,
        _execution_ttl_seconds,
        _hostname_from_url,
        _known_simulator_egress_inputs,
        _resolved_egress_domains,
        _validate_egress_domains,
        _validate_resolved_egress_domains,
    )
    from simulate.services.hosted_sandbox import (
        SandboxProviderConfigurationError,
        sandbox_egress_domain_limit,
        sandbox_provider_name,
        sandbox_runtime_policy,
        validate_sandbox_requirements,
    )

    security = payload.get("security") or {}
    customer_domains = security.get("allowed_egress_domains") or []
    agent = payload.get("agent") or {}
    secret_refs = agent.get("secret_refs") or {}
    # Admission has references, not decrypted values. An alias is enough to
    # account for static provider dependencies; launch resolves value-derived
    # connector endpoints authoritatively.
    target_inputs = {str(alias): "" for alias in secret_refs}
    _validate_egress_domains(customer_domains)
    domains = _resolved_egress_domains(
        payload,
        target_inputs,
        _known_simulator_egress_inputs(),
        _hostname_from_url(callback_url),
    )
    _validate_resolved_egress_domains(
        domains, max_domains=sandbox_egress_domain_limit()
    )
    runtime = payload["runtime"]
    policy = sandbox_runtime_policy()
    disk_gb = policy.fixed_resources[2] if policy.fixed_resources else 10
    provider_name = sandbox_provider_name()
    max_ttl_seconds = max(
        _authoring_ttl_seconds(provider_name),
        _execution_ttl_seconds(runtime, provider_name),
    )
    try:
        validate_sandbox_requirements(
            runtime["cpu_units"],
            runtime["memory_mb"],
            disk_gb,
            max_ttl_seconds,
        )
    except SandboxProviderConfigurationError as exc:
        raise HostedHarnessError(
            "sandbox_requirements_unsupported",
            str(exc),
            status_code=422,
        ) from exc


def serialize_job(job: HostedHarnessJob) -> dict[str, Any]:
    from simulate.services.harness_usage import harness_consumption

    attempt = job.attempts.order_by("-attempt_number").first()
    events: list[dict[str, Any]] = []
    if attempt:
        recent = list(attempt.events.filter(accepted=True).order_by("-sequence")[:100])
        events = [
            {
                "event_id": event.event_id,
                "sequence": event.sequence,
                "stage": event.stage,
                "type": event.event_type,
                "payload": event.payload,
                "emitted_at": event.emitted_at.isoformat(),
            }
            for event in reversed(recent)
        ]
    # Stage outputs — persisted authoritative snapshots from verified bundle.
    stage_outputs_qs = HostedHarnessStageOutput.no_workspace_objects.filter(
        job=job
    ).order_by("created_at")[:20]
    stage_outputs = [
        {
            "id": str(so.id),
            "title": so.title,
            "summary": so.summary,
            "kind": so.kind,
            "data": so.data,
        }
        for so in stage_outputs_qs
    ]
    if not stage_outputs:
        stage_outputs = list(job.stage_outputs or [])
    # Scenarios — bounded to job.scenario_count.
    scenario_regs = list(
        HostedHarnessScenario.no_workspace_objects.filter(job=job)
        .select_related("scenario")
        .order_by("created_at")[: job.scenario_count]
    )
    scenarios = [
        {
            "scenario_key": reg.scenario_key,
            "scenario_id": str(reg.scenario_id),
            "name": getattr(reg.scenario, "name", "") if reg.scenario else "",
            "instruction": (getattr(reg.scenario, "prompt", None) or "")
            if reg.scenario
            else None,
            "use_case": (getattr(reg.scenario, "use_case", None) or "")
            if reg.scenario
            else None,
            "call_execution_id": str(reg.call_execution_id)
            if reg.call_execution_id
            else None,
            "status": _scenario_status(reg),
        }
        for reg in scenario_regs
    ]
    # Receipts — bounded.
    receipt_qs = HostedHarnessReceipt.no_workspace_objects.filter(job=job).order_by(
        "created_at"
    )[: job.scenario_count]
    receipt_rows = list(receipt_qs)
    receipts = [r.body for r in receipt_rows]
    cycle_start = (job.payload.get("metadata") or {}).get("attempt_cycle_start")
    if type(cycle_start) is not int or cycle_start < 1:
        cycle_start = 1
    current_attempt = (
        attempt if attempt and attempt.attempt_number >= cycle_start else None
    )
    registered_keys = {reg.scenario_key for reg in scenario_regs}
    finished = {
        row.body.get("scenario_key")
        for row in receipt_rows
        if current_attempt
        and row.attempt_number == current_attempt.attempt_number
        and isinstance(row.body, dict)
        and isinstance(row.body.get("scenario_key"), str)
        and row.body["scenario_key"] in registered_keys
    }
    started = set()
    if current_attempt:
        started = set(
            current_attempt.events.filter(
                accepted=True,
                event_type="scenario_started",
                payload__scenario_key__in=registered_keys,
            )
            .values_list("payload__scenario_key", flat=True)
            .distinct()
        ) - {None}
    terminal = job.state in {
        HostedHarnessJob.State.COMPLETED,
        HostedHarnessJob.State.FAILED,
        HostedHarnessJob.State.CANCELED,
    }
    active = 0 if terminal else len(started - finished)
    queued = 0 if terminal else max(0, job.scenario_count - len(finished) - active)
    platform = {
        "run_test_id": str(job.run_test_id) if job.run_test_id else None,
        "test_execution_id": str(job.test_execution_id)
        if job.test_execution_id
        else None,
        "url": (
            f"/dashboard/simulate/test/{job.run_test_id}/{job.test_execution_id}/call-details"
            if job.run_test_id and job.test_execution_id
            else None
        ),
    }
    runtime = {}
    if attempt:
        if attempt.provider_ref:
            runtime["sandbox_id"] = attempt.provider_ref
        if attempt.diagnostics_object_key or attempt.diagnostics_error:
            diagnostics = {
                "size": attempt.diagnostics_size or 0,
                "final": bool(attempt.diagnostics_final),
                "error": attempt.diagnostics_error or "",
            }
            if attempt.diagnostics_object_key:
                diagnostics["object_key"] = attempt.diagnostics_object_key
            if attempt.diagnostics_sha256:
                diagnostics["sha256"] = attempt.diagnostics_sha256
            if attempt.diagnostics_captured_at:
                diagnostics["captured_at"] = attempt.diagnostics_captured_at.isoformat()
            runtime["diagnostics"] = diagnostics
    # Surface the resolved transport connector so the environments list can show
    # the agent Type (voice/chat). ``resolve_authored_connector`` pins a concrete
    # connector (e.g. "livekit") on the payload during authoring; before that it
    # is "auto" and the type genuinely is not known yet.
    _agent_cfg = job.payload.get("agent") or {}
    _connector = str(_agent_cfg.get("connector") or "").strip().lower()
    detected_connectors = [_connector] if _connector and _connector != "auto" else []
    # Parallelism surfacing (C4 §6). ``requested`` is the immutable stored intent;
    # ``effective`` and ``degrade_reasons`` come from the LATEST attempt's
    # attempt-level projection (updated at ingestion), never the 100-event window
    # a long run evicts. A new attempt starts cleared, so this reflects the
    # current attempt only. With no degrade event, effective == requested.
    _runtime = job.payload.get("runtime") or {}
    _requested_parallelism = _runtime.get("parallelism") or 1
    _clamp = (job.payload.get("metadata") or {}).get("parallelism_clamped") or {}
    if not isinstance(_clamp, dict):
        _clamp = {}
    _admitted = _clamp.get("admitted", 1 if _clamp else _requested_parallelism)
    if type(_admitted) is not int or _admitted < 1:
        _admitted = 1
    _admitted = min(_admitted, _requested_parallelism)
    _effective = attempt.effective_parallelism if attempt else None
    _degrade_reasons = list(attempt.degrade_reasons or []) if attempt else []
    conversation = HostedHarnessConversation.no_workspace_objects.filter(
        job=job
    ).first()
    return {
        "job": {
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "source": job.payload["source"],
            "metadata": job.payload.get("metadata", {}),
            "runtime": {
                "parallelism": _requested_parallelism,
                "cpu_units": _runtime.get("cpu_units"),
            },
            "run_test_id": str(job.run_test_id) if job.run_test_id else None,
            "test_execution_id": str(job.test_execution_id)
            if job.test_execution_id
            else None,
        },
        "parallelism": {
            "requested": _requested_parallelism,
            "admitted": _admitted,
            "effective": min(_effective, _admitted)
            if _effective is not None
            else _admitted,
            "degrade_reasons": _degrade_reasons,
        },
        "status": {
            "state": job.state,
            "stage": job.current_stage,
            "updated_at": job.updated_at.isoformat(),
            "attempt": attempt.attempt_number if attempt else 0,
            "completed_scenarios": job.completed_count,
            "failed_scenarios": job.failed_count,
            "active_scenarios": active,
            "queued_scenarios": queued,
            "total_scenarios": job.scenario_count,
            "deadline_at": job.deadline_at.isoformat(),
            "cancel_requested_at": (
                job.cancel_requested_at.isoformat() if job.cancel_requested_at else None
            ),
            "failure": job.failure,
        },
        "events": events,
        "stage_outputs": stage_outputs,
        "scenarios": scenarios,
        "receipts": receipts,
        "runtime": runtime,
        "consumption": harness_consumption(job),
        "usage_limit": (job.payload.get("metadata") or {}).get("usage_limit"),
        "adjustments": list(
            (job.payload.get("metadata") or {}).get("adjustments") or []
        ),
        "platform": platform,
        "credentials": {"detected_connectors": detected_connectors},
        "conversation": (
            serialize_conversation(conversation) if conversation is not None else None
        ),
    }


def _scenario_status(reg: HostedHarnessScenario) -> str | None:
    """Derive a human-readable status for a scenario registration."""
    if not reg.call_execution_id:
        return "registered"
    receipt = (
        HostedHarnessReceipt.no_workspace_objects.filter(
            job_id=reg.job_id, scenario_id=reg.id
        )
        .values_list("status", flat=True)
        .first()
    )
    return receipt or "running"


def _connector_credential_readiness(
    payload,
    detected_connectors=(),
    scanned_files=0,
    required_credential_files=(),
):
    """Readiness report the create form renders: which target aliases are still missing.

    Same rule as the authoring gate (``missing_provider_credentials``), expressed as
    requirements so preflight can name them instead of refusing before the user knows. For
    ``auto`` only the families the source scan detected are required; the rest stay
    ``optional`` so a chat agent, or a repository the scan could not classify, is not shown a
    wall of credentials it may never need.
    """
    from simulate.serializers.harness_job import (
        CONNECTOR_ALIASES,
        complete_provider_families,
        missing_provider_credentials,
        present_provider_aliases,
    )

    agent = payload["agent"]
    connector = agent["connector"]
    remote = payload["source"]["kind"] == "remote"
    detected = [name for name in detected_connectors if name in CONNECTOR_ALIASES]
    missing = [] if remote else missing_provider_credentials(agent, detected)
    present = present_provider_aliases(agent)
    required_files = set() if remote else set(required_credential_files)
    missing.extend(sorted(required_files - present))
    if connector == "auto":
        families = dict(CONNECTOR_ALIASES)
        # A complete family of any kind satisfies the run (see missing_provider_credentials),
        # so nothing else is required once one is present.
        required = (
            set() if remote or complete_provider_families(agent) else set(detected)
        )
    else:
        # ``get`` -- a phone target has no credential family, so it asks for nothing.
        families = {connector: CONNECTOR_ALIASES.get(connector, ())}
        required = set() if remote else {connector}

    def _status(name, alias):
        if alias in present:
            return "configured"
        return "missing" if name in required else "optional"

    requirements = [
        {
            "environment_name": alias,
            "purpose": "target_provider",
            "required": name in required,
            "status": _status(name, alias),
        }
        for name, aliases in families.items()
        for alias in aliases
    ]
    requirements.extend(
        {
            "id": f"credential-file:{alias}",
            "environment_name": alias,
            "provider": "Google Vertex AI",
            "purpose": "Agent service-account credential file",
            "kind": "file",
            "required": True,
            "status": "configured" if alias in present else "missing",
        }
        for alias in sorted(required_files)
    )
    choices = []
    if connector == "auto" and len(required) > 1:
        choices.append(
            {
                "id": "target_provider",
                "purpose": "Target provider credentials",
                "satisfied": not missing,
                "options": [
                    list(aliases)
                    for name, aliases in CONNECTOR_ALIASES.items()
                    if name in required
                ],
            }
        )
    return {
        "missing": missing,
        "report": {
            "scanned_files": scanned_files,
            "detected_connectors": detected if connector == "auto" else [connector],
            "requirements": requirements,
            "credential_choices": choices,
        },
    }


def _preflight_source_connectors(request, payload):
    """Scan the submitted source the same way authoring will, so preflight asks for the
    credential family the agent actually uses instead of every family."""
    from simulate.services.hosted_harness_gateway import (
        HostedSourceAcquirer,
        detect_source_credentials,
    )

    # Remote/provider targets have no source tree to acquire. Provider metadata
    # is checked through its authenticated target lookup below, not by invoking
    # the repository source scanner with a synthetic source.
    if payload["source"]["kind"] in {"remote", "provider"}:
        return [], [], 0
    probe = HostedHarnessJob(
        organization=request_organization(request), payload=payload
    )
    archive, _commit = HostedSourceAcquirer().acquire(probe)
    detected, required_files, scanned = detect_source_credentials(archive)
    if payload["agent"]["connector"] != "auto":
        detected = []
    return detected, required_files, scanned


def _validate_required_credential_files(request, payload) -> None:
    """Refuse a launch whose source explicitly requires an absent credential file."""
    from simulate.services.hosted_harness import HostedHarnessError

    analysis = _preflight_source_connectors(request, payload)
    required_files = analysis[1] if len(analysis) == 3 else []
    present = {
        str(alias).upper() for alias in (payload["agent"].get("secret_refs") or {})
    }
    missing = sorted(set(required_files) - present)
    if missing:
        raise HostedHarnessError(
            "credential_file_required",
            (
                "agent source requires a Google Vertex credential file; upload "
                "GOOGLE_APPLICATION_CREDENTIALS JSON before starting the run"
            ),
            status_code=422,
        )


def _preflight_credential_probe(payload) -> list[dict[str, Any]]:
    """Exercise every credential the form submitted against its provider, live.

    Values arrive only through the write-only ``credential_values`` preflight field; a
    LiveKit URL entered as public config counts toward that family. Nothing is stored.
    """
    from simulate.services.harness_credential_probes import (
        probe_all,
        probe_provider_target,
    )

    if payload["source"]["kind"] == "remote":
        return []
    values = {
        str(alias).upper(): str(value)
        for alias, value in (payload.get("credential_values") or {}).items()
    }
    config = payload["agent"].get("config") or {}
    livekit_url = config.get("livekit_url") or config.get("LIVEKIT_URL")
    if livekit_url and not values.get("LIVEKIT_URL"):
        values["LIVEKIT_URL"] = str(livekit_url)
    results = list(probe_all(values))
    agent = payload["agent"]
    connector = str(agent.get("connector") or "").strip().lower()
    mode = str(agent.get("mode") or "").strip().lower()
    if connector in {"vapi", "retell", "retell_chat"} and mode in {
        "connect_only",
        "provider_import",
    }:
        target_field = "assistant_id" if connector == "vapi" else "agent_id"
        target = probe_provider_target(
            connector,
            (agent.get("config") or {}).get(target_field),
            values,
        )
        if target is not None:
            results.append(target)
    return [result.as_dict() for result in results]


_SOURCE_FIX_HINTS = {
    "github_repository_invalid": (
        "Use the owner/repository form, for example acme/support-agent"
    ),
    "github_ref_invalid": "Use a branch, tag or commit SHA for ref",
    "github_repository_not_found": (
        "Check the owner/repository spelling; for a private repository, choose "
        "Private and install the GitHub App on it"
    ),
    "github_clone_failed": (
        "Check the repository exists and, for a private repository, that the "
        "GitHub App is installed on it"
    ),
    "github_installation_token_failed": (
        "Reinstall the GitHub App on this repository and retry"
    ),
    "github_installation_token_missing": (
        "Reinstall the GitHub App on this repository and retry"
    ),
    "github_commit_mismatch": "Use a commit SHA that exists on the selected ref",
    "archive_artifact_missing": "Upload the project folder again, then retry",
    "archive_source_not_found": "Upload the project folder again, then retry",
    "source_archive_too_large": (
        "Remove dependencies, build output and recordings from the upload"
    ),
}


def _check(check_id, label, status, detail, *, missing=(), fix=None):
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "detail": detail,
        "missing": list(missing),
        "fix": fix,
    }


def _probe_check(check_id, label, results, skipped_detail, fix_template):
    if not results:
        return _check(check_id, label, "skipped", skipped_detail)
    failed = [item for item in results if not item.get("ok")]
    if not failed:
        detail = "; ".join(str(item.get("message") or "") for item in results)
        return _check(check_id, label, "passed", detail)
    aliases = sorted({alias for item in failed for alias in item.get("aliases") or []})
    return _check(
        check_id,
        label,
        "failed",
        "; ".join(str(item.get("message") or "") for item in failed),
        missing=aliases,
        fix=fix_template.format(aliases=", ".join(aliases)),
    )


def _dialer_check(connector):
    """Whether the platform can dial a phone target, which only it can supply.

    The customer provides no LiveKit or SIP credential for this lane, so an absent dialer is a
    deployment fault rather than something they can fix by filling the form in. Naming it here
    keeps a run that could only ever fail to dial out of the queue.
    """
    from simulate.services.hosted_harness_gateway import platform_dialer_status

    if connector != "phone":
        return _check(
            "platform_dialer",
            "Outbound dialer",
            "skipped",
            "only a phone target is dialled by the platform",
        )
    status = platform_dialer_status()
    if status["available"]:
        return _check(
            "platform_dialer",
            "Outbound dialer",
            "passed",
            "the platform outbound dialer is configured",
        )
    return _check(
        "platform_dialer",
        "Outbound dialer",
        "failed",
        "the platform outbound dialer is not configured on this deployment",
        missing=status["missing"],
        fix=(
            "Contact your Future AGI administrator: phone targets need the "
            "platform SIP trunk and simulator LiveKit credentials configured"
        ),
    )


def _preflight_checks(
    source_kind,
    source_error,
    scanned_files,
    missing,
    required_files,
    probe,
    connector="",
):
    checks = []
    if source_kind in {"remote", "provider"}:
        checks.append(
            _check(
                "source",
                "Source reachable",
                "skipped",
                f"{source_kind} targets have no source tree to read",
            )
        )
    elif source_error is not None:
        checks.append(
            _check(
                "source",
                "Source reachable",
                "failed",
                source_error.message,
                fix=_SOURCE_FIX_HINTS.get(source_error.code, source_error.message),
            )
        )
    else:
        checks.append(
            _check(
                "source",
                "Source reachable",
                "passed",
                f"{scanned_files} files scanned",
            )
        )
    file_aliases = set(required_files)
    files_missing = [alias for alias in missing if alias in file_aliases]
    creds_missing = [alias for alias in missing if alias not in file_aliases]
    if source_kind == "remote":
        checks.append(
            _check(
                "credentials_present",
                "Target credentials",
                "skipped",
                "remote targets own their credentials",
            )
        )
    elif creds_missing:
        checks.append(
            _check(
                "credentials_present",
                "Target credentials",
                "failed",
                f"{len(creds_missing)} required credential(s) not provided",
                missing=creds_missing,
                fix="Add " + ", ".join(creds_missing) + " under target credentials",
            )
        )
    else:
        checks.append(
            _check(
                "credentials_present",
                "Target credentials",
                "passed",
                "every required credential is referenced",
            )
        )
    if not required_files:
        checks.append(
            _check(
                "credential_files",
                "Credential files",
                "skipped",
                "the source does not require a credential file",
            )
        )
    elif files_missing:
        checks.append(
            _check(
                "credential_files",
                "Credential files",
                "failed",
                "the source requires a credential file that has not been uploaded",
                missing=files_missing,
                fix="Upload the JSON credential file for " + ", ".join(files_missing),
            )
        )
    else:
        checks.append(
            _check(
                "credential_files",
                "Credential files",
                "passed",
                "required credential files are uploaded",
            )
        )
    target_probes = []
    key_probes = []
    for item in probe:
        if str(item.get("provider") or "").endswith("_target"):
            target_probes.append(item)
        else:
            key_probes.append(item)
    checks.append(
        _probe_check(
            "credentials_valid",
            "Credentials accepted by provider",
            key_probes,
            "no credential values were submitted to verify",
            "Replace {aliases} with a key the provider accepts",
        )
    )
    checks.append(
        _probe_check(
            "provider_target",
            "Provider agent reachable",
            target_probes,
            "no hosted provider agent to look up",
            "Check the agent ID belongs to the account behind {aliases}",
        )
    )
    checks.append(_dialer_check(connector))
    return checks


def _sandbox_preflight_body(payload, report):
    """The declared preflight shape, rebuilt from what the sandbox server reports.

    Both providers answer the same endpoint, so they owe the same body. The
    sandbox already discovers credentials and inspects packaging; it just
    reports them in its own envelope, so its findings are mapped onto the
    declared shape rather than passed through. ``probe`` is empty because the
    sandbox holds no provider keys to try, and the snapshot names no image:
    those are Daytona facts, and inventing them here would make the readiness
    panel state something untrue.
    """
    from simulate.services.hosted_harness_gateway import (
        HOSTED_ENGINE_CATALOG,
        HOSTED_RUNTIME_CATALOG,
    )

    credentials = dict(report.get("credentials") or {})
    requirements = [
        item for item in credentials.get("requirements") or [] if isinstance(item, dict)
    ]
    missing = [
        str(item.get("environment_name") or item.get("id") or "")
        for item in requirements
        if item.get("required") and item.get("status") == "missing"
    ]
    required_files = [
        str(item.get("environment_name") or item.get("id") or "")
        for item in requirements
        if item.get("kind") == "file"
    ]
    checks = _preflight_checks(
        payload["source"]["kind"],
        None,
        int(credentials.get("scanned_files") or 0),
        missing,
        required_files,
        [],
        str((payload.get("agent") or {}).get("connector") or ""),
    )
    packaging = report.get("packaging") or {}
    checks.append(_packaging_check(packaging))
    # The sandbox decides readiness from packaging as well as credentials, so
    # its verdict leads and the checks explain it. Deriving the state from the
    # checks alone could report "connected" for a source the sandbox has
    # already refused to run.
    ready = bool(report.get("ready_to_submit"))
    credentials["probe"] = []
    return {
        "ready_to_submit": ready,
        "state": "connected" if ready else "failed",
        "checks": checks,
        "credentials": credentials,
        "parallelism_enabled": False,
        "effective_parallelism": payload["runtime"]["parallelism"],
        "resource_profile": None,
        "snapshot": {
            "name": None,
            "digest": None,
            "engines": HOSTED_ENGINE_CATALOG,
            "runtimes": HOSTED_RUNTIME_CATALOG,
        },
    }


def _packaging_check(packaging):
    """Whether the sandbox can build the source, which only it inspects."""
    if not packaging:
        return _check(
            "packaging",
            "Source is buildable",
            "skipped",
            "the sandbox reported no packaging analysis",
        )
    if packaging.get("ready") and packaging.get("agent_runtime_packaged", True):
        selected = str(packaging.get("selected_path") or "").strip()
        return _check(
            "packaging",
            "Source is buildable",
            "passed",
            f"building from {selected}" if selected else "a build was found",
        )
    notes = [str(note) for note in packaging.get("notes") or [] if str(note).strip()]
    return _check(
        "packaging",
        "Source is buildable",
        "failed",
        notes[0] if notes else "no Dockerfile or compose file packages the agent",
        fix="Add a Dockerfile that runs the agent, or a compose file that starts it",
    )


_ORDINAL_WORDS = {
    word: number
    for number, word in enumerate(
        (
            "first second third fourth fifth sixth seventh eighth ninth tenth "
            "eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth "
            "eighteenth nineteenth twentieth"
        ).split(),
        start=1,
    )
}


def scenarios_meant(
    said: Any, suite: list[dict], numbering: dict[int, str] | None = None
) -> list[str]:
    """Scenario names from "4", "12-30", "12, 15, 18" or a name; `numbering` maps stored numbers."""
    by_number = numbering or {
        position: str(one.get("name") or "") for position, one in enumerate(suite, 1)
    }
    known = {str(one.get("name") or "") for one in suite}
    keys = {
        str(one.get("scenario_key") or ""): str(one.get("name") or "") for one in suite
    }
    found: list[str] = []

    def take(name: str) -> None:
        if name and name not in found:
            found.append(name)

    for piece in said if isinstance(said, (list, tuple)) else [said]:
        for part in re.split(r"[,\s]+(?:and\s+)?", str(piece or "").strip()):
            part = part.strip().strip(".")
            if not part:
                continue
            if part in known:
                take(part)
                continue
            if part in keys:
                take(keys[part])
                continue
            span = re.fullmatch(r"(\d+)\s*(?:-|–|to|through)\s*(\d+)", part)
            if span:
                low, high = sorted((int(span.group(1)), int(span.group(2))))
                for number in range(low, high + 1):
                    take(by_number.get(number, ""))
                continue
            # "#4", "4th", "the fourth".
            plain = re.sub(r"^(?:the|scenario|no\.?|#)\s*", "", part, flags=re.IGNORECASE)
            plain = re.sub(r"(?<=\d)(?:st|nd|rd|th)$", "", plain, flags=re.IGNORECASE)
            if plain.isdigit():
                take(by_number.get(int(plain), ""))
                continue
            if plain.lower() in _ORDINAL_WORDS:
                take(by_number.get(_ORDINAL_WORDS[plain.lower()], ""))
    return found


def _scenario_row(reg, number: int | None = None) -> dict:
    """One registered scenario; ``number`` is its one-based place in its own suite."""
    return {
        "number": number,
        "scenario_key": reg.scenario_key,
        "scenario_id": str(reg.scenario_id),
        "name": getattr(reg.scenario, "name", "") if reg.scenario else "",
        "instruction": (getattr(reg.scenario, "prompt", None) or "")
        if reg.scenario
        else None,
        "use_case": (getattr(reg.scenario, "use_case", None) or "")
        if reg.scenario
        else None,
        "call_execution_id": str(reg.call_execution_id)
        if reg.call_execution_id
        else None,
        "status": _scenario_status(reg),
    }


class HostedHarnessProvider:
    """Persist jobs and drive the configured managed sandbox through Temporal."""

    name = "hosted"

    def create(self, request) -> Response:
        from simulate.services.hosted_harness import (
            HostedHarnessError,
            create_hosted_job,
        )
        from simulate.temporal.client import start_hosted_harness_gateway_workflow

        organization = request_organization(request)
        if organization is None:
            return Response(
                {"detail": "Organization not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            return Response(
                {"detail": "Idempotency-Key header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = request.validated_data
        base_url = (
            getattr(settings, "HARNESS_PUBLIC_BASE_URL", "")
            or request.build_absolute_uri("/")
        ).rstrip("/")
        # This uses only references/configuration and deployment env presence.
        # Definitive launch validation runs again after vault resolution.
        try:
            _validate_secret_refs_hosted(payload["agent"]["secret_refs"])
            _validate_phone_connectivity(payload)
            _validate_known_hosted_egress(payload, base_url)
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        from simulate.services.harness_usage import require_harness_run_usage

        try:
            require_harness_run_usage(str(organization.id), payload)
        except Exception as exc:
            response = _usage_limit_response(exc)
            if response is not None:
                return response
            raise
        try:
            _validate_required_credential_files(request, payload)
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        try:
            job, _ = create_hosted_job(
                organization,
                payload,
                idempotency_key=idempotency_key,
                workspace=request_workspace(request),
            )
            retry_cfg = job.payload["retry"]
            start_hosted_harness_gateway_workflow(
                str(job.id),
                base_url,
                retry_cfg["max_infrastructure_attempts"],
                retry_cfg["initial_backoff_seconds"],
                retry_cfg["max_backoff_seconds"],
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        except Exception:
            return Response(
                {"detail": "Hosted harness scheduler is unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(serialize_job(job), status=status.HTTP_202_ACCEPTED)

    def list(self, request) -> Response:
        organization = request_organization(request)
        jobs = scope_jobs(
            HostedHarnessJob.no_workspace_objects.filter(organization=organization),
            request,
        ).order_by("-created_at")[:100]
        return Response([serialize_job(job) for job in jobs])

    def preflight(self, request) -> Response:
        from simulate.services.hosted_harness import HostedHarnessError
        from simulate.services.hosted_harness_gateway import (
            HOSTED_ENGINE_CATALOG,
            HOSTED_RUNTIME_CATALOG,
        )
        from simulate.services.hosted_sandbox import sandbox_runtime_reference

        payload = request.validated_data
        base_url = (
            getattr(settings, "HARNESS_PUBLIC_BASE_URL", "")
            or request.build_absolute_uri("/")
        ).rstrip("/")
        try:
            _validate_secret_refs_hosted(payload["agent"]["secret_refs"])
            _validate_phone_connectivity(payload)
            _validate_known_hosted_egress(payload, base_url)
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        from simulate.services.hosted_harness import clamp_parallelism

        runtime = payload["runtime"]
        source_error = None
        try:
            source_analysis = _preflight_source_connectors(request, payload)
        except HostedHarnessError as exc:
            source_error = exc
            source_analysis = ([], [], 0)
        # Keep two-item patched return values compatible with tests and custom
        # providers written before credential-file discovery was added.
        if len(source_analysis) == 2:
            detected, scanned = source_analysis
            required_files = []
        else:
            detected, required_files, scanned = source_analysis
        credentials = _connector_credential_readiness(
            payload, detected, scanned, required_files
        )
        probe = _preflight_credential_probe(payload)
        credentials["report"]["probe"] = probe
        checks = _preflight_checks(
            payload["source"]["kind"],
            source_error,
            scanned,
            credentials["missing"],
            required_files,
            probe,
            str(payload["agent"].get("connector") or ""),
        )
        failed = any(check["status"] == "failed" for check in checks)
        runtime_name, runtime_digest = sandbox_runtime_reference()
        # Continuous advisory surface (C4 §4 pin ii / §5). ``parallelism_enabled``
        # reflects the same flag-AND-digest state the register_attempt guard
        # enforces; the ``effective_parallelism`` echo reflects the clamped value
        # (never the requested value) whenever admission would clamp, so the FE
        # is never promised a W the platform will refuse.
        from simulate.services.harness_capacity import configured_capacity

        try:
            capacity = configured_capacity(payload)
            parallel_capacity = configured_capacity(
                {
                    **payload,
                    "scenario_count": max(2, payload.get("scenario_count", 1)),
                    "runtime": {**runtime, "parallelism": 2},
                }
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=503)
        from simulate.services.hosted_sandbox import sandbox_provider_name

        if sandbox_provider_name() != "daytona" and (
            (capacity.snapshot_name and capacity.snapshot_name != runtime_name)
            or (capacity.snapshot_digest and capacity.snapshot_digest != runtime_digest)
        ):
            return Response(
                {
                    "detail": "configured resource profile does not match the selected sandbox runtime"
                },
                status=503,
            )
        digest = capacity.snapshot_digest or runtime_digest
        admitted, _clamped = clamp_parallelism(runtime["parallelism"], digest)
        admitted = min(admitted, capacity.parallelism)
        parallel_digest = parallel_capacity.snapshot_digest or runtime_digest
        _, denied_at_2 = clamp_parallelism(2, parallel_digest)
        body = {
            "ready_to_submit": not failed,
            "state": "failed" if failed else "connected",
            "checks": checks,
            "credentials": credentials["report"],
            "parallelism_enabled": not denied_at_2
            and parallel_capacity.parallelism > 1,
            "effective_parallelism": admitted,
            "resource_profile": {
                "name": capacity.name,
                "cpu_units": capacity.cpu_units,
                "memory_mb": capacity.memory_mb,
                "disk_gb": capacity.disk_gb,
            },
            "snapshot": {
                "name": capacity.snapshot_name or runtime_name or None,
                "digest": digest or None,
                "engines": HOSTED_ENGINE_CATALOG,
                "runtimes": HOSTED_RUNTIME_CATALOG,
            },
        }
        # A source that cannot be read keeps its error status: callers that key on
        # the HTTP status still see the message, and the checks ride along for
        # callers that render them.
        if source_error is not None:
            return Response(
                {**source_error.as_dict(), **body}, status=source_error.status_code
            )
        return Response(body)

    def retrieve(self, request, pk) -> Response:
        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Hosted harness job not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(serialize_job(job))

    def run(self, request, pk) -> Response:
        """Create and dispatch one immutable selected-scenario simulation Run."""

        from simulate.services.hosted_harness import (
            HostedHarnessError,
            create_selected_harness_run,
        )
        from simulate.temporal.client import start_hosted_harness_gateway_workflow

        environment = self._job(request, pk)
        if environment is None:
            return Response(
                {"detail": "Hosted harness environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            return Response(
                {"detail": "Idempotency-Key header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            child, created = create_selected_harness_run(
                environment,
                scenario_keys=list(request.validated_data["scenario_ids"]),
                trials=request.validated_data["trials"],
                idempotency_key=idempotency_key,
            )
            retry_dispatch = False
            if (
                not created
                and child.state == HostedHarnessJob.State.FAILED
                and (child.failure or {}).get("code") == "scheduler_unavailable"
            ):
                with transaction.atomic():
                    child = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                        id=child.id
                    )
                    if (
                        child.state == HostedHarnessJob.State.FAILED
                        and (child.failure or {}).get("code") == "scheduler_unavailable"
                    ):
                        now = timezone.now()
                        child.state = HostedHarnessJob.State.QUEUED
                        child.current_stage = "queued"
                        child.completed_count = 0
                        child.failed_count = 0
                        child.uploaded_artifact_bytes = 0
                        child.terminal_at = None
                        child.failure = None
                        child.content_updated_at = now
                        child.save(
                            update_fields=[
                                "state",
                                "current_stage",
                                "completed_count",
                                "failed_count",
                                "uploaded_artifact_bytes",
                                "terminal_at",
                                "failure",
                                "content_updated_at",
                                "updated_at",
                            ]
                        )
                        TestExecution.no_workspace_objects.filter(
                            id=child.test_execution_id
                        ).update(
                            status=TestExecution.ExecutionStatus.PENDING,
                            started_at=now,
                            completed_at=None,
                            completed_calls=0,
                            failed_calls=0,
                            error_reason=None,
                        )
                        retry_dispatch = True

            if created or retry_dispatch:
                base_url = (
                    getattr(settings, "HARNESS_PUBLIC_BASE_URL", "")
                    or request.build_absolute_uri("/")
                ).rstrip("/")
                retry_cfg = child.payload["retry"]
                start_hosted_harness_gateway_workflow(
                    str(child.id),
                    base_url,
                    retry_cfg["max_infrastructure_attempts"],
                    retry_cfg["initial_backoff_seconds"],
                    retry_cfg["max_backoff_seconds"],
                )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        except Exception:
            logger.exception(
                "selected harness Run dispatch failed environment=%s", environment.id
            )
            if "child" in locals():
                failure = {
                    "domain": "infrastructure",
                    "stage": "queued",
                    "code": "scheduler_unavailable",
                    "message": "Selected harness Run could not be scheduled",
                }
                with transaction.atomic():
                    marked_failed = HostedHarnessJob.no_workspace_objects.filter(
                        id=child.id,
                        state=HostedHarnessJob.State.QUEUED,
                    ).update(
                        state=HostedHarnessJob.State.FAILED,
                        current_stage="failed",
                        terminal_at=timezone.now(),
                        failure=failure,
                    )
                    if marked_failed:
                        TestExecution.no_workspace_objects.filter(
                            id=child.test_execution_id
                        ).update(
                            status=TestExecution.ExecutionStatus.FAILED,
                            completed_at=timezone.now(),
                            error_reason=failure["message"],
                        )
                        return Response(
                            {"detail": "Hosted harness scheduler is unavailable"},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE,
                        )
                child.refresh_from_db()
                if (
                    child.state == HostedHarnessJob.State.FAILED
                    and (child.failure or {}).get("code") == "scheduler_unavailable"
                ):
                    return Response(
                        {"detail": "Hosted harness scheduler is unavailable"},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )
            else:
                return Response(
                    {"detail": "Hosted harness scheduler is unavailable"},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        manifest = list(
            (child.payload.get("metadata") or {}).get("execution_manifest") or []
        )
        selected = list(
            (child.payload.get("metadata") or {}).get("selected_scenario_keys") or []
        )
        return Response(
            {
                "environment_id": str(environment.id),
                "job_id": str(child.id),
                "run_id": str(child.run_id),
                "run_test_id": str(child.run_test_id),
                "test_execution_id": str(child.test_execution_id),
                "scenario_count": len(selected),
                "trials": int((child.payload.get("metadata") or {}).get("trials") or 1),
                "total_calls": len(manifest),
                "state": child.state,
                "stage": child.current_stage,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def cancel(self, request, pk) -> Response:
        from simulate.services.hosted_harness import request_cancellation
        from simulate.temporal.client import cancel_hosted_harness_gateway_workflow

        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Hosted harness job not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        reason = request.validated_data["reason"]
        request_cancellation(job, reason)
        try:
            cancel_hosted_harness_gateway_workflow(str(job.id))
        except Exception:
            logger.exception(
                "hosted harness workflow cancellation signal failed job=%s",
                job.id,
            )
            try:
                from simulate.services.hosted_harness_gateway import (
                    HostedHarnessGateway,
                )

                HostedHarnessGateway().cancel(job, reason=reason)
            except Exception:
                logger.exception(
                    "hosted harness direct cancellation fallback failed job=%s",
                    job.id,
                )
                return Response(
                    {
                        "detail": (
                            "Cancellation was recorded but cleanup could not be "
                            "confirmed; it will be retried."
                        )
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        job.refresh_from_db()
        return Response(serialize_job(job))

    def adjust(self, request, pk) -> Response:
        from simulate.services.hosted_harness import HostedHarnessError
        from simulate.services.hosted_harness_gateway import HostedHarnessGateway

        job = self._job(request, pk)
        if job is None:
            return Response(
                build_error_envelope("Hosted harness job not found", status_code=404),
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            job = HostedHarnessGateway().adjust(job, request.validated_data)
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        return Response(serialize_job(job))

    def send_message(self, request, pk) -> Response:
        from simulate.services.hosted_harness import HostedHarnessError
        from simulate.services.hosted_harness_conversation import (
            enqueue_message,
            serialize_conversation,
        )
        from simulate.tasks.hosted_harness_conversation import (
            ensure_hosted_harness_conversation_runtime,
        )

        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Hosted harness job not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        terminal_states = {
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        }
        if job.state in terminal_states:
            conversation = (
                HostedHarnessConversation.no_workspace_objects.filter(job=job)
                .only("latest_workspace_object_key")
                .first()
            )
            metadata = (job.payload or {}).get("metadata") or {}
            has_archive = bool(
                metadata.get("authoring_object_key")
                or getattr(conversation, "latest_workspace_object_key", None)
            )
            if job.state == HostedHarnessJob.State.COMPLETED and not has_archive:
                return Response(
                    {
                        "error": "conversation_workspace_not_ready",
                        "message": "This completed run has no saved authoring workspace to restore.",
                        "retryable": False,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        data = request.validated_data
        try:
            conversation, _message, _created = enqueue_message(
                job,
                content=data["content"],
                client_request_id=data["client_request_id"],
                kind=data["kind"],
                reply_to=data.get("reply_to"),
                payload=data.get("payload"),
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        base_url = (
            getattr(settings, "HARNESS_PUBLIC_BASE_URL", "")
            or request.build_absolute_uri("/")
        ).rstrip("/")
        try:
            ensure_hosted_harness_conversation_runtime.apply_async(
                args=[str(conversation.id), base_url]
            )
        except Exception:
            return Response(
                {
                    "error": "conversation_scheduler_unavailable",
                    "message": "The message was saved but its runtime could not be scheduled",
                    "retryable": True,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        conversation.refresh_from_db()
        return Response(
            serialize_conversation(conversation),
            status=status.HTTP_202_ACCEPTED,
        )

    def rerun_saved(
        self,
        job_id: str,
        *,
        organization,
        workspace,
        environment_values: dict[str, str],
    ) -> dict[str, Any]:
        """Create a new Run from a saved environment without replacing history."""

        from uuid import uuid4

        from simulate.models import HostedHarnessSecret
        from simulate.services.hosted_harness import (
            HostedHarnessError,
            create_selected_harness_run,
        )
        from simulate.temporal.client import start_hosted_harness_gateway_workflow

        base_url = str(getattr(settings, "HARNESS_PUBLIC_BASE_URL", "") or "").rstrip(
            "/"
        )
        if not base_url:
            raise HostedHarnessError(
                "public_base_url_missing",
                "HARNESS_PUBLIC_BASE_URL is required to rerun a hosted harness job",
                status_code=503,
            )
        job = (
            HostedHarnessJob.no_workspace_objects.filter(
                id=job_id, organization=organization, workspace=workspace
            )
            .select_related("environment")
            .first()
        )
        if job is None:
            raise HostedHarnessError(
                "job_not_found",
                "saved hosted harness job was not found",
                status_code=404,
            )
        if job.state not in {
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        }:
            raise HostedHarnessError(
                "job_not_terminal",
                f"hosted harness job cannot be rerun while it is {job.state}",
                status_code=409,
            )

        environment = job.environment or job
        prior_metadata = (job.payload or {}).get("metadata") or {}
        scenario_keys = list(prior_metadata.get("selected_scenario_keys") or [])
        if not scenario_keys:
            scenario_keys = list(
                environment.scenario_registrations.order_by(
                    "created_at", "id"
                ).values_list("scenario_key", flat=True)
            )
        trials = int(prior_metadata.get("trials") or 1)
        child, _ = create_selected_harness_run(
            environment,
            scenario_keys=scenario_keys,
            trials=trials,
            idempotency_key=str(uuid4()),
        )

        if environment_values:
            payload = copy.deepcopy(child.payload)
            secret_refs = dict((payload.get("agent") or {}).get("secret_refs") or {})
            for alias, value in environment_values.items():
                key = f"harness-{alias.lower()}-{uuid4().hex}"
                HostedHarnessSecret.objects.create(
                    organization=organization,
                    name=key,
                    version="1",
                    encrypted_value=value,
                )
                secret_refs[alias] = {
                    "manager": "platform-vault",
                    "key": key,
                    "version": "1",
                    "purpose": "target_provider",
                }
            payload.setdefault("agent", {})["secret_refs"] = secret_refs
            child.payload = payload
            child.save(update_fields=["payload", "updated_at"])

        retry_cfg = child.payload["retry"]
        try:
            start_hosted_harness_gateway_workflow(
                str(child.id),
                base_url,
                retry_cfg["max_infrastructure_attempts"],
                retry_cfg["initial_backoff_seconds"],
                retry_cfg["max_backoff_seconds"],
            )
        except Exception:
            HostedHarnessJob.no_workspace_objects.filter(id=child.id).update(
                state=HostedHarnessJob.State.FAILED,
                current_stage="failed",
                terminal_at=timezone.now(),
                failure={
                    "domain": "infrastructure",
                    "stage": "queued",
                    "code": "scheduler_unavailable",
                    "message": "Hosted rerun could not be scheduled",
                },
            )
            TestExecution.no_workspace_objects.filter(
                id=child.test_execution_id
            ).update(
                status=TestExecution.ExecutionStatus.FAILED,
                completed_at=timezone.now(),
                error_reason="Hosted rerun could not be scheduled",
            )
            raise
        child.refresh_from_db()
        return serialize_job(child)

    # Editable fields; all but `tests` are refused when the caller declines a re-proof.
    _DESCRIPTIVE_FIELDS = frozenset({"tests"})
    _BEHAVIOURAL_FIELDS = frozenset({"max_turns", "background_noise", "keywords"})
    _PERSONA_FIELDS = frozenset(
        {
            "personality",
            "communication_style",
            "accent",
            "languages",
            "occupation",
            "location",
        }
    )

    def _editing_contract(self, spoken: bool = True) -> dict[str, Any]:
        """Which fields an amend will take, and which of them cannot be taken without a re-proof."""
        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.persona import Persona

        # A call has no turn budget; a chat has no accent or room behind the caller.
        behavioural = self._BEHAVIOURAL_FIELDS - (
            {"max_turns"} if spoken else {"background_noise"}
        )
        persona = self._PERSONA_FIELDS - (set() if spoken else {"accent"})
        vocabulary = {
            "personality": Persona.PersonalityChoices,
            "communication_style": Persona.CommunicationStyleChoices,
            "accent": Persona.AccentChoices,
            "languages": AgentDefinition.LanguageChoices,
            "occupation": Persona.ProfessionChoices,
            "location": Persona.LocationChoices,
        }
        return {
            "editable_fields": sorted(self._DESCRIPTIVE_FIELDS | behavioural),
            "persona_fields": sorted(persona),
            "persona_choices": {
                field: (
                    list(vocabulary[field].labels)
                    if field == "languages"
                    else [value for value, _ in vocabulary[field].choices]
                )
                for field in sorted(persona)
            },
            "rework_fields": sorted(behavioural | persona),
        }

    def list_scenarios(self, request, pk) -> Response:
        """One page of a run's authored scenarios, in the order they were written."""
        from tfc.utils.pagination import ExtendedPageNumberPagination

        job = _scoped_job(request, pk)
        if job is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        from simulate.services.harness_scenarios import (
            apply_filters,
            apply_ordering,
            apply_search,
            field_catalogue,
            group_counts,
            grouped,
            index_scenarios,
            scenario_row,
        )

        queryset = HostedHarnessScenario.no_workspace_objects.filter(
            job=job
        ).select_related("scenario", "call_execution")
        if not queryset.filter(number__isnull=False).exists():
            # Runs from before indexing: index from the stage output or the unpacked archive.
            artefact = (
                HostedHarnessStageOutput.no_workspace_objects.filter(
                    job=job, kind="scenarios"
                )
                .values_list("data", flat=True)
                .first()
            )
            if not artefact:
                artefact = next(
                    (
                        one.get("data")
                        for one in (job.stage_outputs or [])
                        if isinstance(one, dict) and one.get("kind") == "scenarios"
                    ),
                    None,
                )
            if isinstance(artefact, list) and artefact:
                index_scenarios(job, artefact)
                queryset = HostedHarnessScenario.no_workspace_objects.filter(
                    job=job
                ).select_related("scenario", "call_execution")
        # Filter choices are counted before filtering, or picking one value would hide the others.
        queryset = apply_search(queryset, request.query_params.get("search", ""))
        offerable = queryset
        queryset = apply_filters(queryset, request.query_params)
        queryset = apply_ordering(queryset, request.query_params.get("ordering", ""))

        paginator = ExtendedPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request)
        from simulate.services.harness_scenarios import DEFAULT_GROUP_BY

        asked = request.query_params.get("group_by")
        group_by = DEFAULT_GROUP_BY if asked is None else asked
        rows = grouped([scenario_row(one) for one in page or []], group_by)
        response = paginator.get_paginated_response(rows)
        response.data["groups"] = group_counts(rows, queryset, group_by)
        response.data["group_by"] = group_by
        from simulate.services.harness_environment import AGENT_TYPE_VOICE, agent_type

        spoken = agent_type(job) == AGENT_TYPE_VOICE
        response.data["fields"] = field_catalogue(offerable, spoken=spoken)
        response.data["scenario_editing"] = self._editing_contract(spoken)
        from simulate.services.harness_scenarios import GROUPINGS

        response.data["groupings"] = [
            dict(one) for one in GROUPINGS if spoken or one["value"] != "accent"
        ]
        from simulate.services.harness_scenarios import level_labels_for
        response.data["level_labels"] = level_labels_for(rows, response.data["fields"])
        return response

    def scenario_coverage(self, request, pk) -> Response:
        """The suite's coverage grid, over the filtered suite rather than a page."""
        job = _scoped_job(request, pk)
        if job is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        from simulate.services.harness_scenarios import (
            DEFAULT_COL_AXIS,
            DEFAULT_ROW_AXIS,
            apply_filters,
            apply_search,
            coverage_grid,
        )

        queryset = HostedHarnessScenario.no_workspace_objects.filter(job=job)
        queryset = apply_search(queryset, request.query_params.get("search", ""))
        queryset = apply_filters(queryset, request.query_params)
        from simulate.services.harness_environment import AGENT_TYPE_VOICE, agent_type

        return Response(
            coverage_grid(
                queryset,
                request.query_params.get("row_axis") or DEFAULT_ROW_AXIS,
                request.query_params.get("col_axis") or DEFAULT_COL_AXIS,
                spoken=agent_type(job) == AGENT_TYPE_VOICE,
            )
        )

    def amend_scenarios(self, request, pk) -> Response:
        """Edit a finished run's authored suite, one receipt per requested change."""
        from simulate.services.hosted_harness_gateway import (
            push_scenarios_into_live_sandbox,
            rewrite_authoring_scenarios,
        )

        changes = request.validated_data["changes"]
        rework = bool(request.validated_data.get("rework", True))
        with transaction.atomic():
            # Scope before locking, so a busy lock never reveals a job outside the caller's scope.
            scoped = _scoped_job(request, pk)
            if scoped is None:
                return Response(
                    {"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND
                )
            job = (
                HostedHarnessJob.no_workspace_objects.select_for_update()
                .filter(id=scoped.id)
                .first()
            )
            if job is None:
                return Response(
                    {"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND
                )
            output = (
                HostedHarnessStageOutput.no_workspace_objects.select_for_update()
                .filter(job=job, kind="scenarios")
                .first()
            )
            suite = list(output.data or []) if output is not None else None
            if suite is None:
                suite = [
                    dict(one)
                    for one in next(
                        (
                            item.get("data") or []
                            for item in (job.stage_outputs or [])
                            if item.get("kind") == "scenarios"
                        ),
                        [],
                    )
                ]
            if not suite:
                return Response(
                    {
                        "error": "no_authored_suite",
                        "message": "this run has no authored scenarios to amend",
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            by_name = {str(one.get("name") or ""): one for one in suite}
            receipts = []
            touched = False
            # One change may name many scenarios; each gets its own receipt.
            spread = []
            for change in changes:
                said = change.get("scenarios") or change.get("scenario")
                # Resolve numbers against stored numbering, not list position.
                numbering = {
                    row.number: row.name or row.scenario_key
                    for row in HostedHarnessScenario.no_workspace_objects.filter(
                        job=job, number__isnull=False
                    )
                }
                meant = scenarios_meant(said, suite, numbering or None)
                if not meant:
                    receipts.append(
                        {
                            "scenario": str(change.get("scenario") or ""),
                            "outcome": "refused",
                            "why": "nothing in this suite answers to that",
                        }
                    )
                    continue
                spread.extend({**change, "scenario": name} for name in meant)
            for change in spread:
                name = str(change.get("scenario") or "")
                target = by_name.get(name)
                if target is None:
                    receipts.append(
                        {
                            "scenario": name,
                            "outcome": "refused",
                            "why": "no scenario of that name in this suite",
                        }
                    )
                    continue
                op = str(change.get("op") or "")
                if op == "drop":
                    if not rework:
                        receipts.append(
                            {
                                "scenario": name,
                                "outcome": "refused",
                                "why": "dropping a scenario changes the suite, so it needs a re-proof",
                            }
                        )
                        continue
                    suite = [one for one in suite if one is not target]
                    by_name.pop(name, None)
                    touched = True
                    receipts.append(
                        {"scenario": name, "outcome": "applied", "why": "dropped"}
                    )
                    continue
                if op == "set_field":
                    field = str(change.get("field") or "")
                    if field in self._DESCRIPTIVE_FIELDS:
                        pass
                    elif field in self._BEHAVIOURAL_FIELDS:
                        if not rework:
                            receipts.append(
                                {
                                    "scenario": name,
                                    "outcome": "refused",
                                    "why": f"{field} changes what the run does, so it needs a re-proof",
                                }
                            )
                            continue
                    else:
                        receipts.append(
                            {
                                "scenario": name,
                                "outcome": "refused",
                                "why": f"{field} is not editable: it is proved, not described",
                            }
                        )
                        continue
                    target[field] = change.get("value")
                    touched = True
                    receipts.append(
                        {"scenario": name, "outcome": "applied", "why": f"{field} updated"}
                    )
                    continue
                if op == "set_persona":
                    if not rework:
                        receipts.append(
                            {
                                "scenario": name,
                                "outcome": "refused",
                                "why": "the persona is what the agent hears, so it needs a re-proof",
                            }
                        )
                        continue
                    given = dict(change.get("persona") or {})
                    unknown = sorted(set(given) - self._PERSONA_FIELDS)
                    if unknown:
                        receipts.append(
                            {
                                "scenario": name,
                                "outcome": "refused",
                                "why": f"not editable on a persona: {', '.join(unknown)}",
                            }
                        )
                        continue
                    persona = dict(target.get("persona") or {})
                    persona.update(
                        {key: value for key, value in given.items() if value is not None}
                    )
                    target["persona"] = persona
                    touched = True
                    receipts.append(
                        {"scenario": name, "outcome": "applied", "why": "persona updated"}
                    )
                    continue
                receipts.append(
                    {"scenario": name, "outcome": "refused", "why": f"unknown change {op!r}"}
                )
            # Edits pass the same gates as a written scenario.
            if touched:
                try:
                    from fi.alk.harness.scenario import Scenario, scenario_edit_problems
                except ImportError:  # the harness package ships in the runner image, not the web backend
                    scenario_edit_problems = None

                rejected = []
                for one in suite if scenario_edit_problems else ():
                    try:
                        problems = scenario_edit_problems(Scenario.model_validate(one))
                    except Exception:  # noqa: BLE001 - a document we cannot read is the edit's fault
                        problems = ["the edited scenario could not be read"]
                    if problems:
                        rejected.append((str(one.get("name") or ""), problems))
                if rejected:
                    named = {name for name, _ in rejected}
                    return Response(
                        {
                            "receipts": [
                                {
                                    "scenario": name,
                                    "outcome": "refused",
                                    "why": "; ".join(problems),
                                }
                                for name, problems in rejected
                            ]
                            + [
                                one
                                for one in receipts
                                if one.get("scenario") not in named
                                and one.get("outcome") == "refused"
                            ]
                        }
                    )
            if touched:
                if output is not None:
                    output.data = suite
                    output.summary = f"{len(suite)} pre-authored scenarios"
                    output.save(update_fields=["data", "summary", "updated_at"])
                else:
                    job.stage_outputs = [
                        {**item, "data": suite}
                        if item.get("kind") == "scenarios"
                        else item
                        for item in (job.stage_outputs or [])
                    ]
                    job.save(update_fields=["stage_outputs", "updated_at"])
                # Write to the archive, the live guest and the index, or the edit reverts or hides.
                try:
                    from simulate.services.harness_scenarios import index_scenarios

                    index_scenarios(job, suite, prune=True)
                except Exception:  # noqa: BLE001 - the edit itself applied; the index can lag
                    logger.warning(
                        "harness_scenario_reindex_failed job_id=%s", job.id, exc_info=True
                    )
                rewrite_authoring_scenarios(job, suite)
                delivered = push_scenarios_into_live_sandbox(job, suite)
                if delivered:
                    receipts = [
                        {**one, "outcome": "queued"}
                        if one.get("outcome") == "applied"
                        else one
                        for one in receipts
                    ]
        return Response({"receipts": receipts})

    def extend(self, request, pk) -> Response:
        """Chat 'Add scenarios' on a finished RL environment: add ``count`` new scenarios,
        steered by optional guidance, replaying the authored world. Rerun is a separate action."""
        import copy
        from uuid import uuid4

        from simulate.services.hosted_harness import HostedHarnessError

        organization = request_organization(request)
        workspace = request_workspace(request)
        if organization is None:
            return Response(
                {"detail": "Organization not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        count = int(request.validated_data["count"])
        guidance = str(request.validated_data.get("guidance") or "").strip()
        client_request_id = request.validated_data.get("client_request_id") or None

        terminal_states = {
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        }
        try:
            with transaction.atomic():
                job = (
                    HostedHarnessJob.no_workspace_objects.select_for_update()
                    .filter(id=str(pk), organization=organization, workspace=workspace)
                    .first()
                )
                if job is None:
                    raise HostedHarnessError(
                        "job_not_found",
                        "saved hosted harness job was not found",
                        status_code=404,
                    )
                if job.state not in terminal_states:
                    raise HostedHarnessError(
                        "job_not_terminal",
                        "scenarios can be added only after the run finishes; it is "
                        f"{job.state}",
                        status_code=409,
                    )
                metadata = (job.payload or {}).get("metadata") or {}
                if not metadata.get("authoring_object_key"):
                    raise HostedHarnessError(
                        "extend_authoring_snapshot_missing",
                        "This run predates deterministic scenario reuse. Start one new "
                        "end-to-end run; its follow-ups can then add scenarios.",
                        status_code=409,
                    )
                from simulate.services.harness_usage import require_harness_authoring

                require_harness_authoring(str(organization.id))
                # Add relative to what the environment actually holds: the scenarios
                # registered by the last successful run are exactly what the saved authoring
                # archive contains (it is only re-frozen on success). ``job.scenario_count``
                # may still carry a target an earlier failed add never reached.
                existing = HostedHarnessScenario.no_workspace_objects.filter(
                    job=job
                ).count()
                new_count = (existing or job.scenario_count) + count
                if new_count > 200:
                    raise HostedHarnessError(
                        "scenario_limit_exceeded",
                        "a hosted run can contain at most 200 scenarios",
                        status_code=422,
                    )
                payload = copy.deepcopy(job.payload)
                meta = payload.setdefault("metadata", {})
                adjustments = list(meta.get("adjustments") or [])
                adjustments.append(
                    {
                        "adjustment_id": str(uuid4()),
                        "client_request_id": client_request_id,
                        "instruction": guidance,
                        "target_stage": "scenarios",
                        "scenario_delta": count,
                        "status": "pending",
                        "created_at": timezone.now().isoformat(),
                    }
                )
                meta["adjustments"] = adjustments
                # Consumed by the gateway launch: replay the frozen world but re-run
                # scenario-gen to reach the new total, steered by any guidance.
                meta["scenario_extend"] = {
                    "guidance": [guidance] if guidance else [],
                    "target_count": new_count,
                }
                payload["metadata"] = meta
                job.payload = payload
                job.scenario_count = new_count
                job.save(update_fields=["payload", "scenario_count", "updated_at"])
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        except Exception as exc:
            response = _usage_limit_response(exc)
            if response is not None:
                return response
            raise

        try:
            return Response(
                self.rerun_saved(
                    str(pk),
                    organization=organization,
                    workspace=workspace,
                    environment_values={},
                )
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)

    def source_upload(self, request) -> Response:
        from simulate.services.hosted_harness import HostedHarnessError
        from simulate.services.hosted_harness_gateway import store_source_archive

        organization = request_organization(request)
        if organization is None:
            return Response(
                {"detail": "Organization not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        files = request.FILES.getlist("files")
        paths = request.data.getlist("paths")
        if not files or len(files) != len(paths):
            return Response(
                {"detail": "one relative path is required per file"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rejected = _rejected_archive_upload(files)
        if rejected is not None:
            return rejected
        if len(files) > 5_000:
            return Response(
                {"detail": "source may contain at most 5000 files"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        total = sum(int(getattr(uploaded, "size", 0) or 0) for uploaded in files)
        if total > 200 * 1024 * 1024:
            return Response(
                {"detail": "source may not exceed 200 MiB"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            result = store_source_archive(
                organization,
                files,
                paths,
                str(request.data.get("name") or "uploaded-agent"),
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        return Response(result, status=status.HTTP_201_CREATED)

    def health(self) -> dict[str, Any]:
        from simulate.services.hosted_sandbox import (
            sandbox_provider_name,
            sandbox_runtime_reference,
        )

        provider = sandbox_provider_name()
        runtime_name, runtime_build_id = sandbox_runtime_reference()
        if provider == "e2b":
            configured = bool(
                getattr(settings, "E2B_API_KEY", "")
                and runtime_name
                and runtime_build_id
                and int(getattr(settings, "ALK_E2B_MAX_TTL_SECONDS", 0)) > 0
            )
        else:
            configured = bool(getattr(settings, "DAYTONA_API_KEY", "") and runtime_name)
        return {
            "configured": configured,
            "provider": provider,
            "sandbox_provider": provider,
            # Both managed providers now expose callbacks through the platform relay;
            # the provider-specific traffic credential never reaches the guest.
            "public_ingress": True,
        }

    def _job(self, request, pk):
        organization = request_organization(request)
        return scope_jobs(
            HostedHarnessJob.no_workspace_objects.filter(
                id=pk, organization=organization
            ),
            request,
        ).first()


class SandboxHarnessProvider:
    """Thin HTTP proxy to an out-of-process ALK sandbox server.

    Accepts the same v1.6 request the Daytona provider does and maps it to the
    sandbox server's flat contract before forwarding.
    """

    name = "sandbox"

    def _client(self):
        from simulate.services.harness_sandbox import HarnessSandboxClient

        return HarnessSandboxClient()

    def rerun_saved(
        self,
        job_id: str,
        *,
        organization,
        workspace,
        environment_values: dict[str, str],
    ) -> dict[str, Any]:
        from simulate.services.harness_credentials import credentials_for_rerun

        client = self._client()
        saved_environment, secret_refs = credentials_for_rerun(
            job_id,
            organization=organization,
            client=client,
            environment_overrides=environment_values,
        )
        return client.rerun(
            job_id,
            {
                "environment_values": saved_environment,
                "secret_refs": secret_refs,
                "only": [],
            },
        )

    def run(self, request, pk) -> Response:
        return Response(
            build_error_envelope(
                "selected environment Runs require the hosted harness provider",
                status_code=status.HTTP_400_BAD_REQUEST,
                code="run_not_supported",
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    def list_scenarios(self, request, pk) -> Response:
        return Response(
            {
                "error": "scenarios_not_supported",
                "message": "reading an authored suite is only available on the hosted provider",
            },
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    def scenario_coverage(self, request, pk) -> Response:
        return Response(
            {
                "error": "scenarios_not_supported",
                "message": "reading an authored suite is only available on the hosted provider",
            },
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    def amend_scenarios(self, request, pk) -> Response:
        return Response(
            {
                "error": "amend_not_supported",
                "message": "editing an authored suite is only available on the hosted provider",
            },
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    def extend(self, request, pk) -> Response:
        return Response(
            {
                "error": "extend_not_supported",
                "message": (
                    "adding scenarios is only available on the hosted (daytona) provider"
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    @staticmethod
    def _flatten_source(data: dict[str, Any]) -> dict[str, Any]:
        source = data["source"]
        agent = data["agent"]
        flat: dict[str, Any] = {
            "scenario_count": data.get("scenario_count", 10),
            "seed": data.get("seed"),
            "connector": agent.get("connector", "auto"),
            "connector_config": agent.get("config", {}) or {},
            "secret_refs": agent.get("secret_refs", {}) or {},
            "platform_run_id": data.get("platform_run_id"),
            "metadata": data.get("metadata", {}) or {},
        }
        kind = source["kind"]
        if kind == "github":
            flat["github_repository"] = source.get("repository")
            if source.get("ref"):
                flat["github_ref"] = source["ref"]
            if source.get("commit_sha"):
                flat["github_commit_sha"] = source["commit_sha"]
            flat["github_visibility"] = source.get("visibility", "public")
            if source.get("installation_id"):
                flat["github_installation_id"] = source["installation_id"]
        elif kind == "archive":
            flat["source_id"] = str(source["archive_artifact_id"])
        else:  # remote — the sandbox server has no external-target mode
            raise _SandboxMappingError(
                f"source kind {kind!r} is not supported by the sandbox provider"
            )
        return {key: value for key, value in flat.items() if value is not None}

    def create(self, request) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        try:
            payload = self._flatten_source(request.validated_data)
            result = self._client().submit(payload)
        except _SandboxMappingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return Response(result, status=status.HTTP_202_ACCEPTED)

    def list(self, request) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        try:
            return Response(self._client().list_jobs())
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    def preflight(self, request) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        try:
            payload = self._flatten_source(request.validated_data)
            report = self._client().preflight(payload)
        except _SandboxMappingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return Response(_sandbox_preflight_body(request.validated_data, report))

    def retrieve(self, request, pk) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        try:
            return Response(self._client().get(str(pk)))
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    def cancel(self, request, pk) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        try:
            return Response(self._client().cancel(str(pk)))
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    def adjust(self, request, pk) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        try:
            return Response(self._client().adjust(str(pk), request.validated_data))
        except HarnessSandboxRejected as exc:
            return Response(
                build_error_envelope(str(exc), status_code=exc.status_code),
                status=exc.status_code,
            )
        except HarnessSandboxUnavailable as exc:
            return Response(
                build_error_envelope(
                    str(exc), status_code=status.HTTP_503_SERVICE_UNAVAILABLE
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    def send_message(self, request, pk) -> Response:
        return Response(
            {
                "error": "conversation_not_supported",
                "message": (
                    "hosted conversations are available only on the daytona provider"
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def source_upload(self, request) -> Response:
        from simulate.services.harness_sandbox import (
            HarnessSandboxRejected,
            HarnessSandboxUnavailable,
        )

        files = request.FILES.getlist("files")
        paths = request.data.getlist("paths")
        if not files or len(files) != len(paths):
            return Response(
                {"detail": "one relative path is required per file"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rejected = _rejected_archive_upload(files)
        if rejected is not None:
            return rejected
        try:
            result = self._client().upload_source(
                files, paths, str(request.data.get("name") or "uploaded-agent")
            )
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return Response(result, status=status.HTTP_201_CREATED)

    def health(self) -> dict[str, Any]:
        from simulate.services.harness_sandbox import HarnessSandboxUnavailable

        client = self._client()
        try:
            client.health()
            reachable = True
        except HarnessSandboxUnavailable:
            reachable = False
        return {"configured": reachable, "provider": "sandbox", "url": client.base_url}


class _SandboxMappingError(RuntimeError):
    pass
