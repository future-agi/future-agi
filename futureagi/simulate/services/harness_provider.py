"""Execution-backend switch for the hosted ALK harness control plane.

`HarnessJobViewSet` is provider-neutral: it validates the v1.6 request contract
(`futureagi.harness-job.v1`) and delegates to the provider selected by
``settings.HARNESS_PROVIDER``:

- ``daytona`` (default): the platform *is* the gateway. It persists the job,
  starts ``HostedHarnessGatewayWorkflow`` and drives the Daytona sandbox
  (matches the hosted-execution seams contract v1.6 — "the gateway drives the
  Daytona API from outside; no network runtime provider exists").
- ``sandbox``: the platform is a thin proxy to an out-of-process ALK sandbox
  server (dev: ALK's local-process provider; prod: a managed sandbox service).
  The v1.6 request is mapped to the sandbox server's flat contract and
  forwarded over HTTP; no viewset or UI change is required to switch.

Both providers accept the *same* validated v1.6 payload, so switching backends
never changes the platform's public request schema.
"""

from __future__ import annotations

import copy
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from simulate.models import (
    HostedHarnessJob,
    HostedHarnessReceipt,
    HostedHarnessScenario,
    HostedHarnessStageOutput,
    TestExecution,
)


def get_harness_provider():
    """Return the configured harness execution provider (default: daytona)."""
    name = str(getattr(settings, "HARNESS_PROVIDER", "daytona") or "daytona").lower()
    if name == "sandbox":
        return SandboxHarnessProvider()
    return DaytonaHarnessProvider()


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


def _validate_secret_refs_daytona(secret_refs: dict) -> None:
    """Reject secret_refs the Daytona resolver cannot materialize.

    platform-vault target_provider refs keep working.  Any other manager
    (including harness_environment_file) returns a typed error rather than
    silently accepting something the resolver will fail on inside the sandbox.
    """
    from simulate.services.hosted_harness import HostedHarnessError

    for alias, ref in secret_refs.items():
        manager = ref.get("manager", "")
        if manager != "platform-vault":
            raise HostedHarnessError(
                "secret_manager_unsupported",
                f"secret manager {manager!r} for alias {alias!r} is not supported "
                f"by the daytona provider; use platform-vault target_provider refs",
                status_code=422,
            )


def _validate_known_daytona_egress(payload: dict[str, Any], callback_url: str) -> None:
    """Reject known Daytona egress overflow before persisting or enqueueing a
    job."""
    from simulate.services.hosted_harness_gateway import (
        _hostname_from_url,
        _known_simulator_egress_inputs,
        _resolved_egress_domains,
        _validate_egress_domains,
        _validate_resolved_egress_domains,
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
    _validate_resolved_egress_domains(domains)


def serialize_job(job: HostedHarnessJob) -> dict[str, Any]:
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
    receipts = [r.body for r in receipt_qs]
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
    return {
        "job": {
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "source": job.payload["source"],
            "metadata": job.payload.get("metadata", {}),
            "run_test_id": str(job.run_test_id) if job.run_test_id else None,
            "test_execution_id": str(job.test_execution_id)
            if job.test_execution_id
            else None,
        },
        "status": {
            "state": job.state,
            "stage": job.current_stage,
            "updated_at": job.updated_at.isoformat(),
            "attempt": attempt.attempt_number if attempt else 0,
            "completed_scenarios": job.completed_count,
            "failed_scenarios": job.failed_count,
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
        "adjustments": list(
            (job.payload.get("metadata") or {}).get("adjustments") or []
        ),
        "platform": platform,
        "credentials": {"detected_connectors": detected_connectors},
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
    if mode in {"connect_only", "provider_import"}:
        target_field = "assistant_id" if connector == "vapi" else "agent_id"
        target = probe_provider_target(
            connector,
            (agent.get("config") or {}).get(target_field),
            values,
        )
        if target is not None:
            results.append(target)
    return [result.as_dict() for result in results]


class DaytonaHarnessProvider:
    """Platform-as-gateway. Persists the job and drives Daytona via Temporal."""

    name = "daytona"

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
        base_url = (
            getattr(settings, "HARNESS_PUBLIC_BASE_URL", "")
            or request.build_absolute_uri("/")
        ).rstrip("/")
        # This uses only references/configuration and deployment env presence.
        # Definitive launch validation runs again after vault resolution.
        try:
            _validate_secret_refs_daytona(payload["agent"]["secret_refs"])
            _validate_known_daytona_egress(payload, base_url)
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

        payload = request.validated_data
        base_url = (
            getattr(settings, "HARNESS_PUBLIC_BASE_URL", "")
            or request.build_absolute_uri("/")
        ).rstrip("/")
        try:
            _validate_secret_refs_daytona(payload["agent"]["secret_refs"])
            _validate_known_daytona_egress(payload, base_url)
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
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
        return Response(
            {
                "ready_to_submit": not credentials["missing"] and not probe_failed,
                "credentials": credentials["report"],
                "effective_parallelism": runtime["parallelism"],
                "snapshot": {
                    "name": getattr(settings, "ALK_DAYTONA_SNAPSHOT", None),
                    "digest": getattr(settings, "ALK_DAYTONA_SNAPSHOT_DIGEST", None),
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
        request_cancellation(job, request.validated_data["reason"])
        try:
            cancel_hosted_harness_gateway_workflow(str(job.id))
        except Exception:
            return Response(
                {"detail": "Cancellation was recorded but could not be signaled"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        job.refresh_from_db()
        return Response(serialize_job(job))

    def adjust(self, request, pk) -> Response:
        from simulate.services.hosted_harness import HostedHarnessError
        from simulate.services.hosted_harness_gateway import DaytonaHostedGateway

        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Hosted harness job not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            job = DaytonaHostedGateway().adjust(job, request.validated_data)
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        return Response(serialize_job(job))

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
        return {
            "configured": bool(
                getattr(settings, "DAYTONA_API_KEY", "")
                and getattr(settings, "ALK_DAYTONA_SNAPSHOT", "")
            ),
            "provider": "daytona",
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
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
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
