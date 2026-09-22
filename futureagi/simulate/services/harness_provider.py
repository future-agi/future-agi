"""Public execution-backend switch for the hosted ALK harness control plane.

``HARNESS_PROVIDER=hosted`` persists jobs and starts the platform-managed gateway.
``HARNESS_PROVIDER=sandbox`` proxies to an out-of-process ALK sandbox server.
The hosted gateway independently selects Daytona or E2B through
``HOSTED_SANDBOX_PROVIDER``; the public v1.6 request contract does not change.
"""

from __future__ import annotations

import copy
import logging
import os
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

_E164_PHONE = re.compile(r"^\+[1-9]\d{1,14}$")


def _validate_phone_connectivity(payload) -> None:
    """A phone-only target uses platform telephony, never customer SIP credentials."""
    if payload["agent"]["connector"] != "phone":
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


def _organization(request):
    return getattr(request, "organization", None) or getattr(
        request.user, "organization", None
    )


def _workspace(request):
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

    organization = _organization(request)
    workspace = (
        Workspace.no_workspace_objects.select_related("organization")
        .filter(id=workspace_id, organization=organization, is_active=True)
        .first()
    )
    if workspace is None or not request.user.can_access_workspace(workspace):
        raise PermissionDenied("Access denied to this workspace")
    return workspace


def _scope_jobs(queryset, request):
    """Apply the exact tenant scope captured by the submitting request."""
    workspace = _workspace(request)
    if workspace is None:
        return queryset.filter(workspace__isnull=True)
    return queryset.filter(workspace=workspace)


def _scoped_job(request, pk):
    """One job, or None, resolved under the caller's full tenant scope.

    Organization alone is not the scope. A job belongs to a workspace, and matching only on the
    organization let a caller in one workspace read a job from another in the same organization,
    provided they held its id. Every scenario read and write goes through here so the scope is
    applied once rather than remembered at each call site.
    """
    organization = _organization(request)
    if organization is None:
        return None
    return (
        _scope_jobs(
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
        _scenario_row(reg, number) for number, reg in enumerate(scenario_regs, start=1)
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
        families = {connector: CONNECTOR_ALIASES[connector]}
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
    probe = HostedHarnessJob(organization=_organization(request), payload=payload)
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
        "connect_only", "provider_import"
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
    """The scenario names a person meant, from whatever they said.

    People point at scenarios the way they read them: "4", "12-30", "12, 15, 18", or the name
    itself. So "4" has to mean the row the table calls 4, and the table serves the number stored
    against the scenario, which survives a deletion rather than shifting up to fill the gap.
    `numbering` is that stored mapping; falling back to list position is only for a suite that has
    not been indexed yet, where position is all there is.
    """
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
            # "#4", "4th", "the fourth": people say the position, not the index.
            plain = re.sub(r"^(?:the|scenario|no\.?|#)\s*", "", part, flags=re.IGNORECASE)
            plain = re.sub(r"(?<=\d)(?:st|nd|rd|th)$", "", plain, flags=re.IGNORECASE)
            if plain.isdigit():
                take(by_number.get(int(plain), ""))
                continue
            if plain.lower() in _ORDINAL_WORDS:
                take(by_number.get(_ORDINAL_WORDS[plain.lower()], ""))
    return found


def _scenario_row(reg, number: int | None = None) -> dict:
    """One registered scenario, shaped the same way wherever it is read.

    ``number`` is the scenario's place in its own suite, one-based. It is what a person says out
    loud ("the fourth one", "12 to 30"), so it is served rather than derived from whatever subset
    a screen happens to be showing.
    """
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

        organization = _organization(request)
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
        from simulate.services.harness_usage import require_harness_run_usage

        try:
            require_harness_run_usage(str(organization.id), payload)
        except Exception as exc:
            response = _usage_limit_response(exc)
            if response is not None:
                return response
            raise
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
            _validate_required_credential_files(request, payload)
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        try:
            job, _ = create_hosted_job(
                organization,
                payload,
                idempotency_key=idempotency_key,
                workspace=_workspace(request),
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
        organization = _organization(request)
        jobs = _scope_jobs(
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
        try:
            source_analysis = _preflight_source_connectors(request, payload)
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
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
        # Every submitted key must be accepted: a wrong model key beside a valid transport
        # key still ends in a run that cannot speak.
        probe_failed = any(not item["ok"] for item in probe)
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
        return Response(
            {
                "ready_to_submit": not credentials["missing"] and not probe_failed,
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
                    "digest": digest,
                    "engines": HOSTED_ENGINE_CATALOG,
                    "runtimes": HOSTED_RUNTIME_CATALOG,
                },
            }
        )

    def retrieve(self, request, pk) -> Response:
        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Hosted harness job not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(serialize_job(job))

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
        """Start a fresh Daytona attempt for an existing repository job.

        Reruns deliberately keep the registered RunTest/TestExecution and their
        completed call data visible until replacement receipts arrive. The
        source, contract, scenario registrations, adjustments and encrypted
        credential references remain attached to the same durable job.
        """
        from datetime import timedelta
        from uuid import uuid4

        from simulate.models import HostedHarnessSecret
        from simulate.services.hosted_harness import HostedHarnessError
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
        terminal_states = {
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        }
        with transaction.atomic():
            job = (
                HostedHarnessJob.no_workspace_objects.select_for_update()
                .filter(id=job_id, organization=organization, workspace=workspace)
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
                    f"hosted harness job cannot be rerun while it is {job.state}",
                    status_code=409,
                )

            metadata = (job.payload or {}).get("metadata") or {}
            if job.run_test_id and not metadata.get("authoring_object_key"):
                raise HostedHarnessError(
                    "rerun_authoring_snapshot_missing",
                    "This run predates deterministic scenario reuse. Start one new "
                    "end-to-end run; subsequent reruns will reuse its saved contract "
                    "and scenarios.",
                    status_code=409,
                )

            payload = copy.deepcopy(job.payload)
            # The indexed column is authoritative. This also repairs jobs created before chat
            # adjustments atomically updated the dispatch document's duplicated count.
            payload["scenario_count"] = job.scenario_count
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
            metadata = payload.setdefault("metadata", {})
            metadata.pop("usage_limit", None)
            # Retry limits are per user-triggered run, not over the lifetime of
            # the durable job. The gateway uses this marker when deciding if a
            # fresh infrastructure attempt remains available.
            metadata["attempt_cycle_start"] = job.current_attempt_number + 1

            duration = int(payload["runtime"]["max_duration_seconds"])
            job.payload = payload
            job.state = HostedHarnessJob.State.QUEUED
            job.current_stage = "queued"
            job.completed_count = 0
            job.failed_count = 0
            job.uploaded_artifact_bytes = 0
            job.deadline_at = timezone.now() + timedelta(seconds=duration)
            job.cancel_requested_at = None
            job.cancel_reason = None
            job.terminal_at = None
            job.failure = None
            job.save(
                update_fields=[
                    "payload",
                    "state",
                    "current_stage",
                    "completed_count",
                    "failed_count",
                    "uploaded_artifact_bytes",
                    "deadline_at",
                    "cancel_requested_at",
                    "cancel_reason",
                    "terminal_at",
                    "failure",
                    "updated_at",
                ]
            )

            # A saved-suite rerun reuses the registered TestExecution and its
            # CallExecution rows. Reopen the execution as part of the same
            # transaction so clients resume polling while terminal receipts
            # replace the previous attempt's rows. Leaving it COMPLETED makes
            # the call-details grid look frozen even though the hosted guest is
            # actively executing calls.
            if job.test_execution_id:
                TestExecution.objects.filter(id=job.test_execution_id).update(
                    status=TestExecution.ExecutionStatus.RUNNING,
                    started_at=timezone.now(),
                    completed_at=None,
                    completed_calls=0,
                    failed_calls=0,
                )

        retry_cfg = payload["retry"]
        try:
            start_hosted_harness_gateway_workflow(
                str(job.id),
                base_url,
                retry_cfg["max_infrastructure_attempts"],
                retry_cfg["initial_backoff_seconds"],
                retry_cfg["max_backoff_seconds"],
            )
        except Exception:
            HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
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
            raise
        job.refresh_from_db()
        return serialize_job(job)

    # Fields a person may edit on an authored scenario. `tests` is what the report says the run
    # found out; the rest change what the run does, so they are refused when the caller declines
    # a re-proof.
    _DESCRIPTIVE_FIELDS = frozenset({"tests"})
    _BEHAVIOURAL_FIELDS = frozenset({"max_turns", "background_noise"})
    _PERSONA_FIELDS = frozenset(
        {
            "keywords",
            "personality",
            "communication_style",
            "accent",
            "languages",
            "occupation",
            "location",
        }
    )

    def _editing_contract(self) -> dict[str, list[str]]:
        """Which fields an amend will take, and which of them cannot be taken without a re-proof."""
        return {
            "editable_fields": sorted(self._DESCRIPTIVE_FIELDS | self._BEHAVIOURAL_FIELDS),
            "persona_fields": sorted(self._PERSONA_FIELDS),
            "rework_fields": sorted(self._BEHAVIOURAL_FIELDS | self._PERSONA_FIELDS),
        }

    def list_scenarios(self, request, pk) -> Response:
        """One page of a run's authored scenarios, in the order they were written."""
        from tfc.utils.pagination import ExtendedPageNumberPagination

        job = _scoped_job(request, pk)
        if job is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        from simulate.services.harness_scenarios import (
            apply_filters,
            index_scenarios,
            apply_ordering,
            apply_search,
            field_catalogue,
            group_counts,
            grouped,
            scenario_row,
        )

        queryset = HostedHarnessScenario.no_workspace_objects.filter(
            job=job
        ).select_related("scenario", "call_execution")
        if not queryset.filter(number__isnull=False).exists():
            # A run from before the suite was indexed has rows only where a call registered one,
            # and those carry nothing but a key. Index from the artefact on first read rather than
            # leaving those jobs permanently thin on this route.
            # The suite is held in one of two places: its own stage-output row, or the JSON
            # column on the job that a sealed archive is unpacked into. The detail endpoint falls
            # back the same way, so indexing has to look in both or it misses every archived run.
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
        # Search first. What the search leaves is the set the filter panel offers values from:
        # counting them over the FILTERED set instead would drop every value the current filter
        # excludes, so picking "Canadian" would remove "Indian" from the list and an OR could
        # never be built. The grid is the same: it describes what the suite holds, not what one
        # filter left of it.
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
        # The sections this page carries, already counted and in row order, so drawing them is a
        # walk rather than a regrouping.
        response.data["groups"] = group_counts(rows, queryset, group_by)
        response.data["group_by"] = group_by
        # The panel's own field list, counted over the whole filtered suite rather than the page.
        # Computed here so the client draws its controls without a second call or a second source.
        response.data["fields"] = field_catalogue(offerable)
        response.data["scenario_editing"] = self._editing_contract()
        from simulate.services.harness_scenarios import GROUPINGS

        response.data["groupings"] = [dict(one) for one in GROUPINGS]
        return response

    def scenario_coverage(self, request, pk) -> Response:
        """The suite's coverage grid. Its own route because it is its own question.

        The grid describes the whole suite, not a page of it, and its two axes are a filter of a
        different kind from the list's properties. Folding it into the list recomputed a whole-suite
        cross-tab on every page turn and every poll, for an answer that had not changed.
        """
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

        # The grid does not move with the page, but it does move with the filter: it describes
        # whatever the reader has narrowed the suite to. So it takes the list's own search and
        # filters, and only the paging is absent.
        queryset = HostedHarnessScenario.no_workspace_objects.filter(job=job)
        queryset = apply_search(queryset, request.query_params.get("search", ""))
        queryset = apply_filters(queryset, request.query_params)
        return Response(
            coverage_grid(
                queryset,
                request.query_params.get("row_axis") or DEFAULT_ROW_AXIS,
                request.query_params.get("col_axis") or DEFAULT_COL_AXIS,
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
            # Scope first, then lock: locking a row the caller may not read would answer "busy"
            # for a job in another workspace, which is itself an answer about that job.
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
            # One change may name many scenarios: "12-30", a comma list, or a selection sent from
            # the table. Expand before applying so each gets its own receipt.
            spread = []
            for change in changes:
                said = change.get("scenarios") or change.get("scenario")
                # Resolve a number the way the table shows it, not by where the scenario happens
                # to sit in the list: after a deletion those two stop agreeing.
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
            # An edit is held to the bar a written scenario is held to. Without this a person can
            # hand-edit straight past the gates the writer is refused by.
            if touched:
                from fi.alk.harness.scenario import Scenario, scenario_edit_problems

                rejected = []
                for one in suite:
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
                # Two places hold the suite: the archive a rerun replays, and the live guest's
                # own copy, which it re-packs over the archive on a later poll. An edit that
                # misses either one is an edit that comes back.
                # The list route serves the indexed rows, not the artefact, so an edit that
                # skips the index is an edit the table goes on not showing.
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

        organization = _organization(request)
        workspace = _workspace(request)
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

        organization = _organization(request)
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
        organization = _organization(request)
        return _scope_jobs(
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
            return Response(self._client().preflight(payload))
        except _SandboxMappingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

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
